import csv
import io
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

import config
from fake_notion import FakeNotion, member, text, title
from panel import auth, ingest, main, notion_dbs, store
from panel.main import app
from shared.auth import NotionAuth

KANDYDACI, OCENY = "kandydaci-db", "oceny-db"
ALICE = "a0000000-0000-4000-8000-000000000001"    # reviewer
BOB = "b0000000-0000-4000-8000-000000000002"      # board member, also grades
CAROL = "c0000000-0000-4000-8000-000000000003"    # active, has not graded
DAN = "d0000000-0000-4000-8000-000000000004"      # not in Lista członków, Notion gives a name
EVE = "e0000000-0000-4000-8000-000000000005"      # inactive, Zarząd ticked
GHOST = "f0000000-0000-4000-8000-000000000006"    # not in Lista członków, no name from Notion
X = "11111111-1111-4111-8111-111111111111"
Y = "22222222-2222-4222-8222-222222222222"
Z = "33333333-3333-4333-8333-333333333333"        # nobody graded
W = "44444444-4444-4444-8444-444444444444"
GONE = "55555555-5555-4555-8555-555555555555"     # not in Kandydaci
INJECTION = '=HYPERLINK("https://evil.example")'
HEADER = ["Kandydat", "Email", "Średnia", "Liczba ocen", "Oceniający", "Ocena", "Komentarz", "Ostatni zapis"]


def candidate(name, email, czas):
    return {"Imię i nazwisko": title(name), "Email": {"type": "email", "email": email},
            "Czas": {"type": "date", "date": {"start": czas}}}


def grade(cid, uid, ocena, komentarz, saved="2026-10-12T10:05:00.000+02:00", name=None, relation=True):
    person = {"object": "user", "id": uid, **({"name": name} if name else {})}
    return {
        "Nazwa": title(f"{cid} — {uid}"),
        "Kandydat": {"type": "relation", "relation": [{"id": cid}] if relation else []},
        "Oceniający": {"type": "people", "people": [person]}, "Ocena": {"type": "number", "number": ocena},
        "Komentarz": text(komentarz), "Ostatni zapis": {"type": "date", "date": {"start": saved}},
        "Klucz": text(f"{cid}|{uid}"),
    }


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setattr(NotionAuth, "_client", fake)
    monkeypatch.setattr(notion_dbs, "_ids", {config.KANDYDACI_TITLE: KANDYDACI, config.OCENY_TITLE: OCENY})
    monkeypatch.setattr(auth, "_members", {"at": None, "by_person": {}})
    monkeypatch.setattr(store, "_candidates", {"at": None, "list": []})
    monkeypatch.setattr(store, "_graded", {"at": None, "by_user": {}})
    monkeypatch.setattr(ingest, "skipped_submissions", lambda: [])
    for uid, name, active, board in [(ALICE, "Alicja", True, False), (BOB, "Bartek", True, True),
                                     (CAROL, "Celina", True, False), (EVE, "Ewa", False, True)]:
        fake.add(config.MEMBERS_DATABASE_ID, member(uid, name, active=active, board=board))
    fake.add(KANDYDACI, candidate("Adam Pierwszy", "adam@example.com", "2026-09-02T09:30:00.000+02:00"), X)
    fake.add(KANDYDACI, candidate("Zofia Druga", "zofia@example.com", "2026-09-05T12:00:00.000+02:00"), Y)
    fake.add(KANDYDACI, candidate("Dawid Dyskowy", "dawid@example.com", "2026-09-07T18:00:00.000+02:00"), Z)
    fake.add(KANDYDACI, candidate("Wiktor Trzeci", "wiktor@example.com", "2026-09-08T18:00:00.000+02:00"), W)
    fake.add(OCENY, grade(X, ALICE, 3, "komentarz-alicji\nżółć", name="Alicja z Notion"))
    fake.add(OCENY, grade(X, BOB, 9, "SEKRET-BARTKA"))
    fake.add(OCENY, grade(Y, ALICE, 8, INJECTION))
    fake.add(OCENY, grade(Y, BOB, 7, "<img src=x onerror=alert(1)>"))
    fake.add(OCENY, grade(Y, DAN, None, "tylko komentarz", saved="2026-10-12T08:30:00.000Z", name="Dan Spoza"))
    fake.add(OCENY, grade(W, GHOST, 5, "-minus na start", relation=False))
    fake.add(OCENY, grade(GONE, ALICE, 1, "OSIEROCONA"))
    return fake


def as_user(uid, name):
    serializer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="panel-session")
    cookie = serializer.dumps({"uid": uid, "name": name, "email": f"{name.lower()}@example.com"})
    return TestClient(app, base_url="https://testserver", follow_redirects=False, cookies={"panel_session": cookie})


@pytest.fixture
def bob(notion):
    return as_user(BOB, "Bartek N")


def csv_rows(response):
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline="")))


@pytest.mark.parametrize("path", ["/zarzad", "/zarzad/export.csv"])
def test_board_pages_refuse_reviewers_and_inactive_members(notion, monkeypatch, path):
    calls = []
    monkeypatch.setattr(store, "all_grades", lambda: calls.append(1) or [])
    for uid, name in ((ALICE, "Alicja"), (CAROL, "Celina"), (EVE, "Ewa")):
        response = as_user(uid, name).get(path)
        assert response.status_code == 403
        assert "SEKRET-BARTKA" not in response.text and "zofia@example.com" not in response.text
    assert calls == []


@pytest.mark.parametrize("path", ["/zarzad", "/zarzad/export.csv"])
def test_board_pages_redirect_signed_out_users_to_login(notion, path):
    anonymous = TestClient(app, base_url="https://testserver", follow_redirects=False)
    response = anonymous.get(path)
    assert response.status_code == 303 and response.headers["location"] == "/auth/login"


def test_reviewer_pages_never_read_all_grades(notion, monkeypatch):
    def forbidden():
        raise AssertionError("all_grades outside /zarzad")

    monkeypatch.setattr(store, "all_grades", forbidden)
    for client in (as_user(ALICE, "Alicja"), as_user(BOB, "Bartek")):
        for path in ("/", f"/k/{X}"):
            assert client.get(path).status_code == 200


def test_all_grades_reads_every_reviewer_and_ignores_unknown_candidates(notion):
    grades = store.all_grades()
    assert sorted((g["candidate_id"], g["reviewer_id"], g["ocena"]) for g in grades) == sorted([
        (X, ALICE, 3), (X, BOB, 9), (Y, ALICE, 8), (Y, BOB, 7), (Y, DAN, None), (W, GHOST, 5)])
    assert all("OSIEROCONA" not in g["komentarz"] for g in grades)
    dan = next(g for g in grades if g["reviewer_id"] == DAN)
    assert dan["reviewer_name"] == "Dan Spoza" and dan["saved_local"] == "2026-10-12 10:30"


def test_all_grades_keeps_the_newest_of_twin_rows(notion):
    notion.add(OCENY, grade(X, ALICE, 10, "starszy bliźniak", saved="2026-10-12T09:00:00.000+02:00"))
    alice_x = [g for g in store.all_grades() if (g["candidate_id"], g["reviewer_id"]) == (X, ALICE)]
    assert [(g["ocena"], g["komentarz"]) for g in alice_x] == [(3, "komentarz-alicji\nżółć")]


def test_board_page_shows_averages_progress_and_every_reviewers_grades(bob):
    response = bob.get("/zarzad")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert 'href="/zarzad/export.csv"' in html
    ranking = [html.index(name) for name in ("Zofia Druga", "Adam Pierwszy", "Wiktor Trzeci", "Dawid Dyskowy")]
    assert ranking == sorted(ranking)
    assert f'<a href="/k/{Y}">Zofia Druga</a></td>' in html
    assert "<td>7.50</td><td>2</td><td>7–8</td>" in html
    assert "<td>6.00</td><td>2</td><td>3–9</td>" in html
    assert "<td>5.00</td><td>1</td><td>5–5</td>" in html
    assert '<td class="muted">brak ocen</td><td>0</td>' in html

    assert "<td>Alicja</td><td>2/4</td>" in html
    assert "<td>Bartek</td><td>2/4</td>" in html
    assert "<td>Celina</td><td>0/4</td>" in html
    assert "<td>Ewa</td>" not in html

    assert (html.index("<h3>Alicja</h3>") < html.index("<h3>Bartek</h3>") < html.index("<h3>Dan Spoza</h3>")
            < html.index(f"<h3>{GHOST}</h3>"))
    assert "Alicja z Notion" not in html
    assert "SEKRET-BARTKA" in html and "komentarz-alicji\nżółć" in html and "tylko komentarz" in html
    assert "<td>2026-10-12 10:05</td>" in html and "<td>2026-10-12 10:30</td>" in html
    assert "OSIEROCONA" not in html
    assert "<img src=x" not in html and "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "Brak pominiętych zgłoszeń." in html


def test_csv_export(bob):
    response = bob.get("/zarzad/export.csv")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    today = datetime.now(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d")
    assert response.headers["content-disposition"] == f'attachment; filename="oceny-{today}.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    body = response.content.decode("utf-8")
    assert body.startswith("﻿Kandydat,Email,Średnia,Liczba ocen,Oceniający,Ocena,Komentarz,Ostatni zapis\r\n")
    assert body.endswith("\r\n") and body.count("\r\n") == 8

    zofia, adam = ["Zofia Druga", "zofia@example.com", "7.50", "2"], ["Adam Pierwszy", "adam@example.com", "6.00", "2"]
    assert csv_rows(response) == [
        HEADER,
        zofia + ["Alicja", "8", "'" + INJECTION, "2026-10-12 10:05"],
        zofia + ["Bartek", "7", "<img src=x onerror=alert(1)>", "2026-10-12 10:05"],
        zofia + ["Dan Spoza", "", "tylko komentarz", "2026-10-12 10:30"],
        adam + ["Alicja", "3", "komentarz-alicji\nżółć", "2026-10-12 10:05"],
        adam + ["Bartek", "9", "SEKRET-BARTKA", "2026-10-12 10:05"],
        ["Wiktor Trzeci", "wiktor@example.com", "5.00", "1", GHOST, "5", "'-minus na start", "2026-10-12 10:05"],
        ["Dawid Dyskowy", "dawid@example.com", "", "0", "", "", "", ""],
    ]


@pytest.mark.parametrize("value, cell", [
    ("=1+1", "'=1+1"), ("+48 600", "'+48 600"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"), ("\tx", "'\tx"),
    ("\rx", "'\rx"), ("Zofia", "Zofia"), ("a=b", "a=b"), ("", ""), (None, ""), (7, "7"),
])
def test_csv_cells_are_guarded_against_formulas(value, cell):
    assert main._cell(value) == cell


def test_csv_with_candidates_but_no_grades(bob, notion):
    notion.rows[OCENY].clear()
    rows = csv_rows(bob.get("/zarzad/export.csv"))
    assert rows[0] == HEADER
    assert [r[0] for r in rows[1:]] == ["Adam Pierwszy", "Zofia Druga", "Dawid Dyskowy", "Wiktor Trzeci"]
    assert all(r[2:] == ["", "0", "", "", "", ""] for r in rows[1:])
    assert "Nikt jeszcze nie ocenił" in bob.get("/zarzad").text


def test_skipped_submissions_are_listed_as_text(bob, monkeypatch):
    monkeypatch.setattr(ingest, "skipped_submissions", lambda: [
        {"row": 7, "czas": "2026-09-05 14:00:00", "name": "Celina <b>Bezlinku</b>", "email": "celina@example.com",
         "filmik": "javascript:alert(1)"},
        {"row": 9, "czas": "2026-09-06 10:00:00", "name": "Jan", "email": "jan@example.com",
         "filmik": "https://example.com/<script>"},
    ])
    html = bob.get("/zarzad").text
    assert "nie są importowane" in html
    assert "<td>7</td><td>2026-09-05 14:00:00</td><td>Celina &lt;b&gt;Bezlinku&lt;/b&gt;</td>" in html
    assert '<td class="raw">javascript:alert(1)</td>' in html
    assert '<td class="raw">https://example.com/&lt;script&gt;</td>' in html
    assert 'href="javascript:' not in html and 'href="https://example.com' not in html


def test_sheet_failure_shows_a_message_and_logs_the_error_class_only(bob, monkeypatch, capsys):
    def broken():
        raise RuntimeError("sheet 1kBhtnYQ unreachable for celina@example.com")

    monkeypatch.setattr(ingest, "skipped_submissions", broken)
    response = bob.get("/zarzad")
    assert response.status_code == 200
    assert "Nie udało się odczytać arkusza zgłoszeń" in response.text
    assert "<td>7.50</td>" in response.text
    logged = capsys.readouterr().out
    assert "RuntimeError" in logged and "celina" not in logged and "1kBhtnYQ" not in logged
