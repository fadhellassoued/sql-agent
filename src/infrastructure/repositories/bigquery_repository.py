from google.cloud import bigquery
from domain.execution_plan import ExecutionPlan
from application.interfaces.database_repository import DatabaseRepository

class BigQueryRepository(DatabaseRepository):
    def __init__(self, client: bigquery.Client, dataset: str):
        self.client = client
        self.dataset = dataset

    async def execute_query(self, sql: str, limit: int = 1000) -> list:
        if "LIMIT" not in sql.upper():
            sql = f"{sql} LIMIT {limit}"
        query_job = self.client.query(sql)
        rows = query_job.result()
        return [dict(row.items()) for row in rows]

    async def get_execution_plan(self, sql: str) -> ExecutionPlan:
        job_config = bigquery.QueryJobConfig(dry_run=True, use_query_cache=False)
        query_job = self.client.query(sql, job_config=job_config)
        bytes_processed = query_job.total_bytes_processed
        return ExecutionPlan(
            total_bytes_processed=bytes_processed,
            scan_type="DryRun"
        )

    # ============================================================
    # STATS METHODS WITH FALLBACKS
    # ============================================================

    async def get_table_row_count(self, table_name: str) -> int:
        try:
            query = f"SELECT COUNT(*) FROM `{self.client.project}.{self.dataset}.{table_name}`"
            result = self.client.query(query).result()
            return list(result)[0][0]
        except Exception:
            return 0

    async def get_table_size_bytes(self, table_name: str) -> int:
        try:
            query = f"""
                SELECT size_bytes
                FROM `{self.client.project}.{self.dataset}.__TABLES__`
                WHERE table_id = '{table_name}'
            """
            result = self.client.query(query).result()
            return list(result)[0][0] if result.total_rows > 0 else 0
        except Exception:
            return 0

    async def get_date_range(self, table_name: str, date_column: str):
        try:
            query = f"""
                SELECT MIN({date_column}), MAX({date_column})
                FROM `{self.client.project}.{self.dataset}.{table_name}`
            """
            result = self.client.query(query).result()
            row = list(result)[0]
            return row[0], row[1]
        except Exception:
            return None, None

    async def get_column_cardinality(self, table_name: str, column: str) -> int:
        try:
            query = f"""
                SELECT COUNT(DISTINCT {column})
                FROM `{self.client.project}.{self.dataset}.{table_name}`
            """
            result = self.client.query(query).result()
            return list(result)[0][0]
        except Exception:
            return 0