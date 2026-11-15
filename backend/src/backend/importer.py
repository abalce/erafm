"""Import pełnej historii odsłuchów jednej osoby z Last.fm do bazy.

Strony pobieramy od najstarszej do najnowszej. Każda strona to jedna transakcja:
artyści, utwory, scrobble i licznik zapisanych stron trafiają do bazy razem, więc
przerwany import da się wznowić bez dziur i bez powtórek.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Connection, Engine, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .lastfm import (
    MAX_PAGE_SIZE,
    HistoryHidden,
    LastfmClient,
    LastfmError,
    UserInfo,
    UserNotFound,
)
from .lastfm import Scrobble as LastfmScrobble
from .models import Artist, LfmUser, Scrobble, SyncJob, Track

logger = logging.getLogger(__name__)

# Ten sam utwór zapisany ponownie w krótszym odstępie to powtórka, a nie nowy odsłuch
# (Last.fm nie przyjmuje scrobbli utworów krótszych niż 30 s)
DUPLICATE_WINDOW = timedelta(seconds=30)

# Pierwsza część klucza blokady w Postgresie (pg_advisory_lock); druga to id osoby
_IMPORT_LOCK_KEY = 1


def normalize_username(username: str) -> str:
    """Nick w postaci, w jakiej trzymamy go w bazie: Last.fm nie rozróżnia wielkości liter."""
    return username.strip().lower()


def _name_key(name: str) -> str:
    return name.strip().lower()


@dataclass(frozen=True)
class _JobState:
    to_ts: datetime
    total_pages: int | None
    pages_done: int


# --- Przebieg importu (asynchroniczny: rozmowa z Last.fm) --------------------------------


async def run_full_import(
    client: LastfmClient, db: Engine, username: str, *, page_size: int = MAX_PAGE_SIZE
) -> None:
    """Importuje całą historię osoby albo wznawia jej niedokończony import.

    Zapisy do bazy są synchroniczne i krótkie, więc idą do osobnego wątku (`to_thread`),
    żeby nie blokować pętli zdarzeń workera.
    """
    username = normalize_username(username)
    job_id, user_id = await asyncio.to_thread(_open_job, db, username)

    lock = await asyncio.to_thread(_acquire_lock, db, user_id)
    if lock is None:
        logger.info("Import %s trwa już w innym zadaniu, pomijam", username)
        return
    try:
        await _import_pages(client, db, job_id, username, page_size)
    except Exception as exc:
        code = _error_code(exc)
        await asyncio.to_thread(_mark_failed, db, job_id, code)
        if code == "internal_error":
            raise
        logger.warning("Import %s przerwany (%s): %s", username, code, exc)
    finally:
        await asyncio.to_thread(_release_lock, lock, user_id)


async def _import_pages(
    client: LastfmClient, db: Engine, job_id: int, username: str, page_size: int
) -> None:
    info = await client.get_user_info(username)
    state = await asyncio.to_thread(_start_job, db, job_id, info)

    total_pages = state.total_pages
    if total_pages is None:
        # Liczbę stron znamy dopiero z odpowiedzi; `to` jest zamrożone, więc się nie zmieni
        first = await client.get_recent_tracks(username, page=1, limit=page_size, to_ts=state.to_ts)
        total_pages = first.total_pages
        await asyncio.to_thread(_set_total_pages, db, job_id, total_pages)

    logger.info(
        "Import %s: %d stron, zapisanych wcześniej %d", username, total_pages, state.pages_done
    )
    # Strona 1 to najnowsze odsłuchy, więc od najstarszych idziemy malejąco po numerach
    for page_no in range(total_pages - state.pages_done, 0, -1):
        page = await client.get_recent_tracks(
            username, page=page_no, limit=page_size, to_ts=state.to_ts
        )
        await asyncio.to_thread(_store_page, db, job_id, page.scrobbles)

    await asyncio.to_thread(_finish_job, db, job_id)
    logger.info("Import %s zakończony", username)


def _error_code(exc: Exception) -> str:
    if isinstance(exc, UserNotFound):
        return "user_not_found"
    if isinstance(exc, HistoryHidden):
        return "history_hidden"
    if isinstance(exc, LastfmError | httpx.HTTPError):
        return "lastfm_error"
    return "internal_error"


# --- Zadanie importu w bazie (synchroniczne) ---------------------------------------------


def _open_job(db: Engine, username: str) -> tuple[int, int]:
    """Zwraca (id zadania, id osoby): trwający lub przerwany import albo nowy."""
    try:
        return _find_or_create_job(db, username)
    except IntegrityError:
        # Ktoś równolegle założył import tej osoby (pilnuje tego indeks unikalny):
        # za drugim razem znajdziemy już jego wiersz
        return _find_or_create_job(db, username)


def _find_or_create_job(db: Engine, username: str) -> tuple[int, int]:
    with Session(db) as session:
        # Osoba powstaje przed pierwszym zapytaniem do Last.fm, żeby błąd (np. brak
        # takiego konta) miał swoje zadanie; prawdziwą pisownię nicka dopiszemy z profilu
        session.execute(
            insert(LfmUser)
            .values(username=username, display_name=username)
            .on_conflict_do_nothing(index_elements=["username"])
        )
        user_id = session.scalars(select(LfmUser.id).where(LfmUser.username == username)).one()
        job = session.scalars(
            select(SyncJob)
            .where(SyncJob.user_id == user_id, SyncJob.kind == "full")
            .order_by(SyncJob.id.desc())
            .limit(1)
        ).first()
        if job is None or job.status == "done":
            # Last.fm liczy czas w pełnych sekundach
            job = SyncJob(
                user_id=user_id, kind="full", to_ts=datetime.now(UTC).replace(microsecond=0)
            )
            session.add(job)
        elif job.status == "failed":
            # Wznawiamy od zapisanej strony, z tym samym zamrożonym `to_ts`
            job.status = "pending"
            job.error = None
        session.commit()
        return job.id, user_id


def _start_job(db: Engine, job_id: int, info: UserInfo) -> _JobState:
    with Session(db) as session:
        job = session.get_one(SyncJob, job_id)
        job.status = "running"
        user = session.get_one(LfmUser, job.user_id)
        user.display_name = info.username
        user.registered_at = info.registered_at
        user.playcount = info.playcount
        session.commit()
        return _JobState(job.to_ts, job.total_pages, job.pages_done)


def _set_total_pages(db: Engine, job_id: int, total_pages: int) -> None:
    with Session(db) as session:
        session.get_one(SyncJob, job_id).total_pages = total_pages
        session.commit()


def _finish_job(db: Engine, job_id: int) -> None:
    with Session(db) as session:
        job = session.get_one(SyncJob, job_id)
        job.status = "done"
        # Historia jest kompletna do chwili, w której zamroziliśmy okno importu
        session.get_one(LfmUser, job.user_id).last_synced_at = job.to_ts
        session.commit()


def _mark_failed(db: Engine, job_id: int, code: str) -> None:
    with Session(db) as session:
        job = session.get_one(SyncJob, job_id)
        job.status = "failed"
        job.error = code
        session.commit()


# --- Blokada: jedna osoba importowana najwyżej przez jedno zadanie naraz -----------------


def _acquire_lock(db: Engine, user_id: int) -> Connection | None:
    """Bierze blokadę importu osoby; None, gdy trzyma ją inne zadanie.

    Blokada należy do połączenia z bazą: gdy worker padnie, Postgres zwalnia ją sam,
    więc po awarii nic nie blokuje wznowienia.
    """
    conn = db.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        if conn.scalar(select(func.pg_try_advisory_lock(_IMPORT_LOCK_KEY, user_id))):
            return conn
    except BaseException:
        conn.close()
        raise
    conn.close()
    return None


def _release_lock(conn: Connection, user_id: int) -> None:
    try:
        conn.scalar(select(func.pg_advisory_unlock(_IMPORT_LOCK_KEY, user_id)))
    finally:
        conn.close()


# --- Zapis jednej strony ------------------------------------------------------------------


def _store_page(db: Engine, job_id: int, scrobbles: list[LastfmScrobble]) -> None:
    """Zapisuje stronę odsłuchów i odnotowuje postęp w jednej transakcji."""
    with Session(db) as session:
        job = session.get_one(SyncJob, job_id)
        skipped = 0
        if scrobbles:
            # Last.fm podaje od najnowszych; my chcemy od najstarszych, żeby „pierwszy
            # odsłuch” (pisownia nazwy, album utworu) był naprawdę pierwszy
            ordered = sorted(scrobbles, key=lambda s: s.played_at)
            artist_ids = _upsert_artists(session, ordered)
            track_ids = _upsert_tracks(session, ordered, artist_ids)
            artist_of_track = {track_id: key[0] for key, track_id in track_ids.items()}
            plays = [(s.played_at, track_ids[_track_key(s, artist_ids)]) for s in ordered]
            kept, skipped = _without_duplicates(session, job.user_id, plays)
            if kept:
                # Klucz główny odrzuca scrobble, które już są w bazie (ponowny import)
                session.execute(
                    insert(Scrobble).on_conflict_do_nothing(),
                    [
                        {
                            "user_id": job.user_id,
                            "played_at": played_at,
                            "track_id": track_id,
                            "artist_id": artist_of_track[track_id],
                        }
                        for played_at, track_id in kept
                    ],
                )
        job.pages_done += 1
        job.duplicates_skipped += skipped
        session.commit()


def _upsert_artists(session: Session, scrobbles: list[LastfmScrobble]) -> dict[str, int]:
    """Dopisuje brakujących artystów; zwraca id artysty dla każdej znormalizowanej nazwy."""
    wanted: dict[str, dict[str, str | None]] = {}
    for s in scrobbles:
        key = _name_key(s.artist_name)
        wanted.setdefault(key, {"name": s.artist_name, "name_key": key, "mbid": s.artist_mbid})

    def known() -> dict[str, int]:
        rows = session.execute(
            select(Artist.name_key, Artist.id).where(Artist.name_key.in_(wanted))
        )
        return {name_key: artist_id for name_key, artist_id in rows}

    ids = known()
    missing = [row for key, row in wanted.items() if key not in ids]
    if missing:
        session.execute(insert(Artist).on_conflict_do_nothing(index_elements=["name_key"]), missing)
        ids = known()
    return ids


def _upsert_tracks(
    session: Session, scrobbles: list[LastfmScrobble], artist_ids: dict[str, int]
) -> dict[tuple[int, str], int]:
    """Dopisuje brakujące utwory; zwraca id utworu dla pary (id artysty, nazwa utworu)."""
    wanted: dict[tuple[int, str], dict[str, int | str | None]] = {}
    for s in scrobbles:
        key = _track_key(s, artist_ids)
        wanted.setdefault(
            key,
            {
                "artist_id": key[0],
                "name": s.track_name,
                "name_key": key[1],
                "album_name": s.album_name,
            },
        )

    def known() -> dict[tuple[int, str], int]:
        rows = session.execute(
            select(Track.artist_id, Track.name_key, Track.id).where(
                tuple_(Track.artist_id, Track.name_key).in_(list(wanted))
            )
        )
        return {(artist_id, name_key): track_id for artist_id, name_key, track_id in rows}

    ids = known()
    missing = [row for key, row in wanted.items() if key not in ids]
    if missing:
        session.execute(
            insert(Track).on_conflict_do_nothing(index_elements=["artist_id", "name_key"]),
            missing,
        )
        ids = known()
    return ids


def _track_key(scrobble: LastfmScrobble, artist_ids: dict[str, int]) -> tuple[int, str]:
    return artist_ids[_name_key(scrobble.artist_name)], _name_key(scrobble.track_name)


def _without_duplicates(
    session: Session, user_id: int, plays: list[tuple[datetime, int]]
) -> tuple[list[tuple[datetime, int]], int]:
    """Zwraca odsłuchy do zapisania i liczbę pominiętych powtórek (odstęp poniżej 30 s).

    `plays` to pary (czas, id utworu) ułożone od najstarszej. Powtórka może też wypaść
    na granicy stron, dlatego zaczynamy od tego, co zapisaliśmy w ostatnich 30 s przed
    tą stroną.
    """
    oldest = plays[0][0]
    recent = session.execute(
        select(Scrobble.track_id, Scrobble.played_at)
        .where(
            Scrobble.user_id == user_id,
            Scrobble.played_at > oldest - DUPLICATE_WINDOW,
            Scrobble.played_at < oldest,
        )
        .order_by(Scrobble.played_at)
    )
    last_kept: dict[int, datetime] = {track_id: played_at for track_id, played_at in recent}

    kept: list[tuple[datetime, int]] = []
    skipped = 0
    for played_at, track_id in plays:
        previous = last_kept.get(track_id)
        if previous is not None and played_at - previous < DUPLICATE_WINDOW:
            skipped += 1
            continue
        last_kept[track_id] = played_at
        kept.append((played_at, track_id))
    return kept, skipped
