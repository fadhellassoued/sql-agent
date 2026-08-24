from typing import AsyncGenerator, List

from domain.execution_plan import ExecutionPlan

from application.interfaces.llm_service import LLMService

from langchain_google_vertexai import ChatVertexAI
from langchain.schema import HumanMessage

from src.config import settings


class GeminiLLMService(LLMService):
    """
    Gemini service used as the AI explanation layer.

    Gemini does not generate or rewrite the optimized SQL.

    Deterministic SQL analysis:
        SQLGlot + rules + database metadata

    Cost/performance evidence:
        BigQuery

    Natural-language reasoning:
        Gemini / Vertex AI
    """

    def __init__(self):

        self.llm = ChatVertexAI(
            model="gemini-2.5-flash",
            project=settings.GCP_PROJECT_ID,
            location=settings.GCP_REGION,
            streaming=True,
            temperature=0,
        )

    # =============================================================
    # NON-STREAMING EXPLANATION
    # =============================================================

    async def explain(
        self,
        sql: str,
        plan: ExecutionPlan,
        issues: List[str],
    ) -> str:

        prompt = self._build_prompt(
            sql=sql,
            plan=plan,
            issues=issues,
        )

        response = await self.llm.ainvoke(
            [
                HumanMessage(
                    content=prompt
                )
            ]
        )

        return response.content

    # =============================================================
    # STREAMING EXPLANATION
    # =============================================================

    async def stream_explain(
        self,
        prompt: str,
    ) -> AsyncGenerator[str, None]:

        async for chunk in self.llm.astream(
            [
                HumanMessage(
                    content=prompt
                )
            ]
        ):

            if not chunk:
                continue

            content = chunk.content

            if content:
                yield content

    # =============================================================
    # DEFAULT EXPLANATION PROMPT
    # =============================================================

    def _build_prompt(
        self,
        sql: str,
        plan: ExecutionPlan,
        issues: List[str],
    ) -> str:

        issues_text = (
            "\n".join(
                f"- {issue}"
                for issue in issues
            )
            if issues
            else "- No major issues detected."
        )

        bytes_text = (
            str(plan.total_bytes_processed)
            if plan.total_bytes_processed is not None
            else "Unavailable"
        )

        cost_text = (
            f"${plan.cost:.6f}"
            if plan.cost is not None
            else "Unavailable"
        )

        return f"""
You are the explanation layer of a SQL optimization system.

The SQL itself was analyzed and transformed by a deterministic
SQL optimization engine.

IMPORTANT:

- Do NOT rewrite SQL.
- Do NOT invent SQL.
- Do NOT modify the SQL provided below.
- Do NOT claim an optimization occurred unless it is explicitly
  supported by the evidence.
- Do NOT claim that a recommendation already reduced cost unless
  an before/after measurement is provided.
- Clearly distinguish detected issues from recommendations.

Candidate SQL:
{sql}

Detected issues:
{issues_text}

Execution evidence:
- Bytes processed: {bytes_text}
- Estimated cost: {cost_text}
- Scan type: {plan.scan_type}

Explain:
1. The detected issues.
2. Why they may matter.
3. Whether an optimization was actually applied.
4. What the user should consider next.

Maximum 150 words.
"""