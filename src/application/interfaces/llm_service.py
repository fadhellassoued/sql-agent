from abc import ABC, abstractmethod
from typing import AsyncGenerator, List
from domain.execution_plan import ExecutionPlan

class LLMService(ABC):
    @abstractmethod
    async def explain(self, sql: str, plan: ExecutionPlan, issues: List[str]) -> str:
        pass

    @abstractmethod
    async def stream_explain(self, sql: str, plan: ExecutionPlan, issues: List[str]) -> AsyncGenerator[str, None]:
        pass