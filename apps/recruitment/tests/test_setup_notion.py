from itertools import count
from types import SimpleNamespace

import httpx
import pytest
from notion_client.errors import APIResponseError

import config
import setup_notion

MEMBERS = {"Imię i Nazwisko": "title", "Person": "people", "Aktywny członek": "checkbox"}
ZAPISY = {"Imię i nazwisko": "title", "Wybór grupy": "select", "Auto-osoba": "people"}


class FakeNotion:
    """Just enough of the Notion API for setup_notion, including two-way relation sync and the Views API."""

    def __init__(self, members=MEMBERS, board_readable=True, zapisy=ZAPISY):
        self.members = members
        self.zapisy = zapisy
        self.board_readable = board_readable
        self.dbs = {}
        self.views = {}
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
        if database_id == config.ZAPISY_DATABASE_ID:
            if self.zapisy is None:
                raise APIResponseError(httpx.Response(404), "Could not find database", "object_not_found")
            return {"properties": {name: {"type": kind} for name, kind in self.zapisy.items()}}
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
            if kind == "relation" and spec[kind]["type"] == "dual_property":
                target = self.dbs[spec[kind]["database_id"]]["properties"]
                synced = {"id": f"p{next(self.ids)}", "type": "relation",
                          "relation": {"database_id": database_id, "type": "dual_property",
                                       "dual_property": {"synced_property_id": prop["id"],
                                                         "synced_property_name": name}}}
                target["Related to Oceny (Kandydat)"] = synced
                prop[kind]["dual_property"] = {"synced_property_id": synced["id"],
                                               "synced_property_name": "Related to Oceny (Kandydat)"}


    def request(self, path, method, query=None, body=None):
        if method == "POST" and path == "views":
            self.writes.append(("view", body["name"]))
            self.views[f"view-{next(self.ids)}"] = body
            return body
        if path == "views":
            return {"results": [{"object": "view", "id": view_id} for view_id, view in self.views.items()
                                if view["database_id"] == query["database_id"]], "has_more": False}
        kind, _, object_id = path.partition("/")
        if kind == "views":
            return self.views[object_id]
        assert kind == "databases" and method == "GET"
        return {"id": object_id, "data_sources": [{"id": f"ds-{object_id}"}]}


def run_main(monkeypatch, fake):
    monkeypatch.setattr(setup_notion, "NotionAuth", SimpleNamespace(get_client=lambda: fake))
    monkeypatch.setattr(setup_notion, "views_client", lambda: fake)
    return setup_notion.main()


def database_id(fake, title):
    return next(b["id"] for b in fake.children if b["child_database"]["title"] == title)


def schema(fake, title):
    return fake.dbs[database_id(fake, title)]["properties"]


def views(fake):
    return {view["name"]: view for view in fake.views.values()}


def test_first_run_creates_schema(monkeypatch, capsys):
    fake = FakeNotion()
    assert run_main(monkeypatch, fake) == 0

    kandydaci, oceny = schema(fake, "Kandydaci"), schema(fake, "Oceny")
    assert {name: p["type"] for name, p in kandydaci.items()} == {
        "Imię i nazwisko": "title", "Email": "email", "Czas": "date", "Uczelnia": "rich_text",
        "Rok studiów": "select", "Obszary": "multi_select", "Filmik": "url", "Filmik znormalizowany": "url",
        "Status filmiku": "select", "Zdjęcie": "files", "II etap": "checkbox", "Oceny": "relation",
        "Średnia": "rollup", "Liczba ocen": "rollup",
    }
    oceny_ii = schema(fake, "Oceny II etap")
    for grades in (oceny, oceny_ii):
        assert {name: p["type"] for name, p in grades.items()} == {
            "Nazwa": "title", "Kandydat": "relation", "Oceniający": "people", "Ocena": "number",
            "Komentarz": "rich_text", "Ostatni zapis": "date", "Klucz": "rich_text",
        }
        assert grades["Kandydat"]["relation"]["database_id"] == database_id(fake, "Kandydaci")
    assert oceny["Kandydat"]["relation"]["type"] == "dual_property"
    assert oceny_ii["Kandydat"]["relation"]["type"] == "single_property"
    assert kandydaci["Średnia"]["rollup"]["function"] == "average"
    assert kandydaci["Liczba ocen"]["rollup"]["function"] == "not_empty"
    assert kandydaci["Liczba ocen"]["rollup"]["relation_property_name"] == "Oceny"
    out = capsys.readouterr().out
    assert "created database Kandydaci" in out and "db-1" in out
    assert f"Oceny II etap: {database_id(fake, 'Oceny II etap')}" in out


def test_first_run_creates_board_views(monkeypatch):
    fake = FakeNotion()
    assert run_main(monkeypatch, fake) == 0

    oceny_id, kandydaci_id = database_id(fake, "Oceny"), database_id(fake, "Kandydaci")
    oceny, created = schema(fake, "Oceny"), views(fake)
    assert sorted(created) == ["ranking", "wg kandydata", "wg oceniającego"]
    for name, parent, group, sort in [("wg oceniającego", oceny_id, ("person", "Oceniający"), "Ocena"),
                                      ("wg kandydata", oceny_id, ("relation", "Kandydat"), "Ocena"),
                                      ("ranking", kandydaci_id, None, "Średnia")]:
        view = created[name]
        assert (view["database_id"], view["data_source_id"], view["type"]) == (parent, f"ds-{parent}", "table")
        assert view["sorts"] == [{"property": sort, "direction": "descending"}]
        if group:
            assert view["configuration"]["group_by"] == {
                "type": group[0], "property_id": oceny[group[1]]["id"], "sort": {"type": "ascending"}}
        else:
            assert "configuration" not in view


def test_only_missing_views_are_created(monkeypatch):
    fake = FakeNotion()
    run_main(monkeypatch, fake)
    view_id = next(i for i, view in fake.views.items() if view["name"] == "wg kandydata")
    del fake.views[view_id]
    views(fake)["ranking"]["sorts"] = [{"property": "Czas", "direction": "ascending"}]  # edited by the board
    fake.writes.clear()

    assert run_main(monkeypatch, fake) == 0
    assert fake.writes == [("view", "wg kandydata")]
    assert views(fake)["ranking"]["sorts"] == [{"property": "Czas", "direction": "ascending"}]


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


@pytest.mark.parametrize("zapisy, message", [
    (None, "Connections"),
    ({k: v for k, v in ZAPISY.items() if k != "Wybór grupy"}, "'Wybór grupy' must be a select"),
])
def test_unusable_interview_signups_exit(monkeypatch, capsys, zapisy, message):
    fake = FakeNotion(zapisy=zapisy)
    assert run_main(monkeypatch, fake) == 1
    assert fake.writes == []
    assert message in capsys.readouterr().out


def test_unreadable_board_page_exits(monkeypatch, capsys):
    fake = FakeNotion(board_readable=False)
    assert run_main(monkeypatch, fake) == 1
    assert fake.writes == []
    assert "Connections" in capsys.readouterr().out
