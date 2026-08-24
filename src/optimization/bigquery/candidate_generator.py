from __future__ import annotations

import sqlglot

from sqlglot import (
    exp,
)


class BigQueryCandidateGenerator:
    """
    Generate deterministic BigQuery optimization candidates.

    The generator does NOT invent user intent.

    User-controlled choices such as selected output columns
    must be supplied explicitly.
    """

    def generate_column_pruned_candidate(
        self,
        sql: str,
        selected_columns: list[str],
    ) -> dict:

        if (
            not sql
            or not sql.strip()
        ):

            raise ValueError(
                "SQL query cannot be empty."
            )

        if not selected_columns:

            raise ValueError(
                "At least one column must be selected."
            )

        try:

            parsed = (
                sqlglot.parse_one(
                    sql,
                    read="bigquery",
                )
            )

        except Exception as exc:

            raise ValueError(
                f"Unable to parse BigQuery SQL: {exc}"
            ) from exc

        # =====================================================
        # FIND MAIN SELECT
        # =====================================================

        main_select = (
            parsed
            if isinstance(
                parsed,
                exp.Select,
            )
            else parsed.find(
                exp.Select
            )
        )

        if main_select is None:

            raise ValueError(
                "Unable to locate SELECT statement."
            )

        # =====================================================
        # VERIFY SELECT *
        # =====================================================

        wildcard_found = False

        for projection in (
            main_select.expressions
        ):

            if isinstance(
                projection,
                exp.Star,
            ):

                wildcard_found = True

                break

            if (
                isinstance(
                    projection,
                    exp.Column,
                )
                and
                isinstance(
                    projection.this,
                    exp.Star,
                )
            ):

                wildcard_found = True

                break

        if not wildcard_found:

            raise ValueError(
                "The query does not contain a wildcard projection."
            )

        # =====================================================
        # BUILD PROJECTION
        # =====================================================

        new_projection = []

        for column_name in (
            selected_columns
        ):

            cleaned = (
                column_name.strip()
            )

            if not cleaned:

                continue

            # Nested BigQuery fields may contain dots.
            parts = (
                cleaned.split(".")
            )

            if len(parts) == 1:

                column_expression = (
                    exp.column(
                        parts[0]
                    )
                )

            else:

                column_expression = (
                    exp.column(
                        parts[-1],
                        table=
                            ".".join(
                                parts[:-1]
                            ),
                    )
                )

            new_projection.append(
                column_expression
            )

        if not new_projection:

            raise ValueError(
                "No valid selected columns were supplied."
            )

        # =====================================================
        # REPLACE PROJECTION
        # =====================================================

        main_select.set(
            "expressions",
            new_projection,
        )

        candidate_sql = (
            parsed.sql(
                dialect="bigquery"
            )
        )

        return {
            "candidate_sql":
                candidate_sql,

            "rewrite_type":
                "COLUMN_PRUNING",

            "selected_columns":
                selected_columns,

            "changed":
                True,
        }