"""Every Notion read of the panel and the Oceny upsert. Grades are user-scoped, except board-only all_grades."""
import re
import threading
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from notion_client.errors import APIErrorCode, APIResponseError

import config
from panel import auth
from panel.notion_dbs import database_id
from shared.auth import NotionAuth

WARSAW = ZoneInfo("Europe/Warsaw")
CANDIDATES_TTL = 60
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
# a remembered Oceny page that was deleted or archived; other errors must not lead to a duplicate create
GONE = (APIErrorCode.ObjectNotFound, APIErrorCode.ValidationError)

_candidates = {"at": None, "list": []}
_candidates_lock = threading.Lock()
_graded = {"at": None, "by_user": {}}
_graded_lock = threading.Lock()
# Klucz -> Oceny page id: right after a create the row is missing from queries for a while
_page_ids = {}
_locks = {}
_locks_guard = threading.Lock()


def _query_all(db_id, **kwargs):
    client = NotionAuth.get_client()
    pages, cursor = [], None
    while True:
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.databases.query(database_id=db_id, page_size=100, **kwargs)
        pages.extend(response["results"])
        if not response.get("has_more"):
            return pages
        cursor = response["next_cursor"]


def _text(prop):
    return "".join(t["plain_text"] for t in (prop or {}).get("title") or (prop or {}).get("rich_text") or [])


def _select(prop):
    return ((prop or {}).get("select") or {}).get("name") or ""


def _date(prop):
    return ((prop or {}).get("date") or {}).get("start") or ""


def _local(iso, fmt):
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    return (moment.astimezone(WARSAW) if moment.tzinfo else moment).strftime(fmt)


def _youtube_id(url):
    video = parse_qs(urlsplit(url).query).get("v", [""])[0]
    return video if YOUTUBE_ID.fullmatch(video) else None


def _http_url(url):
    return url if urlsplit(url).scheme in ("http", "https") else None


def _candidate(page):
    p = page["properties"]
    normalized = (p.get("Filmik znormalizowany") or {}).get("url") or ""
    status = _select(p.get("Status filmiku"))
    youtube_id = _youtube_id(normalized) if status == "youtube" else None
    czas = _date(p.get("Czas"))
    return {
        "id": page["id"],
        "name": _text(p.get("Imię i nazwisko")),
        "email": (p.get("Email") or {}).get("email") or "",
        "czas": czas,
        "czas_local": _local(czas, "%Y-%m-%d %H:%M") if czas else "",
        "uczelnia": _text(p.get("Uczelnia")),
        "rok": _select(p.get("Rok studiów")),
        "obszary": [o["name"] for o in (p.get("Obszary") or {}).get("multi_select", [])],
        "filmik": (p.get("Filmik") or {}).get("url") or "",
        "normalized": normalized,
        "status": status,
        "youtube_id": youtube_id,
        # derived from the id: Notion file URLs (Zdjęcie) expire after an hour
        "thumbnail": f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg" if youtube_id else None,
        "link": _http_url(normalized),
    }


def candidates():
    """Kandydaci sorted by Czas, cached for 60 s."""
    with _candidates_lock:
        now = time.monotonic()
        if _candidates["at"] is None or now - _candidates["at"] >= CANDIDATES_TTL:
            pages = _query_all(database_id(config.KANDYDACI_TITLE))
            _candidates["list"] = sorted((_candidate(p) for p in pages), key=lambda c: (c["czas"], c["name"]))
            _candidates["at"] = now
        return _candidates["list"]


def candidate(cid):
    """A candidate from the Kandydaci list or None; never a page retrieve, the bot token reads far more."""
    wanted = cid.replace("-", "").lower()
    return next((c for c in candidates() if c["id"].replace("-", "") == wanted), None)


def _key(cid, user):
    return f"{cid}|{user.id}"


def _grade(page, key):
    """The row's grade if it is a live row with exactly this Klucz, else None."""
    p = page["properties"]
    if page.get("archived") or page.get("in_trash") or _text(p.get("Klucz")) != key:
        return None
    saved_at = _date(p.get("Ostatni zapis"))
    return {
        "ocena": (p.get("Ocena") or {}).get("number"),
        "komentarz": _text(p.get("Komentarz")),
        "saved_at": saved_at,
        "saved_time": _local(saved_at, "%H:%M") if saved_at else "",
    }


def _own_page(key):
    page_id = _page_ids.get(key)
    if page_id:
        try:
            page = NotionAuth.get_client().pages.retrieve(page_id=page_id)
            if _grade(page, key):
                return page
        except APIResponseError as error:
            if error.code not in GONE:
                raise
        _page_ids.pop(key, None)
    by_key = {"property": "Klucz", "rich_text": {"equals": key}}
    for page in _query_all(database_id(config.OCENY_TITLE), filter=by_key):
        if _grade(page, key):
            _page_ids[key] = page["id"]
            return page
    return None


def my_grade(user, cid):
    key = _key(cid, user)
    page = _own_page(key)
    return _grade(page, key) if page else None


def my_grades(user):
    """{candidate id: grade} of the session user."""
    grades = {}
    mine = {"property": "Oceniający", "people": {"contains": user.id}}
    for page in _query_all(database_id(config.OCENY_TITLE), filter=mine):
        cid = _text(page["properties"].get("Klucz")).split("|")[0]
        grade = _grade(page, _key(cid, user))
        if grade:
            grades[cid] = grade
    for key in [k for k in list(_page_ids) if k.endswith(f"|{user.id}")]:
        cid = key.split("|")[0]
        if cid not in grades:
            grade = my_grade(user, cid)
            if grade:
                grades[cid] = grade
    return grades


def _lock(key):
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def _rich_text(value):
    # 1000-character chunks stay under Notion's 2000 limit even when it counts UTF-16 units
    return [{"text": {"content": value[i:i + 1000]}} for i in range(0, len(value), 1000)]


def save_grade(user, cid, ocena, komentarz):
    """Upsert the session user's row for this candidate; returns the save time (ISO, Europe/Warsaw)."""
    found = candidate(cid)
    if found is None:
        raise LookupError(cid)
    key = _key(found["id"], user)
    saved_at = datetime.now(WARSAW).isoformat(timespec="seconds")
    properties = {
        "Nazwa": {"title": _rich_text(f"{found['name']} — {user.name}")},
        "Kandydat": {"relation": [{"id": found["id"]}]},
        "Oceniający": {"people": [{"id": user.id}]},
        "Ocena": {"number": ocena},
        "Komentarz": {"rich_text": _rich_text(komentarz)},
        "Ostatni zapis": {"date": {"start": saved_at}},
        "Klucz": {"rich_text": _rich_text(key)},
    }
    client = NotionAuth.get_client()
    with _lock(key):
        page_id = _page_ids.get(key)
        if page_id:
            try:
                client.pages.update(page_id=page_id, properties=properties)
                return saved_at
            except APIResponseError as error:
                if error.code not in GONE:
                    raise
                _page_ids.pop(key, None)
        page = _own_page(key)
        if page:
            client.pages.update(page_id=page["id"], properties=properties)
        else:
            page = client.pages.create(parent={"database_id": database_id(config.OCENY_TITLE)}, properties=properties)
            _page_ids[key] = page["id"]
    with _graded_lock:
        mine = _graded["by_user"].setdefault(user.id, set())
        if ocena is None:
            mine.discard(found["id"])
        else:
            mine.add(found["id"])
    return saved_at


def _graded_by_user():
    """Reviewer id -> ids of the candidates they graded, cached for 60 s; save_grade keeps it current."""
    with _graded_lock:
        now = time.monotonic()
        if _graded["at"] is None or now - _graded["at"] >= CANDIDATES_TTL:
            graded = defaultdict(set)
            has_grade = {"property": "Ocena", "number": {"is_not_empty": True}}
            for page in _query_all(database_id(config.OCENY_TITLE), filter=has_grade):
                cid, _, uid = _text(page["properties"].get("Klucz")).partition("|")
                graded[uid].add(cid)
            _graded["by_user"], _graded["at"] = dict(graded), now
        return _graded["by_user"]


def progress():
    """Graded/total per active member. Counts only: no grade or comment leaves this function."""
    ids = {c["id"] for c in candidates()}
    graded = _graded_by_user()
    rows = [{"name": m["name"], "graded": len(graded.get(pid, set()) & ids), "total": len(ids)}
            for pid, m in auth.members().items() if m["active"]]
    return sorted(rows, key=lambda r: r["name"].lower())


def all_grades():
    """Every reviewer's live Oceny rows of current candidates. Board only: called solely by the /zarzad routes.
    One row per candidate and reviewer, the newest: a create resent after a Notion 5xx can leave a twin row"""
    grades = {}
    for page in _query_all(database_id(config.OCENY_TITLE)):
        if page.get("archived") or page.get("in_trash"):
            continue
        p = page["properties"]
        key_cid, _, key_uid = _text(p.get("Klucz")).partition("|")
        relation = (p.get("Kandydat") or {}).get("relation") or []
        found = candidate(relation[0]["id"] if relation else key_cid)
        if found is None:
            continue
        person = ((p.get("Oceniający") or {}).get("people") or [{}])[0]
        saved_at = _date(p.get("Ostatni zapis"))
        row = {
            "candidate_id": found["id"],
            "reviewer_id": person.get("id") or key_uid,
            "reviewer_name": person.get("name") or "",
            "ocena": (p.get("Ocena") or {}).get("number"),
            "komentarz": _text(p.get("Komentarz")),
            "saved_at": saved_at,
            "saved_local": _local(saved_at, "%Y-%m-%d %H:%M") if saved_at else "",
        }
        pair = (row["candidate_id"], row["reviewer_id"])
        if pair not in grades or saved_at > grades[pair]["saved_at"]:
            grades[pair] = row
    return list(grades.values())
