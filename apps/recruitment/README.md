# Recruitment review panel

A small web app where active members of Teatr SGH watch the candidates' recruitment videos and grade
them (1–10 plus a comment). It exists because Notion Plus cannot hide rows per user.
[`REVIEW_PANEL_SPEC.md`](REVIEW_PANEL_SPEC.md) is the source of truth; §10 explains why it is built
this way, §11 describes what the panel shows in stage II (interviews from 2026-10-21): only candidates invited to
stage II, grouped by the interview group they signed up for, graded afresh; every reviewer sees the
averages of the stage II and stage I grades (never single grades or their number) and one list of all
comments of both stages without author or stage (on the card, and in a "Komentarze" toggle under each
candidate on the list). On the list a reviewer also changes their own stage II grade and adds any number of
anonymous comments.

## Architecture

- One FastAPI service, `panel-rekrutacja`, on Cloud Run (europe-west1, max 1 instance, scales to zero); Jinja2 templates and two small vanilla JS files (card, list), no build step.
- Notion is the only store: `Kandydaci`, `Oceny` (stage I, kept as a record), `Oceny II etap` and `Komentarze II etap` under the restricted board page "Ściśle tajne"; `Lista członków` (read-only) decides who may sign in (`Aktywny członek`); the interview sign-ups ("Zgłoszenia na rozmowę rekrutacyjną", read-only) give each candidate's group.
- Sign in with Notion (public integration, identity only); the session is a signed cookie, `panel_session`, valid 30 days.
- Ingest: Google Form → Sheet → `POST /internal/ingest`, called by Apps Script on submit and by Cloud Scheduler every 10 minutes; create-only, one candidate per e-mail.
- Delivery: GitHub Actions → Artifact Registry → Cloud Run through Workload Identity Federation; secrets in Secret Manager.

Code: `config.py` (IDs and URLs, each overridable by an env var), `panel/main.py` (routes, security
headers, error pages), `panel/auth.py` (OAuth, session, membership), `panel/store.py` (all Notion reads
and writes of grades and comments), `panel/ingest.py`, `panel/ratelimit.py`, `setup_notion.py` (schema),
`apps_script/` (form trigger), `infra/bootstrap.sh` (one-time GCP setup, already applied).

## Local development

From the repository root, with Python 3.11 (what CI and the image use):

```sh
python3.11 -m venv .venv
.venv/bin/pip install -r apps/recruitment/requirements.txt pytest
```

Settings come from the environment or the gitignored `.env` at the repository root (`shared/auth.py`
loads it): `NOTION_API_TOKEN` (integration token), `SESSION_SECRET` and `INGEST_TOKEN` (any random
value locally, never the production ones), `NOTION_OAUTH_CLIENT_ID`/`_SECRET` (only for the real
sign-in), and `GOOGLE_CLIENT_EMAIL`/`GOOGLE_PRIVATE_KEY` for the Sheet (without them, Application
Default Credentials are used). Any value in `config.py` can be overridden the same way.

**Never point a local run at the production databases.** Create a scratch subpage under "Ściśle
tajne", connect the integration to it, create the schema there and set `BOARD_PAGE_ID` to it:

```sh
PARENT_PAGE_ID=<id of Ściśle tajne> BOARD_PAGE_ID=<scratch page id> .venv/bin/python apps/recruitment/setup_notion.py
cd apps/recruitment
BASE_URL=http://localhost:8000 BOARD_PAGE_ID=<scratch page id> PYTHONPATH=../.. ../../.venv/bin/uvicorn panel.main:app --reload --port 8000
```

Sign in with Notion only works on the deployed URL (the integration's only redirect URI), so locally
mint a session cookie for an active member's Notion user id (the `Person` of their row in Lista
członków; `NotionAuth.get_client().users.list()` shows ids):

```sh
../../.venv/bin/python -c 'import os, sys; from dotenv import load_dotenv; from itsdangerous import URLSafeTimedSerializer as S
load_dotenv(); print(S(os.environ["SESSION_SECRET"], salt="panel-session").dumps({"uid": sys.argv[1], "name": "Dev", "email": ""}))' <notion-user-id>
```

Add it in the browser's dev tools as cookie `panel_session` for `localhost:8000`, path `/`. Grades
saved locally go to the scratch `Oceny`; `curl -X POST -H "X-Ingest-Token: <INGEST_TOKEN>"
http://localhost:8000/internal/ingest` imports the Sheet into the scratch `Kandydaci`.

## Tests

```sh
.venv/bin/python -m pytest -q apps/recruitment/tests
```

They run against an in-memory Notion (`tests/fake_notion.py`) with dummy secrets and never reach
Notion or Google. `panel-deploy` runs them on every pull request and every run.

## Deployment

Nothing deploys from a laptop: no `gcloud run deploy`, `gcloud builds submit`, `docker push`,
Secret Manager writes or production schema changes from a workstation. The only identity that can
deploy is `panel-deployer`, reachable only from GitHub Actions on `main`.

- `panel-deploy.yml` (by hand, on `main`): tests, image checks, build and push, a check that every secret has a version, deploy, the Scheduler job `panel-ingest`, a `/health` smoke test (Cloud Run's front end answers `/healthz` itself; the container check uses it locally). A merge does not deploy. Pull requests run only the tests and image checks.
- `panel-shutdown.yml` (by hand): deletes the Scheduler job `panel-ingest`, then the service. Notion data, images, secrets and WIF stay, so `panel-deploy` brings the panel back on the same URL.
- `panel-secrets.yml` (by hand): copies the five GitHub secrets into Secret Manager and destroys older versions.
- `panel-setup.yml` (by hand): checks that the interview sign-ups are readable, creates or completes the Notion schema under the production board page (`Oceny II etap`, `Komentarze II etap` and the `II etap` checkbox included), and the board's stage I views "wg oceniającego", "wg kandydata" (Oceny) and "ranking" (Kandydaci). A view is created only when none has its name, so the board may change or delete views in Notion; a deleted one comes back on the next run.

First deploy: run `panel-deploy` → it stops at the secrets check → run `panel-secrets` → re-run `panel-deploy`.

## Operations

- **Manual ingest** (idempotent): `curl -fsS -X POST -H "X-Ingest-Token: $INGEST_TOKEN" https://panel-rekrutacja-590133883335.europe-west1.run.app/internal/ingest` returns the counts. A project owner can read the token in Secret Manager; never paste it into code, commits or chats.
- **`PANEL_MIN_INSTANCES`** (repository variable): `1` during the grading window (no cold starts, about 4.50 USD per two weeks), `0` afterwards; it takes effect on the next `panel-deploy` run.
- **Rotating a secret**: update the GitHub secret, run `panel-secrets`, then `panel-deploy` (instances read secrets when they start). Rotating `SESSION_SECRET` logs everyone out. `INGEST_TOKEN` also lives in the Apps Script: see [Rotate the token](apps_script/README.md#rotate-the-token). Regenerate `NOTION_OAUTH_CLIENT_SECRET` in the Notion developer portal first. `NOTION_API_TOKEN` is shared with the repository's other Notion workflows.
- **Apps Script trigger**: install, verify and troubleshoot with [`apps_script/README.md`](apps_script/README.md).
- **Saves** (grades and comments) are limited per user (60 distinct candidate-seconds a minute, at most two requests per candidate per second); over that the API answers 429 with `Retry-After`; the card retries by itself, the list asks to try again.
- **Logs** carry counts, sheet row numbers, error class names and stack frames, never comments, names, e-mails, links, OAuth codes or tokens; the access log drops query strings. Cloud Run's own request log still records full URLs, including the single-use `code` of `/auth/callback`.
- **Removing a candidate** (withdrawn, or a second e-mail of the same person): delete their row in the Sheet first, then delete the page in Kandydaci. Ingest is create-only and re-creates, without grades, any candidate whose Sheet row is still there.
- **Who is in stage II**: the `II etap` checkbox in Kandydaci; untick it to take a candidate off the panel (their grades stay in Notion).
- **Groups**: a sign-up matches a candidate by name (case, Polish letters and word order ignored), else by the e-mail of the Notion account that made it. Sign-ups matching nobody are listed under "Zapisy bez kandydata" on the panel's main page; fix the name in the sign-up row.
- **After the window**: the board reads the grades and comments in Notion (`Oceny II etap`, `Komentarze II etap`) and runs `panel-shutdown`. Then delete the Apps Script trigger (Triggers → `onFormSubmit` → Delete trigger): with the service gone, every form submission fails with HTTP 404 and Apps Script e-mails the owner. To bring the panel back, run `panel-deploy` and reinstall the trigger ([`apps_script/README.md`](apps_script/README.md)).

## Privacy model

- Every grade write and every read of a grade with its author is scoped to the session user: no endpoint takes a user id, and each `Oceny II etap` row is re-checked by its `Klucz` (`<candidate id>|<user id>`); a `Komentarze II etap` row is only ever created, never read back with its author. Other reviewers' grades, and all stage I grades, leave `store.py` only as a per-candidate average (no count), their comments only without author or time and sorted by text. The progress table shows counts only.
- In Notion the grade databases sit under "Ściśle tajne" (board and integration only), so reviewers cannot open them there.
- The bot token is used only on Cloud Run (and by `panel-setup` for the schema); the user's OAuth token is discarded at sign-in.
- CSRF: OAuth `state` cookie, `SameSite=Lax` session cookie, same-origin check (`Sec-Fetch-Site` or `Origin`) on `PUT` and logout, and `PUT` accepts only `application/json`. Every response carries a strict CSP, `nosniff` and `strict-origin-when-cross-origin`; 404 and 500 pages show no error details.
