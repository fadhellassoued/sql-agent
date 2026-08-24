import sqlglot
from sqlglot import parse_one

from domain.database_schema import (
    DatabaseSchema,
)

from infrastructure.services.knowledge_catalog import (
    KnowledgeCatalogService,
)

from domain.optimization_result import (
    OptimizationResult,
)


def _extract_table_names(
    parsed,
) -> list[str]:

    tables = []

    for table in parsed.find_all(
        sqlglot.expressions.Table
    ):

        try:

            table_name = (
                table.name
            )

            if table_name:

                tables.append(
                    table_name
                )

        except Exception:

            continue

    return list(
        dict.fromkeys(
            tables
        )
    )


async def optimize_sql(
    sql: str,
    schema: DatabaseSchema,
    dataset: str = None,
    db_repo=None,
    knowledge_catalog: KnowledgeCatalogService = None,
    database_type: str = "bigquery",
) -> dict:
    """
    Transitional deterministic SQL optimizer.

    Current responsibility:

        - parse SQL safely
        - regenerate canonical dialect SQL
        - DO NOT claim formatting as an optimization

    No semantic rewrite is performed yet.

    Later:
        BigQuery rewrites move to BigQueryOptimizer
        PostgreSQL rewrites move to PostgreSQLOptimizer
    """

    if (
        not sql
        or not sql.strip()
    ):

        raise ValueError(
            "SQL query cannot be empty."
        )

    if schema is None:

        raise ValueError(
            "Database schema is required."
        )

    normalized_database_type = (
        database_type
        or
        "bigquery"
    ).lower()

    dialect = (
        "postgres"
        if normalized_database_type
        in {
            "postgres",
            "postgresql",
        }
        else
        "bigquery"
    )

    # =========================================================
    # PARSE
    # =========================================================

    try:

        parsed = parse_one(
            sql,
            dialect=dialect,
        )

    except Exception as exc:

        raise ValueError(
            f"Unable to parse SQL using "
            f"the {dialect} dialect: {exc}"
        ) from exc

    # =========================================================
    # TABLES
    # =========================================================

    table_names = (
        _extract_table_names(
            parsed
        )
    )

    # =========================================================
    # NORMALIZATION
    # =========================================================

    normalized_sql = (
        parsed.sql(
            dialect=dialect
        )
    )

    normalized = (
        normalized_sql.strip()
        !=
        sql.strip()
    )

    # =========================================================
    # REAL PERFORMANCE REWRITES
    # =========================================================
    #
    # At this stage none are applied.
    #
    # Therefore:
    #
    # changed = False
    #
    # even if formatting/canonical SQL differs.
    # =========================================================

    optimized_sql = (
        normalized_sql
    )

    rules_applied = []

    # =========================================================
    # RESULT
    # =========================================================

    result = OptimizationResult(
        original_sql=
            sql,

        optimized_sql=
            optimized_sql,

        rules_applied=
            rules_applied,
    )

    return {
        "original_sql":
            result.original_sql,

        "optimized_sql":
            result.optimized_sql,

        "rules_applied":
            [],

        # Real performance optimization?
        "changed":
            False,

        # Formatting only?
        "normalized":
            normalized,

        "normalization_message":
            (
                f"SQL normalized using "
                f"the {dialect} dialect."
                if normalized
                else None
            ),

        "tables":
            table_names,
    }