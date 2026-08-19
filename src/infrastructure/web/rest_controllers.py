from fastapi import APIRouter
from pydantic import BaseModel

import re
import time

from sqlglot import parse_one

from infrastructure.services.connection_manager_impl import (
    ConnectionManagerImpl,
)

from infrastructure.repositories.redis_cache import (
    RedisCacheRepository,
)

from infrastructure.services.gemini_llm import (
    GeminiLLMService,
)

from infrastructure.repositories.bigquery_repository import (
    BigQueryRepository,
)

from infrastructure.repositories.postgres_repository import (
    PostgresRepository,
)

from agents.langchain_agent import create_agent

from tools.detect_issues import detect_issues


router = APIRouter()


# ============================================================
# GLOBAL SERVICES
# ============================================================

cache_repo = RedisCacheRepository()

conn_manager = ConnectionManagerImpl(
    cache_repo
)

llm_service = GeminiLLMService()


# ============================================================
# REQUEST MODELS
# ============================================================

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


# ============================================================
# SQL EXTRACTION
# ============================================================

def extract_sql(text: str) -> str:
    """
    Robust extraction of SQL from an LLM response.

    Priority:
        1. ```sql ... ```
        2. ``` ... ```
        3. SELECT/WITH ending with semicolon
        4. SELECT/WITH without semicolon
    """

    if not text:
        return ""

    text = text.strip()

    # --------------------------------------------------------
    # 1. SQL fenced code block
    # --------------------------------------------------------

    sql_block = re.search(
        r"```sql\s*(.*?)```",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    if sql_block:

        return (
            sql_block
            .group(1)
            .strip()
        )

    # --------------------------------------------------------
    # 2. Generic fenced block
    # --------------------------------------------------------

    generic_block = re.search(
        r"```\s*(.*?)```",
        text,
        flags=re.DOTALL,
    )

    if generic_block:

        candidate = (
            generic_block
            .group(1)
            .strip()
        )

        if re.match(
            r"^(SELECT|WITH)\b",
            candidate,
            flags=re.IGNORECASE,
        ):

            return candidate

    # --------------------------------------------------------
    # 3. SELECT/WITH with semicolon
    # --------------------------------------------------------

    statement_match = re.search(
        r"(?is)\b(?:SELECT|WITH)\b.*?;",
        text,
    )

    if statement_match:

        return (
            statement_match
            .group(0)
            .strip()
        )

    # --------------------------------------------------------
    # 4. SELECT/WITH without semicolon
    # --------------------------------------------------------

    fallback = re.search(
        r"(?is)\b(?:SELECT|WITH)\b.*",
        text,
    )

    if fallback:

        candidate = (
            fallback
            .group(0)
            .strip()
        )

        candidate = re.split(
            r"\n\s*(?:\*\*Explanation\*\*|Explanation:|Why:)",
            candidate,
            maxsplit=1,
            flags=re.IGNORECASE,
        )[0].strip()

        return candidate

    return text


def validate_sql(
    sql: str,
    database_type: str,
) -> bool:

    if not sql or not sql.strip():
        return False

    dialect = (
        "postgres"
        if database_type.lower()
        in {
            "postgres",
            "postgresql",
        }
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
            "[REST] SQL validation failed: "
            f"{exc}"
        )

        return False


# ============================================================
# CONNECT
# ============================================================

@router.post("/connect")
async def connect(
    request: ConnectRequest,
):
    """
    Connect to a database and return a session ID.
    """

    credentials = request.model_dump(
        exclude_none=True
    )

    session_id = await conn_manager.connect(
        credentials
    )

    schema = await conn_manager.get_schema(
        session_id
    )

    if not schema:
        raise ValueError(
            "Schema not found after connection"
        )

    tables = [
        table.name
        for table in schema.tables
    ]

    # Initialize memory
    await conn_manager.init_memory(
        session_id
    )

    return {
        "session_id": session_id,
        "tables": tables,
    }


# ============================================================
# ANALYZE
# ============================================================

@router.post("/analyze")
async def analyze(
    request: AnalyzeRequest,
):
    """
    Original MVP REST analysis endpoint.

    Uses the LangChain/Groq agent but protects the
    generated SQL with parsing and BigQuery dry-run validation.
    """

    start_time = time.time()

    # ========================================================
    # 1. GET CONNECTION
    # ========================================================

    conn_obj = await conn_manager.get_connection(
        request.session_id
    )

    if not conn_obj:
        raise ValueError(
            "Session not found"
        )

    database_type = conn_obj["type"]

    dataset = conn_obj.get(
        "dataset"
    )

    # ========================================================
    # 2. CREATE DATABASE REPOSITORY
    # ========================================================

    if database_type == "bigquery":

        db_repo = BigQueryRepository(
            conn_obj["client"],
            dataset,
        )

    elif database_type == "postgresql":

        db_repo = PostgresRepository(
            conn_obj["engine"]
        )

    else:

        raise ValueError(
            "Unsupported database type"
        )

    # ========================================================
    # 3. KNOWLEDGE CATALOG
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
                    dataset=dataset,
                )
            )

            print(
                "[REST] Knowledge Catalog "
                "service initialized"
            )

        except Exception as exc:

            print(
                "[REST] Knowledge Catalog "
                f"not available: {exc}"
            )

    # ========================================================
    # 4. GET SCHEMA
    # ========================================================

    schema = await conn_manager.get_schema(
        request.session_id
    )

    if not schema:

        raise ValueError(
            "Schema not found for session"
        )

    # ========================================================
    # 5. MEMORY
    # ========================================================

    memory = await conn_manager.get_memory(
        request.session_id
    )

    # ========================================================
    # 6. CREATE AGENT
    # ========================================================

    agent = create_agent(
        session_id=request.session_id,
        schema=schema,
        db_repo=db_repo,
        knowledge_catalog=knowledge_catalog,
        llm_service=llm_service,
        memory=memory,
    )

    print(
        "[REST] Executing LangChain agent..."
    )

    # ========================================================
    # 7. INVOKE AGENT
    # ========================================================

    result = await agent.ainvoke(
        {
            "input": request.sql_query
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
        "[REST] Raw agent output:"
    )

    print(
        agent_output
    )

    # ========================================================
    # 8. EXTRACT SQL
    # ========================================================

    optimized_sql = extract_sql(
        agent_output
    )

    print(
        "[REST] Extracted optimized SQL:"
    )

    print(
        optimized_sql
    )

    # ========================================================
    # 9. PARSER VALIDATION
    # ========================================================

    if not validate_sql(
        optimized_sql,
        database_type,
    ):

        print(
            "[REST] Extracted SQL failed "
            "parser validation."
        )

        print(
            "[REST] Falling back to original query."
        )

        optimized_sql = request.sql_query

    # ========================================================
    # 10. EXPLANATION
    # ========================================================

    explanation = agent_output.replace(
        optimized_sql,
        "",
        1,
    ).strip()

    if not explanation:

        explanation = (
            "The query was analyzed and "
            "an optimized candidate was generated."
        )

    # ========================================================
    # 11. ISSUE DETECTION
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
        request.sql_query
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
    # 12. DRY RUN OPTIMIZED QUERY
    # ========================================================

    bytes_processed = 0
    cost = 0.0

    try:

        print(
            "[REST] Running dry-run "
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
            "[REST] Optimized SQL "
            "dry-run succeeded."
        )

    except Exception as exc:

        print(
            "[REST] Optimized SQL dry-run "
            f"failed: {exc}"
        )

        # ====================================================
        # FALLBACK TO ORIGINAL QUERY
        # ====================================================

        optimized_sql = request.sql_query

        explanation += (
            "\n\n"
            "The generated optimized query "
            "could not be validated by the database, "
            "so the original query was retained."
        )

        try:

            print(
                "[REST] Running dry-run "
                "for original SQL..."
            )

            original_plan = (
                await db_repo.get_execution_plan(
                    request.sql_query
                )
            )

            bytes_processed = (
                original_plan
                .total_bytes_processed
                or 0
            )

            cost = (
                bytes_processed / 1e12
            ) * 5.0

            cost = round(
                cost,
                6,
            )

        except Exception as original_exc:

            print(
                "[REST] Original SQL dry-run "
                f"also failed: {original_exc}"
            )

            bytes_processed = 0
            cost = 0.0

    # ========================================================
    # 13. TIMING
    # ========================================================

    execution_time_ms = (
        time.time()
        - start_time
    ) * 1000

    # ========================================================
    # 14. RESPONSE
    # ========================================================

    return {
        "original_query":
            request.sql_query,

        "optimized_query":
            optimized_sql,

        "issues":
            issues,

        "explanation":
            explanation,

        "estimated_cost_usd":
            cost,

        "execution_time_ms":
            round(
                execution_time_ms,
                2,
            ),

        "bytes_processed":
            bytes_processed,
    }


# ============================================================
# HELPERS
# ============================================================

def get_connection_manager():
    return conn_manager


def get_analyze_use_case():
    return None