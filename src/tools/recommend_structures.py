import sqlglot

from sqlglot import (
    parse_one,
)

from domain.database_schema import (
    DatabaseSchema,
)


async def recommend_structures(
    sql: str,
    schema: DatabaseSchema,
    database_type: str = "bigquery",
) -> list[str]:
    """
    Transitional recommendation layer.

    IMPORTANT:

    BigQuery physical-design recommendations require actual
    BigQuery metadata and therefore belong in:

        optimization/bigquery/

    This transitional function intentionally avoids speculative
    BigQuery partition/clustering advice.

    PostgreSQL recommendations remain temporarily here until
    PostgreSQLOptimizer receives its own evidence layer.
    """

    recommendations = []

    database_type = (
        database_type
        or
        "bigquery"
    ).lower()

    dialect = (
        "postgres"
        if database_type
        in {
            "postgres",
            "postgresql",
        }
        else
        "bigquery"
    )

    try:

        parsed = (
            parse_one(
                sql,
                dialect=dialect,
            )
        )

    except Exception as exc:

        return [
            (
                "Unable to analyze query for "
                f"optimization recommendations: {exc}"
            )
        ]

    # =========================================================
    # BIGQUERY
    # =========================================================

    if database_type == "bigquery":

        # -----------------------------------------------------
        # SELECT * PROJECTION ONLY
        # -----------------------------------------------------

        has_projection_star = False

        for select in parsed.find_all(
            sqlglot.expressions.Select
        ):

            for item in select.expressions:

                if isinstance(
                    item,
                    sqlglot.expressions.Star,
                ):

                    has_projection_star = True

                    break

                if isinstance(
                    item,
                    sqlglot.expressions.Column,
                ):

                    if isinstance(
                        item.this,
                        sqlglot.expressions.Star,
                    ):

                        has_projection_star = True

                        break

            if has_projection_star:
                break

        if has_projection_star:

            recommendations.append(
                (
                    "BigQuery column pruning opportunity: "
                    "the query projects all columns. "
                    "Select only columns required by the "
                    "consumer when the required projection "
                    "is known."
                )
            )

        # -----------------------------------------------------
        # NO PARTITION / CLUSTERING ADVICE HERE
        # -----------------------------------------------------
        #
        # Those recommendations require actual table metadata.
        #
        # BigQueryOptimizer + BigQueryEvidenceAnalyzer now
        # owns them.
        # -----------------------------------------------------

        return recommendations

    # =========================================================
    # POSTGRESQL
    # =========================================================

    referenced_tables = {
        table.name

        for table
        in parsed.find_all(
            sqlglot.expressions.Table
        )
    }

    where_columns = set()

    for where in parsed.find_all(
        sqlglot.expressions.Where
    ):

        for column in where.find_all(
            sqlglot.expressions.Column
        ):

            where_columns.add(
                column.name.lower()
            )

    join_columns = set()

    for join in parsed.find_all(
        sqlglot.expressions.Join
    ):

        on_expression = (
            join.args.get(
                "on"
            )
        )

        if not on_expression:
            continue

        for column in (
            on_expression.find_all(
                sqlglot.expressions.Column
            )
        ):

            join_columns.add(
                column.name.lower()
            )

    for table in schema.tables:

        if (
            referenced_tables
            and
            table.name
            not in referenced_tables
        ):
            continue

        for column in table.columns:

            column_name = (
                column.name.lower()
            )

            if (
                column_name
                in where_columns
            ):

                recommendations.append(
                    (
                        "PostgreSQL index opportunity: "
                        f"consider an index on "
                        f"{table.name}({column.name}) "
                        "if EXPLAIN evidence and workload "
                        "frequency support it."
                    )
                )

            elif (
                column_name
                in join_columns
            ):

                recommendations.append(
                    (
                        "PostgreSQL join-index opportunity: "
                        f"{table.name}({column.name}) "
                        "participates in a JOIN. "
                        "Validate index usefulness using "
                        "PostgreSQL EXPLAIN evidence."
                    )
                )

    return recommendations[:6]