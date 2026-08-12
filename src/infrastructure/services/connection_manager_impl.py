import uuid
import json
from google.oauth2 import service_account
from typing import Any, Dict
from google.cloud import bigquery
from google.oauth2 import service_account
from sqlalchemy import create_engine
from application.interfaces.connection_manager import ConnectionManager
from application.interfaces.schema_discovery_service import SchemaDiscoveryService
from infrastructure.services.schema_discovery_bigquery import BigQuerySchemaDiscovery
from infrastructure.services.schema_discovery_postgres import PostgresSchemaDiscovery
from infrastructure.repositories.bigquery_repository import BigQueryRepository
from infrastructure.repositories.postgres_repository import PostgresRepository
from domain.database_schema import DatabaseSchema
from src.config import settings
from langchain.memory import ConversationBufferMemory

class ConnectionManagerImpl(ConnectionManager):
    def __init__(self, cache_repo):
        self.sessions: Dict[str, dict] = {}
        self.cache_repo = cache_repo

    async def connect(self, credentials: dict) -> str:
        session_id = str(uuid.uuid4())
        print(f"[ConnectionManager] Creating session: {session_id}")
        db_type = credentials.get("db_type")
        
        if db_type == "bigquery":
            project_id = credentials["project_id"]
            dataset = credentials.get("dataset", settings.BIGQUERY_DATASET)
            
            # Create BigQuery client
            if "credentials_json" in credentials:
                creds = service_account.Credentials.from_service_account_info(
                    eval(credentials["credentials_json"])
                )
                client = bigquery.Client(project=project_id, credentials=creds)
            else:
                client = bigquery.Client(project=project_id)
            
            repo = BigQueryRepository(client, dataset)
            
            # Try Knowledge Catalog (Dataplex) first, with fallback
            discovery = None
            schema = None
            
            try:
                from infrastructure.services.knowledge_catalog import KnowledgeCatalogService
                discovery = KnowledgeCatalogService(
                    project_id=project_id,
                    location="us-central1",           # Same as your Dataplex lake
                    lake="sql-optimizer-lake",
                    zone="bigquery-zone",
                    dataset=dataset
                )
                print(f"[ConnectionManager] Using Knowledge Catalog for dataset: {dataset}")
                
                # Attempt schema discovery via Knowledge Catalog
                schema = await discovery.discover_schema()
                print("[ConnectionManager] Schema discovery via Knowledge Catalog succeeded.")
                
            except Exception as e:
                print(f"[ConnectionManager] Knowledge Catalog failed: {e}. Falling back to INFORMATION_SCHEMA.")
                # Fallback to INFORMATION_SCHEMA
                discovery = BigQuerySchemaDiscovery(client, dataset)
                schema = await discovery.discover_schema()
                print("[ConnectionManager] Schema discovery via INFORMATION_SCHEMA succeeded.")
            
            connection_obj = {
                "type": "bigquery",
                "client": client,
                "repo": repo,
                "discovery": discovery,
                "dataset": dataset
            }
            
        elif db_type == "postgresql":
            url = f"postgresql://{credentials['username']}:{credentials['password']}@{credentials['host']}:{credentials['port']}/{credentials['database']}"
            engine = create_engine(url)
            repo = PostgresRepository(engine)
            discovery = PostgresSchemaDiscovery(engine)
            connection_obj = {
                "type": "postgresql",
                "engine": engine,
                "repo": repo,
                "discovery": discovery
            }
            # Discover schema for PostgreSQL
            schema = await discovery.discover_schema()
            
        else:
            raise ValueError("Unsupported database type")

        # Store the session with the discovered schema
        self.sessions[session_id] = {
            "connection": connection_obj,
            "schema": schema
        }
        
        print(f"[ConnectionManager] Session stored: {session_id}")
        print(f"[ConnectionManager] Active sessions: {list(self.sessions.keys())}")
        return session_id

    async def get_connection(self, session_id: str) -> Any:
        print(f"[ConnectionManager] Looking for session: {session_id}")
        print(f"[ConnectionManager] Available sessions: {list(self.sessions.keys())}")
        
        session = self.sessions.get(session_id)
        if not session:
            print(f"[ConnectionManager] Session not found: {session_id}")
            raise ValueError("Session not found")
        
        print(f"[ConnectionManager] Session found: {session_id}")
        return session["connection"]

    async def get_schema(self, session_id: str) -> DatabaseSchema:
        # Check cache first
        cached = await self.cache_repo.get(f"schema:{session_id}")
        if cached:
            return cached
        
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError("Session not found")
        return session["schema"]
    
    async def init_memory(self, session_id: str):
        """Initialize a memory object for the session."""
        if session_id not in self.sessions:
            raise ValueError("Session not found")
        memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
        self.sessions[session_id]["memory"] = memory
        return memory
    
    async def get_memory(self, session_id: str):
        """Get the memory object for a session."""
        session = self.sessions.get(session_id)
        if not session:
            raise ValueError("Session not found")
        if "memory" not in session:
            # Initialize if not exists
            memory = ConversationBufferMemory(memory_key="chat_history", return_messages=True)
            session["memory"] = memory
            return memory
        return session["memory"]
    