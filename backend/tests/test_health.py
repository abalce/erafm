from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend.db import engine
from backend.main import app


def test_health():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


def test_health_db_down(monkeypatch):
    def broken_connect():
        raise OperationalError("SELECT 1", None, Exception("db down"))

    monkeypatch.setattr(engine, "connect", broken_connect)

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"detail": "db_unavailable"}
