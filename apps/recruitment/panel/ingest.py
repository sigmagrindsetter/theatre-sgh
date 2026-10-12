"""
Sheet → Kandydaci (REVIEW_PANEL_SPEC.md §5): create-only, one page per e-mail, never updated
Logs carry sheet row numbers and error classes only, never names, e-mails or links
"""
import hmac
import threading
from datetime import datetime

import google.auth.transport.requests
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from google.oauth2 import id_token

import config
from panel.notion_dbs import database_id
from panel.web import secret
from panel.youtube import parse_video, thumbnail
from shared.auth import GoogleAuth, NotionAuth

router = APIRouter()

COLUMNS = {
    "Sygnatura czasowa": "czas",
    "Imię i nazwisko:": "name",
    "Adres e-mail:": "email",
    "Na jakiej uczelni studiujesz?": "uczelnia",
    "Na którym roku studiów jesteś?": "rok",
    "W jakich obszarach chcesz rozwijać się w naszej organizacji?": "obszary",
    "Tu wstaw link do swojego filmiku rekrutacyjnego": "filmik",
}
THUMBNAIL_NAME = "miniatura.jpg"
SHEET_TIMEOUT = 30  # seconds; gspread waits forever by default, and a stalled read would hold _lock

_lock = threading.Lock()
_created = set()  # e-mails created by this process: a Kandydaci query can lag right after creation


def submissions():
    """Sheet rows since the cutoff in sheet order, with their sheet row number and parsed time."""
    client = GoogleAuth.get_client()
    client.http_client.set_timeout(SHEET_TIMEOUT)
    spreadsheet = client.open_by_key(config.SHEET_ID)
    sheet = spreadsheet.worksheet(config.SHEET_NAME) if config.SHEET_NAME else spreadsheet.sheet1
    values = sheet.get_all_values()
    header = [cell.strip() for cell in values[0]]
    columns = {key: header.index(title) for title, key in COLUMNS.items()}
    cutoff = datetime.fromisoformat(config.CUTOFF_DATE)
    rows = []
    for number, cells in enumerate(values[1:], start=2):
        row = {key: cells[i].strip() if i < len(cells) else "" for key, i in columns.items()}
        try:
            time = datetime.strptime(row["czas"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            if row["czas"]:
                print(f"Ingest: row {number} has an unreadable timestamp, ignored")
            continue
        if time >= cutoff:
            rows.append({**row, "row": number, "time": time})
    return rows


def email_key(email):
    return "".join(email.split()).lower()


def plan(rows):
    """{e-mail: (row, video)} for the first row with a valid link per e-mail, and the rows of e-mails without one."""
    accepted = {}
    for row in rows:
        email, video = email_key(row["email"]), parse_video(row["filmik"])
        if email and video and email not in accepted:
            accepted[email] = (row, video)
    skipped = [row for row in rows if email_key(row["email"]) not in accepted]
    return accepted, skipped


def skipped_submissions():
    """Rows since the cutoff whose e-mail has no row with a valid link; computed on demand, never stored."""
    _, skipped = plan(submissions())
    return [{key: row[key] for key in ("row", "czas", "name", "email", "filmik")} for row in skipped]


def existing_emails(client, kandydaci):
    emails, cursor = set(), None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        response = client.databases.query(kandydaci, **kwargs)
        for page in response["results"]:
            email = page["properties"].get("Email", {}).get("email")
            if email:
                emails.add(email_key(email))
        if not response.get("has_more"):
            return emails
        cursor = response["next_cursor"]


def _text(value):
    return [{"type": "text", "text": {"content": value[:2000]}}] if value else []


def _option(value):
    """Select option name: Notion forbids commas and allows 100 characters."""
    return " ".join(value.replace(",", " ").split())[:100]


def properties(email, row, video):
    obszary = dict.fromkeys(name for name in map(_option, row["obszary"].split(", ")) if name)
    props = {
        "Imię i nazwisko": {"title": _text(row["name"])},
        "Email": {"email": email},
        "Czas": {"date": {"start": row["time"].isoformat(), "time_zone": "Europe/Warsaw"}},
        "Uczelnia": {"rich_text": _text(row["uczelnia"])},
        "Obszary": {"multi_select": [{"name": name} for name in obszary]},
        "Filmik": {"url": row["filmik"][:2000]},
        "Filmik znormalizowany": {"url": video.url},
        "Status filmiku": {"select": {"name": video.kind}},
    }
    if _option(row["rok"]):
        props["Rok studiów"] = {"select": {"name": _option(row["rok"])}}
    return props


def body(row, video):
    if video.kind == "youtube":
        player = {"type": "video", "video": {"type": "external", "external": {"url": video.url}}}
    else:
        player = {"type": "embed", "embed": {"url": video.url}}
    raw = {"type": "text", "text": {"content": row["filmik"][:2000], "link": {"url": video.url}}}
    return [player, {"type": "paragraph", "paragraph": {"rich_text": [raw]}}]


def upload_thumbnail(client, video_id):
    """File Upload id of the YouTube thumbnail (usable as both Zdjęcie and cover), or None if there is none."""
    image = thumbnail(video_id)
    if not image:
        return None
    content, content_type = image
    upload = client.request(path="file_uploads", method="POST", body={
        "mode": "single_part", "filename": THUMBNAIL_NAME, "content_type": content_type})
    # The SDK sends JSON only; its httpx client carries the base URL, token and Notion-Version
    response = client.client.post(f"file_uploads/{upload['id']}/send",
                                  files={"file": (THUMBNAIL_NAME, content, content_type)})
    response.raise_for_status()
    return upload["id"]


def create_candidate(client, kandydaci, email, row, video):
    page = {"parent": {"database_id": kandydaci}, "properties": properties(email, row, video),
            "children": body(row, video)}
    if video.video_id:
        try:
            upload = upload_thumbnail(client, video.video_id)
        except Exception as error:  # the photo is optional, the candidate is not
            upload, reason = None, type(error).__name__
        else:
            reason = "no thumbnail"
        if upload:
            image = {"type": "file_upload", "file_upload": {"id": upload}}
            page["properties"]["Zdjęcie"] = {"files": [{**image, "name": THUMBNAIL_NAME}]}
            page["cover"] = image
        else:
            print(f"Ingest: row {row['row']} without photo ({reason})")
    client.pages.create(**page)


def run_ingest(kandydaci):
    """Create a Kandydaci page for every new e-mail; serialised so a waiting caller sees earlier creations."""
    with _lock:
        accepted, skipped = plan(submissions())
        client = NotionAuth.get_client()
        known = existing_emails(client, kandydaci) | _created
        result = {"created": 0, "existing": 0, "skipped": len(skipped),
                  "skipped_rows": [row["row"] for row in skipped], "errors": 0}
        for email, (row, video) in accepted.items():
            if email in known:
                result["existing"] += 1
                continue
            try:
                create_candidate(client, kandydaci, email, row, video)
            except Exception as error:  # one bad row must not block the others
                print(f"Ingest: row {row['row']} not created ({type(error).__name__})")
                result["errors"] += 1
                continue
            _created.add(email)
            result["created"] += 1
        print("Ingest: {created} created, {existing} existing, {skipped} skipped {skipped_rows}, "
              "{errors} errors".format(**result))
        return result


def _refusal(request):
    """None if the caller may run ingest (Apps Script token or Scheduler OIDC token), else the HTTP status."""
    token = request.headers.get("x-ingest-token")
    if token is not None:
        return None if hmac.compare_digest(token.encode(), secret("INGEST_TOKEN").encode()) else 403
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return 401
    try:
        claims = id_token.verify_oauth2_token(
            credentials, google.auth.transport.requests.Request(), audience=config.BASE_URL)
    except Exception:  # bad signature, audience, expiry or certificate fetch
        return 403
    if claims.get("email") == config.SCHEDULER_SA_EMAIL and claims.get("email_verified") is True:
        return None
    return 403


@router.post("/internal/ingest")
def ingest(request: Request):
    status = _refusal(request)
    if status:
        return Response(status_code=status)
    try:
        kandydaci = database_id(config.KANDYDACI_TITLE)
    except LookupError as error:
        return JSONResponse({"detail": str(error)}, status_code=503)
    return run_ingest(kandydaci)
