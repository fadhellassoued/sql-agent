from fastapi import WebSocket, WebSocketDisconnect
import json
from .rest_controllers import conn_manager, cache_repo, llm_service
from infrastructure.repositories.bigquery_repository import BigQueryRepository
from infrastructure.repositories.postgres_repository import PostgresRepository
from agents.langchain_agent import create_agent
from tools.detect_issues import detect_issues
import re
import time

async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("[WebSocket] Connection accepted")
    start_time = time.time()
    try:
        # Receive initial message
        data = await websocket.receive_json()
        print(f"[WebSocket] Received: {data}")
        
        session_id = data.get("session_id")
        query = data.get("sql_query")
        if not session_id or not query:
            print("[WebSocket] Missing session_id or query")
            await websocket.send_json({"error": "Missing session_id or sql_query"})
            await websocket.close()
            return

        print(f"[WebSocket] Looking for session: {session_id}")
        conn_obj = await conn_manager.get_connection(session_id)
        print(f"[WebSocket] Connection found: {conn_obj['type']}")

        # Create repository
        if conn_obj["type"] == "bigquery":
            db_repo = BigQueryRepository(conn_obj["client"], conn_obj.get("dataset"))
        elif conn_obj["type"] == "postgresql":
            db_repo = PostgresRepository(conn_obj["engine"])
        else:
            await websocket.send_json({"error": "Unsupported database type"})
            await websocket.close()
            return

        # Try to instantiate Knowledge Catalog service
        knowledge_catalog = None
        if conn_obj["type"] == "bigquery":
            try:
                from infrastructure.services.knowledge_catalog import KnowledgeCatalogService
                knowledge_catalog = KnowledgeCatalogService(
                    project_id=conn_obj["client"].project,
                    location="us-central1",
                    lake="sql-optimizer-lake",
                    zone="bigquery-zone",
                    dataset=conn_obj.get("dataset")
                )
                print("[WebSocket] Knowledge Catalog service initialized")
            except Exception as e:
                print(f"[WebSocket] Knowledge Catalog not available: {e}")

        # Get schema for the session
        schema = await conn_manager.get_schema(session_id)
        if not schema:
            await websocket.send_json({"error": "Schema not found for session"})
            await websocket.close()
            return

        # Get or create memory for this session
        memory = await conn_manager.get_memory(session_id)

        # Create the LangChain agent
        agent = create_agent(
            session_id=session_id,
            schema=schema,
            db_repo=db_repo,
            knowledge_catalog=knowledge_catalog,
            llm_service=llm_service,
            memory=memory
        )

        print("[WebSocket] Executing LangChain agent...")
        
        # Invoke the agent with the query
        result = await agent.ainvoke({"input": query})
        agent_output = result.get("output", "No optimization provided")
        
        # ============================================================
        # EXTRACT OPTIMIZED SQL AND ISSUES
        # ============================================================
        def extract_sql(text: str) -> str:
            text = re.sub(r'```sql\s*', '', text, flags=re.IGNORECASE)
            text = re.sub(r'```\s*', '', text, flags=re.IGNORECASE)
            match = re.search(
                r'(?P<sql>(?:WITH\s+.*?\s+)?SELECT\s+.*?)(?=;|\s*\n\s*(?!(AND|OR|WHERE|GROUP|HAVING|ORDER|LIMIT|JOIN|ON|USING|UNION|INTERSECT|EXCEPT)\b)\s*[A-Za-z])',
                text,
                re.IGNORECASE | re.DOTALL
            )
            if match:
                sql = match.group('sql').strip()
                if ';' in sql:
                    sql = sql.split(';')[0].strip() + ';'
                return sql
            fallback = re.search(r'(SELECT\s+.*?;?)', text, re.IGNORECASE | re.DOTALL)
            if fallback:
                return fallback.group(1).strip()
            return text.strip()

        optimized_sql = extract_sql(agent_output)
        explanation = agent_output.replace(optimized_sql, "").strip()
        if explanation == optimized_sql:
            explanation = ""

        # Run detect_issues on the original query
        issues_result = await detect_issues(query)
        if isinstance(issues_result, dict):
            issues = issues_result.get("issues", [])
        elif isinstance(issues_result, list):
            issues = issues_result
        else:
            issues = []

        # Dry-run for cost
        try:
            plan = await db_repo.get_execution_plan(optimized_sql)
            bytes_processed = plan.total_bytes_processed
            cost = (bytes_processed / 1e12) * 5.0
            cost = round(cost, 6)
        except Exception as e:
            print(f"[WebSocket] Error getting execution plan: {e}")
            cost = 0.0

        execution_time_ms = (time.time() - start_time) * 1000

        # ============================================================
        # STREAM THE EXPLANATION TOKEN BY TOKEN
        # ============================================================
        # Build the prompt for the LLM
        prompt = f"""
        You are a SQL performance expert. Explain the optimization of this query in a clear, concise way.
        Original query: {query}
        Optimized query: {optimized_sql}
        Issues found: {', '.join(issues)}
        Provide a brief explanation (max 100 words) of what was improved and why.
        """

        # Try to stream the explanation
        chunk_count = 0 
        try:
            async for chunk in llm_service.stream_explain(prompt):
                chunk_count += 1
                print(f"[WebSocket] Sending chunk {chunk_count}")
                await websocket.send_json({"type": "token", "content": chunk})
        except Exception as e:
            print(f"[WebSocket] Streaming error: {e}")
            # Fallback: send the pre‑extracted explanation as a single token
            await websocket.send_json({"type": "token", "content": explanation or "No explanation available."})

        # Send the final result with all data
        response = {
            "original_query": query,
            "optimized_query": optimized_sql,
            "issues": issues,
            "explanation": explanation,
            "estimated_cost_usd": cost,
            "execution_time_ms": round(execution_time_ms, 2),
        }
        await websocket.send_json({"type": "result", "data": response})
        await websocket.send_json({"type": "done"})

        print("[WebSocket] Done message sent")

    except WebSocketDisconnect:
        print("[WebSocket] Client disconnected")
    except Exception as e:
        print(f"[WebSocket] Error: {str(e)}")
        import traceback
        traceback.print_exc()
        await websocket.send_json({"error": str(e)})
    finally:
        await websocket.close()
        print("[WebSocket] Connection closed")