"""Serwowanie zbudowanego frontendu przez API (tryb jednego kontenera)."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from procrastinate import testing
from sqlalchemy import Engine

from backend import worker
from backend.config import settings
from backend.main import app, mount_frontend


@pytest.fixture
def frontend(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<html>strona</html>")
    (tmp_path / "_app").mkdir()
    (tmp_path / "_app" / "app.js").write_text("console.log('erafm')")
    return tmp_path


@pytest.fixture
def client(frontend: Path) -> TestClient:
    app = FastAPI()

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"ok": "tak"}

    mount_frontend(app, frontend)
    return TestClient(app)


def test_files_and_index_are_served(client: TestClient) -> None:
    assert client.get("/").text == "<html>strona</html>"
    assert client.get("/_app/app.js").text == "console.log('erafm')"


def test_unknown_page_gets_index_for_browser_routing(client: TestClient) -> None:
    response = client.get("/u/solejuk2115")

    assert response.status_code == 200
    assert response.text == "<html>strona</html>"


def test_api_routes_win_and_unknown_api_is_404(client: TestClient) -> None:
    assert client.get("/api/ping").json() == {"ok": "tak"}
    assert client.get("/api/nie-ma-takiego").status_code == 404
    assert client.get("/api").status_code == 404


@pytest.fixture
def queue(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[testing.InMemoryConnector]:
    monkeypatch.setattr(worker, "engine", clean_db)
    connector = testing.InMemoryConnector()
    with worker.app.replace_connector(connector):
        yield connector


def test_worker_runs_inside_api_when_enabled(
    queue: testing.InMemoryConnector, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "run_worker_in_api", True)

    # `with` uruchamia lifespan: start i zatrzymanie workera razem z API
    with TestClient(app) as client:
        client.portal.call(worker.ping.defer_async)
        # Udane zadania worker od razu usuwa z kolejki (delete_jobs), więc zniknięcie
        # zadania `ping` oznacza, że zostało wykonane
        for _ in range(100):
            names = [job["task_name"] for job in queue.jobs.values()]
            if "backend.worker.ping" not in names:
                break
            client.portal.call(asyncio.sleep, 0.05)
        else:
            pytest.fail("worker w procesie API nie wykonał zadania")
