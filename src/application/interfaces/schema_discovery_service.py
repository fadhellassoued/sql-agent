from abc import ABC, abstractmethod
from domain.database_schema import DatabaseSchema

class SchemaDiscoveryService(ABC):
    @abstractmethod
    async def discover_schema(self, connection) -> DatabaseSchema:
        pass