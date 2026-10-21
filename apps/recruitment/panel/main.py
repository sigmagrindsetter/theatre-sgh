"""Recruitment review panel (REVIEW_PANEL_SPEC.md §5, §11): app, routes, templates."""
import logging
import sys
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, StringConstraints, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from panel import auth, ingest, ratelimit, store
from panel.auth import User, require_reviewer
from panel.web import templates

NO_STORE = {"Cache-Control": "no-store"}
NO_GROUP = "Bez grupy"
SECURITY_HEADERS = {
    # Drive thumbnails redirect to lh3.googleusercontent.com
    "Content-Security-Policy": "default-src 'self'; "
                               "img-src 'self' https://i.ytimg.com https://drive.google.com https://lh3.googleusercontent.com; "
                               "frame-src https://www.youtube-nocookie.com https://drive.google.com; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'; object-src 'none'; script-src 'self'; style-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
}
ERRORS = {404: ("Nie znaleziono", "Nie ma takiej strony ani takiego kandydata."),
          500: ("Błąd serwera", "Coś poszło nie tak. Spróbuj ponownie za chwilę.")}
TOO_MANY = "Za dużo zapisów naraz. Spróbuj ponownie za chwilę."
MAX_BODY = 64 * 1024  # a 2000-character comment is at most ~24 KiB of JSON


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


def _groups():
    """store.groups(), or (None, []) when the sign-ups cannot be read: they only sort the candidates into groups."""
    try:
        return store.groups()
    except Exception as error:
        print(f"Interview sign-ups unavailable ({type(error).__name__})")
        return None, []


@app.get("/")
def index(request: Request, user: User = Depends(require_reviewer)):
    grades = store.my_grades(user)
    averages = store.averages()
    stage1 = store.stage1_averages()
    chosen, unmatched = _groups()
    sections = defaultdict(list)
    for c in store.candidates():
        group = chosen.get(c["id"], NO_GROUP) if chosen is not None else None
        sections[group].append((c, grades.get(c["id"]), averages.get(c["id"]), stage1.get(c["id"]),
                                store.comments(user, c["id"])))
    ordered = sorted(sections.items(), key=lambda item: (item[0] in (NO_GROUP, None), item[0] or ""))
    context = {"user": user, "sections": ordered, "signups_failed": chosen is None, "unmatched": unmatched,
               "progress": store.progress()}
    return templates.TemplateResponse(request, "index.html", context, headers=NO_STORE)


@app.get("/k/{cid}")
def card(request: Request, cid: str, user: User = Depends(require_reviewer)):
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    chosen, _ = _groups()
    context = {"user": user, "c": candidate, "grade": store.my_grade(user, candidate["id"]),
               "group": chosen.get(candidate["id"], NO_GROUP) if chosen is not None else None,
               "average": store.averages().get(candidate["id"]),
               "stage1_average": store.stage1_averages().get(candidate["id"]),
               "comments": store.comments(user, candidate["id"])}
    return templates.TemplateResponse(request, "candidate.html", context, headers=NO_STORE)


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ocena: Annotated[StrictInt, Field(ge=1, le=10)] | None
    komentarz: Annotated[StrictStr, Field(max_length=2000)] = None  # left out by the list: kept as it is; null is a 422


class Comment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    komentarz: Annotated[StrictStr, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def write_guard(request: Request, cid: str, user: User = Depends(require_reviewer)) -> User:
    """CSRF, content type and rate limit of a grade save or a comment, checked before the body is validated."""
    if not auth.same_origin(request):
        raise HTTPException(403, "Brak dostępu")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Wymagany format JSON")
    wait = ratelimit.retry_after(user.id, cid)
    if wait:
        raise HTTPException(429, TOO_MANY, headers={"Retry-After": str(wait)})
    return user


async def _body(request, model):
    """The body, read only after write_guard and capped: a body parameter is parsed in full before any check."""
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            raise HTTPException(413, "Za długi zapis")
    try:
        return model.model_validate_json(body)
    except ValidationError as error:
        raise RequestValidationError(error.errors(include_url=False, include_input=False)) from None


async def grade_body(request: Request, user: User = Depends(write_guard)) -> Grade:
    return await _body(request, Grade)


async def comment_body(request: Request, user: User = Depends(write_guard)) -> Comment:
    return await _body(request, Comment)


def _invited(cid):
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    return candidate


@app.put("/api/oceny/{cid}")
def put_grade(cid: str, grade: Grade = Depends(grade_body), user: User = Depends(write_guard)):
    return {"saved_at": store.save_grade(user, _invited(cid)["id"], grade.ocena, grade.komentarz)}


@app.post("/api/komentarze/{cid}")
def post_comment(cid: str, comment: Comment = Depends(comment_body), user: User = Depends(write_guard)):
    return {"saved_at": store.add_comment(user, _invited(cid)["id"], comment.komentarz)}
