import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://labguard:labguard@localhost:5432/labguard",
)

# Worker settings
MAX_ATTEMPTS = int(os.getenv("WORKER_MAX_ATTEMPTS", "3"))  # failed tries before a message is parked as 'failed'
POLL_INTERVAL = float(os.getenv("WORKER_POLL_INTERVAL", "1.0"))  # seconds to sleep when the queue is empty
