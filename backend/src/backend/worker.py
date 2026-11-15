"""Kolejka zadań w Postgresie (procrastinate): aplikacja kolejki i zadania workera.

Worker: `procrastinate --app=backend.worker.app worker`
Zlecenie zadania z terminala: `procrastinate --app=backend.worker.app defer backend.worker.ping`
Import historii: `... defer backend.worker.import_full '{"username": "nick"}'`
"""

import logging

import procrastinate

from .config import settings
from .db import engine
from .importer import run_full_import
from .lastfm import LastfmClient

logger = logging.getLogger(__name__)


def _conninfo(database_url: str) -> str:
    # SQLAlchemy zapisuje sterownik w adresie (postgresql+psycopg://), a libpq go nie rozumie
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(conninfo=_conninfo(settings.database_url))
)


@app.task
async def ping() -> None:
    """Zadanie diagnostyczne: sprawdza, czy worker odbiera i wykonuje zadania."""
    logger.info("pong")


_lastfm: LastfmClient | None = None


def _lastfm_client() -> LastfmClient:
    # Jeden klient na cały proces workera: limit 4 zapytań na sekundę jest wspólny
    # dla wszystkich zadań. Powstaje przy pierwszym użyciu, już w pętli zdarzeń workera.
    global _lastfm
    if _lastfm is None:
        _lastfm = LastfmClient(settings.lastfm_api_key)
    return _lastfm


@app.task
async def import_full(username: str) -> None:
    """Importuje całą historię odsłuchów osoby albo wznawia przerwany import."""
    await run_full_import(_lastfm_client(), engine, username)
