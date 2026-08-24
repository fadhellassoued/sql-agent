import sqlglot
from sqlglot import parse_one


async def detect_issues(
    sql: str,
    dialect: str = "bigquery",
) -> list[str]:
    """
    Detect potential SQL performance/cost issues.

    Important:
    This function only DETECTS issues.
    It never modifies the SQL.
    """

    issues: list[str] = []

    try:
        parsed = parse_one(sql, dialect=dialect)

        # ---------------------------------------------------------
        # 1. SELECT *
        # ---------------------------------------------------------
        if parsed.find(sqlglot.expressions.Star):
            issues.append(
                "SELECT * detected – explicitly select only the required "
                "columns to reduce unnecessary data processing."
            )

        # ---------------------------------------------------------
        # 2. Missing WHERE
        # ---------------------------------------------------------
        if not parsed.find(sqlglot.expressions.Where):
            issues.append(
                "No WHERE clause detected – this may result in scanning "
                "more data than necessary."
            )

        # ---------------------------------------------------------
        # 3. CROSS JOIN
        # ---------------------------------------------------------
        for join in parsed.find_all(sqlglot.expressions.Join):
            kind = join.args.get("kind")

            if isinstance(kind, str) and kind.upper() == "CROSS":
                issues.append(
                    "CROSS JOIN detected – verify that a Cartesian join "
                    "is actually required."
                )
                break

        # ---------------------------------------------------------
        # 4. GROUP BY / ORDER BY on large analytical queries
        # ---------------------------------------------------------
        if parsed.find(sqlglot.expressions.Group):
            if not parsed.find(sqlglot.expressions.Limit):
                issues.append(
                    "Aggregation query without a LIMIT detected – verify "
                    "that the full result set is required."
                )

        # ---------------------------------------------------------
        # LIMIT is intentionally NOT classified as a required
        # performance optimization.
        #
        # Example:
        #   SELECT COUNT(*) FROM orders;
        #
        # Adding LIMIT would not preserve the intended result.
        # ---------------------------------------------------------

    except Exception as exc:
        issues.append(f"SQL parsing error: {exc}")

    return issues