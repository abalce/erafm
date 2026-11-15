"""Testy kolejki zadań na kolejce w pamięci (bez prawdziwego workera)."""

from collections.abc import Iterator

import pytest
from procrastinate import testing
from sqlalchemy import Engine, text

from backend import worker
from backend.worker import _conninfo, app, ping, resume_stalled_imports


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def queue(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[testing.InMemoryConnector]:
    """Kolejka w pamięci; zadania workera widzą bazę testową zamiast prawdziwej."""
    monkeypatch.setattr(worker, "engine", clean_db)
    connector = testing.InMemoryConnector()
    with app.replace_connector(connector):
        yield connector


def jobs_of(connector: testing.InMemoryConnector, task_name: str) -> list[dict]:
    return [job for job in connector.jobs.values() if job["task_name"] == task_name]


def test_ping_is_picked_up_and_succeeds(queue: testing.InMemoryConnector) -> None:
    ping.defer()
    # wait=False: worker wykonuje to, co czeka w kolejce, i kończy pracę
    app.run_worker(wait=False, install_signal_handlers=False)

    assert [job["status"] for job in jobs_of(queue, "backend.worker.ping")] == ["succeeded"]


def test_conninfo_drops_sqlalchemy_driver() -> None:
    url = "postgresql+psycopg://user:haslo@db:5432/baza"
    assert _conninfo(url) == "postgresql://user:haslo@db:5432/baza"


@pytest.mark.anyio
async def test_stalled_imports_are_deferred_again(
    queue: testing.InMemoryConnector, clean_db: Engine
) -> None:
    add_user = text("INSERT INTO lfm_user (id, username, display_name) VALUES (:id, :nick, :nick)")
    add_job = text(
        "INSERT INTO sync_job (user_id, kind, status, to_ts, updated_at) "
        "VALUES (:user_id, 'full', :status, now(), now() - make_interval(mins => :idle))"
    )
    with clean_db.begin() as conn:
        for user_id, nick in enumerate(["przerwana", "w-trakcie", "skonczona", "nieudana"], 1):
            conn.execute(add_user, {"id": user_id, "nick": nick})
        conn.execute(add_job, {"user_id": 1, "status": "running", "idle": 10})
        conn.execute(add_job, {"user_id": 2, "status": "running", "idle": 1})
        conn.execute(add_job, {"user_id": 3, "status": "done", "idle": 10})
        conn.execute(add_job, {"user_id": 4, "status": "failed", "idle": 10})

    await resume_stalled_imports(0)
    await resume_stalled_imports(0)  # drugie zlecenie tej samej osoby nie trafia do kolejki

    deferred = jobs_of(queue, "backend.worker.import_full")
    assert [job["args"] for job in deferred] == [{"username": "przerwana"}]
