from application.interfaces.connection_manager import ConnectionManager
from application.interfaces.cache_repository import CacheRepository

class ConnectDatabaseUseCase:
    def __init__(self, conn_manager: ConnectionManager, cache: CacheRepository):
        self.conn_manager = conn_manager
        self.cache = cache

    async def execute(self, credentials: dict) -> dict:
        session_id = await self.conn_manager.connect(credentials)
        schema = await self.conn_manager.get_schema(session_id)
        # Cache schema for fast access
        await self.cache.set(f"schema:{session_id}", schema, ttl=3600)
        tables = [t.name for t in schema.tables]
        return {"session_id": session_id, "tables": tables}