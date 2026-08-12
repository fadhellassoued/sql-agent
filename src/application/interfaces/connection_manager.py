from abc import ABC, abstractmethod
from typing import Any
from domain.database_schema import DatabaseSchema

class ConnectionManager(ABC):
    @abstractmethod
    async def connect(self, credentials: dict) -> str:  # returns session_id
        pass

    @abstractmethod
    async def get_connection(self, session_id: str) -> Any:
        pass

    @abstractmethod
    async def get_schema(self, session_id: str) -> DatabaseSchema:
        pass