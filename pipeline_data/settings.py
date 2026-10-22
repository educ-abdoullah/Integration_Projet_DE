"""Configuration par variables d'environnement, partagee avec les conteneurs."""

import os
from urllib.parse import quote


def database_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    user = quote(os.getenv("POSTGRES_USER", "pipeline"), safe="")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = quote(os.getenv("POSTGRES_DB", "transactions"), safe="")
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"
