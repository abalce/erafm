"""Testy endpointów importu: baza testowa i kolejka w pamięci zamiast workera."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from procrastinate import testing
from sqlalchemy import Engine, text

from backend import worker
from backend.main import app, get_db


@pytest.fixture
def queue() -> Iterator[testing.InMemoryConnector]:
    connector = testing.InMemoryConnector()
    with worker.app.replace_connector(connector):
        yield connector


@pytest.fixture
def client(clean_db: Engine, queue: testing.InMemoryConnector) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_import(
    db: Engine, status: str, *, pages: tuple[int, int] | None = None, minutes_ago: int = 0
) -> None:
    """Zakłada osobę `przykladowa` z jednym importem w podanym stanie."""
    done, total = pages or (0, None)
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name) "
                "VALUES (1, 'przykladowa', 'Przykladowa')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO sync_job "
                "(user_id, kind, status, to_ts, pages_done, total_pages, error) "
                "VALUES (1, 'full', :status, now() - make_interval(mins => :ago), "
                ":done, :total, :error)"
            ),
            {
                "ago": minutes_ago,
                "status": status,
                "done": done,
                "total": total,
                "error": "lastfm_error" if status == "failed" else None,
            },
        )


def queued_imports(queue: testing.InMemoryConnector) -> list[dict]:
    return [job["args"] for job in queue.jobs.values()]


def test_sync_starts_import_for_new_user(
    client: TestClient, queue: testing.InMemoryConnector
) -> None:
    response = client.post("/api/users/PrzykLadowa/sync")

    assert response.status_code == 202
    assert response.json() == {
        "username": "przykladowa",
        "display_name": "przykladowa",
        "status": "pending",
        "progress": 0,
        "error": None,
        "last_synced_at": None,
    }
    assert queued_imports(queue) == [{"username": "przykladowa"}]


def test_repeated_sync_does_not_start_second_import(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    client.post("/api/users/przykladowa/sync")
    response = client.post("/api/users/przykladowa/sync")

    assert response.status_code == 202
    assert response.json()["status"] == "pending"
    assert queued_imports(queue) == [{"username": "przykladowa"}]
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM sync_job")) == 1


def test_sync_resumes_running_import(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_import(clean_db, "running", pages=(55, 275))
    response = client.post("/api/users/przykladowa/sync")

    assert (response.json()["status"], response.json()["progress"]) == ("running", 20)
    assert queued_imports(queue) == [{"username": "przykladowa"}]


def test_sync_retries_failed_import(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_import(clean_db, "failed", pages=(55, 275))
    response = client.post("/api/users/przykladowa/sync")

    body = response.json()
    assert (body["status"], body["progress"], body["error"]) == ("pending", 20, None)
    assert queued_imports(queue) == [{"username": "przykladowa"}]


def test_sync_leaves_freshly_synced_history_alone(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_import(clean_db, "done", pages=(275, 275), minutes_ago=2)
    response = client.post("/api/users/przykladowa/sync")

    assert response.status_code == 202
    assert (response.json()["status"], response.json()["progress"]) == ("done", 100)
    assert queued_imports(queue) == []


def test_sync_refreshes_older_history(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_import(clean_db, "done", pages=(275, 275), minutes_ago=6)
    response = client.post("/api/users/przykladowa/sync")

    assert (response.json()["status"], response.json()["progress"]) == ("pending", 0)
    assert queued_imports(queue) == [{"username": "przykladowa"}]
    with clean_db.connect() as conn:
        assert conn.scalars(text("SELECT kind FROM sync_job ORDER BY id")).all() == [
            "full",
            "incremental",
        ]


@pytest.mark.parametrize("nick", ["dwa%20slowa", "x" * 65])
def test_sync_rejects_impossible_username(
    client: TestClient, queue: testing.InMemoryConnector, nick: str
) -> None:
    response = client.post(f"/api/users/{nick}/sync")

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_username"}
    assert queued_imports(queue) == []


def test_status_shows_progress_and_error(client: TestClient, clean_db: Engine) -> None:
    add_import(clean_db, "failed", pages=(274, 275))
    response = client.get("/api/users/Przykladowa/status")

    assert response.status_code == 200
    assert response.json() == {
        "username": "przykladowa",
        "display_name": "Przykladowa",
        "status": "failed",
        "progress": 99,
        "error": "lastfm_error",
        "last_synced_at": None,
    }


def test_status_of_unknown_user(client: TestClient) -> None:
    response = client.get("/api/users/nieznana/status")

    assert response.status_code == 404
    assert response.json() == {"detail": "not_imported"}
