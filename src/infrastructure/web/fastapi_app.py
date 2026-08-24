from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .rest_controllers import router
from .websocket_handler import websocket_endpoint

from infrastructure.security.secure_logging import (
    install_secure_logging_filters,
)

from src.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uvicorn logging is configured before FastAPI lifespan startup.
    # Installing the filters here ensures the access handlers exist.
    install_secure_logging_filters()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="SQL Optimizer Agent",
        version="2.0.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SESSION_SECRET,
        session_cookie="sql_optimizer_session",
        same_site="lax",
        https_only=False,  # LOCAL ONLY
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(
        router,
        prefix="/api/v1",
    )

    app.add_api_websocket_route(
        "/ws/analyze",
        websocket_endpoint,
    )

    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
        }

    return app