"""Testy wzbogacania artystów: prawdziwa baza testowa, udawany Last.fm."""

from collections.abc import Iterator

import httpx
import pytest
import respx
from sqlalchemy import Engine, text
from tenacity import wait_none

from backend import enrichment
from backend.lastfm import LastfmClient
from backend.locks import ENRICHMENT_LOCK, release_lock, try_lock

pytestmark = pytest.mark.anyio

BIG_PLAYCOUNT = 3_100_000_000  # więcej, niż mieści zwykła kolumna całkowita


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeLastfm:
    """Udaje artist.getInfo i artist.getTopTags."""

    def __init__(self) -> None:
        self.tags: dict[str, list[tuple[str, int]]] = {}
        self.errors: dict[str, int] = {}  # artysta -> kod błędu Last.fm
        self.asked: list[str] = []  # artyści w kolejności zapytań o getInfo

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        artist = params["artist"]
        if params["method"] == "artist.getinfo":
            self.asked.append(artist)
        if artist in self.errors:
            return httpx.Response(400, json={"error": self.errors[artist], "message": "błąd"})
        if params["method"] == "artist.getinfo":
            stats = {"listeners": "1000", "playcount": str(BIG_PLAYCOUNT)}
            return httpx.Response(200, json={"artist": {"mbid": "", "stats": stats}})
        tags = [{"name": name, "count": weight} for name, weight in self.tags.get(artist, [])]
        return httpx.Response(200, json={"toptags": {"tag": tags}})


@pytest.fixture
def fake() -> Iterator[FakeLastfm]:
    fake = FakeLastfm()
    with respx.mock as mock:
        mock.get(host="ws.audioscrobbler.com", path="/2.0/").mock(side_effect=fake)
        yield fake


async def enrich(db: Engine) -> int:
    # Bez czekania między próbami i bez limitu, żeby testy były szybkie
    async with LastfmClient("test-key", max_rate=1000, retry_wait=wait_none()) as client:
        return await enrichment.run_enrichment(client, db)


def add_artist(db: Engine, name: str, plays: int) -> None:
    """Dodaje artystę z jednym utworem i podaną liczbą odsłuchów."""
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name) VALUES (1, 'a', 'A') "
                "ON CONFLICT DO NOTHING"
            )
        )
        artist_id = conn.scalar(
            text("INSERT INTO artist (name, name_key) VALUES (:n, lower(:n)) RETURNING id"),
            {"n": name},
        )
        track_id = conn.scalar(
            text(
                "INSERT INTO track (artist_id, name, name_key) VALUES (:a, 'Utwór', 'utwór') "
                "RETURNING id"
            ),
            {"a": artist_id},
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "SELECT 1, now() - make_interval(hours => n), :t, :a FROM generate_series(1, :p) n"
            ),
            {"t": track_id, "a": artist_id, "p": plays},
        )


def artist_row(db: Engine, name: str) -> tuple:
    with db.connect() as conn:
        return conn.execute(
            text(
                "SELECT listeners, global_playcount, enriched_at IS NOT NULL FROM artist "
                "WHERE name = :n"
            ),
            {"n": name},
        ).one()


def tags_of(db: Engine, name: str) -> list[tuple[str, int]]:
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT t.name, at.weight FROM artist_tag at "
                "JOIN tag t ON t.id = at.tag_id JOIN artist a ON a.id = at.artist_id "
                "WHERE a.name = :n ORDER BY at.weight DESC, t.name"
            ),
            {"n": name},
        )
        return [tuple(row) for row in rows]


async def test_saves_stats_and_tags_most_played_first(
    fake: FakeLastfm, clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enrichment, "CONCURRENCY", 1)  # żeby kolejność zapytań była pewna
    add_artist(clean_db, "Rzadki", plays=1)
    add_artist(clean_db, "Ulubiony", plays=3)
    add_artist(clean_db, "Średni", plays=2)
    fake.tags["Ulubiony"] = [("Rock", 100), ("seen live", 40), ("rock", 7), (" ", 5)]

    assert await enrich(clean_db) == 3

    assert fake.asked == ["Ulubiony", "Średni", "Rzadki"]
    assert artist_row(clean_db, "Ulubiony") == (1000, BIG_PLAYCOUNT, True)
    # Surowe tagi małymi literami; przy powtórce nazwy zostaje wyższa waga
    assert tags_of(clean_db, "Ulubiony") == [("rock", 100), ("seen live", 40)]
    assert tags_of(clean_db, "Rzadki") == []


async def test_fresh_artists_are_skipped_and_stale_ones_refreshed(
    fake: FakeLastfm, clean_db: Engine
) -> None:
    add_artist(clean_db, "Świeży", plays=2)
    add_artist(clean_db, "Dawny", plays=1)
    fake.tags["Dawny"] = [("shoegaze", 100), ("dream pop", 60)]
    await enrich(clean_db)
    fake.asked.clear()

    assert await enrich(clean_db) == 0
    assert fake.asked == []

    with clean_db.begin() as conn:
        conn.execute(
            text("UPDATE artist SET enriched_at = now() - interval '31 days' WHERE name = 'Dawny'")
        )
    fake.tags["Dawny"] = [("shoegaze", 100), ("noise pop", 30)]

    assert await enrich(clean_db) == 1
    assert fake.asked == ["Dawny"]
    assert tags_of(clean_db, "Dawny") == [("shoegaze", 100), ("noise pop", 30)]


async def test_unknown_artist_is_marked_as_checked(fake: FakeLastfm, clean_db: Engine) -> None:
    add_artist(clean_db, "Nieznany", plays=1)
    fake.errors["Nieznany"] = 6

    assert await enrich(clean_db) == 1
    assert artist_row(clean_db, "Nieznany") == (None, None, True)

    fake.asked.clear()
    await enrich(clean_db)
    assert fake.asked == []  # wrócimy do niego dopiero po 30 dniach


async def test_failing_artist_is_left_for_next_run(fake: FakeLastfm, clean_db: Engine) -> None:
    add_artist(clean_db, "Pechowy", plays=2)
    add_artist(clean_db, "Zwykły", plays=1)
    fake.errors["Pechowy"] = 8  # błąd przejściowy: klient ponawia i w końcu się poddaje

    assert await enrich(clean_db) == 1
    assert artist_row(clean_db, "Pechowy") == (None, None, False)
    assert artist_row(clean_db, "Zwykły") == (1000, BIG_PLAYCOUNT, True)

    del fake.errors["Pechowy"]
    assert await enrich(clean_db) == 1
    assert artist_row(clean_db, "Pechowy") == (1000, BIG_PLAYCOUNT, True)


async def test_aborts_when_lastfm_keeps_failing(
    fake: FakeLastfm, clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(enrichment, "CONCURRENCY", 1)
    monkeypatch.setattr(enrichment, "MAX_FAILURES_IN_A_ROW", 2)
    for plays, name in enumerate(["Czwarty", "Trzeci", "Drugi", "Pierwszy"], start=1):
        add_artist(clean_db, name, plays=plays)
        fake.errors[name] = 10  # np. nieważny klucz API

    with pytest.raises(enrichment.EnrichmentAborted):
        await enrich(clean_db)

    assert fake.asked == ["Pierwszy", "Drugi"]


async def test_skipped_when_another_run_holds_the_lock(fake: FakeLastfm, clean_db: Engine) -> None:
    add_artist(clean_db, "Ktoś", plays=1)
    lock = try_lock(clean_db, ENRICHMENT_LOCK)
    assert lock is not None
    try:
        assert await enrich(clean_db) == 0
        assert fake.asked == []
    finally:
        release_lock(lock, ENRICHMENT_LOCK)
