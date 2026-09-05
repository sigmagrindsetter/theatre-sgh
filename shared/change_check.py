import json
import os
import subprocess

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


def _code_version():
    """Cache entries are only valid for the code that wrote them, so a deploy
    forces one regeneration even when Notion data has not changed."""
    sha = os.environ.get("GITHUB_SHA")
    if not sha:
        try:
            sha = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=os.path.dirname(CACHE_FILE),
                text=True, stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError):
            sha = None
    return sha


def get_cached_time(key):
    data = _read_cache()
    if data.get("_code") != _code_version():
        return None
    return data.get(key)


def set_cached_time(key, timestamp):
    data = _read_cache()
    data[key] = timestamp
    data["_code"] = _code_version()
    _write_cache(data)


def check_page_changed(notion, page_id, cache_key):
    page = notion.pages.retrieve(page_id)
    current = page["last_edited_time"]
    cached = get_cached_time(cache_key)

    if cached == current:
        return False, current
    return True, current


def check_databases_changed(notion, database_ids, cache_key):
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
