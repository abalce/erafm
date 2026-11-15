"""Wzbogacanie artystów danymi z Last.fm: liczba słuchaczy, globalne odsłuchy i tagi.

Stan trzymamy w kolumnie `artist.enriched_at`, więc przerwane wzbogacanie po prostu
bierze następnych artystów bez świeżych danych.
"""

import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import Engine, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from .lastfm import ArtistInfo, ArtistNotFound, ArtistTag, LastfmClient, LastfmError
from .locks import ENRICHMENT_LOCK, release_lock, try_lock
from .models import Artist, Scrobble, Tag
from .models import ArtistTag as ArtistTagRow

logger = logging.getLogger(__name__)

# Po tylu dniach pytamy Last.fm o artystę ponownie
ENRICHMENT_TTL = timedelta(days=30)
# Tylu artystów naraz; wspólny limit zapytań klienta i tak obowiązuje
CONCURRENCY = 3
# Zapisujemy najwyżej tyle tagów artysty (Last.fm zwraca dziś do 10)
MAX_TAGS = 15
# Tyle nieudanych artystów z rzędu oznacza, że kłopot jest po stronie Last.fm albo
# klucza API, a nie pojedynczych nazw: przerywamy, następne uruchomienie spróbuje znowu
MAX_FAILURES_IN_A_ROW = 20


class EnrichmentAborted(Exception):
    """Wzbogacanie przerwane, bo kolejne zapytania do Last.fm kończyły się błędem."""


async def run_enrichment(client: LastfmClient, db: Engine) -> int:
    """Wzbogaca artystów bez świeżych danych, od najczęściej słuchanych.

    Zwraca liczbę zapisanych artystów. Nic nie robi, gdy wzbogacanie już trwa.
    """
    lock = await asyncio.to_thread(try_lock, db, ENRICHMENT_LOCK)
    if lock is None:
        logger.info("Wzbogacanie artystów trwa już w innym zadaniu, pomijam")
        return 0
    try:
        return await _enrich_all(client, db)
    finally:
        await asyncio.to_thread(release_lock, lock, ENRICHMENT_LOCK)


async def _enrich_all(client: LastfmClient, db: Engine) -> int:
    artists = await asyncio.to_thread(_artists_to_enrich, db)
    if not artists:
        return 0
    logger.info("Wzbogacanie: %d artystów do odświeżenia", len(artists))

    todo = iter(artists)  # wspólna kolejka: każdy z równoległych wykonawców bierze następnego
    saved = 0
    failures_in_a_row = 0

    async def worker() -> None:
        nonlocal saved, failures_in_a_row
        for artist_id, name in todo:
            if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
                return
            try:
                info, tags = await _fetch(client, name)
            except (LastfmError, httpx.HTTPError) as exc:
                # Artysta zostaje bez danych i wróci przy następnym uruchomieniu
                failures_in_a_row += 1
                logger.warning("Wzbogacanie: błąd dla artysty %r: %s", name, exc)
                continue
            failures_in_a_row = 0
            await asyncio.to_thread(_save, db, artist_id, info, tags)
            saved += 1
            if saved % 500 == 0:
                logger.info("Wzbogacanie: zapisano %d z %d", saved, len(artists))

    await asyncio.gather(*(worker() for _ in range(CONCURRENCY)))
    if failures_in_a_row >= MAX_FAILURES_IN_A_ROW:
        raise EnrichmentAborted(f"{failures_in_a_row} błędów Last.fm z rzędu, zapisano {saved}")
    logger.info("Wzbogacanie zakończone: zapisano %d z %d", saved, len(artists))
    return saved


async def _fetch(client: LastfmClient, name: str) -> tuple[ArtistInfo | None, list[ArtistTag]]:
    try:
        return await client.get_artist_info(name), await client.get_artist_top_tags(name)
    except ArtistNotFound:
        # Last.fm nie zna artysty: zapisujemy „sprawdzony, bez danych”
        return None, []


def _artists_to_enrich(db: Engine) -> list[tuple[int, str]]:
    """Artyści nigdy niesprawdzeni albo sprawdzeni dawno temu, od najczęściej słuchanych."""
    plays = (
        select(Scrobble.artist_id, func.count().label("plays"))
        .group_by(Scrobble.artist_id)
        .subquery()
    )
    with Session(db) as session:
        rows = session.execute(
            select(Artist.id, Artist.name)
            .outerjoin(plays, plays.c.artist_id == Artist.id)
            .where(
                or_(Artist.enriched_at.is_(None), Artist.enriched_at < func.now() - ENRICHMENT_TTL)
            )
            .order_by(func.coalesce(plays.c.plays, 0).desc(), Artist.id)
        )
        return [(artist_id, name) for artist_id, name in rows]


def _save(db: Engine, artist_id: int, info: ArtistInfo | None, tags: list[ArtistTag]) -> None:
    """Zapisuje dane artysty i podmienia jego tagi w jednej transakcji."""
    # Nazwy małymi literami; przy powtórce po normalizacji wygrywa tag o wyższej wadze
    weights: dict[str, int] = {}
    for tag in sorted(tags, key=lambda t: t.weight, reverse=True)[:MAX_TAGS]:
        name = tag.name.strip().lower()
        if name:
            weights.setdefault(name, tag.weight)

    with Session(db) as session:
        values: dict[str, object] = {"enriched_at": func.now()}
        if info is not None:
            values |= {
                "listeners": info.listeners,
                "global_playcount": info.playcount,
                # mbid ze scrobbli zostaje; dopisujemy tylko brakujący
                "mbid": func.coalesce(Artist.mbid, info.mbid),
            }
        session.execute(update(Artist).where(Artist.id == artist_id).values(**values))

        session.execute(delete(ArtistTagRow).where(ArtistTagRow.artist_id == artist_id))
        if weights:
            # Stała kolejność wstawiania: równoległe zapisy tych samych tagów nie zakleszczą się
            names = sorted(weights)
            session.execute(
                insert(Tag).on_conflict_do_nothing(index_elements=["name"]),
                [{"name": name} for name in names],
            )
            tag_ids = session.execute(select(Tag.name, Tag.id).where(Tag.name.in_(names)))
            session.execute(
                insert(ArtistTagRow),
                [
                    {"artist_id": artist_id, "tag_id": tag_id, "weight": weights[name]}
                    for name, tag_id in tag_ids
                ],
            )
        session.commit()
