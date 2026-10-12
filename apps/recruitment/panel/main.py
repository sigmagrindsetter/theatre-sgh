"""Recruitment review panel (REVIEW_PANEL_SPEC.md §5): app, routes, templates."""
import csv
import io
import logging
import sys
import traceback
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from panel import auth, ingest, ratelimit, store
from panel.auth import User, require_board, require_reviewer
from panel.web import templates

NO_STORE = {"Cache-Control": "no-store"}
CSV_HEADER = ["Kandydat", "Email", "Średnia", "Liczba ocen", "Oceniający", "Ocena", "Komentarz", "Ostatni zapis"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://i.ytimg.com; "
                               "frame-src https://www.youtube-nocookie.com; frame-ancestors 'none'; base-uri 'none'; "
                               "form-action 'self'; object-src 'none'; script-src 'self'; style-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
ERRORS = {404: ("Nie znaleziono", "Nie ma takiej strony ani takiego kandydata."),
          500: ("Błąd serwera", "Coś poszło nie tak. Spróbuj ponownie za chwilę.")}
TOO_MANY = "Za dużo zapisów naraz. Spróbuj ponownie za chwilę."
MAX_GRADE_BODY = 64 * 1024  # a 2000-character comment is at most ~24 KiB of JSON


class PathOnly(logging.Filter):
    """Drops query strings from uvicorn's access log: /auth/callback carries the OAuth code."""

    def filter(self, record):
        if isinstance(record.args, tuple):
            record.args = tuple(arg.split("?", 1)[0] if isinstance(arg, str) and arg.startswith("/") else arg
                                for arg in record.args)
        return True


logging.getLogger("uvicorn.access").addFilter(PathOnly())

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(ingest.router)
app.include_router(auth.router)
app.add_exception_handler(auth.AuthError, auth.auth_error_handler)


def _api(request):
    return request.url.path.startswith(("/api/", "/internal/"))


def _error(request, status_code):
    title, message = ERRORS[status_code]
    if _api(request):
        return JSONResponse({"detail": title}, status_code=status_code)
    context = {"title": title, "message": message}
    return templates.TemplateResponse(request, "auth_message.html", context, status_code=status_code)


@app.middleware("http")
async def harden(request: Request, call_next):
    """Security headers on every response; an unhandled error is logged by type and stack, never its message."""
    try:
        response = await call_next(request)
    except Exception as error:
        stack = "".join(traceback.format_tb(error.__traceback__))
        print(f"Unhandled {type(error).__name__} on {request.method} {request.url.path}\n{stack}",
              file=sys.stderr, flush=True)
        response = _error(request, 500)
    response.headers.update(SECURITY_HEADERS)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404 and not _api(request):
        return _error(request, 404)
    return await http_exception_handler(request, exc)


# Cloud Run's front end answers paths ending in "z" itself, so the deploy smoke test uses /health
@app.get("/health")
@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


@app.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html")


@app.get("/")
def index(request: Request, user: User = Depends(require_reviewer)):
    grades = store.my_grades(user)
    rows = [(c, grades.get(c["id"])) for c in store.candidates()]
    context = {"user": user, "rows": rows, "progress": store.progress()}
    return templates.TemplateResponse(request, "index.html", context, headers=NO_STORE)


@app.get("/k/{cid}")
def card(request: Request, cid: str, user: User = Depends(require_reviewer)):
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    context = {"user": user, "c": candidate, "grade": store.my_grade(user, candidate["id"])}
    return templates.TemplateResponse(request, "candidate.html", context, headers=NO_STORE)


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ocena: Annotated[StrictInt, Field(ge=1, le=10)] | None
    komentarz: Annotated[StrictStr, Field(max_length=2000)]


def write_guard(request: Request, cid: str, user: User = Depends(require_reviewer)) -> User:
    """CSRF, content type and rate limit of a grade save, checked before the body is validated."""
    if not auth.same_origin(request):
        raise HTTPException(403, "Brak dostępu")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Wymagany format JSON")
    wait = ratelimit.retry_after(user.id, cid)
    if wait:
        raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(wait)})
    return user


async def grade_body(request: Request, user: User = Depends(write_guard)) -> Grade:
    """The grade, read only after write_guard and capped: a body parameter is parsed in full before any check."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_GRADE_BODY:
            raise HTTPException(413, "Za długi zapis")
    try:
        return Grade.model_validate_json(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(include_url=False, include_input=False)) from None


@app.put("/api/oceny/{cid}")
def put_grade(cid: str, grade: Grade = Depends(grade_body), user: User = Depends(write_guard)):
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    return {"saved_at": store.save_grade(user, candidate["id"], grade.ocena, grade.komentarz)}


def _ranking():
    """Candidates by average grade, best first and ungraded last, each with every reviewer's rows."""
    members = auth.members()
    by_candidate = defaultdict(list)
    for grade in store.all_grades():
        member = members.get(grade["reviewer_id"])
        reviewer = (member and member["name"]) or grade["reviewer_name"] or grade["reviewer_id"]
        by_candidate[grade["candidate_id"]].append({**grade, "reviewer": reviewer})
    ranking = []
    for c in store.candidates():
        grades = sorted(by_candidate[c["id"]], key=lambda g: g["reviewer"].lower())
        values = [g["ocena"] for g in grades if g["ocena"] is not None]
        ranking.append({"c": c, "grades": grades, "count": len(values),
                        "average": sum(values) / len(values) if values else None,
                        "min": min(values, default=None), "max": max(values, default=None)})
    return sorted(ranking, key=lambda r: (r["average"] is None, -(r["average"] or 0)))


def _by_reviewer(ranking):
    """[(reviewer name, [(candidate, grade)])] in ranking order, reviewers by name."""
    reviewers = {}
    for row in ranking:
        for grade in row["grades"]:
            reviewers.setdefault(grade["reviewer_id"], (grade["reviewer"], []))[1].append((row["c"], grade))
    return sorted(reviewers.values(), key=lambda item: item[0].lower())


@app.get("/zarzad")
def board_page(request: Request, user: User = Depends(require_board)):
    ranking = _ranking()
    try:
        skipped = ingest.skipped_submissions()
    except Exception as error:  # the sheet is only one section of this page
        print(f"Zarząd: skipped submissions unavailable ({type(error).__name__})")
        skipped = None
    context = {"user": user, "ranking": ranking, "reviewers": _by_reviewer(ranking),
               "progress": store.progress(), "skipped": skipped}
    return templates.TemplateResponse(request, "zarzad.html", context, headers=NO_STORE)


def _cell(value):
    """CSV cell text; a leading apostrophe stops spreadsheets from reading it as a formula."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


@app.get("/zarzad/export.csv")
def board_csv(user: User = Depends(require_board)):
    out = io.StringIO()
    # Semicolons and a decimal comma: Excel with Polish regional settings opens such a file in columns
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in _ranking():
        average = f"{row['average']:.2f}".replace(".", ",") if row["average"] is not None else ""
        candidate = [row["c"]["name"], row["c"]["email"], average, row["count"]]
        for grade in row["grades"] or [None]:
            reviewer = ([grade["reviewer"], grade["ocena"], grade["komentarz"], grade["saved_local"]]
                        if grade else [None] * 4)
            writer.writerow([_cell(value) for value in candidate + reviewer])
    filename = f"oceny-{datetime.now(store.WARSAW):%Y-%m-%d}.csv"
    headers = {**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"'}
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
