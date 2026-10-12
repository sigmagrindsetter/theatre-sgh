from itertools import count
from types import SimpleNamespace

import httpx
from notion_client.errors import APIResponseError

import config
import setup_notion

MEMBERS = {"Imię i Nazwisko": "title", "Person": "people", "Aktywny członek": "checkbox", "Zarząd": "checkbox"}


class FakeNotion:
    """Just enough of the Notion API for setup_notion, including two-way relation sync."""

    def __init__(self, members=MEMBERS, board_readable=True):
        self.members = members
        self.board_readable = board_readable
        self.dbs = {}
        self.children = []
        self.writes = []
        self.ids = count(1)
        self.pages = SimpleNamespace(retrieve=self.retrieve_page)
        self.databases = SimpleNamespace(retrieve=self.retrieve_db, create=self.create_db, update=self.update_db)
        self.blocks = SimpleNamespace(children=SimpleNamespace(list=self.list_children))

    def retrieve_page(self, page_id):
        if not self.board_readable:
            raise APIResponseError(httpx.Response(404), "Could not find page", "object_not_found")
        return {"id": page_id, "parent": {"type": "page_id", "page_id": config.PARENT_PAGE_ID}}

    def retrieve_db(self, database_id):
        if database_id == config.MEMBERS_DATABASE_ID:
            return {"properties": {name: {"type": kind} for name, kind in self.members.items()}}
        return self.dbs[database_id]

    def list_children(self, block_id, **kwargs):
        assert block_id == config.BOARD_PAGE_ID
        return {"results": self.children, "has_more": False}

    def create_db(self, parent, title, properties):
        self.writes.append(("create", title[0]["text"]["content"]))
        database_id = f"db-{next(self.ids)}"
        self.dbs[database_id] = {"id": database_id, "properties": {}}
        self.children.append({"type": "child_database", "id": database_id,
                              "child_database": {"title": title[0]["text"]["content"]}})
        self.add(database_id, properties)
        return self.dbs[database_id]

    def update_db(self, database_id, properties):
        self.writes.append(("update", database_id, sorted(properties)))
        existing = self.dbs[database_id]["properties"]
        for key, spec in properties.items():
            renamed = next((name for name, p in existing.items() if p["id"] == key), None)
            if renamed:
                existing[spec["name"]] = existing.pop(renamed)
            else:
                self.add(database_id, {key: spec})
        return self.dbs[database_id]

    def add(self, database_id, properties):
        for name, spec in properties.items():
            kind = next(iter(spec))
            prop = {"id": f"p{next(self.ids)}", "type": kind, kind: dict(spec[kind])}
            self.dbs[database_id]["properties"][name] = prop
            if kind == "relation":
                target = self.dbs[spec[kind]["database_id"]]["properties"]
                synced = {"id": f"p{next(self.ids)}", "type": "relation",
                          "relation": {"database_id": database_id, "type": "dual_property",
                                       "dual_property": {"synced_property_id": prop["id"],
                                                         "synced_property_name": name}}}
                target["Related to Oceny (Kandydat)"] = synced
                prop[kind]["dual_property"] = {"synced_property_id": synced["id"],
                                               "synced_property_name": "Related to Oceny (Kandydat)"}


def run_main(monkeypatch, fake):
    monkeypatch.setattr(setup_notion, "NotionAuth", SimpleNamespace(get_client=lambda: fake))
    return setup_notion.main()


def schema(fake, title):
    database_id = next(b["id"] for b in fake.children if b["child_database"]["title"] == title)
    return fake.dbs[database_id]["properties"]


def test_first_run_creates_schema(monkeypatch, capsys):
    fake = FakeNotion()
    assert run_main(monkeypatch, fake) == 0

    kandydaci, oceny = schema(fake, "Kandydaci"), schema(fake, "Oceny")
    assert {name: p["type"] for name, p in kandydaci.items()} == {
        "Imię i nazwisko": "title", "Email": "email", "Czas": "date", "Uczelnia": "rich_text",
        "Rok studiów": "select", "Obszary": "multi_select", "Filmik": "url", "Filmik znormalizowany": "url",
        "Status filmiku": "select", "Zdjęcie": "files", "Oceny": "relation", "Średnia": "rollup",
        "Liczba ocen": "rollup",
    }
    assert {name: p["type"] for name, p in oceny.items()} == {
        "Nazwa": "title", "Kandydat": "relation", "Oceniający": "people", "Ocena": "number",
        "Komentarz": "rich_text", "Ostatni zapis": "date", "Klucz": "rich_text",
    }
    assert oceny["Kandydat"]["relation"]["type"] == "dual_property"
    assert kandydaci["Średnia"]["rollup"]["function"] == "average"
    assert kandydaci["Liczba ocen"]["rollup"]["function"] == "not_empty"
    assert kandydaci["Liczba ocen"]["rollup"]["relation_property_name"] == "Oceny"
    out = capsys.readouterr().out
    assert "created database Kandydaci" in out and "db-1" in out


def test_second_run_writes_nothing(monkeypatch, capsys):
    fake = FakeNotion()
    run_main(monkeypatch, fake)
    fake.writes.clear()
    capsys.readouterr()

    assert run_main(monkeypatch, fake) == 0
    assert fake.writes == []
    assert "Nothing changed" in capsys.readouterr().out


def test_existing_database_only_gets_missing_properties(monkeypatch):
    fake = FakeNotion()
    run_main(monkeypatch, fake)
    kandydaci = schema(fake, "Kandydaci")
    del kandydaci["Uczelnia"]
    kandydaci["Rok studiów"]["type"] = "rich_text"  # changed by the board: left alone
    fake.writes.clear()

    assert run_main(monkeypatch, fake) == 0
    assert fake.writes == [("update", "db-1", ["Uczelnia"])]
    assert kandydaci["Rok studiów"]["type"] == "rich_text"


def test_missing_zarzad_checkbox_exits(monkeypatch, capsys):
    fake = FakeNotion(members={k: v for k, v in MEMBERS.items() if k != "Zarząd"})
    assert run_main(monkeypatch, fake) == 1
    assert fake.writes == []
    assert "Zarząd" in capsys.readouterr().out


def test_unreadable_board_page_exits(monkeypatch, capsys):
    fake = FakeNotion(board_readable=False)
    assert run_main(monkeypatch, fake) == 1
    assert fake.writes == []
    assert "Connections" in capsys.readouterr().out
