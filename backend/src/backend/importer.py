"""Import historii odsłuchów jednej osoby z Last.fm do bazy.

Pierwszy import osoby jest pełny (cała historia), kolejne przyrostowe (tylko to, co
przybyło od ostatniego odsłuchu w bazie). Oba różnią się wyłącznie początkiem okna.

Strony pobieramy od najstarszej do najnowszej. Każda strona to jedna transakcja:
artyści, utwory, scrobble i licznik zapisanych stron trafiają do bazy razem, więc
przerwany import da się wznowić bez dziur i bez powtórek.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import BaseModel
from sqlalchemy import Engine, func, select, tuple_
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
from .locks import IMPORT_LOCK, release_lock, try_lock
from .models import Artist, LfmUser, Scrobble, SyncJob, Track

logger = logging.getLogger(__name__)

# Ten sam utwór zapisany ponownie w krótszym odstępie to powtórka, a nie nowy odsłuch
# (Last.fm nie przyjmuje scrobbli utworów krótszych niż 30 s)
DUPLICATE_WINDOW = timedelta(seconds=30)

# Import bez postępu przez tyle czasu uznajemy za przerwany (np. restart workera).
# Najdłuższa uczciwa przerwa to 5 prób jednego zapytania, czyli ok. 90 s.
STALLED_AFTER = timedelta(minutes=3)

# Nie odświeżamy historii częściej: frontend prosi o to przy każdym otwarciu profilu
REFRESH_INTERVAL = timedelta(minutes=5)

# Import przyrostowy cofa się o tyle przed ostatni odsłuch w bazie, bo odsłuchy bywają
# dosyłane z opóźnieniem, ze starszą datą. Według relacji użytkowników Last.fm odrzuca
# scrobble starsze niż 2 tygodnie (oficjalna dokumentacja nie podaje liczby).
# Odsłuchy, które już mamy, odrzuci klucz główny tabeli scrobble.
LATE_SCROBBLE_WINDOW = timedelta(days=14)

# Oficjalnych zasad nicków Last.fm nie znamy, więc odrzucamy tylko to, co na pewno
# nie jest nickiem: pusty tekst, spacje, ukośniki i wszystko dłuższe niż kolumna w bazie
_USERNAME_RE = re.compile(r"[^\s/]{1,64}")


def normalize_username(username: str) -> str:
    """Nick w postaci, w jakiej trzymamy go w bazie: Last.fm nie rozróżnia wielkości liter."""
    return username.strip().lower()


def is_valid_username(username: str) -> bool:
    return _USERNAME_RE.fullmatch(username) is not None


def _name_key(name: str) -> str:
    return name.strip().lower()


@dataclass(frozen=True)
class _JobState:
    from_ts: datetime | None
    to_ts: datetime
    total_pages: int | None
    pages_done: int


# --- Przebieg importu (asynchroniczny: rozmowa z Last.fm) --------------------------------


async def run_import(
    client: LastfmClient, db: Engine, username: str, *, page_size: int = MAX_PAGE_SIZE
) -> bool:
    """Importuje historię osoby: całą, gdy jej nie mamy, albo tylko nowe odsłuchy.
    Wznawia też niedokończony import. Zwraca True, gdy import właśnie się zakończył.

    Zapisy do bazy są synchroniczne i krótkie, więc idą do osobnego wątku (`to_thread`),
    żeby nie blokować pętli zdarzeń workera.
    """
    username = normalize_username(username)
    job = await asyncio.to_thread(open_import, db, username)
    if job is None:
        logger.info("Historia %s jest świeża, pomijam", username)
        return False
    job_id, user_id = job

    # Jedna osoba importowana najwyżej przez jedno zadanie naraz
    lock = await asyncio.to_thread(try_lock, db, IMPORT_LOCK, user_id)
    if lock is None:
        logger.info("Import %s trwa już w innym zadaniu, pomijam", username)
        return False
    try:
        await _import_pages(client, db, job_id, username, page_size)
        return True
    except Exception as exc:
        code = _error_code(exc)
        await asyncio.to_thread(_mark_failed, db, job_id, code)
        if code == "internal_error":
            raise
        logger.warning("Import %s przerwany (%s): %s", username, code, exc)
        return False
    finally:
        await asyncio.to_thread(release_lock, lock, IMPORT_LOCK, user_id)


async def _import_pages(
    client: LastfmClient, db: Engine, job_id: int, username: str, page_size: int
) -> None:
    info = await client.get_user_info(username)
    state = await asyncio.to_thread(_start_job, db, job_id, info)

    total_pages = state.total_pages
    if total_pages is None:
        # Liczbę stron znamy dopiero z odpowiedzi; `to` jest zamrożone, więc się nie zmieni
        first = await client.get_recent_tracks(
            username, page=1, limit=page_size, from_ts=state.from_ts, to_ts=state.to_ts
        )
        total_pages = first.total_pages
        await asyncio.to_thread(_set_total_pages, db, job_id, total_pages)

    logger.info(
        "Import %s: %d stron, zapisanych wcześniej %d", username, total_pages, state.pages_done
    )
    # Strona 1 to najnowsze odsłuchy, więc od najstarszych idziemy malejąco po numerach
    for page_no in range(total_pages - state.pages_done, 0, -1):
        page = await client.get_recent_tracks(
            username, page=page_no, limit=page_size, from_ts=state.from_ts, to_ts=state.to_ts
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


def open_import(db: Engine, username: str) -> tuple[int, int] | None:
    """Zwraca (id zadania, id osoby) importu do wykonania: trwającego, przerwanego albo
    nowego. None, gdy historia osoby została odświeżona przed chwilą.

    `username` musi być znormalizowany (`normalize_username`).
    """
    try:
        return _find_or_create_job(db, username)
    except IntegrityError:
        # Ktoś równolegle założył import tej osoby (pilnuje tego indeks unikalny):
        # za drugim razem znajdziemy już jego wiersz
        return _find_or_create_job(db, username)


def _find_or_create_job(db: Engine, username: str) -> tuple[int, int] | None:
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
            select(SyncJob).where(SyncJob.user_id == user_id).order_by(SyncJob.id.desc()).limit(1)
        ).first()
        # Last.fm liczy czas w pełnych sekundach
        now = datetime.now(UTC).replace(microsecond=0)
        if job is None:
            job = SyncJob(user_id=user_id, kind="full", to_ts=now)
            session.add(job)
        elif job.status == "failed":
            # Wznawiamy od zapisanej strony, z tym samym zamrożonym oknem czasu
            job.status = "pending"
            job.error = None
        elif job.status == "done":
            if now - job.to_ts < REFRESH_INTERVAL:
                return None
            last_played = session.scalar(
                select(func.max(Scrobble.played_at)).where(Scrobble.user_id == user_id)
            )
            # Pusta historia nie ma od czego liczyć okna: pytamy wtedy o wszystko
            from_ts = last_played - LATE_SCROBBLE_WINDOW if last_played else None
            job = SyncJob(user_id=user_id, kind="incremental", from_ts=from_ts, to_ts=now)
            session.add(job)
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
        return _JobState(job.from_ts, job.to_ts, job.total_pages, job.pages_done)


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


# --- Stan importu dla API i wznawianie przerwanych importów -------------------------------


class ImportStatus(BaseModel):
    username: str
    display_name: str
    status: str  # pending, running, done albo failed
    progress: int  # procent zapisanych stron, 0-100
    error: str | None  # kod błędu, gdy status to failed
    last_synced_at: datetime | None


def import_status(db: Engine, username: str) -> ImportStatus | None:
    """Stan ostatniego importu osoby; None, gdy nigdy jej nie importowaliśmy."""
    with Session(db) as session:
        row = session.execute(
            select(LfmUser, SyncJob)
            .join(SyncJob, SyncJob.user_id == LfmUser.id)
            .where(LfmUser.username == username)
            .order_by(SyncJob.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        user, job = row
        return ImportStatus(
            username=user.username,
            display_name=user.display_name,
            status=job.status,
            progress=_progress(job),
            error=job.error,
            last_synced_at=user.last_synced_at,
        )


def _progress(job: SyncJob) -> int:
    if job.status == "done":
        return 100
    if not job.total_pages:
        return 0
    # Zaokrąglamy w dół: 100% pokazujemy dopiero po zakończeniu importu
    return min(99, job.pages_done * 100 // job.total_pages)


def stalled_imports(db: Engine) -> list[str]:
    """Nicki osób, których import jest w toku, ale od dawna nie zrobił postępu."""
    with Session(db) as session:
        return list(
            session.scalars(
                select(LfmUser.username)
                .join(SyncJob, SyncJob.user_id == LfmUser.id)
                .where(
                    SyncJob.status.in_(("pending", "running")),
                    SyncJob.updated_at < func.now() - STALLED_AFTER,
                )
                .order_by(LfmUser.username)
            )
        )


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
