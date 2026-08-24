from langchain.tools import StructuredTool

from tools.detect_issues import detect_issues
from tools.optimize_sql import optimize_sql
from tools.recommend_structures import recommend_structures


def create_tools(
    session_id: str,
    schema,
    db_repo,
    knowledge_catalog,
    llm_service,
    database_type: str = "bigquery",
):
    """
    Create LangChain tools around the deterministic SQL services.

    The tools are asynchronous and therefore do not use asyncio.run().
    """

    # =============================================================
    # TOOL 1 — ISSUE DETECTION
    # =============================================================
    async def detect_wrapper(sql: str):
        return await detect_issues(
            sql=sql,
            dialect=(
                "postgres"
                if database_type.lower() in {"postgres", "postgresql"}
                else "bigquery"
            ),
        )

    detect_tool = StructuredTool.from_function(
        coroutine=detect_wrapper,
        name="detect_issues",
        description=(
            "Analyze a SQL query and identify potential performance "
            "and cost issues. This tool does not modify the SQL."
        ),
    )

    # =============================================================
    # TOOL 2 — DETERMINISTIC SQL OPTIMIZATION
    # =============================================================
    async def optimize_wrapper(sql: str):
        result = await optimize_sql(
            sql=sql,
            schema=schema,
            db_repo=db_repo,
            knowledge_catalog=knowledge_catalog,
        )

        return result

    optimize_tool = StructuredTool.from_function(
        coroutine=optimize_wrapper,
        name="optimize_sql",
        description=(
            "Apply deterministic SQL optimization using SQLGlot and "
            "database metadata. Returns the candidate optimized SQL, "
            "the original SQL, and the deterministic rules applied. "
            "Do not invent or modify the returned SQL."
        ),
    )

    # =============================================================
    # TOOL 3 — DATABASE-SPECIFIC RECOMMENDATIONS
    # =============================================================
    async def recommend_wrapper(sql: str):
        return await recommend_structures(
            sql=sql,
            schema=schema,
            database_type=database_type,
        )

    recommend_tool = StructuredTool.from_function(
        coroutine=recommend_wrapper,
        name="recommend_optimizations",
        description=(
            "Generate database-specific optimization recommendations. "
            "For BigQuery, focus on partitioning, clustering, partition "
            "pruning, materialized views, pre-aggregation, and reducing "
            "unnecessary columns."
        ),
    )

    return [
        detect_tool,
        optimize_tool,
        recommend_tool,
    ]