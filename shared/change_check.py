"""
Change detection for Notion sync scripts.

Uses a local JSON file to track last-synced timestamps.
In GitHub Actions, this file is persisted between runs via actions/cache.
Locally, it persists in the working directory.
"""

import json
import os

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


def check_page_changed(notion, page_id, cache_key):
    """Check if a Notion page has been modified since last sync.

    Returns (changed: bool, current_timestamp: str).
    """
    page = notion.pages.retrieve(page_id)
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
        result = notion.databases.query(
            database_id=db_id,
            sorts=[{"timestamp": "last_edited_time", "direction": "descending"}],
            page_size=1,
        )
        if result["results"]:
            t = result["results"][0]["last_edited_time"]
            if latest is None or t > latest:
                latest = t

    cached = get_cached_time(cache_key)
    if cached and latest and cached >= latest:
        return False, latest
    return True, latest
