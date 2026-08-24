from typing import Optional

from domain.sql_query import SQLQuery

from application.interfaces.database_repository import DatabaseRepository
from application.interfaces.cache_repository import CacheRepository
from application.interfaces.llm_service import LLMService
from application.interfaces.connection_manager import ConnectionManager

from tools.detect_issues import detect_issues
from tools.optimize_sql import optimize_sql
from tools.recommend_structures import recommend_structures

from infrastructure.services.knowledge_catalog import KnowledgeCatalogService


class AnalyzeQueryUseCase:
    """
    Canonical deterministic SQL analysis pipeline.

    Responsibility:

        1. Discover schema
        2. Detect SQL issues
        3. Generate deterministic candidate SQL
        4. Collect execution evidence
        5. Generate recommendations
        6. Ask the LLM to explain the evidence

    The LLM does NOT generate the optimized SQL.
    """

    def __init__(
        self,
        conn_manager: ConnectionManager,
        db_repo: DatabaseRepository,
        cache_repo: CacheRepository,
        llm_service: LLMService,
        knowledge_catalog: Optional[
            KnowledgeCatalogService
        ] = None,
        database_type: str = "bigquery",
    ):
        self.conn_manager = conn_manager
        self.db_repo = db_repo
        self.cache_repo = cache_repo
        self.llm_service = llm_service
        self.knowledge_catalog = knowledge_catalog
        self.database_type = database_type

    async def execute(
        self,
        query: str,
        session_id: str,
        dataset: str = None,
    ) -> SQLQuery:

        # =========================================================
        # 1. GET DATABASE SCHEMA
        # =========================================================
        schema = await self.conn_manager.get_schema(
            session_id
        )

        if not schema:
            raise ValueError(
                "Schema not found for session."
            )

        # =========================================================
        # 2. CACHE
        # =========================================================
        cache_key = self._build_cache_key(
            query=query,
            session_id=session_id,
        )

        cached = await self.cache_repo.get(
            cache_key
        )

        if cached:
            return SQLQuery(
                **cached
            )

        # =========================================================
        # 3. DETECT ISSUES
        # =========================================================
        dialect = (
            "postgres"
            if self.database_type.lower()
            in {"postgres", "postgresql"}
            else "bigquery"
        )

        issues = await detect_issues(
            query,
            dialect=dialect,
        )

        # =========================================================
        # 4. DETERMINISTIC OPTIMIZATION
        # =========================================================
        optimization_result = await optimize_sql(
            query,
            schema,
            dataset=dataset,
            db_repo=self.db_repo,
            knowledge_catalog=self.knowledge_catalog,
        )

        optimized = optimization_result.get(
            "optimized_sql",
            query,
        )

        rules_applied = optimization_result.get(
            "rules_applied",
            [],
        )

        # =========================================================
        # 5. EXECUTION / DRY-RUN PLAN
        # =========================================================
        plan = await self.db_repo.get_execution_plan(
            optimized
        )
        sql_query.execution_plan = plan

        # =========================================================
        # 6. DATABASE-SPECIFIC RECOMMENDATIONS
        # =========================================================
        recommendations = await recommend_structures(
            sql=query,
            schema=schema,
            database_type=self.database_type,
        )

        # =========================================================
        # 7. LLM EXPLANATION
        # =========================================================
        explanation = await self.llm_service.explain(
            optimized,
            plan,
            issues,
        )

        # =========================================================
        # 8. BUILD RESULT
        # =========================================================
        result = SQLQuery(
            text=query,
            issues=issues,
            optimized_text=optimized,
            explanation=explanation,
            cost_estimate=None,
            recommendations=recommendations,
            optimization_rules=rules_applied,
        )

        # =========================================================
        # 9. CACHE RESULT
        # =========================================================
        await self.cache_repo.set(
            cache_key,
            result.__dict__,
            ttl=600,
        )

        return result

    @staticmethod
    def _build_cache_key(
        query: str,
        session_id: str,
    ) -> str:
        """
        Cache keys must be session-aware.

        This avoids returning an analysis generated against one
        database/schema for an identical SQL query executed in
        another session.
        """

        return (
            f"query-analysis:"
            f"{session_id}:"
            f"{hash(query)}"
        )