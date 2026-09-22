"""Environment + runtime configuration. Never logs or exposes the API key."""
import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY", "").strip()
FIREWORKS_BASE_URL = os.getenv(
    "FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1"
).rstrip("/")

DB_PATH = os.getenv("ROUTER_DB_PATH", str(REPO_ROOT / "router.db"))
TIMEOUT_SECONDS = float(os.getenv("FIREWORKS_TIMEOUT_SECONDS", "120"))


def has_api_key() -> bool:
    return bool(FIREWORKS_API_KEY)


def redact(text: str) -> str:
    """Strip anything key-shaped out of text before it reaches a client or log."""
    if not text:
        return text
    out = text
    if FIREWORKS_API_KEY:
        out = out.replace(FIREWORKS_API_KEY, "***REDACTED***")
    import re

    return re.sub(r"fw_[A-Za-z0-9_\-]{8,}", "***REDACTED***", out)
