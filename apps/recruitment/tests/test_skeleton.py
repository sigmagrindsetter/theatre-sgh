from fastapi.testclient import TestClient

from panel.main import app

client = TestClient(app)


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


def test_legal_pages():
    for path in ("/privacy", "/terms"):
        response = client.get(path)
        assert response.status_code == 200
        assert "teatr.sgh@gmail.com" in response.text
