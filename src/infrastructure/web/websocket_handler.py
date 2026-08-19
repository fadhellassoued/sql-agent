from fastapi import WebSocket, WebSocketDisconnect

import re
import time

from sqlglot import parse_one

from .rest_controllers import (
    conn_manager,
    cache_repo,
    llm_service,
)

from infrastructure.repositories.bigquery_repository import (
    BigQueryRepository,
)

from infrastructure.repositories.postgres_repository import (
    PostgresRepository,
)

from agents.langchain_agent import create_agent

from tools.detect_issues import detect_issues


# ================================================================
# SQL EXTRACTION HELPERS
# ================================================================

def extract_sql(text: str) -> str:
    """
    Extract SQL from the LangChain/Groq response.

    Priority:
        1. ```sql ... ```
        2. ``` ... ```
        3. SELECT/WITH statement ending with semicolon
        4. SELECT/WITH statement without semicolon
        5. Original text
    """

    if not text:
        return ""

    text = text.strip()

    # ------------------------------------------------------------
    # 1. Prefer a fenced SQL block
    # ------------------------------------------------------------

    sql_block = re.search(
        r"```sql\s*(.*?)```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if sql_block:
        return sql_block.group(1).strip()

    # ------------------------------------------------------------
    # 2. Generic fenced code block
    # ------------------------------------------------------------

    generic_block = re.search(
        r"```\s*(.*?)```",
        text,
        flags=re.DOTALL,
    )

    if generic_block:
        candidate = generic_block.group(1).strip()

        if re.match(
            r"^(SELECT|WITH)\b",
            candidate,
            flags=re.IGNORECASE,
        ):
            return candidate

    # ------------------------------------------------------------
    # 3. Find SELECT/WITH followed by a semicolon
    # ------------------------------------------------------------

    statement_match = re.search(
        r"(?is)\b(?:SELECT|WITH)\b.*?;",
        text,
    )

    if statement_match:
        return statement_match.group(0).strip()

    # ------------------------------------------------------------
    # 4. Find SELECT/WITH without semicolon
    #
    # Use the first SQL-looking line onward.
    # ------------------------------------------------------------

    fallback_match = re.search(
        r"(?is)\b(?:SELECT|WITH)\b.*",
        text,
    )

    if fallback_match:
        candidate = fallback_match.group(0).strip()

        # Remove obvious trailing markdown sections if present.
        candidate = re.split(
            r"\n\s*(?:\*\*Explanation\*\*|Explanation:|Why:)",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

        return candidate

    # ------------------------------------------------------------
    # 5. Final fallback
    # ------------------------------------------------------------

    return text


def validate_sql(
    sql: str,
    database_type: str = "bigquery",
) -> bool:
    """
    Basic SQL syntax validation using SQLGlot.

    This is only an MVP validation layer.
    BigQuery dry-run remains the authoritative validation.
    """

    if not sql or not sql.strip():
        return False

    dialect = (
        "postgres"
        if database_type.lower()
        in {"postgres", "postgresql"}
        else "bigquery"
    )

    try:
        parsed = parse_one(
            sql,
            dialect=dialect,
        )

        return parsed is not None

    except Exception as exc:
        print(
            f"[WebSocket] SQL parser validation failed: {exc}"
        )
        return False


def clean_explanation(
    agent_output: str,
    optimized_sql: str,
) -> str:
    """
    Remove the extracted SQL from the agent response
    so that the remaining text can be displayed as explanation.
    """

    if not agent_output:
        return ""

    if not optimized_sql:
        return agent_output.strip()

    explanation = agent_output.replace(
        optimized_sql,
        "",
        1,
    ).strip()

    # Remove markdown fences left behind.
    explanation = re.sub(
        r"```sql\s*",
        "",
        explanation,
        flags=re.IGNORECASE,
    )

    explanation = explanation.replace(
        "```",
        "",
    )

    return explanation.strip()


# ================================================================
# WEBSOCKET ENDPOINT
# ================================================================

async def websocket_endpoint(
    websocket: WebSocket,
):
    await websocket.accept()

    print(
        "[WebSocket] Connection accepted"
    )

    start_time = time.time()

    try:

        # ========================================================
        # 1. RECEIVE REQUEST
        # ========================================================

        data = await websocket.receive_json()

        print(
            f"[WebSocket] Received: {data}"
        )

        session_id = data.get(
            "session_id"
        )

        query = data.get(
            "sql_query"
        )

        if not session_id or not query:

            print(
                "[WebSocket] Missing "
                "session_id or query"
            )

            await websocket.send_json(
                {
                    "error":
                        "Missing session_id or sql_query"
                }
            )

            return

        # ========================================================
        # 2. GET CONNECTION
        # ========================================================

        print(
            f"[WebSocket] Looking for session: "
            f"{session_id}"
        )

        conn_obj = await conn_manager.get_connection(
            session_id
        )

        if not conn_obj:

            await websocket.send_json(
                {
                    "error": "Session not found"
                }
            )

            return

        database_type = conn_obj["type"]

        print(
            f"[WebSocket] Connection found: "
            f"{database_type}"
        )

        # ========================================================
        # 3. CREATE DATABASE REPOSITORY
        # ========================================================

        if database_type == "bigquery":

            db_repo = BigQueryRepository(
                conn_obj["client"],
                conn_obj.get("dataset"),
            )

        elif database_type == "postgresql":

            db_repo = PostgresRepository(
                conn_obj["engine"]
            )

        else:

            await websocket.send_json(
                {
                    "error":
                        "Unsupported database type"
                }
            )

            return

        # ========================================================
        # 4. KNOWLEDGE CATALOG
        # ========================================================

        knowledge_catalog = None

        if database_type == "bigquery":

            try:

                from infrastructure.services.knowledge_catalog import (
                    KnowledgeCatalogService,
                )

                knowledge_catalog = (
                    KnowledgeCatalogService(
                        project_id=conn_obj[
                            "client"
                        ].project,
                        location="us-central1",
                        lake="sql-optimizer-lake",
                        zone="bigquery-zone",
                        dataset=conn_obj.get(
                            "dataset"
                        ),
                    )
                )

                print(
                    "[WebSocket] Knowledge Catalog "
                    "service initialized"
                )

            except Exception as exc:

                print(
                    "[WebSocket] Knowledge Catalog "
                    f"not available: {exc}"
                )

        # ========================================================
        # 5. GET SCHEMA
        # ========================================================

        schema = await conn_manager.get_schema(
            session_id
        )

        if not schema:

            await websocket.send_json(
                {
                    "error":
                        "Schema not found for session"
                }
            )

            return

        # ========================================================
        # 6. MEMORY
        # ========================================================

        memory = await conn_manager.get_memory(
            session_id
        )

        # ========================================================
        # 7. CREATE LANGCHAIN AGENT
        # ========================================================

        agent = create_agent(
            session_id=session_id,
            schema=schema,
            db_repo=db_repo,
            knowledge_catalog=knowledge_catalog,
            llm_service=llm_service,
            memory=memory,
        )

        print(
            "[WebSocket] Executing "
            "LangChain agent..."
        )

        # ========================================================
        # 8. INVOKE AGENT
        # ========================================================

        result = await agent.ainvoke(
            {
                "input": query
            }
        )

        agent_output = result.get(
            "output",
            "",
        )

        if not agent_output:

            agent_output = (
                "No optimization provided."
            )

        print(
            "[WebSocket] Raw agent output:"
        )

        print(
            agent_output
        )

        # ========================================================
        # 9. EXTRACT OPTIMIZED SQL
        # ========================================================

        optimized_sql = extract_sql(
            agent_output
        )

        print(
            "[WebSocket] Extracted optimized SQL:"
        )

        print(
            optimized_sql
        )

        # ========================================================
        # 10. BASIC SQL VALIDATION
        # ========================================================

        sql_is_valid = validate_sql(
            optimized_sql,
            database_type,
        )

        if not sql_is_valid:

            print(
                "[WebSocket] Extracted SQL "
                "failed parser validation."
            )

            print(
                "[WebSocket] Falling back "
                "to original query."
            )

            optimized_sql = query

        # ========================================================
        # 11. EXPLANATION FROM AGENT OUTPUT
        # ========================================================

        explanation = clean_explanation(
            agent_output,
            optimized_sql,
        )

        # ========================================================
        # 12. DETECT ISSUES
        # ========================================================

        dialect = (
            "postgres"
            if database_type.lower()
            in {
                "postgres",
                "postgresql",
            }
            else "bigquery"
        )

        issues_result = await detect_issues(
            query
        )

        if isinstance(
            issues_result,
            dict,
        ):

            issues = issues_result.get(
                "issues",
                [],
            )

        elif isinstance(
            issues_result,
            list,
        ):

            issues = issues_result

        else:

            issues = []

        # ========================================================
        # 13. DRY RUN OPTIMIZED QUERY
        # ========================================================

        bytes_processed = 0
        cost = 0.0

        try:

            print(
                "[WebSocket] Running dry-run "
                "for optimized SQL..."
            )

            plan = await db_repo.get_execution_plan(
                optimized_sql
            )

            bytes_processed = (
                plan.total_bytes_processed
                or 0
            )

            cost = (
                bytes_processed / 1e12
            ) * 5.0

            cost = round(
                cost,
                6,
            )

            print(
                "[WebSocket] Optimized SQL "
                "dry-run succeeded."
            )

        except Exception as exc:

            print(
                "[WebSocket] Optimized SQL "
                f"dry-run failed: {exc}"
            )

            # ====================================================
            # FALLBACK TO ORIGINAL QUERY
            # ====================================================

            print(
                "[WebSocket] Falling back "
                "to original query for metrics."
            )

            optimized_sql = query

            explanation = (
                explanation
                + "\n\n"
                "The generated optimized query "
                "could not be validated by BigQuery, "
                "so the original query was retained."
            ).strip()

            try:

                original_plan = (
                    await db_repo.get_execution_plan(
                        query
                    )
                )

                bytes_processed = (
                    original_plan.total_bytes_processed
                    or 0
                )

                cost = (
                    bytes_processed / 1e12
                ) * 5.0

                cost = round(
                    cost,
                    6,
                )

                print(
                    "[WebSocket] Original query "
                    "dry-run succeeded."
                )

            except Exception as original_exc:

                print(
                    "[WebSocket] Original query "
                    f"dry-run also failed: "
                    f"{original_exc}"
                )

                bytes_processed = 0
                cost = 0.0

        # ========================================================
        # 14. EXECUTION / ANALYSIS TIME
        # ========================================================

        execution_time_ms = (
            time.time()
            - start_time
        ) * 1000

        # ========================================================
        # 15. GEMINI EXPLANATION STREAM
        # ========================================================

        prompt = f"""
You are a SQL performance expert.

Explain the result of the SQL optimization.

Original query:
{query}

Optimized query:
{optimized_sql}

Detected issues:
{', '.join(issues)}

Estimated bytes processed:
{bytes_processed}

Estimated cost:
${cost:.6f}

Explain:
- what issues were detected
- what was changed
- why the change may help

Do not generate new SQL.

Maximum 100 words.
"""

        chunk_count = 0

        try:

            async for chunk in (
                llm_service.stream_explain(
                    prompt
                )
            ):

                if not chunk:
                    continue

                chunk_count += 1

                print(
                    f"[WebSocket] "
                    f"Sending chunk "
                    f"{chunk_count}"
                )

                await websocket.send_json(
                    {
                        "type": "token",
                        "content": chunk,
                    }
                )

        except Exception as exc:

            print(
                "[WebSocket] Streaming error: "
                f"{exc}"
            )

            await websocket.send_json(
                {
                    "type": "token",
                    "content": (
                        explanation
                        or "No explanation available."
                    ),
                }
            )

        # ========================================================
        # 16. FINAL RESULT
        # ========================================================

        response = {
            "original_query": query,

            "optimized_query": optimized_sql,

            "issues": issues,

            "explanation": explanation,

            "estimated_cost_usd": cost,

            "execution_time_ms": round(
                execution_time_ms,
                2,
            ),

            "bytes_processed": bytes_processed,
        }

        await websocket.send_json(
            {
                "type": "result",
                "data": response,
            }
        )

        await websocket.send_json(
            {
                "type": "done"
            }
        )

        print(
            "[WebSocket] Done message sent"
        )

    except WebSocketDisconnect:

        print(
            "[WebSocket] Client disconnected"
        )

    except Exception as exc:

        print(
            f"[WebSocket] Error: {exc}"
        )

        import traceback

        traceback.print_exc()

        try:

            await websocket.send_json(
                {
                    "error": str(exc)
                }
            )

        except Exception:
            pass

    finally:

        try:
            await websocket.close()

        except Exception:
            pass

        print(
            "[WebSocket] Connection closed"
        )