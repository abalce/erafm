"""Testy importu historii: prawdziwa baza testowa, udawany Last.fm."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tenacity import wait_none

from backend import importer
from backend.lastfm import LastfmClient
from backend.locks import IMPORT_LOCK, release_lock, try_lock
from backend.models import Artist, LfmUser, Scrobble, SyncJob, Track

pytestmark = pytest.mark.anyio

T0 = 1_700_000_000  # 14 listopada 2023, 22:13:20 UTC


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeLastfm:
    """Udaje API Last.fm dla jednej osoby: profil i stronicowaną historię odsłuchów."""

    def __init__(
        self, plays: list[tuple[str, str, int]], *, display_name: str = "Przykladowa"
    ) -> None:
        # plays: (artysta, utwór, czas uniksowy)
        self.plays = plays
        self.display_name = display_name
        self.error: tuple[int, str] | None = None  # błąd dla każdego zapytania
        self.fail_on_page: int | None = None  # błąd tylko przy tej stronie historii
        self.pages_requested: list[int] = []
        self.to_values: set[str] = set()
        self.from_values: set[str | None] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if self.error is not None:
            return self._error(*self.error)
        if params["method"] == "user.getinfo":
            return httpx.Response(200, json=self._user_info())
        page, limit = int(params["page"]), int(params["limit"])
        self.pages_requested.append(page)
        self.to_values.add(params["to"])
        self.from_values.add(params.get("from"))
        if page == self.fail_on_page:
            return self._error(10, "Invalid API key")
        window = (int(params.get("from", 0)), int(params["to"]))
        return httpx.Response(200, json=self._recent_tracks(page, limit, window))

    @staticmethod
    def _error(code: int, message: str) -> httpx.Response:
        return httpx.Response(400, json={"error": code, "message": message})

    def _user_info(self) -> dict:
        return {
            "user": {
                "name": self.display_name,
                "playcount": str(len(self.plays)),
                "registered": {"unixtime": str(T0 - 1000)},
            }
        }

    def _recent_tracks(self, page: int, limit: int, window: tuple[int, int]) -> dict:
        # Jak w Last.fm: od najnowszych, strona 1 to najnowsze odsłuchy
        plays = sorted(
            (p for p in self.plays if window[0] <= p[2] <= window[1]),
            key=lambda p: p[2],
            reverse=True,
        )
        chunk = plays[(page - 1) * limit : page * limit]
        return {
            "recenttracks": {
                "track": [
                    {
                        "artist": {"mbid": "", "#text": artist},
                        "name": track,
                        "album": {"mbid": "", "#text": f"Album {track}"},
                        "date": {"uts": str(uts)},
                    }
                    for artist, track, uts in chunk
                ],
                "@attr": {
                    "page": str(page),
                    "perPage": str(limit),
                    "totalPages": str(math.ceil(len(plays) / limit)),
                    "total": str(len(plays)),
                },
            }
        }


@pytest.fixture
def lastfm_mock() -> Iterator[respx.MockRouter]:
    with respx.mock as mock:
        yield mock


async def run_import(
    mock: respx.MockRouter, fake: FakeLastfm, db: Engine, username: str = "przykladowa"
) -> bool:
    mock.get(host="ws.audioscrobbler.com", path="/2.0/").mock(side_effect=fake)
    # Bez czekania między próbami i bez limitu, żeby testy były szybkie
    async with LastfmClient("test-key", max_rate=1000, retry_wait=wait_none()) as client:
        return await importer.run_import(client, db, username, page_size=2)


def the_job(db: Engine) -> SyncJob:
    with Session(db) as session:
        return session.scalars(select(SyncJob)).one()


def all_jobs(db: Engine) -> list[SyncJob]:
    with Session(db) as session:
        return list(session.scalars(select(SyncJob).order_by(SyncJob.id)))


def aggregate_totals(db: Engine) -> tuple[int, int]:
    """Suma odsłuchów zapisana w weekly_artist_stats i w first_listen."""
    with db.connect() as conn:
        return (
            conn.scalar(text("SELECT coalesce(sum(plays), 0) FROM weekly_artist_stats")),
            conn.scalar(text("SELECT coalesce(sum(total_plays), 0) FROM first_listen")),
        )


def age_last_sync(db: Engine, minutes: int) -> None:
    """Udaje, że ostatni import skończył się `minutes` minut temu."""
    with db.begin() as conn:
        conn.execute(
            text("UPDATE sync_job SET to_ts = to_ts - make_interval(mins => :m)"), {"m": minutes}
        )


def scrobble_count(db: Engine) -> int:
    with Session(db) as session:
        return session.scalar(select(func.count()).select_from(Scrobble))


FIVE_PLAYS = [
    ("Radiohead", "Creep", T0),
    ("radiohead", "Creep", T0 + 300),
    ("Björk", "Jóga", T0 + 600),
    ("Radiohead", "Karma Police", T0 + 900),
    ("Björk", "Jóga", T0 + 1200),
]


async def test_full_import_stores_whole_history(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    await run_import(lastfm_mock, fake, clean_db, username="  PrzykLadowa ")

    job = the_job(clean_db)
    assert (job.status, job.total_pages, job.pages_done, job.error) == ("done", 3, 3, None)
    assert job.duplicates_skipped == 0
    # Jedno zapytanie o liczbę stron, potem strony od najstarszej (3) do najnowszej (1)
    assert fake.pages_requested == [1, 3, 2, 1]
    assert len(fake.to_values) == 1

    with Session(clean_db) as session:
        user = session.scalars(select(LfmUser)).one()
        assert (user.username, user.display_name, user.playcount) == (
            "przykladowa",
            "Przykladowa",
            5,
        )
        assert user.last_synced_at == job.to_ts
        # Artysta rozpoznawany bez względu na wielkość liter; pisownia z pierwszego odsłuchu
        assert sorted(session.scalars(select(Artist.name))) == ["Björk", "Radiohead"]
        assert session.scalar(select(func.count()).select_from(Track)) == 3
        assert session.scalar(select(Track.album_name).where(Track.name == "Creep")) == (
            "Album Creep"
        )
        played = session.scalars(select(Scrobble.played_at).order_by(Scrobble.played_at)).all()
        assert played == [datetime.fromtimestamp(p[2], UTC) for p in FIVE_PLAYS]
    # Zakończony import ma już przeliczone agregaty
    assert aggregate_totals(clean_db) == (5, 5)


async def test_duplicates_under_30s_are_skipped_also_across_pages(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    plays = [
        ("Radiohead", "Creep", T0),
        ("Radiohead", "Creep", T0 + 10),  # powtórka na tej samej stronie
        ("Björk", "Jóga", T0 + 15),  # inny utwór w tym samym czasie zostaje
        ("Radiohead", "Creep", T0 + 40),  # 40 s od zachowanego odsłuchu: zostaje
        ("Radiohead", "Creep", T0 + 60),  # powtórka; poprzedni odsłuch jest na innej stronie
    ]
    await run_import(lastfm_mock, FakeLastfm(plays), clean_db)

    job = the_job(clean_db)
    assert (job.status, job.duplicates_skipped) == ("done", 2)
    with Session(clean_db) as session:
        played = session.scalars(select(Scrobble.played_at).order_by(Scrobble.played_at)).all()
    assert played == [datetime.fromtimestamp(T0 + s, UTC) for s in (0, 15, 40)]


async def test_interrupted_import_resumes_without_gaps_or_repeats(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    fake.fail_on_page = 2
    await run_import(lastfm_mock, fake, clean_db)

    failed = the_job(clean_db)
    assert (failed.status, failed.error, failed.pages_done) == ("failed", "lastfm_error", 1)
    assert scrobble_count(clean_db) == 1  # tylko najstarsza strona
    assert aggregate_totals(clean_db) == (0, 0)  # agregaty powstają dopiero z całej historii

    # Między próbami przybył nowy odsłuch: nie może przesunąć stron wznowionego importu
    fake.plays = [*FIVE_PLAYS, ("Radiohead", "Creep", int(datetime.now(UTC).timestamp()) + 60)]
    fake.fail_on_page = None
    fake.pages_requested.clear()
    await run_import(lastfm_mock, fake, clean_db)

    resumed = the_job(clean_db)  # ten sam wiersz, nie nowe zadanie
    assert (resumed.id, resumed.status, resumed.error) == (failed.id, "done", None)
    assert (resumed.pages_done, resumed.to_ts) == (3, failed.to_ts)
    assert fake.pages_requested == [2, 1]
    assert len(fake.to_values) == 1
    assert scrobble_count(clean_db) == 5


@pytest.mark.parametrize(
    ("lastfm_code", "expected"),
    [(6, "user_not_found"), (17, "history_hidden"), (10, "lastfm_error")],
)
async def test_lastfm_errors_mark_job_failed(
    lastfm_mock: respx.MockRouter, clean_db: Engine, lastfm_code: int, expected: str
) -> None:
    fake = FakeLastfm([])
    fake.error = (lastfm_code, "błąd z Last.fm")
    await run_import(lastfm_mock, fake, clean_db)

    job = the_job(clean_db)
    assert (job.status, job.error) == ("failed", expected)
    assert scrobble_count(clean_db) == 0


async def test_empty_history_finishes(lastfm_mock: respx.MockRouter, clean_db: Engine) -> None:
    await run_import(lastfm_mock, FakeLastfm([]), clean_db)

    job = the_job(clean_db)
    assert (job.status, job.total_pages, job.pages_done) == ("done", 0, 0)


async def test_import_skipped_when_another_one_holds_the_lock(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    fake.error = (6, "User not found")
    await run_import(lastfm_mock, fake, clean_db)  # zakłada osobę i zadanie
    fake.error = None

    with Session(clean_db) as session:
        user_id = session.scalars(select(LfmUser.id)).one()
    lock = try_lock(clean_db, IMPORT_LOCK, user_id)
    assert lock is not None
    try:
        await run_import(lastfm_mock, fake, clean_db)
        assert fake.pages_requested == []
        assert scrobble_count(clean_db) == 0
    finally:
        release_lock(lock, IMPORT_LOCK, user_id)

    await run_import(lastfm_mock, fake, clean_db)
    assert scrobble_count(clean_db) == 5


LAST_PLAY = FIVE_PLAYS[-1][2]
NEW_PLAYS = [
    ("Björk", "Hyperballad", LAST_PLAY - 3600),  # dosłany z opóźnieniem, ze starszą datą
    ("Radiohead", "Creep", LAST_PLAY + 500),
    ("Portishead", "Roads", LAST_PLAY + 800),
]


async def test_fresh_history_is_not_refreshed_again(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    await run_import(lastfm_mock, fake, clean_db)
    fake.plays = [*FIVE_PLAYS, *NEW_PLAYS]
    fake.pages_requested.clear()

    await run_import(lastfm_mock, fake, clean_db)  # mniej niż 5 minut po poprzednim

    assert fake.pages_requested == []
    assert len(all_jobs(clean_db)) == 1
    assert scrobble_count(clean_db) == 5


async def test_incremental_import_fetches_only_recent_window(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    await run_import(lastfm_mock, fake, clean_db)
    age_last_sync(clean_db, minutes=10)
    fake.plays = [*FIVE_PLAYS, *NEW_PLAYS]
    fake.pages_requested.clear()
    fake.from_values.clear()

    await run_import(lastfm_mock, fake, clean_db)

    full, incremental = all_jobs(clean_db)
    assert (full.kind, full.from_ts) == ("full", None)
    assert (incremental.kind, incremental.status) == ("incremental", "done")
    # Okno zaczyna się 14 dni przed ostatnim odsłuchem w bazie
    window_start = datetime.fromtimestamp(LAST_PLAY, UTC) - timedelta(days=14)
    assert incremental.from_ts == window_start
    assert fake.from_values == {str(int(window_start.timestamp()))}
    # Wszystkie 8 odsłuchów mieści się w oknie: 4 strony po 2 zamiast całej historii od zera
    assert fake.pages_requested == [1, 4, 3, 2, 1]
    assert scrobble_count(clean_db) == 8  # stare bez powtórek, nowe razem z dosłanym
    assert aggregate_totals(clean_db) == (8, 8)
    with Session(clean_db) as session:
        assert session.scalars(select(LfmUser.last_synced_at)).one() == incremental.to_ts


async def test_interrupted_incremental_import_resumes_same_window(
    lastfm_mock: respx.MockRouter, clean_db: Engine
) -> None:
    fake = FakeLastfm(FIVE_PLAYS)
    await run_import(lastfm_mock, fake, clean_db)
    age_last_sync(clean_db, minutes=10)
    fake.plays = [*FIVE_PLAYS, *NEW_PLAYS]
    fake.fail_on_page = 2
    await run_import(lastfm_mock, fake, clean_db)

    failed = all_jobs(clean_db)[-1]
    assert (failed.kind, failed.status, failed.pages_done) == ("incremental", "failed", 2)

    fake.fail_on_page = None
    fake.pages_requested.clear()
    await run_import(lastfm_mock, fake, clean_db)

    resumed = all_jobs(clean_db)[-1]
    assert (resumed.id, resumed.status) == (failed.id, "done")
    assert (resumed.from_ts, resumed.to_ts) == (failed.from_ts, failed.to_ts)
    assert fake.pages_requested == [2, 1]
    assert scrobble_count(clean_db) == 8


def test_only_one_active_job_per_user(clean_db: Engine) -> None:
    insert_job = text(
        "INSERT INTO sync_job (user_id, kind, status, to_ts) VALUES (1, 'full', :status, now())"
    )
    with clean_db.begin() as conn:
        conn.execute(text("INSERT INTO lfm_user (username, display_name) VALUES ('a', 'A')"))
        conn.execute(insert_job, {"status": "done"})
        conn.execute(insert_job, {"status": "running"})
    with pytest.raises(IntegrityError), clean_db.begin() as conn:
        conn.execute(insert_job, {"status": "pending"})
