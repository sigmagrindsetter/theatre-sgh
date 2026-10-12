"""Recruitment review panel (REVIEW_PANEL_SPEC.md §5): app, routes, templates."""
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from panel import auth, ingest, store
from panel.auth import User, require_reviewer
from panel.web import templates

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(ingest.router)
app.include_router(auth.router)
app.add_exception_handler(auth.AuthError, auth.auth_error_handler)


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
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/k/{cid}")
def card(request: Request, cid: str, user: User = Depends(require_reviewer)):
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    context = {"user": user, "c": candidate, "grade": store.my_grade(user, candidate["id"])}
    return templates.TemplateResponse(request, "candidate.html", context)


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ocena: Annotated[StrictInt, Field(ge=1, le=10)] | None
    komentarz: Annotated[StrictStr, Field(max_length=2000)]


@app.put("/api/oceny/{cid}")
def put_grade(request: Request, cid: str, grade: Grade, user: User = Depends(require_reviewer)):
    if not auth.same_origin(request):
        raise HTTPException(403, "Brak dostępu")
    candidate = store.candidate(cid)
    if candidate is None:
        raise HTTPException(404, "Nie ma takiego kandydata")
    return {"saved_at": store.save_grade(user, candidate["id"], grade.ocena, grade.komentarz)}
