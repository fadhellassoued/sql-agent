from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(
        0,
        str(SRC_DIR),
    )

from optimization.bigquery.preaggregation_semantic_validator import (
    BigQueryPreaggregationSemanticValidator,
)

from optimization.bigquery.preaggregation_candidate_generator import (
    BigQueryPreaggregationCandidateGenerator,
)

QUERY_DAILY_REVENUE = """
SELECT
    DATE(created_at) AS sale_date,
    SUM(sale_price) AS revenue
FROM `sql-optimizer-agent.ecommerce.order_items`
GROUP BY sale_date
ORDER BY sale_date
""".strip()


def _catalog_candidate(
    *,
    description: str | None = None,
    references_source: bool = True,
    candidate_fqn: str = (
        "bigquery:sql-optimizer-agent.ecommerce.daily_sales"
    ),
) -> dict:
    return {
        "fully_qualified_name": candidate_fqn,
        "display_name": "daily_sales",
        "description": (
            description
            or (
                "Curated daily sales aggregate for analytical and reporting "
                "workloads. Grain: one row represents one calendar day. "
                "Derived from ecommerce.order_items. Contains item_count, "
                "order_count, revenue, average_item_sale_price. Preferred "
                "analytical asset for daily revenue trends."
            )
        ),
        "references_source": references_source,
        "matched_keywords": [
            "aggregate",
            "analytical",
            "curated",
            "daily",
            "preferred",
            "reporting",
        ],
        "relevance_score": 17,
    }


def _catalog_payload(
    *,
    candidate: dict | None = None,
) -> dict:
    return {
        "status": "FOUND",
        "source_table": (
            "bigquery:sql-optimizer-agent.ecommerce.order_items"
        ),
        "candidates": [
            candidate
            or _catalog_candidate()
        ],
        "candidate_count": 1,
        "source": "knowledge_catalog",
    }


class _FakeField:
    def __init__(
        self,
        name: str,
        field_type: str,
    ):
        self.name = name
        self.field_type = field_type


class _FakeTable:
    def __init__(
        self,
        *,
        schema: list[_FakeField],
        modified: datetime,
    ):
        self.schema = schema
        self.modified = modified


class _FakeBigQueryClient:
    def __init__(
        self,
        *,
        candidate_modified_offset_days: int = 0,
        candidate_sale_date_type: str = "DATE",
        include_revenue: bool = True,
    ):
        now = datetime.now(
            timezone.utc
        )

        self.source = _FakeTable(
            schema=[
                _FakeField(
                    "created_at",
                    "TIMESTAMP",
                ),
                _FakeField(
                    "sale_price",
                    "FLOAT64",
                ),
                _FakeField(
                    "product_id",
                    "INT64",
                ),
            ],
            modified=now,
        )

        candidate_schema = [
            _FakeField(
                "sale_date",
                candidate_sale_date_type,
            ),
        ]

        if include_revenue:
            candidate_schema.append(
                _FakeField(
                    "revenue",
                    "FLOAT64",
                )
            )

        self.candidate = _FakeTable(
            schema=candidate_schema,
            modified=(
                now
                + timedelta(
                    days=
                        candidate_modified_offset_days
                )
            ),
        )

    def get_table(
        self,
        full_name: str,
    ):
        if full_name == (
            "sql-optimizer-agent.ecommerce.order_items"
        ):
            return self.source

        if full_name == (
            "sql-optimizer-agent.ecommerce.daily_sales"
        ):
            return self.candidate

        raise KeyError(
            full_name
        )


class PreaggregationSemanticRegressionTests(
    unittest.TestCase
):
    def setUp(
        self,
    ):
        self.validator = (
            BigQueryPreaggregationSemanticValidator()
        )

        self.generator = (
            BigQueryPreaggregationCandidateGenerator()
        )

    def _analyze(
        self,
        *,
        sql: str,
        catalog: dict | None = None,
        client: _FakeBigQueryClient | None = None,
    ) -> dict:
        return asyncio.run(
            self.validator.analyze(
                sql=sql,
                catalog_alternatives=(
                    catalog
                    or _catalog_payload()
                ),
                client=(
                    client
                    or _FakeBigQueryClient()
                ),
                default_project=(
                    "sql-optimizer-agent"
                ),
                default_dataset=(
                    "ecommerce"
                ),
            )
        )

    # =========================================================
    # POSITIVE CONTROL
    # =========================================================

    def test_daily_sum_pattern_is_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            (
                "PROVEN_FOR_SUPPORTED_"
                "PREAGGREGATION_PATTERN"
            ),
        )

        self.assertEqual(
            proof.get(
                "candidate_table"
            ),
            (
                "sql-optimizer-agent."
                "ecommerce.daily_sales"
            ),
        )

        candidate = (
            self.generator.generate(
                semantic_proof=proof
            )
        )

        self.assertTrue(
            candidate.get(
                "generated"
            )
        )

        self.assertEqual(
            candidate.get(
                "candidate_sql"
            ),
            (
                "SELECT `sale_date`, `revenue` "
                "FROM "
                "`sql-optimizer-agent.ecommerce.daily_sales` "
                "ORDER BY `sale_date`"
            ),
        )

    # =========================================================
    # QUERY-SHAPE NEGATIVE TESTS
    # =========================================================

    def test_product_grain_query_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql="""
SELECT
    product_id AS product_id,
    SUM(sale_price) AS revenue
FROM `sql-optimizer-agent.ecommerce.order_items`
GROUP BY product_id
ORDER BY product_id
""".strip(),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "UNSUPPORTED_PROJECTION",
        )

    def test_avg_measure_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql="""
SELECT
    DATE(created_at) AS sale_date,
    AVG(sale_price) AS average_sale_price
FROM `sql-optimizer-agent.ecommerce.order_items`
GROUP BY sale_date
ORDER BY sale_date
""".strip(),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "UNSUPPORTED_PROJECTION",
        )

    def test_where_filter_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql="""
SELECT
    DATE(created_at) AS sale_date,
    SUM(sale_price) AS revenue
FROM `sql-optimizer-agent.ecommerce.order_items`
WHERE created_at >= TIMESTAMP('2026-01-01')
GROUP BY sale_date
ORDER BY sale_date
""".strip(),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "UNSUPPORTED_QUERY_CLAUSES",
        )

    def test_join_query_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql="""
SELECT
    DATE(oi.created_at) AS sale_date,
    SUM(oi.sale_price) AS revenue
FROM `sql-optimizer-agent.ecommerce.order_items` AS oi
JOIN `sql-optimizer-agent.ecommerce.orders` AS o
    ON oi.order_id = o.order_id
GROUP BY sale_date
ORDER BY sale_date
""".strip(),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertIn(
            proof.get(
                "code"
            ),
            {
                "UNSUPPORTED_QUERY_CLAUSES",
                "UNSUPPORTED_SOURCE_SHAPE",
            },
        )

    def test_having_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql="""
SELECT
    DATE(created_at) AS sale_date,
    SUM(sale_price) AS revenue
FROM `sql-optimizer-agent.ecommerce.order_items`
GROUP BY sale_date
HAVING revenue > 1000
ORDER BY sale_date
""".strip(),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "UNSUPPORTED_QUERY_CLAUSES",
        )

    # =========================================================
    # CATALOG NEGATIVE TESTS
    # =========================================================

    def test_catalog_candidate_without_source_reference_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            catalog=_catalog_payload(
                candidate=
                    _catalog_candidate(
                        references_source=False,
                    )
            ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "NO_SOURCE_REFERENCING_CANDIDATE",
        )

    def test_catalog_description_missing_required_measure_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            catalog=_catalog_payload(
                candidate=
                    _catalog_candidate(
                        description=(
                            "Curated daily sales aggregate. "
                            "Grain: one row represents one calendar day. "
                            "Derived from ecommerce.order_items. "
                            "Contains item_count and order_count."
                        )
                    )
            ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "NO_PROVEN_COMPATIBLE_CANDIDATE",
        )

    def test_catalog_description_missing_daily_grain_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            catalog=_catalog_payload(
                candidate=
                    _catalog_candidate(
                        description=(
                            "Curated sales aggregate derived from "
                            "ecommerce.order_items. Contains revenue."
                        )
                    )
            ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "NO_PROVEN_COMPATIBLE_CANDIDATE",
        )

    # =========================================================
    # BIGQUERY PHYSICAL-EVIDENCE NEGATIVE TESTS
    # =========================================================

    def test_stale_candidate_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            client=
                _FakeBigQueryClient(
                    candidate_modified_offset_days=-1,
                ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

        self.assertEqual(
            proof.get(
                "code"
            ),
            "NO_PROVEN_COMPATIBLE_CANDIDATE",
        )

    def test_missing_candidate_measure_column_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            client=
                _FakeBigQueryClient(
                    include_revenue=False,
                ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

    def test_wrong_candidate_dimension_type_is_not_proven(
        self,
    ):
        proof = self._analyze(
            sql=QUERY_DAILY_REVENUE,
            client=
                _FakeBigQueryClient(
                    candidate_sale_date_type="STRING",
                ),
        )

        self.assertEqual(
            proof.get(
                "status"
            ),
            "NOT_PROVEN",
        )

    # =========================================================
    # CANDIDATE GENERATOR FAIL-CLOSED TEST
    # =========================================================

    def test_candidate_generator_refuses_unproven_input(
        self,
    ):
        candidate = (
            self.generator.generate(
                semantic_proof={
                    "status":
                        "NOT_PROVEN",
                    "reason":
                        "Test rejection.",
                }
            )
        )

        self.assertFalse(
            candidate.get(
                "generated"
            )
        )

        self.assertIsNone(
            candidate.get(
                "candidate_sql"
            )
        )


if __name__ == "__main__":
    unittest.main(
        verbosity=2
    )