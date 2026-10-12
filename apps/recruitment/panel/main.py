"""Recruitment review panel (REVIEW_PANEL_SPEC.md §5): app, routes, templates."""
import csv
import io
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from panel import auth, ingest, store
from panel.auth import User, require_board, require_reviewer
from panel.web import templates

NO_STORE = {"Cache-Control": "no-store"}
CSV_HEADER = ["Kandydat", "Email", "Średnia", "Liczba ocen", "Oceniający", "Ocena", "Komentarz", "Ostatni zapis"]
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")

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
    writer = csv.writer(out, delimiter=",", lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for row in _ranking():
        average = f"{row['average']:.2f}" if row["average"] is not None else ""
        candidate = [row["c"]["name"], row["c"]["email"], average, row["count"]]
        for grade in row["grades"] or [None]:
            reviewer = ([grade["reviewer"], grade["ocena"], grade["komentarz"], grade["saved_local"]]
                        if grade else [None] * 4)
            writer.writerow([_cell(value) for value in candidate + reviewer])
    filename = f"oceny-{datetime.now(store.WARSAW):%Y-%m-%d}.csv"
    headers = {**NO_STORE, "Content-Disposition": f'attachment; filename="{filename}"'}
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8", headers=headers)
