"""Testy odkrywania: ziarna, punktacja, odrzucanie znanych i pobieranie podobnych."""

import math
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from procrastinate import testing
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from tenacity import wait_none

from backend import worker
from backend.aggregates import recompute_user_stats
from backend.lastfm import LastfmClient
from backend.main import app, get_db
from backend.recommend import recommendations
from backend.similar import run_similar

LAST = datetime(2025, 6, 1, 12, tzinfo=UTC)  # ostatni scrobble osoby
LONG_AGO = LAST - timedelta(days=200)  # poza oknem 90 dni ziaren


def load(db: Engine, plays: dict[str, tuple[int, datetime]]) -> dict[str, int]:
    """Osoba `ala` i jej odsłuchy {artysta: (liczba, kiedy ostatni)}; zwraca id artystów."""
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) "
                "VALUES (1, 'ala', 'Ala', now())"
            )
        )
        for name, (count, last) in plays.items():
            artist_id = conn.scalar(
                text("INSERT INTO artist (name, name_key) VALUES (:n, lower(:n)) RETURNING id"),
                {"n": name},
            )
            conn.execute(
                text(
                    "INSERT INTO track (id, artist_id, name, name_key) "
                    "VALUES (:a, :a, 'Utwór', 'utwór')"
                ),
                {"a": artist_id},
            )
            conn.execute(
                text(
                    "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                    "SELECT 1, :last - make_interval(mins => n), :a, :a "
                    "FROM generate_series(0, :count - 1) n"
                ),
                {"a": artist_id, "last": last, "count": count},
            )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()
    return ids(db)


def ids(db: Engine) -> dict[str, int]:
    with db.connect() as conn:
        return dict(conn.execute(text("SELECT name, id FROM artist")).all())


def add_similar(db: Engine, pairs: list[tuple[str, str, float]], fetched: list[str]) -> None:
    """Podobni (ziarno, kandydat, match); kandydaci spoza historii trafiają do `artist`."""
    with db.begin() as conn:
        for _, candidate, _ in pairs:
            conn.execute(
                text(
                    "INSERT INTO artist (name, name_key) VALUES (:n, lower(:n)) "
                    "ON CONFLICT (name_key) DO NOTHING"
                ),
                {"n": candidate},
            )
        conn.execute(
            text(
                "INSERT INTO artist_similar (artist_id, similar_id, match) "
                "SELECT s.id, c.id, :m FROM artist s, artist c WHERE s.name = :s AND c.name = :c"
            ),
            [{"s": s, "c": c, "m": m} for s, c, m in pairs],
        )
        conn.execute(
            text("UPDATE artist SET similar_fetched_at = now() WHERE name = ANY(:names)"),
            {"names": fetched},
        )


def history(db: Engine) -> None:
    load(
        db,
        {
            "A": (20, LAST),  # ziarno
            "B": (5, LAST - timedelta(days=30)),  # ziarno
            "C": (50, LONG_AGO),  # dawno: nie jest ziarnem
            "K": (3, LONG_AGO),  # znany (3 odsłuchy), choć nie słuchany ostatnio
            "L": (2, LONG_AGO),  # 2 odsłuchy to jeszcze nie „znany”
        },
    )


def test_scores_reasons_and_known_artists(clean_db: Engine) -> None:
    history(clean_db)
    add_similar(
        clean_db,
        [
            ("A", "X", 0.9),
            ("A", "Y", 0.5),
            ("A", "K", 1.0),
            ("A", "L", 0.4),
            ("B", "X", 0.8),
            ("B", "Z", 1.0),
            ("C", "W", 1.0),
        ],
        fetched=["A", "B", "C"],
    )

    result = recommendations(clean_db, "ala")

    assert (result.seeds, result.pending) == (2, 0)
    ln_a, ln_b = math.log(21), math.log(6)
    assert [(r.name, r.because) for r in result.items] == [
        ("X", ["A", "B"]),
        ("Z", ["B"]),
        ("Y", ["A"]),
        ("L", ["A"]),
    ]
    scores = {r.name: r.score for r in result.items}
    assert scores["X"] == pytest.approx(0.9 * ln_a + 0.8 * ln_b, abs=1e-3)
    assert scores["L"] == pytest.approx(0.4 * ln_a, abs=1e-3)


def test_seed_without_similar_is_pending(clean_db: Engine) -> None:
    history(clean_db)
    add_similar(clean_db, [("A", "X", 0.9), ("B", "Z", 1.0)], fetched=["A"])

    result = recommendations(clean_db, "ala")

    assert (result.seeds, result.pending) == (2, 1)
    assert [r.name for r in result.items] == ["X"]


# --- endpoint ---


@pytest.fixture
def queue() -> Iterator[testing.InMemoryConnector]:
    connector = testing.InMemoryConnector()
    with worker.app.replace_connector(connector):
        yield connector


@pytest.fixture
def client(clean_db: Engine, queue: testing.InMemoryConnector) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def queued(queue: testing.InMemoryConnector) -> list[tuple[str, dict]]:
    return [(job["task_name"], job["args"]) for job in queue.jobs.values()]


def test_endpoint_queues_missing_similar(
    client: TestClient, clean_db: Engine, queue: testing.InMemoryConnector
) -> None:
    history(clean_db)

    response = client.get("/api/users/Ala/recommendations")

    assert response.status_code == 200
    assert response.json() == {"username": "ala", "seeds": 2, "pending": 2, "items": []}
    assert queued(queue) == [("backend.worker.fetch_similar", {"username": "ala"})]


def test_endpoint_with_everything_ready_queues_nothing(
    client: TestClient, clean_db: Engine, queue: testing.InMemoryConnector
) -> None:
    history(clean_db)
    add_similar(clean_db, [("A", "X", 0.9)], fetched=["A", "B"])

    body = client.get("/api/users/ala/recommendations").json()

    assert [(r["name"], r["because"]) for r in body["items"]] == [("X", ["A"])]
    assert queued(queue) == []


def test_endpoint_for_unknown_person(client: TestClient) -> None:
    response = client.get("/api/users/nikt/recommendations")

    assert (response.status_code, response.json()) == (404, {"detail": "not_imported"})


# --- pobieranie podobnych z Last.fm ---


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def similar_json(*artists: tuple[str, float]) -> dict:
    return {
        "similarartists": {
            "artist": [{"name": name, "mbid": "", "match": str(match)} for name, match in artists]
        }
    }


@pytest.mark.anyio
async def test_run_similar_saves_new_candidates(clean_db: Engine) -> None:
    history(clean_db)
    answers = {
        # „x” i „X” to ten sam artysta po normalizacji: zostaje wyższe podobieństwo;
        # A podobny sam do siebie jest pomijany
        "A": httpx.Response(200, json=similar_json(("X", 0.9), ("x", 0.95), ("K", 0.5), ("a", 1))),
        "B": httpx.Response(400, json={"error": 6, "message": "Artist not found"}),
    }

    def lastfm(request: httpx.Request) -> httpx.Response:
        return answers[request.url.params["artist"]]

    with respx.mock as mock:
        mock.get(host="ws.audioscrobbler.com", path="/2.0/").mock(side_effect=lastfm)
        async with LastfmClient("test-key", max_rate=1000, retry_wait=wait_none()) as client:
            saved = await run_similar(client, clean_db, "ala")

    assert saved == 2
    with clean_db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT s.name, c.name, sim.match FROM artist_similar sim "
                "JOIN artist s ON s.id = sim.artist_id JOIN artist c ON c.id = sim.similar_id "
                "ORDER BY c.name"
            )
        ).all()
        fetched = conn.execute(
            text("SELECT name FROM artist WHERE similar_fetched_at IS NOT NULL ORDER BY name")
        ).scalars()
        fetched = list(fetched)
    assert [tuple(r) for r in rows] == [("A", "K", 0.5), ("A", "x", 0.95)]
    assert fetched == ["A", "B"]  # B: Last.fm go nie zna, ale jest sprawdzony


@pytest.mark.anyio
async def test_run_similar_skips_fresh_seeds_and_keeps_failed_for_later(
    clean_db: Engine,
) -> None:
    history(clean_db)
    add_similar(clean_db, [("A", "X", 0.9)], fetched=["A"])
    asked: list[str] = []

    def lastfm(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.params["artist"])
        return httpx.Response(500, text="awaria")

    with respx.mock as mock:
        mock.get(host="ws.audioscrobbler.com", path="/2.0/").mock(side_effect=lastfm)
        async with LastfmClient("test-key", max_rate=1000, retry_wait=wait_none()) as client:
            saved = await run_similar(client, clean_db, "ala")

    assert saved == 0
    assert set(asked) == {"B"}  # A ma świeże dane
    with clean_db.connect() as conn:
        assert conn.scalar(text("SELECT similar_fetched_at FROM artist WHERE name = 'B'")) is None
