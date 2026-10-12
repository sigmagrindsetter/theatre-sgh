"""In-memory stand-in for the notion-client calls the panel makes."""
import copy
import uuid
from types import SimpleNamespace


def title(text):
    return {"type": "title", "title": [{"plain_text": text}] if text else []}


def people(*ids):
    return {"type": "people", "people": [{"object": "user", "id": i} for i in ids]}


def checkbox(value):
    return {"type": "checkbox", "checkbox": value}


def member(person_id, name, active=True, board=False):
    return {"Imię i Nazwisko": title(name), "Person": people(*([person_id] if person_id else [])),
            "Aktywny członek": checkbox(active), "Zarząd": checkbox(board)}


class FakeNotion:
    def __init__(self, page_size=2):
        self.page_size = page_size
        self.rows = {}
        self.calls = []
        self.databases = SimpleNamespace(query=self._query)

    def add(self, database_id, properties, page_id=None):
        page = {"object": "page", "id": page_id or str(uuid.uuid4()), "archived": False,
                "parent": {"type": "database_id", "database_id": database_id}, "properties": properties}
        self.rows.setdefault(database_id, []).append(page)
        return page

    def _query(self, database_id, filter=None, start_cursor=None, page_size=100, **kwargs):
        assert filter is None
        self.calls.append(("query", database_id, filter))
        hits = self.rows.get(database_id, [])
        start = int(start_cursor or 0)
        end = start + min(page_size, self.page_size)
        more = end < len(hits)
        return {"results": copy.deepcopy(hits[start:end]), "has_more": more, "next_cursor": str(end) if more else None}
