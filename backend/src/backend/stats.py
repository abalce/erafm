"""Podstawowe statystyki jednej osoby: liczby ogólne, top artyści i utwory, odsłuchy
miesiąc po miesiącu. Liczone na żądanie z tabel agregatów i ze scrobbli."""

from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from .config import settings
from .models import LfmUser, SyncJob

MAX_LIMIT = 50


class NotImported(Exception):
    """Nie mamy historii tej osoby (nieznany nick albo nieudany pierwszy import)."""


class ImportInProgress(Exception):
    """Pierwszy import osoby jeszcze trwa: częściowych liczb nie pokazujemy."""


class Totals(BaseModel):
    scrobbles: int
    artists: int
    tracks: int
    first_scrobble_at: datetime | None


class TopArtist(BaseModel):
    id: int
    name: str
    plays: int
    image_url: str | None


class TopTrack(BaseModel):
    id: int
    name: str
    artist_name: str
    plays: int


class MonthlyPlays(BaseModel):
    month: date  # pierwszy dzień miesiąca
    plays: int


class UserStats(BaseModel):
    username: str
    display_name: str
    totals: Totals
    top_artists: list[TopArtist]
    top_tracks: list[TopTrack]
    monthly: list[MonthlyPlays]


_TOTALS = text("""
    SELECT coalesce(sum(total_plays), 0) AS scrobbles,
           count(*) AS artists,
           (SELECT count(DISTINCT track_id) FROM scrobble WHERE user_id = :user_id) AS tracks,
           min(first_at) AS first_scrobble_at
    FROM first_listen
    WHERE user_id = :user_id
""")

_TOP_ARTISTS = text("""
    SELECT a.id, a.name, f.total_plays AS plays, a.image_url
    FROM first_listen f
    JOIN artist a ON a.id = f.artist_id
    WHERE f.user_id = :user_id
    ORDER BY f.total_plays DESC, a.id
    LIMIT :limit
""")

_TOP_TRACKS = text("""
    SELECT t.id, t.name, a.name AS artist_name, top.plays
    FROM (
        SELECT track_id, count(*) AS plays
        FROM scrobble
        WHERE user_id = :user_id
        GROUP BY track_id
        ORDER BY plays DESC, track_id
        LIMIT :limit
    ) top
    JOIN track t ON t.id = top.track_id
    JOIN artist a ON a.id = t.artist_id
    ORDER BY top.plays DESC, t.id
""")

# generate_series daje ciągłą serię miesięcy od pierwszego odsłuchu do bieżącego miesiąca,
# więc miesiące bez słuchania dostają zero zamiast znikać
_MONTHLY = text("""
    WITH plays AS (
        SELECT date_trunc('month', played_at AT TIME ZONE :timezone)::date AS month,
               count(*) AS plays
        FROM scrobble
        WHERE user_id = :user_id
        GROUP BY 1
    )
    SELECT months.month::date AS month, coalesce(plays.plays, 0) AS plays
    FROM generate_series(
        (SELECT min(month) FROM plays),
        date_trunc('month', now() AT TIME ZONE :timezone)::date,
        interval '1 month'
    ) AS months (month)
    LEFT JOIN plays ON plays.month = months.month::date
    ORDER BY 1
""")


def find_imported_user(session: Session, username: str) -> LfmUser:
    """Osoba o znormalizowanym nicku, której historię mamy. Rzuca NotImported albo
    ImportInProgress, gdy nie ma jeszcze czego pokazać."""
    user = session.scalars(select(LfmUser).where(LfmUser.username == username)).first()
    if user is None:
        raise NotImported
    if user.last_synced_at is None:
        # Żaden import się jeszcze nie zakończył: trwa pierwszy albo się nie udał
        status = session.scalar(
            select(SyncJob.status)
            .where(SyncJob.user_id == user.id)
            .order_by(SyncJob.id.desc())
            .limit(1)
        )
        raise ImportInProgress if status in ("pending", "running") else NotImported
    return user


def user_stats(db: Engine, username: str, limit: int = 10) -> UserStats:
    """Statystyki osoby o znormalizowanym nicku (wyjątki jak w find_imported_user)."""
    with Session(db) as session:
        user = find_imported_user(session, username)
        params = {"user_id": user.id, "limit": limit, "timezone": settings.app_timezone}
        return UserStats(
            username=user.username,
            display_name=user.display_name,
            totals=Totals(**session.execute(_TOTALS, params).mappings().one()),
            top_artists=[
                TopArtist(**row) for row in session.execute(_TOP_ARTISTS, params).mappings()
            ],
            top_tracks=[TopTrack(**row) for row in session.execute(_TOP_TRACKS, params).mappings()],
            monthly=[MonthlyPlays(**row) for row in session.execute(_MONTHLY, params).mappings()],
        )
