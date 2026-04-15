"""
Change detection for Notion sync scripts.

Uses a local JSON file to track last-synced timestamps.
In GitHub Actions, this file is persisted between runs via actions/cache.
Locally, it persists in the working directory.
"""

import json
import os
import time

CACHE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".last_sync")


def _read_cache():
    try:
        with open(CACHE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write_cache(data):
    with open(CACHE_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_cached_time(key):
    return _read_cache().get(key)


def set_cached_time(key, timestamp):
    data = _read_cache()
    data[key] = timestamp
    _write_cache(data)


def _retry_notion_call(fn, retries=3, backoff=2):
    """Retry a Notion API call on transient server errors (5xx)."""
    for attempt in range(retries):
        try:
            return fn()
        except Exception as e:
            status = getattr(e, "status", None) or getattr(getattr(e, "response", None), "status_code", None)
            if status and 500 <= status < 600 and attempt < retries - 1:
                wait = backoff * (attempt + 1)
                print(f"  Notion {status}, retrying in {wait}s ({attempt + 1}/{retries})")
                time.sleep(wait)
            else:
                raise


def check_page_changed(notion, page_id, cache_key):
    """Check if a Notion page has been modified since last sync.

    Returns (changed: bool, current_timestamp: str).
    """
    page = _retry_notion_call(lambda: notion.pages.retrieve(page_id))
    current = page["last_edited_time"]
    cached = get_cached_time(cache_key)

    if cached == current:
        return False, current
    return True, current


def check_databases_changed(notion, database_ids, cache_key):
    """Check if any page in the given databases has been modified since last sync.

    Queries each database for the most recently edited page and compares
    with the cached timestamp.

    Returns (changed: bool, latest_timestamp: str).
    """
    latest = None
    for db_id in database_ids:
        result = _retry_notion_call(lambda db=db_id: notion.databases.query(
            database_id=db,
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
            page_size=1,
        ))
        if result["results"]:
            t = result["results"][0]["last_edited_time"]
            if latest is None or t > latest:
                latest = t

    cached = get_cached_time(cache_key)
    if cached and latest and cached >= latest:
        return False, latest
    return True, latest
