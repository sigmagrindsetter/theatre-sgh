import os
import threading
import time
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer

import config
from notion_client.errors import APIErrorCode, APIResponseError

from fake_notion import FakeNotion, error, member, people, text, title
from panel import auth, notion_dbs, store
from panel.main import app
from shared.auth import NotionAuth

KANDYDACI, OCENY = "kandydaci-db", "oceny-db"
ALICE = "a0000000-0000-4000-8000-000000000001"  # reviewer
BOB = "b0000000-0000-4000-8000-000000000002"    # board member, also grades
CAROL = "c0000000-0000-4000-8000-000000000003"  # active, has not graded
EVE = "e0000000-0000-4000-8000-000000000005"    # inactive
X = "11111111-1111-4111-8111-111111111111"
Y = "22222222-2222-4222-8222-222222222222"
Z = "33333333-3333-4333-8333-333333333333"
ALICE_X_ROW = "aaaa0000-0000-4000-8000-00000000000a"
BOB_X_ROW = "bbbb0000-0000-4000-8000-00000000000b"
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}


def candidate_props(name, czas, video, status, rok="I SL", obszary=("Aktorstwo", "Scenografia")):
    normalized = f"https://www.youtube.com/watch?v={video}" if status == "youtube" else video
    return {
        "Imię i nazwisko": title(name), "Email": {"type": "email", "email": f"{name[:3].lower()}@example.com"},
        "Czas": {"type": "date", "date": {"start": czas}}, "Uczelnia": text("SGH"),
        "Rok studiów": {"type": "select", "select": {"name": rok}},
        "Obszary": {"type": "multi_select", "multi_select": [{"name": o} for o in obszary]},
        "Filmik": {"type": "url", "url": f"https://youtu.be/{video}?si=x" if status == "youtube" else video},
        "Filmik znormalizowany": {"type": "url", "url": normalized},
        "Status filmiku": {"type": "select", "select": {"name": status}},
    }


def grade_props(cid, uid, ocena, komentarz, reviewers=None):
    return {
        "Nazwa": title(f"{cid} — {uid}"), "Kandydat": {"type": "relation", "relation": [{"id": cid}]},
        "Oceniający": people(*(reviewers or [uid])), "Ocena": {"type": "number", "number": ocena},
        "Komentarz": text(komentarz), "Ostatni zapis": {"type": "date", "date": {"start": "2026-10-12T10:05:00.000+02:00"}},
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
    monkeypatch.setattr(store, "_page_ids", {})
    monkeypatch.setattr(store, "_locks", {})
    for uid, name, active, board in [(ALICE, "Alicja", True, False), (BOB, "Bartek", True, True),
                                     (CAROL, "Celina", True, False), (EVE, "Ewa", False, False)]:
        fake.add(config.MEMBERS_DATABASE_ID, member(uid, name, active=active, board=board))
    fake.add(KANDYDACI, candidate_props("Zofia Druga", "2026-09-05T12:00:00.000+02:00", "bbbbbbbbbbb", "youtube"), Y)
    fake.add(KANDYDACI, candidate_props("Adam Pierwszy", "2026-09-02T09:30:00.000+02:00", "aaaaaaaaaaa", "youtube"), X)
    fake.add(KANDYDACI, candidate_props("Dawid Dyskowy", "2026-09-07T18:00:00.000+02:00",
                                        "https://drive.google.com/drive/folders/abc", "drive"), Z)
    fake.add(OCENY, grade_props(X, ALICE, 3, "komentarz-alicji"), ALICE_X_ROW)
    fake.add(OCENY, grade_props(X, BOB, 9, "SEKRET-BARTKA"), BOB_X_ROW)
    return fake


def session(uid, name):
    serializer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="panel-session")
    return serializer.dumps({"uid": uid, "name": name, "email": f"{name.lower()}@example.com"})


def as_user(uid, name):
    return TestClient(app, base_url="https://testserver", follow_redirects=False,
                      cookies={"panel_session": session(uid, name)})


@pytest.fixture
def alice(notion):
    return as_user(ALICE, "Alicja N")


def row(notion, page_id):
    return next(p for p in notion.rows[OCENY] if p["id"] == page_id)


def test_list_shows_candidates_sorted_with_own_status_and_progress(alice):
    response = alice.get("/")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert html.index("Adam Pierwszy") < html.index("Zofia Druga") < html.index("Dawid Dyskowy")
    assert "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg" in html
    assert f'href="/k/{X}"' in html
    assert "Aktorstwo, Scenografia" in html and "I SL" in html
    assert "3/10" in html and html.count("brak oceny") == 2
    assert "<td>Alicja</td><td>1/3</td>" in html
    assert "<td>Bartek</td><td>1/3</td>" in html
    assert "<td>Celina</td><td>0/3</td>" in html
    assert "Ewa" not in html
    assert 'href="/zarzad"' not in html
    assert 'action="/auth/logout"' in html


def test_board_member_sees_board_link(notion):
    assert 'href="/zarzad"' in as_user(BOB, "Bartek N").get("/").text


def test_card_restores_own_grade(alice):
    response = alice.get(f"/k/{X}")
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "Adam Pierwszy" in html and "ada@example.com" in html and "https://youtu.be/aaaaaaaaaaa?si=x" in html
    assert 'src="https://www.youtube-nocookie.com/embed/aaaaaaaaaaa"' in html
    assert f'data-id="{X}"' in html and 'data-ocena="3"' in html
    assert html.count('aria-pressed="true"') == 1
    assert '<button type="button" data-ocena="3" aria-pressed="true">' in html
    assert ">komentarz-alicji</textarea>" in html
    assert "Zapisano 10:05" in html
    assert 'maxlength="2000"' in html
    assert '<script src="/static/app.js" defer>' in html


def test_drive_candidate_gets_a_link_not_a_player(alice):
    html = alice.get(f"/k/{Z}").text
    assert "youtube-nocookie" not in html
    assert 'href="https://drive.google.com/drive/folders/abc"' in html


def test_drive_file_gets_the_drive_player_and_thumbnail(notion, alice):
    page = notion.add(KANDYDACI, candidate_props("Daria Plikowa", "2026-09-08T18:00:00.000+02:00",
                                                 "https://drive.google.com/file/d/1FileIdAbcXyz/view", "drive"))
    html = alice.get(f"/k/{page['id']}").text
    assert 'src="https://drive.google.com/file/d/1FileIdAbcXyz/preview"' in html
    assert 'href="https://drive.google.com/file/d/1FileIdAbcXyz/view"' in html
    assert 'src="https://drive.google.com/thumbnail?id=1FileIdAbcXyz&amp;sz=w480"' in alice.get("/").text


def test_privacy_reads_never_show_another_reviewers_grade(alice):
    for path in ("/", f"/k/{X}", f"/k/{X}?uid={BOB}", f"/k/{X}?user={BOB}"):
        html = alice.get(path).text
        assert "SEKRET-BARTKA" not in html
        assert "9/10" not in html
        assert 'data-ocena="9" aria-pressed="true"' not in html
        if path != "/":
            assert f'<section id="grade" data-id="{X}" data-ocena="3">' in html


def test_privacy_put_only_touches_own_row(alice, notion):
    bob_before = row(notion, BOB_X_ROW)["properties"].copy()
    response = alice.put(f"/api/oceny/{X}", json={"ocena": 5, "komentarz": "nowy"}, headers=SAME_ORIGIN)
    assert response.status_code == 200
    [(_, page_id, props)] = notion.calls_of("update")
    assert page_id == ALICE_X_ROW
    assert props["Klucz"]["rich_text"][0]["text"]["content"] == f"{X}|{ALICE}"
    assert props["Oceniający"] == {"people": [{"id": ALICE}]}
    assert row(notion, BOB_X_ROW)["properties"] == bob_before
    assert notion.calls_of("create") == []


@pytest.mark.parametrize("method, path, body", [
    ("put", f"/api/oceny/{X}", {"ocena": 1, "komentarz": "x", "uid": BOB}),
    ("put", f"/api/oceny/{X}", {"ocena": 1, "komentarz": "x", "Klucz": f"{X}|{BOB}"}),
    ("put", f"/api/oceny/{X}", {"ocena": 1, "komentarz": "x", "Oceniający": BOB}),
    ("put", f"/api/oceny/{X}%7C{BOB}", {"ocena": 1, "komentarz": "x"}),
    ("put", f"/api/oceny/{BOB_X_ROW}", {"ocena": 1, "komentarz": "x"}),
    ("put", f"/api/oceny/{X}?uid={BOB}", {"ocena": 1, "komentarz": "x"}),
    ("get", f"/k/{BOB_X_ROW}", None),
    ("get", f"/k/{X}%7C{BOB}", None),
])
def test_privacy_no_request_shape_reaches_another_reviewers_row(alice, notion, method, path, body):
    bob_before = row(notion, BOB_X_ROW)["properties"].copy()
    kwargs = {"json": body} if body is not None else {}
    response = getattr(alice, method)(path, headers=SAME_ORIGIN, **kwargs)
    assert response.status_code in (200, 404, 422)
    assert "SEKRET-BARTKA" not in response.text
    assert row(notion, BOB_X_ROW)["properties"] == bob_before
    assert all(call[1] != BOB_X_ROW for call in notion.calls)
    assert notion.calls_of("create") == []


def test_privacy_rows_are_rechecked_by_klucz(alice, notion):
    notion.add(OCENY, grade_props(Y, BOB, 8, "SEKRET-2", reviewers=[BOB, ALICE]))
    assert "SEKRET-2" not in alice.get(f"/k/{Y}").text
    assert store.my_grades(auth.User(ALICE, "Alicja", "", False)).keys() == {X}


def test_progress_returns_counts_only(notion):
    rows = store.progress()
    assert all(set(r) == {"name", "graded", "total"} for r in rows)
    assert [r["name"] for r in rows] == ["Alicja", "Bartek", "Celina"]


def test_progress_scans_oceny_once_a_minute_and_counts_own_saves_at_once(alice, notion):
    def alicja():
        return next(r["graded"] for r in store.progress() if r["name"] == "Alicja")

    assert alicja() == 1
    scans = len(notion.calls_of("query"))
    assert alice.put(f"/api/oceny/{Y}", json={"ocena": 7, "komentarz": ""}, headers=SAME_ORIGIN).status_code == 200
    assert alicja() == 2  # the new row still lags in queries
    assert alice.put(f"/api/oceny/{X}", json={"ocena": None, "komentarz": ""}, headers=SAME_ORIGIN).status_code == 200
    assert alicja() == 1
    assert [c for c in notion.calls_of("query")[scans:] if c[1] == OCENY and "Ocena" in str(c)] == []


def test_first_save_creates_and_later_saves_update_despite_query_lag(alice, notion):
    first = alice.put(f"/api/oceny/{Y}", json={"ocena": 7, "komentarz": "dobry"}, headers=SAME_ORIGIN)
    assert first.status_code == 200
    [(_, db, props)] = notion.calls_of("create")
    assert db == OCENY
    assert props["Nazwa"]["title"][0]["text"]["content"] == "Zofia Druga — Alicja N"
    assert props["Kandydat"] == {"relation": [{"id": Y}]}
    assert props["Oceniający"] == {"people": [{"id": ALICE}]}
    assert props["Ocena"] == {"number": 7}
    assert props["Komentarz"]["rich_text"][0]["text"]["content"] == "dobry"
    assert props["Ostatni zapis"] == {"date": {"start": first.json()["saved_at"]}}
    assert props["Klucz"]["rich_text"][0]["text"]["content"] == f"{Y}|{ALICE}"
    saved = datetime.fromisoformat(first.json()["saved_at"])
    assert saved.utcoffset() is not None and abs(time.time() - saved.timestamp()) < 60

    assert alice.put(f"/api/oceny/{Y}", json={"ocena": None, "komentarz": "jednak bez oceny"},
                     headers=SAME_ORIGIN).status_code == 200
    assert len(notion.calls_of("create")) == 1
    [(_, page_id, props)] = notion.calls_of("update")
    assert props["Ocena"] == {"number": None}

    card = alice.get(f"/k/{Y}").text
    assert ">jednak bez oceny</textarea>" in card and 'data-ocena=""' in card
    assert alice.put(f"/api/oceny/{Y}", json={"ocena": 6, "komentarz": ""}, headers=SAME_ORIGIN).status_code == 200
    assert "6/10" in alice.get("/").text
    assert len(notion.calls_of("create")) == 1
    assert len([p for p in notion.rows[OCENY] if p["id"] == page_id]) == 1


def test_lost_page_id_falls_back_to_query(alice, notion):
    alice.put(f"/api/oceny/{Y}", json={"ocena": 7, "komentarz": ""}, headers=SAME_ORIGIN)
    notion.settle()
    store._page_ids.clear()
    alice.put(f"/api/oceny/{Y}", json={"ocena": 8, "komentarz": ""}, headers=SAME_ORIGIN)
    assert len(notion.calls_of("create")) == 1
    assert len(notion.calls_of("update")) == 1


def test_archived_row_is_replaced_but_transient_errors_never_duplicate(alice, notion, monkeypatch):
    user = auth.User(ALICE, "Alicja", "", False)
    store.save_grade(user, Y, 7, "")
    assert len(notion.calls_of("create")) == 1
    notion.rows[OCENY][-1]["archived"] = True
    store.save_grade(user, Y, 8, "")
    assert len(notion.calls_of("create")) == 2

    def rate_limited(**kwargs):
        raise error(429, APIErrorCode.RateLimited)

    monkeypatch.setattr(notion.pages, "update", rate_limited)
    monkeypatch.setattr(notion.pages, "retrieve", rate_limited)
    with pytest.raises(APIResponseError):
        store.save_grade(user, Y, 9, "")
    with pytest.raises(APIResponseError):
        store.my_grade(user, Y)
    assert len(notion.calls_of("create")) == 2


def test_concurrent_saves_create_one_row(notion, monkeypatch):
    create = notion.pages.create

    def slow_create(**kwargs):
        time.sleep(0.05)
        return create(**kwargs)

    monkeypatch.setattr(notion.pages, "create", slow_create)
    user = auth.User(ALICE, "Alicja", "", False)
    threads = [threading.Thread(target=store.save_grade, args=(user, Y, n, "")) for n in (4, 5, 6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(notion.calls_of("create")) == 1
    assert len(notion.calls_of("update")) == 2


@pytest.mark.parametrize("body", [
    {"ocena": 7.5, "komentarz": ""}, {"ocena": "7", "komentarz": ""}, {"ocena": True, "komentarz": ""},
    {"ocena": 0, "komentarz": ""}, {"ocena": 11, "komentarz": ""}, {"ocena": 7, "komentarz": "x" * 2001},
    {"ocena": 7}, {"komentarz": ""}, {"ocena": 7, "komentarz": None}, {"ocena": 7, "komentarz": 5},
])
def test_put_rejects_invalid_bodies(alice, notion, body):
    response = alice.put(f"/api/oceny/{X}", json=body, headers=SAME_ORIGIN)
    assert response.status_code == 422
    assert notion.calls_of("update") == [] and notion.calls_of("create") == []


@pytest.mark.parametrize("body", [{"ocena": None, "komentarz": ""}, {"ocena": 1, "komentarz": "x" * 2000},
                                  {"ocena": 10, "komentarz": "ąę 🎭" * 400}])
def test_put_accepts_valid_bodies(alice, body):
    assert alice.put(f"/api/oceny/{X}", json=body, headers=SAME_ORIGIN).status_code == 200


def test_long_comment_is_split_into_notion_sized_chunks(alice, notion):
    alice.put(f"/api/oceny/{Y}", json={"ocena": 2, "komentarz": "x" * 2000}, headers=SAME_ORIGIN)
    [(_, _, props)] = notion.calls_of("create")
    assert [len(t["text"]["content"]) for t in props["Komentarz"]["rich_text"]] == [1000, 1000]


def test_unknown_candidate_is_404_without_page_retrieve(alice, notion):
    unknown = "99999999-9999-4999-8999-999999999999"
    assert alice.put(f"/api/oceny/{unknown}", json={"ocena": 1, "komentarz": ""}, headers=SAME_ORIGIN).status_code == 404
    assert alice.get(f"/k/{unknown}").status_code == 404
    assert alice.get(f"/k/{config.MEMBERS_DATABASE_ID}").status_code == 404
    assert notion.calls_of("retrieve") == [] and notion.calls_of("create") == []


def test_candidate_id_without_dashes_resolves(alice):
    assert "Adam Pierwszy" in alice.get(f"/k/{X.replace('-', '')}").text


def test_candidates_are_cached(alice, notion):
    alice.get("/")
    alice.get(f"/k/{X}")
    alice.get("/")
    assert len([c for c in notion.calls_of("query") if c[1] == KANDYDACI]) == 2  # two pages of size 2


def test_put_requires_session_same_origin_and_active_member(notion):
    anonymous = TestClient(app, base_url="https://testserver", follow_redirects=False)
    response = anonymous.put(f"/api/oceny/{X}", json={"ocena": 1, "komentarz": ""}, headers=SAME_ORIGIN)
    assert response.status_code == 401 and response.headers["content-type"].startswith("application/json")
    assert anonymous.get("/").headers["location"] == "/auth/login"
    assert anonymous.get(f"/k/{X}").headers["location"] == "/auth/login"

    alice = as_user(ALICE, "Alicja")
    for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "https://evil.example"}, {}):
        assert alice.put(f"/api/oceny/{X}", json={"ocena": 1, "komentarz": ""}, headers=headers).status_code == 403

    eve = as_user(EVE, "Ewa")
    assert eve.put(f"/api/oceny/{X}", json={"ocena": 1, "komentarz": ""}, headers=SAME_ORIGIN).status_code == 403
    assert eve.get("/").status_code == 403
    assert notion.calls_of("update") == [] and notion.calls_of("create") == []


def test_candidate_text_is_escaped(alice, notion):
    props = candidate_props("<script>alert(1)</script>", "2026-09-10T10:00:00.000+02:00", "javascript:alert(1)", "drive")
    props["Obszary"]["multi_select"] = [{"name": '"><img src=x onerror=alert(1)>'}]
    notion.add(KANDYDACI, props, "44444444-4444-4444-8444-444444444444")
    for html in (alice.get("/").text, alice.get("/k/44444444-4444-4444-8444-444444444444").text):
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "<img src=x" not in html
        assert 'href="javascript:' not in html
