"""Shared web helpers: Jinja2 templates and required secrets."""
import os
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def secret(name):
    """Required secret from the environment (Secret Manager on Cloud Run, shell or .env locally)."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable {name}")
    return value
