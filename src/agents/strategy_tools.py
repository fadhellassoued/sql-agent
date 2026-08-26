from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from optimization.core.query_structure_analyzer import (
    QueryStructureAnalyzer,
)

from optimization.bigquery.evidence import (
    BigQueryEvidenceAnalyzer,
)

from optimization.bigquery.evidence_join import (
    BigQueryJoinEvidenceAnalyzer,
)

from optimization.bigquery.join_optimizer import (
    BigQueryJoinOptimizer,
)

from optimization.bigquery.user_intent import (
    BigQueryUserIntentAnalyzer,
)

from optimization.bigquery.catalog_enrichment import (
    BigQueryCatalogEnricher,
)

from infrastructure.catalog.knowledge_catalog_client import (
    KnowledgeCatalogClient,
)


from infrastructure.catalog.catalog_cache import (
    KnowledgeCatalogAlternativeCache,
)
# Process-scoped Redis cache. Redis I/O is executed through asyncio.to_thread
# because the existing Redis client is synchronous.
_catalog_alternative_cache = (
    KnowledgeCatalogAlternativeCache()
)


class SQLInput(BaseModel):
    sql: str = Field(
        description="The original SQL query to inspect."
    )


class AgentEvidenceContext:
    """
    Request-scoped evidence cache.

    Full deterministic evidence stays server-side in this context.
    Tool responses sent back to the LLM are intentionally compact.
    """

    def __init__(
        self,
        *,
        database_type: str,
        conn_obj: dict,
        db_repo: Any,
    ):
        self.database_type = (
            database_type or ""
        ).lower()

        self.conn_obj = conn_obj
        self.db_repo = db_repo

        self.structure = None
        self.bigquery_evidence = None
        self.join_evidence = None
        self.join_optimization = None
        self.user_intent = None
        self.cost_estimate = None

        # Supplementary semantic/governance evidence from Knowledge Catalog.
        # BigQuery metadata remains authoritative for physical facts.
        self.catalog_evidence = None

        # Candidate curated/pre-aggregated assets discovered from Knowledge Catalog.
        # Recommendation evidence only; never authorizes a rewrite.
        self.catalog_alternatives = None

        self.tool_trace: list[str] = []


class SQLAgentToolbox:
    """
    Evidence-only tools.

    Safety:
    - no arbitrary result-producing query execution;
    - no free-form executable SQL generation;
    - no candidate approval;
    - no quality-gate bypass.

    Latency:
    - full evidence is cached in AgentEvidenceContext;
    - the LLM receives compact summaries only;
    - BigQuery JOIN analysis has a bundled tool so one tool call can
      gather metadata + join evidence + optimization opportunities.
    """

    def __init__(
        self,
        context: AgentEvidenceContext,
    ):
        self.context = context

        self.structure_analyzer = (
            QueryStructureAnalyzer()
        )

        self.join_evidence_analyzer = (
            BigQueryJoinEvidenceAnalyzer()
        )

        self.join_optimizer = (
            BigQueryJoinOptimizer()
        )

        self.user_intent_analyzer = (
            BigQueryUserIntentAnalyzer()
        )

    def _trace(
        self,
        name: str,
    ) -> None:
        self.context.tool_trace.append(
            name
        )

        print(
            f"[Agent Tool] {name}"
        )

    def _dialect(
        self,
    ) -> str:
        return (
            "postgres"
            if self.context.database_type
            in {
                "postgres",
                "postgresql",
            }
            else "bigquery"
        )

    def _ensure_structure(
        self,
        sql: str,
    ):
        if self.context.structure is None:
            self.context.structure = (
                self.structure_analyzer.analyze(
                    sql,
                    dialect=self._dialect(),
                )
            )

        return self.context.structure

    async def _ensure_bigquery_metadata(
        self,
        sql: str,
    ) -> dict:
        if (
            self.context.bigquery_evidence
            is not None
        ):
            return self.context.bigquery_evidence

        structure = (
            self._ensure_structure(
                sql
            )
        )

        analyzer = (
            BigQueryEvidenceAnalyzer(
                client=
                    self.context.conn_obj[
                        "client"
                    ],

                default_dataset=
                    self.context.conn_obj.get(
                        "dataset"
                    ),
            )
        )

        self.context.bigquery_evidence = (
            await analyzer.analyze(
                structure
            )
        )

        return (
            self.context.bigquery_evidence
        )

    async def _ensure_knowledge_catalog(
        self,
        sql: str,
    ) -> dict:
        """
        Lazily enrich already-resolved BigQuery assets with Knowledge
        Catalog context.

        Knowledge Catalog is supplementary semantic/governance evidence.
        It never replaces BigQuery metadata as physical truth and a catalog
        failure must never fail SQL optimization.
        """

        if (
            self.context.catalog_evidence
            is not None
        ):
            return (
                self.context.catalog_evidence
            )

        if (
            self.context.database_type
            != "bigquery"
        ):
            self.context.catalog_evidence = {
                "status": "NOT_APPLICABLE",
                "tables": [],
                "source": "knowledge_catalog",
            }

            return (
                self.context.catalog_evidence
            )

        started = time.perf_counter()

        try:
            # Resolve physical assets first. This keeps catalog lookup narrow:
            # only tables actually referenced by the SQL are enriched.
            metadata = (
                await self._ensure_bigquery_metadata(
                    sql
                )
            )

            assets: list[dict[str, str]] = []

            for table in (
                (metadata or {}).get(
                    "tables",
                    [],
                )
                or []
            ):
                full_name = (
                    table.get("full_name")
                    or ""
                ).strip()

                parts = [
                    part.strip("`")
                    for part in full_name.split(".")
                    if part
                ]

                project_id = None
                dataset_id = None
                table_id = None

                if len(parts) >= 3:
                    project_id = parts[-3]
                    dataset_id = parts[-2]
                    table_id = parts[-1]

                elif len(parts) == 2:
                    project_id = (
                        self.context.conn_obj.get(
                            "project"
                        )
                        or self.context.conn_obj.get(
                            "project_id"
                        )
                    )
                    dataset_id = parts[-2]
                    table_id = parts[-1]

                elif len(parts) == 1:
                    project_id = (
                        self.context.conn_obj.get(
                            "project"
                        )
                        or self.context.conn_obj.get(
                            "project_id"
                        )
                    )
                    dataset_id = (
                        self.context.conn_obj.get(
                            "dataset"
                        )
                        or self.context.conn_obj.get(
                            "dataset_id"
                        )
                    )
                    table_id = parts[-1]

                if (
                    project_id
                    and dataset_id
                    and table_id
                ):
                    assets.append(
                        {
                            "project_id":
                                str(project_id),

                            "dataset_id":
                                str(dataset_id),

                            "table_id":
                                str(table_id),
                        }
                    )

            consumer_project_id = (
                self.context.conn_obj.get(
                    "project"
                )
                or self.context.conn_obj.get(
                    "project_id"
                )
            )

            if not consumer_project_id:
                self.context.catalog_evidence = {
                    "status": "UNAVAILABLE",
                    "tables": [],
                    "reason":
                        "No Google Cloud consumer project "
                        "is available.",
                    "source": "knowledge_catalog",
                }

                return (
                    self.context.catalog_evidence
                )

            if not assets:
                self.context.catalog_evidence = {
                    "status": "NO_REFERENCED_TABLES",
                    "tables": [],
                    "referenced_table_count": 0,
                    "catalog_match_count": 0,
                    "source": "knowledge_catalog",
                }

                return (
                    self.context.catalog_evidence
                )

            catalog_client = (
                KnowledgeCatalogClient(
                    consumer_project_id=
                        str(consumer_project_id)
                )
            )

            enricher = (
                BigQueryCatalogEnricher(
                    catalog_client
                )
            )

            self.context.catalog_evidence = (
                await enricher.enrich_assets(
                    assets=assets
                )
            )

        except Exception as exc:
            # Fail open for enrichment only. Catalog evidence is optional and
            # must not block the deterministic SQL optimization pipeline.
            self.context.catalog_evidence = {
                "status": "UNAVAILABLE",
                "tables": [],
                "reason":
                    "Knowledge Catalog enrichment "
                    "is temporarily unavailable.",
                "source": "knowledge_catalog",
            }

            # Do not print the raw exception because provider errors can expose
            # resource/account details in logs.
            print(
                "[KnowledgeCatalog] "
                f"unavailable "
                f"error_type={type(exc).__name__}"
            )

        finally:
            duration_ms = (
                time.perf_counter()
                - started
            ) * 1000

            print(
                "[TIMING] Knowledge Catalog enrichment: "
                f"{duration_ms:.2f} ms"
            )

        return (
            self.context.catalog_evidence
        )

    async def _ensure_knowledge_catalog_alternatives(
        self,
        sql: str,
    ) -> dict:
        """
        Lazily discover curated/pre-aggregated BigQuery assets in the same
        project + dataset as the query's single referenced source table.

        Two cache layers are used:
        1. request-scoped AgentEvidenceContext;
        2. Redis cross-request cache with a short configurable TTL.

        Safety:
            - recommendation evidence only;
            - no semantic-equivalence claim;
            - no SQL generation;
            - no candidate approval;
            - Redis/cache failures never block optimization.
        """

        # ---------------------------------------------------------
        # REQUEST-SCOPED CACHE
        # ---------------------------------------------------------
        if (
            self.context.catalog_alternatives
            is not None
        ):
            return (
                self.context.catalog_alternatives
            )

        if (
            self.context.database_type
            != "bigquery"
        ):
            self.context.catalog_alternatives = {
                "status": "NOT_APPLICABLE",
                "source_table": None,
                "candidates": [],
                "candidate_count": 0,
                "source": "knowledge_catalog",
                "cache_status": "NOT_APPLICABLE",
            }

            return (
                self.context.catalog_alternatives
            )

        started = (
            time.perf_counter()
        )

        try:
            # Resolve the physical source using authoritative BigQuery
            # metadata before constructing any Catalog cache key.
            metadata = (
                await self._ensure_bigquery_metadata(
                    sql
                )
            )

            tables = (
                (metadata or {}).get(
                    "tables",
                    [],
                )
                or []
            )

            if len(tables) != 1:
                self.context.catalog_alternatives = {
                    "status": "UNSUPPORTED_SOURCE_SHAPE",
                    "source_table": None,
                    "candidates": [],
                    "candidate_count": 0,
                    "reason":
                        "Catalog alternative discovery currently requires "
                        "exactly one referenced BigQuery table.",
                    "source": "knowledge_catalog",
                    "cache_status": "BYPASSED",
                }

                return (
                    self.context.catalog_alternatives
                )

            table = tables[0]

            full_name = (
                table.get(
                    "full_name"
                )
                or ""
            ).strip()

            parts = [
                part.strip("`")
                for part in full_name.split(".")
                if part
            ]

            project_id = None
            dataset_id = None
            table_id = None

            if len(parts) >= 3:
                project_id = parts[-3]
                dataset_id = parts[-2]
                table_id = parts[-1]

            elif len(parts) == 2:
                project_id = (
                    self.context.conn_obj.get(
                        "project"
                    )
                    or self.context.conn_obj.get(
                        "project_id"
                    )
                )
                dataset_id = parts[-2]
                table_id = parts[-1]

            elif len(parts) == 1:
                project_id = (
                    self.context.conn_obj.get(
                        "project"
                    )
                    or self.context.conn_obj.get(
                        "project_id"
                    )
                )
                dataset_id = (
                    self.context.conn_obj.get(
                        "dataset"
                    )
                    or self.context.conn_obj.get(
                        "dataset_id"
                    )
                )
                table_id = parts[-1]

            if not (
                project_id
                and dataset_id
                and table_id
            ):
                self.context.catalog_alternatives = {
                    "status": "UNRESOLVED_SOURCE_TABLE",
                    "source_table": full_name or None,
                    "candidates": [],
                    "candidate_count": 0,
                    "reason":
                        "The referenced BigQuery source table could not "
                        "be resolved to project, dataset, and table identifiers.",
                    "source": "knowledge_catalog",
                    "cache_status": "BYPASSED",
                }

                return (
                    self.context.catalog_alternatives
                )

            project_id = str(
                project_id
            )
            dataset_id = str(
                dataset_id
            )
            table_id = str(
                table_id
            )

            # -----------------------------------------------------
            # REDIS CROSS-REQUEST CACHE
            # -----------------------------------------------------
            try:
                cached = (
                    await asyncio.to_thread(
                        _catalog_alternative_cache.load,
                        project_id=project_id,
                        dataset_id=dataset_id,
                        source_table_id=table_id,
                    )
                )

                if cached is not None:
                    self.context.catalog_alternatives = (
                        cached
                    )

                    print(
                        "[KnowledgeCatalog Cache] HIT "
                        f"source={project_id}.{dataset_id}.{table_id}"
                    )

                    return (
                        self.context.catalog_alternatives
                    )

                print(
                    "[KnowledgeCatalog Cache] MISS "
                    f"source={project_id}.{dataset_id}.{table_id}"
                )

            except Exception as cache_exc:
                # Redis is an optimization only. A cache outage must not
                # prevent live Knowledge Catalog discovery.
                print(
                    "[KnowledgeCatalog Cache] unavailable "
                    f"operation=load "
                    f"error_type={type(cache_exc).__name__}"
                )

            consumer_project_id = (
                self.context.conn_obj.get(
                    "project"
                )
                or self.context.conn_obj.get(
                    "project_id"
                )
            )

            if not consumer_project_id:
                self.context.catalog_alternatives = {
                    "status": "UNAVAILABLE",
                    "source_table": full_name or None,
                    "candidates": [],
                    "candidate_count": 0,
                    "reason":
                        "No Google Cloud consumer project is available.",
                    "source": "knowledge_catalog",
                    "cache_status": "BYPASSED",
                }

                return (
                    self.context.catalog_alternatives
                )

            # -----------------------------------------------------
            # LIVE KNOWLEDGE CATALOG DISCOVERY
            # -----------------------------------------------------
            catalog_client = (
                KnowledgeCatalogClient(
                    consumer_project_id=
                        str(consumer_project_id)
                )
            )

            result = (
                await catalog_client
                .find_preaggregated_candidates(
                    project_id=project_id,
                    dataset_id=dataset_id,
                    source_table_id=table_id,
                )
            )

            result = (
                dict(result)
                if isinstance(
                    result,
                    dict,
                )
                else {
                    "status": "UNAVAILABLE",
                    "source_table":
                        f"bigquery:{project_id}.{dataset_id}.{table_id}",
                    "candidates": [],
                    "candidate_count": 0,
                    "source": "knowledge_catalog",
                }
            )

            result[
                "cache_status"
            ] = "MISS"

            self.context.catalog_alternatives = (
                result
            )

            # Cache stable positive and negative discovery results.
            # Do not cache provider failures/UNAVAILABLE responses, because
            # doing so would turn a temporary outage into a sticky failure.
            if (
                result.get(
                    "status"
                )
                in {
                    "FOUND",
                    "NO_MATCHES",
                }
            ):
                try:
                    await asyncio.to_thread(
                        _catalog_alternative_cache.save,
                        project_id=project_id,
                        dataset_id=dataset_id,
                        source_table_id=table_id,
                        payload=result,
                    )

                    print(
                        "[KnowledgeCatalog Cache] WRITE "
                        f"source={project_id}.{dataset_id}.{table_id} "
                        f"ttl={_catalog_alternative_cache.ttl_seconds}s"
                    )

                except Exception as cache_exc:
                    print(
                        "[KnowledgeCatalog Cache] unavailable "
                        f"operation=save "
                        f"error_type={type(cache_exc).__name__}"
                    )

        except Exception as exc:
            self.context.catalog_alternatives = {
                "status": "UNAVAILABLE",
                "source_table": None,
                "candidates": [],
                "candidate_count": 0,
                "reason":
                    "Knowledge Catalog alternative discovery "
                    "is temporarily unavailable.",
                "source": "knowledge_catalog",
                "cache_status": "ERROR",
            }

            print(
                "[KnowledgeCatalog] "
                "alternative_discovery_unavailable "
                f"error_type={type(exc).__name__}"
            )

        finally:
            duration_ms = (
                time.perf_counter()
                - started
            ) * 1000

            print(
                "[TIMING] Knowledge Catalog alternative discovery: "
                f"{duration_ms:.2f} ms"
            )

        return (
            self.context.catalog_alternatives
        )

    def _compact_catalog_alternatives(
        self,
        evidence: dict | None,
    ) -> dict:
        """Keep LLM-facing alternative-asset evidence compact."""

        compact_candidates = []

        for candidate in ((evidence or {}).get("candidates", []) or []):
            description = (candidate.get("description") or "").strip()
            if len(description) > 350:
                description = description[:347] + "..."

            compact_candidates.append(
                {
                    "table": candidate.get("fully_qualified_name"),
                    "display_name": candidate.get("display_name"),
                    "description": description or None,
                    "matched_keywords": candidate.get("matched_keywords") or [],
                    "references_source": bool(
                        candidate.get("references_source", False)
                    ),
                    "relevance_score": candidate.get("relevance_score"),
                }
            )

        return {
            "status": (evidence or {}).get("status", "UNKNOWN"),
            "source_table": (evidence or {}).get("source_table"),
            "candidate_count": len(compact_candidates),
            "candidates": compact_candidates,
        }

    def _compact_structure(
        self,
        structure,
    ) -> dict:
        tables = []

        for table in (
            getattr(
                structure,
                "tables",
                [],
            )
            or []
        ):
            tables.append(
                (
                    getattr(
                        table,
                        "full_name",
                        None,
                    )
                    or
                    getattr(
                        table,
                        "name",
                        None,
                    )
                )
            )

        return {
            "tables":
                tables,

            "table_count":
                len(
                    tables
                ),

            "join_count":
                getattr(
                    structure,
                    "join_count",
                    0,
                ),

            "cte_count":
                getattr(
                    structure,
                    "cte_count",
                    0,
                ),

            "subquery_count":
                getattr(
                    structure,
                    "subquery_count",
                    0,
                ),

            "has_select_star":
                bool(
                    getattr(
                        structure,
                        "has_select_star",
                        False,
                    )
                ),

            "has_distinct":
                bool(
                    getattr(
                        structure,
                        "has_distinct",
                        False,
                    )
                ),

            "has_limit":
                bool(
                    getattr(
                        structure,
                        "has_limit",
                        False,
                    )
                ),

            "limit_value":
                getattr(
                    structure,
                    "limit_value",
                    None,
                ),
        }

    def _compact_metadata(
        self,
        evidence: dict | None,
    ) -> dict:
        compact_tables = []

        for table in (
            (evidence or {}).get(
                "tables",
                [],
            )
            or []
        ):
            compact_tables.append(
                {
                    "table":
                        table.get(
                            "full_name"
                        ),

                    "rows":
                        table.get(
                            "row_count"
                        ),

                    "size_bytes":
                        table.get(
                            "size_bytes"
                        ),

                    "partitioned":
                        table.get(
                            "partitioned"
                        ),

                    "partition_field":
                        table.get(
                            "partition_field"
                        ),

                    "clustering":
                        table.get(
                            "clustering_fields"
                        )
                        or [],
                }
            )

        return {
            "table_count":
                len(
                    compact_tables
                ),

            "tables":
                compact_tables,

            "metadata_error_count":
                len(
                    (evidence or {}).get(
                        "metadata_errors",
                        [],
                    )
                    or []
                ),
        }

    def _compact_catalog_evidence(
        self,
        evidence: dict | None,
    ) -> dict:
        """
        Keep the LLM-facing catalog payload small.

        Full catalog evidence remains server-side in AgentEvidenceContext.
        """

        tables = []

        for table in (
            (evidence or {}).get(
                "tables",
                [],
            )
            or []
        ):
            entry = (
                table.get(
                    "entry"
                )
                or {}
            )

            description = (
                entry.get(
                    "description"
                )
                or ""
            ).strip()

            if len(description) > 300:
                description = (
                    description[:297]
                    + "..."
                )

            tables.append(
                {
                    "table":
                        table.get(
                            "table"
                        ),

                    "status":
                        table.get(
                            "status"
                        ),

                    "display_name":
                        entry.get(
                            "display_name"
                        ),

                    "description":
                        description
                        or None,

                    "system":
                        entry.get(
                            "system"
                        ),

                    "location":
                        entry.get(
                            "location"
                        ),

                    "entry_type":
                        entry.get(
                            "entry_type"
                        ),
                }
            )

        return {
            "status":
                (evidence or {}).get(
                    "status",
                    "UNKNOWN",
                ),

            "referenced_table_count":
                (evidence or {}).get(
                    "referenced_table_count",
                    len(tables),
                ),

            "catalog_match_count":
                (evidence or {}).get(
                    "catalog_match_count",
                    0,
                ),

            "tables":
                tables,
        }

    def _compact_join_evidence(
        self,
        evidence: dict | None,
    ) -> dict:
        joins = []

        for edge in (
            (evidence or {}).get(
                "edges",
                [],
            )
            or []
        ):
            transformed_pairs = (
                edge.get(
                    "transformed_pairs"
                )
                or
                edge.get(
                    "transformed_equality_pairs"
                )
                or []
            )

            right_source = (
                edge.get(
                    "right_source"
                )
                or {}
            )

            joins.append(
                {
                    "join_index":
                        edge.get(
                            "join_index"
                        ),

                    "type":
                        edge.get(
                            "join_type"
                        ),

                    "right_alias":
                        right_source.get(
                            "alias"
                        ),

                    "condition":
                        edge.get(
                            "condition"
                        ),

                    "has_simple_equality":
                        bool(
                            edge.get(
                                "equality_pairs"
                            )
                        ),

                    "has_transformed_key":
                        bool(
                            transformed_pairs
                        ),

                    "size_ratio":
                        edge.get(
                            "static_size_ratio"
                        ),

                    "risk":
                        edge.get(
                            "risk_level"
                        ),
                }
            )

        finding_codes = [
            finding.get(
                "code"
            )
            for finding in (
                (evidence or {}).get(
                    "findings",
                    [],
                )
                or []
            )
            if finding.get(
                "code"
            )
        ]

        return {
            "join_count":
                (evidence or {}).get(
                    "join_count",
                    len(
                        joins
                    ),
                ),

            "joins":
                joins,

            "finding_codes":
                finding_codes,
        }

    def _compact_opportunities(
        self,
        result: dict | None,
    ) -> dict:
        opportunities = []

        for opportunity in (
            (result or {}).get(
                "opportunities",
                [],
            )
            or []
        ):
            opportunities.append(
                {
                    "code":
                        opportunity.get(
                            "code"
                        ),

                    "severity":
                        opportunity.get(
                            "severity"
                        ),

                    "title":
                        opportunity.get(
                            "title"
                        ),

                    "rewrite_status":
                        opportunity.get(
                            "rewrite_status"
                        ),

                    "candidate_kind":
                        opportunity.get(
                            "candidate_kind"
                        ),
                }
            )

        return {
            "opportunity_count":
                len(
                    opportunities
                ),

            "candidate_generation_allowed":
                bool(
                    (result or {}).get(
                        "candidate_generation_allowed",
                        False,
                    )
                ),

            "safe_rewrite_available":
                bool(
                    (result or {}).get(
                        "safe_rewrite_available",
                        False,
                    )
                ),

            "opportunities":
                opportunities,
        }

    async def analyze_query_structure(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "analyze_query_structure"
        )

        structure = (
            self._ensure_structure(
                sql
            )
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "query_structure",

                "structure":
                    self._compact_structure(
                        structure
                    ),
            },
            default=str,
        )

    async def get_bigquery_metadata(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "get_bigquery_metadata"
        )

        if (
            self.context.database_type
            != "bigquery"
        ):
            return json.dumps(
                {
                    "status":
                        "not_applicable",

                    "reason":
                        "Database is not BigQuery.",
                }
            )

        evidence = (
            await self._ensure_bigquery_metadata(
                sql
            )
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "bigquery_metadata",

                "metadata":
                    self._compact_metadata(
                        evidence
                    ),
            },
            default=str,
        )

    async def analyze_bigquery_joins(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "analyze_bigquery_joins"
        )

        if (
            self.context.database_type
            != "bigquery"
        ):
            return json.dumps(
                {
                    "status":
                        "not_applicable",

                    "reason":
                        "Database is not BigQuery.",
                }
            )

        structure = (
            self._ensure_structure(
                sql
            )
        )

        table_evidence = (
            await self._ensure_bigquery_metadata(
                sql
            )
        )

        evidence = (
            self.join_evidence_analyzer.analyze(
                sql=
                    sql,

                structure=
                    structure,

                table_evidence=
                    table_evidence,
            )
        )

        self.context.join_evidence = (
            evidence
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "bigquery_join_evidence",

                "join_evidence":
                    self._compact_join_evidence(
                        evidence
                    ),
            },
            default=str,
        )

    async def get_join_optimization_opportunities(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "get_join_optimization_opportunities"
        )

        structure = (
            self._ensure_structure(
                sql
            )
        )

        if self.context.join_evidence is None:
            table_evidence = (
                await self._ensure_bigquery_metadata(
                    sql
                )
            )

            self.context.join_evidence = (
                self.join_evidence_analyzer.analyze(
                    sql=
                        sql,

                    structure=
                        structure,

                    table_evidence=
                        table_evidence,
                )
            )

        result = (
            self.join_optimizer.analyze(
                join_evidence=
                    self.context.join_evidence,

                structure=
                    structure,
            )
        )

        self.context.join_optimization = (
            result
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "join_optimization_opportunities",

                "optimization":
                    self._compact_opportunities(
                        result
                    ),
            },
            default=str,
        )

    async def analyze_bigquery_join_strategy_evidence(
        self,
        sql: str,
    ) -> str:
        """
        Low-latency BigQuery JOIN evidence bundle.

        One LangChain tool call computes:
        structure -> metadata -> join evidence -> join opportunities.

        Full evidence is cached in AgentEvidenceContext.
        The LLM receives only compact summaries.
        """

        self._trace(
            "analyze_bigquery_join_strategy_evidence"
        )

        if (
            self.context.database_type
            != "bigquery"
        ):
            return json.dumps(
                {
                    "status":
                        "not_applicable",

                    "reason":
                        "Database is not BigQuery.",
                }
            )

        structure = (
            self._ensure_structure(
                sql
            )
        )

        table_evidence = (
            await self._ensure_bigquery_metadata(
                sql
            )
        )

        join_evidence = (
            self.join_evidence_analyzer.analyze(
                sql=
                    sql,

                structure=
                    structure,

                table_evidence=
                    table_evidence,
            )
        )

        self.context.join_evidence = (
            join_evidence
        )

        join_optimization = (
            self.join_optimizer.analyze(
                join_evidence=
                    join_evidence,

                structure=
                    structure,
            )
        )

        self.context.join_optimization = (
            join_optimization
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_refs": [
                    "query_structure",
                    "bigquery_metadata",
                    "bigquery_join_evidence",
                    "join_optimization_opportunities",
                ],

                "structure":
                    self._compact_structure(
                        structure
                    ),

                "metadata":
                    self._compact_metadata(
                        table_evidence
                    ),

                "join_evidence":
                    self._compact_join_evidence(
                        join_evidence
                    ),

                "optimization":
                    self._compact_opportunities(
                        join_optimization
                    ),
            },
            default=str,
        )

    async def determine_user_intent_requirement(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "determine_user_intent_requirement"
        )

        structure = (
            self._ensure_structure(
                sql
            )
        )

        if (
            self.context.database_type
            == "bigquery"
        ):
            evidence = (
                await self._ensure_bigquery_metadata(
                    sql
                )
            )

            required = (
                self.user_intent_analyzer.analyze(
                    structure=
                        structure,

                    evidence=
                        evidence,
                )
            )
        else:
            required = None

        self.context.user_intent = (
            required
        )

        payload = (
            required.to_dict()
            if required is not None
            else None
        )

        compact_payload = None

        if payload is not None:
            columns = [
                column.get(
                    "name"
                )
                for column in (
                    payload.get(
                        "columns"
                    )
                    or []
                )
                if column.get(
                    "name"
                )
            ]

            compact_payload = {
                "type":
                    payload.get(
                        "type"
                    ),

                "reason":
                    payload.get(
                        "reason"
                    ),

                "table":
                    payload.get(
                        "table"
                    ),

                "column_count":
                    len(
                        columns
                    ),

                # Keep names because the user may need to select from them.
                "columns":
                    columns,
            }

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "user_intent_requirement",

                "required_user_input":
                    compact_payload,
            },
            default=str,
        )

    async def get_knowledge_catalog_context(
        self,
        sql: str,
    ) -> str:
        """
        Retrieve semantic/governance context for BigQuery tables already
        referenced by the query.

        This is supplementary evidence only. It cannot authorize SQL rewrites,
        candidate generation, or candidate approval.
        """

        self._trace(
            "get_knowledge_catalog_context"
        )

        if (
            self.context.database_type
            != "bigquery"
        ):
            return json.dumps(
                {
                    "status":
                        "not_applicable",

                    "reason":
                        "Knowledge Catalog enrichment "
                        "is currently available only "
                        "for BigQuery.",
                }
            )

        evidence = (
            await self._ensure_knowledge_catalog(
                sql
            )
        )

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "knowledge_catalog_context",

                "catalog":
                    self._compact_catalog_evidence(
                        evidence
                    ),
            },
            default=str,
        )

    async def find_knowledge_catalog_alternatives(
        self,
        sql: str,
    ) -> str:
        """
        Discover possible curated/pre-aggregated BigQuery assets related to
        the query's single referenced source table. Recommendation evidence only.
        """

        self._trace(
            "find_knowledge_catalog_alternatives"
        )

        if self.context.database_type != "bigquery":
            return json.dumps(
                {
                    "status": "not_applicable",
                    "reason": (
                        "Knowledge Catalog alternative discovery is currently "
                        "available only for BigQuery."
                    ),
                }
            )

        evidence = await self._ensure_knowledge_catalog_alternatives(sql)

        return json.dumps(
            {
                "status": "ok",
                "evidence_ref": "knowledge_catalog_alternatives",
                "catalog_alternatives": self._compact_catalog_alternatives(evidence),
            },
            default=str,
        )

    async def estimate_query_cost(
        self,
        sql: str,
    ) -> str:
        self._trace(
            "estimate_query_cost"
        )

        if (
            self.context.database_type
            != "bigquery"
        ):
            return json.dumps(
                {
                    "status":
                        "not_applicable",

                    "reason":
                        "BigQuery dry-run cost estimation "
                        "is unavailable for this database.",
                }
            )

        estimate = (
            await self.context.db_repo
            .estimate_query_cost(
                sql
            )
        )

        self.context.cost_estimate = (
            estimate
        )

        compact_estimate = {
            "bytes_processed":
                estimate.get(
                    "bytes_processed"
                )
                if isinstance(
                    estimate,
                    dict,
                )
                else None,

            "estimated_cost_usd":
                estimate.get(
                    "estimated_cost_usd"
                )
                if isinstance(
                    estimate,
                    dict,
                )
                else None,

            "scan_type":
                estimate.get(
                    "scan_type"
                )
                if isinstance(
                    estimate,
                    dict,
                )
                else None,
        }

        return json.dumps(
            {
                "status":
                    "ok",

                "evidence_ref":
                    "bigquery_dry_run_cost",

                "estimate":
                    compact_estimate,
            },
            default=str,
        )

    def tools(
        self,
    ):
        return [
            StructuredTool.from_function(
                coroutine=
                    self.analyze_bigquery_join_strategy_evidence,

                name=
                    "analyze_bigquery_join_strategy_evidence",

                description=(
                    "Preferred low-latency tool for BigQuery JOIN queries. "
                    "In one call it gathers query structure, referenced-table "
                    "metadata, join evidence, and deterministic join "
                    "optimization opportunities. Use this before requesting "
                    "JOIN_REWRITE or candidate generation."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.analyze_query_structure,

                name=
                    "analyze_query_structure",

                description=(
                    "Compact SQL structure analysis. Prefer this for "
                    "non-JOIN queries or when only structure is needed."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.get_bigquery_metadata,

                name=
                    "get_bigquery_metadata",

                description=(
                    "Compact live BigQuery physical metadata for referenced "
                    "tables. Full metadata remains server-side."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.analyze_bigquery_joins,

                name=
                    "analyze_bigquery_joins",

                description=(
                    "Granular BigQuery join evidence tool. Normally prefer "
                    "analyze_bigquery_join_strategy_evidence for lower latency."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.get_join_optimization_opportunities,

                name=
                    "get_join_optimization_opportunities",

                description=(
                    "Deterministic join optimization opportunities. A "
                    "JOIN_REWRITE decision is not permitted without this "
                    "evidence or the bundled join-strategy tool."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.determine_user_intent_requirement,

                name=
                    "determine_user_intent_requirement",

                description=(
                    "Determine whether a safe rewrite needs explicit user "
                    "intent, such as required output columns for SELECT star."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.get_knowledge_catalog_context,

                name=
                    "get_knowledge_catalog_context",

                description=(
                    "Retrieve Knowledge Catalog semantic, descriptive, "
                    "and governance context for BigQuery tables already "
                    "referenced by the SQL. Use this only when business "
                    "meaning, asset descriptions, governance context, or "
                    "semantic table understanding could materially improve "
                    "the strategy. Do not use it for basic physical metadata, "
                    "cost estimation, or every query by default."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.find_knowledge_catalog_alternatives,

                name=
                    "find_knowledge_catalog_alternatives",

                description=(
                    "Discover possible curated or pre-aggregated BigQuery "
                    "tables in the same dataset as a single referenced source "
                    "table. Use this when an aggregation or repeated analytical "
                    "workload could benefit from a documented summary/reporting "
                    "asset. Results are recommendation evidence only and must "
                    "never be treated as proof of semantic equivalence or as "
                    "authorization to rewrite the SQL automatically."
                ),

                args_schema=
                    SQLInput,
            ),

            StructuredTool.from_function(
                coroutine=
                    self.estimate_query_cost,

                name=
                    "estimate_query_cost",

                description=(
                    "BigQuery dry-run cost estimate only. Use only when cost "
                    "evidence is necessary for the strategy; do not call it "
                    "just because the tool exists."
                ),

                args_schema=
                    SQLInput,
            ),
        ]