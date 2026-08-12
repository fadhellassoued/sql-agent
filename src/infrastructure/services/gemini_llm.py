from typing import AsyncGenerator, List
from domain.execution_plan import ExecutionPlan
from application.interfaces.llm_service import LLMService
from langchain_google_vertexai import ChatVertexAI
from langchain.schema import HumanMessage
from src.config import settings

class GeminiLLMService(LLMService):
    def __init__(self):
        self.llm = ChatVertexAI(
            model="gemini-2.5-flash",          # or "gemini-1.5-pro"
            project=settings.GCP_PROJECT_ID,
            location=settings.GCP_REGION,      # e.g., "us-central1"
            streaming=True,
            temperature=0
        )

    async def explain(self, sql: str, plan: ExecutionPlan, issues: List[str]) -> str:
        prompt = self._build_prompt(sql, plan, issues)
        response = await self.llm.agenerate([[HumanMessage(content=prompt)]])
        return response.generations[0][0].text

    async def stream_explain(self, prompt: str) -> AsyncGenerator[str, None]:
        """Stream explanation from Gemini."""
        async for chunk in self.llm.astream([HumanMessage(content=prompt)]):
            yield chunk.content

    def _build_prompt(self, sql: str, plan: ExecutionPlan, issues: List[str]) -> str:
        return f"""
        You are a SQL performance expert.
        The user query is: {sql}
        Detected issues: {', '.join(issues)}
        Execution plan: bytes processed {plan.total_bytes_processed}, scan type {plan.scan_type}

        Explain why it's slow and how to improve it.
        Provide actionable advice (max 150 words).
        """