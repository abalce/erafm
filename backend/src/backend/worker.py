"""Kolejka zadań w Postgresie (procrastinate): aplikacja kolejki i zadania workera.

Worker: `procrastinate --app=backend.worker.app worker`
Zlecenie zadania z terminala: `procrastinate --app=backend.worker.app defer backend.worker.ping`
"""

import logging

import procrastinate

from .config import settings

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
