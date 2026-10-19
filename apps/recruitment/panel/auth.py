"""Sign in with Notion (identity only), session cookie and authorisation from Lista członków."""
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeTimedSerializer

import config
from panel.web import secret, templates
from shared.auth import NotionAuth

router = APIRouter()

AUTHORIZE_URL = "https://api.notion.com/v1/oauth/authorize"
TOKEN_URL = "https://api.notion.com/v1/oauth/token"
NOTION_VERSION = "2022-06-28"
SESSION_COOKIE = "panel_session"
STATE_COOKIE = "panel_oauth_state"
SESSION_MAX_AGE = 30 * 24 * 3600
STATE_MAX_AGE = 10 * 60
MEMBERS_TTL = 5 * 60
NOT_MEMBER = ("Panel jest przeznaczony wyłącznie dla aktywnych członków Teatru SGH. "
              "Jeśli nim jesteś, poproś Zarząd o zaznaczenie „Aktywny członek” na Liście członków.")

_http = httpx.Client(timeout=10)
_members = {"at": None, "by_person": {}}
_members_lock = threading.Lock()


@dataclass(frozen=True)
class User:
    id: str
    name: str
    email: str


class AuthError(Exception):
    """Raised by the dependencies; auth_error_handler turns it into a redirect, 401 or 403."""

    def __init__(self, status_code, message=""):
        super().__init__(status_code)
        self.status_code = status_code
        self.message = message


def members(refresh=False):
    """Person id -> {name, active} from Lista członków (read-only), cached for 5 minutes."""
    with _members_lock:
        now = time.monotonic()
        if refresh or _members["at"] is None or now - _members["at"] >= MEMBERS_TTL:
            _members["by_person"] = _load_members()
            _members["at"] = now
        return _members["by_person"]


def _load_members():
    client = NotionAuth.get_client()
    by_person = {}
    cursor = None
    while True:
        kwargs = {"database_id": config.MEMBERS_DATABASE_ID, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.databases.query(**kwargs)
        for page in response["results"]:
            props = page["properties"]
            name = "".join(t["plain_text"] for t in props.get("Imię i Nazwisko", {}).get("title", [])).strip()
            active = props.get("Aktywny członek", {}).get("checkbox") is True
            for person in props.get("Person", {}).get("people", []):
                entry = by_person.setdefault(person["id"], {"name": "", "active": False})
                if active or not entry["name"]:
                    entry["name"] = name or person.get("name") or entry["name"]
                entry["active"] = entry["active"] or active
        if not response.get("has_more"):
            return by_person
        cursor = response["next_cursor"]


def _serializer(salt):
    return URLSafeTimedSerializer(secret("SESSION_SECRET"), salt=salt)


def _unsign(token, salt, max_age):
    if not token:
        return None
    try:
        return _serializer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None


def _session(request):
    data = _unsign(request.cookies.get(SESSION_COOKIE), "panel-session", SESSION_MAX_AGE)
    if isinstance(data, dict) and isinstance(data.get("uid"), str):
        return data
    return None


def _active_user(session):
    member = members().get(session["uid"])
    if not (member and member["active"]):
        return None
    return User(session["uid"], session.get("name") or "", session.get("email") or "")


def current_user(request: Request):
    """The signed-in active member, or None."""
    session = _session(request)
    return _active_user(session) if session else None


def require_reviewer(request: Request) -> User:
    session = _session(request)
    if session is None:
        raise AuthError(401)
    user = _active_user(session)
    if user is None:
        raise AuthError(403, NOT_MEMBER)
    return user


def auth_error_handler(request: Request, exc: AuthError):
    api = request.url.path.startswith("/api/")
    if exc.status_code == 401:
        if api:
            return JSONResponse({"detail": "Sesja wygasła, zaloguj się ponownie."}, status_code=401)
        return RedirectResponse("/auth/login", status_code=303)
    if api:
        return JSONResponse({"detail": "Brak dostępu"}, status_code=403)
    return _page(request, "Brak dostępu", exc.message, 403, logout=True)


def same_origin(request: Request):
    """CSRF guard for state-changing requests."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site == "same-origin"
    base = urlsplit(config.BASE_URL)
    return request.headers.get("origin") == f"{base.scheme}://{base.netloc}"


def _page(request, title, message, status_code, login=False, logout=False):
    context = {"title": title, "message": message, "login": login, "logout": logout}
    return templates.TemplateResponse(request, "auth_message.html", context, status_code=status_code)


def _redirect_uri():
    return config.BASE_URL.rstrip("/") + "/auth/callback"


def _exchange_code(code):
    """Notion user {uid, name, email} for an authorisation code; the access token is discarded."""
    try:
        response = _http.post(
            TOKEN_URL,
            auth=(secret("NOTION_OAUTH_CLIENT_ID"), secret("NOTION_OAUTH_CLIENT_SECRET")),
            json={"grant_type": "authorization_code", "code": code, "redirect_uri": _redirect_uri()},
            headers={"Notion-Version": NOTION_VERSION},
        )
        if response.status_code != 200:
            return None
        user = (response.json().get("owner") or {}).get("user") or {}
    except (httpx.HTTPError, ValueError, AttributeError):
        return None
    if not isinstance(user.get("id"), str):
        return None
    return {"uid": user["id"], "name": user.get("name") or "", "email": (user.get("person") or {}).get("email") or ""}


@router.get("/auth/login")
def login():
    state = secrets.token_urlsafe(32)
    query = urlencode({
        "client_id": secret("NOTION_OAUTH_CLIENT_ID"),
        "response_type": "code",
        "owner": "user",
        "redirect_uri": _redirect_uri(),
        "state": state,
    })
    response = RedirectResponse(f"{AUTHORIZE_URL}?{query}", status_code=302)
    response.set_cookie(STATE_COOKIE, _serializer("panel-oauth-state").dumps(state), max_age=STATE_MAX_AGE,
                        path="/auth", secure=True, httponly=True, samesite="lax")
    return response


@router.get("/auth/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    expected = _unsign(request.cookies.get(STATE_COOKIE), "panel-oauth-state", STATE_MAX_AGE)
    if error:
        response = _page(request, "Logowanie przerwane",
                         "Logowanie przez Notion zostało anulowane lub się nie powiodło.", 400, login=True)
    elif not (code and state and isinstance(expected, str)
              and secrets.compare_digest(state.encode(), expected.encode())):
        response = _page(request, "Nieprawidłowe logowanie",
                         "Link logowania wygasł albo jest nieprawidłowy.", 400, login=True)
    else:
        response = _sign_in(request, code)
    response.delete_cookie(STATE_COOKIE, path="/auth", secure=True, httponly=True, samesite="lax")
    return response


def _sign_in(request, code):
    identity = _exchange_code(code)
    if identity is None:
        return _page(request, "Logowanie nie powiodło się", "Notion nie potwierdził logowania. Spróbuj ponownie.",
                     400, login=True)
    member = members().get(identity["uid"])
    if not (member and member["active"]):
        member = members(refresh=True).get(identity["uid"])
    if not (member and member["active"]):
        return _page(request, "Brak dostępu", NOT_MEMBER, 403)
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(SESSION_COOKIE, _serializer("panel-session").dumps(identity), max_age=SESSION_MAX_AGE,
                        path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.post("/auth/logout")
def logout(request: Request):
    if not same_origin(request):
        return JSONResponse({"detail": "Brak dostępu"}, status_code=403)
    # Not "/": that would redirect on to Notion, and CSP form-action 'self' blocks a form's redirect off-site
    response = RedirectResponse("/auth/logged-out", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return response


@router.get("/auth/logged-out")
def logged_out(request: Request):
    return _page(request, "Wylogowano", "Wylogowano z panelu rekrutacyjnego.", 200, login=True)
