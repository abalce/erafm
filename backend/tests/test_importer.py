"""Testy importu pełnej historii: prawdziwa baza testowa, udawany Last.fm."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import Engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from tenacity import wait_none

from backend.importer import _acquire_lock, _release_lock, run_full_import
from backend.lastfm import LastfmClient
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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = request.url.params
        if self.error is not None:
            return self._error(*self.error)
        if params["method"] == "user.getinfo":
            return httpx.Response(200, json=self._user_info())
        page, limit = int(params["page"]), int(params["limit"])
        self.pages_requested.append(page)
        self.to_values.add(params["to"])
        if page == self.fail_on_page:
            return self._error(10, "Invalid API key")
        return httpx.Response(200, json=self._recent_tracks(page, limit, int(params["to"])))

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

    def _recent_tracks(self, page: int, limit: int, to: int) -> dict:
        # Jak w Last.fm: od najnowszych, strona 1 to najnowsze odsłuchy
        plays = sorted((p for p in self.plays if p[2] <= to), key=lambda p: p[2], reverse=True)
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
) -> None:
    mock.get(host="ws.audioscrobbler.com", path="/2.0/").mock(side_effect=fake)
    # Bez czekania między próbami i bez limitu, żeby testy były szybkie
    async with LastfmClient("test-key", max_rate=1000, retry_wait=wait_none()) as client:
        await run_full_import(client, db, username, page_size=2)


def the_job(db: Engine) -> SyncJob:
    with Session(db) as session:
        return session.scalars(select(SyncJob)).one()


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
    lock = _acquire_lock(clean_db, user_id)
    assert lock is not None
    try:
        await run_import(lastfm_mock, fake, clean_db)
        assert fake.pages_requested == []
        assert scrobble_count(clean_db) == 0
    finally:
        _release_lock(lock, user_id)

    await run_import(lastfm_mock, fake, clean_db)
    assert scrobble_count(clean_db) == 5


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
