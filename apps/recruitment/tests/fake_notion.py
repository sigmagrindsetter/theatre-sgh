"""In-memory stand-in for the notion-client calls the panel makes."""
import copy
import uuid
from types import SimpleNamespace

from notion_client.errors import APIErrorCode, APIResponseError


def title(text):
    return {"type": "title", "title": [{"plain_text": text}] if text else []}


def text(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value}] if value else []}


def people(*ids):
    return {"type": "people", "people": [{"object": "user", "id": i} for i in ids]}


def checkbox(value):
    return {"type": "checkbox", "checkbox": value}


def member(person_id, name, active=True, board=False):
    return {"Imię i Nazwisko": title(name), "Person": people(*([person_id] if person_id else [])),
            "Aktywny członek": checkbox(active), "Zarząd": checkbox(board)}


def _stored(props):
    """Request-shaped property values -> response-shaped ones (rich text gains plain_text)."""
    out = {}
    for name, value in props.items():
        value = copy.deepcopy(value)
        for kind in ("title", "rich_text"):
            if kind in value:
                value[kind] = [{"plain_text": item["text"]["content"]} for item in value[kind]]
        out[name] = value
    return out


def _plain(prop):
    return "".join(t["plain_text"] for t in prop.get("title") or prop.get("rich_text") or [])


def _matches(page, flt):
    if flt is None:
        return True
    prop = page["properties"].get(flt["property"], {})
    if "people" in flt:
        return any(p["id"] == flt["people"]["contains"] for p in prop.get("people", []))
    if "rich_text" in flt:
        return _plain(prop) == flt["rich_text"]["equals"]
    if flt.get("number") == {"is_not_empty": True}:
        return prop.get("number") is not None
    raise AssertionError(f"unsupported filter {flt}")


def error(status, code):
    return APIResponseError(SimpleNamespace(status_code=status, headers={}, text=""), code.value, code)


class FakeNotion:
    """Rows created through pages.create stay invisible to queries until settle(), like Notion's index lag."""

    def __init__(self, page_size=2):
        self.page_size = page_size
        self.rows = {}
        self.calls = []
        self.lagging = set()
        self.databases = SimpleNamespace(query=self._query)
        self.pages = SimpleNamespace(create=self._create, update=self._update, retrieve=self._retrieve)

    def add(self, database_id, properties, page_id=None):
        page = {"object": "page", "id": page_id or str(uuid.uuid4()), "archived": False,
                "parent": {"type": "database_id", "database_id": database_id}, "properties": properties}
        self.rows.setdefault(database_id, []).append(page)
        return page

    def settle(self):
        self.lagging.clear()

    def calls_of(self, kind):
        return [call for call in self.calls if call[0] == kind]

    def _query(self, database_id, filter=None, start_cursor=None, page_size=100, **kwargs):
        self.calls.append(("query", database_id, filter))
        hits = [p for p in self.rows.get(database_id, [])
                if p["id"] not in self.lagging and not p["archived"] and _matches(p, filter)]
        start = int(start_cursor or 0)
        end = start + min(page_size, self.page_size)
        more = end < len(hits)
        return {"results": copy.deepcopy(hits[start:end]), "has_more": more, "next_cursor": str(end) if more else None}

    def _page(self, page_id):
        for pages in self.rows.values():
            for page in pages:
                if page["id"] == page_id:
                    return page
        raise error(404, APIErrorCode.ObjectNotFound)

    def _create(self, parent, properties, **kwargs):
        self.calls.append(("create", parent["database_id"], properties))
        page = self.add(parent["database_id"], _stored(properties))
        self.lagging.add(page["id"])
        return copy.deepcopy(page)

    def _update(self, page_id, properties, **kwargs):
        self.calls.append(("update", page_id, properties))
        page = self._page(page_id)
        if page["archived"]:
            raise error(400, APIErrorCode.ValidationError)
        page["properties"].update(_stored(properties))
        return copy.deepcopy(page)

    def _retrieve(self, page_id, **kwargs):
        self.calls.append(("retrieve", page_id, None))
        return copy.deepcopy(self._page(page_id))
