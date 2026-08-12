from domain.sql_query import SQLQuery
from application.interfaces.database_repository import DatabaseRepository
from application.interfaces.cache_repository import CacheRepository
from application.interfaces.llm_service import LLMService
from application.interfaces.connection_manager import ConnectionManager
from tools.detect_issues import detect_issues
from tools.optimize_sql import optimize_sql
from tools.recommend_structures import recommend_structures
from typing import Optional
from infrastructure.services.knowledge_catalog import KnowledgeCatalogService

class AnalyzeQueryUseCase:
    def __init__(
        self,
        conn_manager: ConnectionManager,
        db_repo: DatabaseRepository,
        cache_repo: CacheRepository,
        llm_service: LLMService,
        knowledge_catalog: Optional[KnowledgeCatalogService] = None
    ):
        self.conn_manager = conn_manager
        self.db_repo = db_repo
        self.cache_repo = cache_repo
        self.llm_service = llm_service
        self.knowledge_catalog = knowledge_catalog

    async def execute(self, query: str, session_id: str, dataset: str = None) -> SQLQuery:
        # 1. Get schema from connection manager
        schema = await self.conn_manager.get_schema(session_id)
        if not schema:
            raise ValueError("Schema not found for session")

        # 2. Check cache for this query (optional)
        cache_key = f"query:{hash(query)}"
        cached = await self.cache_repo.get(cache_key)
        if cached:
            return SQLQuery(**cached)

        # 3. Detect issues (generic)
        issues = await detect_issues(query)

        # 4. Intelligent optimization using schema, dataset, db_repo, and Knowledge Catalog
        opt_result = await optimize_sql(
            query,
            schema,
            dataset=dataset,
            db_repo=self.db_repo,                       
            knowledge_catalog=self.knowledge_catalog
        )
        optimized = opt_result.get("optimized_sql", query)

        # 5. Get execution plan
        plan = await self.db_repo.get_execution_plan(optimized)

        # 6. Build dynamic prompt with schema
        prompt = self._build_prompt(query, schema, issues, plan)

        # 7. Generate explanation
        explanation = await self.llm_service.explain(query, plan, issues)

        # 8. Estimate cost (simplified)
        cost = (plan.total_bytes_processed / 1e12) * 5.0

        # 9. Recommendations (generic)
        recommendations = await recommend_structures(query, schema)

        result = SQLQuery(
            text=query,
            issues=issues,
            optimized_text=optimized,
            explanation=explanation,
            cost_estimate=cost
        )

        # Cache result
        await self.cache_repo.set(cache_key, result.__dict__, ttl=600)
        return result

    def _build_prompt(self, query: str, schema, issues, plan) -> str:
        tables_str = "\n".join([
            f"- {t.name} ({', '.join([c.name for c in t.columns])})"
            for t in schema.tables
        ])
        return f"""
        You are a SQL performance expert.
        The user's database has the following schema:
        {tables_str}

        The user query is: {query}
        Detected issues: {', '.join(issues)}
        Execution plan: bytes processed {plan.total_bytes_processed}, scan type {plan.scan_type}

        Explain why it's slow and how to improve it.
        Provide actionable advice (max 150 words).
        """