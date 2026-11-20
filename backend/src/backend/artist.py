"""Historia jednego artysty u jednej osoby: pierwszy odsłuch i odsłuchy tydzień po tygodniu.

Z gotowych tabel (`first_listen`, `weekly_artist_stats`), więc szybko. Używa tego widok
artysty w duecie (woła endpoint dla obu osób), a później może też profil.
"""

from datetime import date, datetime

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .stats import find_imported_user


class ArtistNotFound(Exception):
    """Nie ma artysty o takim id."""


class ArtistWeek(BaseModel):
    week: date  # poniedziałek
    plays: int


class ArtistHistory(BaseModel):
    username: str
    artist_id: int
    name: str
    listeners: int | None
    image_url: str | None
    total_plays: int  # 0, gdy osoba nie słuchała tego artysty
    first_at: datetime | None
    last_at: datetime | None
    first_track: str | None  # utwór z pierwszego odsłuchu
    weeks: list[ArtistWeek]  # tylko tygodnie z odsłuchami, od najstarszego


_ARTIST = text("SELECT id, name, listeners, image_url FROM artist WHERE id = :artist_id")

# Pierwszy utwór dobieramy jak w erach: scrobble z dokładnie tą chwilą co first_at
_FIRST = text("""
    SELECT f.first_at, f.last_at, f.total_plays, t.name AS first_track
    FROM first_listen f
    LEFT JOIN scrobble s
      ON s.user_id = f.user_id AND s.artist_id = f.artist_id AND s.played_at = f.first_at
    LEFT JOIN track t ON t.id = s.track_id
    WHERE f.user_id = :user_id AND f.artist_id = :artist_id
    ORDER BY s.track_id
    LIMIT 1
""")

_WEEKS = text("""
    SELECT week, plays
    FROM weekly_artist_stats
    WHERE user_id = :user_id AND artist_id = :artist_id
    ORDER BY week
""")


def artist_history(db: Engine, username: str, artist_id: int) -> ArtistHistory:
    """Historia artysty u osoby o znormalizowanym nicku. Rzuca ArtistNotFound albo
    wyjątki z stats.find_imported_user."""
    with Session(db) as session:
        user = find_imported_user(session, username)
        artist = session.execute(_ARTIST, {"artist_id": artist_id}).first()
        if artist is None:
            raise ArtistNotFound
        params = {"user_id": user.id, "artist_id": artist_id}
        first = session.execute(_FIRST, params).first()
        return ArtistHistory(
            username=user.username,
            artist_id=artist.id,
            name=artist.name,
            listeners=artist.listeners,
            image_url=artist.image_url,
            total_plays=first.total_plays if first else 0,
            first_at=first.first_at if first else None,
            last_at=first.last_at if first else None,
            first_track=first.first_track if first else None,
            weeks=[
                ArtistWeek(week=row.week, plays=row.plays)
                for row in session.execute(_WEEKS, params)
            ],
        )
