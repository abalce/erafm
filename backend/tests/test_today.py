"""Testy „tego dnia X lat temu” i rocznic pierwszego odsłuchu."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.main import app, get_db
from backend.today import month_days_around, same_day_in_year, years_between


def test_same_day_in_year_moves_leap_day_to_28th() -> None:
    assert same_day_in_year(date(2028, 2, 29), 2024) == date(2024, 2, 29)
    assert same_day_in_year(date(2028, 2, 29), 2025) == date(2025, 2, 28)
    assert same_day_in_year(date(2026, 11, 20), 2023) == date(2023, 11, 20)


def test_month_days_around_adds_leap_day_and_crosses_new_year() -> None:
    assert month_days_around(date(2026, 3, 1), 2) == [
        "02-27",
        "02-28",
        "02-29",
        "03-01",
        "03-02",
        "03-03",
    ]
    assert month_days_around(date(2026, 1, 1), 1) == ["01-01", "01-02", "12-31"]


def test_years_between_counts_to_the_nearest_anniversary() -> None:
    assert years_between(date(2020, 11, 22), date(2026, 11, 20)) == 6
    # rocznica wypada po Nowym Roku, więc to rok, a nie dwa lata
    assert years_between(date(2024, 12, 30), date(2026, 1, 2)) == 1
    assert years_between(date(2025, 1, 2), date(2025, 12, 30)) == 1


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def load_plays(db: Engine, plays: list[tuple[str, str, datetime]]) -> None:
    """Osoba z odsłuchami (artysta, utwór, chwila w UTC)."""
    artists = sorted({artist for artist, _, _ in plays})
    tracks = sorted({(artist, track) for artist, track, _ in plays})
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) "
                "VALUES (1, 'przykladowa', 'Przykladowa', now())"
            )
        )
        conn.execute(
            text("INSERT INTO artist (id, name, name_key) VALUES (:id, :name, lower(:name))"),
            [{"id": i + 1, "name": name} for i, name in enumerate(artists)],
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) "
                "SELECT :id, id, :name, lower(:name) FROM artist WHERE name = :artist"
            ),
            [{"id": i + 1, "artist": a, "name": t} for i, (a, t) in enumerate(tracks)],
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "SELECT 1, :at, t.id, t.artist_id FROM track t JOIN artist a ON a.id = t.artist_id "
                "WHERE a.name = :artist AND t.name = :track"
            ),
            [{"artist": a, "track": t, "at": at} for a, t, at in plays],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()


def at(day: str, hour: int = 12, minute: int = 0) -> datetime:
    return datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=UTC)


def many(artist: str, track: str, start: datetime, count: int) -> list[tuple[str, str, datetime]]:
    return [(artist, track, start + timedelta(minutes=i)) for i in range(count)]


def test_years_with_plays_on_the_same_day(client: TestClient, clean_db: Engine) -> None:
    load_plays(
        clean_db,
        [
            *many("Radiohead", "Creep", at("2023-11-20"), 3),
            ("Björk", "Jóga", at("2023-11-20", 13)),
            # 19 listopada 23:30 UTC to w Polsce już 20 listopada
            ("Björk", "Hyperballad", at("2024-11-19", 23, 30)),
            # 20 listopada 23:30 UTC to w Polsce 21 listopada: nie liczy się
            ("Radiohead", "Creep", at("2024-11-20", 23, 30)),
            ("Radiohead", "Creep", at("2025-06-01")),
        ],
    )

    body = client.get("/api/users/przykladowa/today?date=2026-11-20").json()

    assert body["date"] == "2026-11-20"
    assert body["years"] == [
        {
            "year": 2024,
            "years_ago": 2,
            "day": "2024-11-20",
            "plays": 1,
            "top_artists": [{"name": "Björk", "plays": 1}],
            "top_track": {"name": "Hyperballad", "artist_name": "Björk", "plays": 1},
        },
        {
            "year": 2023,
            "years_ago": 3,
            "day": "2023-11-20",
            "plays": 4,
            "top_artists": [{"name": "Radiohead", "plays": 3}, {"name": "Björk", "plays": 1}],
            "top_track": {"name": "Creep", "artist_name": "Radiohead", "plays": 3},
        },
    ]


def test_leap_day_uses_28th_in_other_years(client: TestClient, clean_db: Engine) -> None:
    load_plays(
        clean_db,
        [("Radiohead", "Creep", at("2024-02-29")), ("Radiohead", "Creep", at("2025-02-28"))],
    )

    body = client.get("/api/users/przykladowa/today?date=2028-02-29").json()

    assert [(y["day"], y["years_ago"]) for y in body["years"]] == [
        ("2025-02-28", 3),
        ("2024-02-29", 4),
    ]


def test_anniversaries(client: TestClient, clean_db: Engine) -> None:
    load_plays(
        clean_db,
        [
            # początek konta: Shakira „poznana” w pierwszych 60 dniach się nie liczy
            *many("Shakira", "Hips Don't Lie", at("2020-11-21"), 30),
            # poznani w tym tygodniu kalendarzowym, potem słuchani dużo
            ("Mojave 3", "My Life In Art", at("2021-11-22")),
            *many("Mojave 3", "Who Do You Love", at("2022-01-10"), 40),
            ("Galaxie 500", "Strange", at("2022-11-18")),
            *many("Galaxie 500", "Tugboat", at("2023-01-10"), 25),
            # za mało odsłuchów
            *many("Low", "Words", at("2021-11-20"), 10),
            # poznany tydzień za wcześnie
            *many("Slowdive", "Alison", at("2021-11-10"), 30),
            # 29 lutego: rocznica przypada na 28 lutego albo 1 marca
            *many("Hole", "Malibu", at("2024-02-29"), 20),
        ],
    )

    body = client.get("/api/users/przykladowa/today?date=2026-11-20").json()

    assert body["anniversaries"] == [
        {
            "artist_id": 4,  # id według kolejności alfabetycznej w load_plays
            "artist_name": "Mojave 3",
            "first_at": "2021-11-22T12:00:00Z",
            "years_ago": 5,
            "first_track": "My Life In Art",
            "total_plays": 41,
        },
        {
            "artist_id": 1,
            "artist_name": "Galaxie 500",
            "first_at": "2022-11-18T12:00:00Z",
            "years_ago": 4,
            "first_track": "Strange",
            "total_plays": 26,
        },
    ]
    leap = client.get("/api/users/przykladowa/today?date=2026-03-01").json()
    assert [(a["artist_name"], a["years_ago"]) for a in leap["anniversaries"]] == [("Hole", 2)]


def test_recent_discoveries_are_not_anniversaries(client: TestClient, clean_db: Engine) -> None:
    load_plays(
        clean_db,
        [
            *many("Radiohead", "Creep", at("2023-01-01"), 5),  # początek konta
            *many("Hole", "Malibu", at("2025-12-30"), 30),  # trzy dni przed „dziś”
        ],
    )

    body = client.get("/api/users/przykladowa/today?date=2026-01-02").json()

    assert body["anniversaries"] == []


def test_invalid_date(client: TestClient, clean_db: Engine) -> None:
    load_plays(clean_db, [("Radiohead", "Creep", at("2024-01-01"))])

    response = client.get("/api/users/przykladowa/today?date=20-11-2026")

    assert (response.status_code, response.json()) == (422, {"detail": "invalid_date"})


def test_today_for_unknown_user(client: TestClient) -> None:
    response = client.get("/api/users/nikt/today")

    assert (response.status_code, response.json()) == (404, {"detail": "not_imported"})
