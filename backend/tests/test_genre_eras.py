"""Testy er gatunkowych na sztucznych tygodniach i tagach."""

from collections.abc import Iterator
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from backend.genre_eras import user_genre_eras
from backend.main import app, get_db
from backend.tags import tag_key

MONDAY = date(2024, 1, 1)


def week(n: int) -> date:
    return MONDAY + timedelta(weeks=n)


def load(db: Engine, tags: dict[str, dict[str, int]], plays: list[tuple[int, str, int]]) -> None:
    """Osoba `ala`: artyści z tagami {artysta: {tag: waga}} i tygodnie
    (tydzień, artysta, odsłuchy)."""
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) "
                "VALUES (1, 'ala', 'Ala', now())"
            )
        )
        for name in sorted(tags):
            conn.execute(
                text("INSERT INTO artist (name, name_key) VALUES (:n, lower(:n))"), {"n": name}
            )
        all_tags = sorted({t for artist_tags in tags.values() for t in artist_tags})
        conn.execute(text("INSERT INTO tag (name) VALUES (:t)"), [{"t": t} for t in all_tags])
        conn.execute(
            text(
                "INSERT INTO artist_tag (artist_id, tag_id, weight) "
                "SELECT a.id, t.id, :w FROM artist a, tag t WHERE a.name = :a AND t.name = :t"
            ),
            [
                {"a": a, "t": t, "w": w}
                for a, artist_tags in tags.items()
                for t, w in artist_tags.items()
            ],
        )
        conn.execute(
            text(
                "INSERT INTO weekly_artist_stats (user_id, week, artist_id, plays) "
                "SELECT 1, :week, id, :plays FROM artist WHERE name = :a"
            ),
            [{"week": week(n), "a": a, "plays": p} for n, a, p in plays],
        )
        # first_listen wyznacza artystów osoby (do rzadkości tagów)
        conn.execute(
            text(
                "INSERT INTO first_listen (user_id, artist_id, first_at, last_at, total_plays) "
                "SELECT 1, artist_id, min(week), max(week), sum(plays) "
                "FROM weekly_artist_stats WHERE user_id = 1 GROUP BY artist_id"
            )
        )


def scenario(db: Engine) -> None:
    """Tło: przez 80 tygodni co tydzień 20 artystów po 2 odsłuchy, każdy z tagiem „rock”
    i własnym tagiem. Tygodnie 15–24: dwóch artystów shoegaze; 50–57: k-pop zapisany na dwa
    sposoby. Ery zajmują mniej niż 30% tygodni, więc próg osoby zostaje na poziomie tła."""
    tags = {f"Tło {i}": {"rock": 100, f"tło{i}": 50} for i in range(100)}
    tags |= {
        "Slowdive": {"rock": 100, "shoegaze": 100, "seen live": 100},
        "Hum": {"rock": 100, "shoegaze": 80},
        # „rock” także tu: ma go każdy artysta osoby, więc jego rzadkość to dokładnie zero
        "aespa": {"rock": 100, "k-pop": 100},
        "TWICE": {"rock": 100, "kpop": 100},
    }
    plays = [(w, f"Tło {(w % 5) * 20 + k}", 2) for w in range(80) for k in range(20)]
    plays += [(w, a, n) for w in range(15, 25) for a, n in (("Slowdive", 16), ("Hum", 14))]
    plays += [(w, a, 15) for w in range(50, 58) for a in ("aespa", "TWICE")]
    load(db, tags, plays)


def test_rare_genres_win_over_broad_tags(clean_db: Engine) -> None:
    scenario(clean_db)

    result = user_genre_eras(clean_db, "ala")

    # „rock” ma każdy artysta, więc nic nie mówi i nie wygrywa żadnego tygodnia
    assert [(e.tag, e.start, e.end) for e in result.eras] == [
        ("shoegaze", week(15), week(24) + timedelta(days=6)),
        ("k-pop", week(50), week(57) + timedelta(days=6)),
    ]


def test_period_artists_and_plays(clean_db: Engine) -> None:
    scenario(clean_db)

    shoegaze = user_genre_eras(clean_db, "ala").eras[0]

    # „rock” ma rzadkość zero, więc każdy odsłuch Slowdive i Hum idzie w całości do shoegaze
    assert shoegaze.plays == 300
    assert shoegaze.share == pytest.approx(300 / (300 + 400), abs=1e-3)
    assert [(a.name, a.plays) for a in shoegaze.top_artists] == [("Slowdive", 160), ("Hum", 140)]


def test_spellings_are_one_genre(clean_db: Engine) -> None:
    scenario(clean_db)

    kpop = user_genre_eras(clean_db, "ala").eras[1]

    assert {a.name for a in kpop.top_artists} == {"aespa", "TWICE"}
    assert kpop.plays == 240
    assert tag_key("K-Pop") == tag_key("kpop") == tag_key("k pop") == "kpop"


def test_ignored_tags_make_no_eras(clean_db: Engine) -> None:
    load(
        clean_db,
        {"Slowdive": {"seen live": 100, "80s": 90}, "Tło": {"tło": 100}},
        [(w, "Slowdive", 30) for w in range(10)] + [(w, "Tło", 30) for w in range(10)],
    )

    result = user_genre_eras(clean_db, "ala")

    assert all(e.tag == "tło" for e in [*result.eras, *result.episodes])


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_endpoint(client: TestClient, clean_db: Engine) -> None:
    scenario(clean_db)

    body = client.get("/api/users/Ala/genre-eras").json()

    assert [e["tag"] for e in body["eras"]] == ["shoegaze", "k-pop"]
    assert body["eras"][0]["top_artists"][0] == {"artist_id": 2, "name": "Slowdive", "plays": 160}


def test_endpoint_unknown_person(client: TestClient) -> None:
    response = client.get("/api/users/nikt/genre-eras")

    assert (response.status_code, response.json()) == (404, {"detail": "not_imported"})
