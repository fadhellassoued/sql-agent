import asyncio

from src.infrastructure.catalog.knowledge_catalog_client import (
    KnowledgeCatalogClient,
)


async def main():

    client = KnowledgeCatalogClient(
        consumer_project_id=
            "sql-optimizer-agent"
    )

    result = (
        await client.find_preaggregated_candidates(
            project_id=
                "sql-optimizer-agent",

            dataset_id=
                "ecommerce",

            source_table_id=
                "order_items",
        )
    )

    print(
        result
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )