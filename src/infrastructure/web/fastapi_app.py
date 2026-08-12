from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .rest_controllers import router
from .websocket_handler import websocket_endpoint

def create_app() -> FastAPI:
    app = FastAPI(title="SQL Optimizer Agent", version="2.0.0")
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    
    app.include_router(router, prefix="/api/v1")
    app.add_api_websocket_route("/ws/analyze", websocket_endpoint)
    
    @app.get("/health")
    async def health():
        return {"status": "healthy"}
    
    return app