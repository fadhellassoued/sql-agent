from langchain.tools import StructuredTool
from tools.detect_issues import detect_issues
from tools.optimize_sql import optimize_sql
from tools.recommend_structures import recommend_structures
import asyncio

def create_tools(session_id: str, schema: dict, db_repo, knowledge_catalog, llm_service):
    # Tool 1: detect_issues
    detect_tool = StructuredTool.from_function(
        func=lambda sql: asyncio.run(detect_issues(sql)),
        name="detect_issues",
        description="Analyze a SQL query and return a list of performance issues."
    )

    # Tool 2: optimize_sql (returns ONLY the optimized SQL)
    def optimize_wrapper(sql: str):
        result = asyncio.run(optimize_sql(
            sql,
            schema,
            db_repo=db_repo,
            knowledge_catalog=knowledge_catalog
        ))
        # Return only the optimized SQL, not the rule_applied
        return result["optimized_sql"]

    optimize_tool = StructuredTool.from_function(
        func=optimize_wrapper,
        name="optimize_sql",
        description="Rewrite a SQL query to improve performance. Returns the optimized SQL."
    )

    # Tool 3: recommend_structures
    def recommend_wrapper(sql: str):
        return asyncio.run(recommend_structures(sql, schema))

    recommend_tool = StructuredTool.from_function(
        func=recommend_wrapper,
        name="recommend_structures",
        description="Suggest indexes or partitioning for long-term improvements."
    )

    return [detect_tool, optimize_tool, recommend_tool]