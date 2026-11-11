"""Testy kolejki zadań na kolejce w pamięci (bez bazy i bez prawdziwego workera)."""

from procrastinate import testing

from backend.worker import _conninfo, app, ping


def test_ping_is_picked_up_and_succeeds() -> None:
    connector = testing.InMemoryConnector()
    with app.replace_connector(connector) as test_app:
        ping.defer()
        # wait=False: worker wykonuje to, co czeka w kolejce, i kończy pracę
        test_app.run_worker(wait=False, install_signal_handlers=False)

    jobs = list(connector.jobs.values())
    assert [job["task_name"] for job in jobs] == ["backend.worker.ping"]
    assert [job["status"] for job in jobs] == ["succeeded"]


def test_conninfo_drops_sqlalchemy_driver() -> None:
    url = "postgresql+psycopg://user:haslo@db:5432/baza"
    assert _conninfo(url) == "postgresql://user:haslo@db:5432/baza"
