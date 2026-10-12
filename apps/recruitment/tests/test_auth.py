import base64
import json
import os
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner, URLSafeTimedSerializer

import config
from fake_notion import FakeNotion, member
from panel import auth
from panel.main import app
from shared.auth import NotionAuth

ALICE = "a0000000-0000-4000-8000-000000000001"   # active reviewer
BOB = "b0000000-0000-4000-8000-000000000002"     # active board member
EVE = "e0000000-0000-4000-8000-000000000005"     # inactive member
LATE = "c0000000-0000-4000-8000-000000000003"    # added to Lista członków after the cache was filled
MALLORY = "f0000000-0000-4000-8000-000000000006"  # not in Lista członków
CODES = {"code-alice": ALICE, "code-bob": BOB, "code-late": LATE, "code-eve": EVE, "code-mallory": MALLORY}
BASE_ORIGIN = "https://panel-rekrutacja-590133883335.europe-west1.run.app"

probe = FastAPI()
probe.include_router(auth.router)
probe.add_exception_handler(auth.AuthError, auth.auth_error_handler)


@probe.get("/whoami")
@probe.get("/api/whoami")
def whoami(user: auth.User = Depends(auth.require_reviewer)):
    return {"id": user.id, "name": user.name, "email": user.email, "board": user.board}


@probe.get("/optional")
def optional(request: Request):
    user = auth.current_user(request)
    return {"id": user.id if user else None}


@probe.get("/board")
def board(user: auth.User = Depends(auth.require_board)):
    return {"id": user.id}


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    monkeypatch.setattr(NotionAuth, "_client", fake)
    monkeypatch.setattr(auth, "_members", {"at": None, "by_person": {}})
    fake.add(config.MEMBERS_DATABASE_ID, member(ALICE, "Alicja Recenzentka"))
    fake.add(config.MEMBERS_DATABASE_ID, member(BOB, "Bob Zarządca", board=True))
    fake.add(config.MEMBERS_DATABASE_ID, member(EVE, "Ewa Była", active=False, board=True))
    fake.add(config.MEMBERS_DATABASE_ID, member(None, "Bez konta"))
    return fake


@pytest.fixture
def token_requests(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        uid = CODES.get(json.loads(request.content)["code"])
        if uid is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(200, json={
            "access_token": "notion-access-token", "token_type": "bearer", "bot_id": "bot", "workspace_id": "ws",
            "owner": {"type": "user", "user": {"object": "user", "id": uid, "name": f"Notion {uid[:1]}",
                                               "person": {"email": f"{uid[:1]}@example.com"}}},
        })

    monkeypatch.setattr(auth, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    return seen


@pytest.fixture
def client(notion, token_requests):
    return TestClient(probe, base_url="https://testserver", follow_redirects=False)


def set_cookies(response):
    return response.headers.get_list("set-cookie")


def sign_in(client, code):
    login = client.get("/auth/login")
    state = parse_qs(urlsplit(login.headers["location"]).query)["state"][0]
    return client.get("/auth/callback", params={"code": code, "state": state})


def session_cookie(response):
    return next(c for c in set_cookies(response) if c.startswith("panel_session="))


def test_app_mounts_auth_routes():
    response = TestClient(app, base_url="https://testserver", follow_redirects=False).get("/auth/login")
    assert response.status_code == 302


def test_login_redirects_to_notion_with_state_cookie(client):
    response = client.get("/auth/login")
    assert response.status_code == 302
    url = urlsplit(response.headers["location"])
    assert (url.scheme, url.netloc, url.path) == ("https", "api.notion.com", "/v1/oauth/authorize")
    query = parse_qs(url.query)
    assert query["client_id"] == ["test-notion_oauth_client_id"]
    assert query["response_type"] == ["code"]
    assert query["owner"] == ["user"]
    assert query["redirect_uri"] == [f"{BASE_ORIGIN}/auth/callback"]
    assert len(query["state"][0]) >= 32
    assert "redirect_uri=https%3A%2F%2Fpanel-rekrutacja" in url.query
    cookie = set_cookies(response)[0]
    assert cookie.startswith("panel_oauth_state=")
    assert query["state"][0] not in cookie
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/auth", "Max-Age=600"):
        assert flag in cookie


def test_each_login_gets_a_new_state(client):
    states = {parse_qs(urlsplit(client.get("/auth/login").headers["location"]).query)["state"][0] for _ in range(3)}
    assert len(states) == 3


def test_active_member_gets_session_cookie(client, token_requests):
    response = sign_in(client, "code-alice")
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = session_cookie(response)
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", f"Max-Age={30 * 24 * 3600}"):
        assert flag in cookie
    assert any(c.startswith('panel_oauth_state=""') for c in set_cookies(response))

    value = cookie.split(";")[0].split("=", 1)[1]
    payload = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="panel-session").loads(value)
    assert payload == {"uid": ALICE, "name": "Notion a", "email": "a@example.com"}
    assert "notion-access-token" not in cookie

    me = client.get("/whoami").json()
    assert me == {"id": ALICE, "name": "Notion a", "email": "a@example.com", "board": False}
    assert client.get("/optional").json() == {"id": ALICE}
    assert client.get("/board").status_code == 403


def test_token_exchange_request(client, token_requests):
    sign_in(client, "code-alice")
    [request] = token_requests
    assert request.method == "POST"
    assert str(request.url) == "https://api.notion.com/v1/oauth/token"
    expected = base64.b64encode(b"test-notion_oauth_client_id:test-notion_oauth_client_secret").decode()
    assert request.headers["authorization"] == f"Basic {expected}"
    assert request.headers["notion-version"] == "2022-06-28"
    assert json.loads(request.content) == {"grant_type": "authorization_code", "code": "code-alice",
                                           "redirect_uri": f"{BASE_ORIGIN}/auth/callback"}


def test_board_member_gets_board_role(client):
    sign_in(client, "code-bob")
    assert client.get("/whoami").json()["board"] is True
    assert client.get("/board").json() == {"id": BOB}


@pytest.mark.parametrize("code", ["code-mallory", "code-eve"])
def test_non_member_and_inactive_member_get_403_without_session(client, code):
    response = sign_in(client, code)
    assert response.status_code == 403
    assert "Brak dostępu" in response.text
    assert "aktywnych członków" in response.text
    assert not any(c.startswith("panel_session=") for c in set_cookies(response))
    assert client.get("/whoami").status_code == 303


def test_login_refreshes_members_for_unknown_user(client, notion):
    auth.members()
    notion.add(config.MEMBERS_DATABASE_ID, member(LATE, "Spóźniony"))
    assert sign_in(client, "code-late").status_code == 303
    assert client.get("/whoami").json()["id"] == LATE


def test_state_mismatch_is_rejected(client, token_requests):
    client.get("/auth/login")
    response = client.get("/auth/callback", params={"code": "code-alice", "state": "forged"})
    assert response.status_code == 400
    assert not any(c.startswith("panel_session=") for c in set_cookies(response))
    assert token_requests == []


def test_callback_without_state_cookie_is_rejected(notion, token_requests):
    fresh = TestClient(probe, base_url="https://testserver", follow_redirects=False)
    assert fresh.get("/auth/callback", params={"code": "code-alice", "state": "x"}).status_code == 400
    assert token_requests == []


def test_error_param_shows_friendly_page(client, token_requests):
    client.get("/auth/login")
    response = client.get("/auth/callback", params={"error": "access_denied", "state": "x"})
    assert response.status_code == 400
    assert "Logowanie przerwane" in response.text
    assert 'href="/auth/login"' in response.text
    assert token_requests == []


def test_failed_token_exchange_gives_no_session(client):
    response = sign_in(client, "code-unknown")
    assert response.status_code == 400
    assert not any(c.startswith("panel_session=") for c in set_cookies(response))


def test_unauthenticated_html_redirects_and_api_gets_401(client):
    response = client.get("/whoami")
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/login"
    response = client.get("/api/whoami")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")


def valid_session(uid=ALICE, signer=TimestampSigner):
    serializer = URLSafeTimedSerializer(os.environ["SESSION_SECRET"], salt="panel-session", signer=signer)
    return serializer.dumps({"uid": uid, "name": "X", "email": "x@example.com"})


class PastSigner(TimestampSigner):
    def get_timestamp(self):
        return int(time.time()) - 31 * 24 * 3600


@pytest.mark.parametrize("cookie", [
    valid_session()[:-2] + "xx",
    valid_session().replace(".", "!", 1),
    URLSafeTimedSerializer("other-secret", salt="panel-session").dumps({"uid": ALICE}),
    valid_session(signer=PastSigner),
    "garbage",
])
def test_tampered_or_expired_cookie_is_rejected(notion, cookie):
    fresh = TestClient(probe, base_url="https://testserver", follow_redirects=False)
    assert fresh.get("/api/whoami", headers={"Cookie": f"panel_session={cookie}"}).status_code == 401
    assert fresh.get("/whoami", headers={"Cookie": f"panel_session={cookie}"}).status_code == 303


def test_valid_cookie_is_accepted(notion):
    fresh = TestClient(probe, base_url="https://testserver", follow_redirects=False)
    assert fresh.get("/api/whoami", headers={"Cookie": f"panel_session={valid_session()}"}).status_code == 200


def untick(notion, uid, prop):
    for page in notion.rows[config.MEMBERS_DATABASE_ID]:
        if any(p["id"] == uid for p in page["properties"]["Person"]["people"]):
            page["properties"][prop]["checkbox"] = False


def test_role_revocation_takes_effect_after_cache_refresh(client, notion):
    sign_in(client, "code-bob")
    assert client.get("/board").status_code == 200

    untick(notion, BOB, "Zarząd")
    assert client.get("/board").status_code == 200
    auth._members["at"] -= auth.MEMBERS_TTL
    assert client.get("/board").status_code == 403
    assert client.get("/whoami").status_code == 200

    untick(notion, BOB, "Aktywny członek")
    auth._members["at"] -= auth.MEMBERS_TTL
    response = client.get("/whoami")
    assert response.status_code == 403
    assert "Brak dostępu" in response.text
    assert 'action="/auth/logout"' in response.text
    assert client.get("/api/whoami").status_code == 403
    assert client.get("/optional").json() == {"id": None}


def test_members_cache_is_reused_within_ttl(client, notion):
    sign_in(client, "code-alice")
    queries = len(notion.calls)
    for _ in range(3):
        client.get("/whoami")
    assert len(notion.calls) == queries


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
    {"Sec-Fetch-Site": "cross-site", "Origin": BASE_ORIGIN},
    {"Origin": "https://evil.example"},
    {},
])
def test_logout_rejects_cross_site_requests(client, headers):
    sign_in(client, "code-alice")
    response = client.post("/auth/logout", headers=headers)
    assert response.status_code == 403
    assert client.get("/whoami").status_code == 200


@pytest.mark.parametrize("headers", [{"Sec-Fetch-Site": "same-origin"}, {"Origin": BASE_ORIGIN}])
def test_logout_clears_session(client, headers):
    sign_in(client, "code-alice")
    response = client.post("/auth/logout", headers=headers)
    assert response.status_code == 303
    assert response.headers["location"] == "/auth/logged-out"
    assert any(c.startswith('panel_session=""') and "Max-Age=0" in c for c in set_cookies(response))
    assert client.get("/whoami").status_code == 303
    page = client.get("/auth/logged-out")
    assert page.status_code == 200 and "Wylogowano" in page.text and 'href="/auth/login"' in page.text
