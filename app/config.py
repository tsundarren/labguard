import os

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://labguard:labguard@localhost:5432/labguard",
)
