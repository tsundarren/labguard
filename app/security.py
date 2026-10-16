"""Minimal protections for a public demo. Read at request time so tests can change them."""
import hmac
import os

from fastapi import Header, HTTPException


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """If API_KEY is set, writers must send it as the X-API-Key header. Unset = open (local dev)."""
    expected = os.getenv("API_KEY")
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(401, "Missing or invalid API key.")


def is_read_only() -> bool:
    return os.getenv("READ_ONLY", "").lower() in ("1", "true", "yes")


def require_writable() -> None:
    """READ_ONLY=1 stops visitors from changing finding statuses on a public deployment."""
    if is_read_only():
        raise HTTPException(403, "This deployment is read-only.")
