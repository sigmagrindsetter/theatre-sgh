#!/usr/bin/env python3
"""
Notion schema of the review panel (REVIEW_PANEL_SPEC.md §4, §11): Kandydaci, Oceny and Oceny II etap
under the board page
Idempotent: creates missing databases, properties and board views; never deletes, renames or retypes
anything except the one-time rename of the synced relation property on Kandydaci to "Oceny"
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx
from notion_client import Client
from notion_client.errors import APIResponseError

import config
from shared.auth import NotionAuth, RetryingTransport

KANDYDACI_PROPERTIES = {
    "Imię i nazwisko": {"title": {}},
    "Email": {"email": {}},
    "Czas": {"date": {}},
    "Uczelnia": {"rich_text": {}},
    "Rok studiów": {"select": {}},
    "Obszary": {"multi_select": {}},
    "Filmik": {"url": {}},
    "Filmik znormalizowany": {"url": {}},
    "Status filmiku": {"select": {"options": [{"name": "youtube"}, {"name": "drive"}]}},
    "Zdjęcie": {"files": {}},
    "II etap": {"checkbox": {}},
}

OCENY_PROPERTIES = {
    "Nazwa": {"title": {}},
    "Oceniający": {"people": {}},
    "Ocena": {"number": {"format": "number"}},
    "Komentarz": {"rich_text": {}},
    "Ostatni zapis": {"date": {}},
    "Klucz": {"rich_text": {}},
}

RELATION = "Kandydat"  # on Oceny; its synced twin on Kandydaci is SYNCED
SYNCED = "Oceny"

ROLLUPS = {
    "Średnia": {"rollup": {"relation_property_name": SYNCED, "rollup_property_name": "Ocena",
                           "function": "average"}},
    "Liczba ocen": {"rollup": {"relation_property_name": SYNCED, "rollup_property_name": "Ocena",
                               "function": "not_empty"}},
}

MEMBERS_PROPERTIES = {"Person": "people", "Aktywny członek": "checkbox"}
ZAPISY_PROPERTIES = {"Imię i nazwisko": "title", "Wybór grupy": "select"}

# The Views API needs Notion-Version 2025-09-03 or later; everything else stays on notion-client's default
VIEWS_API_VERSION = "2026-03-11"

# Board views (U6), created once by name: a view the board later edits in Notion is left alone
VIEWS = {
    config.OCENY_TITLE: [
        {"name": "wg oceniającego", "group_by": ("person", "Oceniający"), "sort": "Ocena"},
        {"name": "wg kandydata", "group_by": ("relation", RELATION), "sort": "Ocena"},
    ],
    config.KANDYDACI_TITLE: [{"name": "ranking", "sort": "Średnia"}],
}


class SetupError(Exception):
    pass


def plain(notion_id):
    return notion_id.replace("-", "")


def check_board_page(client):
    try:
        page = client.pages.retrieve(config.BOARD_PAGE_ID)
    except APIResponseError as error:
        raise SetupError(
            f"Board page {config.BOARD_PAGE_ID} is not readable ({error.code}). Restricting the page "
            "can drop the integration: open the page in Notion → ••• → Connections → add theatre-sgh."
        )
    parent = page["parent"].get("page_id") or ""
    if plain(parent) != plain(config.PARENT_PAGE_ID):
        raise SetupError(f"Board page {config.BOARD_PAGE_ID} is not a child of {config.PARENT_PAGE_ID}")


def check_members(client):
    try:
        properties = client.databases.retrieve(config.MEMBERS_DATABASE_ID)["properties"]
    except APIResponseError as error:
        raise SetupError(f"Lista członków is not readable ({error.code})")
    for name, kind in MEMBERS_PROPERTIES.items():
        if properties.get(name, {}).get("type") != kind:
            raise SetupError(f"Lista członków: '{name}' must be a {kind} property")


def check_zapisy(client):
    try:
        properties = client.databases.retrieve(config.ZAPISY_DATABASE_ID)["properties"]
    except APIResponseError as error:
        raise SetupError(f"Interview sign-ups {config.ZAPISY_DATABASE_ID} are not readable ({error.code}): "
                         "open the database in Notion → ••• → Connections → add theatre-sgh")
    for name, kind in ZAPISY_PROPERTIES.items():
        if properties.get(name, {}).get("type") != kind:
            raise SetupError(f"Interview sign-ups: '{name}' must be a {kind} property")


def child_databases(client):
    found, cursor = {}, None
    while True:
        kwargs = {"block_id": config.BOARD_PAGE_ID}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.blocks.children.list(**kwargs)
        for block in response["results"]:
            if block["type"] == "child_database":
                found.setdefault(block["child_database"]["title"], block["id"])
        if not response.get("has_more"):
            return found
        cursor = response["next_cursor"]


def views_client():
    return Client(auth=os.environ["NOTION_API_TOKEN"], notion_version=VIEWS_API_VERSION,
                  client=httpx.Client(transport=RetryingTransport()))


class Setup:
    def __init__(self, client, views):
        self.client = client
        self.views = views
        self.changes = []

    def create(self, title, properties):
        database = self.client.databases.create(
            parent={"type": "page_id", "page_id": config.BOARD_PAGE_ID},
            title=[{"type": "text", "text": {"content": title}}],
            properties=properties,
        )
        self.changes.append(f"created database {title}")
        return database

    def add_missing(self, database, title, wanted):
        existing = database["properties"]
        for name, spec in wanted.items():
            kind = next(iter(spec))
            if name in existing and existing[name]["type"] != kind:
                print(f"  ! {title}.{name} is {existing[name]['type']}, expected {kind}; left as is")
        missing = {name: spec for name, spec in wanted.items() if name not in existing}
        for name, spec in missing.items():
            if "title" in spec:
                raise SetupError(f"{title}: the title property must be named '{name}'")
        if not missing:
            return database
        database = self.client.databases.update(database["id"], properties=missing)
        self.changes.append(f"{title}: added {', '.join(missing)}")
        return database

    def ensure(self, databases, title, properties):
        if title in databases:
            database = self.client.databases.retrieve(databases[title])
            return self.add_missing(database, title, properties)
        return self.create(title, properties)

    def run(self):
        databases = child_databases(self.client)
        kandydaci = self.ensure(databases, config.KANDYDACI_TITLE, KANDYDACI_PROPERTIES)
        relation = {RELATION: {"relation": {"database_id": kandydaci["id"], "type": "dual_property",
                                            "dual_property": {}}}}
        oceny = self.ensure(databases, config.OCENY_TITLE, {**OCENY_PROPERTIES, **relation})

        link = oceny["properties"][RELATION]
        if link["type"] != "relation" or plain(link["relation"]["database_id"]) != plain(kandydaci["id"]):
            raise SetupError(f"Oceny.{RELATION} must be a relation to Kandydaci {kandydaci['id']}")
        if SYNCED not in kandydaci["properties"]:
            kandydaci = self.client.databases.retrieve(kandydaci["id"])
        if SYNCED not in kandydaci["properties"]:
            synced = link["relation"].get("dual_property", {}).get("synced_property_id")
            if not synced:
                raise SetupError(f"Oceny.{RELATION} is not a two-way relation")
            kandydaci = self.client.databases.update(kandydaci["id"], properties={synced: {"name": SYNCED}})
            self.changes.append(f"Kandydaci: synced relation renamed to {SYNCED}")
        kandydaci = self.add_missing(kandydaci, config.KANDYDACI_TITLE, ROLLUPS)
        self.ensure_views(oceny, config.OCENY_TITLE)
        self.ensure_views(kandydaci, config.KANDYDACI_TITLE)

        # one-way: a second synced relation and rollups on Kandydaci would only clutter it
        one_way = {RELATION: {"relation": {"database_id": kandydaci["id"], "type": "single_property",
                                           "single_property": {}}}}
        oceny_ii = self.ensure(databases, config.OCENY_II_TITLE, {**OCENY_PROPERTIES, **one_way})
        link = oceny_ii["properties"][RELATION]
        if link["type"] != "relation" or plain(link["relation"]["database_id"]) != plain(kandydaci["id"]):
            raise SetupError(f"{config.OCENY_II_TITLE}.{RELATION} must be a relation to Kandydaci {kandydaci['id']}")
        return kandydaci["id"], oceny["id"], oceny_ii["id"]

    def ensure_views(self, database, title):
        """Create the board views missing by name, as tables sorted descending, grouped where given."""
        api = self.views
        refs = api.request(path="views", method="GET", query={"database_id": database["id"]})["results"]
        existing = {api.request(path=f"views/{ref['id']}", method="GET")["name"] for ref in refs}
        source = api.request(path=f"databases/{database['id']}", method="GET")["data_sources"][0]["id"]
        for view in VIEWS[title]:
            if view["name"] in existing:
                continue
            body = {"database_id": database["id"], "data_source_id": source, "name": view["name"], "type": "table",
                    "sorts": [{"property": view["sort"], "direction": "descending"}]}
            if "group_by" in view:
                kind, name = view["group_by"]
                body["configuration"] = {"type": "table", "group_by": {
                    "type": kind, "property_id": database["properties"][name]["id"], "sort": {"type": "ascending"}}}
            api.request(path="views", method="POST", body=body)
            self.changes.append(f"{title}: created view '{view['name']}'")


def main():
    client = NotionAuth.get_client()
    try:
        check_board_page(client)
        check_members(client)
        check_zapisy(client)
        setup = Setup(client, views_client())
        kandydaci_id, oceny_id, oceny_ii_id = setup.run()
    except (SetupError, APIResponseError) as error:
        print(f"✗ {error}")
        return 1
    for change in setup.changes:
        print(f"  + {change}")
    if not setup.changes:
        print("Nothing changed: schema already up to date")
    print(f"{config.KANDYDACI_TITLE}: {kandydaci_id}")
    print(f"{config.OCENY_TITLE}: {oceny_id}")
    print(f"{config.OCENY_II_TITLE}: {oceny_ii_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
