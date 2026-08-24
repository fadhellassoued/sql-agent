import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
from infrastructure.web.fastapi_app import create_app
from infrastructure.security.secure_logging import install_secure_logging_filters

load_dotenv()

class Settings:
    PROJECT_NAME: str = "SQL Optimizer Agent"
    VERSION: str = "2.0.0"

    # GCP
    GCP_PROJECT_ID: str = os.getenv("GCP_PROJECT_ID", "")
    GCP_REGION: str = os.getenv("GCP_REGION", "europe-west1")

    # BigQuery default dataset (used if not provided in connect)
    BIGQUERY_DATASET: str = os.getenv("BIGQUERY_DATASET", "")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "")

    # Gemini
    GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-2.0-flash-exp")

settings = Settings()

# Crée l'application FastAPI
app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)