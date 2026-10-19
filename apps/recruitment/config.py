"""
Recruitment review panel configuration (REVIEW_PANEL_SPEC.md §6.4)
IDs are stored in code (not secrets); each value can be overridden by an env var of the same name
"""
import os

# Notion pages created by the board (§4): "Rekrutacja X 2026" and its restricted child "Ściśle tajne"
PARENT_PAGE_ID = os.getenv("PARENT_PAGE_ID", "3f73f4160a3780f5afc1d195032e5501")
BOARD_PAGE_ID = os.getenv("BOARD_PAGE_ID", "3f73f4160a37806b8fa9feb5cfdb083b")

# Databases under the board page, found by title
KANDYDACI_TITLE = "Kandydaci"
OCENY_TITLE = "Oceny"  # stage I: kept as a record, the panel reads only its comments
OCENY_II_TITLE = "Oceny II etap"

# Interview sign-ups of stage II ("Wybór grupy"); a workspace-level database shared with the integration
ZAPISY_DATABASE_ID = os.getenv("ZAPISY_DATABASE_ID", "d303f4160a3782eb876b815d54dae4df")

# Lista członków (read-only): Person, Aktywny członek
MEMBERS_DATABASE_ID = os.getenv("MEMBERS_DATABASE_ID", "18a3f4160a3780c884bcd88c8e0c49b7")

# Google Form responses; the sheet is reused from 2025, hence the cutoff
SHEET_ID = os.getenv("SHEET_ID", "1kBhtnYQNuuunlXxdDehMl85Pg6Q215W7w03qmaXCPGM")
SHEET_NAME = os.getenv("SHEET_NAME") or None  # None = first sheet
CUTOFF_DATE = os.getenv("CUTOFF_DATE", "2026-09-01")

# Public URL of the Cloud Run service; OAuth redirect URI and Scheduler OIDC audience derive from it
BASE_URL = os.getenv("BASE_URL", "https://panel-rekrutacja-590133883335.europe-west1.run.app")

# Identity whose Google OIDC token may call POST /internal/ingest (Cloud Scheduler)
SCHEDULER_SA_EMAIL = os.getenv("SCHEDULER_SA_EMAIL", "panel-runtime@theatre-sgh.iam.gserviceaccount.com")
