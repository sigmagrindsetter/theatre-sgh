import os
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(APP_DIR), str(APP_DIR.parent.parent)]

# Dummy values so tests never reach real Notion or Google with the developer's .env
for name in ("NOTION_API_TOKEN", "NOTION_OAUTH_CLIENT_ID", "NOTION_OAUTH_CLIENT_SECRET",
             "SESSION_SECRET", "INGEST_TOKEN"):
    os.environ[name] = f"test-{name.lower()}"
# Empty rather than unset: load_dotenv() in shared/auth.py does not override existing variables
for name in ("GOOGLE_CLIENT_EMAIL", "GOOGLE_PRIVATE_KEY"):
    os.environ[name] = ""
