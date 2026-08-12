import sqlglot
from sqlglot import parse_one

async def detect_issues(sql: str) -> list:
    issues = []
    try:
        parsed = parse_one(sql, dialect="postgres")
        if parsed.find(sqlglot.expressions.Star):
            issues.append("SELECT * detected – specify only required columns.")
        if not parsed.find(sqlglot.expressions.Where):
            issues.append("Missing WHERE clause – this will scan the entire table.")
        if not parsed.find(sqlglot.expressions.Limit):
            issues.append("Missing LIMIT – consider limiting results.")
        for node in parsed.find_all(sqlglot.expressions.Join):
            if node.args.get('kind', '').upper() == 'CROSS':
                issues.append("CROSS JOIN detected – prefer INNER JOIN with condition.")
                break
    except Exception as e:
        issues.append(f"Parsing error: {str(e)}")
    return issues