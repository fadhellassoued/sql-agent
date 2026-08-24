import os
from dotenv import load_dotenv

load_dotenv()


class Settings:

    PROJECT_NAME: str = (
        "SQL Optimizer Agent"
    )

    VERSION: str = (
        "2.0.0"
    )

    # ========================================================
    # GCP
    # ========================================================

    GCP_PROJECT_ID: str = os.getenv(
        "GCP_PROJECT_ID",
        "",
    )

    GCP_REGION: str = os.getenv(
        "GCP_REGION",
        "europe-west1",
    )

    # ========================================================
    # BIGQUERY
    # ========================================================

    BIGQUERY_DATASET: str = os.getenv(
        "BIGQUERY_DATASET",
        "",
    )

    # ========================================================
    # REDIS
    # ========================================================

    REDIS_URL: str = os.getenv(
        "REDIS_URL",
        "",
    )

    # ========================================================
    # GEMINI
    # ========================================================

    GEMINI_MODEL: str = os.getenv(
        "GEMINI_MODEL",
        "gemini-2.0-flash-exp",
    )

    # ========================================================
    # GROQ
    # ========================================================

    GROQ_API_KEY: str = os.getenv(
        "GROQ_API_KEY",
        "",
    )

    # ========================================================
    # GOOGLE OAUTH
    # ========================================================

    GOOGLE_OAUTH_CLIENT_ID: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_ID",
        "",
    )

    GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv(
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "",
    )

    GOOGLE_OAUTH_REDIRECT_URI: str = os.getenv(
        "GOOGLE_OAUTH_REDIRECT_URI",
        "http://localhost:8000/api/v1/gcp/auth/callback",
    )

    FRONTEND_URL: str = os.getenv(
        "FRONTEND_URL",
        "http://localhost:5173",
    )

    SESSION_SECRET: str = os.getenv(
        "SESSION_SECRET",
        "",
    )

    BIGQUERY_ON_DEMAND_PRICE_PER_TIB: float = float(
        os.getenv(
            "BIGQUERY_ON_DEMAND_PRICE_PER_TIB",
            "6.25",
        )
    )

    BIGQUERY_MAX_ESTIMATED_COST_USD: float = float(
        os.getenv(
            "BIGQUERY_MAX_ESTIMATED_COST_USD",
            "1.00",
        )
    )


settings = Settings()