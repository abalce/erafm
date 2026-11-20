"""Testy kompatybilności dwóch osób na sztucznych tygodniach."""

import math
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text

from backend.compat import CompatParams, duet_compatibility

A, B = 1, 2
MONDAY = date(2024, 1, 1)


def week(n: int) -> date:
    return MONDAY + timedelta(weeks=n)


def load(db: Engine, rows: list[tuple[int, int, str, int]]) -> None:
    """Dwie osoby i ich tygodnie (osoba, numer tygodnia od 1.01.2024, artysta, odsłuchy)."""
    artists = sorted({artist for _, _, artist, _ in rows})
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) VALUES "
                "(1, 'ala', 'Ala', now()), (2, 'basia', 'Basia', now())"
            )
        )
        conn.execute(
            text("INSERT INTO artist (id, name, name_key) VALUES (:id, :name, lower(:name))"),
            [{"id": i + 1, "name": name} for i, name in enumerate(artists)],
        )
        conn.execute(
            text(
                "INSERT INTO weekly_artist_stats (user_id, week, artist_id, plays) "
                "SELECT :user, :week, id, :plays FROM artist WHERE name = :artist"
            ),
            [
                {"user": user, "week": week(n), "artist": artist, "plays": plays}
                for user, n, artist, plays in rows
            ],
        )


def compat(db: Engine, **params: int):
    return duet_compatibility(db, "ala", "basia", CompatParams(**params))


def test_identical_tastes_are_fully_compatible(clean_db: Engine) -> None:
    taste = {"Radiohead": 50, "Björk": 20, "Low": 5}
    load(clean_db, [(user, 0, a, n) for user in (A, B) for a, n in taste.items()])

    result = compat(clean_db, min_year_plays=1)

    assert result.similarity == 1.0
    assert result.shared_artists == 3
    assert [(y.year, y.similarity) for y in result.years] == [(2024, 1.0)]


def test_disjoint_tastes_are_not_compatible(clean_db: Engine) -> None:
    load(clean_db, [(A, 0, "Radiohead", 50), (B, 0, "Wardruna", 50)])

    result = compat(clean_db, min_year_plays=1)

    assert (result.similarity, result.shared_artists, result.connecting) == (0.0, 0, [])


def test_logarithm_dampens_one_obsession(clean_db: Engine) -> None:
    # A „katuje” Radiohead, B słucha obu artystów po równo
    load(
        clean_db,
        [(A, 0, "Radiohead", 1000), (A, 0, "Low", 10), (B, 0, "Radiohead", 10), (B, 0, "Low", 10)],
    )

    result = compat(clean_db)

    a = [math.log1p(1000), math.log1p(10)]
    b = [math.log1p(10), math.log1p(10)]
    expected = (a[0] * b[0] + a[1] * b[1]) / (math.hypot(*a) * math.hypot(*b))
    assert result.similarity == pytest.approx(expected, abs=1e-4)
    # bez logarytmu wyszłoby ok. 0,71: Radiohead przesądziłoby o wyniku
    assert result.similarity > 0.85


def test_only_the_shared_period_counts(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            # zanim B zaczęło scrobblować, A słuchała tylko Wardruny
            (A, 0, "Wardruna", 500),
            (A, 10, "Radiohead", 30),
            (B, 10, "Radiohead", 30),
        ],
    )

    result = compat(clean_db, min_year_plays=1)

    assert result.since == week(10)
    assert result.similarity == 1.0
    assert result.disputed_first == []  # Wardruna jest sprzed wspólnego okresu


def test_years_need_enough_plays(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            (A, 0, "Radiohead", 150),
            (B, 0, "Radiohead", 150),
            (A, 52, "Radiohead", 150),  # 2024-12-30 to poniedziałek, więc tydzień 2024
            (A, 53, "Radiohead", 150),  # 2025
            (B, 53, "Radiohead", 99),  # za mało odsłuchów B w 2025
        ],
    )

    result = compat(clean_db)

    assert [(y.year, y.plays_first, y.plays_second) for y in result.years] == [(2024, 300, 150)]


def test_connecting_and_disputed_artists(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            (A, 0, "Radiohead", 100),
            (B, 0, "Radiohead", 10),
            (A, 0, "Low", 40),
            (B, 0, "Low", 40),
            (A, 0, "Taco Hemingway", 80),
            (B, 0, "Taco Hemingway", 2),
            (A, 0, "BTS", 60),
            (B, 0, "Wardruna", 70),
            (B, 0, "Giles Corey", 30),
            (A, 0, "Giles Corey", 3),  # 3 odsłuchy to już nie „prawie wcale”
        ],
    )

    result = compat(clean_db, top_disputed=2)

    # Low ma najwięcej z mniejszego udziału: 40/283 u A i 40/152 u B
    assert [(a.name, a.plays_first, a.plays_second) for a in result.connecting][:2] == [
        ("Low", 40, 40),
        ("Radiohead", 100, 10),
    ]
    assert [(a.name, a.plays, a.other_plays) for a in result.disputed_first] == [
        ("Taco Hemingway", 80, 2),
        ("BTS", 60, 0),
    ]
    assert [(a.name, a.plays, a.other_plays) for a in result.disputed_second] == [
        ("Wardruna", 70, 0)
    ]


def test_person_without_plays(clean_db: Engine) -> None:
    load(clean_db, [(A, 0, "Radiohead", 10)])

    result = compat(clean_db)

    assert (result.since, result.similarity, result.years) == (None, 0.0, [])
