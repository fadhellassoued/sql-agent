from fastapi import APIRouter, Depends
from pydantic import BaseModel
from application.use_cases.connect_database import ConnectDatabaseUseCase
from infrastructure.services.connection_manager_impl import ConnectionManagerImpl
from infrastructure.repositories.redis_cache import RedisCacheRepository
from infrastructure.services.gemini_llm import GeminiLLMService
from src.config import settings
from infrastructure.repositories.bigquery_repository import BigQueryRepository
from infrastructure.repositories.postgres_repository import PostgresRepository
from agents.langchain_agent import create_agent
from tools.detect_issues import detect_issues
import re
import time

router = APIRouter()

# ============================================================
# Create a SINGLE global instance of ConnectionManager
# ============================================================
cache_repo = RedisCacheRepository()
conn_manager = ConnectionManagerImpl(cache_repo)
llm_service = GeminiLLMService()

class ConnectRequest(BaseModel):
    db_type: str
    host: str = None
    port: int = None
    database: str = None
    username: str = None
    password: str = None
    project_id: str = None
    dataset: str = None
    credentials_json: str = None

class AnalyzeRequest(BaseModel):
    session_id: str
    sql_query: str

@router.post("/connect")
async def connect(request: ConnectRequest):
    """Connect to a database and return a session ID."""
    credentials = request.dict(exclude_none=True)
    session_id = await conn_manager.connect(credentials)
    
    # Get tables from the schema
    schema = await conn_manager.get_schema(session_id)
    tables = [t.name for t in schema.tables]
    
    # Initialize memory for this session
    await conn_manager.init_memory(session_id)
    
    return {"session_id": session_id, "tables": tables}

@router.post("/analyze")
async def analyze(request: AnalyzeRequest):
    """Analyze and optimize a SQL query using LangChain agent."""
    start_time = time.time()
    
    # Get the connection object for this session
    conn_obj = await conn_manager.get_connection(request.session_id)
    
    # Get dataset name from the connection object
    dataset = conn_obj.get("dataset")
    
    # Create the appropriate database repository
    if conn_obj["type"] == "bigquery":
        db_repo = BigQueryRepository(conn_obj["client"], conn_obj.get("dataset"))
    elif conn_obj["type"] == "postgresql":
        db_repo = PostgresRepository(conn_obj["engine"])
    else:
        raise ValueError("Unsupported database type")

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
                dataset=dataset
            )
            print("[REST] Knowledge Catalog service initialized")
        except Exception as e:
            print(f"[REST] Knowledge Catalog not available: {e}")

    # Get schema for the session
    schema = await conn_manager.get_schema(request.session_id)
    if not schema:
        raise ValueError("Schema not found for session")

    # Get memory for this session
    memory = await conn_manager.get_memory(request.session_id)

    # Create the LangChain agent
    agent = create_agent(
        session_id=request.session_id,
        schema=schema,
        db_repo=db_repo,
        knowledge_catalog=knowledge_catalog,
        llm_service=llm_service,
        memory=memory
    )

    print("[REST] Executing LangChain agent...")
    
    # Invoke the agent with the query
    result = await agent.ainvoke({"input": request.sql_query})
    
    # The agent returns a dictionary with "output" key
    agent_output = result.get("output", "No optimization provided")
    
    # ============================================================
    # ROBUST SQL EXTRACTION (handles multi-line SQL and explanations)
    # ============================================================
    def extract_sql(text: str) -> str:
        # Remove markdown code fences
        text = re.sub(r'```sql\s*', '', text, flags=re.IGNORECASE)
        text = re.sub(r'```\s*', '', text, flags=re.IGNORECASE)
        # Match the first SELECT statement (including CTEs)
        # We capture until a semicolon OR until a newline followed by a word that isn't part of SQL (e.g., "This", "The", etc.)
        match = re.search(
            r'(?P<sql>(?:WITH\s+.*?\s+)?SELECT\s+.*?)(?=;|\s*\n\s*(?!(AND|OR|WHERE|GROUP|HAVING|ORDER|LIMIT|JOIN|ON|USING|UNION|INTERSECT|EXCEPT)\b)\s*[A-Za-z])',
            text,
            re.IGNORECASE | re.DOTALL
        )
        if match:
            sql = match.group('sql').strip()
            # If there is a semicolon inside, split at the first semicolon to avoid extra text
            if ';' in sql:
                sql = sql.split(';')[0].strip() + ';'
            return sql
        # Fallback: try to find a SELECT statement without the context
        fallback = re.search(r'(SELECT\s+.*?;?)', text, re.IGNORECASE | re.DOTALL)
        if fallback:
            return fallback.group(1).strip()
        return text.strip()

    optimized_sql = extract_sql(agent_output)
    explanation = agent_output.replace(optimized_sql, "").strip()
    if explanation == optimized_sql:
        explanation = ""

    # Run detect_issues on the original query (with safety check)
    issues_result = await detect_issues(request.sql_query)
    if isinstance(issues_result, dict):
        issues = issues_result.get("issues", [])
    elif isinstance(issues_result, list):
        issues = issues_result
    else:
        issues = []

    # Run dry-run on the optimized query to get bytes processed
    try:
        plan = await db_repo.get_execution_plan(optimized_sql)
        bytes_processed = plan.total_bytes_processed
        cost = (bytes_processed / 1e12) * 5.0  # $5 per TB
        cost = round(cost, 6)
    except Exception as e:
        print(f"[REST] Error getting execution plan: {e}")
        bytes_processed = 0
        cost = 0.0

    # Execution time (approximate)
    execution_time_ms = (time.time() - start_time) * 1000

    return {
        "original_query": request.sql_query,
        "optimized_query": optimized_sql,
        "issues": issues,
        "explanation": explanation if explanation else "Query optimized.",
        "estimated_cost_usd": cost,
        "execution_time_ms": round(execution_time_ms, 2),
    }

def get_connection_manager():
    return conn_manager

def get_analyze_use_case():
    # This would need to be implemented with proper dependencies
    pass