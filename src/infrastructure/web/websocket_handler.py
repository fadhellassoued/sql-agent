from dataclasses import asdict

from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)

import time


from .rest_controllers import (
    conn_manager,
    llm_service,
    agent_state_store,
    intent_store,
    candidate_store,
)


# ============================================================
# LANGCHAIN / GEMINI STRATEGIST
# ============================================================

from agents.langchain_strategy_agent import (
    LangChainStrategyAgent,
)


# ============================================================
# DATABASE REPOSITORIES
# ============================================================

from infrastructure.repositories.bigquery_repository import (
    BigQueryRepository,
)

from infrastructure.repositories.postgres_repository import (
    PostgresRepository,
)


# ============================================================
# OPTIMIZATION ARCHITECTURE
# ============================================================

from optimization.core.query_structure_analyzer import (
    QueryStructureAnalyzer,
)

from optimization.bigquery.optimizer import (
    BigQueryOptimizer,
)

from optimization.bigquery.evidence import (
    BigQueryEvidenceAnalyzer,
)

from optimization.bigquery.evidence_join import (
    BigQueryJoinEvidenceAnalyzer,
)

from optimization.bigquery.join_optimizer import (
    BigQueryJoinOptimizer,
    JOIN_OPTIMIZER_OWNED_GENERIC_FINDING_CODES,
)

from optimization.bigquery.join_candidate_generator import (
    BigQueryJoinCandidateGenerator,
)

from optimization.bigquery.candidate_validator import (
    BigQueryCandidateValidator,
)

from optimization.bigquery.preaggregation_semantic_validator import (
    BigQueryPreaggregationSemanticValidator,
)

from optimization.bigquery.preaggregation_candidate_generator import (
    BigQueryPreaggregationCandidateGenerator,
)

from optimization.bigquery.user_intent import (
    BigQueryUserIntentAnalyzer,
)

from optimization.postgresql.optimizer import (
    PostgreSQLOptimizer,
)


# ============================================================
# LEGACY / TRANSITIONAL TOOLS
# ============================================================
#
# These remain temporarily while optimization rules are being
# migrated into database-specific optimizer modules.
#
# ============================================================

from tools.detect_issues import (
    detect_issues,
)

from tools.optimize_sql import (
    optimize_sql,
)

from tools.recommend_structures import (
    recommend_structures,
)


# ============================================================
# GLOBAL SERVICES
# ============================================================

query_structure_analyzer = (
    QueryStructureAnalyzer()
)

bigquery_optimizer = (
    BigQueryOptimizer()
)

bigquery_join_evidence_analyzer = (
    BigQueryJoinEvidenceAnalyzer()
)

bigquery_join_optimizer = (
    BigQueryJoinOptimizer()
)

bigquery_join_candidate_generator = (
    BigQueryJoinCandidateGenerator()
)

bigquery_candidate_validator = (
    BigQueryCandidateValidator()
)

bigquery_preaggregation_semantic_validator = (
    BigQueryPreaggregationSemanticValidator()
)

bigquery_preaggregation_candidate_generator = (
    BigQueryPreaggregationCandidateGenerator()
)

bigquery_user_intent_analyzer = (
    BigQueryUserIntentAnalyzer()
)

postgresql_optimizer = (
    PostgreSQLOptimizer()
)


# ============================================================
# HELPERS
# ============================================================

def _build_structure_payload(
    structure,
) -> dict:
    """
    Convert QueryStructure dataclass to a JSON-safe dictionary.
    """

    return asdict(
        structure
    )


def _extract_finding_messages(
    optimizer_result: dict,
) -> list[str]:
    """
    Convert structured optimizer findings into plain messages.

    The structured findings are also preserved in the response.
    """

    messages = []

    for finding in (
        optimizer_result.get(
            "findings",
            [],
        )
    ):

        message = finding.get(
            "message"
        )

        if message:

            messages.append(
                message
            )

    return messages


def _deduplicate_strings(
    values: list[str],
) -> list[str]:
    """
    Remove duplicate textual findings/recommendations while
    preserving their original order.
    """

    result = []

    seen = set()

    for value in values:

        if not value:

            continue

        normalized = (
            value.strip()
        )

        if normalized in seen:

            continue

        seen.add(
            normalized
        )

        result.append(
            normalized
        )

    return result


def _refine_legacy_issues(
    legacy_issues: list[str] | None,
    database_type: str,
    bigquery_evidence: dict | None = None,
    database_findings: list[dict] | None = None,
) -> list[str]:
    """
    Refine generic legacy findings using database-specific evidence.

    The legacy detector is intentionally generic and may produce
    statements that are too broad for BigQuery.

    In particular, an absent WHERE clause does not automatically
    imply fewer BigQuery bytes would be scanned if a filter were
    added. For unpartitioned tables, a WHERE clause may reduce the
    returned rows or downstream work without reducing table-scan
    bytes.

    PostgreSQL behavior is preserved unchanged.
    """

    issues = list(
        legacy_issues
        or []
    )

    normalized_database_type = (
        database_type
        or ""
    ).lower()

    # =========================================================
    # NON-BIGQUERY
    # =========================================================

    if (
        normalized_database_type
        !=
        "bigquery"
    ):

        return issues

    # =========================================================
    # BIGQUERY
    # =========================================================

    refined = []

    no_where_detected = False

    database_finding_codes = {
        finding.get(
            "code"
        )

        for finding in (
            database_findings
            or []
        )
    }

    has_database_cross_join = (
        "BQ_CROSS_JOIN"
        in database_finding_codes
    )

    for issue in issues:

        normalized_issue = (
            issue.lower()
        )

        # -----------------------------------------------------
        # Prefer deterministic BigQuery JOIN evidence over the
        # generic legacy CROSS JOIN warning. This prevents the
        # same condition from appearing twice in Issues Detected.
        # -----------------------------------------------------

        if (
            has_database_cross_join
            and
            "cross join"
            in normalized_issue
        ):

            continue

        # -----------------------------------------------------
        # Remove the generic BigQuery-inaccurate WHERE warning.
        # -----------------------------------------------------

        if (
            "no where clause detected"
            in normalized_issue
        ):

            no_where_detected = True

            continue

        refined.append(
            issue
        )

    # No generic WHERE warning was produced.
    if not no_where_detected:

        return refined

    evidence = (
        bigquery_evidence
        or {}
    )

    tables = (
        evidence.get(
            "tables",
            [],
        )
        or []
    )

    # If metadata is unavailable, do not replace a generic warning
    # with another unsupported statement.
    if not tables:

        return refined

    partitioned_tables = [
        table

        for table
        in tables

        if table.get(
            "partitioned"
        )
    ]

    unpartitioned_tables = [
        table

        for table
        in tables

        if not table.get(
            "partitioned"
        )
    ]

    # ---------------------------------------------------------
    # ALL REFERENCED TABLES ARE UNPARTITIONED
    # ---------------------------------------------------------

    if (
        unpartitioned_tables
        and
        not partitioned_tables
    ):

        refined.append(
            (
                "The query has no row filter. "
                "The referenced BigQuery table(s) are not "
                "partitioned, so adding a WHERE clause does "
                "not automatically reduce bytes scanned. "
                "A filter may still reduce returned rows or "
                "downstream processing."
            )
        )

    # ---------------------------------------------------------
    # PARTITIONED / MIXED TABLE SET
    # ---------------------------------------------------------
    #
    # Do not add the generic warning here. BigQueryOptimizer
    # already owns metadata-aware partition-filter findings and
    # can identify the actual partition field.
    # ---------------------------------------------------------

    return refined


def _format_bytes(
    value: int | None,
) -> str:
    """
    Format byte values consistently using binary units.

    Examples:

        949338916
        -> 905.36 MiB

        121525290780
        -> 113.18 GiB

    This formatted value is passed directly to Gemini so that
    the LLM does not perform its own unit conversion.
    """

    if value is None:

        return "Unavailable"

    value = float(
        value
    )

    kib = 1024

    mib = (
        1024 ** 2
    )

    gib = (
        1024 ** 3
    )

    tib = (
        1024 ** 4
    )

    if value >= tib:

        return (
            f"{value / tib:.3f} TiB"
        )

    if value >= gib:

        return (
            f"{value / gib:.2f} GiB"
        )

    if value >= mib:

        return (
            f"{value / mib:.2f} MiB"
        )

    if value >= kib:

        return (
            f"{value / kib:.2f} KiB"
        )

    return (
        f"{int(value)} bytes"
    )


def _short_session_id(
    value: str | None,
) -> str:
    if not value:
        return "none"

    return (
        f"{value[:8]}…"
    )



def _get_agent_catalog_alternatives(
    strategy_agent,
) -> dict | None:
    """
    Return request-scoped Knowledge Catalog alternative evidence gathered
    by the strategist, if available.

    This evidence is recommendation-only and must never be treated as an
    approved executable rewrite.
    """

    if (
        strategy_agent is None
        or getattr(
            strategy_agent,
            "context",
            None,
        )
        is None
    ):
        return None

    evidence = getattr(
        strategy_agent.context,
        "catalog_alternatives",
        None,
    )

    return (
        evidence
        if isinstance(
            evidence,
            dict,
        )
        else None
    )


def _build_catalog_recommendations(
    *,
    agent_plan,
    catalog_alternatives: dict | None,
) -> list[str]:
    """
    Convert verified PREAGGREGATION_REVIEW evidence into user-facing
    recommendation text.

    Safety:
    - only uses assets actually returned by Knowledge Catalog;
    - never generates replacement SQL;
    - never claims semantic equivalence;
    - never marks a recommendation as an applied optimization.
    """

    if (
        agent_plan is None
        or getattr(
            agent_plan,
            "strategy",
            None,
        )
        != "PREAGGREGATION_REVIEW"
    ):
        return []

    candidates = (
        (catalog_alternatives or {}).get(
            "candidates",
            [],
        )
        or []
    )

    recommendations = []

    for candidate in candidates[:3]:
        candidate_name = (
            candidate.get(
                "fully_qualified_name"
            )
            or candidate.get(
                "display_name"
            )
        )

        if not candidate_name:
            continue

        if candidate_name.startswith(
            "bigquery:"
        ):
            candidate_name = (
                candidate_name[
                    len("bigquery:"):
                ]
            )

        references_source = bool(
            candidate.get(
                "references_source",
                False,
            )
        )

        relationship_text = (
            " Its catalog metadata explicitly references the source table."
            if references_source
            else ""
        )

        recommendations.append(
            (
                "Knowledge Catalog identified "
                f"{candidate_name} as a documented curated/pre-aggregated "
                "asset worth evaluating for this analytical workload."
                f"{relationship_text} "
                "No automatic table substitution was applied because "
                "semantic compatibility has not been proven."
            )
        )

    return recommendations


def _build_agent_strategy_text(
    agent_plan,
) -> str:
    """
    Compact strategist summary for the Gemini explanation layer.
    """

    if agent_plan is None:
        return "Unavailable."

    proposed_changes = (
        getattr(
            agent_plan,
            "proposed_changes",
            None,
        )
        or []
    )

    change_lines = []

    for change in proposed_changes[:3]:
        if hasattr(
            change,
            "model_dump",
        ):
            change = (
                change.model_dump()
            )

        if not isinstance(
            change,
            dict,
        ):
            continue

        change_lines.append(
            (
                f"- [{change.get('code', 'UNKNOWN')}] "
                f"{change.get('title', 'Review opportunity')}: "
                f"{change.get('rationale', '')}"
            )
        )

    proposed_changes_text = (
        "\n".join(
            change_lines
        )
        if change_lines
        else "- None."
    )

    return (
        f"Strategy: {getattr(agent_plan, 'strategy', None)}\n"
        f"Goal: {getattr(agent_plan, 'goal', None)}\n"
        f"Rationale: {getattr(agent_plan, 'rationale', None)}\n"
        f"Confidence: {getattr(agent_plan, 'confidence', None)}\n"
        f"Candidate generation requested: "
        f"{getattr(agent_plan, 'candidate_generation_requested', False)}\n"
        f"Proposed review items:\n"
        f"{proposed_changes_text}"
    )


def _build_catalog_alternatives_text(
    catalog_alternatives: dict | None,
) -> str:
    """
    Compact Knowledge Catalog alternative evidence for the explanation prompt.
    """

    if not catalog_alternatives:
        return "Not collected."

    candidates = (
        catalog_alternatives.get(
            "candidates",
            [],
        )
        or []
    )

    if not candidates:
        return (
            "Knowledge Catalog alternative discovery ran, "
            "but no candidate assets were found."
        )

    lines = []

    for candidate in candidates[:3]:
        candidate_name = (
            candidate.get(
                "fully_qualified_name"
            )
            or candidate.get(
                "display_name"
            )
            or "Unknown asset"
        )

        description = (
            candidate.get(
                "description"
            )
            or ""
        ).strip()

        if len(description) > 320:
            description = (
                description[:317]
                + "..."
            )

        lines.append(
            (
                f"- {candidate_name}; "
                f"references_source="
                f"{bool(candidate.get('references_source', False))}; "
                f"relevance_score="
                f"{candidate.get('relevance_score')}; "
                f"signals="
                f"{candidate.get('matched_keywords') or []}; "
                f"description={description or 'Unavailable'}"
            )
        )

    return "\n".join(
        lines
    )


# ============================================================
# WEBSOCKET ENDPOINT
# ============================================================

async def websocket_endpoint(
    websocket: WebSocket,
):
    """
    Enterprise SQL optimization pipeline.

    Architecture:

        User SQL
            ↓
        LangChain / Gemini Strategist
        (tool selection + strategy plan; advisory in Phase 2A)
            ↓
        QueryStructureAnalyzer
            ↓
        Database router
            ├── BigQuery
            │     ↓
            │ BigQueryEvidenceAnalyzer
            │     ↓
            │ BigQueryJoinEvidenceAnalyzer
            │     ↓
            │ BigQueryJoinOptimizer
            │     ↓
            │ BigQueryJoinCandidateGenerator
            │     ↓
            │ BigQueryOptimizer
            │
            └── PostgreSQL
                  ↓
              PostgreSQLOptimizer

            ↓
        Transitional deterministic rewrite
            ↓
        Database-specific recommendations
            ↓
        Database execution-plan / dry-run
            ↓
        Deterministic result
            ↓
        Gemini explanation
            ↓
        DONE

    IMPORTANT:

    Gemini never generates executable SQL.

    Actual SQL rewrites must remain deterministic and later
    pass through the quality gate.
    """

    await websocket.accept()

    print(
        "[WebSocket] Connection accepted"
    )

    pipeline_start = (
        time.perf_counter()
    )

    try:

        # =====================================================
        # 1. RECEIVE REQUEST
        # =====================================================

        data = (
            await websocket.receive_json()
        )

        print(
            "[WebSocket] Analysis request received "
            f"session={_short_session_id(data.get('session_id'))}, "
            f"sql_chars={len(data.get('sql_query') or '')}"
        )

        session_id = (
            data.get(
                "session_id"
            )
        )

        query = (
            data.get(
                "sql_query"
            )
        )

        if (
            not session_id
            or not query
        ):

            print(
                "[WebSocket] Missing "
                "session_id or sql_query"
            )

            await websocket.send_json(
                {
                    "error":
                        "Missing session_id or sql_query"
                }
            )

            return

        # =====================================================
        # 2. GET CONNECTION
        # =====================================================

        print(
            "[WebSocket] Looking for session="
            f"{_short_session_id(session_id)}"
        )

        conn_obj = (
            await conn_manager.get_connection(
                session_id
            )
        )

        if not conn_obj:

            await websocket.send_json(
                {
                    "error":
                        "Session not found"
                }
            )

            return

        database_type = (
            conn_obj[
                "type"
            ]
        )

        normalized_database_type = (
            database_type.lower()
        )

        print(
            "[WebSocket] Connection found: "
            f"{database_type}"
        )

        # =====================================================
        # 3. CREATE DATABASE REPOSITORY
        # =====================================================

        if (
            normalized_database_type
            ==
            "bigquery"
        ):

            db_repo = (
                BigQueryRepository(
                    conn_obj[
                        "client"
                    ],

                    conn_obj.get(
                        "dataset"
                    ),
                )
            )

            dialect = (
                "bigquery"
            )

        elif (
            normalized_database_type
            in {
                "postgres",
                "postgresql",
            }
        ):

            db_repo = (
                PostgresRepository(
                    conn_obj[
                        "engine"
                    ]
                )
            )

            dialect = (
                "postgres"
            )

        else:

            await websocket.send_json(
                {
                    "error": (
                        "Unsupported database type: "
                        f"{database_type}"
                    )
                }
            )

            return

        # =====================================================
        # 4. GET SCHEMA
        # =====================================================

        schema_start = (
            time.perf_counter()
        )

        schema = (
            await conn_manager.get_schema(
                session_id
            )
        )

        schema_time_ms = (
            time.perf_counter()
            -
            schema_start
        ) * 1000

        print(
            "[TIMING] Schema retrieval: "
            f"{schema_time_ms:.2f} ms"
        )

        if not schema:

            await websocket.send_json(
                {
                    "error":
                        "Schema not found for session"
                }
            )

            return

        # =====================================================
        # 4B. LANGCHAIN / GEMINI STRATEGY PLANNING
        # =====================================================
        #
        # Phase 2A: Gemini/LangChain chooses evidence tools and
        # proposes an optimization strategy. The existing
        # deterministic optimizer remains authoritative.
        #
        # The strategist does NOT generate executable SQL, approve
        # candidates, bypass semantic proof, or bypass the quality
        # gate. If it fails, the existing optimizer continues.
        # =====================================================

        agent_plan = None

        # Keep the request-scoped strategist available after planning so
        # deterministic stages can reuse evidence already gathered by its
        # tools instead of recomputing the same structure/metadata/joins.
        strategy_agent = None

        agent_strategy_time_ms = 0.0

        agent_strategy_start = (
            time.perf_counter()
        )

        try:

            print(
                "[Agent] Starting strategy reasoning..."
            )

            memory = (
                await conn_manager.get_memory(
                    session_id
                )
            )

            strategy_agent = (
                LangChainStrategyAgent(
                    database_type=
                        database_type,

                    conn_obj=
                        conn_obj,

                    db_repo=
                        db_repo,

                    memory=
                        memory,
                )
            )

            agent_plan = (
                await strategy_agent.plan(
                    query
                )
            )

            print(
                "[Agent] Strategy complete"
            )

            print(
                "[Agent] strategy="
                f"{agent_plan.strategy}, "
                "candidate_requested="
                f"{agent_plan.candidate_generation_requested}, "
                "requires_user_intent="
                f"{agent_plan.requires_user_intent}, "
                "confidence="
                f"{agent_plan.confidence}"
            )

            print(
                "[Agent] tools_used="
                f"{agent_plan.tools_used}"
            )

        except Exception as exc:

            print(
                "[Agent] Strategy unavailable: "
                f"{type(exc).__name__}: {exc}"
            )

            agent_plan = None

        finally:

            agent_strategy_time_ms = (
                time.perf_counter()
                -
                agent_strategy_start
            ) * 1000

            print(
                "[TIMING] Agent strategy planning: "
                f"{agent_strategy_time_ms:.2f} ms"
            )

        # =====================================================
        # 5. GENERIC QUERY STRUCTURE ANALYSIS
        # =====================================================

        print(
            "[WebSocket] Running generic "
            "query structure analysis..."
        )

        structure_start = (
            time.perf_counter()
        )

        try:

            cached_structure = (
                strategy_agent.context.structure
                if (
                    strategy_agent is not None
                    and
                    getattr(
                        strategy_agent,
                        "context",
                        None,
                    )
                    is not None
                )
                else None
            )

            if cached_structure is not None:

                structure = cached_structure

                print(
                    "[Agent Reuse] Query structure reused "
                    "from strategist evidence context."
                )

            else:

                structure = (
                    query_structure_analyzer.analyze(
                        query,
                        dialect=dialect,
                    )
                )

        except Exception as exc:

            print(
                "[WebSocket] Query structure "
                f"analysis failed: {exc}"
            )

            await websocket.send_json(
                {
                    "error": (
                        "SQL parsing failed: "
                        f"{exc}"
                    )
                }
            )

            return

        structure_time_ms = (
            time.perf_counter()
            -
            structure_start
        ) * 1000

        print(
            "[TIMING] Query structure analysis: "
            f"{structure_time_ms:.2f} ms"
        )

        print(
            "[WebSocket] Structure: "
            f"tables={len(structure.tables)}, "
            f"joins={structure.join_count}, "
            f"ctes={structure.cte_count}, "
            f"subqueries={structure.subquery_count}, "
            f"select_star={structure.has_select_star}"
        )

        # =====================================================
        # 6. DATABASE-SPECIFIC EVIDENCE + ANALYSIS
        # =====================================================

        print(
            "[WebSocket] Running "
            f"{database_type}-specific analysis..."
        )

        database_optimizer_start = (
            time.perf_counter()
        )

        bigquery_evidence = None

        bigquery_join_evidence = None

        bigquery_join_optimization = None

        bigquery_join_candidate = None

        bigquery_preaggregation_semantic = None
        bigquery_preaggregation_candidate = None
        preaggregation_semantic_time_ms = 0.0
        preaggregation_candidate_validation_time_ms = 0.0

        evidence_time_ms = 0.0

        join_evidence_time_ms = 0.0

        join_optimization_time_ms = 0.0

        join_candidate_generation_time_ms = 0.0

        join_candidate_validation_time_ms = 0.0

        required_user_input = None

        # -----------------------------------------------------
        # BIGQUERY PATH
        # -----------------------------------------------------

        if (
            normalized_database_type
            ==
            "bigquery"
        ):

            print(
                "[WebSocket] Loading BigQuery "
                "metadata evidence..."
            )

            evidence_start = (
                time.perf_counter()
            )

            try:

                cached_bigquery_evidence = (
                    strategy_agent.context.bigquery_evidence
                    if (
                        strategy_agent is not None
                        and
                        getattr(
                            strategy_agent,
                            "context",
                            None,
                        )
                        is not None
                    )
                    else None
                )

                if cached_bigquery_evidence is not None:

                    bigquery_evidence = (
                        cached_bigquery_evidence
                    )

                    print(
                        "[Agent Reuse] BigQuery metadata evidence "
                        "reused from strategist evidence context."
                    )

                else:

                    evidence_analyzer = (
                        BigQueryEvidenceAnalyzer(
                            client=
                                conn_obj[
                                    "client"
                                ],

                            default_dataset=
                                conn_obj.get(
                                    "dataset"
                                ),
                        )
                    )

                    bigquery_evidence = (
                        await evidence_analyzer.analyze(
                            structure
                        )
                    )

            except Exception as exc:

                print(
                    "[WebSocket] BigQuery "
                    f"evidence failed: {exc}"
                )

                bigquery_evidence = {
                    "database_type":
                        "bigquery",

                    "tables":
                        [],

                    "table_count":
                        0,

                    "metadata_errors": [
                        {
                            "error":
                                str(
                                    exc
                                )
                        }
                    ],
                }

            evidence_time_ms = (
                time.perf_counter()
                -
                evidence_start
            ) * 1000

            print(
                "[TIMING] BigQuery evidence: "
                f"{evidence_time_ms:.2f} ms"
            )

            print(
                "[WebSocket] BigQuery evidence "
                "tables="
                f"{bigquery_evidence.get('table_count', 0)}"
            )

            # -------------------------------------------------
            # LOG REFERENCED TABLE EVIDENCE
            # -------------------------------------------------

            for table in (
                bigquery_evidence.get(
                    "tables",
                    [],
                )
            ):

                print(
                    "[BigQuery Evidence] "
                    f"{table.get('full_name')} | "
                    f"rows={table.get('row_count')} | "
                    f"bytes={table.get('size_bytes')} | "
                    f"partitioned={table.get('partitioned')} | "
                    f"partition_field="
                    f"{table.get('partition_field')} | "
                    f"clustering="
                    f"{table.get('clustering_fields')}"
                )

            # -------------------------------------------------
            # LOG METADATA ERRORS
            # -------------------------------------------------

            for metadata_error in (
                bigquery_evidence.get(
                    "metadata_errors",
                    [],
                )
            ):

                print(
                    "[BigQuery Evidence] "
                    "Metadata error: "
                    f"{metadata_error}"
                )

            # -------------------------------------------------
            # BIGQUERY JOIN EVIDENCE
            # -------------------------------------------------

            print(
                "[WebSocket] Running BigQuery "
                "join evidence analysis..."
            )

            join_evidence_start = (
                time.perf_counter()
            )

            try:

                cached_join_evidence = (
                    strategy_agent.context.join_evidence
                    if (
                        strategy_agent is not None
                        and
                        getattr(
                            strategy_agent,
                            "context",
                            None,
                        )
                        is not None
                    )
                    else None
                )

                if cached_join_evidence is not None:

                    bigquery_join_evidence = (
                        cached_join_evidence
                    )

                    print(
                        "[Agent Reuse] BigQuery join evidence "
                        "reused from strategist evidence context."
                    )

                else:

                    bigquery_join_evidence = (
                        bigquery_join_evidence_analyzer.analyze(
                            sql=
                                query,

                            structure=
                                structure,

                            table_evidence=
                                bigquery_evidence,
                        )
                    )

            except Exception as exc:

                print(
                    "[WebSocket] BigQuery join evidence "
                    f"failed: {exc}"
                )

                bigquery_join_evidence = {
                    "join_count":
                        structure.join_count,

                    "edges":
                        [],

                    "findings":
                        [
                            {
                                "code":
                                    "BQ_JOIN_EVIDENCE_FAILED",

                                "severity":
                                    "warning",

                                "category":
                                    "join_analysis",

                                "message":
                                    (
                                        "BigQuery join evidence analysis "
                                        "could not be completed."
                                    ),

                                "evidence": {
                                    "error":
                                        str(
                                            exc
                                        ),
                                },
                            }
                        ],

                    "has_cross_join":
                        False,

                    "large_join_count":
                        0,
                }

            join_evidence_time_ms = (
                time.perf_counter()
                -
                join_evidence_start
            ) * 1000

            print(
                "[TIMING] BigQuery join evidence: "
                f"{join_evidence_time_ms:.2f} ms"
            )

            print(
                "[WebSocket] Join evidence: "
                f"edges={bigquery_join_evidence.get('join_count', 0)}"
            )

            for edge in (
                bigquery_join_evidence.get(
                    "edges",
                    [],
                )
            ):

                right_source = (
                    edge.get(
                        "right_source"
                    )
                    or {}
                )

                print(
                    "[BigQuery Join Evidence] "
                    f"scope={edge.get('scope_index')} | "
                    f"join={edge.get('join_index')} | "
                    f"type={edge.get('join_type')} | "
                    f"left={edge.get('referenced_left_aliases')} | "
                    f"right={right_source.get('alias')} | "
                    f"condition={edge.get('condition')} | "
                    f"keys={edge.get('equality_pairs')} | "
                    f"size_ratio={edge.get('static_size_ratio')} | "
                    f"risk={edge.get('risk_level')}"
                )

            # -------------------------------------------------
            # BIGQUERY JOIN OPTIMIZATION INTELLIGENCE
            # -------------------------------------------------

            print(
                "[WebSocket] Running BigQuery "
                "join optimization intelligence..."
            )

            join_optimization_start = (
                time.perf_counter()
            )

            try:

                cached_join_optimization = (
                    strategy_agent.context.join_optimization
                    if (
                        strategy_agent is not None
                        and
                        getattr(
                            strategy_agent,
                            "context",
                            None,
                        )
                        is not None
                    )
                    else None
                )

                if cached_join_optimization is not None:

                    bigquery_join_optimization = (
                        cached_join_optimization
                    )

                    print(
                        "[Agent Reuse] BigQuery join optimization "
                        "opportunities reused from strategist "
                        "evidence context."
                    )

                else:

                    bigquery_join_optimization = (
                        bigquery_join_optimizer.analyze(
                            join_evidence=
                                bigquery_join_evidence,

                            structure=
                                structure,
                        )
                    )

            except Exception as exc:

                print(
                    "[WebSocket] BigQuery join optimizer "
                    f"failed: {exc}"
                )

                bigquery_join_optimization = {
                    "database_type":
                        "bigquery",

                    "opportunity_count":
                        0,

                    "opportunities":
                        [],

                    "recommendations":
                        [],

                    "safe_rewrite_available":
                        False,

                    "candidate_generation_allowed":
                        False,

                    "error": {
                        "code":
                            "BQ_JOIN_OPTIMIZER_FAILED",

                        "message":
                            str(
                                exc
                            ),
                    },
                }

            join_optimization_time_ms = (
                time.perf_counter()
                -
                join_optimization_start
            ) * 1000

            print(
                "[TIMING] BigQuery join optimization: "
                f"{join_optimization_time_ms:.2f} ms"
            )

            print(
                "[WebSocket] Join optimization opportunities: "
                f"{bigquery_join_optimization.get('opportunity_count', 0)}"
            )

            for opportunity in (
                bigquery_join_optimization.get(
                    "opportunities",
                    [],
                )
            ):

                print(
                    "[BigQuery Join Optimization] "
                    f"{opportunity.get('code')} | "
                    f"severity={opportunity.get('severity')} | "
                    f"rewrite_status={opportunity.get('rewrite_status')} | "
                    f"title={opportunity.get('title')}"
                )

            # -------------------------------------------------
            # BIGQUERY JOIN CANDIDATE GENERATION
            # -------------------------------------------------

            print(
                "[WebSocket] Evaluating safe BigQuery "
                "join candidate generation..."
            )

            join_candidate_generation_start = (
                time.perf_counter()
            )

            try:

                bigquery_join_candidate = (
                    bigquery_join_candidate_generator.generate(
                        sql=
                            query,

                        join_evidence=
                            bigquery_join_evidence,

                        table_evidence=
                            bigquery_evidence,
                    )
                )

            except Exception as exc:

                print(
                    "[WebSocket] BigQuery join candidate "
                    f"generation failed: {exc}"
                )

                bigquery_join_candidate = {
                    "generated":
                        False,

                    "candidate_sql":
                        None,

                    "rewrite_type":
                        None,

                    "rule_codes":
                        [],

                    "semantic_proof": {
                        "status":
                            "ERROR",
                    },

                    "requires_quality_gate":
                        False,

                    "reason":
                        (
                            "Join candidate generation failed."
                        ),
                }

            join_candidate_generation_time_ms = (
                time.perf_counter()
                -
                join_candidate_generation_start
            ) * 1000

            print(
                "[TIMING] BigQuery join candidate generation: "
                f"{join_candidate_generation_time_ms:.2f} ms"
            )

            print(
                "[WebSocket] Join candidate generated: "
                f"{bigquery_join_candidate.get('generated', False)}"
            )

            if (
                bigquery_join_candidate.get(
                    "generated"
                )
            ):

                print(
                    "[BigQuery Join Candidate] "
                    f"rewrite_type="
                    f"{bigquery_join_candidate.get('rewrite_type')} | "
                    f"rules="
                    f"{bigquery_join_candidate.get('rule_codes')} | "
                    f"semantic_status="
                    f"{(bigquery_join_candidate.get('semantic_proof') or {}).get('status')}"
                )

            else:

                print(
                    "[BigQuery Join Candidate] "
                    f"No candidate: "
                    f"{bigquery_join_candidate.get('reason')}"
                )

            # -------------------------------------------------
            # BIGQUERY OPTIMIZER
            # -------------------------------------------------

            database_optimizer_result = (
                bigquery_optimizer.analyze(
                    structure=
                        structure,

                    metadata=
                        bigquery_evidence,
                )
            )

            # -------------------------------------------------
            # USER INTENT ANALYSIS
            # -------------------------------------------------

            required_user_input = (
                bigquery_user_intent_analyzer.analyze(
                    structure=
                        structure,

                    evidence=
                        bigquery_evidence,
                )
            )

            if required_user_input:

                print(
                    "[WebSocket] User input required: "
                    f"{required_user_input.type}"
                )

        # -----------------------------------------------------
        # POSTGRESQL PATH
        # -----------------------------------------------------

        else:

            database_optimizer_result = (
                postgresql_optimizer.analyze(
                    structure=
                        structure,

                    metadata=
                        None,
                )
            )

        database_optimizer_time_ms = (
            time.perf_counter()
            -
            database_optimizer_start
        ) * 1000

        print(
            "[TIMING] Database-specific analysis: "
            f"{database_optimizer_time_ms:.2f} ms"
        )

        raw_database_findings = (
            list(
                database_optimizer_result.get(
                    "findings",
                    [],
                )
                or []
            )
        )

        # =====================================================
        # JOIN RECOMMENDATION OWNERSHIP BOUNDARY
        # =====================================================
        #
        # The general BigQueryOptimizer historically emitted some
        # join-specific recommendations. Those are now owned by
        # BigQueryJoinOptimizer. Filter them here before merging
        # deterministic join evidence, so the response has one
        # recommendation owner and no duplicate warning path.
        # =====================================================

        if (
            normalized_database_type
            ==
            "bigquery"
        ):

            database_findings = [
                finding

                for finding
                in raw_database_findings

                if finding.get(
                    "code"
                )
                not in
                JOIN_OPTIMIZER_OWNED_GENERIC_FINDING_CODES
            ]

        else:

            database_findings = (
                raw_database_findings
            )

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_join_evidence
        ):

            database_findings.extend(
                list(
                    bigquery_join_evidence.get(
                        "findings",
                        [],
                    )
                    or []
                )
            )

        error_findings = []
        warning_findings = []
        informational_findings = []

        for finding in (
            database_findings
            or []
        ):

            severity = (
                finding.get(
                    "severity",
                    "info",
                )
                or "info"
            ).lower()

            if severity in {
                "error",
                "critical",
            }:

                error_findings.append(
                    finding
                )

            elif severity in {
                "warning",
                "warn",
            }:

                warning_findings.append(
                    finding
                )

            else:

                informational_findings.append(
                    finding
                )

        database_issue_findings = (
            list(
                error_findings
            )
            +
            list(
                warning_findings
            )
        )

        database_finding_messages = (
            _extract_finding_messages(
                {
                    "findings":
                        database_issue_findings
                }
            )
        )

        information_messages = (
            _extract_finding_messages(
                {
                    "findings":
                        informational_findings
                }
            )
        )

        print(
            "[WebSocket] Database findings: "
            f"{database_findings}"
        )

        if (
            normalized_database_type
            ==
            "bigquery"
        ):

            filtered_join_owned_codes = [
                finding.get(
                    "code"
                )

                for finding
                in raw_database_findings

                if finding.get(
                    "code"
                )
                in
                JOIN_OPTIMIZER_OWNED_GENERIC_FINDING_CODES
            ]

            print(
                "[WebSocket] General BigQuery join findings "
                "suppressed by ownership boundary: "
                f"{filtered_join_owned_codes}"
            )

        print(
            "[WebSocket] Database issue findings: "
            f"{database_issue_findings}"
        )

        print(
            "[WebSocket] Informational findings: "
            f"{informational_findings}"
        )

        # =====================================================
        # 7. LEGACY ISSUE DETECTION
        # =====================================================

        print(
            "[WebSocket] Running legacy "
            "deterministic issue detection..."
        )

        issue_start = (
            time.perf_counter()
        )

        legacy_issues = (
            await detect_issues(
                query,
                dialect=dialect,
            )
        )

        issue_time_ms = (
            time.perf_counter()
            -
            issue_start
        ) * 1000

        print(
            "[TIMING] Legacy issue detection: "
            f"{issue_time_ms:.2f} ms"
        )

        # =========================================================
        # REFINE GENERIC FINDINGS USING DATABASE EVIDENCE
        # =========================================================

        refined_legacy_issues = (
            _refine_legacy_issues(
                legacy_issues=
                    legacy_issues,

                database_type=
                    normalized_database_type,

                bigquery_evidence=
                    bigquery_evidence,

                database_findings=
                    database_findings,
            )
        )

        # =========================================================
        # MERGE LEGACY + DATABASE-SPECIFIC FINDINGS
        # =========================================================

        issues = (
            _deduplicate_strings(
                list(
                    refined_legacy_issues
                    or []
                )
                +
                database_finding_messages
            )
        )

        print(
            "[WebSocket] Issues detected: "
            f"{issues}"
        )

        # =====================================================
        # 8. TRANSITIONAL DETERMINISTIC SQL OPTIMIZATION
        # =====================================================

        print(
            "[WebSocket] Running transitional "
            "deterministic SQL optimization..."
        )

        optimization_start = (
            time.perf_counter()
        )

        optimization_result = (
            await optimize_sql(
                sql=
                    query,

                schema=
                    schema,

                dataset=
                    conn_obj.get(
                        "dataset"
                    ),

                db_repo=
                    db_repo,

                knowledge_catalog=
                    None,

                database_type=
                    database_type,
            )
        )

        optimized_sql = (
            optimization_result.get(
                "optimized_sql",
                query,
            )
        )

        rules_applied = (
            optimization_result.get(
                "rules_applied",
                [],
            )
        )

        changed = (
            optimization_result.get(
                "changed",
                False,
            )
        )

        normalized = (
            optimization_result.get(
                "normalized",
                False,
            )
        )

        normalization_message = (
            optimization_result.get(
                "normalization_message"
            )
        )

        optimization_time_ms = (
            time.perf_counter()
            -
            optimization_start
        ) * 1000

        print(
            "[TIMING] Deterministic optimization: "
            f"{optimization_time_ms:.2f} ms"
        )

        print(
            "[WebSocket] SQL changed "
            "(performance rewrite): "
            f"{changed}"
        )

        print(
            "[WebSocket] SQL normalized: "
            f"{normalized}"
        )

        print(
            "[WebSocket] Rules applied: "
            f"{rules_applied}"
        )

        # =====================================================
        # 9. DATABASE-SPECIFIC RECOMMENDATIONS
        # =====================================================

        print(
            "[WebSocket] Generating "
            "database-specific recommendations..."
        )

        recommendation_start = (
            time.perf_counter()
        )

        legacy_recommendations = (
            await recommend_structures(
                sql=
                    query,

                schema=
                    schema,

                database_type=
                    database_type,
            )
        )

        join_recommendations = (
            (
                bigquery_join_optimization.get(
                    "recommendations",
                    [],
                )
                if (
                    normalized_database_type
                    ==
                    "bigquery"
                    and
                    bigquery_join_optimization
                )
                else []
            )
            or []
        )

        catalog_alternatives = (
            _get_agent_catalog_alternatives(
                strategy_agent
            )
        )

        catalog_recommendations = (
            _build_catalog_recommendations(
                agent_plan=
                    agent_plan,

                catalog_alternatives=
                    catalog_alternatives,
            )
        )

        recommendations = (
            _deduplicate_strings(
                list(
                    legacy_recommendations
                    or []
                )
                +
                list(
                    join_recommendations
                )
                +
                list(
                    catalog_recommendations
                )
            )
        )

        if catalog_recommendations:
            print(
                "[WebSocket] Knowledge Catalog recommendations: "
                f"{catalog_recommendations}"
            )

        # =====================================================
        # 9B. PRE-AGGREGATION SEMANTIC PROOF + CONTROLLED CANDIDATE
        # =====================================================
        if (
            normalized_database_type == "bigquery"
            and agent_plan is not None
            and getattr(agent_plan, "strategy", None) == "PREAGGREGATION_REVIEW"
            and catalog_alternatives
        ):
            preaggregation_semantic_start = time.perf_counter()
            try:
                print("[WebSocket] Running deterministic pre-aggregation semantic proof...")
                bigquery_preaggregation_semantic = (
                    await bigquery_preaggregation_semantic_validator.analyze(
                        sql=query,
                        catalog_alternatives=catalog_alternatives,
                        client=conn_obj["client"],
                        default_project=(conn_obj.get("project") or conn_obj.get("project_id")),
                        default_dataset=conn_obj.get("dataset"),
                    )
                )
                print(
                    "[BigQuery Preaggregation Semantic] "
                    f"status={bigquery_preaggregation_semantic.get('status')} | "
                    f"candidate={bigquery_preaggregation_semantic.get('candidate_table')}"
                )
                bigquery_preaggregation_candidate = (
                    bigquery_preaggregation_candidate_generator.generate(
                        semantic_proof=bigquery_preaggregation_semantic
                    )
                )
                print(
                    "[BigQuery Preaggregation Candidate] "
                    f"generated={bigquery_preaggregation_candidate.get('generated', False)}"
                )
                if bigquery_preaggregation_candidate.get("generated"):
                    print(
                        "[BigQuery Preaggregation Candidate] "
                        f"sql={bigquery_preaggregation_candidate.get('candidate_sql')}"
                    )
            except Exception as exc:
                print(
                    "[WebSocket] Pre-aggregation semantic/candidate generation failed: "
                    f"{type(exc).__name__}: {exc}"
                )
                bigquery_preaggregation_semantic = {
                    "status": "ERROR",
                    "supported": False,
                    "reason": "Pre-aggregation semantic proof could not be completed.",
                }
                bigquery_preaggregation_candidate = {
                    "generated": False,
                    "candidate_sql": None,
                    "rewrite_type": None,
                    "rule_codes": [],
                    "semantic_proof": bigquery_preaggregation_semantic,
                    "requires_quality_gate": False,
                    "reason": "Pre-aggregation candidate generation was unavailable.",
                }
            finally:
                preaggregation_semantic_time_ms = (
                    time.perf_counter() - preaggregation_semantic_start
                ) * 1000
                print(
                    "[TIMING] Pre-aggregation semantic proof + candidate: "
                    f"{preaggregation_semantic_time_ms:.2f} ms"
                )

        semantic_proof_status = (
            (
                bigquery_join_candidate.get(
                    "semantic_proof",
                    {},
                )
                or {}
            ).get(
                "status"
            )
            if (
                bigquery_join_candidate
                and
                bigquery_join_candidate.get(
                    "generated"
                )
            )
            else None
        )

        if (
            semantic_proof_status
            ==
            "PROVEN_FOR_SUPPORTED_PATTERN"
        ):
            # Once deterministic proof has established that the supported
            # transformed-key rewrite is safe, do not keep telling the user
            # to "review whether the transformation is required" or that
            # semantic proof is still needed.
            stale_transformed_recommendation_markers = (
                "review whether the transformation is required",
                "equivalent typed keys",
                "precomputed normalized key",
            )

            recommendations = [
                recommendation
                for recommendation
                in recommendations
                if not any(
                    marker
                    in recommendation.lower()
                    for marker
                    in stale_transformed_recommendation_markers
                )
            ]

        recommendation_time_ms = (
            time.perf_counter()
            -
            recommendation_start
        ) * 1000

        print(
            "[TIMING] Recommendations: "
            f"{recommendation_time_ms:.2f} ms"
        )

        # =====================================================
        # 10. DATABASE EXECUTION PLAN / DRY RUN
        # =====================================================

        print(
            "[WebSocket] Running database "
            "execution-plan analysis..."
        )

        dry_run_start = (
            time.perf_counter()
        )

        bytes_processed = None

        estimated_cost_usd = None

        scan_type = None

        try:

            plan = (
                await db_repo.get_execution_plan(
                    optimized_sql
                )
            )

            bytes_processed = (
                plan.total_bytes_processed
                if (
                    plan.total_bytes_processed
                    is not None
                )
                else 0
            )

            estimated_cost_usd = (
                plan.cost
            )

            scan_type = (
                plan.scan_type
            )

            print(
                "[WebSocket] Bytes processed: "
                f"{bytes_processed}"
            )

            print(
                "[WebSocket] Estimated cost: "
                f"{estimated_cost_usd}"
            )

            print(
                "[WebSocket] Scan type: "
                f"{scan_type}"
            )

        except Exception as exc:

            print(
                "[WebSocket] Error getting "
                f"execution plan: {exc}"
            )

        dry_run_time_ms = (
            time.perf_counter()
            -
            dry_run_start
        ) * 1000

        print(
            "[TIMING] Database plan analysis: "
            f"{dry_run_time_ms:.2f} ms"
        )

        # =====================================================
        # 10B. BIGQUERY JOIN CANDIDATE QUALITY GATE
        # =====================================================

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_join_candidate
            and
            bigquery_join_candidate.get(
                "generated"
            )
        ):

            print(
                "[WebSocket] Validating BigQuery "
                "join candidate..."
            )

            join_candidate_validation_start = (
                time.perf_counter()
            )

            try:

                semantic_proof = (
                    bigquery_join_candidate.get(
                        "semantic_proof",
                        {},
                    )
                    or {}
                )

                runtime_eligible = (
                    semantic_proof.get(
                        "status"
                    )
                    ==
                    "PROVEN_FOR_SUPPORTED_PATTERN"
                )

                quality_gate = (
                    await bigquery_candidate_validator.compare(
                        repository=
                            db_repo,

                        original_sql=
                            query,

                        candidate_sql=
                            bigquery_join_candidate[
                                "candidate_sql"
                            ],

                        # Reuse the original dry run that was already
                        # produced above. This avoids a duplicate BigQuery
                        # dry-run API call during candidate validation.
                        original_estimate={
                            "bytes_processed":
                                bytes_processed,

                            "estimated_cost_usd":
                                estimated_cost_usd,

                            "scan_type":
                                scan_type,
                        },

                        # Runtime benchmarking is only eligible when the
                        # deterministic semantic-proof layer has already
                        # proven the supported rewrite pattern.
                        allow_runtime_validation=
                            runtime_eligible,

                        # Runtime execution is user-triggered through the
                        # dedicated REST benchmark endpoint. Optimize Query
                        # performs semantic + dry-run validation only.
                        execute_runtime_validation=
                            False,
                    )
                )

                bigquery_join_candidate[
                    "quality_gate"
                ] = quality_gate

                bigquery_join_candidate[
                    "original_query"
                ] = query

                bigquery_join_candidate[
                    "candidate_query"
                ] = (
                    bigquery_join_candidate[
                        "candidate_sql"
                    ]
                )

                decision = (
                    quality_gate.get(
                        "decision"
                    )
                )

                print(
                    "[BigQuery Join Candidate] "
                    f"quality_gate={decision}"
                )

                if (
                    decision
                    in {
                        "APPROVE_SCAN_COST",
                        "APPROVE_RUNTIME_IMPROVEMENT",
                    }
                ):

                    optimized_sql = (
                        bigquery_join_candidate[
                            "candidate_sql"
                        ]
                    )

                    changed = (
                        True
                    )

                    normalized = (
                        False
                    )

                    for rule_code in (
                        bigquery_join_candidate.get(
                            "rule_codes",
                            [],
                        )
                        or []
                    ):

                        if (
                            rule_code
                            not in
                            rules_applied
                        ):

                            rules_applied.append(
                                rule_code
                            )

                    candidate_metrics = (
                        quality_gate.get(
                            "candidate",
                            {},
                        )
                        or {}
                    )

                    if (
                        candidate_metrics.get(
                            "bytes_processed"
                        )
                        is not None
                    ):

                        bytes_processed = (
                            candidate_metrics[
                                "bytes_processed"
                            ]
                        )

                    if (
                        candidate_metrics.get(
                            "estimated_cost_usd"
                        )
                        is not None
                    ):

                        estimated_cost_usd = (
                            candidate_metrics[
                                "estimated_cost_usd"
                            ]
                        )

            except Exception as exc:

                print(
                    "[WebSocket] BigQuery join candidate "
                    f"validation failed: {exc}"
                )

                bigquery_join_candidate[
                    "quality_gate"
                ] = {
                    "decision":
                        "REJECT_VALIDATION_ERROR",

                    "reason":
                        (
                            "The candidate could not be validated "
                            "by BigQuery."
                        ),
                }

            join_candidate_validation_time_ms = (
                time.perf_counter()
                -
                join_candidate_validation_start
            ) * 1000

            print(
                "[TIMING] BigQuery join candidate validation: "
                f"{join_candidate_validation_time_ms:.2f} ms"
            )

        # =====================================================
        # 10C. BIGQUERY PRE-AGGREGATION CANDIDATE QUALITY GATE
        # =====================================================
        if (
            normalized_database_type == "bigquery"
            and bigquery_preaggregation_candidate
            and bigquery_preaggregation_candidate.get("generated")
        ):
            print("[WebSocket] Validating BigQuery pre-aggregation candidate...")
            preaggregation_candidate_validation_start = time.perf_counter()
            try:
                preaggregation_quality_gate = await bigquery_candidate_validator.compare(
                    repository=db_repo,
                    original_sql=query,
                    candidate_sql=bigquery_preaggregation_candidate["candidate_sql"],
                    original_estimate={
                        "bytes_processed": bytes_processed,
                        "estimated_cost_usd": estimated_cost_usd,
                        "scan_type": scan_type,
                    },
                    allow_runtime_validation=False,
                    execute_runtime_validation=False,
                )
                bigquery_preaggregation_candidate["quality_gate"] = preaggregation_quality_gate
                bigquery_preaggregation_candidate["original_query"] = query
                bigquery_preaggregation_candidate["candidate_query"] = bigquery_preaggregation_candidate["candidate_sql"]
                preaggregation_decision = preaggregation_quality_gate.get("decision")
                print(
                    "[BigQuery Preaggregation Candidate] "
                    f"quality_gate={preaggregation_decision}"
                )

                if preaggregation_decision in {"APPROVE_SCAN_COST", "APPROVE_RUNTIME_IMPROVEMENT"}:
                    optimized_sql = bigquery_preaggregation_candidate["candidate_sql"]
                    changed = True
                    normalized = False
                    for rule_code in (bigquery_preaggregation_candidate.get("rule_codes", []) or []):
                        if rule_code not in rules_applied:
                            rules_applied.append(rule_code)
                    candidate_metrics = preaggregation_quality_gate.get("candidate", {}) or {}
                    if candidate_metrics.get("bytes_processed") is not None:
                        bytes_processed = candidate_metrics["bytes_processed"]
                    if candidate_metrics.get("estimated_cost_usd") is not None:
                        estimated_cost_usd = candidate_metrics["estimated_cost_usd"]
                    recommendations = [
                        recommendation
                        for recommendation in recommendations
                        if "no automatic table substitution was applied" not in recommendation.lower()
                    ]
            except Exception as exc:
                print(
                    "[WebSocket] BigQuery pre-aggregation candidate validation failed: "
                    f"{exc}"
                )
                bigquery_preaggregation_candidate["quality_gate"] = {
                    "decision": "REJECT_VALIDATION_ERROR",
                    "approved": False,
                    "reason": "The pre-aggregation candidate could not be validated by BigQuery.",
                }

            preaggregation_candidate_validation_time_ms = (
                time.perf_counter() - preaggregation_candidate_validation_start
            ) * 1000
            print(
                "[TIMING] BigQuery pre-aggregation candidate validation: "
                f"{preaggregation_candidate_validation_time_ms:.2f} ms"
            )

        # =====================================================
        # 11. OPTIMIZATION STATUS
        # =====================================================

        candidate_gate_decision = None

        preaggregation_gate_decision = (
            ((bigquery_preaggregation_candidate.get("quality_gate", {}) or {}).get("decision"))
            if (bigquery_preaggregation_candidate and bigquery_preaggregation_candidate.get("generated"))
            else None
        )

        if preaggregation_gate_decision is not None:
            candidate_gate_decision = preaggregation_gate_decision

        if (
            candidate_gate_decision is None
            and
            bigquery_join_candidate
            and
            bigquery_join_candidate.get(
                "generated"
            )
        ):
            candidate_gate_decision = (
                (
                    bigquery_join_candidate.get(
                        "quality_gate",
                        {},
                    )
                    or {}
                ).get(
                    "decision"
                )
            )

        join_opportunities = (
            (
                bigquery_join_optimization.get(
                    "opportunities",
                    [],
                )
                if bigquery_join_optimization
                else []
            )
            or []
        )

        has_recommendation_only_opportunity = any(
            opportunity.get(
                "rewrite_status"
            )
            in {
                "RECOMMEND_ONLY",
                "REQUIRES_SEMANTIC_PROOF",
            }
            for opportunity in join_opportunities
        )

        has_user_intent_opportunity = any(
            opportunity.get(
                "rewrite_status"
            )
            ==
            "REQUIRES_USER_INTENT"
            for opportunity in join_opportunities
        )

        # -----------------------------------------------------
        # Canonical user-facing status vocabulary.
        # -----------------------------------------------------
        # Keep low-level normalization as metadata only. A formatting
        # difference should not change the user-facing optimization state.

        if required_user_input is not None:

            optimization_status = (
                "NEEDS_USER_INTENT"
            )

        elif candidate_gate_decision in {
            "APPROVE_SCAN_COST",
            "APPROVE_RUNTIME_IMPROVEMENT",
        }:

            optimization_status = (
                candidate_gate_decision
            )

        elif (
            candidate_gate_decision
            ==
            "NEEDS_RUNTIME_VALIDATION"
        ):

            optimization_status = (
                "NEEDS_RUNTIME_VALIDATION"
            )

        elif candidate_gate_decision in {
            "SCAN_COST_UNCHANGED",
            "RUNTIME_NO_MEASURED_IMPROVEMENT",
            "RUNTIME_INCONCLUSIVE",
            "RUNTIME_VALIDATION_BLOCKED",
            "REJECT_HIGHER_SCAN_COST",
            "REJECT_INVALID_CANDIDATE",
            "REJECT_VALIDATION_ERROR",
        }:

            optimization_status = (
                candidate_gate_decision
            )

        elif has_user_intent_opportunity:

            optimization_status = (
                "NEEDS_USER_INTENT"
            )

        elif (
            agent_plan is not None
            and
            getattr(
                agent_plan,
                "strategy",
                None,
            )
            ==
            "PREAGGREGATION_REVIEW"
            and
            catalog_recommendations
        ):

            # A real optimization opportunity was discovered, but it is
            # recommendation-only because semantic compatibility with the
            # alternative asset has not been proven.
            optimization_status = (
                "RECOMMENDATIONS_ONLY"
            )

        elif has_recommendation_only_opportunity:

            optimization_status = (
                "RECOMMENDATIONS_ONLY"
            )

        elif recommendations:

            optimization_status = (
                "RECOMMENDATIONS_ONLY"
            )

        elif changed:

            # Transitional rewrite path. Keep explicit until every
            # transformation is routed through the shared quality gate.
            optimization_status = (
                "CANDIDATE_REWRITE"
            )

        else:

            optimization_status = (
                "NO_OPTIMIZATION_NEEDED"
            )

        print(
            "[WebSocket] Optimization status: "
            f"{optimization_status}"
        )

        # -----------------------------------------------------
        # Redis workflow state
        # -----------------------------------------------------

        try:
            agent_state_store.save(
                session_id,
                {
                    "optimization_status":
                        optimization_status,

                    "database_type":
                        database_type,

                    "has_candidate":
                        bool(
                            (bigquery_join_candidate and bigquery_join_candidate.get("generated"))
                            or
                            (bigquery_preaggregation_candidate and bigquery_preaggregation_candidate.get("generated"))
                        ),

                    "candidate_gate_decision":
                        candidate_gate_decision,

                    "requires_user_input":
                        (
                            required_user_input
                            is not None
                        ),

                    "agent_strategy":
                        (
                            agent_plan.model_dump()
                            if agent_plan is not None
                            else None
                        ),

                    "agent_evidence_reused":
                        bool(
                            strategy_agent is not None
                            and
                            getattr(
                                strategy_agent,
                                "context",
                                None,
                            )
                            is not None
                            and
                            (
                                strategy_agent.context.structure
                                is not None
                                or
                                strategy_agent.context.bigquery_evidence
                                is not None
                                or
                                strategy_agent.context.join_evidence
                                is not None
                                or
                                strategy_agent.context.join_optimization
                                is not None
                                or
                                getattr(
                                    strategy_agent.context,
                                    "catalog_alternatives",
                                    None,
                                )
                                is not None
                            )
                        ),
                },
            )

            if required_user_input is not None:
                intent_store.save(
                    session_id,
                    "current",
                    required_user_input.to_dict(),
                )
            else:
                intent_store.delete(
                    (
                        session_id,
                        "current",
                    )
                )

            persisted_candidate = None
            if (bigquery_preaggregation_candidate and bigquery_preaggregation_candidate.get("generated")):
                persisted_candidate = bigquery_preaggregation_candidate
            elif (bigquery_join_candidate and bigquery_join_candidate.get("generated")):
                persisted_candidate = bigquery_join_candidate

            if persisted_candidate is not None:
                candidate_store.save(
                    session_id,
                    "current",
                    {
                        "rewrite_type": persisted_candidate.get("rewrite_type"),
                        "rule_codes": persisted_candidate.get("rule_codes", []),
                        "semantic_proof": persisted_candidate.get("semantic_proof"),
                        "quality_gate": persisted_candidate.get("quality_gate"),
                        "original_query": query,
                        "candidate_query": persisted_candidate.get("candidate_sql"),
                    },
                )
            else:
                candidate_store.delete(
                    (
                        session_id,
                        "current",
                    )
                )

        except Exception as exc:
            print(
                "[Redis] Workflow state persistence "
                "failed: "
                f"{type(exc).__name__}"
            )

        # =====================================================
        # 12. BUILD DETERMINISTIC RESULT
        # =====================================================

        deterministic_latency_ms = (
            time.perf_counter()
            -
            pipeline_start
        ) * 1000

        deterministic_response = {

            # -------------------------------------------------
            # SQL
            # -------------------------------------------------

            "session_id":
                session_id,

            "original_query":
                query,

            # -------------------------------------------------
            # LANGCHAIN STRATEGY PLAN
            # -------------------------------------------------

            "agent_strategy":
                (
                    agent_plan.model_dump()
                    if agent_plan is not None
                    else None
                ),

            "knowledge_catalog_alternatives":
                (
                    catalog_alternatives
                    if normalized_database_type == "bigquery"
                    else None
                ),

            "bigquery_preaggregation_semantic":
                (
                    bigquery_preaggregation_semantic
                    if normalized_database_type == "bigquery"
                    else None
                ),

            "bigquery_preaggregation_candidate":
                (
                    bigquery_preaggregation_candidate
                    if normalized_database_type == "bigquery"
                    else None
                ),

            "optimized_query":
                optimized_sql,

            "optimization_changed":
                changed,

            "optimization_status":
                optimization_status,

            "optimization_rules":
                rules_applied,

            "sql_normalized":
                normalized,

            "normalization_message":
                normalization_message,

            "required_user_input":
                (
                    required_user_input.to_dict()
                    if required_user_input
                    else None
                ),

            # -------------------------------------------------
            # GENERIC QUERY STRUCTURE
            # -------------------------------------------------

            "query_structure":
                _build_structure_payload(
                    structure
                ),

            # -------------------------------------------------
            # DATABASE-SPECIFIC ANALYSIS
            # -------------------------------------------------

            "database_analysis": {

                "database_type":
                    database_type,

                "findings":
                    database_findings,

                "error_findings":
                    error_findings,

                "warning_findings":
                    warning_findings,

                "informational_findings":
                    informational_findings,

                "safe_rewrite_available":
                    database_optimizer_result.get(
                        "safe_rewrite_available",
                        False,
                    ),

                "evidence_available":
                    database_optimizer_result.get(
                        "evidence_available",
                        False,
                    ),
            },

            # -------------------------------------------------
            # BIGQUERY EVIDENCE
            # -------------------------------------------------

            "bigquery_evidence":
                (
                    bigquery_evidence
                    if (
                        normalized_database_type
                        ==
                        "bigquery"
                    )
                    else None
                ),

            "bigquery_join_evidence":
                (
                    bigquery_join_evidence
                    if (
                        normalized_database_type
                        ==
                        "bigquery"
                    )
                    else None
                ),

            "bigquery_join_optimization":
                (
                    bigquery_join_optimization
                    if (
                        normalized_database_type
                        ==
                        "bigquery"
                    )
                    else None
                ),

            "bigquery_join_candidate":
                (
                    bigquery_join_candidate
                    if (
                        normalized_database_type
                        ==
                        "bigquery"
                    )
                    else None
                ),

            # -------------------------------------------------
            # EXISTING FRONTEND FIELDS
            # -------------------------------------------------

            "issues":
                issues,

            "information":
                information_messages,

            "recommendations":
                recommendations,

            "explanation":
                "",

            # -------------------------------------------------
            # METRICS
            # -------------------------------------------------

            "metrics": {

                "bytes_processed":
                    bytes_processed,

                "bytes_processed_formatted":
                    _format_bytes(
                        bytes_processed
                    ),

                "estimated_cost_usd":
                    (
                        round(
                            estimated_cost_usd,
                            6,
                        )

                        if (
                            estimated_cost_usd
                            is not None
                        )

                        else None
                    ),

                "analysis_latency_ms":
                    round(
                        deterministic_latency_ms,
                        2,
                    ),

                "scan_type":
                    scan_type,

                # ---------------------------------------------
                # PIPELINE TIMINGS
                # ---------------------------------------------

                "schema_retrieval_ms":
                    round(
                        schema_time_ms,
                        2,
                    ),

                "agent_strategy_ms":
                    round(
                        agent_strategy_time_ms,
                        2,
                    ),

                "structure_analysis_ms":
                    round(
                        structure_time_ms,
                        2,
                    ),

                "evidence_analysis_ms":
                    round(
                        evidence_time_ms,
                        2,
                    ),

                "join_evidence_analysis_ms":
                    round(
                        join_evidence_time_ms,
                        2,
                    ),

                "join_optimization_analysis_ms":
                    round(
                        join_optimization_time_ms,
                        2,
                    ),

                "join_candidate_generation_ms":
                    round(
                        join_candidate_generation_time_ms,
                        2,
                    ),

                "join_candidate_validation_ms":
                    round(
                        join_candidate_validation_time_ms,
                        2,
                    ),

                "database_analysis_ms":
                    round(
                        database_optimizer_time_ms,
                        2,
                    ),

                "issue_detection_ms":
                    round(
                        issue_time_ms,
                        2,
                    ),

                "optimization_ms":
                    round(
                        optimization_time_ms,
                        2,
                    ),

                "recommendation_ms":
                    round(
                        recommendation_time_ms,
                        2,
                    ),

                "execution_plan_ms":
                    round(
                        dry_run_time_ms,
                        2,
                    ),
            },
        }

        # =====================================================
        # 13. SEND DETERMINISTIC RESULT
        # =====================================================

        print(
            "[WebSocket] Sending deterministic "
            "result before Gemini..."
        )

        await websocket.send_json(
            {
                "type":
                    "result",

                "data":
                    deterministic_response,
            }
        )

        # =====================================================
        # 14. PREPARE GEMINI EXPLANATION
        # =====================================================

        print(
            "[WebSocket] Starting Gemini "
            "explanation stream..."
        )

        explanation_start = (
            time.perf_counter()
        )

        explanation = ""

        # -----------------------------------------------------
        # ISSUES
        # -----------------------------------------------------

        issues_text = (
            "\n".join(
                f"- {issue}"

                for issue
                in issues
            )

            if issues

            else (
                "- No issues detected."
            )
        )

        # -----------------------------------------------------
        # RULES
        # -----------------------------------------------------

        rules_text = (
            "\n".join(
                f"- {rule}"

                for rule
                in rules_applied
            )

            if rules_applied

            else (
                "- No performance-oriented deterministic SQL "
                "rewrite was applied."
            )
        )

        # -----------------------------------------------------
        # NORMALIZATION
        # -----------------------------------------------------

        normalization_text = (
            normalization_message
            if normalization_message
            else (
                "No SQL normalization was necessary."
            )
        )

        # -----------------------------------------------------
        # RECOMMENDATIONS
        # -----------------------------------------------------

        recommendations_text = (
            "\n".join(
                f"- {recommendation}"

                for recommendation
                in recommendations
            )

            if recommendations

            else (
                "- No additional recommendations."
            )
        )

        # -----------------------------------------------------
        # REQUIRED USER INPUT
        # -----------------------------------------------------

        required_user_input_text = (
            "None."
        )

        if required_user_input:

            required_payload = (
                required_user_input.to_dict()
            )

            required_user_input_text = (
                f"Type: {required_payload.get('type')}\n"
                f"Reason: {required_payload.get('reason')}\n"
                f"Table: {required_payload.get('table')}\n"
                f"Available columns: "
                f"{[column.get('name') for column in (required_payload.get('columns') or [])]}"
            )

        # -----------------------------------------------------
        # DATABASE FINDINGS
        # -----------------------------------------------------
        #
        # Keep warning/error findings distinct from informational
        # evidence in the Gemini prompt, mirroring the frontend.
        # This reduces the chance that the explanation labels an
        # informational observation as a confirmed issue.
        # -----------------------------------------------------

        database_issue_findings_text = (
            "\n".join(
                (
                    f"- [{finding.get('code', 'UNKNOWN')}] "
                    f"{finding.get('message', '')}"
                )

                for finding
                in database_issue_findings
            )

            if database_issue_findings

            else (
                "- No database-specific warning/error findings."
            )
        )

        database_information_findings_text = (
            "\n".join(
                (
                    f"- [{finding.get('code', 'UNKNOWN')}] "
                    f"{finding.get('message', '')}"
                )

                for finding
                in informational_findings
            )

            if informational_findings

            else (
                "- No database-specific informational findings."
            )
        )

        # -----------------------------------------------------
        # STRUCTURE SUMMARY
        # -----------------------------------------------------

        tables_text = (
            ", ".join(
                (
                    table.full_name
                    or
                    table.name
                )

                for table
                in structure.tables
            )

            if structure.tables

            else (
                "None"
            )
        )

        # -----------------------------------------------------
        # SELECT PROJECTION SUMMARY
        # -----------------------------------------------------

        select_expression_limit = 20

        selected_expressions = (
            structure.select_expressions[
                :select_expression_limit
            ]
        )

        select_expressions_text = (
            ", ".join(
                selected_expressions
            )

            if selected_expressions

            else "None"
        )

        if (
            len(
                structure.select_expressions
            )
            >
            select_expression_limit
        ):

            remaining = (
                len(
                    structure.select_expressions
                )
                -
                select_expression_limit
            )

            select_expressions_text += (
                f", ... and {remaining} more"
            )

        structure_summary = f"""
Tables referenced: {tables_text}
Join count: {structure.join_count}
CTE count: {structure.cte_count}
Subquery count: {structure.subquery_count}
SELECT projection wildcard: {structure.has_select_star}
Selected expressions: {select_expressions_text}
DISTINCT: {structure.has_distinct}
Aggregations: {structure.aggregations}
LIMIT: {structure.limit_value if structure.has_limit else "None"}
"""

        # -----------------------------------------------------
        # BIGQUERY TABLE EVIDENCE SUMMARY
        # -----------------------------------------------------

        bigquery_evidence_text = (
            "Not applicable."
        )

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_evidence
        ):

            evidence_lines = []

            for table in (
                bigquery_evidence.get(
                    "tables",
                    [],
                )
            ):

                table_size_text = (
                    _format_bytes(
                        table.get(
                            "size_bytes"
                        )
                    )
                )

                evidence_lines.append(
                    (
                        f"- {table.get('full_name')}: "
                        f"rows={table.get('row_count')}, "
                        f"logical_size={table_size_text}, "
                        f"partitioned={table.get('partitioned')}, "
                        f"partition_field={table.get('partition_field')}, "
                        f"clustering={table.get('clustering_fields')}"
                    )
                )

            bigquery_evidence_text = (
                "\n".join(
                    evidence_lines
                )

                if evidence_lines

                else (
                    "No BigQuery table metadata was available."
                )
            )

        # -----------------------------------------------------
        # BIGQUERY JOIN EVIDENCE SUMMARY
        # -----------------------------------------------------

        bigquery_join_evidence_text = (
            "Not applicable."
        )

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_join_evidence
        ):

            join_lines = []

            for edge in (
                bigquery_join_evidence.get(
                    "edges",
                    [],
                )
            ):

                right_source = (
                    edge.get(
                        "right_source"
                    )
                    or {}
                )

                right_metadata = (
                    right_source.get(
                        "metadata"
                    )
                    or {}
                )

                equality_pairs = (
                    edge.get(
                        "equality_pairs",
                        [],
                    )
                    or []
                )

                equality_text = []

                for pair in equality_pairs:

                    left = (
                        pair.get(
                            "left"
                        )
                        or {}
                    )

                    right = (
                        pair.get(
                            "right"
                        )
                        or {}
                    )

                    equality_text.append(
                        (
                            f"{left.get('sql')} = "
                            f"{right.get('sql')}"
                        )
                    )

                join_lines.append(
                    (
                        f"- Join {edge.get('join_index')} "
                        f"(scope {edge.get('scope_index')}): "
                        f"type={edge.get('join_type')}, "
                        f"left_aliases={edge.get('referenced_left_aliases')}, "
                        f"right_alias={right_source.get('alias')}, "
                        f"right_table={right_source.get('table')}, "
                        f"right_size={right_metadata.get('size_formatted')}, "
                        f"keys={equality_text}, "
                        f"filtered_aliases={edge.get('filtered_aliases')}, "
                        f"static_size_ratio={edge.get('static_size_ratio')}, "
                        f"risk={edge.get('risk_level')}"
                    )
                )

            bigquery_join_evidence_text = (
                "\n".join(
                    join_lines
                )
                if join_lines
                else (
                    "No joins detected."
                )
            )

        # -----------------------------------------------------
        # BIGQUERY JOIN OPTIMIZATION SUMMARY
        # -----------------------------------------------------

        bigquery_join_optimization_text = (
            "Not applicable."
        )

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_join_optimization
        ):

            optimization_lines = []

            for opportunity in (
                bigquery_join_optimization.get(
                    "opportunities",
                    [],
                )
            ):

                optimization_lines.append(
                    (
                        f"- [{opportunity.get('code')}] "
                        f"{opportunity.get('title')}: "
                        f"{opportunity.get('message')} "
                        f"Recommendation: "
                        f"{opportunity.get('recommendation')} "
                        f"Rewrite status: "
                        f"{opportunity.get('rewrite_status')}."
                    )
                )

            bigquery_join_optimization_text = (
                "\n".join(
                    optimization_lines
                )
                if optimization_lines
                else (
                    "No join optimization opportunities were emitted."
                )
            )

        # -----------------------------------------------------
        # BIGQUERY JOIN CANDIDATE SUMMARY
        # -----------------------------------------------------

        bigquery_join_candidate_text = (
            "Not applicable."
        )

        if (
            normalized_database_type
            ==
            "bigquery"
            and
            bigquery_join_candidate
        ):

            if (
                bigquery_join_candidate.get(
                    "generated"
                )
            ):

                candidate_gate = (
                    bigquery_join_candidate.get(
                        "quality_gate",
                        {},
                    )
                    or {}
                )

                semantic_proof = (
                    bigquery_join_candidate.get(
                        "semantic_proof",
                        {},
                    )
                    or {}
                )

                runtime_evidence = (
                    candidate_gate.get(
                        "runtime_evidence",
                        {},
                    )
                    or {}
                )

                original_gate = (
                    candidate_gate.get(
                        "original",
                        {},
                    )
                    or {}
                )

                candidate_gate_metrics = (
                    candidate_gate.get(
                        "candidate",
                        {},
                    )
                    or {}
                )

                bigquery_join_candidate_text = (
                    f"Candidate generated: yes\n"
                    f"Candidate SQL: "
                    f"{bigquery_join_candidate.get('candidate_sql')}\n"
                    f"Rewrite type: "
                    f"{bigquery_join_candidate.get('rewrite_type')}\n"
                    f"Semantic proof status: "
                    f"{semantic_proof.get('status')}\n"
                    f"Semantic proof basis: "
                    f"{semantic_proof.get('proof_basis', 'Unavailable')}\n"
                    f"Quality-gate decision: "
                    f"{candidate_gate.get('decision', 'NOT_RUN')}\n"
                    f"Scan-cost status: "
                    f"{candidate_gate.get('scan_cost_status', 'UNKNOWN')}\n"
                    f"Original dry-run bytes: "
                    f"{original_gate.get('bytes_processed')}\n"
                    f"Candidate dry-run bytes: "
                    f"{candidate_gate_metrics.get('bytes_processed')}\n"
                    f"Runtime status: "
                    f"{candidate_gate.get('runtime_status', 'NOT_RUN')}\n"
                    f"Runtime evidence status: "
                    f"{runtime_evidence.get('status', 'NOT_RUN')}\n"
                    f"Quality-gate reason: "
                    f"{candidate_gate.get('reason', 'Unavailable')}"
                )

            else:

                bigquery_join_candidate_text = (
                    "Candidate generated: no\n"
                    f"Reason: "
                    f"{bigquery_join_candidate.get('reason')}"
                )

        # -----------------------------------------------------
        # BIGQUERY PRE-AGGREGATION CANDIDATE SUMMARY
        # -----------------------------------------------------
        bigquery_preaggregation_candidate_text = "Not applicable."
        if normalized_database_type == "bigquery" and bigquery_preaggregation_candidate:
            preagg_gate = bigquery_preaggregation_candidate.get("quality_gate", {}) or {}
            preagg_proof = bigquery_preaggregation_candidate.get("semantic_proof", {}) or {}
            preagg_original = preagg_gate.get("original", {}) or {}
            preagg_candidate_metrics = preagg_gate.get("candidate", {}) or {}
            preagg_savings = preagg_gate.get("savings", {}) or {}
            bigquery_preaggregation_candidate_text = (
                f"Candidate generated: {bigquery_preaggregation_candidate.get('generated', False)}\n"
                f"Candidate SQL: {bigquery_preaggregation_candidate.get('candidate_sql')}\n"
                f"Semantic proof status: {preagg_proof.get('status')}\n"
                f"Semantic proof basis: {preagg_proof.get('proof_basis', 'Unavailable')}\n"
                f"Quality-gate decision: {preagg_gate.get('decision', 'NOT_RUN')}\n"
                f"Original dry-run bytes: {preagg_original.get('bytes_processed')}\n"
                f"Candidate dry-run bytes: {preagg_candidate_metrics.get('bytes_processed')}\n"
                f"Scan reduction percent: {preagg_savings.get('reduction_percent')}\n"
                f"Quality-gate reason: {preagg_gate.get('reason', 'Unavailable')}"
            )

        # -----------------------------------------------------
        # AGENT STRATEGY + KNOWLEDGE CATALOG ALTERNATIVES
        # -----------------------------------------------------

        agent_strategy_text = (
            _build_agent_strategy_text(
                agent_plan
            )
        )

        catalog_alternatives_text = (
            _build_catalog_alternatives_text(
                catalog_alternatives
            )
        )

        # -----------------------------------------------------
        # COST EVIDENCE
        # -----------------------------------------------------

        bytes_text = (
            _format_bytes(
                bytes_processed
            )
        )

        cost_text = (
            f"${estimated_cost_usd:.6f}"

            if (
                estimated_cost_usd
                is not None
            )

            else (
                "Unavailable"
            )
        )

        # =====================================================
        # DATABASE-AWARE EXPLANATION PROMPT
        # =====================================================

        explanation_prompt = f"""
You are the explanation layer of an enterprise SQL
optimization assistant.

Database:
{database_type}

The SQL analysis and deterministic optimization have already
been performed by database-aware backend components.

You MUST NOT generate, rewrite, or modify SQL.

Your job is only to explain the evidence already produced.

Original query:
{query}

Current backend SQL after approved rewrites (otherwise normalized/original):
{optimized_sql}

Optimization status:
{optimization_status}

Agent strategy:
{agent_strategy_text}

Knowledge Catalog alternative evidence:
{catalog_alternatives_text}

BigQuery pre-aggregation semantic proof / candidate / quality gate:
{bigquery_preaggregation_candidate_text}

SQL normalization:
{normalization_text}

Query structure:
{structure_summary}

Detected issues:
{issues_text}

Database-specific warning/error findings:
{database_issue_findings_text}

Database-specific informational evidence:
{database_information_findings_text}

BigQuery table metadata evidence:
{bigquery_evidence_text}

BigQuery join evidence:
{bigquery_join_evidence_text}

BigQuery join optimization opportunities:
{bigquery_join_optimization_text}

BigQuery join candidate / quality gate:
{bigquery_join_candidate_text}

Performance-oriented deterministic optimization rules applied:
{rules_text}

Recommendations:
{recommendations_text}

Required user input:
{required_user_input_text}

Measured database evidence:

Data processed:
{bytes_text}

Estimated cost:
{cost_text}

IMPORTANT RULES:

- Do not invent SQL.
- Do not invent filters.
- Do not invent columns.
- Do not invent indexes.
- Do not invent partitioning.
- Do not invent clustering.
- Do not invent execution-plan statistics.
- Do not invent performance numbers.

- The Data processed value above has already been converted
  to the correct binary unit by the backend.

- Repeat the provided Data processed value exactly if you
  mention it.

- Do NOT convert the byte quantity yourself.

- Do not switch between MB/MiB or GB/GiB.

- If the database is BigQuery, do not provide PostgreSQL-style
  index recommendations.

- If the database is PostgreSQL, do not describe BigQuery
  partition pruning, clustering, slots, or bytes-scanned billing.

- If a BigQuery table is marked partitioned=False, do NOT claim
  that the current query can benefit from partition pruning on
  that existing table.

- If a BigQuery table has clustering=[], do NOT claim that
  clustering is currently being used.

- Do not recommend a particular clustering key unless supplied
  deterministic evidence explicitly supports that recommendation.

- COUNT(*) is an aggregation and must NOT be described as a
  SELECT * projection issue.

- The field "SELECT projection wildcard" is authoritative.

- If SELECT projection wildcard is False, never say that the
  query uses SELECT *.

- If SELECT projection wildcard is False, never say that the
  query selects all columns.

- If SELECT projection wildcard is False and selected
  expressions are provided, describe them as specific selected
  columns or expressions.

- If SELECT projection wildcard is True, you may describe the
  query as projecting all columns for that wildcard scope.

- For BigQuery, the absence of a WHERE clause does NOT by itself
  prove that more bytes will be scanned.

- For an unpartitioned BigQuery table, never claim that adding
  an arbitrary WHERE clause will reduce bytes scanned unless
  explicit dry-run evidence demonstrates that reduction.

- A WHERE clause on an unpartitioned BigQuery table may still
  reduce returned rows or downstream computation, but that is
  different from reducing table scan bytes.

- Only claim partition pruning when deterministic metadata says
  the relevant table is partitioned and identifies the
  partition field.

- BigQuery join table sizes are static physical table metadata.

- Do NOT describe static physical table size as actual join input
  size after filtering.

- Do NOT claim actual shuffle bytes unless execution-stage evidence
  explicitly provides shuffle bytes.

- Do NOT claim that BigQuery chose broadcast join, hash join, or a
  particular physical join strategy unless execution evidence
  explicitly provides it.

- A large physical table participating in a join is evidence for
  investigation, not proof of an expensive intermediate result.

- A static size ratio is not a measured join cost.

- A static size ratio or static table-size skew does NOT prove an
  inefficient join, poor join plan, expensive shuffle, large
  intermediate result, or performance regression. Describe it only
  as static metadata worth reviewing.

- The join risk_level is a deterministic review-priority heuristic.
  It is NOT a measured runtime risk, cost, shuffle volume, or proof
  that the join is inefficient.

- A transformed join equality proves only that an expression such
  as CAST(), function application, or another transformation appears
  in the equality condition.

- Do NOT say that a transformed join key "prevents BigQuery from
  optimizing", "prevents efficient joins", "causes inefficient join
  processing", "causes shuffle", or "is slower" unless measured
  execution evidence explicitly proves that claim.

- Do NOT claim that removing CAST/functions from a join key will
  improve performance. Say only that the transformation should be
  reviewed for necessity and that any rewrite requires semantic proof
  plus measured validation.

- If a WHERE predicate references an alias, you may say that the
  query contains a predicate referencing that source. Do not claim
  the exact number of rows removed unless measured evidence exists.

- CROSS JOIN may be identified as a Cartesian join risk when the
  deterministic parser explicitly identifies CROSS JOIN.

- Never claim that changing join order will reduce BigQuery cost
  unless a validated candidate and measured comparison prove it.

- Join optimization opportunities marked RECOMMEND_ONLY,
  REQUIRES_USER_INTENT, or REQUIRES_SEMANTIC_PROOF are not
  executable rewrites and must not be described as applied
  optimizations.

- A generated join candidate is not an applied optimization unless
  its quality-gate decision is APPROVE_SCAN_COST or
  APPROVE_RUNTIME_IMPROVEMENT.

- If Semantic proof status is PROVEN_FOR_SUPPORTED_PATTERN, semantic
  proof for that narrowly supported rewrite has ALREADY been established
  by deterministic metadata/type checks. Do NOT say that semantic proof
  is still required. You may state that the proof is limited to the
  supported rewrite pattern and does not prove a performance benefit.

- If Scan-cost status is UNCHANGED, say exactly that BigQuery dry-run
  bytes / scan-cost evidence is unchanged. Do NOT describe this as proof
  that runtime performance is unchanged or that the candidate is worse.

- NEEDS_RUNTIME_VALIDATION means the candidate passed the supported
  semantic proof and BigQuery dry-run validity checks, scan bytes were
  unchanged, and controlled runtime evidence has not yet been collected.

- Only describe runtime / slot-time improvement when Runtime evidence
  status is IMPROVED. If runtime evidence is NOT_RUN, DISABLED, BLOCKED,
  INCONCLUSIVE, or NO_MEASURED_IMPROVEMENT, do not invent runtime claims.

- Do not call informational findings "issues", "problems",
  "inefficiencies", or "performance regressions" unless separate
  warning/error or measured evidence supports that classification.

- When discussing transformed keys or size skew, preserve the exact
  distinction between evidence, recommendation, and measured result.

- If optimization_status is NEEDS_USER_INTENT, explicitly state
  what user/application intent is required before a safe candidate
  can be produced.

- If optimization_status is NO_SAFE_REWRITE, explicitly state
  that no safe automatic rewrite was possible.

- If optimization_status is NORMALIZED_ONLY, explicitly state
  that SQL syntax/format was normalized but no performance
  optimization was applied.

- NORMALIZED_ONLY is not an optimization success.

- Do not call an unchanged or normalization-only SQL statement
  "optimized SQL".

- The current data-processed and cost values describe only the
  SQL actually analyzed by the database dry-run/execution-plan
  step.

- NO_OPTIMIZATION_NEEDED means no supported optimization opportunity
  requiring action was found. Do not frame it as a failure.

- RECOMMENDATIONS_ONLY means analysis found review opportunities but no
  validated executable rewrite is currently approved.

- If Agent strategy is PREAGGREGATION_REVIEW and Knowledge Catalog alternative
  evidence contains a candidate, explain that Catalog discovered the asset.
  Then use the separate pre-aggregation semantic-proof / quality-gate evidence
  to determine whether it stayed recommendation-only or became approved.

- A Knowledge Catalog alternative by itself is NOT proof of semantic
  equivalence. Only deterministic semantic proof with status
  PROVEN_FOR_SUPPORTED_PREAGGREGATION_PATTERN establishes compatibility for
  the narrowly supported pattern.

- If the pre-aggregation quality-gate decision is APPROVE_SCAN_COST or
  APPROVE_RUNTIME_IMPROVEMENT, describe the controlled substitution as an
  approved optimization and cite the measured dry-run reduction. Do NOT say
  that no automatic substitution was made.

- If semantic proof is NOT_PROVEN/ERROR or the candidate is not approved, keep
  the Catalog alternative recommendation-only.

- NEEDS_USER_INTENT means the optimizer requires user/application semantics
  before a safe candidate can be created.

- Recommendations are opportunities, not measured improvements.

- Only describe savings as measured when explicit original and
  candidate measurements are both provided.

Explain the result concisely and clearly.

Maximum 180 words.
"""

        # =====================================================
        # 15. STREAM GEMINI EXPLANATION
        # =====================================================

        print(
            "[TIMING][Gemini Explanation Prompt] "
            f"chars={len(explanation_prompt)}"
        )

        explanation_first_token_ms = None
        explanation_chunk_count = 0
        explanation_character_count = 0

        try:

            async for chunk in (
                llm_service.stream_explain(
                    explanation_prompt
                )
            ):

                if not chunk:

                    continue

                if explanation_first_token_ms is None:
                    explanation_first_token_ms = (
                        time.perf_counter() - explanation_start
                    ) * 1000.0
                    print(
                        "[TIMING][Gemini Explanation] "
                        f"first_token={explanation_first_token_ms:.2f} ms"
                    )

                explanation_chunk_count += 1
                explanation_character_count += len(chunk)

                explanation += (
                    chunk
                )

                await websocket.send_json(
                    {
                        "type":
                            "token",

                        "content":
                            chunk,
                    }
                )

        except Exception as exc:

            print(
                "[WebSocket] Gemini streaming "
                f"error: {exc}"
            )

            explanation = (
                "The deterministic analysis completed "
                "successfully, but Gemini was unavailable "
                "for the explanation."
            )

            await websocket.send_json(
                {
                    "type":
                        "token",

                    "content":
                        explanation,
                }
            )

        explanation_time_ms = (
            time.perf_counter()
            -
            explanation_start
        ) * 1000

        print(
            "[TIMING][Gemini Explanation Summary] "
            f"first_token={explanation_first_token_ms} ms "
            f"total={explanation_time_ms:.2f} ms "
            f"chunks={explanation_chunk_count} "
            f"chars={explanation_character_count}"
        )

        print(
            "[TIMING] Gemini explanation: "
            f"{explanation_time_ms:.2f} ms"
        )

        # =====================================================
        # 16. SEND COMPLETED EXPLANATION
        # =====================================================

        await websocket.send_json(
            {
                "type":
                    "explanation_complete",

                "content":
                    explanation,
            }
        )

        # =====================================================
        # 17. FINAL TIMING
        # =====================================================

        total_latency_ms = (
            time.perf_counter()
            -
            pipeline_start
        ) * 1000

        print(
            "[TIMING] Deterministic result ready: "
            f"{deterministic_latency_ms:.2f} ms"
        )

        print(
            "[TIMING] Total pipeline including Gemini: "
            f"{total_latency_ms:.2f} ms"
        )

        # =====================================================
        # 18. DONE
        # =====================================================

        await websocket.send_json(
            {
                "type":
                    "done"
            }
        )

        print(
            "[WebSocket] Analysis completed."
        )

    # =========================================================
    # CLIENT DISCONNECT
    # =========================================================

    except WebSocketDisconnect:

        print(
            "[WebSocket] Client disconnected"
        )

    # =========================================================
    # GENERAL ERROR
    # =========================================================

    except Exception as exc:

        print(
            f"[WebSocket] Error: {exc}"
        )

        import traceback

        traceback.print_exc()

        try:

            await websocket.send_json(
                {
                    "error":
                        str(
                            exc
                        )
                }
            )

        except Exception:

            pass

    # =========================================================
    # CLOSE CONNECTION
    # =========================================================

    finally:

        try:

            await websocket.close()

        except Exception:

            pass

        print(
            "[WebSocket] Connection closed"
        )