# Form-submit trigger for the review panel

`Code.gs` tells the panel to import new candidates as soon as someone submits the recruitment
form. It sends no form answers: the panel reads them from the Sheet itself. If the script fails,
nothing is lost, because the panel also imports every 10 minutes (8:00–24:00); the script only
makes it quicker.

The script runs as the Google account of the person who adds the trigger in step 3, and that
person needs **edit access to the responses Sheet**. If they later lose that access, the trigger
stops; someone else with edit access then repeats step 3.

## Install (once per Sheet)

1. Open the form's responses Sheet → **Extensions → Apps Script**. In the editor, delete the
   sample `function myFunction() { }`, paste the whole content of
   [`Code.gs`](https://github.com/sigmagrindsetter/theatre-sgh/blob/main/apps/recruitment/apps_script/Code.gs)
   and click **Save** (disk icon).
2. **Project Settings** (gear icon, left bar) → **Script Properties** → **Add script property**,
   add two properties, then **Save script properties**:
   - `INGEST_URL` = `https://panel-rekrutacja-590133883335.europe-west1.run.app/internal/ingest`
   - `INGEST_TOKEN` = the value of the `INGEST_TOKEN` GitHub secret. GitHub never shows a saved
     secret, so get it from whoever generated it, or a GCP project owner can read it in Google
     Cloud Console → Secret Manager → `INGEST_TOKEN` → latest version → *View secret value*. If
     nobody can, follow [Rotate the token](#rotate-the-token). Paste it only here: never into
     the code, a commit, a chat or an e-mail.
3. **Triggers** (alarm-clock icon, left bar) → **Add Trigger** (bottom right) and choose:
   function `onFormSubmit`, deployment `Head`, event source `From spreadsheet`, event type
   `On form submit` (optionally *Failure notification settings* → *Notify me immediately*) →
   **Save**. Google asks you to authorise the script: pick your account → *Advanced* → *Go to …
   (unsafe)* → tick the permission if there is a checkbox → **Allow**. The warning appears for every self-written script; the only permission
   it needs is "Connect to an external service". Add the trigger once, not once per person.

## Verify

1. In the editor, pick `testIngest` in the function list next to **Run** and click **Run**. The
   execution log ends with a line like `Ingest HTTP 200: created=0 existing=8 skipped=1
   skipped_rows=1 errors=0`.
2. Submit a test response through the form with an e-mail no candidate uses and a link to any
   public YouTube video. Within a minute **Executions** (list icon, left bar) shows
   `onFormSubmit` as *Completed* and the candidate appears in **Kandydaci** (Notion →
   Rekrutacja X 2026 → Ściśle tajne).
3. Clean up in this order: first delete the test response's row in the Sheet (otherwise the
   10-minute import creates the candidate again), then open the test candidate in Kandydaci →
   **•••** → **Delete** (this archives the page; Trash keeps it for 30 days).

If a run fails, Executions shows the reason:

| Message | Fix |
|---|---|
| `Script Property … is missing` | Step 2 |
| `HTTP 401` or `HTTP 403` | `INGEST_TOKEN` differs from the panel's: copy it again or rotate it |
| `HTTP 404` | `INGEST_URL` is wrong; it must end with `/internal/ingest` |
| `HTTP 5xx` or a timeout | Problem in the panel; tell whoever maintains it. The 10-minute import catches up once it is fixed |

## Rotate the token

Do this if the token may have leaked or someone who knew it leaves. Needs admin rights on the
GitHub repository.

1. Generate a new value, e.g. `openssl rand -hex 32` in a terminal or Cloud Shell.
2. GitHub → repository **Settings → Secrets and variables → Actions** → `INGEST_TOKEN` →
   **Update** with the new value.
3. GitHub → **Actions** → run **panel-secrets**, then run **panel-deploy** (the panel reads the
   token when it starts).
4. Apps Script → Project Settings → Script Properties → edit `INGEST_TOKEN` → paste the new value
   → **Save script properties**, then run `testIngest` and check for `HTTP 200`.

Between steps 3 and 4 the script gets `HTTP 401`/`403`; submissions are still imported by the
10-minute import, which does not use this token.

Anyone who can edit the Sheet can open this script and read its Script Properties. The token only
lets them start an import early, but it is one more reason to restrict the Sheet's sharing.
