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
