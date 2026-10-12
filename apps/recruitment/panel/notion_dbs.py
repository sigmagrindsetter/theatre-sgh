"""Kandydaci / Oceny database IDs, found by title under the board page and cached per process."""
import config
from shared.auth import NotionAuth

_ids = {}


def database_id(title):
    if title not in _ids:
        client = NotionAuth.get_client()
        cursor = None
        while True:
            kwargs = {"block_id": config.BOARD_PAGE_ID}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = client.blocks.children.list(**kwargs)
            for block in response["results"]:
                if block["type"] == "child_database":
                    _ids.setdefault(block["child_database"]["title"], block["id"])
            if not response.get("has_more"):
                break
            cursor = response["next_cursor"]
    if title not in _ids:
        raise LookupError(f"Database '{title}' not found under the board page; run panel-setup.yml")
    return _ids[title]
