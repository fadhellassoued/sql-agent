from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict

from google.cloud import bigquery
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from application.interfaces.connection_manager import (
    ConnectionManager,
)

from infrastructure.services.schema_discovery_bigquery import (
    BigQuerySchemaDiscovery,
)

from infrastructure.services.schema_discovery_postgres import (
    PostgresSchemaDiscovery,
)

from infrastructure.repositories.bigquery_repository import (
    BigQueryRepository,
)

from infrastructure.repositories.postgres_repository import (
    PostgresRepository,
)

from domain.database_schema import DatabaseSchema

from infrastructure.memory.langchain_redis_memory import (
    RedisConversationMemory,
)


class ConnectionManagerImpl(ConnectionManager):
    """
    Phase-1 Redis connection manager.

    Live BigQuery clients / SQLAlchemy engines remain process-local.
    Redis stores only serializable session metadata and conversation
    memory. Raw OAuth tokens and passwords are intentionally excluded.
    """

    def __init__(
        self,
        cache_repo,
        *,
        session_store=None,
        conversation_store=None,
    ):
        self.sessions: Dict[str, dict] = {}
        self.cache_repo = cache_repo
        self.session_store = session_store
        self.conversation_store = (
            conversation_store
        )

    async def connect(
        self,
        credentials: dict,
    ) -> str:
        session_id = str(
            uuid.uuid4()
        )

        print(
            "[ConnectionManager] Creating session"
        )

        db_type = (
            credentials.get(
                "db_type"
            )
            or
            "bigquery"
        ).lower()

        if db_type == "bigquery":
            project_id = credentials.get(
                "project_id"
            )

            dataset = credentials.get(
                "dataset"
            )

            user_credentials = credentials.get(
                "gcp_credentials"
            )

            if not project_id:
                raise ValueError(
                    "GCP project_id is required."
                )

            if not dataset:
                raise ValueError(
                    "BigQuery dataset is required."
                )

            if user_credentials is None:
                raise ValueError(
                    "Connected Google OAuth credentials "
                    "are required."
                )

            project_id = (
                project_id.strip()
            )

            dataset = (
                dataset.strip()
            )

            print(
                "[ConnectionManager] Creating "
                "BigQuery client using the connected "
                "user's OAuth credentials."
            )

            try:
                client = bigquery.Client(
                    project=project_id,
                    credentials=user_credentials,
                )

            except Exception as exc:
                raise ValueError(
                    "Unable to create BigQuery client "
                    f"for the selected project: {exc}"
                ) from exc

            full_dataset_id = (
                f"{project_id}.{dataset}"
            )

            try:
                client.get_dataset(
                    full_dataset_id
                )

            except Exception as exc:
                raise ValueError(
                    "Unable to access the selected "
                    "BigQuery dataset. "
                    f"Details: {exc}"
                ) from exc

            print(
                "[ConnectionManager] User-authorized "
                "access confirmed: "
                f"{full_dataset_id}"
            )

            repo = BigQueryRepository(
                client,
                dataset,
            )

            discovery = BigQuerySchemaDiscovery(
                client,
                dataset,
            )

            schema = (
                await discovery.discover_schema()
            )

            connection_obj = {
                "type":
                    "bigquery",

                "client":
                    client,

                "repo":
                    repo,

                "discovery":
                    discovery,

                "project_id":
                    project_id,

                "dataset":
                    dataset,

                "user_id":
                    credentials.get(
                        "authenticated_user_id"
                    ),

                "user_email":
                    credentials.get(
                        "authenticated_user_email"
                    ),
            }

            redis_metadata = {
                "type":
                    "bigquery",

                "project_id":
                    project_id,

                "dataset":
                    dataset,

                "user_id":
                    credentials.get(
                        "authenticated_user_id"
                    ),

                "created_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            }

        elif db_type == "postgresql":
            required = [
                "host",
                "port",
                "database",
                "username",
                "password",
            ]

            missing = [
                field
                for field in required
                if not credentials.get(
                    field
                )
            ]

            if missing:
                raise ValueError(
                    "Missing PostgreSQL fields: "
                    f"{', '.join(missing)}"
                )

            connection_url = URL.create(
                drivername=
                    "postgresql+psycopg2",

                username=
                    credentials["username"],

                password=
                    credentials["password"],

                host=
                    credentials["host"],

                port=
                    int(
                        credentials["port"]
                    ),

                database=
                    credentials["database"],
            )

            try:
                engine = create_engine(
                    connection_url,
                    pool_pre_ping=True,
                )

                with engine.connect() as connection:
                    connection.execute(
                        text(
                            "SELECT 1"
                        )
                    )

            except Exception as exc:
                raise ValueError(
                    "Unable to connect to PostgreSQL: "
                    f"{exc}"
                ) from exc

            repo = PostgresRepository(
                engine
            )

            discovery = PostgresSchemaDiscovery(
                engine
            )

            schema = (
                await discovery.discover_schema()
            )

            connection_obj = {
                "type":
                    "postgresql",

                "engine":
                    engine,

                "repo":
                    repo,

                "discovery":
                    discovery,

                "host":
                    credentials["host"],

                "port":
                    int(
                        credentials["port"]
                    ),

                "database":
                    credentials["database"],

                "username":
                    credentials["username"],

                "user_id":
                    credentials.get(
                        "authenticated_user_id"
                    ),

                "user_email":
                    credentials.get(
                        "authenticated_user_email"
                    ),
            }

            redis_metadata = {
                "type":
                    "postgresql",

                "host":
                    credentials["host"],

                "port":
                    int(
                        credentials["port"]
                    ),

                "database":
                    credentials["database"],

                "username":
                    credentials["username"],

                "user_id":
                    credentials.get(
                        "authenticated_user_id"
                    ),

                "created_at":
                    datetime.now(
                        timezone.utc
                    ).isoformat(),
            }

        else:
            raise ValueError(
                f"Unsupported database type: "
                f"{db_type}"
            )

        self.sessions[
            session_id
        ] = {
            "connection":
                connection_obj,

            "schema":
                schema,
        }

        if self.session_store is not None:
            self.session_store.save(
                session_id,
                redis_metadata,
            )

        print(
            "[ConnectionManager] Session stored"
        )

        print(
            "[ConnectionManager] Session owner verified"
        )

        return session_id

    async def get_connection(
        self,
        session_id: str,
    ) -> Any:
        session = self.sessions.get(
            session_id
        )

        if session:
            if self.session_store is not None:
                self.session_store.touch(
                    (session_id,)
                )

            return session[
                "connection"
            ]

        redis_session = (
            self.session_store.load(
                session_id
            )
            if self.session_store is not None
            else None
        )

        if redis_session:
            raise ValueError(
                "Session metadata exists in Redis, "
                "but the live database connection "
                "is not available in this backend "
                "process. Reconnect the database "
                "session. Cross-instance credential "
                "reconstruction must be implemented "
                "separately without storing raw OAuth "
                "tokens in Redis."
            )

        raise ValueError(
            "Session not found"
        )

    async def get_schema(
        self,
        session_id: str,
    ) -> DatabaseSchema:
        session = self.sessions.get(
            session_id
        )

        if not session:
            raise ValueError(
                "Session not found"
            )

        return session[
            "schema"
        ]

    async def init_memory(
        self,
        session_id: str,
    ):
        local_exists = (
            session_id
            in
            self.sessions
        )

        redis_exists = (
            self.session_store.load(
                session_id
            )
            if self.session_store is not None
            else None
        )

        if (
            not local_exists
            and
            not redis_exists
        ):
            raise ValueError(
                "Session not found"
            )

        if self.conversation_store is None:
            raise RuntimeError(
                "Redis conversation store "
                "is not configured."
            )

        return RedisConversationMemory(
            store=
                self.conversation_store,

            session_id=
                session_id,

            memory_key=
                "chat_history",

            input_key=
                "input",

            output_key=
                "output",

            return_messages=
                True,
        )

    async def get_memory(
        self,
        session_id: str,
    ):
        return await self.init_memory(
            session_id
        )