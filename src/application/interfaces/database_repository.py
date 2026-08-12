from abc import ABC, abstractmethod
from typing import List, Dict, Any
from domain.execution_plan import ExecutionPlan

class DatabaseRepository(ABC):
    @abstractmethod
    async def execute_query(self, sql: str, limit: int = 1000) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def get_execution_plan(self, sql: str) -> ExecutionPlan:
        pass