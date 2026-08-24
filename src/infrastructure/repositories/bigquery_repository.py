from google.cloud import bigquery

from domain.execution_plan import ExecutionPlan

from application.interfaces.database_repository import (
    DatabaseRepository,
)

from src.config import settings

import time


# ==============================================================
# BIGQUERY PRICING
# ==============================================================
#
# IMPORTANT:
#
# This is an ON-DEMAND ESTIMATE.
#
# BigQuery can also use capacity / Editions pricing, where a
# per-TiB dollar estimate is not the user's actual query charge.
#
# The price is configurable because:
#   - pricing can change,
#   - region/currency can matter,
#   - production projects can use capacity pricing.
#
# ==============================================================

BYTES_PER_TIB = 1024 ** 4


class BigQueryRepository(
    DatabaseRepository
):

    def __init__(
        self,
        client: bigquery.Client,
        dataset: str,
    ):

        self.client = client

        self.dataset = dataset

    # ==============================================================
    # QUERY EXECUTION
    # ==============================================================

    async def execute_query(
        self,
        sql: str,
        limit: int = 1000,
    ) -> list:

        if (
            "LIMIT"
            not in sql.upper()
        ):

            sql = (
                f"{sql.rstrip(';')} "
                f"LIMIT {limit}"
            )

        query_start = (
            time.perf_counter()
        )

        query_job = (
            self.client.query(
                sql
            )
        )

        rows = (
            query_job.result()
        )

        execution_time_ms = (
            time.perf_counter()
            -
            query_start
        ) * 1000

        print(
            "[TIMING][BigQuery] "
            "query execution: "
            f"{execution_time_ms:.2f} ms"
        )

        return [
            dict(
                row.items()
            )

            for row
            in rows
        ]

    # ==============================================================
    # DRY RUN / EXECUTION PLAN
    # ==============================================================

    async def get_execution_plan(
        self,
        sql: str,
    ) -> ExecutionPlan:
        """
        Perform a BigQuery dry run.

        No query results are executed/returned.

        BigQuery validates the query and estimates the number
        of bytes that would be processed.

        cost is an estimated ON-DEMAND analysis price and must
        not be presented as the user's final invoice.
        """

        total_start = (
            time.perf_counter()
        )

        # ----------------------------------------------------------
        # DRY-RUN CONFIG
        # ----------------------------------------------------------

        config_start = (
            time.perf_counter()
        )

        job_config = (
            bigquery.QueryJobConfig(
                dry_run=True,

                # Estimate uncached execution.
                use_query_cache=False,
            )
        )

        config_time_ms = (
            time.perf_counter()
            -
            config_start
        ) * 1000

        # ----------------------------------------------------------
        # BIGQUERY API CALL
        # ----------------------------------------------------------

        query_start = (
            time.perf_counter()
        )

        query_job = (
            self.client.query(
                sql,
                job_config=
                    job_config,
            )
        )

        query_time_ms = (
            time.perf_counter()
            -
            query_start
        ) * 1000

        # ----------------------------------------------------------
        # BYTES PROCESSED
        # ----------------------------------------------------------

        metadata_start = (
            time.perf_counter()
        )

        bytes_processed = int(
            query_job.total_bytes_processed
            or
            0
        )

        metadata_time_ms = (
            time.perf_counter()
            -
            metadata_start
        ) * 1000

        # ----------------------------------------------------------
        # ON-DEMAND COST ESTIMATE
        # ----------------------------------------------------------

        tib_processed = (
            bytes_processed
            /
            BYTES_PER_TIB
        )

        price_per_tib = float(
            settings
            .BIGQUERY_ON_DEMAND_PRICE_PER_TIB
        )

        estimated_cost = (
            tib_processed
            *
            price_per_tib
        )

        # ----------------------------------------------------------
        # TOTAL TIMING
        # ----------------------------------------------------------

        total_time_ms = (
            time.perf_counter()
            -
            total_start
        ) * 1000

        print(
            "[TIMING][BigQuery] "
            "dry-run config: "
            f"{config_time_ms:.2f} ms"
        )

        print(
            "[TIMING][BigQuery] "
            "client.query: "
            f"{query_time_ms:.2f} ms"
        )

        print(
            "[TIMING][BigQuery] "
            "bytes metadata extraction: "
            f"{metadata_time_ms:.2f} ms"
        )

        print(
            "[TIMING][BigQuery] "
            "total dry-run: "
            f"{total_time_ms:.2f} ms"
        )

        print(
            "[BigQueryRepository] "
            f"bytes processed: "
            f"{bytes_processed}"
        )

        print(
            "[BigQueryRepository] "
            f"TiB processed: "
            f"{tib_processed:.8f}"
        )

        print(
            "[BigQueryRepository] "
            "estimated on-demand cost: "
            f"${estimated_cost:.8f}"
        )

        return ExecutionPlan(
            total_bytes_processed=
                bytes_processed,

            scan_type=
                "DryRun",

            cost=
                estimated_cost,
        )

    # ==============================================================
    # COST ESTIMATE
    # ==============================================================

    async def estimate_query_cost(
        self,
        sql: str,
    ) -> dict:
        """
        High-level cost-estimation API used by the frontend.

        This wraps the BigQuery dry-run and adds:
          - byte conversions
          - threshold information
          - human-readable billing mode
          - latency

        No actual query execution occurs.
        """

        if (
            not sql
            or not sql.strip()
        ):

            raise ValueError(
                "SQL query cannot be empty."
            )

        start_time = (
            time.perf_counter()
        )

        execution_plan = (
            await self.get_execution_plan(
                sql
            )
        )

        bytes_processed = int(
            execution_plan
            .total_bytes_processed
            or
            0
        )

        estimated_cost = float(
            execution_plan.cost
            or
            0
        )

        threshold = float(
            settings
            .BIGQUERY_MAX_ESTIMATED_COST_USD
        )

        tib_processed = (
            bytes_processed
            /
            BYTES_PER_TIB
        )

        gib_processed = (
            bytes_processed
            /
            (
                1024 ** 3
            )
        )

        exceeds_threshold = (
            estimated_cost
            >
            threshold
        )

        latency_ms = (
            time.perf_counter()
            -
            start_time
        ) * 1000

        return {
            "bytes_processed":
                bytes_processed,

            "gib_processed":
                gib_processed,

            "tib_processed":
                tib_processed,

            "estimated_cost_usd":
                estimated_cost,

            "price_per_tib_usd":
                float(
                    settings
                    .BIGQUERY_ON_DEMAND_PRICE_PER_TIB
                ),

            "billing_mode":
                "on_demand_estimate",

            "dry_run":
                True,

            "threshold_usd":
                threshold,

            "exceeds_threshold":
                exceeds_threshold,

            "estimation_latency_ms":
                round(
                    latency_ms,
                    2,
                ),
        }

    # ==============================================================
    # TABLE ROW COUNT
    # ==============================================================

    async def get_table_row_count(
        self,
        table_name: str,
    ) -> int:

        try:

            query = f"""
                SELECT row_count
                FROM `{self.client.project}.{self.dataset}.__TABLES__`
                WHERE table_id = '{table_name}'
            """

            start = (
                time.perf_counter()
            )

            result = (
                self.client
                .query(
                    query
                )
                .result()
            )

            elapsed_ms = (
                time.perf_counter()
                -
                start
            ) * 1000

            print(
                "[TIMING][BigQuery] "
                f"row count {table_name}: "
                f"{elapsed_ms:.2f} ms"
            )

            rows = list(
                result
            )

            if rows:

                return (
                    rows[0][0]
                    or
                    0
                )

            return 0

        except Exception as exc:

            print(
                "[BigQueryRepository] "
                "row count failed for "
                f"{table_name}: "
                f"{exc}"
            )

            return 0

    # ==============================================================
    # TABLE SIZE
    # ==============================================================

    async def get_table_size_bytes(
        self,
        table_name: str,
    ) -> int:

        try:

            query = f"""
                SELECT size_bytes
                FROM `{self.client.project}.{self.dataset}.__TABLES__`
                WHERE table_id = '{table_name}'
            """

            start = (
                time.perf_counter()
            )

            result = (
                self.client
                .query(
                    query
                )
                .result()
            )

            elapsed_ms = (
                time.perf_counter()
                -
                start
            ) * 1000

            print(
                "[TIMING][BigQuery] "
                f"table size {table_name}: "
                f"{elapsed_ms:.2f} ms"
            )

            rows = list(
                result
            )

            if rows:

                return (
                    rows[0][0]
                    or
                    0
                )

            return 0

        except Exception as exc:

            print(
                "[BigQueryRepository] "
                "table size failed for "
                f"{table_name}: "
                f"{exc}"
            )

            return 0

    # ==============================================================
    # DATE RANGE
    # ==============================================================

    async def get_date_range(
        self,
        table_name: str,
        date_column: str,
    ):

        try:

            query = f"""
                SELECT
                    MIN(`{date_column}`),
                    MAX(`{date_column}`)
                FROM `{self.client.project}.{self.dataset}.{table_name}`
            """

            start = (
                time.perf_counter()
            )

            result = (
                self.client
                .query(
                    query
                )
                .result()
            )

            elapsed_ms = (
                time.perf_counter()
                -
                start
            ) * 1000

            print(
                "[TIMING][BigQuery] "
                "date range "
                f"{table_name}.{date_column}: "
                f"{elapsed_ms:.2f} ms"
            )

            rows = list(
                result
            )

            if rows:

                return (
                    rows[0][0],
                    rows[0][1],
                )

            return (
                None,
                None,
            )

        except Exception as exc:

            print(
                "[BigQueryRepository] "
                "date range failed for "
                f"{table_name}.{date_column}: "
                f"{exc}"
            )

            return (
                None,
                None,
            )

    # ==============================================================
    # COLUMN CARDINALITY
    # ==============================================================

    async def get_column_cardinality(
        self,
        table_name: str,
        column: str,
    ) -> int:

        try:

            query = f"""
                SELECT COUNT(DISTINCT `{column}`)
                FROM `{self.client.project}.{self.dataset}.{table_name}`
            """

            start = (
                time.perf_counter()
            )

            result = (
                self.client
                .query(
                    query
                )
                .result()
            )

            elapsed_ms = (
                time.perf_counter()
                -
                start
            ) * 1000

            print(
                "[TIMING][BigQuery] "
                "cardinality "
                f"{table_name}.{column}: "
                f"{elapsed_ms:.2f} ms"
            )

            rows = list(
                result
            )

            if rows:

                return (
                    rows[0][0]
                    or
                    0
                )

            return 0

        except Exception as exc:

            print(
                "[BigQueryRepository] "
                "cardinality failed for "
                f"{table_name}.{column}: "
                f"{exc}"
            )

            return 0