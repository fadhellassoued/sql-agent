from __future__ import annotations

import asyncio
from typing import Any

from infrastructure.catalog.knowledge_catalog_client import (
    KnowledgeCatalogClient,
)


class BigQueryCatalogEnricher:
    """
    Adds Knowledge Catalog semantic/governance evidence to the
    physical BigQuery evidence already collected by SQL-Agent.

    Ownership boundary:

        BigQuery metadata
            = physical truth

        Knowledge Catalog
            = semantic / descriptive / governance context

    Catalog information never directly approves a SQL rewrite.
    """

    def __init__(
        self,
        catalog_client: KnowledgeCatalogClient,
    ):
        self.catalog_client = catalog_client

    async def enrich_assets(
        self,
        *,
        assets: list[dict[str, str]],
    ) -> dict[str, Any]:
        """
        Enrich already-resolved BigQuery assets.

        Each asset should contain:
            project_id
            dataset_id
            table_id

        This deliberately operates on resolved assets rather than
        searching the enterprise catalog broadly.
        """

        normalized_assets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()

        for asset in assets or []:
            project_id = (
                asset.get("project_id") or ""
            ).strip()

            dataset_id = (
                asset.get("dataset_id") or ""
            ).strip()

            table_id = (
                asset.get("table_id") or ""
            ).strip()

            if not (
                project_id
                and dataset_id
                and table_id
            ):
                continue

            key = (
                project_id,
                dataset_id,
                table_id,
            )

            if key in seen:
                continue

            seen.add(key)

            normalized_assets.append(
                {
                    "project_id": project_id,
                    "dataset_id": dataset_id,
                    "table_id": table_id,
                }
            )

        if not normalized_assets:
            return {
                "status": "NO_REFERENCED_TABLES",
                "tables": [],
                "referenced_table_count": 0,
                "catalog_match_count": 0,
                "source": "knowledge_catalog",
            }

        tasks = [
            self._enrich_one(
                project_id=asset["project_id"],
                dataset_id=asset["dataset_id"],
                table_id=asset["table_id"],
            )
            for asset in normalized_assets
        ]

        results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        tables = []

        for asset, result in zip(
            normalized_assets,
            results,
        ):
            if isinstance(result, Exception):
                tables.append(
                    {
                        **asset,
                        "table":
                            (
                                f"{asset['project_id']}."
                                f"{asset['dataset_id']}."
                                f"{asset['table_id']}"
                            ),
                        "status": "UNAVAILABLE",
                        "entry": None,
                        "reason":
                            "Knowledge Catalog enrichment "
                            "could not be retrieved.",
                        "source": "knowledge_catalog",
                    }
                )

                continue

            tables.append(result)

        found_count = sum(
            1
            for item in tables
            if item.get("status") == "FOUND"
        )

        unavailable_count = sum(
            1
            for item in tables
            if item.get("status") == "UNAVAILABLE"
        )

        if found_count:
            status = "AVAILABLE"

        elif unavailable_count == len(tables):
            status = "UNAVAILABLE"

        else:
            status = "NO_MATCHES"

        return {
            "status": status,
            "tables": tables,
            "referenced_table_count":
                len(normalized_assets),
            "catalog_match_count":
                found_count,
            "source": "knowledge_catalog",
        }

    async def _enrich_one(
        self,
        *,
        project_id: str,
        dataset_id: str,
        table_id: str,
    ) -> dict[str, Any]:

        result = (
            await self.catalog_client
            .find_bigquery_table(
                project_id=project_id,
                dataset_id=dataset_id,
                table_id=table_id,
            )
        )

        full_name = (
            f"{project_id}."
            f"{dataset_id}."
            f"{table_id}"
        )

        return {
            "project_id": project_id,
            "dataset_id": dataset_id,
            "table_id": table_id,
            "table": full_name,
            "status":
                result.get(
                    "status",
                    "UNKNOWN",
                ),
            "expected_fqn":
                result.get(
                    "expected_fqn"
                ),
            "entry":
                result.get(
                    "entry"
                ),
            "source":
                "knowledge_catalog",
        }