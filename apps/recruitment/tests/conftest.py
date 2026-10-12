import itertools
import os
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(APP_DIR), str(APP_DIR.parent.parent)]

# Dummy values so tests never reach real Notion or Google with the developer's .env
for name in ("NOTION_API_TOKEN", "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET",
             "SESSION_SECRET", "INGEST_TOKEN"):
    os.environ[name] = f"test-{name.lower()}"
# Empty rather than unset: load_dotenv() in shared/auth.py does not override existing variables
for name in ("GOOGLE_CLIENT_EMAIL", "GOOGLE_PRIVATE_KEY"):
    os.environ[name] = ""


@pytest.fixture(autouse=True)
def rate_limit(monkeypatch):
    """A fresh save limiter whose clock moves 2 s per save, so only tests that set the clock can trip it."""
    from panel import ratelimit
    ticks = itertools.count(step=2)
    monkeypatch.setattr(ratelimit, "clock", lambda: next(ticks))
    monkeypatch.setattr(ratelimit, "_events", defaultdict(OrderedDict))
