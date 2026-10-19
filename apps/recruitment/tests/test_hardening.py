import http.client
import json
import logging
import os
import re
import socket
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from notion_client.errors import APIErrorCode, APIResponseError
from uvicorn.logging import AccessFormatter

import config
from fake_notion import FakeNotion, checkbox, member, title
from panel import auth, ingest, main, notion_dbs, ratelimit, store
from panel.main import app
from shared.auth import NotionAuth

KANDYDACI, OCENY = "kandydaci-db", "oceny-ii-db"
ALICE = "a0000000-0000-4000-8000-000000000001"  # reviewer
BOB = "b0000000-0000-4000-8000-000000000002"    # second reviewer
X = "11111111-1111-4111-8111-111111111111"      # YouTube candidate
Y = "22222222-2222-4222-8222-222222222222"
UNKNOWN = "99999999-9999-4999-8999-999999999999"
SAME_ORIGIN = {"Sec-Fetch-Site": "same-origin"}
JSON = {**SAME_ORIGIN, "Content-Type": "application/json"}
SENTINEL = "SENTINEL-5b1e"
CSP = ("default-src 'self'; img-src 'self' https://i.ytimg.com https://drive.google.com https://lh3.googleusercontent.com; "
       "frame-src https://www.youtube-nocookie.com https://drive.google.com; frame-ancestors 'none'; base-uri 'none'; "
       "form-action 'self'; object-src 'none'; script-src 'self'; style-src 'self'")
TEMPLATES = Path(__file__).resolve().parent.parent / "panel" / "templates"


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setattr(NotionAuth, "_client", fake)
    monkeypatch.setattr(notion_dbs, "_ids", {config.KANDYDACI_TITLE: KANDYDACI, config.OCENY_II_TITLE: OCENY,
                                             config.OCENY_TITLE: "oceny-db"})
    monkeypatch.setattr(auth, "_members", {"at": None, "by_person": {}})
    monkeypatch.setattr(store, "_candidates", {"at": None, "list": []})
    monkeypatch.setattr(store, "_page_ids", {})
    monkeypatch.setattr(store, "_locks", {})
    fake.add(config.MEMBERS_DATABASE_ID, member(ALICE, "Alicja"))
    fake.add(config.MEMBERS_DATABASE_ID, member(BOB, "Bartek"))
    czas = {"type": "date", "date": {"start": "2026-09-02T09:30:00.000+02:00"}}
    fake.add(KANDYDACI, {"Imię i nazwisko": title("Adam Pierwszy"), "Czas": czas, "II etap": checkbox(True),
                         "Filmik znormalizowany": {"type": "url", "url": "https://www.youtube.com/watch?v=aaaaaaaaaaa"},
                         "Status filmiku": {"type": "select", "select": {"name": "youtube"}}}, X)
    fake.add(KANDYDACI, {"Imię i nazwisko": title("Zofia Druga"), "Czas": czas, "II etap": checkbox(True)}, Y)
    return fake


def session(uid):
    serializer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="panel-session")
    return serializer.dumps({"uid": uid, "name": "Test", "email": ""})


def as_user(uid):
    return TestClient(app, base_url="https://testserver", follow_redirects=False,
                      cookies={"panel_session": session(uid)})


@pytest.fixture
def alice(notion):
    return as_user(ALICE)


@pytest.fixture
def clock(monkeypatch):
    now = SimpleNamespace(t=1000.0)
    monkeypatch.setattr(ratelimit, "clock", lambda: now.t)
    return now


def put(client, cid=X):
    return client.put(f"/api/oceny/{cid}", json={"ocena": 5, "komentarz": ""}, headers=SAME_ORIGIN)


# Security headers and CSP

def test_security_headers_on_every_response(alice):
    anonymous = TestClient(app, base_url="https://testserver", follow_redirects=False)
    responses = [alice.get("/"), alice.get(f"/k/{X}"), put(alice), alice.get("/nope"), alice.get("/static/app.js"),
                 anonymous.get("/healthz"), anonymous.get("/privacy"), anonymous.get("/auth/login"),
                 anonymous.get("/"), anonymous.post("/internal/ingest")]
    for response in responses:
        assert response.headers["content-security-policy"] == CSP
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


@pytest.mark.parametrize("template", sorted(p.name for p in TEMPLATES.glob("*.html")))
def test_templates_have_no_inline_script_style_or_handler(template):
    source = (TEMPLATES / template).read_text()
    assert not re.search(r"<script(?![^>]*\ssrc=)|<style|\sstyle=|\son[a-z]+\s*=|javascript:", source, re.I)


def test_rendered_pages_load_only_what_the_csp_allows(alice, notion):
    pages = [alice.get(path).text for path in ("/", f"/k/{X}", "/nope", "/privacy", "/terms", "/auth/logged-out")]
    html = "\n".join(pages)
    assert 'src="https://www.youtube-nocookie.com/embed/aaaaaaaaaaa"' in html
    assert 'src="https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"' in html
    allowed = ("/static/", "https://i.ytimg.com/", "https://www.youtube-nocookie.com/")
    assert all(src.startswith(allowed) for src in re.findall(r'\ssrc="([^"]*)"', html))
    assert all(href.startswith("/static/") for href in re.findall(r'<link rel="stylesheet" href="([^"]*)"', html))
    assert set(re.findall(r'\saction="([^"]*)"', html)) == {"/auth/logout"}


# PUT /api/oceny: content type and rate limit

@pytest.mark.parametrize("content_type", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_put_requires_json_content_type(alice, notion, content_type):
    headers = {**SAME_ORIGIN, **({"Content-Type": content_type} if content_type else {})}
    response = alice.put(f"/api/oceny/{X}", content=b'{"ocena": 5, "komentarz": ""}', headers=headers)
    assert response.status_code == 415
    assert notion.calls_of("create") == [] and notion.calls_of("update") == []


def test_put_body_is_read_only_after_sign_in_and_capped(alice, notion, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("body parsed before the checks")

    huge = b'{"ocena": 5, "komentarz": "' + b"x" * 200_000 + b'"}'
    anonymous = TestClient(app, base_url="https://testserver")
    with monkeypatch.context() as patch:
        patch.setattr(main.Grade, "model_validate_json", forbidden)
        assert anonymous.put(f"/api/oceny/{X}", content=huge, headers=JSON).status_code == 401
    assert alice.put(f"/api/oceny/{X}", content=huge, headers=JSON).status_code == 413
    assert notion.calls_of("create") == [] and notion.calls_of("update") == []


def test_put_accepts_json_with_charset(alice):
    response = alice.put(f"/api/oceny/{X}", content=b'{"ocena": 5, "komentarz": ""}',
                         headers={**SAME_ORIGIN, "Content-Type": "application/json; charset=utf-8"})
    assert response.status_code == 200


def replay(clock, saves, uid=ALICE):
    """Run (time, candidate) saves through the limiter; the refused ones as (time, retry after)."""
    refused = []
    for at, cid in sorted(saves):
        clock.t = at
        wait = ratelimit.retry_after(uid, cid)
        if wait:
            refused.append((at, wait))
    return refused


def test_autosave_at_debounce_speed_with_flushes_is_never_limited(clock):
    # 10 minutes of edits, a save every 1.5 s (the debounce), each overlapped by a blur or keepalive flush
    saves = [(1000 + 1.5 * i, X) for i in range(400)] + [(1000 + 1.5 * i + 0.3, X) for i in range(400)]
    assert replay(clock, saves) == []


def test_one_save_per_second_for_ten_minutes_is_never_limited(clock):
    saves = [(1000 + i, X if i % 2 else Y) for i in range(600)]
    assert replay(clock, saves) == []


def test_fast_grading_run_across_candidates_is_never_limited(clock):
    # A grade every 2 s on a new card, each card flushed when left
    cards = [f"card-{i}" for i in range(150)]
    saves = [(1000 + 2 * i, cid) for i, cid in enumerate(cards)] + [(1000.5 + 2 * i, cid) for i, cid in enumerate(cards)]
    assert replay(clock, saves) == []


def test_hammering_one_candidate_is_coalesced_and_capped(clock):
    refused = replay(clock, [(1000.1, X)] * 100)
    assert len(refused) == 100 - ratelimit.REPEATS
    assert {wait for _, wait in refused} == {1}
    clock.t = 1001.0
    assert ratelimit.retry_after(ALICE, X) is None


def test_script_over_many_candidates_is_held_to_the_window(clock):
    # 10 candidates a second for 5 minutes
    saves = [(1000 + s + i / 10, f"card-{i}") for s in range(300) for i in range(10)]
    refused = replay(clock, saves)
    assert len(saves) - len(refused) <= ratelimit.EVENTS + 300
    first_at, first_wait = refused[0]
    assert first_at == 1006.0 and first_wait == 54
    assert replay(clock, [(1060.0, "card-x")], uid=BOB) == []


def test_refused_saves_are_not_counted(clock):
    assert replay(clock, [(1000 + i / 100, f"card-{i}") for i in range(ratelimit.EVENTS)]) == []
    assert len(replay(clock, [(1030 + i / 1000, f"spam-{i}") for i in range(100)])) == 100
    clock.t = 1000 + ratelimit.WINDOW
    assert ratelimit.retry_after(ALICE, "card-late") is None


def test_rate_limited_put_is_429_with_retry_after_and_never_reaches_notion(alice, notion, clock):
    assert [put(alice).status_code for _ in range(ratelimit.REPEATS)] == [200] * ratelimit.REPEATS
    writes = len(notion.calls_of("create")) + len(notion.calls_of("update"))
    response = put(alice)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json() == {"detail": "Za dużo zapisów naraz. Spróbuj ponownie za chwilę."}
    assert len(notion.calls_of("create")) + len(notion.calls_of("update")) == writes
    assert put(as_user(BOB)).status_code == 200
    clock.t += 1
    assert put(alice).status_code == 200


# Error pages

def test_unknown_pages_get_the_html_404(alice):
    anonymous = TestClient(app, base_url="https://testserver", follow_redirects=False)
    for response in (anonymous.get("/nope"), alice.get(f"/k/{UNKNOWN}"), alice.get("/static/missing.css")):
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("text/html")
        assert "<h1>Nie znaleziono</h1>" in response.text


def test_api_and_internal_404s_stay_json(alice):
    assert put(alice, UNKNOWN).json() == {"detail": "Nie ma takiego kandydata"}
    for response in (alice.get("/api/nope"), alice.post("/internal/nope")):
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/json"


def test_500_hides_the_error_from_page_api_and_logs(alice, monkeypatch, capfd):
    def broken():
        raise RuntimeError(f"Notion said {SENTINEL}")

    monkeypatch.setattr(store, "candidates", broken)
    page, api = alice.get("/"), put(alice)  # TestClient re-raises anything the app lets escape
    assert page.status_code == 500 and api.status_code == 500
    assert "<h1>Błąd serwera</h1>" in page.text
    assert page.headers["content-security-policy"] == CSP
    assert api.json() == {"detail": "Błąd serwera"}
    out, err = capfd.readouterr()
    assert "Unhandled RuntimeError on GET /\n" in err and "Unhandled RuntimeError on PUT /api/oceny/" in err
    assert SENTINEL not in page.text + api.text + out + err


# Logs

@contextmanager
def server(asgi_app):
    """A real uvicorn on a free local port, so its access log runs as in production."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    instance = uvicorn.Server(uvicorn.Config(asgi_app, log_config=None, lifespan="off"))
    thread = threading.Thread(target=instance.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    try:
        yield sock.getsockname()[1]
    finally:
        instance.should_exit = True
        thread.join(10)
        sock.close()


def request(port, method, path, headers=None, body=None):
    """http.client rather than httpx: the test's own client must not log the URLs it calls."""
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    response.read()
    connection.close()
    return response


def cookie(response, name):
    return next(c.split(";")[0] for c in response.headers.get_all("set-cookie") if c.startswith(name + "="))


def test_logs_never_carry_comment_code_token_or_email(notion, monkeypatch, capfd, caplog):
    code, comment = f"code-{SENTINEL}", f"komentarz-{SENTINEL}"

    def token_endpoint(request):
        assert json.loads(request.content)["code"] == code
        return httpx.Response(200, json={"access_token": f"token-{SENTINEL}", "owner": {"type": "user", "user": {
            "id": ALICE, "name": f"Alicja {SENTINEL}", "person": {"email": f"{SENTINEL}@example.com"}}}})

    def rejecting(**kwargs):  # a Notion validation message can quote the rejected value
        raise APIResponseError(SimpleNamespace(status_code=400, headers={}, text=""),
                               f"body failed validation, instead was `{comment}`", APIErrorCode.ValidationError)

    monkeypatch.setattr(auth, "_http", httpx.Client(transport=httpx.MockTransport(token_endpoint)))
    caplog.set_level(logging.DEBUG)
    with server(app) as port:
        login = request(port, "GET", "/auth/login")
        state = parse_qs(urlsplit(login.getheader("location")).query)["state"][0]
        query = urlencode({"code": code, "state": state})
        callback = request(port, "GET", f"/auth/callback?{query}", {"Cookie": cookie(login, "panel_oauth_state")})
        assert callback.status == 303
        assert request(port, "GET", f"/auth/callback?code={code}&state=wrong").status == 400
        headers = {"Cookie": cookie(callback, "panel_session"), "Content-Type": "application/json", **SAME_ORIGIN}
        for komentarz, status in [(comment, 200), (comment * 100, 422)]:
            body = json.dumps({"ocena": 7, "komentarz": komentarz})
            assert request(port, "PUT", f"/api/oceny/{X}", headers, body).status == status
        monkeypatch.setattr(notion.pages, "update", rejecting)
        monkeypatch.setattr(notion.pages, "create", rejecting)
        body = json.dumps({"ocena": 8, "komentarz": comment})
        assert request(port, "PUT", f"/api/oceny/{X}", headers, body).status == 500
    out, err = capfd.readouterr()
    formatter = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False)
    access = [formatter.format(r) for r in caplog.records if r.name == "uvicorn.access"]
    assert any('"GET /auth/callback HTTP/1.1" 303' in line for line in access)
    assert any('"PUT /api/oceny/' in line and '" 500' in line for line in access)
    assert "Unhandled APIResponseError on PUT /api/oceny/" in err
    assert SENTINEL not in "\n".join([out, err, caplog.text, *access])
