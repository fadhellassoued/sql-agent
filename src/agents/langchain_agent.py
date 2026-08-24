from langchain.agents import (
    create_openai_tools_agent,
    AgentExecutor,
)

from langchain.memory import ConversationBufferMemory

from langchain.prompts import ChatPromptTemplate

from langchain_google_vertexai import ChatVertexAI

from tools.langchain_tools import create_tools

from src.config import settings


def create_agent(
    session_id: str,
    schema,
    db_repo,
    knowledge_catalog,
    llm_service,
    memory=None,
    database_type: str = "bigquery",
):
    """
    Create the conversational SQL optimization agent.

    Architecture:

        Gemini / Vertex AI
            =
        reasoning + orchestration + explanation

        SQLGlot + database metadata + deterministic tools
            =
        SQL analysis + optimization evidence

    The Gemini model must never invent or rewrite executable SQL.
    """

    # =============================================================
    # GEMINI / VERTEX AI
    # =============================================================

    llm = ChatVertexAI(
        model="gemini-2.5-flash",
        project=settings.GCP_PROJECT_ID,
        location=settings.GCP_REGION,
        temperature=0,
        streaming=True,
    )

    # =============================================================
    # DETERMINISTIC TOOLS
    # =============================================================

    tools = create_tools(
        session_id=session_id,
        schema=schema,
        db_repo=db_repo,
        knowledge_catalog=knowledge_catalog,
        llm_service=llm_service,
        database_type=database_type,
    )

    # =============================================================
    # CONVERSATION MEMORY
    # =============================================================

    if memory is None:

        memory = ConversationBufferMemory(
            memory_key="chat_history",
            return_messages=True,
        )

    # =============================================================
    # GEMINI AGENT PROMPT
    # =============================================================

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                """
You are a BigQuery SQL optimization assistant.

You use deterministic tools to analyze SQL queries and
provide evidence-based recommendations.

Your role is:

    Gemini
        ↓
    reasoning
        ↓
    tool orchestration
        ↓
    explanation

The deterministic tools are the authority for SQL analysis
and SQL transformations.

IMPORTANT RULES:

1. ALWAYS use detect_issues before explaining a query.

2. Call optimize_sql when a candidate SQL optimization
   should be evaluated.

3. Call recommend_optimizations when database-specific
   recommendations are useful.

4. NEVER invent an optimized SQL query.

5. NEVER rewrite the SQL returned by optimize_sql.

6. The optimized SQL returned by optimize_sql is authoritative.

7. Clearly distinguish:

   - detected issues
   - deterministic optimizations actually applied
   - recommendations
   - explanation

8. If optimize_sql returns SQL identical to the original,
   explicitly state that no safe deterministic SQL rewrite
   was applied.

9. Never claim a recommendation produced a performance or
   cost improvement unless measured evidence is available.

10. For BigQuery, prioritize:

    - partitioning
    - partition pruning
    - clustering
    - column pruning
    - materialized views
    - pre-aggregation
    - reducing bytes processed
    - reducing unnecessary joins
    - reducing unnecessary computation

11. Do not recommend traditional PostgreSQL-style indexes
    for BigQuery.

12. If evidence is insufficient, explicitly say so.

You are an intelligent explanation and orchestration layer,
not the SQL execution engine.
""",
            ),
            (
                "placeholder",
                "{chat_history}",
            ),
            (
                "human",
                "{input}",
            ),
            (
                "placeholder",
                "{agent_scratchpad}",
            ),
        ]
    )

    # =============================================================
    # AGENT
    # =============================================================

    agent = create_openai_tools_agent(
        llm,
        tools,
        prompt,
    )

    # =============================================================
    # EXECUTOR
    # =============================================================

    executor = AgentExecutor(
        agent=agent,
        tools=tools,
        memory=memory,
        verbose=True,
        handle_parsing_errors=True,
        max_iterations=5,
        return_intermediate_steps=True,
    )

    return executor