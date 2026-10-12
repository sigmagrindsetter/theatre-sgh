import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

import config
from panel import ingest
from panel.main import app
from shared.auth import GoogleAuth

HEADER = ["Sygnatura czasowa", "Imię i nazwisko:", "Adres e-mail:", "Na jakiej uczelni studiujesz?",
          "Na którym roku studiów jesteś?", "W jakich obszarach chcesz rozwijać się w naszej organizacji? ",
          "Tu wstaw link do swojego filmiku rekrutacyjnego "]
ROWS = [
    ["2026-08-31 23:59:59", "Stary Kandydat", "stary@example.com", "SGH", "I SL", "Aktorstwo",
     "https://youtu.be/OldOldOld01"],
    ["2026-09-01 10:00:00", "Anna Testowa", "anna@example.com", "SGH", "I SL",
     "Aktorstwo, Scenografia, coś innego,np. dźwięk", "https://youtu.be/AnnaVideo01?si=Tracking123"],
    ["2026-09-02 11:00:00", "Bartek Próbny", " Bartek@Example.com ", "UW", "III SL", "Aktorstwo",
     "wyślę później"],
    ["2026-09-03 12:00:00", "Bartek Próbny", "bartek@example.com", "UW", "III SL", "Aktorstwo",
     "https://youtube.com/shorts/BartekVid02?feature=shared"],
    ["2026-09-04 13:00:00", "Anna Testowa", "ANNA@example.com ", "SGH", "I SL", "Aktorstwo",
     "https://youtu.be/AnnaVideo03"],
    ["2026-09-05 14:00:00", "Celina Bezlinku", "celina@example.com", "PW", "", "Reżyseria", "brak"],
    ["2026-09-06 15:00:00", "Dawid Dyskowy", "dawid@example.com", "SGH", "3 rok studiów magisterskich",
     "Technika", "https://drive.google.com/drive/folders/1FolderIdXyz?usp=sharing"],
]
NAMES = {row[1] for row in ROWS}
EMAILS = {row[2].strip().lower() for row in ROWS}


class FakeSheet:
    def __init__(self, values):
        self.sheet1 = SimpleNamespace(get_all_values=lambda: values)
        self.http_client = SimpleNamespace(set_timeout=lambda timeout: setattr(self, "timeout", timeout))

    def open_by_key(self, key):
        assert key == config.SHEET_ID
        return self


class FakeNotion:
    def __init__(self, existing=(), lagging=False, fail_create=False, fail_upload=False):
        self.existing = [{"properties": {"Email": {"email": email}}} for email in existing]
        self.lagging = lagging
        self.fail_create = fail_create
        self.fail_upload = fail_upload
        self.created = []
        self.uploads = []
        self.databases = SimpleNamespace(query=self.query)
        self.pages = SimpleNamespace(create=self.create)
        self.client = SimpleNamespace(post=self.send)

    def query(self, database_id, page_size, start_cursor=None):
        assert database_id == "kandydaci-db"
        visible = [] if self.lagging else self.existing
        return {"results": visible, "has_more": False, "next_cursor": None}

    def create(self, **page):
        if self.fail_create:
            raise RuntimeError("Notion is down")
        self.created.append(page)
        self.existing.append({"properties": {"Email": page["properties"]["Email"]}})

    def request(self, path, method, body):
        assert (path, method) == ("file_uploads", "POST")
        if self.fail_upload:
            raise httpx.ConnectError("boom")
        self.uploads.append(body)
        return {"id": f"upload-{len(self.uploads)}"}

    def send(self, path, files):
        assert path.startswith("file_uploads/upload-") and path.endswith("/send")
        return httpx.Response(200, json={"status": "uploaded"}, request=httpx.Request("POST", "https://x/" + path))


@pytest.fixture
def notion(monkeypatch):
    fake = FakeNotion()
    use(monkeypatch, fake)
    monkeypatch.setattr(ingest, "GoogleAuth", SimpleNamespace(get_client=lambda: FakeSheet([HEADER, *ROWS])))
    monkeypatch.setattr(ingest, "thumbnail", lambda video_id: (b"jpeg", "image/jpeg"))
    monkeypatch.setattr(ingest, "_created", set())
    monkeypatch.setattr(ingest, "database_id", lambda title: "kandydaci-db")
    return fake


def use(monkeypatch, fake):
    monkeypatch.setattr(ingest, "NotionAuth", SimpleNamespace(get_client=lambda: fake))


def by_email(fake):
    return {page["properties"]["Email"]["email"]: page for page in fake.created}


def test_cutoff_skip_and_dedupe(notion):
    result = ingest.run_ingest("kandydaci-db")
    assert result == {"created": 3, "existing": 0, "skipped": 1, "skipped_rows": [7], "errors": 0}
    pages = by_email(notion)
    assert set(pages) == {"anna@example.com", "bartek@example.com", "dawid@example.com"}
    assert pages["anna@example.com"]["properties"]["Filmik znormalizowany"]["url"] == \
        "https://www.youtube.com/watch?v=AnnaVideo01"
    assert pages["bartek@example.com"]["properties"]["Czas"]["date"]["start"] == "2026-09-03T12:00:00"


def test_page_properties_and_body(notion):
    ingest.run_ingest("kandydaci-db")
    anna, dawid = by_email(notion)["anna@example.com"], by_email(notion)["dawid@example.com"]

    props = anna["properties"]
    assert anna["parent"] == {"database_id": "kandydaci-db"}
    assert props["Imię i nazwisko"]["title"][0]["text"]["content"] == "Anna Testowa"
    assert props["Czas"]["date"] == {"start": "2026-09-01T10:00:00", "time_zone": "Europe/Warsaw"}
    assert props["Obszary"]["multi_select"] == [{"name": "Aktorstwo"}, {"name": "Scenografia"},
                                                {"name": "coś innego np. dźwięk"}]
    assert props["Rok studiów"]["select"] == {"name": "I SL"}
    assert props["Status filmiku"]["select"] == {"name": "youtube"}
    assert props["Filmik"]["url"] == "https://youtu.be/AnnaVideo01?si=Tracking123"
    image = {"type": "file_upload", "file_upload": {"id": "upload-1"}}
    assert anna["cover"] == image
    assert props["Zdjęcie"]["files"] == [{**image, "name": "miniatura.jpg"}]
    assert anna["children"][0] == {"type": "video", "video": {
        "type": "external", "external": {"url": "https://www.youtube.com/watch?v=AnnaVideo01"}}}
    assert anna["children"][1]["paragraph"]["rich_text"][0]["text"]["content"] == ROWS[1][6]

    assert dawid["properties"]["Status filmiku"]["select"] == {"name": "drive"}
    assert dawid["children"][0] == {"type": "embed", "embed": {"url": ROWS[6][6]}}
    assert "cover" not in dawid and "Zdjęcie" not in dawid["properties"]
    assert notion.uploads == [{"mode": "single_part", "filename": "miniatura.jpg", "content_type": "image/jpeg"}] * 2


def test_second_run_creates_nothing(notion):
    ingest.run_ingest("kandydaci-db")
    result = ingest.run_ingest("kandydaci-db")
    assert (result["created"], result["existing"]) == (0, 3)
    assert len(notion.created) == 3


def test_second_run_creates_nothing_when_query_lags(notion):
    notion.lagging = True
    ingest.run_ingest("kandydaci-db")
    assert ingest.run_ingest("kandydaci-db")["created"] == 0
    assert len(notion.created) == 3


def test_concurrent_runs_create_each_candidate_once(notion, monkeypatch):
    notion.lagging = True
    create = notion.create

    def slow_create(**page):
        time.sleep(0.05)
        create(**page)

    monkeypatch.setattr(notion.pages, "create", slow_create)
    runs = [threading.Thread(target=ingest.run_ingest, args=("kandydaci-db",)) for _ in range(2)]
    for run in runs:
        run.start()
    for run in runs:
        run.join()
    assert len(notion.created) == 3


def test_sheet_reads_have_a_timeout(notion, monkeypatch):
    sheet = FakeSheet([HEADER, *ROWS])
    monkeypatch.setattr(ingest, "GoogleAuth", SimpleNamespace(get_client=lambda: sheet))
    ingest.submissions()
    assert sheet.timeout == ingest.SHEET_TIMEOUT


def test_existing_candidates_are_never_touched(notion, monkeypatch):
    fake = FakeNotion(existing=["Anna@Example.com", "bartek@example.com", "dawid@example.com"])
    use(monkeypatch, fake)
    assert ingest.run_ingest("kandydaci-db")["existing"] == 3
    assert fake.created == []


def test_thumbnail_failure_does_not_block_creation(notion, monkeypatch, capsys):
    fake = FakeNotion(fail_upload=True)
    use(monkeypatch, fake)
    assert ingest.run_ingest("kandydaci-db")["created"] == 3
    assert all("cover" not in page for page in fake.created)
    assert "row 3 without photo (ConnectError)" in capsys.readouterr().out


def test_logs_contain_no_personal_data(notion, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "thumbnail", lambda video_id: None)
    ingest.run_ingest("kandydaci-db")
    use(monkeypatch, FakeNotion(fail_create=True))
    monkeypatch.setattr(ingest, "_created", set())
    result = ingest.run_ingest("kandydaci-db")
    assert result["errors"] == 3
    out = capsys.readouterr().out
    assert "row 3 not created (RuntimeError)" in out
    assert not any(value in out for value in NAMES | EMAILS)
    assert "youtu" not in out and "drive" not in out


def test_skipped_submissions(notion):
    assert ingest.skipped_submissions() == [{"row": 7, "czas": "2026-09-05 14:00:00", "name": "Celina Bezlinku",
                                             "email": "celina@example.com", "filmik": "brak"}]


client = TestClient(app)


def test_endpoint_token(notion):
    response = client.post("/internal/ingest", headers={"X-Ingest-Token": "test-ingest_token"})
    assert response.status_code == 200
    assert response.json()["created"] == 3


@pytest.mark.parametrize("headers, status", [
    ({}, 401),
    ({"X-Ingest-Token": "wrong"}, 403),
    ({"X-Ingest-Token": ""}, 403),
    ({"Authorization": "Basic abc"}, 401),
])
def test_endpoint_rejects(notion, headers, status):
    response = client.post("/internal/ingest", headers=headers)
    assert response.status_code == status
    assert response.content == b""
    assert notion.created == []


def oidc(monkeypatch, claims):
    seen = {}

    def verify(token, request, audience):
        seen.update(token=token, audience=audience)
        if isinstance(claims, Exception):
            raise claims
        return claims

    monkeypatch.setattr(ingest.id_token, "verify_oauth2_token", verify)
    return seen


def test_endpoint_oidc(notion, monkeypatch):
    seen = oidc(monkeypatch, {"email": config.SCHEDULER_SA_EMAIL, "email_verified": True})
    response = client.post("/internal/ingest", headers={"Authorization": "Bearer id-token"})
    assert response.status_code == 200
    assert seen == {"token": "id-token", "audience": config.BASE_URL}


@pytest.mark.parametrize("claims", [
    {"email": "someone@theatre-sgh.iam.gserviceaccount.com", "email_verified": True},
    {"email": config.SCHEDULER_SA_EMAIL, "email_verified": False},
    {"email": config.SCHEDULER_SA_EMAIL},
    ValueError("Token has wrong audience"),
])
def test_endpoint_oidc_rejects(notion, monkeypatch, claims):
    oidc(monkeypatch, claims)
    response = client.post("/internal/ingest", headers={"Authorization": "Bearer id-token"})
    assert response.status_code == 403
    assert notion.created == []


def test_endpoint_without_database(notion, monkeypatch):
    def missing(title):
        raise LookupError(f"Database '{title}' not found under the board page; run panel-setup.yml")

    monkeypatch.setattr(ingest, "database_id", missing)
    response = client.post("/internal/ingest", headers={"X-Ingest-Token": "test-ingest_token"})
    assert response.status_code == 503
    assert "panel-setup.yml" in response.json()["detail"]


def test_google_auth_falls_back_to_adc(monkeypatch):
    calls = {}

    def default(scopes):
        calls["scopes"] = scopes
        return "adc-creds", "theatre-sgh"

    monkeypatch.delenv("GOOGLE_PRIVATE_KEY", raising=False)
    monkeypatch.setattr(GoogleAuth, "_client", None)
    monkeypatch.setattr("shared.auth.google.auth.default", default)
    monkeypatch.setattr("shared.auth.gspread.authorize", lambda creds: f"gspread({creds})")
    assert GoogleAuth.get_client() == "gspread(adc-creds)"
    assert "https://www.googleapis.com/auth/spreadsheets.readonly" in calls["scopes"]
