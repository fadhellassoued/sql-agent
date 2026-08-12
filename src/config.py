import os
from dotenv import load_dotenv

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
    #groq
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

settings = Settings()