from __future__ import annotations

import os
import time
from collections import defaultdict
from typing import Any

from langchain.agents import (
    AgentExecutor,
    create_tool_calling_agent,
)

from langchain_core.callbacks import (
    BaseCallbackHandler,
)

from langchain_core.prompts import (
    ChatPromptTemplate,
    MessagesPlaceholder,
)

from langchain_google_vertexai import (
    ChatVertexAI,
)

from .agent_models import (
    AgentOptimizationPlan,
)

from .strategy_tools import (
    AgentEvidenceContext,
    SQLAgentToolbox,
)



class _StrategyLatencyCallback(BaseCallbackHandler):
    """Request-scoped strategist timing callback."""

    def __init__(self):
        self.started_at = time.perf_counter()
        self.llm_starts = {}
        self.tool_starts = {}
        self.llm_rounds = []
        self.tool_durations = defaultdict(list)

    @staticmethod
    def _key(run_id):
        return str(run_id)

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.llm_starts[self._key(run_id)] = time.perf_counter()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.llm_starts[self._key(run_id)] = time.perf_counter()

    def on_llm_end(self, response, *, run_id, **kwargs):
        started = self.llm_starts.pop(self._key(run_id), None)
        if started is None:
            return
        duration_ms = (time.perf_counter() - started) * 1000.0
        self.llm_rounds.append(duration_ms)
        print(
            "[TIMING][Agent LLM] "
            f"round={len(self.llm_rounds)} "
            f"duration={duration_ms:.2f} ms"
        )

    def on_llm_error(self, error, *, run_id, **kwargs):
        self.llm_starts.pop(self._key(run_id), None)

    def on_tool_start(self, serialized, input_str, *, run_id, **kwargs):
        name = (serialized or {}).get("name") or "unknown_tool"
        self.tool_starts[self._key(run_id)] = (time.perf_counter(), str(name))

    def on_tool_end(self, output, *, run_id, **kwargs):
        started_info = self.tool_starts.pop(self._key(run_id), None)
        if started_info is None:
            return
        started, name = started_info
        duration_ms = (time.perf_counter() - started) * 1000.0
        self.tool_durations[name].append(duration_ms)
        print(
            "[TIMING][Agent Tool] "
            f"name={name} "
            f"duration={duration_ms:.2f} ms"
        )

    def on_tool_error(self, error, *, run_id, **kwargs):
        self.tool_starts.pop(self._key(run_id), None)

    def print_summary(self):
        llm_total_ms = sum(self.llm_rounds)
        tool_total_ms = sum(sum(v) for v in self.tool_durations.values())
        wall_ms = (time.perf_counter() - self.started_at) * 1000.0
        print(
            "[TIMING][Agent Summary] "
            f"llm_rounds={len(self.llm_rounds)} "
            f"llm_total={llm_total_ms:.2f} ms "
            f"tool_total={tool_total_ms:.2f} ms "
            f"wall={wall_ms:.2f} ms"
        )
        for name, values in self.tool_durations.items():
            print(
                "[TIMING][Agent Tool Summary] "
                f"name={name} calls={len(values)} "
                f"total={sum(values):.2f} ms"
            )


SYSTEM_PROMPT = """
You are the evidence-strategy layer of a SQL optimization agent.

Your job in this phase is ONLY to choose the minimum evidence tools needed.
Do not write executable SQL. Do not approve candidates.

BigQuery JOIN:
- Prefer analyze_bigquery_join_strategy_evidence.
- Do not repeat lower-level tools if the bundled tool already returned enough evidence.

SELECT *:
- If required output columns are unknown, use determine_user_intent_requirement.
- Never invent columns.
- Knowledge Catalog does not replace explicit user intent for required output columns.

Knowledge Catalog:
- BigQuery metadata is authoritative for physical facts such as schema, types,
  size, partitioning, clustering, and execution-related evidence.
- Knowledge Catalog is supplementary semantic, descriptive, and governance evidence.
- Use get_knowledge_catalog_context only when business meaning, table purpose,
  governance context, or semantic ambiguity could materially improve the strategy.

Knowledge Catalog alternative discovery:
- For a BigQuery query that contains GROUP BY or aggregate functions and references
  exactly one base table, call find_knowledge_catalog_alternatives unless one of these
  conditions clearly applies:
  1. the query requires row-level detail rather than aggregated analytical output;
  2. the query contains JOINs and JOIN analysis is the primary optimization path;
  3. the query contains SELECT * and explicit output-column intent must be resolved first;
  4. deterministic evidence already proves that pre-aggregation is irrelevant.
- Treat aggregation functions such as SUM, COUNT, AVG, MIN, and MAX as a signal for this rule.
- The purpose of find_knowledge_catalog_alternatives is to discover documented curated,
  summary, reporting, or pre-aggregated assets that may serve the analytical workload.
- If the tool finds no suitable asset, continue with the other evidence normally.
- find_knowledge_catalog_alternatives is discovery/recommendation only. A discovered asset
  is not proof of semantic equivalence and must never authorize automatic source-table
  substitution, executable candidate generation, or candidate approval.
- Do not call Knowledge Catalog for every query.
- Do not call it merely to detect SELECT *, obtain table size, inspect partitioning
  or clustering, estimate cost, or perform ordinary deterministic JOIN analysis.
- A missing description or a catalog entry with little semantic metadata is not an error.
- Catalog evidence alone must never authorize executable SQL changes or candidate approval.
- Never invent business definitions, relationships, certified assets, preferred tables,
  or alternative tables that are not present in retrieved evidence.

Do not call estimate_query_cost unless cost evidence is necessary.

After gathering enough evidence, return a brief natural-language summary of:
- what evidence was collected;
- what optimization direction appears relevant;
- any uncertainty.

The deterministic candidate builder, semantic proof, and quality gate remain authoritative.
"""



def _supported_join_opportunity_exists(
    context: AgentEvidenceContext,
) -> bool:
    result = (
        context.join_optimization
        or {}
    )

    opportunities = (
        result.get(
            "opportunities",
            [],
        )
        or []
    )

    supported_statuses = {
        "REQUIRES_SEMANTIC_PROOF",
        "READY_FOR_CANDIDATE",
    }

    return any(
        (
            opportunity.get(
                "rewrite_status"
            )
            in supported_statuses
        )
        for opportunity
        in opportunities
    )


def _enforce_required_tool_discipline(
    *,
    payload: dict,
    context: AgentEvidenceContext,
) -> dict:
    """
    Server-side safety enforcement.

    Prompt rules guide the LLM.
    This function makes the rule non-negotiable even if the model ignores
    the prompt.

    The strategist cannot request JOIN_REWRITE without deterministic
    opportunity evidence.
    """

    actual_tools = list(
        dict.fromkeys(
            context.tool_trace
        )
    )

    payload[
        "tools_used"
    ] = actual_tools

    wants_join_rewrite = (
        payload.get(
            "strategy"
        )
        ==
        "JOIN_REWRITE"
        or
        bool(
            payload.get(
                "candidate_generation_requested"
            )
        )
    )

    if not wants_join_rewrite:
        return payload

    allowed_evidence_tool_used = any(
        tool_name
        in {
            "analyze_bigquery_join_strategy_evidence",
            "get_join_optimization_opportunities",
        }
        for tool_name
        in actual_tools
    )

    supported_opportunity = (
        _supported_join_opportunity_exists(
            context
        )
    )

    if (
        allowed_evidence_tool_used
        and
        supported_opportunity
    ):
        # JOIN_REWRITE means: ask the controlled candidate builder
        # to attempt the supported rewrite. This is NOT approval.
        # Semantic proof and the quality gate remain authoritative.
        if (
            payload.get(
                "strategy"
            )
            ==
            "JOIN_REWRITE"
        ):
            payload[
                "candidate_generation_requested"
            ] = True

        return payload

    # Fail closed: keep useful reasoning, but do not allow the LLM to request
    # deterministic candidate generation without the required evidence.
    payload[
        "strategy"
    ] = "GENERAL_REVIEW"

    payload[
        "candidate_generation_requested"
    ] = False

    payload[
        "confidence"
    ] = "low"

    limitations = list(
        payload.get(
            "limitations",
            [],
        )
        or []
    )

    limitations.append(
        (
            "JOIN_REWRITE was downgraded because deterministic join "
            "optimization opportunity evidence was not collected."
        )
    )

    payload[
        "limitations"
    ] = limitations

    return payload


def _actual_tool_trace(context: AgentEvidenceContext) -> list[str]:
    return list(dict.fromkeys(context.tool_trace))

def _evidence_refs_from_context(context: AgentEvidenceContext) -> list[str]:
    refs = []
    if context.structure is not None:
        refs.append("query_structure")
    if context.bigquery_evidence is not None:
        refs.append("bigquery_metadata")
    if context.join_evidence is not None:
        refs.append("bigquery_join_evidence")
    if context.join_optimization is not None:
        refs.append("join_optimization_opportunities")
    if context.user_intent is not None:
        refs.append("user_intent_requirement")
    if getattr(context, "catalog_evidence", None) is not None:
        refs.append("knowledge_catalog_context")
    if getattr(context, "catalog_alternatives", None) is not None:
        refs.append("knowledge_catalog_alternatives")
    if context.cost_estimate is not None:
        refs.append("bigquery_dry_run_cost")
    return refs

def _build_join_plan(context: AgentEvidenceContext) -> dict | None:
    result = context.join_optimization or {}
    opportunities = result.get("opportunities", []) or []
    if not opportunities: return None
    proposed_changes = []
    supported_candidate = False
    requires_user_intent = False
    for opportunity in opportunities:
        code = opportunity.get("code") or "BQ_JOIN_REVIEW"
        title = opportunity.get("title") or "Join optimization review"
        rewrite_status = opportunity.get("rewrite_status") or "RECOMMEND_ONLY"
        candidate_kind = opportunity.get("candidate_kind")
        req_sem = rewrite_status == "REQUIRES_SEMANTIC_PROOF"
        req_intent = rewrite_status == "REQUIRES_USER_INTENT"
        requires_user_intent = requires_user_intent or req_intent
        if rewrite_status in {"REQUIRES_SEMANTIC_PROOF", "READY_FOR_CANDIDATE"}: supported_candidate = True
        proposed_changes.append({"code":code,"title":title,"rationale":f"Deterministic join analysis identified {code} with rewrite status {rewrite_status}.","candidate_kind":candidate_kind,"requires_user_intent":req_intent,"requires_semantic_proof":req_sem})
    if supported_candidate:
        return {"goal":"Improve the JOIN using a supported deterministic rewrite path.","strategy":"JOIN_REWRITE","rationale":"The agent requested JOIN evidence and deterministic join analysis found a supported optimization opportunity. Candidate construction must still pass semantic proof and the quality gate.","proposed_changes":proposed_changes,"requires_user_intent":requires_user_intent,"requested_user_input":"Additional user intent is required before a safe candidate can be constructed." if requires_user_intent else None,"candidate_generation_requested":True,"confidence":"high","limitations":["The strategy does not approve executable SQL. Candidate construction, semantic proof, dry-run validation, and runtime validation remain separate."]}
    return {"goal":"Review the JOIN using deterministic evidence.","strategy":"GENERAL_REVIEW","rationale":"JOIN evidence was collected, but deterministic join analysis did not expose a supported candidate path.","proposed_changes":proposed_changes,"requires_user_intent":requires_user_intent,"requested_user_input":"Additional user intent is required for this JOIN review." if requires_user_intent else None,"candidate_generation_requested":False,"confidence":"medium","limitations":["No deterministic supported JOIN rewrite is currently available."]}

def _build_user_intent_plan(context: AgentEvidenceContext) -> dict | None:
    required = context.user_intent
    if required is None: return None
    try: payload = required.to_dict()
    except Exception: payload = {}
    input_type = payload.get("type") or "USER_INTENT_REQUIRED"
    reason = payload.get("reason") or "A safe rewrite requires explicit user intent."
    strategy = "COLUMN_PRUNING" if input_type in {"SELECT_STAR_COLUMNS","REQUIRED_COLUMNS"} or "column" in input_type.lower() else "GENERAL_REVIEW"
    return {"goal":"Collect the missing user intent required for a safe optimization.","strategy":strategy,"rationale":reason,"proposed_changes":[{"code":"USER_INTENT_REQUIRED","title":"User intent required","rationale":reason,"candidate_kind":None,"requires_user_intent":True,"requires_semantic_proof":False}],"requires_user_intent":True,"requested_user_input":reason,"candidate_generation_requested":False,"confidence":"high","limitations":["The optimizer will not invent the user's required output."]}

def _build_catalog_preaggregation_plan(
    context: AgentEvidenceContext,
) -> dict | None:
    """
    Build a recommendation-only pre-aggregation plan from verified
    Knowledge Catalog alternative evidence.

    Safety:
    - the LLM must have actually selected the discovery tool;
    - at least one server-side candidate must exist;
    - no executable SQL candidate is requested;
    - no semantic equivalence is asserted;
    - no automatic source-table substitution is allowed.
    """

    actual_tools = _actual_tool_trace(
        context
    )

    if (
        "find_knowledge_catalog_alternatives"
        not in actual_tools
    ):
        return None

    evidence = (
        getattr(
            context,
            "catalog_alternatives",
            None,
        )
        or {}
    )

    candidates = (
        evidence.get(
            "candidates",
            [],
        )
        or []
    )

    if not candidates:
        return None

    # Discovery is already ranked server-side by the catalog client.
    # Keep the strategist recommendation-only and expose only evidence
    # that was actually retrieved.
    proposed_changes = []

    for candidate in candidates[:3]:
        candidate_fqn = (
            candidate.get(
                "fully_qualified_name"
            )
            or candidate.get(
                "display_name"
            )
            or "catalog asset"
        )

        description = (
            candidate.get(
                "description"
            )
            or ""
        ).strip()

        references_source = bool(
            candidate.get(
                "references_source",
                False,
            )
        )

        relevance_score = (
            candidate.get(
                "relevance_score"
            )
        )

        matched_keywords = (
            candidate.get(
                "matched_keywords"
            )
            or []
        )

        rationale_parts = [
            (
                "Knowledge Catalog discovered "
                f"{candidate_fqn} as a documented curated/"
                "pre-aggregated candidate."
            )
        ]

        if references_source:
            rationale_parts.append(
                "Its catalog metadata explicitly references the "
                "query's source table."
            )

        if matched_keywords:
            rationale_parts.append(
                "Catalog signals: "
                + ", ".join(
                    str(keyword)
                    for keyword
                    in matched_keywords
                )
                + "."
            )

        if relevance_score is not None:
            rationale_parts.append(
                "Server-side catalog relevance score: "
                f"{relevance_score}."
            )

        if description:
            # Keep the plan compact; full evidence remains in context.
            compact_description = (
                description
                if len(description) <= 240
                else description[:237] + "..."
            )

            rationale_parts.append(
                "Catalog description: "
                f"{compact_description}"
            )

        proposed_changes.append(
            {
                "code":
                    "BQ_CATALOG_PREAGGREGATION_CANDIDATE",

                "title":
                    (
                        "Evaluate curated/pre-aggregated asset: "
                        f"{candidate_fqn}"
                    ),

                "rationale":
                    " ".join(
                        rationale_parts
                    ),

                "candidate_kind":
                    None,

                "requires_user_intent":
                    False,

                "requires_semantic_proof":
                    True,
            }
        )

    return {
        "goal":
            (
                "Evaluate whether a documented curated or pre-aggregated "
                "BigQuery asset can serve this analytical workload."
            ),

        "strategy":
            "PREAGGREGATION_REVIEW",

        "rationale":
            (
                "The agent explicitly requested Knowledge Catalog alternative "
                "discovery and the server found one or more documented candidate "
                "assets. These are recommendation candidates only; equivalence "
                "has not been proven."
            ),

        "proposed_changes":
            proposed_changes,

        "requires_user_intent":
            False,

        "requested_user_input":
            None,

        "candidate_generation_requested":
            False,

        "confidence":
            (
                "high"
                if any(
                    bool(
                        candidate.get(
                            "references_source",
                            False,
                        )
                    )
                    for candidate
                    in candidates
                )
                else "medium"
            ),

        "limitations": [
            (
                "Knowledge Catalog discovery does not prove that the alternative "
                "asset preserves the query's exact semantics."
            ),
            (
                "No automatic source-table replacement or executable SQL "
                "candidate is authorized by this strategy."
            ),
            (
                "A future semantic compatibility check should compare grain, "
                "dimensions, measures, filters, and freshness before any "
                "substitution is considered."
            ),
        ],
    }


def _build_structure_plan(context: AgentEvidenceContext) -> dict:
    structure=context.structure
    if structure is not None and bool(getattr(structure,"has_select_star",False)):
        return {"goal":"Review projection width and required output columns.","strategy":"COLUMN_PRUNING","rationale":"The query contains SELECT *, but no explicit user-intent requirement was established.","proposed_changes":[],"requires_user_intent":True,"requested_user_input":"Specify which output columns are required.","candidate_generation_requested":False,"confidence":"medium","limitations":["No column-pruning candidate should be created until the required output columns are known."]}
    return {"goal":"Determine whether the query needs a supported optimization.","strategy":"NO_CHANGE","rationale":"The evidence selected by the agent does not currently show a supported rewrite requiring candidate generation.","proposed_changes":[],"requires_user_intent":False,"requested_user_input":None,"candidate_generation_requested":False,"confidence":"medium","limitations":["Only evidence gathered by the agent's selected tools is considered in this strategy plan."]}

def _assemble_plan_from_agent_evidence(
    context: AgentEvidenceContext,
) -> AgentOptimizationPlan:
    plan = (
        _build_user_intent_plan(
            context
        )
        or _build_join_plan(
            context
        )
        or _build_catalog_preaggregation_plan(
            context
        )
        or _build_structure_plan(
            context
        )
    )

    plan["tools_used"] = (
        _actual_tool_trace(
            context
        )
    )

    plan["evidence_refs"] = (
        _evidence_refs_from_context(
            context
        )
    )

    plan = (
        _enforce_required_tool_discipline(
            payload=plan,
            context=context,
        )
    )

    return (
        AgentOptimizationPlan.model_validate(
            plan
        )
    )

class LangChainStrategyAgent:
    """
    Agentic strategist.

    The LLM owns:
    - tool selection;
    - evidence strategy;
    - optimization-strategy reasoning.

    Deterministic code owns:
    - executable candidate construction;
    - semantic proof;
    - dry-run validation;
    - runtime validation;
    - final approval/rejection.
    """

    def __init__(
        self,
        *,
        database_type: str,
        conn_obj: dict,
        db_repo: Any,
        memory=None,
    ):
        self.context = (
            AgentEvidenceContext(
                database_type=
                    database_type,

                conn_obj=
                    conn_obj,

                db_repo=
                    db_repo,
            )
        )

        self.toolbox = (
            SQLAgentToolbox(
                self.context
            )
        )

        model_name = os.getenv(
            "SQL_AGENT_STRATEGY_MODEL",
            "gemini-2.5-flash",
        )

        project = (
            os.getenv(
                "VERTEX_PROJECT_ID"
            )
            or
            conn_obj.get(
                "project_id"
            )
        )

        location = os.getenv(
            "VERTEX_LOCATION",
            "us-central1",
        )

        llm = (
            ChatVertexAI(
                model=
                    model_name,

                project=
                    project,

                location=
                    location,

                temperature=
                    0,

                max_output_tokens=
                    500,

                max_retries=
                    2,
            )
        )

        prompt = (
            ChatPromptTemplate
            .from_messages(
                [
                    (
                        "system",
                        SYSTEM_PROMPT,
                    ),

                    (
                        "human",
                        "{input}",
                    ),

                    MessagesPlaceholder(
                        variable_name=
                            "agent_scratchpad",
                    ),
                ]
            )
        )

        tools = (
            self.toolbox.tools()
        )

        agent = (
            create_tool_calling_agent(
                llm,
                tools,
                prompt,
            )
        )

        self.executor = (
            AgentExecutor(
                agent=
                    agent,

                tools=
                    tools,

                # Query-level strategist is intentionally stateless for
                # latency. Redis/session state remains available to the
                # surrounding application and deterministic intent flow.
                memory=
                    None,

                verbose=
                    False,

                # Lower than before because the bundled JOIN tool should
                # normally need only one evidence call.
                max_iterations=
                    3,

                handle_parsing_errors=
                    True,

                return_intermediate_steps=
                    True,
            )
        )

    async def plan(
        self,
        sql: str,
    ) -> AgentOptimizationPlan:
        """LLM chooses evidence tools; server assembles the constrained plan."""

        latency_callback = _StrategyLatencyCallback()
        executor_started = time.perf_counter()

        await self.executor.ainvoke(
            {
                "input": (
                    "Gather the minimum evidence needed for an optimization strategy.\nSQL:\n"
                    f"{sql}"
                )
            },
            config={
                "callbacks": [latency_callback]
            },
        )

        executor_time_ms = (
            time.perf_counter() - executor_started
        ) * 1000.0

        print(
            "[TIMING][Agent Executor] "
            f"{executor_time_ms:.2f} ms"
        )
        latency_callback.print_summary()

        print("[Agent] Evidence gathering complete")
        print(
            "[Agent] Assembling constrained strategy plan "
            "from collected evidence..."
        )

        assemble_started = time.perf_counter()
        plan = _assemble_plan_from_agent_evidence(self.context)
        assemble_time_ms = (
            time.perf_counter() - assemble_started
        ) * 1000.0

        print(
            "[TIMING][Agent Plan Assembly] "
            f"{assemble_time_ms:.2f} ms"
        )
        print("[Agent] Constrained strategy plan complete")
        return plan
