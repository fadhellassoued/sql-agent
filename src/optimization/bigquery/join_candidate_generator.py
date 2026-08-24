from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp


class BigQueryJoinCandidateGenerator:
    """
    Generate narrowly-scoped BigQuery JOIN candidates only when
    semantic preconditions can be established from deterministic
    metadata evidence.

    Phase 1 supported rewrite:
        CAST(a.key AS STRING) = CAST(b.key AS STRING)
        ->
        a.key = b.key

    The rewrite is allowed only when:
      - both sides are direct columns wrapped by CAST;
      - both CAST targets are STRING;
      - both underlying BigQuery column types are INT64/INTEGER;
      - both underlying types are the same;
      - the transformed equality was already identified by the
        join-evidence analyzer.

    The generator does NOT decide that the candidate is faster.
    The quality gate must validate the generated SQL separately.
    """

    SAFE_DIRECT_TYPES = {
        "INT64",
        "INTEGER",
    }

    def generate(
        self,
        *,
        sql: str,
        join_evidence: dict | None,
        table_evidence: dict | None,
    ) -> dict:

        if not sql or not sql.strip():
            return self._no_candidate(
                "SQL query is empty."
            )

        join_evidence = (
            join_evidence
            or {}
        )

        edges = (
            join_evidence.get(
                "edges",
                [],
            )
            or []
        )

        if not edges:
            return self._no_candidate(
                "No resolved join edges are available."
            )

        alias_metadata = (
            self._build_alias_metadata(
                edges=
                    edges,

                table_evidence=
                    table_evidence,
            )
        )

        proven_pairs = []

        for edge in edges:

            transformed_pairs = (
                edge.get(
                    "transformed_equality_pairs",
                    [],
                )
                or []
            )

            for pair in transformed_pairs:

                proof = (
                    self._prove_redundant_string_cast_pair(
                        pair=
                            pair,

                        alias_metadata=
                            alias_metadata,

                        scope_index=
                            edge.get(
                                "scope_index"
                            ),

                        join_index=
                            edge.get(
                                "join_index"
                            ),
                    )
                )

                if proof:

                    proven_pairs.append(
                        proof
                    )

        if not proven_pairs:

            return self._no_candidate(
                (
                    "No transformed join equality currently meets "
                    "the strict semantic-proof rules for an automatic "
                    "candidate. Full schema metadata was resolved for "
                    f"aliases: {sorted(alias_metadata.keys())}."
                )
            )

        try:

            expression = (
                sqlglot.parse_one(
                    sql,
                    read="bigquery",
                )
            )

        except Exception as exc:

            return self._no_candidate(
                f"Unable to parse SQL for candidate generation: {exc}"
            )

        proof_keys = {
            (
                self._normalize_sql(
                    proof[
                        "left_expression"
                    ]
                ),
                self._normalize_sql(
                    proof[
                        "right_expression"
                    ]
                ),
            )

            for proof
            in proven_pairs
        }

        rewritten_pairs = []

        for equality in list(
            expression.find_all(
                exp.EQ
            )
        ):

            left = equality.left
            right = equality.right

            if not (
                isinstance(
                    left,
                    exp.Cast,
                )
                and
                isinstance(
                    right,
                    exp.Cast,
                )
            ):
                continue

            left_sql = (
                self._expression_sql(
                    left
                )
            )

            right_sql = (
                self._expression_sql(
                    right
                )
            )

            normalized_pair = (
                self._normalize_sql(
                    left_sql
                ),
                self._normalize_sql(
                    right_sql
                ),
            )

            reverse_pair = (
                normalized_pair[
                    1
                ],
                normalized_pair[
                    0
                ],
            )

            if (
                normalized_pair
                not in
                proof_keys
                and
                reverse_pair
                not in
                proof_keys
            ):
                continue

            left_column = (
                left.this
            )

            right_column = (
                right.this
            )

            if not (
                isinstance(
                    left_column,
                    exp.Column,
                )
                and
                isinstance(
                    right_column,
                    exp.Column,
                )
            ):
                continue

            equality.set(
                "this",
                left_column.copy(),
            )

            equality.set(
                "expression",
                right_column.copy(),
            )

            rewritten_pairs.append(
                {
                    "original":
                        (
                            f"{left_sql} = "
                            f"{right_sql}"
                        ),

                    "candidate":
                        (
                            f"{self._expression_sql(left_column)} = "
                            f"{self._expression_sql(right_column)}"
                        ),
                }
            )

        if not rewritten_pairs:

            return self._no_candidate(
                (
                    "Semantic proof was available, but the exact "
                    "transformed equality could not be matched in "
                    "the parsed SQL."
                )
            )

        candidate_sql = (
            expression.sql(
                dialect="bigquery"
            )
        )

        if (
            self._normalize_sql(
                candidate_sql
            )
            ==
            self._normalize_sql(
                sql
            )
        ):

            return self._no_candidate(
                "Candidate SQL is unchanged."
            )

        return {
            "generated":
                True,

            "candidate_sql":
                candidate_sql,

            "rewrite_type":
                "REMOVE_REDUNDANT_JOIN_CAST",

            "rule_codes": [
                "BQ_REMOVE_REDUNDANT_JOIN_CAST"
            ],

            "semantic_proof": {
                "status":
                    "PROVEN_FOR_SUPPORTED_PATTERN",

                "proof_basis":
                    (
                        "Both join keys are INT64/INTEGER physical "
                        "columns and both sides apply CAST(... AS STRING). "
                        "The candidate removes only those matching casts; "
                        "the join columns and equality relation are unchanged."
                    ),

                "pairs":
                    proven_pairs,
            },

            "rewritten_pairs":
                rewritten_pairs,

            "requires_quality_gate":
                True,
        }

    # =========================================================
    # SEMANTIC PROOF
    # =========================================================

    def _prove_redundant_string_cast_pair(
        self,
        *,
        pair: dict,
        alias_metadata: dict[str, dict],
        scope_index: Any,
        join_index: Any,
    ) -> dict | None:

        left_expression = (
            pair.get(
                "left_expression"
            )
        )

        right_expression = (
            pair.get(
                "right_expression"
            )
        )

        left_columns = (
            pair.get(
                "left_columns",
                [],
            )
            or []
        )

        right_columns = (
            pair.get(
                "right_columns",
                [],
            )
            or []
        )

        if (
            len(
                left_columns
            )
            != 1
            or
            len(
                right_columns
            )
            != 1
        ):

            return None

        left_column = (
            left_columns[
                0
            ]
            or {}
        )

        right_column = (
            right_columns[
                0
            ]
            or {}
        )

        left_alias = (
            left_column.get(
                "table"
            )
        )

        right_alias = (
            right_column.get(
                "table"
            )
        )

        left_name = (
            left_column.get(
                "name"
            )
        )

        right_name = (
            right_column.get(
                "name"
            )
        )

        if not (
            left_alias
            and
            right_alias
            and
            left_name
            and
            right_name
            and
            left_expression
            and
            right_expression
        ):

            return None

        left_type = (
            self._lookup_column_type(
                alias_metadata.get(
                    left_alias.lower()
                ),
                left_name,
            )
        )

        right_type = (
            self._lookup_column_type(
                alias_metadata.get(
                    right_alias.lower()
                ),
                right_name,
            )
        )

        if not (
            left_type
            and
            right_type
        ):

            return None

        left_type = (
            left_type.upper()
        )

        right_type = (
            right_type.upper()
        )

        if (
            left_type
            !=
            right_type
        ):

            return None

        if (
            left_type
            not in
            self.SAFE_DIRECT_TYPES
        ):

            return None

        try:

            left_ast = (
                sqlglot.parse_one(
                    left_expression,
                    read="bigquery",
                )
            )

            right_ast = (
                sqlglot.parse_one(
                    right_expression,
                    read="bigquery",
                )
            )

        except Exception:

            return None

        if not (
            isinstance(
                left_ast,
                exp.Cast,
            )
            and
            isinstance(
                right_ast,
                exp.Cast,
            )
            and
            isinstance(
                left_ast.this,
                exp.Column,
            )
            and
            isinstance(
                right_ast.this,
                exp.Column,
            )
        ):

            return None

        left_target = (
            self._cast_target_name(
                left_ast
            )
        )

        right_target = (
            self._cast_target_name(
                right_ast
            )
        )

        if (
            left_target
            !=
            "STRING"
            or
            right_target
            !=
            "STRING"
        ):

            return None

        return {
            "scope_index":
                scope_index,

            "join_index":
                join_index,

            "left_expression":
                left_expression,

            "right_expression":
                right_expression,

            "left_column":
                left_column.get(
                    "sql"
                )
                or
                (
                    f"{left_alias}.{left_name}"
                ),

            "right_column":
                right_column.get(
                    "sql"
                )
                or
                (
                    f"{right_alias}.{right_name}"
                ),

            "left_source_type":
                left_type,

            "right_source_type":
                right_type,

            "cast_target":
                "STRING",
        }

    # =========================================================
    # METADATA HELPERS
    # =========================================================

    def _build_alias_metadata(
        self,
        *,
        edges: list[dict],
        table_evidence: dict | None,
    ) -> dict[str, dict]:
        """
        Resolve JOIN aliases to the full BigQuery table evidence.

        Join evidence intentionally stores a compact metadata summary.
        Candidate generation needs full column/type metadata, so this
        method maps each join alias back to BigQueryEvidenceAnalyzer
        output using full table names.
        """

        table_index = (
            self._build_table_evidence_index(
                table_evidence
            )
        )

        result: dict[str, dict] = {}

        for edge in edges:

            sources = list(
                edge.get(
                    "left_sources",
                    [],
                )
                or []
            )

            right_source = (
                edge.get(
                    "right_source"
                )
                or {}
            )

            if right_source:

                sources.append(
                    right_source
                )

            for source in sources:

                if not source:

                    continue

                alias = (
                    source.get(
                        "alias"
                    )
                )

                if not alias:

                    continue

                metadata = (
                    self._resolve_source_metadata(
                        source=
                            source,

                        table_index=
                            table_index,
                    )
                )

                if metadata:

                    result[
                        alias.lower()
                    ] = metadata

        return result

    def _build_table_evidence_index(
        self,
        table_evidence: dict | None,
    ) -> dict[str, dict]:
        """
        Index full BigQuery evidence by useful physical identifiers.
        """

        index: dict[str, dict] = {}

        evidence = (
            table_evidence
            or {}
        )

        for table in (
            evidence.get(
                "tables",
                [],
            )
            or []
        ):

            candidates = []

            full_name = (
                table.get(
                    "full_name"
                )
            )

            name = (
                table.get(
                    "name"
                )
            )

            alias = (
                table.get(
                    "alias"
                )
            )

            for value in (
                full_name,
                name,
                alias,
            ):

                if value:

                    candidates.append(
                        value
                    )

            if full_name:

                parts = [
                    part
                    for part
                    in full_name.split(
                        "."
                    )
                    if part
                ]

                if len(
                    parts
                ) >= 2:

                    candidates.append(
                        ".".join(
                            parts[-2:]
                        )
                    )

                if parts:

                    candidates.append(
                        parts[-1]
                    )

            for candidate in candidates:

                normalized = (
                    self._normalize_identifier(
                        candidate
                    )
                )

                if normalized:

                    index[
                        normalized
                    ] = table

        return index

    def _resolve_source_metadata(
        self,
        *,
        source: dict,
        table_index: dict[str, dict],
    ) -> dict | None:
        """
        Resolve one JoinEvidence source to the full table metadata.
        """

        compact_metadata = (
            source.get(
                "metadata"
            )
            or {}
        )

        candidates = [
            compact_metadata.get(
                "full_name"
            ),
            source.get(
                "table"
            ),
        ]

        for candidate in candidates:

            normalized = (
                self._normalize_identifier(
                    candidate
                )
            )

            if not normalized:

                continue

            metadata = (
                table_index.get(
                    normalized
                )
            )

            if metadata:

                return metadata

            pieces = [
                piece
                for piece
                in normalized.split(
                    "."
                )
                if piece
            ]

            if len(
                pieces
            ) >= 2:

                metadata = (
                    table_index.get(
                        ".".join(
                            pieces[-2:]
                        )
                    )
                )

                if metadata:

                    return metadata

            if pieces:

                metadata = (
                    table_index.get(
                        pieces[-1]
                    )
                )

                if metadata:

                    return metadata

        # Preserve compatibility for any future evidence shape that
        # already carries full columns inside join metadata.
        if compact_metadata:

            return compact_metadata

        return None

    def _normalize_identifier(
        self,
        value: str | None,
    ) -> str:
        """
        Normalize a BigQuery identifier for metadata lookup.
        """

        if not value:

            return ""

        return (
            str(
                value
            )
            .strip()
            .strip(
                "`"
            )
            .lower()
        )

    def _lookup_column_type(
        self,
        metadata: dict | None,
        column_name: str,
    ) -> str | None:

        if not metadata:

            return None

        columns = (
            metadata.get(
                "columns",
                [],
            )
            or []
        )

        target = (
            column_name.lower()
        )

        for column in columns:

            name = (
                column.get(
                    "name"
                )
            )

            if (
                name
                and
                name.lower()
                ==
                target
            ):

                return (
                    column.get(
                        "type"
                    )
                    or
                    column.get(
                        "field_type"
                    )
                )

        return None

    # =========================================================
    # SQL HELPERS
    # =========================================================

    def _cast_target_name(
        self,
        cast_expression: exp.Cast,
    ) -> str | None:

        target = (
            cast_expression.args.get(
                "to"
            )
        )

        if target is None:

            return None

        target_sql = (
            target.sql(
                dialect="bigquery"
            )
            .strip()
            .upper()
        )

        if target_sql in {
            "STRING",
            "TEXT",
        }:

            return "STRING"

        return target_sql

    def _expression_sql(
        self,
        expression: exp.Expression,
    ) -> str:

        return (
            expression.sql(
                dialect="bigquery"
            )
        )

    def _normalize_sql(
        self,
        sql: str,
    ) -> str:

        return (
            " ".join(
                (
                    sql
                    or ""
                )
                .strip()
                .split()
            )
            .lower()
        )

    def _no_candidate(
        self,
        reason: str,
    ) -> dict:

        return {
            "generated":
                False,

            "candidate_sql":
                None,

            "rewrite_type":
                None,

            "rule_codes":
                [],

            "semantic_proof": {
                "status":
                    "NOT_PROVEN",
            },

            "rewritten_pairs":
                [],

            "requires_quality_gate":
                False,

            "reason":
                reason,
        }