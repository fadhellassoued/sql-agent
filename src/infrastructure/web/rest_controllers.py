from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)

from fastapi.responses import RedirectResponse

from pydantic import BaseModel

import time

from google.cloud import bigquery

from sqlalchemy import inspect, text

from infrastructure.services.connection_manager_impl import (
    ConnectionManagerImpl,
)

from infrastructure.repositories.redis_cache import (
    RedisCacheRepository,
)

from infrastructure.memory.redis_client import (
    RedisClientProvider,
)

from infrastructure.memory.conversation_store import (
    ConversationStore,
)

from infrastructure.memory.state_stores import (
    SessionStore,
    AgentStateStore,
    IntentStore,
    CandidateStore,
    MetadataCacheStore,
)

from infrastructure.services.gemini_llm import (
    GeminiLLMService,
)

from infrastructure.repositories.bigquery_repository import (
    BigQueryRepository,
)

from infrastructure.repositories.postgres_repository import (
    PostgresRepository,
)

from optimization.bigquery.candidate_generator import (
    BigQueryCandidateGenerator,
)

from optimization.bigquery.candidate_validator import (
    BigQueryCandidateValidator,
)

from optimization.core.query_structure_analyzer import (
    QueryStructureAnalyzer,
)

from optimization.bigquery.evidence import (
    BigQueryEvidenceAnalyzer,
)

from optimization.bigquery.evidence_join import (
    BigQueryJoinEvidenceAnalyzer,
)

from optimization.bigquery.join_candidate_generator import (
    BigQueryJoinCandidateGenerator,
)

from agents.langchain_agent import (
    create_agent,
)

from infrastructure.security.iap_auth import (
    AuthenticatedUser,
    get_authenticated_user,
)

from infrastructure.security.gcp_oauth import (
    create_authorization_url,
    create_oauth_flow,
    generate_code_verifier,
    store_oauth_credentials,
    get_oauth_credentials,
    get_oauth_user,
    delete_oauth_session,
)

from src.config import settings


router = APIRouter()


# ============================================================
# SAFE LOGGING HELPERS
# ============================================================

def _mask_email(value: str | None) -> str:
    if not value or "@" not in value:
        return "***"

    local, domain = value.split("@", 1)
    visible = local[:2] if local else ""
    return f"{visible}***@{domain}"


def _short_session_id(value: str | None) -> str:
    if not value:
        return "none"
    return f"{value[:8]}…"


def _safe_connection_log(credentials: dict) -> dict:
    # Never log credentials, tokens, user IDs, emails, or passwords.
    allowed = {
        "db_type",
        "project_id",
        "dataset",
        "host",
        "port",
        "database",
        "username",
    }

    return {
        key: value
        for key, value in credentials.items()
        if key in allowed
    }


# ============================================================
# GLOBAL SERVICES
# ============================================================

cache_repo = RedisCacheRepository()

redis_provider = RedisClientProvider()

session_store = SessionStore(
    redis_provider
)

conversation_store = ConversationStore(
    redis_provider
)

agent_state_store = AgentStateStore(
    redis_provider
)

intent_store = IntentStore(
    redis_provider
)

candidate_store = CandidateStore(
    redis_provider
)

metadata_cache_store = MetadataCacheStore(
    redis_provider
)

conn_manager = ConnectionManagerImpl(
    cache_repo,
    session_store=
        session_store,
    conversation_store=
        conversation_store,
)

llm_service = GeminiLLMService()


# ============================================================
# REQUEST MODELS
# ============================================================

class GCPDatasetsRequest(BaseModel):
    project_id: str


class ConnectRequest(BaseModel):

    db_type: str = "bigquery"

    # --------------------------------------------------------
    # BigQuery
    # --------------------------------------------------------

    project_id: str | None = None
    dataset: str | None = None

    # --------------------------------------------------------
    # PostgreSQL
    # --------------------------------------------------------

    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None


class EstimateCostRequest(BaseModel):

    session_id: str
    sql_query: str


class ColumnOptimizationRequest(BaseModel):

    session_id: str
    sql_query: str
    selected_columns: list[str]


class RuntimeBenchmarkRequest(BaseModel):

    session_id: str
    sql_query: str


class AnalyzeRequest(BaseModel):

    session_id: str
    sql_query: str


# ============================================================
# APPLICATION AUTHENTICATION
# ============================================================

def require_authenticated_user(
    request: Request,
) -> AuthenticatedUser:

    return get_authenticated_user(
        request
    )


# ============================================================
# GENERIC REQUEST USER
# ============================================================

def get_request_user(
    request: Request,
) -> dict:

    oauth_session_id = (
        request.session.get(
            "gcp_oauth_session_id"
        )
    )

    oauth_user = get_oauth_user(
        oauth_session_id
    )

    if oauth_user:

        return {
            "user_id":
                oauth_user[
                    "user_id"
                ],

            "email":
                oauth_user[
                    "email"
                ],

            "source":
                "google_oauth",
        }

    app_user = (
        require_authenticated_user(
            request
        )
    )

    return {
        "user_id":
            app_user.user_id,

        "email":
            app_user.email,

        "source":
            "iap",
    }


# ============================================================
# APPLICATION USER
# ============================================================

@router.get(
    "/auth/me"
)
async def get_current_user(
    request: Request,
):

    user = (
        require_authenticated_user(
            request
        )
    )

    return {
        "authenticated":
            True,

        "user_id":
            user.user_id,

        "email":
            user.email,
    }


# ============================================================
# GOOGLE CLOUD OAUTH STATUS
# ============================================================

@router.get(
    "/gcp/auth/status"
)
async def gcp_auth_status(
    request: Request,
):

    oauth_session_id = (
        request.session.get(
            "gcp_oauth_session_id"
        )
    )

    user = get_oauth_user(
        oauth_session_id
    )

    if not user:

        return {
            "authenticated":
                False,

            "user":
                None,
        }

    return {
        "authenticated":
            True,

        "user": {
            "user_id":
                user[
                    "user_id"
                ],

            "email":
                user[
                    "email"
                ],
        },
    }


# ============================================================
# GOOGLE CLOUD OAUTH LOGIN
# ============================================================

@router.get(
    "/gcp/auth/login"
)
async def gcp_auth_login(
    request: Request,
):

    code_verifier = (
        generate_code_verifier()
    )

    (
        authorization_url,
        state,
    ) = create_authorization_url(
        code_verifier
    )

    request.session[
        "gcp_oauth_state"
    ] = state

    request.session[
        "gcp_oauth_code_verifier"
    ] = code_verifier

    print(
        "[OAuth] Authorization started."
    )

    return RedirectResponse(
        authorization_url
    )


# ============================================================
# GOOGLE CLOUD OAUTH CALLBACK
# ============================================================

@router.get(
    "/gcp/auth/callback"
)
async def gcp_auth_callback(
    request: Request,
):

    expected_state = (
        request.session.get(
            "gcp_oauth_state"
        )
    )

    code_verifier = (
        request.session.get(
            "gcp_oauth_code_verifier"
        )
    )

    returned_state = (
        request.query_params.get(
            "state"
        )
    )

    if (
        not expected_state
        or not returned_state
        or expected_state
        != returned_state
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid OAuth state. "
                "Authentication request rejected."
            ),
        )

    if not code_verifier:

        raise HTTPException(
            status_code=400,
            detail=(
                "OAuth PKCE verifier is missing. "
                "Please restart Google sign-in."
            ),
        )

    flow = create_oauth_flow(
        state=returned_state,
        code_verifier=code_verifier,
    )

    try:

        flow.fetch_token(
            authorization_response=
                str(
                    request.url
                )
        )

        credentials = (
            flow.credentials
        )

        (
            oauth_session_id,
            user,
        ) = store_oauth_credentials(
            credentials
        )

    except Exception as exc:

        print(
            "[OAuth] Callback failed: "
            f"{exc}"
        )

        request.session.pop(
            "gcp_oauth_state",
            None,
        )

        request.session.pop(
            "gcp_oauth_code_verifier",
            None,
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "Google authentication failed: "
                f"{exc}"
            ),
        )

    request.session[
        "gcp_oauth_session_id"
    ] = oauth_session_id

    request.session.pop(
        "gcp_oauth_state",
        None,
    )

    request.session.pop(
        "gcp_oauth_code_verifier",
        None,
    )

    print(
        "[OAuth] Google Cloud account connected "
        f"user={_mask_email(user.get('email'))}"
    )

    return RedirectResponse(
        url=(
            f"{settings.FRONTEND_URL}"
            "?gcp_auth=success"
        )
    )


# ============================================================
# GOOGLE CLOUD OAUTH LOGOUT
# ============================================================

@router.post(
    "/gcp/auth/logout"
)
async def gcp_auth_logout(
    request: Request,
):

    oauth_session_id = (
        request.session.get(
            "gcp_oauth_session_id"
        )
    )

    delete_oauth_session(
        oauth_session_id
    )

    request.session.pop(
        "gcp_oauth_session_id",
        None,
    )

    request.session.pop(
        "gcp_oauth_state",
        None,
    )

    request.session.pop(
        "gcp_oauth_code_verifier",
        None,
    )

    return {
        "authenticated":
            False
    }


# ============================================================
# BIGQUERY DATASET DISCOVERY
# ============================================================

@router.post(
    "/gcp/datasets"
)
async def list_gcp_datasets(
    request: GCPDatasetsRequest,
    http_request: Request,
):

    project_id = (
        request.project_id.strip()
    )

    if not project_id:

        raise HTTPException(
            status_code=400,
            detail=(
                "GCP project_id is required."
            ),
        )

    oauth_session_id = (
        http_request.session.get(
            "gcp_oauth_session_id"
        )
    )

    user_credentials = (
        get_oauth_credentials(
            oauth_session_id
        )
    )

    oauth_user = (
        get_oauth_user(
            oauth_session_id
        )
    )

    if (
        not user_credentials
        or not oauth_user
    ):

        raise HTTPException(
            status_code=401,
            detail=(
                "Connect your Google Cloud "
                "account first."
            ),
        )

    print(
        "[REST] Dataset discovery "
        f"user={_mask_email(oauth_user.get('email'))}, "
        f"project={project_id}"
    )

    try:

        client = bigquery.Client(
            project=project_id,
            credentials=
                user_credentials,
        )

        datasets = []

        for dataset in (
            client.list_datasets(
                project=project_id
            )
        ):

            datasets.append(
                {
                    "dataset_id":
                        dataset.dataset_id,

                    "full_dataset_id":
                        dataset.full_dataset_id,
                }
            )

        datasets.sort(
            key=lambda item:
                item[
                    "dataset_id"
                ].lower()
        )

        print(
            "[REST] Found "
            f"{len(datasets)} dataset(s) "
            f"for user={_mask_email(oauth_user.get('email'))} "
            f"in project={project_id}"
        )

        return {
            "project_id":
                project_id,

            "authenticated_user":
                oauth_user[
                    "email"
                ],

            "datasets":
                datasets,
        }

    except Exception as exc:

        print(
            "[REST] Dataset discovery failed: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=403,
            detail=(
                f"Unable to access GCP project "
                f"'{project_id}' as "
                f"{oauth_user['email']}. "
                f"Verify that this Google account "
                f"has the required BigQuery IAM permissions. "
                f"Details: {exc}"
            ),
        )


# ============================================================
# CONNECT
# ============================================================

@router.post(
    "/connect"
)
async def connect(
    request: ConnectRequest,
    http_request: Request,
):

    credentials = (
        request.model_dump(
            exclude_none=True
        )
    )

    if (
        request.db_type.lower()
        ==
        "bigquery"
    ):

        oauth_session_id = (
            http_request.session.get(
                "gcp_oauth_session_id"
            )
        )

        gcp_credentials = (
            get_oauth_credentials(
                oauth_session_id
            )
        )

        oauth_user = (
            get_oauth_user(
                oauth_session_id
            )
        )

        if (
            not gcp_credentials
            or not oauth_user
        ):

            raise HTTPException(
                status_code=401,
                detail=(
                    "Google Cloud authentication "
                    "is required."
                ),
            )

        credentials[
            "gcp_credentials"
        ] = gcp_credentials

        credentials[
            "authenticated_user_id"
        ] = oauth_user[
            "user_id"
        ]

        credentials[
            "authenticated_user_email"
        ] = oauth_user[
            "email"
        ]

        print(
            "[REST] Creating BigQuery connection "
            f"for user={_mask_email(oauth_user.get('email'))}"
        )

    else:

        app_user = (
            require_authenticated_user(
                http_request
            )
        )

        credentials[
            "authenticated_user_id"
        ] = app_user.user_id

        credentials[
            "authenticated_user_email"
        ] = app_user.email

        print(
            "[REST] Creating PostgreSQL connection "
            f"for application user="
            f"{app_user.email}"
        )

    safe_log = (
        _safe_connection_log(
            credentials
        )
    )

    print(
        "[REST] Connection parameters:"
    )

    print(
        safe_log
    )

    try:

        session_id = (
            await conn_manager.connect(
                credentials
            )
        )

        schema = (
            await conn_manager.get_schema(
                session_id
            )
        )

        if not schema:

            raise ValueError(
                "Schema not found after connection."
            )

        tables = [
            table.name

            for table
            in schema.tables
        ]

        await conn_manager.init_memory(
            session_id
        )

        print(
            "[REST] Connection successful "
            f"session={_short_session_id(session_id)}"
        )

        return {
            "session_id":
                session_id,

            "db_type":
                request.db_type,

            "project_id":
                request.project_id,

            "dataset":
                request.dataset,

            "database":
                request.database,

            "tables":
                tables,
        }

    except HTTPException:

        raise

    except Exception as exc:

        print(
            "[REST] Connection failed: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=400,
            detail=str(
                exc
            ),
        )


# ============================================================
# DATABASE / DATASET METADATA
# ============================================================

@router.get(
    "/sessions/{session_id}/metadata"
)
async def get_session_metadata(
    session_id: str,
    http_request: Request,
):

    try:

        conn_obj = (
            await conn_manager.get_connection(
                session_id
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    request_user = (
        get_request_user(
            http_request
        )
    )

    session_owner = (
        conn_obj.get(
            "user_id"
        )
    )

    if (
        session_owner
        and
        session_owner
        !=
        request_user[
            "user_id"
        ]
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "This database session belongs "
                "to another authenticated user."
            ),
        )

    database_type = (
        conn_obj[
            "type"
        ]
    )

    # ========================================================
    # BIGQUERY METADATA
    # ========================================================

    if database_type == "bigquery":

        client = (
            conn_obj[
                "client"
            ]
        )

        project_id = (
            conn_obj[
                "project_id"
            ]
        )

        dataset_id = (
            conn_obj[
                "dataset"
            ]
        )

        full_dataset_id = (
            f"{project_id}."
            f"{dataset_id}"
        )

        try:

            dataset_obj = (
                client.get_dataset(
                    full_dataset_id
                )
            )

            table_items = list(
                client.list_tables(
                    full_dataset_id
                )
            )

            tables = []

            total_rows = 0
            total_bytes = 0

            for table_item in table_items:

                full_table_id = (
                    f"{project_id}."
                    f"{dataset_id}."
                    f"{table_item.table_id}"
                )

                try:

                    table = (
                        client.get_table(
                            full_table_id
                        )
                    )

                except Exception as table_exc:

                    print(
                        "[REST] Unable to load "
                        f"metadata for "
                        f"{full_table_id}: "
                        f"{table_exc}"
                    )

                    continue

                row_count = (
                    table.num_rows
                    or
                    0
                )

                size_bytes = (
                    table.num_bytes
                    or
                    0
                )

                total_rows += (
                    row_count
                )

                total_bytes += (
                    size_bytes
                )

                partition_type = (
                    None
                )

                partition_field = (
                    None
                )

                if table.time_partitioning:

                    partition_type = (
                        str(
                            table
                            .time_partitioning
                            .type
                        )
                    )

                    partition_field = (
                        table
                        .time_partitioning
                        .field
                    )

                elif table.range_partitioning:

                    partition_type = (
                        "RANGE"
                    )

                    partition_field = (
                        table
                        .range_partitioning
                        .field
                    )

                columns = []

                def add_fields(
                    fields,
                    prefix="",
                ):

                    for field in fields:

                        field_name = (
                            f"{prefix}.{field.name}"
                            if prefix
                            else field.name
                        )

                        columns.append(
                            {
                                "name":
                                    field_name,

                                "type":
                                    field.field_type,

                                "mode":
                                    field.mode,

                                "description":
                                    field.description,
                            }
                        )

                        if field.fields:

                            add_fields(
                                field.fields,
                                field_name,
                            )

                add_fields(
                    table.schema
                )

                tables.append(
                    {
                        "name":
                            table.table_id,

                        "full_name":
                            full_table_id,

                        "table_type":
                            getattr(
                                table_item,
                                "table_type",
                                None,
                            ),

                        "row_count":
                            row_count,

                        "size_bytes":
                            size_bytes,

                        "partitioned":
                            bool(
                                table.time_partitioning
                                or
                                table.range_partitioning
                            ),

                        "partition_type":
                            partition_type,

                        "partition_field":
                            partition_field,

                        "clustering_fields":
                            (
                                table.clustering_fields
                                or
                                []
                            ),

                        "created":
                            (
                                table.created.isoformat()
                                if table.created
                                else None
                            ),

                        "modified":
                            (
                                table.modified.isoformat()
                                if table.modified
                                else None
                            ),

                        "description":
                            table.description,

                        "columns":
                            columns,
                    }
                )

            tables.sort(
                key=lambda item:
                    item[
                        "name"
                    ].lower()
            )

            return {
                "database_type":
                    "bigquery",

                "authenticated_user":
                    request_user[
                        "email"
                    ],

                "project_id":
                    project_id,

                "dataset":
                    dataset_id,

                "overview": {
                    "table_count":
                        len(
                            tables
                        ),

                    "total_rows":
                        total_rows,

                    "total_bytes":
                        total_bytes,

                    "location":
                        dataset_obj.location,

                    "created":
                        (
                            dataset_obj.created.isoformat()
                            if dataset_obj.created
                            else None
                        ),

                    "modified":
                        (
                            dataset_obj.modified.isoformat()
                            if dataset_obj.modified
                            else None
                        ),

                    "description":
                        dataset_obj.description,
                },

                "tables":
                    tables,
            }

        except Exception as exc:

            print(
                "[REST] BigQuery metadata "
                f"retrieval failed: {exc}"
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    "Unable to retrieve BigQuery "
                    f"metadata: {exc}"
                ),
            )

    # ========================================================
    # POSTGRESQL METADATA
    # ========================================================

    elif database_type == "postgresql":

        engine = (
            conn_obj[
                "engine"
            ]
        )

        try:

            inspector = inspect(
                engine
            )

            schema_names = [
                schema_name

                for schema_name
                in inspector.get_schema_names()

                if schema_name not in {
                    "pg_catalog",
                    "information_schema",
                }
            ]

            metadata_tables = []
            total_bytes = 0

            with engine.connect() as connection:

                db_size = (
                    connection.execute(
                        text(
                            """
                            SELECT
                                pg_database_size(
                                    current_database()
                                )
                            """
                        )
                    ).scalar()
                )

            for schema_name in schema_names:

                table_names = (
                    inspector.get_table_names(
                        schema=
                            schema_name
                    )
                )

                for table_name in table_names:

                    columns_raw = (
                        inspector.get_columns(
                            table_name,
                            schema=
                                schema_name,
                        )
                    )

                    columns = [
                        {
                            "name":
                                column[
                                    "name"
                                ],

                            "type":
                                str(
                                    column[
                                        "type"
                                    ]
                                ),

                            "nullable":
                                column.get(
                                    "nullable"
                                ),

                            "default":
                                (
                                    str(
                                        column.get(
                                            "default"
                                        )
                                    )
                                    if column.get(
                                        "default"
                                    )
                                    is not None
                                    else None
                                ),
                        }

                        for column
                        in columns_raw
                    ]

                    primary_key = (
                        inspector
                        .get_pk_constraint(
                            table_name,
                            schema=
                                schema_name,
                        )
                    )

                    foreign_keys = (
                        inspector
                        .get_foreign_keys(
                            table_name,
                            schema=
                                schema_name,
                        )
                    )

                    indexes = (
                        inspector
                        .get_indexes(
                            table_name,
                            schema=
                                schema_name,
                        )
                    )

                    estimated_rows = None
                    table_size = 0

                    with engine.connect() as connection:

                        row_result = (
                            connection.execute(
                                text(
                                    """
                                    SELECT
                                        c.reltuples::BIGINT
                                    FROM pg_class c
                                    JOIN pg_namespace n
                                        ON n.oid =
                                           c.relnamespace
                                    WHERE
                                        n.nspname =
                                        :schema_name
                                    AND
                                        c.relname =
                                        :table_name
                                    LIMIT 1
                                    """
                                ),

                                {
                                    "schema_name":
                                        schema_name,

                                    "table_name":
                                        table_name,
                                },
                            ).scalar()
                        )

                        if row_result is not None:

                            estimated_rows = (
                                int(
                                    row_result
                                )
                            )

                        size_result = (
                            connection.execute(
                                text(
                                    """
                                    SELECT
                                        pg_total_relation_size(
                                            quote_ident(
                                                :schema_name
                                            )
                                            || '.'
                                            ||
                                            quote_ident(
                                                :table_name
                                            )
                                        )
                                    """
                                ),

                                {
                                    "schema_name":
                                        schema_name,

                                    "table_name":
                                        table_name,
                                },
                            ).scalar()
                        )

                        if size_result:

                            table_size = (
                                int(
                                    size_result
                                )
                            )

                    total_bytes += (
                        table_size
                    )

                    metadata_tables.append(
                        {
                            "name":
                                table_name,

                            "schema":
                                schema_name,

                            "full_name":
                                (
                                    f"{schema_name}."
                                    f"{table_name}"
                                ),

                            "row_count":
                                estimated_rows,

                            "row_count_is_estimate":
                                True,

                            "size_bytes":
                                table_size,

                            "columns":
                                columns,

                            "primary_key":
                                primary_key.get(
                                    "constrained_columns",
                                    [],
                                ),

                            "foreign_keys":
                                foreign_keys,

                            "indexes":
                                indexes,
                        }
                    )

            metadata_tables.sort(
                key=lambda item: (
                    item[
                        "schema"
                    ].lower(),

                    item[
                        "name"
                    ].lower(),
                )
            )

            return {
                "database_type":
                    "postgresql",

                "host":
                    conn_obj.get(
                        "host"
                    ),

                "port":
                    conn_obj.get(
                        "port"
                    ),

                "database":
                    conn_obj.get(
                        "database"
                    ),

                "overview": {
                    "schema_count":
                        len(
                            schema_names
                        ),

                    "table_count":
                        len(
                            metadata_tables
                        ),

                    "database_size_bytes":
                        db_size,

                    "total_table_bytes":
                        total_bytes,

                    "schemas":
                        schema_names,
                },

                "tables":
                    metadata_tables,
            }

        except Exception as exc:

            print(
                "[REST] PostgreSQL metadata "
                f"retrieval failed: {exc}"
            )

            raise HTTPException(
                status_code=500,
                detail=(
                    "Unable to retrieve PostgreSQL "
                    f"metadata: {exc}"
                ),
            )

    raise HTTPException(
        status_code=400,
        detail=(
            f"Unsupported database type: "
            f"{database_type}"
        ),
    )


# ============================================================
# BIGQUERY COST ESTIMATION
# ============================================================

@router.post(
    "/cost/estimate"
)
async def estimate_query_cost(
    request: EstimateCostRequest,
    http_request: Request,
):

    if (
        not request.sql_query
        or not request.sql_query.strip()
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "SQL query is required."
            ),
        )

    # ========================================================
    # GET SESSION
    # ========================================================

    try:

        conn_obj = (
            await conn_manager.get_connection(
                request.session_id
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    # ========================================================
    # SESSION OWNERSHIP
    # ========================================================

    request_user = (
        get_request_user(
            http_request
        )
    )

    session_owner = (
        conn_obj.get(
            "user_id"
        )
    )

    if (
        session_owner
        and
        session_owner
        !=
        request_user[
            "user_id"
        ]
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "This database session belongs "
                "to another authenticated user."
            ),
        )

    # ========================================================
    # BIGQUERY ONLY
    # ========================================================

    if (
        conn_obj.get(
            "type"
        )
        !=
        "bigquery"
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Pre-optimization cost estimation "
                "is currently available for "
                "BigQuery only."
            ),
        )

    # ========================================================
    # REPOSITORY
    # ========================================================

    repository = (
        conn_obj.get(
            "repo"
        )
    )

    if repository is None:

        repository = (
            BigQueryRepository(
                conn_obj[
                    "client"
                ],
                conn_obj.get(
                    "dataset"
                ),
            )
        )

    # ========================================================
    # DRY RUN
    # ========================================================

    try:

        estimate = (
            await repository
            .estimate_query_cost(
                request.sql_query
            )
        )

    except Exception as exc:

        print(
            "[REST] Cost estimation failed: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "BigQuery could not estimate "
                "this query. "
                f"Details: {exc}"
            ),
        )

    print(
        "[REST] Cost estimate complete "
        f"user={_mask_email(request_user.get('email'))}, "
        f"bytes={estimate.get('bytes_processed')}, "
        f"cost={estimate.get('estimated_cost_usd')}"
    )

    return {
        "session_id":
            request.session_id,

        "database_type":
            "bigquery",

        "project_id":
            conn_obj.get(
                "project_id"
            ),

        "dataset":
            conn_obj.get(
                "dataset"
            ),

        "authenticated_user":
            request_user[
                "email"
            ],

        **estimate,
    }


# ============================================================
# BIGQUERY USER-TRIGGERED RUNTIME BENCHMARK
# ============================================================

@router.post(
    "/optimization/bigquery/runtime"
)
async def benchmark_bigquery_runtime(
    request: RuntimeBenchmarkRequest,
    http_request: Request,
):
    """
    User-triggered controlled runtime benchmark.

    Security / safety properties:
      - session ownership is verified;
      - BigQuery only;
      - the server re-analyzes the ORIGINAL SQL;
      - the server regenerates the strict join candidate itself;
      - the server requires PROVEN_FOR_SUPPORTED_PATTERN;
      - the validator re-runs dry-run safety checks;
      - real jobs only execute when runtime validation is enabled and
        both queries are under the configured byte ceiling.

    The client does NOT supply executable candidate SQL.
    """

    if (
        not request.sql_query
        or
        not request.sql_query.strip()
    ):
        raise HTTPException(
            status_code=400,
            detail="Original SQL query is required.",
        )

    try:
        conn_obj = (
            await conn_manager.get_connection(
                request.session_id
            )
        )
    except Exception as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        )

    if not conn_obj:
        raise HTTPException(
            status_code=404,
            detail="Database session not found.",
        )

    request_user = (
        get_request_user(
            http_request
        )
    )

    session_owner = (
        conn_obj.get(
            "user_id"
        )
    )

    if (
        session_owner
        and
        session_owner
        !=
        request_user[
            "user_id"
        ]
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                "This database session belongs "
                "to another authenticated user."
            ),
        )

    if (
        conn_obj.get(
            "type"
        )
        !=
        "bigquery"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Runtime benchmarking is currently "
                "available for BigQuery only."
            ),
        )

    repository = (
        BigQueryRepository(
            conn_obj[
                "client"
            ],
            conn_obj.get(
                "dataset"
            ),
        )
    )

    # --------------------------------------------------------
    # RE-ESTABLISH STRUCTURE + METADATA + JOIN EVIDENCE
    # --------------------------------------------------------

    try:
        structure = (
            QueryStructureAnalyzer()
            .analyze(
                request.sql_query
            )
        )

        table_evidence = (
            await BigQueryEvidenceAnalyzer(
                client=
                    conn_obj[
                        "client"
                    ],

                default_dataset=
                    conn_obj.get(
                        "dataset"
                    ),
            ).analyze(
                structure
            )
        )

        join_evidence = (
            BigQueryJoinEvidenceAnalyzer()
            .analyze(
                sql=
                    request.sql_query,

                structure=
                    structure,

                table_evidence=
                    table_evidence,
            )
        )

        generated_candidate = (
            BigQueryJoinCandidateGenerator()
            .generate(
                sql=
                    request.sql_query,

                join_evidence=
                    join_evidence,

                table_evidence=
                    table_evidence,
            )
        )

    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to re-establish the candidate "
                f"safety proof: {exc}"
            ),
        )

    if not generated_candidate.get(
        "generated"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "The current original SQL no longer produces "
                "a supported runtime-eligible candidate."
            ),
        )

    semantic_proof = (
        generated_candidate.get(
            "semantic_proof",
            {},
        )
        or {}
    )

    if (
        semantic_proof.get(
            "status"
        )
        !=
        "PROVEN_FOR_SUPPORTED_PATTERN"
    ):
        raise HTTPException(
            status_code=400,
            detail=(
                "Runtime benchmarking requires deterministic "
                "semantic proof for the supported rewrite pattern."
            ),
        )

    candidate_sql = (
        generated_candidate.get(
            "candidate_sql"
        )
    )

    validator = (
        BigQueryCandidateValidator()
    )

    benchmark_started_at = (
        time.perf_counter()
    )

    try:
        comparison = (
            await validator.compare(
                repository=
                    repository,

                original_sql=
                    request.sql_query,

                candidate_sql=
                    candidate_sql,

                allow_runtime_validation=
                    True,

                execute_runtime_validation=
                    True,
            )
        )

    except Exception as exc:
        print(
            "[REST] Runtime benchmark failed "
            f"user={_mask_email(request_user.get('email'))}: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "BigQuery runtime benchmark could not "
                f"be completed: {exc}"
            ),
        )

    benchmark_duration_ms = (
        time.perf_counter()
        -
        benchmark_started_at
    ) * 1000.0

    runtime_evidence = (
        comparison.get(
            "runtime_evidence"
        )
        or {}
    )

    original_runtime = (
        runtime_evidence.get(
            "original"
        )
        or {}
    )

    candidate_runtime = (
        runtime_evidence.get(
            "candidate"
        )
        or {}
    )

    print(
        "[RuntimeBenchmark] "
        f"pairs={runtime_evidence.get('completed_pairs')}, "
        f"candidate_wins={runtime_evidence.get('candidate_win_count')}, "
        f"required_wins={runtime_evidence.get('required_candidate_wins')}, "
        f"original_median_slot_ms={original_runtime.get('slot_millis')}, "
        f"candidate_median_slot_ms={candidate_runtime.get('slot_millis')}, "
        f"improvement_percent={runtime_evidence.get('slot_millis_improvement_percent')}, "
        f"duration_ms={benchmark_duration_ms:.2f}, "
        f"decision={comparison.get('decision')}"
    )

    print(
        "[REST] BigQuery runtime benchmark complete "
        f"user={_mask_email(request_user.get('email'))}, "
        f"decision={comparison.get('decision')}, "
        f"pairs={runtime_evidence.get('completed_pairs')}"
    )

    return {
        "session_id":
            request.session_id,

        "database_type":
            "bigquery",

        "rewrite_type":
            generated_candidate.get(
                "rewrite_type"
            ),

        "original_query":
            request.sql_query,

        "candidate_query":
            candidate_sql,

        "rule_codes":
            generated_candidate.get(
                "rule_codes",
                [],
            ),

        "optimization_rules":
            generated_candidate.get(
                "rule_codes",
                [],
            ),

        "optimization_status":
            comparison.get(
                "decision"
            ),

        "optimization_approved":
            comparison.get(
                "approved",
                False,
            ),

        "semantic_proof":
            semantic_proof,

        "quality_gate":
            comparison,

        "runtime_benchmark_duration_ms":
            benchmark_duration_ms,

        "candidate_explanation": (
            "The user-triggered BigQuery runtime benchmark completed "
            "using paired executions with alternating order. Approval "
            "requires both the configured median slot-time improvement "
            "and the minimum candidate win count."
        ),
    }


# ============================================================
# ANALYZE
# ============================================================


# ============================================================
# BIGQUERY COLUMN-SELECTION OPTIMIZATION
# ============================================================

@router.post(
    "/optimization/bigquery/columns"
)
async def optimize_bigquery_columns(
    request: ColumnOptimizationRequest,
    http_request: Request,
):
    """
    Generate and validate a deterministic BigQuery column-pruning
    candidate after the user explicitly chooses the required columns.

    Flow:

        SELECT *
            ↓
        user chooses columns
            ↓
        deterministic candidate SQL
            ↓
        original BigQuery dry run
            ↓
        candidate BigQuery dry run
            ↓
        APPROVE / REJECT decision
    """

    # ========================================================
    # VALIDATE REQUEST
    # ========================================================

    if (
        not request.sql_query
        or not request.sql_query.strip()
    ):

        raise HTTPException(
            status_code=400,
            detail="SQL query is required.",
        )

    selected_columns = [
        column.strip()
        for column in request.selected_columns
        if column and column.strip()
    ]

    selected_columns = list(
        dict.fromkeys(
            selected_columns
        )
    )

    if not selected_columns:

        raise HTTPException(
            status_code=400,
            detail=(
                "At least one column must be selected."
            ),
        )

    # ========================================================
    # GET SESSION
    # ========================================================

    try:

        conn_obj = (
            await conn_manager.get_connection(
                request.session_id
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    if not conn_obj:

        raise HTTPException(
            status_code=404,
            detail="Database session not found.",
        )

    # ========================================================
    # SESSION OWNERSHIP
    # ========================================================

    request_user = (
        get_request_user(
            http_request
        )
    )

    session_owner = (
        conn_obj.get(
            "user_id"
        )
    )

    if (
        session_owner
        and
        session_owner
        !=
        request_user[
            "user_id"
        ]
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "This database session belongs "
                "to another authenticated user."
            ),
        )

    # ========================================================
    # BIGQUERY ONLY
    # ========================================================

    if (
        conn_obj.get(
            "type"
        )
        !=
        "bigquery"
    ):

        raise HTTPException(
            status_code=400,
            detail=(
                "Column-selection optimization is currently "
                "available for BigQuery only."
            ),
        )

    # ========================================================
    # BUILD REPOSITORY
    # ========================================================

    repository = (
        BigQueryRepository(
            conn_obj[
                "client"
            ],
            conn_obj.get(
                "dataset"
            ),
        )
    )

    # ========================================================
    # GENERATE DETERMINISTIC CANDIDATE
    # ========================================================

    generator = (
        BigQueryCandidateGenerator()
    )

    try:

        candidate_result = (
            generator.generate_column_pruned_candidate(
                sql=
                    request.sql_query,

                selected_columns=
                    selected_columns,
            )
        )

    except Exception as exc:

        print(
            "[REST] Column-pruning candidate generation "
            f"failed: {exc}"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "Unable to generate the column-pruned "
                f"candidate: {exc}"
            ),
        )

    candidate_sql = (
        candidate_result[
            "candidate_sql"
        ]
    )

    # ========================================================
    # BIGQUERY QUALITY GATE
    # ========================================================

    validator = (
        BigQueryCandidateValidator()
    )

    try:

        comparison = (
            await validator.compare(
                repository=
                    repository,

                original_sql=
                    request.sql_query,

                candidate_sql=
                    candidate_sql,
            )
        )

    except Exception as exc:

        print(
            "[REST] BigQuery candidate validation "
            f"failed: {exc}"
        )

        raise HTTPException(
            status_code=400,
            detail=(
                "BigQuery could not validate the "
                f"candidate query: {exc}"
            ),
        )

    print(
        "[REST] BigQuery column candidate complete "
        f"user={_mask_email(request_user.get('email'))}, "
        f"decision={comparison.get('decision')}, "
        f"selected_column_count={len(selected_columns)}"
    )

    decision = (
        comparison.get(
            "decision"
        )
    )

    approved = (
        decision
        in {
            "APPROVE_SCAN_COST",
            "APPROVE_RUNTIME_IMPROVEMENT",
            # Backward compatibility with older validator responses.
            "APPROVE",
        }
    )

    safety_basis = {
        "status":
            "USER_INTENT_CONFIRMED",

        "basis": (
            "The user explicitly selected the output columns. "
            "Changing SELECT * to this projection is therefore an "
            "authorized output-schema change rather than an automatic "
            "semantic-equivalence claim."
        ),
    }

    candidate_explanation = (
        "The column-pruning candidate was generated from your explicit "
        "column selection and validated with BigQuery dry-run evidence. "
        "The user-intent safety gate is USER_INTENT_CONFIRMED. "
        f"Quality-gate decision: {decision}."
    )

    return {
        "session_id":
            request.session_id,

        "database_type":
            "bigquery",

        "rewrite_type":
            "COLUMN_PRUNING",

        "optimization_status":
            decision,

        "optimization_approved":
            approved,

        "optimization_rules": (
            [
                "BQ_COLUMN_PRUNING"
            ]
            if approved
            else []
        ),

        "original_query":
            request.sql_query,

        "candidate_query":
            candidate_sql,

        "selected_columns":
            selected_columns,

        "semantic_proof":
            safety_basis,

        "safety_basis":
            safety_basis,

        "candidate_explanation":
            candidate_explanation,

        "quality_gate":
            comparison,
    }


@router.post(
    "/analyze"
)
async def analyze(
    request: AnalyzeRequest,
    http_request: Request,
):

    request_user = (
        get_request_user(
            http_request
        )
    )

    start_time = (
        time.perf_counter()
    )

    try:

        conn_obj = (
            await conn_manager.get_connection(
                request.session_id
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    if (
        conn_obj.get(
            "user_id"
        )
        and
        conn_obj.get(
            "user_id"
        )
        !=
        request_user[
            "user_id"
        ]
    ):

        raise HTTPException(
            status_code=403,
            detail=(
                "This database session belongs "
                "to another authenticated user."
            ),
        )

    database_type = (
        conn_obj[
            "type"
        ]
    )

    if database_type == "bigquery":

        db_repo = (
            BigQueryRepository(
                conn_obj[
                    "client"
                ],

                conn_obj.get(
                    "dataset"
                ),
            )
        )

    elif database_type == "postgresql":

        db_repo = (
            PostgresRepository(
                conn_obj[
                    "engine"
                ]
            )
        )

    else:

        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported database type: "
                f"{database_type}"
            ),
        )

    try:

        schema = (
            await conn_manager.get_schema(
                request.session_id
            )
        )

    except Exception as exc:

        raise HTTPException(
            status_code=404,
            detail=str(
                exc
            ),
        )

    if not schema:

        raise HTTPException(
            status_code=404,
            detail=(
                "Schema not found."
            ),
        )

    memory = (
        await conn_manager.get_memory(
            request.session_id
        )
    )

    agent = create_agent(
        session_id=
            request.session_id,

        schema=
            schema,

        db_repo=
            db_repo,

        knowledge_catalog=
            None,

        llm_service=
            llm_service,

        memory=
            memory,

        database_type=
            database_type,
    )

    try:

        result = (
            await agent.ainvoke(
                {
                    "input":
                        request.sql_query
                }
            )
        )

    except Exception as exc:

        print(
            "[REST] Agent execution failed: "
            f"{exc}"
        )

        raise HTTPException(
            status_code=500,
            detail=(
                "Agent execution failed: "
                f"{exc}"
            ),
        )

    return {
        "result":
            result.get(
                "output",
                "",
            ),

        "execution_time_ms":
            round(
                (
                    time.perf_counter()
                    -
                    start_time
                )
                *
                1000,
                2,
            ),
    }


# ============================================================
# HEALTH
# ============================================================

@router.get(
    "/health"
)
async def api_health():

    redis_healthy = False
    redis_error = None

    try:
        redis_healthy = (
            redis_provider.ping()
        )

    except Exception as exc:
        redis_error = (
            type(exc).__name__
        )

    return {
        "status":
            (
                "healthy"
                if redis_healthy
                else "degraded"
            ),

        "redis":
            {
                "healthy":
                    redis_healthy,

                "error":
                    redis_error,
            },
    }


# ============================================================
# DEPENDENCY HELPERS
# ============================================================

def get_connection_manager():

    return conn_manager


def get_analyze_use_case():

    return None