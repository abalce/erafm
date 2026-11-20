"""Pobieranie podobnych artystów (artist.getSimilar) dla ziaren rekomendacji jednej osoby.

Działa w workerze, tak jak import i wzbogacanie: cały ruch do Last.fm idzie przez jeden
klient ze wspólnym limitem zapytań. Wynik ważny 30 dni (`artist.similar_fetched_at`).
Podobnych, których nie mamy w bazie, dopisujemy do `artist`, żeby wzbogacanie dociągnęło
im liczbę słuchaczy i tagi.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .lastfm import ArtistNotFound, LastfmClient, LastfmError, SimilarArtist
from .models import Artist, ArtistSimilar, LfmUser

logger = logging.getLogger(__name__)

SIMILAR_TTL = timedelta(days=30)
SIMILAR_LIMIT = 50
CONCURRENCY = 3


@dataclass(frozen=True)
class SeedParams:
    seeds: int = 30
    # Ziarna to najczęściej słuchani w tylu dniach przed ostatnim scrobblem osoby: profil,
    # który przestał scrobblować, też dostaje rekomendacje
    window_days: int = 90


DEFAULT_SEEDS = SeedParams()


@dataclass(frozen=True)
class Seed:
    artist_id: int
    name: str
    plays: int
    fresh: bool  # podobni pobrani w ciągu SIMILAR_TTL
    fetched: bool  # podobni pobrani kiedykolwiek (stare dane też da się użyć)


_SEEDS = text("""
    WITH last AS (SELECT max(played_at) AS at FROM scrobble WHERE user_id = :user_id)
    SELECT a.id AS artist_id, a.name, count(*) AS plays,
           a.similar_fetched_at > now() - make_interval(days => :ttl_days) AS fresh,
           a.similar_fetched_at IS NOT NULL AS fetched
    FROM scrobble s
    CROSS JOIN last
    JOIN artist a ON a.id = s.artist_id
    WHERE s.user_id = :user_id
      AND s.played_at > last.at - make_interval(days => :window_days)
    GROUP BY a.id, a.name, a.similar_fetched_at
    ORDER BY plays DESC, a.id
    LIMIT :seeds
""")


def seeds_of(session: Session, user_id: int, params: SeedParams = DEFAULT_SEEDS) -> list[Seed]:
    """Ziarna rekomendacji osoby, od najczęściej słuchanego."""
    rows = session.execute(
        _SEEDS,
        {
            "user_id": user_id,
            "ttl_days": SIMILAR_TTL.days,
            "window_days": params.window_days,
            "seeds": params.seeds,
        },
    )
    return [
        Seed(r.artist_id, r.name, r.plays, fresh=bool(r.fresh), fetched=r.fetched) for r in rows
    ]


def _stale_seeds(db: Engine, username: str) -> list[Seed]:
    with Session(db) as session:
        user = session.scalars(select(LfmUser).where(LfmUser.username == username)).first()
        if user is None:
            return []
        return [seed for seed in seeds_of(session, user.id) if not seed.fresh]


def _save(db: Engine, artist_id: int, similar: list[SimilarArtist]) -> None:
    """Podmienia podobnych artysty w jednej transakcji."""
    # Ta sama nazwa po normalizacji może przyjść dwa razy: zostaje wyższe podobieństwo
    wanted: dict[str, SimilarArtist] = {}
    for artist in sorted(similar, key=lambda a: a.match, reverse=True):
        wanted.setdefault(artist.name.strip().lower(), artist)

    with Session(db) as session:
        ids: dict[str, int] = {}
        if wanted:
            keys = sorted(wanted)  # stała kolejność: równoległe zapisy się nie zakleszczą
            session.execute(
                insert(Artist).on_conflict_do_nothing(index_elements=["name_key"]),
                [{"name": wanted[k].name, "name_key": k, "mbid": wanted[k].mbid} for k in keys],
            )
            ids = dict(
                session.execute(
                    select(Artist.name_key, Artist.id).where(Artist.name_key.in_(keys))
                ).all()
            )
        session.execute(delete(ArtistSimilar).where(ArtistSimilar.artist_id == artist_id))
        # Artysta bywa podobny sam do siebie (inna pisownia tej samej nazwy): pomijamy
        rows = [
            {"artist_id": artist_id, "similar_id": ids[key], "match": artist.match}
            for key, artist in wanted.items()
            if key in ids and ids[key] != artist_id
        ]
        if rows:
            session.execute(insert(ArtistSimilar), rows)
        session.execute(
            update(Artist).where(Artist.id == artist_id).values(similar_fetched_at=func.now())
        )
        session.commit()


async def run_similar(client: LastfmClient, db: Engine, username: str) -> int:
    """Pobiera podobnych dla ziaren osoby, które nie mają świeżych danych. Zwraca liczbę
    zapisanych ziaren; artysta z błędem Last.fm czeka na następne uruchomienie."""
    seeds = await asyncio.to_thread(_stale_seeds, db, username)
    if not seeds:
        return 0
    logger.info("Podobni artyści dla %s: %d ziaren do pobrania", username, len(seeds))
    todo = iter(seeds)
    saved = 0

    async def worker() -> None:
        nonlocal saved
        for seed in todo:
            try:
                similar = await client.get_similar_artists(seed.name, SIMILAR_LIMIT)
            except ArtistNotFound:
                similar = []  # Last.fm nie zna artysty: zapisujemy „sprawdzony, bez podobnych”
            except (LastfmError, httpx.HTTPError) as exc:
                logger.warning("Podobni: błąd dla artysty %r: %s", seed.name, exc)
                continue
            await asyncio.to_thread(_save, db, seed.artist_id, similar)
            saved += 1

    await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
    logger.info("Podobni artyści dla %s: zapisano %d z %d", username, saved, len(seeds))
    return saved
