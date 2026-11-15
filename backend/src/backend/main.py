import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from . import worker
from .config import settings
from .db import engine
from .importer import (
    ImportStatus,
    import_status,
    is_valid_username,
    normalize_username,
    open_import,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # API tylko zleca zadania workerowi: do tego wystarcza synchroniczne połączenie z kolejką
    worker.app.open()
    try:
        yield
    finally:
        worker.app.close()


app = FastAPI(title="Erafm", lifespan=lifespan)


def get_db() -> Engine:
    return engine


Db = Annotated[Engine, Depends(get_db)]

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        logger.exception("Health check: błąd połączenia z bazą")
        raise HTTPException(status_code=503, detail="db_unavailable") from e
    return {"status": "ok", "db": "ok"}


@app.post("/users/{nick}/sync", status_code=202)
def sync_user(nick: str, db: Db) -> ImportStatus:
    """Zleca import historii osoby i zwraca jego stan. Można wołać wielokrotnie:
    trwający import się nie dubluje, przerwany rusza dalej, a zakończony jest odświeżany
    o nowe odsłuchy, najwyżej raz na 5 minut."""
    username = normalize_username(nick)
    if not is_valid_username(username):
        raise HTTPException(status_code=422, detail="invalid_username")
    if open_import(db, username) is not None:
        worker.defer_import(username)
    status = import_status(db, username)
    assert status is not None  # open_import założył osobę i zadanie
    return status


@app.get("/users/{nick}/status")
def user_status(nick: str, db: Db) -> ImportStatus:
    status = import_status(db, normalize_username(nick))
    if status is None:
        raise HTTPException(status_code=404, detail="not_imported")
    return status
