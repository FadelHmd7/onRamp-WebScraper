from fastapi.testclient import TestClient

from onramp_scraper.api.app import app


def test_health_is_public_and_ok() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
