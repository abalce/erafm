"""Kolejka zadań w Postgresie (procrastinate): aplikacja kolejki i zadania workera.

Worker: `procrastinate --app=backend.worker.app worker`
Zlecenie zadania z terminala: `procrastinate --app=backend.worker.app defer backend.worker.ping`
Import historii: `... defer backend.worker.sync_user '{"username": "nick"}'`
"""

import asyncio
import logging

import procrastinate

from .config import settings
from .db import engine
from .enrichment import run_enrichment
from .importer import run_import, stalled_imports
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
async def sync_user(username: str) -> None:
    """Importuje historię osoby: całą za pierwszym razem, potem tylko nowe odsłuchy.
    Wznawia też przerwany import."""
    if await run_import(_lastfm_client(), engine, username):
        # Nowe odsłuchy mogły przynieść nowych artystów: uzupełniamy ich dane w tle
        await _defer_enrichment()


async def _defer_enrichment() -> None:
    try:
        await enrich_artists.defer_async(timestamp=0)
    except procrastinate.exceptions.AlreadyEnqueued:
        logger.debug("Wzbogacanie artystów już czeka w kolejce")


# Co kwadrans: dokańcza wzbogacanie przerwane restartem workera i odświeża artystów
# sprawdzonych ponad 30 dni temu. Gdy nie ma nic do zrobienia, kończy się na jednym zapytaniu.
@app.periodic(cron="*/15 * * * *")
@app.task(queueing_lock="enrich_artists")
async def enrich_artists(timestamp: int) -> None:
    """Uzupełnia dane artystów z Last.fm (słuchacze, odsłuchy, tagi)."""
    await run_enrichment(_lastfm_client(), engine)


def _import_of(username: str) -> procrastinate.jobs.JobDeferrer:
    # queueing_lock: w kolejce czeka najwyżej jedno zlecenie importu tej osoby
    return sync_user.configure(queueing_lock=f"import:{username}")


async def defer_import(username: str) -> None:
    """Zleca import osoby (dla API). Nic nie robi, gdy takie zlecenie już czeka."""
    try:
        await _import_of(username).defer_async(username=username)
    except procrastinate.exceptions.AlreadyEnqueued:
        logger.debug("Import %s już czeka w kolejce", username)


@app.periodic(cron="* * * * *")
@app.task(queueing_lock="resume_stalled_imports")
async def resume_stalled_imports(timestamp: int) -> None:
    """Co minutę zleca ponownie importy przerwane np. restartem workera.

    Zbędne zlecenie nie szkodzi: zadanie `sync_user` kończy od razu, gdy import tej
    osoby naprawdę trwa (blokada w bazie).
    """
    for username in await asyncio.to_thread(stalled_imports, engine):
        try:
            await _import_of(username).defer_async(username=username)
            logger.info("Wznawiam przerwany import %s", username)
        except procrastinate.exceptions.AlreadyEnqueued:
            logger.debug("Import %s już czeka w kolejce", username)
