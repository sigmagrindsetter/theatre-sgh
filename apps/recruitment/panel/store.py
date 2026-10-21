"""Every Notion read of the panel, the Oceny II etap upsert and Komentarze II etap adds (REVIEW_PANEL_SPEC.md §11).
A grade or comment leaves this module with its author only to that author; to others only as an average
or as a comment without author or time. Stage I grades leave it only as averages."""
import re
import threading
import time
import unicodedata
import uuid
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
STAGE1_TTL = 10 * 60  # stage I is closed, its Oceny no longer change
INVITED = "II etap"
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
DRIVE_FILE = re.compile(r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]{10,})(?:[/?]|$)")
# a remembered Oceny page that was deleted or archived; other errors must not lead to a duplicate create
GONE = (APIErrorCode.ObjectNotFound, APIErrorCode.ValidationError)

_candidates = {"at": None, "list": []}
_candidates_lock = threading.Lock()
_grades_ii = {"at": None, "by_key": {}}
_saved = {}  # Klucz -> row saved by this process: a new row lags in queries for a while
_grades_ii_lock = threading.Lock()
_comments_ii = {"at": None, "by_key": {}}
_added = {}  # Klucz -> comment added by this process: a new row lags in queries
_comments_ii_lock = threading.Lock()
_stage1 = {"at": None, "comments": {}, "averages": {}}
_stage1_lock = threading.Lock()
_signups = {"at": None, "list": []}
_signups_lock = threading.Lock()
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


def _drive_id(url):
    """File id of a Drive file link; folder links have no player."""
    match = DRIVE_FILE.match(url)
    return match.group(1) if match else None


def _http_url(url):
    return url if urlsplit(url).scheme in ("http", "https") else None


def _candidate(page):
    p = page["properties"]
    normalized = (p.get("Filmik znormalizowany") or {}).get("url") or ""
    status = _select(p.get("Status filmiku"))
    youtube_id = _youtube_id(normalized) if status == "youtube" else None
    drive_id = _drive_id(normalized) if status == "drive" else None
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
        "drive_id": drive_id,
        # derived from the id: Notion file URLs (Zdjęcie) expire after an hour
        "thumbnail": (f"https://i.ytimg.com/vi/{youtube_id}/hqdefault.jpg" if youtube_id
                      else f"https://drive.google.com/thumbnail?id={drive_id}&sz=w480" if drive_id else None),
        "link": _http_url(normalized),
    }


def candidates():
    """Kandydaci invited to stage II (II etap ticked) sorted by Czas, cached for 60 s."""
    with _candidates_lock:
        now = time.monotonic()
        if _candidates["at"] is None or now - _candidates["at"] >= CANDIDATES_TTL:
            pages = [p for p in _query_all(database_id(config.KANDYDACI_TITLE))
                     if (p["properties"].get(INVITED) or {}).get("checkbox") is True]
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
    for page in _query_all(database_id(config.OCENY_II_TITLE), filter=by_key):
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
    for page in _query_all(database_id(config.OCENY_II_TITLE), filter=mine):
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


def save_grade(user, cid, ocena, komentarz=None):
    """Upsert the session user's row for this candidate, keeping its comment when komentarz is None;
    returns the save time (ISO, Europe/Warsaw)"""
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
        "Ostatni zapis": {"date": {"start": saved_at}},
        "Klucz": {"rich_text": _rich_text(key)},
    }
    if komentarz is not None:
        properties["Komentarz"] = {"rich_text": _rich_text(komentarz)}
    client = NotionAuth.get_client()
    with _lock(key):
        page = None
        page_id = _page_ids.get(key)
        if page_id:
            try:
                page = client.pages.update(page_id=page_id, properties=properties)
            except APIResponseError as error:
                if error.code not in GONE:
                    raise
                _page_ids.pop(key, None)
        if page is None:
            page = _own_page(key)
            if page:
                page = client.pages.update(page_id=page["id"], properties=properties)
            else:
                page = client.pages.create(parent={"database_id": database_id(config.OCENY_II_TITLE)},
                                           properties=properties)
                _page_ids[key] = page["id"]
    kept = _text(page["properties"].get("Komentarz")) if komentarz is None else komentarz
    row = {"cid": found["id"], "uid": user.id, "ocena": ocena, "komentarz": kept, "saved_at": saved_at}
    with _grades_ii_lock:
        _saved[key] = row
        _grades_ii["by_key"][key] = row
    return saved_at


def add_comment(user, cid, komentarz):
    """A new row in Komentarze II etap; returns the save time (ISO, Europe/Warsaw)."""
    found = candidate(cid)
    if found is None:
        raise LookupError(cid)
    # a create resent after a Notion 5xx carries the same Klucz, so its twin row is read once
    key = str(uuid.uuid4())
    saved_at = datetime.now(WARSAW).isoformat(timespec="seconds")
    NotionAuth.get_client().pages.create(parent={"database_id": database_id(config.KOMENTARZE_II_TITLE)}, properties={
        "Nazwa": {"title": _rich_text(f"{found['name']} — {user.name}")},
        "Kandydat": {"relation": [{"id": found["id"]}]},
        "Oceniający": {"people": [{"id": user.id}]},
        "Komentarz": {"rich_text": _rich_text(komentarz)},
        "Czas": {"date": {"start": saved_at}},
        "Klucz": {"rich_text": _rich_text(key)},
    })
    row = {"cid": found["id"], "komentarz": komentarz}
    with _comments_ii_lock:
        _added[key] = row
        _comments_ii["by_key"][key] = row
    return saved_at


def _comment_rows():
    """Every live Komentarze II etap row, one per Klucz, cached for 60 s; this process's own adds count at once."""
    with _comments_ii_lock:
        now = time.monotonic()
        if _comments_ii["at"] is None or now - _comments_ii["at"] >= CANDIDATES_TTL:
            rows = {}
            for page in _query_all(database_id(config.KOMENTARZE_II_TITLE)):
                p = page["properties"]
                key = _text(p.get("Klucz"))
                linked = (p.get("Kandydat") or {}).get("relation") or []
                if page.get("archived") or page.get("in_trash") or not (key and linked):
                    continue
                rows[key] = {"cid": linked[0]["id"], "komentarz": _text(p.get("Komentarz"))}
            rows.update((key, row) for key, row in _added.items() if key not in rows)
            _comments_ii["by_key"], _comments_ii["at"] = rows, now
        return list(_comments_ii["by_key"].values())


def _live_rows(title):
    """Klucz -> {cid, uid, ocena, komentarz, saved_at} of the live rows of a grades database, the newest per Klucz:
    a create resent after a Notion 5xx can leave a twin row"""
    rows = {}
    for page in _query_all(database_id(title)):
        key = _text(page["properties"].get("Klucz"))
        cid, _, uid = key.partition("|")
        grade = _grade(page, key)
        if not (cid and uid and grade):
            continue
        if key not in rows or grade["saved_at"] > rows[key]["saved_at"]:
            rows[key] = {"cid": cid, "uid": uid, "ocena": grade["ocena"], "komentarz": grade["komentarz"],
                         "saved_at": grade["saved_at"]}
    return rows


def _rows_ii():
    """Every reviewer's live Oceny II etap rows, cached for 60 s; this process's own saves count at once."""
    with _grades_ii_lock:
        now = time.monotonic()
        if _grades_ii["at"] is None or now - _grades_ii["at"] >= CANDIDATES_TTL:
            rows = _live_rows(config.OCENY_II_TITLE)
            for key, row in _saved.items():
                if key not in rows or row["saved_at"] >= rows[key]["saved_at"]:
                    rows[key] = row
            _grades_ii["by_key"], _grades_ii["at"] = rows, now
        return list(_grades_ii["by_key"].values())


def progress():
    """Graded/total per active member. Counts only: no grade or comment leaves this function."""
    ids = {c["id"] for c in candidates()}
    graded = defaultdict(set)
    for row in _rows_ii():
        if row["ocena"] is not None:
            graded[row["uid"]].add(row["cid"])
    rows = [{"name": m["name"], "graded": len(graded[pid] & ids), "total": len(ids)}
            for pid, m in auth.members().items() if m["active"]]
    return sorted(rows, key=lambda r: r["name"].lower())


def _averages(rows):
    """Candidate id -> average of the grades in the rows; no count, so a single grade is harder to work out."""
    values = defaultdict(list)
    for row in rows:
        if row["ocena"] is not None:
            values[row["cid"]].append(row["ocena"])
    return {cid: sum(v) / len(v) for cid, v in values.items()}


def averages():
    """Candidate id -> average of every reviewer's stage II grades."""
    return _averages(_rows_ii())


def _fold(text):
    """Text without case or Polish letters, for alphabetical order and name matching."""
    text = unicodedata.normalize("NFKD", text.replace("ł", "l").replace("Ł", "L"))
    return "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()


def _anonymous(comments):
    """Non-empty comments sorted by text, so their order tells neither the author nor the save time."""
    return sorted((c.strip() for c in comments if c.strip()), key=_fold)


def comments(user, cid):
    """Every comment on the candidate except the session user's stage II grade comment (it is in their text box):
    added comments, stage II and stage I ones in one list, without author, time or stage"""
    wanted = cid.replace("-", "")
    stage2 = [row["komentarz"] for row in _rows_ii() if row["cid"] == cid and row["uid"] != user.id]
    added = [row["komentarz"] for row in _comment_rows() if row["cid"].replace("-", "") == wanted]
    return _anonymous(stage2 + added + _stage1_data()["comments"].get(cid, []))


def _stage1_data():
    """Stage I comments per candidate, and stage I averages; cached for 10 minutes."""
    with _stage1_lock:
        now = time.monotonic()
        if _stage1["at"] is None or now - _stage1["at"] >= STAGE1_TTL:
            rows = list(_live_rows(config.OCENY_TITLE).values())
            by_candidate = defaultdict(list)
            for row in rows:
                by_candidate[row["cid"]].append(row["komentarz"])
            _stage1["comments"] = {c: _anonymous(comments) for c, comments in by_candidate.items()}
            _stage1["averages"] = _averages(rows)
            _stage1["at"] = now
        return _stage1


def stage1_averages():
    """Candidate id -> average of every reviewer's stage I grades."""
    return _stage1_data()["averages"]


def _name_key(name):
    """A name without case, Polish letters or word order: sign-ups are typed by hand."""
    return " ".join(sorted(_fold(name).split()))


def _signup_list():
    """Interview sign-ups [{name, emails, group, at}], cached for 60 s."""
    with _signups_lock:
        now = time.monotonic()
        if _signups["at"] is None or now - _signups["at"] >= CANDIDATES_TTL:
            signups = []
            for page in _query_all(config.ZAPISY_DATABASE_ID):
                p = page["properties"]
                people = (p.get("Auto-osoba") or {}).get("people") or []
                signups.append({
                    "name": _text(p.get("Imię i nazwisko")).strip(),
                    "emails": {((x.get("person") or {}).get("email") or "").strip().lower() for x in people} - {""},
                    "group": _select(p.get("Wybór grupy")),
                    "at": _date(p.get("Auto-czas")) or page.get("created_time") or "",
                })
            _signups["list"], _signups["at"] = signups, now
        return _signups["list"]


def groups():
    """(candidate id -> chosen group, sign-ups matching no invited candidate). A sign-up matches by name, else by the
    e-mail of its Notion account; of a candidate's sign-ups the newest with a group wins"""
    by_name, by_email = {}, {}
    for c in candidates():
        by_name.setdefault(_name_key(c["name"]), c["id"])
        by_email.setdefault(c["email"].strip().lower(), c["id"])
    by_name.pop("", None)
    by_email.pop("", None)
    chosen, unmatched = {}, []
    for signup in sorted(_signup_list(), key=lambda s: s["at"]):
        if not (signup["name"] or signup["group"]):
            continue  # a row added and left empty
        cid = by_name.get(_name_key(signup["name"])) or next(
            (by_email[e] for e in sorted(signup["emails"]) if e in by_email), None)
        if cid is None:
            unmatched.append(signup)
        elif signup["group"] or cid not in chosen:
            chosen[cid] = signup["group"]
    return {cid: group for cid, group in chosen.items() if group}, unmatched
