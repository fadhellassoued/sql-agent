from sqlalchemy import create_engine, text
from domain.execution_plan import ExecutionPlan
from application.interfaces.database_repository import DatabaseRepository

class PostgresRepository(DatabaseRepository):
    def __init__(self, engine):
        self.engine = engine

    async def execute_query(self, sql: str, limit: int = 1000):
        with self.engine.connect() as conn:
            if "LIMIT" not in sql.upper():
                sql = f"{sql} LIMIT {limit}"
            result = conn.execute(text(sql))
            rows = result.fetchall()
            columns = result.keys()
            return [dict(zip(columns, row)) for row in rows]

    async def get_execution_plan(self, sql: str):
        with self.engine.connect() as conn:
            result = conn.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}"))
            plan_json = result.fetchone()[0]
            plan = plan_json[0]["Plan"]
            # Simulate bytes processed (PostgreSQL doesn't have this natively)
            return ExecutionPlan(
                total_bytes_processed=0,
                scan_type=plan.get("Node Type", "Unknown")
            )