from __future__ import annotations

from typing import Any

import sqlglot

from sqlglot import exp


# ============================================================
# CONSTANTS
# ============================================================

GIB = 1024 ** 3

LARGE_TABLE_BYTES = (
    10 * GIB
)

VERY_LARGE_TABLE_BYTES = (
    100 * GIB
)


# ============================================================
# HELPERS
# ============================================================

def _safe_int(
    value: Any,
) -> int | None:

    if value is None:

        return None

    try:

        return int(
            value
        )

    except (
        TypeError,
        ValueError,
    ):

        return None


def _format_bytes(
    value: int | None,
) -> str:

    if value is None:

        return "unknown"

    kib = 1024
    mib = 1024 ** 2
    gib = 1024 ** 3
    tib = 1024 ** 4

    numeric = float(
        value
    )

    if numeric >= tib:

        return (
            f"{numeric / tib:.3f} TiB"
        )

    if numeric >= gib:

        return (
            f"{numeric / gib:.2f} GiB"
        )

    if numeric >= mib:

        return (
            f"{numeric / mib:.2f} MiB"
        )

    if numeric >= kib:

        return (
            f"{numeric / kib:.2f} KiB"
        )

    return (
        f"{int(numeric)} bytes"
    )


def _expression_sql(
    expression: exp.Expression | None,
) -> str | None:

    if expression is None:

        return None

    try:

        return expression.sql(
            dialect="bigquery"
        )

    except Exception:

        return str(
            expression
        )


def _column_payload(
    column: exp.Column,
) -> dict:

    return {
        "table":
            column.table
            or None,

        "name":
            column.name,

        "sql":
            _expression_sql(
                column
            ),
    }


def _extract_columns(
    expression: exp.Expression | None,
) -> list[dict]:

    if expression is None:

        return []

    result = []

    seen = set()

    for column in (
        expression.find_all(
            exp.Column
        )
    ):

        key = (
            column.table
            or "",
            column.name,
        )

        if key in seen:

            continue

        seen.add(
            key
        )

        result.append(
            _column_payload(
                column
            )
        )

    return result


def _extract_column_aliases(
    expression: exp.Expression | None,
) -> list[str]:

    aliases = []

    seen = set()

    for column in (
        _extract_columns(
            expression
        )
    ):

        alias = (
            column.get(
                "table"
            )
        )

        if not alias:

            continue

        if alias in seen:

            continue

        seen.add(
            alias
        )

        aliases.append(
            alias
        )

    return aliases


# ============================================================
# BIGQUERY JOIN EVIDENCE ANALYZER
# ============================================================

class BigQueryJoinEvidenceAnalyzer:
    """
    Build evidence for BigQuery JOIN operations.

    IMPORTANT:

    This component does NOT claim actual shuffle volume,
    intermediate result size, slot usage, or execution cost.

    Static table metadata represents physical table metadata
    before filters, projections, aggregation, or optimizer
    execution decisions.

    Actual runtime evidence must eventually come from BigQuery
    job/stage statistics.
    """

    def analyze(
        self,
        sql: str,
        structure,
        table_evidence: dict | None,
    ) -> dict:

        # =====================================================
        # EMPTY / NO JOIN FAST PATH
        # =====================================================

        if (
            not structure
            or getattr(
                structure,
                "join_count",
                0,
            ) == 0
        ):

            return {
                "join_count":
                    0,

                "edges":
                    [],

                "findings":
                    [],

                "has_cross_join":
                    False,

                "large_join_count":
                    0,
            }

        # =====================================================
        # PARSE SQL
        # =====================================================

        try:

            tree = (
                sqlglot.parse_one(
                    sql,
                    read="bigquery",
                )
            )

        except Exception as exc:

            return {
                "join_count":
                    getattr(
                        structure,
                        "join_count",
                        0,
                    ),

                "edges":
                    [],

                "findings":
                    [
                        {
                            "code":
                                "BQ_JOIN_EVIDENCE_PARSE_FAILED",

                            "severity":
                                "warning",

                            "category":
                                "join_analysis",

                            "message":
                                (
                                    "Join evidence analysis could "
                                    "not parse the SQL."
                                ),

                            "evidence": {
                                "error":
                                    str(
                                        exc
                                    ),
                            },
                        }
                    ],

                "has_cross_join":
                    False,

                "large_join_count":
                    0,
            }

        # =====================================================
        # BUILD METADATA INDEX
        # =====================================================

        metadata_index = (
            self._build_metadata_index(
                table_evidence
            )
        )

        edges = []

        findings = []

        scope_index = 0

        # =====================================================
        # ANALYZE EVERY SELECT SCOPE
        # =====================================================

        for select_node in (
            tree.find_all(
                exp.Select
            )
        ):

            scope_index += 1

            joins = (
                select_node.args.get(
                    "joins"
                )
                or []
            )

            if not joins:

                continue

            scope_aliases = (
                self._build_scope_source_map(
                    select_node=
                        select_node,

                    metadata_index=
                        metadata_index,
                )
            )

            where_expression = (
                select_node.args.get(
                    "where"
                )
            )

            where_sql = (
                _expression_sql(
                    where_expression
                )
            )

            where_columns = (
                _extract_columns(
                    where_expression
                )
            )

            filtered_aliases = (
                _extract_column_aliases(
                    where_expression
                )
            )

            # -------------------------------------------------
            # Sequential join chain.
            #
            # left_scope_aliases represents all sources already
            # present on the left side before the next JOIN.
            # -------------------------------------------------

            left_scope_aliases = (
                self._initial_left_aliases(
                    select_node
                )
            )

            for (
                join_index,
                join_node,
            ) in enumerate(
                joins,
                start=1,
            ):

                edge = (
                    self._analyze_join(
                        scope_index=
                            scope_index,

                        join_index=
                            join_index,

                        join_node=
                            join_node,

                        left_scope_aliases=
                            list(
                                left_scope_aliases
                            ),

                        source_map=
                            scope_aliases,

                        where_sql=
                            where_sql,

                        where_columns=
                            where_columns,

                        filtered_aliases=
                            filtered_aliases,
                    )
                )

                edges.append(
                    edge
                )

                findings.extend(
                    edge.get(
                        "findings",
                        [],
                    )
                )

                right_alias = (
                    edge.get(
                        "right_source",
                        {}
                    ).get(
                        "alias"
                    )
                )

                if (
                    right_alias
                    and
                    right_alias
                    not in left_scope_aliases
                ):

                    left_scope_aliases.append(
                        right_alias
                    )

        # =====================================================
        # GLOBAL JOIN GRAPH FINDINGS
        # =====================================================

        if len(
            edges
        ) >= 3:

            findings.append(
                {
                    "code":
                        "BQ_COMPLEX_JOIN_GRAPH",

                    "severity":
                        "info",

                    "category":
                        "join_graph",

                    "message":
                        (
                            f"The query contains {len(edges)} "
                            "join edges. Join ordering, predicate "
                            "placement, projection width, and "
                            "intermediate-result size should be "
                            "reviewed using BigQuery execution "
                            "evidence."
                        ),

                    "evidence": {
                        "join_count":
                            len(
                                edges
                            ),
                    },
                }
            )

        has_cross_join = any(
            edge.get(
                "is_cross_join"
            )

            for edge
            in edges
        )

        large_join_count = sum(
            1

            for edge
            in edges

            if edge.get(
                "large_input"
            )
        )

        return {
            "join_count":
                len(
                    edges
                ),

            "scope_count":
                scope_index,

            "edges":
                edges,

            "findings":
                self._deduplicate_findings(
                    findings
                ),

            "has_cross_join":
                has_cross_join,

            "large_join_count":
                large_join_count,

            "limitations": [
                (
                    "Table byte and row counts are static metadata "
                    "and do not represent post-filter join inputs."
                ),
                (
                    "Join evidence does not currently claim actual "
                    "shuffle bytes, slot usage, or intermediate "
                    "result size."
                ),
                (
                    "Actual execution-stage evidence should later "
                    "come from BigQuery job statistics."
                ),
            ],
        }

    # =========================================================
    # METADATA INDEX
    # =========================================================

    def _build_metadata_index(
        self,
        evidence: dict | None,
    ) -> dict[str, dict]:

        index = {}

        evidence = (
            evidence
            or {}
        )

        for table in (
            evidence.get(
                "tables",
                [],
            )
            or []
        ):

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

            candidates = [
                full_name,
                name,
                alias,
            ]

            if full_name:

                pieces = (
                    full_name.split(
                        "."
                    )
                )

                if len(
                    pieces
                ) >= 2:

                    candidates.append(
                        ".".join(
                            pieces[-2:]
                        )
                    )

                candidates.append(
                    pieces[-1]
                )

            for candidate in candidates:

                if not candidate:

                    continue

                index[
                    candidate.lower()
                ] = table

        return index

    # =========================================================
    # SOURCE MAP
    # =========================================================

    def _build_scope_source_map(
        self,
        select_node: exp.Select,
        metadata_index: dict[str, dict],
    ) -> dict[str, dict]:

        result = {}

        from_node = (
            select_node.args.get(
                "from"
            )
        )

        if from_node:

            source = (
                getattr(
                    from_node,
                    "this",
                    None,
                )
            )

            source_payload = (
                self._source_payload(
                    source,
                    metadata_index,
                )
            )

            if source_payload:

                alias = (
                    source_payload.get(
                        "alias"
                    )
                )

                if alias:

                    result[
                        alias.lower()
                    ] = source_payload

        for join_node in (
            select_node.args.get(
                "joins"
            )
            or []
        ):

            source_payload = (
                self._source_payload(
                    join_node.this,
                    metadata_index,
                )
            )

            if not source_payload:

                continue

            alias = (
                source_payload.get(
                    "alias"
                )
            )

            if alias:

                result[
                    alias.lower()
                ] = source_payload

        return result

    # =========================================================
    # INITIAL LEFT SIDE
    # =========================================================

    def _initial_left_aliases(
        self,
        select_node: exp.Select,
    ) -> list[str]:

        result = []

        from_node = (
            select_node.args.get(
                "from"
            )
        )

        if not from_node:

            return result

        source = (
            getattr(
                from_node,
                "this",
                None,
            )
        )

        alias = (
            self._source_alias(
                source
            )
        )

        if alias:

            result.append(
                alias
            )

        return result

    # =========================================================
    # SOURCE PAYLOAD
    # =========================================================

    def _source_payload(
        self,
        source: exp.Expression | None,
        metadata_index: dict[str, dict],
    ) -> dict | None:

        if source is None:

            return None

        source_sql = (
            _expression_sql(
                source
            )
        )

        alias = (
            self._source_alias(
                source
            )
        )

        table_name = None

        source_type = (
            source.__class__.__name__
        )

        metadata = None

        # -----------------------------------------------------
        # PHYSICAL TABLE
        # -----------------------------------------------------

        if isinstance(
            source,
            exp.Table,
        ):

            table_name = (
                self._table_full_name(
                    source
                )
            )

            lookup_candidates = [
                table_name,
                source.name,
                alias,
            ]

            for candidate in (
                lookup_candidates
            ):

                if not candidate:

                    continue

                metadata = (
                    metadata_index.get(
                        candidate.lower()
                    )
                )

                if metadata:

                    break

        # -----------------------------------------------------
        # SUBQUERY
        # -----------------------------------------------------

        elif isinstance(
            source,
            exp.Subquery,
        ):

            table_name = None

        # -----------------------------------------------------
        # UNNEST
        # -----------------------------------------------------

        elif isinstance(
            source,
            exp.Unnest,
        ):

            table_name = None

        return {
            "source_type":
                source_type,

            "sql":
                source_sql,

            "table":
                table_name,

            "alias":
                alias,

            "metadata":
                self._metadata_summary(
                    metadata
                ),
        }

    # =========================================================
    # ANALYZE ONE JOIN
    # =========================================================

    def _analyze_join(
        self,
        scope_index: int,
        join_index: int,
        join_node: exp.Join,
        left_scope_aliases: list[str],
        source_map: dict[str, dict],
        where_sql: str | None,
        where_columns: list[dict],
        filtered_aliases: list[str],
    ) -> dict:

        right_source = (
            self._source_payload_from_map(
                join_node.this,
                source_map,
            )
        )

        right_alias = (
            right_source.get(
                "alias"
            )
            if right_source
            else None
        )

        join_type = (
            self._join_type(
                join_node
            )
        )

        is_cross_join = (
            join_type
            ==
            "CROSS"
        )

        condition = (
            join_node.args.get(
                "on"
            )
        )

        condition_sql = (
            _expression_sql(
                condition
            )
        )

        using_columns = (
            self._extract_using_columns(
                join_node
            )
        )

        condition_columns = (
            _extract_columns(
                condition
            )
        )

        equality_pairs = (
            self._extract_equality_pairs(
                condition
            )
        )

        transformed_key_pairs = (
            self._extract_transformed_equality_pairs(
                condition
            )
        )

        # -----------------------------------------------------
        # Determine which left-side physical aliases are
        # actually referenced in this join condition.
        # -----------------------------------------------------

        condition_aliases = set(
            _extract_column_aliases(
                condition
            )
        )

        referenced_left_aliases = [
            alias

            for alias
            in left_scope_aliases

            if alias
            in condition_aliases
        ]

        left_sources = []

        for alias in (
            referenced_left_aliases
        ):

            source = (
                source_map.get(
                    alias.lower()
                )
            )

            if source:

                left_sources.append(
                    source
                )

        right_metadata = (
            right_source.get(
                "metadata"
            )
            if right_source
            else None
        )

        right_size = (
            _safe_int(
                (
                    right_metadata
                    or {}
                ).get(
                    "size_bytes"
                )
            )
        )

        left_sizes = []

        for source in left_sources:

            metadata = (
                source.get(
                    "metadata"
                )
                or {}
            )

            size = (
                _safe_int(
                    metadata.get(
                        "size_bytes"
                    )
                )
            )

            if size is not None:

                left_sizes.append(
                    size
                )

        largest_left_size = (
            max(
                left_sizes
            )

            if left_sizes

            else None
        )

        size_ratio = (
            self._size_ratio(
                largest_left_size,
                right_size,
            )
        )

        right_filtered = (
            bool(
                right_alias
                and
                right_alias
                in filtered_aliases
            )
        )

        left_filtered_aliases = [
            alias

            for alias
            in referenced_left_aliases

            if alias
            in filtered_aliases
        ]

        findings = []

        risk_reasons = []

        risk_score = 0

        # =====================================================
        # CROSS JOIN
        # =====================================================

        if is_cross_join:

            risk_score += 4

            risk_reasons.append(
                "cross_join"
            )

            findings.append(
                {
                    "code":
                        "BQ_CROSS_JOIN",

                    "severity":
                        "warning",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "CROSS JOIN detected. Verify that a "
                            "Cartesian combination is intentional."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,
                    },
                }
            )

        # =====================================================
        # JOIN WITHOUT EXPLICIT CONDITION
        # =====================================================

        if (
            not is_cross_join
            and
            condition is None
            and
            not using_columns
        ):

            risk_score += 4

            risk_reasons.append(
                "missing_join_condition"
            )

            findings.append(
                {
                    "code":
                        "BQ_JOIN_WITHOUT_CONDITION",

                    "severity":
                        "warning",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "A JOIN without an ON or USING "
                            "condition was detected."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,
                    },
                }
            )

        # =====================================================
        # TRANSFORMED JOIN KEYS
        # =====================================================

        if transformed_key_pairs:

            risk_score += 2

            risk_reasons.append(
                "transformed_join_key"
            )

            findings.append(
                {
                    "code":
                        "BQ_TRANSFORMED_JOIN_KEY",

                    "severity":
                        "info",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "A join equality uses an expression "
                            "rather than simple column-to-column "
                            "keys. Review whether transformation "
                            "of join keys is necessary."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,

                        "expressions":
                            transformed_key_pairs,
                    },
                }
            )

        # =====================================================
        # LARGE RIGHT INPUT
        # =====================================================

        large_input = False

        if (
            right_size is not None
            and
            right_size
            >=
            LARGE_TABLE_BYTES
        ):

            large_input = True

            risk_score += 1

            risk_reasons.append(
                "large_right_table"
            )

            findings.append(
                {
                    "code":
                        "BQ_LARGE_JOIN_INPUT",

                    "severity":
                        "info",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "A join references a large physical "
                            f"table ({_format_bytes(right_size)} "
                            "logical size). Static table size is "
                            "not the same as the actual post-filter "
                            "join input."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,

                        "table":
                            (
                                right_source
                                or {}
                            ).get(
                                "table"
                            ),

                        "size_bytes":
                            right_size,
                    },
                }
            )

        # =====================================================
        # LARGE-LARGE STATIC TABLE JOIN
        # =====================================================

        if (
            largest_left_size is not None
            and
            right_size is not None
            and
            largest_left_size
            >=
            LARGE_TABLE_BYTES
            and
            right_size
            >=
            LARGE_TABLE_BYTES
        ):

            large_input = True

            risk_score += 2

            risk_reasons.append(
                "large_large_join"
            )

            findings.append(
                {
                    "code":
                        "BQ_LARGE_LARGE_JOIN",

                    "severity":
                        "info",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "This join connects physical tables "
                            "that are both large in static metadata. "
                            "Use BigQuery execution-stage evidence "
                            "before making claims about shuffle or "
                            "intermediate-result volume."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,

                        "largest_left_size_bytes":
                            largest_left_size,

                        "right_size_bytes":
                            right_size,
                    },
                }
            )

        # =====================================================
        # STATIC SIZE SKEW
        # =====================================================

        if (
            size_ratio is not None
            and
            size_ratio >= 100
        ):

            # Static size skew is evidence worth reviewing, but it
            # is not proof that the join itself is expensive.
            #
            # Give it a small contribution so the edge is classified
            # as LOW risk instead of NONE when this is the only signal.
            risk_score += 1

            risk_reasons.append(
                "static_size_skew"
            )

            findings.append(
                {
                    "code":
                        "BQ_JOIN_SIZE_SKEW",

                    "severity":
                        "info",

                    "category":
                        "join_analysis",

                    "message":
                        (
                            "The physical table metadata for this "
                            f"join differs by approximately "
                            f"{size_ratio:.1f}x. This is static "
                            "metadata only and does not measure "
                            "post-filter join inputs."
                        ),

                    "evidence": {
                        "scope_index":
                            scope_index,

                        "join_index":
                            join_index,

                        "size_ratio":
                            round(
                                size_ratio,
                                2,
                            ),
                    },
                }
            )

        # =====================================================
        # RISK LEVEL
        # =====================================================

        if risk_score >= 5:

            risk_level = (
                "high"
            )

        elif risk_score >= 2:

            risk_level = (
                "medium"
            )

        elif risk_score >= 1:

            risk_level = (
                "low"
            )

        else:

            risk_level = (
                "none"
            )

        return {
            "scope_index":
                scope_index,

            "join_index":
                join_index,

            "join_type":
                join_type,

            "is_cross_join":
                is_cross_join,

            "left_scope_aliases":
                left_scope_aliases,

            "referenced_left_aliases":
                referenced_left_aliases,

            "left_sources":
                left_sources,

            "right_source":
                right_source,

            "condition":
                condition_sql,

            "using_columns":
                using_columns,

            "condition_columns":
                condition_columns,

            "equality_pairs":
                equality_pairs,

            "transformed_equality_pairs":
                transformed_key_pairs,

            "where_clause":
                where_sql,

            "where_columns":
                where_columns,

            "filtered_aliases":
                filtered_aliases,

            "left_filtered_aliases":
                left_filtered_aliases,

            "right_filtered":
                right_filtered,

            "largest_left_size_bytes":
                largest_left_size,

            "right_size_bytes":
                right_size,

            "static_size_ratio":
                (
                    round(
                        size_ratio,
                        2,
                    )

                    if size_ratio is not None

                    else None
                ),

            "large_input":
                large_input,

            "risk_score":
                risk_score,

            "risk_level":
                risk_level,

            "risk_reasons":
                risk_reasons,

            "findings":
                findings,
        }

    # =========================================================
    # SOURCE PAYLOAD FROM EXISTING SCOPE MAP
    # =========================================================

    def _source_payload_from_map(
        self,
        source: exp.Expression | None,
        source_map: dict[str, dict],
    ) -> dict | None:

        if source is None:

            return None

        alias = (
            self._source_alias(
                source
            )
        )

        if alias:

            existing = (
                source_map.get(
                    alias.lower()
                )
            )

            if existing:

                return existing

        return {
            "source_type":
                source.__class__.__name__,

            "sql":
                _expression_sql(
                    source
                ),

            "table":
                (
                    self._table_full_name(
                        source
                    )

                    if isinstance(
                        source,
                        exp.Table,
                    )

                    else None
                ),

            "alias":
                alias,

            "metadata":
                None,
        }

    # =========================================================
    # TABLE NAME
    # =========================================================

    def _table_full_name(
        self,
        table: exp.Table,
    ) -> str:

        parts = []

        catalog = (
            table.catalog
        )

        db = (
            table.db
        )

        name = (
            table.name
        )

        if catalog:

            parts.append(
                catalog
            )

        if db:

            parts.append(
                db
            )

        if name:

            parts.append(
                name
            )

        return ".".join(
            parts
        )

    # =========================================================
    # SOURCE ALIAS
    # =========================================================

    def _source_alias(
        self,
        source: exp.Expression | None,
    ) -> str | None:

        if source is None:

            return None

        alias = (
            source.alias
        )

        if alias:

            return alias

        if isinstance(
            source,
            exp.Table,
        ):

            return (
                source.name
            )

        return None

    # =========================================================
    # METADATA SUMMARY
    # =========================================================

    def _metadata_summary(
        self,
        metadata: dict | None,
    ) -> dict | None:

        if not metadata:

            return None

        return {
            "full_name":
                metadata.get(
                    "full_name"
                ),

            "row_count":
                metadata.get(
                    "row_count"
                ),

            "size_bytes":
                metadata.get(
                    "size_bytes"
                ),

            "size_formatted":
                _format_bytes(
                    _safe_int(
                        metadata.get(
                            "size_bytes"
                        )
                    )
                ),

            "partitioned":
                metadata.get(
                    "partitioned"
                ),

            "partition_field":
                metadata.get(
                    "partition_field"
                ),

            "clustering_fields":
                metadata.get(
                    "clustering_fields"
                )
                or [],
        }

    # =========================================================
    # JOIN TYPE
    # =========================================================

    def _join_type(
        self,
        join: exp.Join,
    ) -> str:

        side = (
            join.args.get(
                "side"
            )
        )

        kind = (
            join.args.get(
                "kind"
            )
        )

        method = (
            join.args.get(
                "method"
            )
        )

        values = []

        for value in [
            side,
            kind,
            method,
        ]:

            if not value:

                continue

            values.append(
                str(
                    value
                ).upper()
            )

        joined = (
            " ".join(
                values
            )
        )

        if (
            "CROSS"
            in joined
        ):

            return "CROSS"

        if (
            "LEFT"
            in joined
        ):

            return "LEFT"

        if (
            "RIGHT"
            in joined
        ):

            return "RIGHT"

        if (
            "FULL"
            in joined
        ):

            return "FULL"

        if (
            "SEMI"
            in joined
        ):

            return "SEMI"

        if (
            "ANTI"
            in joined
        ):

            return "ANTI"

        return "INNER"

    # =========================================================
    # USING
    # =========================================================

    def _extract_using_columns(
        self,
        join: exp.Join,
    ) -> list[str]:

        using = (
            join.args.get(
                "using"
            )
        )

        if not using:

            return []

        result = []

        if isinstance(
            using,
            list,
        ):

            expressions = (
                using
            )

        elif hasattr(
            using,
            "expressions",
        ):

            expressions = (
                using.expressions
            )

        else:

            expressions = [
                using
            ]

        for item in expressions:

            name = getattr(
                item,
                "name",
                None,
            )

            if name:

                result.append(
                    name
                )

            else:

                sql = (
                    _expression_sql(
                        item
                    )
                )

                if sql:

                    result.append(
                        sql
                    )

        return result

    # =========================================================
    # SIMPLE EQUALITY JOIN KEYS
    # =========================================================

    def _extract_equality_pairs(
        self,
        condition: exp.Expression | None,
    ) -> list[dict]:

        if condition is None:

            return []

        pairs = []

        for equality in (
            condition.find_all(
                exp.EQ
            )
        ):

            left = (
                equality.this
            )

            right = (
                equality.expression
            )

            if (
                isinstance(
                    left,
                    exp.Column,
                )
                and
                isinstance(
                    right,
                    exp.Column,
                )
            ):

                pairs.append(
                    {
                        "left":
                            _column_payload(
                                left
                            ),

                        "right":
                            _column_payload(
                                right
                            ),
                    }
                )

        return pairs

    # =========================================================
    # TRANSFORMED EQUALITY KEYS
    # =========================================================

    def _extract_transformed_equality_pairs(
        self,
        condition: exp.Expression | None,
    ) -> list[dict]:

        if condition is None:

            return []

        result = []

        for equality in (
            condition.find_all(
                exp.EQ
            )
        ):

            left = (
                equality.this
            )

            right = (
                equality.expression
            )

            if (
                isinstance(
                    left,
                    exp.Column,
                )
                and
                isinstance(
                    right,
                    exp.Column,
                )
            ):

                continue

            left_columns = (
                _extract_columns(
                    left
                )
            )

            right_columns = (
                _extract_columns(
                    right
                )
            )

            # Only treat it as a transformed JOIN equality if
            # columns are involved.
            if (
                not left_columns
                and
                not right_columns
            ):

                continue

            result.append(
                {
                    "left_expression":
                        _expression_sql(
                            left
                        ),

                    "right_expression":
                        _expression_sql(
                            right
                        ),

                    "left_columns":
                        left_columns,

                    "right_columns":
                        right_columns,
                }
            )

        return result

    # =========================================================
    # SIZE RATIO
    # =========================================================

    def _size_ratio(
        self,
        left_size: int | None,
        right_size: int | None,
    ) -> float | None:

        if (
            left_size is None
            or
            right_size is None
            or
            left_size <= 0
            or
            right_size <= 0
        ):

            return None

        larger = max(
            left_size,
            right_size,
        )

        smaller = min(
            left_size,
            right_size,
        )

        return (
            larger
            /
            smaller
        )

    # =========================================================
    # DEDUP FINDINGS
    # =========================================================

    def _deduplicate_findings(
        self,
        findings: list[dict],
    ) -> list[dict]:

        result = []

        seen = set()

        for finding in findings:

            key = (
                finding.get(
                    "code"
                ),
                finding.get(
                    "message"
                ),
                str(
                    finding.get(
                        "evidence"
                    )
                ),
            )

            if key in seen:

                continue

            seen.add(
                key
            )

            result.append(
                finding
            )

        return result