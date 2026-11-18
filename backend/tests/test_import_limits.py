"""Limity pierwszych importów na publicznym linku: ile naraz i ile osób w bazie."""

import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from procrastinate import testing
from sqlalchemy import Engine, text

from backend import importer, worker
from backend.config import settings
from backend.importer import ImportQueueFull, open_import
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


@pytest.fixture(autouse=True)
def small_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "max_active_first_imports", 2)
    monkeypatch.setattr(settings, "max_users", 3)


def add_user(db: Engine, nick: str, *, synced: bool, job_status: str | None) -> None:
    """Osoba z historią (`synced`) albo bez, z ostatnim importem w podanym stanie."""
    with db.begin() as conn:
        user_id = conn.scalar(
            text(
                "INSERT INTO lfm_user (username, display_name, last_synced_at) "
                "VALUES (:n, :n, CASE WHEN :synced THEN now() - interval '1 hour' END) "
                "RETURNING id"
            ),
            {"n": nick, "synced": synced},
        )
        if job_status is not None:
            conn.execute(
                text(
                    "INSERT INTO sync_job (user_id, kind, status, to_ts) "
                    "VALUES (:u, 'full', :s, now() - interval '1 hour')"
                ),
                {"u": user_id, "s": job_status},
            )


def user_count(db: Engine) -> int:
    with db.connect() as conn:
        return conn.scalar(text("SELECT count(*) FROM lfm_user"))


def queued(queue: testing.InMemoryConnector) -> list[str]:
    return [job["args"]["username"] for job in queue.jobs.values()]


def test_third_first_import_waits(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_user(clean_db, "pierwsza", synced=False, job_status="running")
    add_user(clean_db, "druga", synced=False, job_status="pending")

    response = client.post("/api/users/trzecia/sync")

    assert response.status_code == 429
    assert response.json() == {"detail": "import_queue_full"}
    assert queued(queue) == []
    assert user_count(clean_db) == 2  # odrzucony nick nie zostawia śladu


def test_known_user_refreshes_even_when_queue_is_full(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_user(clean_db, "pierwsza", synced=False, job_status="running")
    add_user(clean_db, "druga", synced=False, job_status="running")
    add_user(clean_db, "stala", synced=True, job_status="done")

    response = client.post("/api/users/stala/sync")

    assert response.status_code == 202
    assert queued(queue) == ["stala"]


def test_refresh_of_known_user_does_not_block_first_imports(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    # Odświeżania osób z historią nie liczą się do limitu pierwszych importów
    add_user(clean_db, "stala", synced=True, job_status="running")
    add_user(clean_db, "pierwsza", synced=False, job_status="running")

    assert client.post("/api/users/nowa/sync").status_code == 202


def test_retry_of_failed_first_import_counts_against_queue(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_user(clean_db, "pierwsza", synced=False, job_status="running")
    add_user(clean_db, "druga", synced=False, job_status="running")
    add_user(clean_db, "pechowa", synced=False, job_status="failed")

    response = client.post("/api/users/pechowa/sync")

    assert response.status_code == 429
    with clean_db.connect() as conn:
        status = conn.scalar(
            text(
                "SELECT j.status FROM sync_job j JOIN lfm_user u ON u.id = j.user_id "
                "WHERE u.username = 'pechowa'"
            )
        )
    assert status == "failed"  # odmowa niczego nie zmieniła


def test_new_user_refused_when_database_is_full(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    for nick in ("a", "b", "c"):
        add_user(clean_db, nick, synced=True, job_status="done")

    response = client.post("/api/users/czwarta/sync")

    assert response.status_code == 403
    assert response.json() == {"detail": "user_limit_reached"}
    assert queued(queue) == []
    # Osoby, które już są, dalej się odświeżają
    assert client.post("/api/users/a/sync").status_code == 202


def test_failed_first_imports_do_not_take_places(
    client: TestClient, queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_user(clean_db, "a", synced=True, job_status="done")
    add_user(clean_db, "b", synced=True, job_status="done")
    add_user(clean_db, "literowka", synced=False, job_status="failed")
    add_user(clean_db, "bez-zadania", synced=False, job_status=None)

    assert client.post("/api/users/trzecia/sync").status_code == 202


def test_simultaneous_requests_do_not_exceed_limit(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "max_active_first_imports", 1)
    # Pauza między sprawdzeniem limitu a zapisem: bez blokady oba zlecenia zdążyłyby
    # sprawdzić limit, zanim którekolwiek zapisze swój import
    check = importer._check_first_import_limits

    def slow_check(session: object, user_id: int) -> None:
        check(session, user_id)
        time.sleep(0.3)

    monkeypatch.setattr(importer, "_check_first_import_limits", slow_check)
    start = threading.Barrier(2)
    results: dict[str, str] = {}

    def request(nick: str) -> None:
        start.wait()
        try:
            open_import(clean_db, nick)
            results[nick] = "przyjęty"
        except ImportQueueFull:
            results[nick] = "odrzucony"

    threads = [threading.Thread(target=request, args=(nick,)) for nick in ("jedna", "druga")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(results.values()) == ["odrzucony", "przyjęty"]
