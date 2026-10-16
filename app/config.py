import os


def normalize_database_url(url: str) -> str:
    """Hosts hand out postgres:// or postgresql:// URLs; SQLAlchemy must be told to use psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


DATABASE_URL = normalize_database_url(
    os.getenv("DATABASE_URL", "postgresql+psycopg://labguard:labguard@localhost:5432/labguard")
)

# Worker settings
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))  # failed tries before a message is parked as 'failed'
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))  # standalone worker: seconds to sleep when idle
# Embedded worker (hosted demo): the API wakes it on every new message, so polling is only a safety net.
# Keep it long so an idle database can scale to zero.
EMBEDDED_POLL_INTERVAL = float(os.getenv("EMBEDDED_WORKER_POLL_INTERVAL", "900"))
