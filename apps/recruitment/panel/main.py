"""Recruitment review panel (REVIEW_PANEL_SPEC.md §5): app, routes, templates."""
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from panel import ingest
from panel.web import templates

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(ingest.router)


@app.get("/healthz")
def healthz():
    return PlainTextResponse("ok")


@app.get("/privacy")
def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html")


@app.get("/terms")
def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html")
