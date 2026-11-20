import asyncio
import datetime as dt
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from procrastinate.jobs import DeleteJobCondition
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from . import worker
from .artist import ArtistHistory, ArtistNotFound, artist_history
from .config import settings
from .db import engine
from .duet import Duet, duet
from .eras import UserEras, user_eras
from .friends import FriendSuggestion, user_friends
from .importer import (
    ImportQueueFull,
    ImportStatus,
    UserLimitReached,
    import_status,
    is_valid_username,
    normalize_username,
    open_import,
)
from .lastfm import LastfmError, UserNotFound
from .recommend import Recommendations, recommendations
from .stats import MAX_LIMIT, ImportInProgress, NotImported, UserStats, user_stats
from .today import OnThisDay, on_this_day

logger = logging.getLogger(__name__)

# Tyle sekund worker czeka na dokończenie zadań przy wyłączaniu serwera. Hosting daje
# kilkadziesiąt sekund; przerwany import wznowi się sam przy następnym starcie.
WORKER_SHUTDOWN_TIMEOUT = 10


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    async with worker.app.open_async():
        if not settings.run_worker_in_api:
            yield
            return
        # Uvicorn ustawia logi tylko swoje; bez tego w logach hostingu nie byłoby
        # przebiegu importów (osobny worker z CLI procrastinate robi to sam)
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
        worker_task = asyncio.create_task(
            worker.app.run_worker_async(
                concurrency=3,
                delete_jobs=DeleteJobCondition.SUCCESSFUL,
                install_signal_handlers=False,
                shutdown_graceful_timeout=WORKER_SHUTDOWN_TIMEOUT,
            ),
            name="worker",
        )
        worker_task.add_done_callback(_log_worker_exit)
        logger.info("Worker kolejki działa w procesie API")
        try:
            yield
        finally:
            worker_task.cancel()
            with suppress(asyncio.CancelledError):
                await worker_task


def _log_worker_exit(task: asyncio.Task[None]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Worker kolejki w procesie API przestał działać", exc_info=task.exception())


app = FastAPI(title="Erafm", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Wszystkie endpointy są pod /api: reszta adresów należy do frontendu
api = APIRouter(prefix="/api")


def get_db() -> Engine:
    return engine


Db = Annotated[Engine, Depends(get_db)]


@api.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        logger.exception("Health check: błąd połączenia z bazą")
        raise HTTPException(status_code=503, detail="db_unavailable") from e
    return {"status": "ok", "db": "ok"}


@api.post("/users/{nick}/sync", status_code=202)
async def sync_user(nick: str, db: Db) -> ImportStatus:
    """Zleca import historii osoby i zwraca jego stan. Można wołać wielokrotnie:
    trwający import się nie dubluje, przerwany rusza dalej, a zakończony jest odświeżany
    o nowe odsłuchy, najwyżej raz na 5 minut."""
    username = normalize_username(nick)
    if not is_valid_username(username):
        raise HTTPException(status_code=422, detail="invalid_username")
    # Zapytania do bazy są synchroniczne, więc idą do wątku; zlecenie do kolejki jest
    # asynchroniczne, bo kolejka może dzielić pętlę zdarzeń z workerem w tym procesie
    try:
        job = await asyncio.to_thread(open_import, db, username)
    except ImportQueueFull:
        raise HTTPException(status_code=429, detail="import_queue_full") from None
    except UserLimitReached:
        raise HTTPException(status_code=403, detail="user_limit_reached") from None
    if job is not None:
        await worker.defer_import(username)
    status = await asyncio.to_thread(import_status, db, username)
    assert status is not None  # open_import założył osobę i zadanie
    return status


@api.get("/users/{nick}/status")
def user_status(nick: str, db: Db) -> ImportStatus:
    status = import_status(db, normalize_username(nick))
    if status is None:
        raise HTTPException(status_code=404, detail="not_imported")
    return status


@api.get("/users/{nick}/stats")
def stats(nick: str, db: Db, limit: int = 10) -> UserStats:
    """Liczby ogólne, top artyści i utwory (`limit` pozycji) oraz odsłuchy miesięcznie."""
    if not 1 <= limit <= MAX_LIMIT:
        raise HTTPException(status_code=422, detail="invalid_limit")
    try:
        return user_stats(db, normalize_username(nick), limit)
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None


@api.get("/users/{nick}/eras")
def eras(nick: str, db: Db) -> UserEras:
    """Ery artystów, krótsze epizody i okresy ciszy na osi całej historii."""
    try:
        return user_eras(db, normalize_username(nick))
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None


@api.get("/users/{nick}/today")
def today(nick: str, db: Db, date: str | None = None) -> OnThisDay:
    """Ten sam dzień w poprzednich latach i rocznice pierwszego odsłuchu. Bez `date`
    (RRRR-MM-DD) bierze dzisiejszą datę w strefie aplikacji."""
    try:
        day = dt.date.fromisoformat(date) if date is not None else None
    except ValueError:
        raise HTTPException(status_code=422, detail="invalid_date") from None
    try:
        return on_this_day(db, normalize_username(nick), day)
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None


@api.get("/duet")
def duet_view(u: str, db: Db) -> Duet:
    """Zarażenia w obie strony i kompatybilność dwóch osób: `u=nick1,nick2`, pierwsza
    osoba to „ty”. Import każdej osoby zleca się osobno przez /users/{nick}/sync."""
    nicks = [normalize_username(nick) for nick in u.split(",")]
    if len(nicks) != 2 or nicks[0] == nicks[1] or not all(map(is_valid_username, nicks)):
        raise HTTPException(status_code=422, detail="invalid_duet")
    try:
        return duet(db, *nicks)
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None


@api.get("/users/{nick}/artists/{artist_id}")
def artist(nick: str, artist_id: int, db: Db) -> ArtistHistory:
    """Pierwszy odsłuch i odsłuchy tydzień po tygodniu jednego artysty u jednej osoby.
    Osoba, która go nie słuchała, dostaje zero odsłuchów, a nie błąd."""
    try:
        return artist_history(db, normalize_username(nick), artist_id)
    except ArtistNotFound:
        raise HTTPException(status_code=404, detail="artist_not_found") from None
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None


@api.get("/users/{nick}/friends")
async def friends(nick: str, db: Db) -> list[FriendSuggestion]:
    """Znajomi osoby z Last.fm do podpowiedzi w duecie; najpierw osoby już w Erafm."""
    username = normalize_username(nick)
    if not is_valid_username(username):
        raise HTTPException(status_code=422, detail="invalid_username")
    try:
        return await user_friends(db, username)
    except UserNotFound:
        raise HTTPException(status_code=404, detail="user_not_found") from None
    except (LastfmError, httpx.HTTPError):
        logger.exception("Nie udało się pobrać znajomych z Last.fm")
        raise HTTPException(status_code=502, detail="lastfm_error") from None


@api.get("/users/{nick}/recommendations")
async def recommend(nick: str, db: Db) -> Recommendations:
    """Rekomendacje nowych artystów z uzasadnieniem. Gdy części ziaren brakuje podobnych
    artystów, zleca ich pobranie i zwraca wynik z tego, co już jest (`pending` > 0)."""
    username = normalize_username(nick)
    try:
        result = await asyncio.to_thread(recommendations, db, username)
    except NotImported:
        raise HTTPException(status_code=404, detail="not_imported") from None
    except ImportInProgress:
        raise HTTPException(status_code=409, detail="import_in_progress") from None
    if result.pending:
        await worker.defer_similar(username)
    return result


app.include_router(api)


class SinglePageApp(StaticFiles):
    """Pliki zbudowanego frontendu. Adres, który nie jest plikiem (np. /u/nick), dostaje
    index.html, a stronę wybiera już router w przeglądarce."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Nieznany adres pod /api to błąd API, a nie strona frontendu
            if exc.status_code != 404 or path == "api" or path.startswith("api/"):
                raise
            return await super().get_response("index.html", scope)


def mount_frontend(target: FastAPI, directory: Path) -> None:
    # Montowane po routerze API, więc /api/... ma pierwszeństwo
    target.mount("/", SinglePageApp(directory=directory, html=True), name="frontend")


if settings.static_dir is not None:
    mount_frontend(app, settings.static_dir)
