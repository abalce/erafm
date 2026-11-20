"""Testy zarażeń w duecie na sztucznych danych."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.infection import duet_infections, niche_weight

A, B = 1, 2
# Na początku historii każdej osoby jest odsłuch artysty „Start”: wyznacza początek konta
START = datetime(2023, 1, 1, 12, tzinfo=UTC)


def day(n: int) -> datetime:
    """Dzień `n` od początku historii obu osób."""
    return START + timedelta(days=n)


def test_niche_weight() -> None:
    assert niche_weight(100_000) == pytest.approx(0.2)
    assert niche_weight(None) == pytest.approx(0.2)  # niewzbogacony: jak 100 tys.
    assert niche_weight(1) == pytest.approx(0.5)  # 1 słuchacz nie daje wagi nieskończonej
    assert niche_weight(1_000_000) == pytest.approx(1 / 6)


def load(
    db: Engine,
    plays: list[tuple[int, str, datetime, int]],
    listeners: dict[str, int | None] | None = None,
) -> None:
    """Dwie osoby i ich odsłuchy (osoba, artysta, początek, liczba odsłuchów co godzinę)."""
    listeners = listeners or {}
    artists = sorted({artist for _, artist, _, _ in plays})
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) VALUES "
                "(1, 'ala', 'Ala', now()), (2, 'basia', 'Basia', now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO artist (id, name, name_key, listeners) "
                "VALUES (:id, :name, lower(:name), :listeners)"
            ),
            [
                {"id": i + 1, "name": name, "listeners": listeners.get(name, 100_000)}
                for i, name in enumerate(artists)
            ],
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) "
                "SELECT id, id, name, name_key FROM artist"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "SELECT :user, :at + n * interval '1 hour', a.id, a.id "
                "FROM artist a, generate_series(0, :count - 1) AS n WHERE a.name = :artist"
            ),
            [
                {"user": user, "artist": artist, "at": at, "count": count}
                for user, artist, at, count in plays
            ],
        )
    with Session(db) as session:
        recompute_user_stats(session, A)
        recompute_user_stats(session, B)
        session.commit()


def found(db: Engine) -> dict[str, list[str]]:
    result = duet_infections(db, "ala", "basia")
    return {
        f"{side.source}->{side.target}": [i.artist_name for i in side.infections]
        for side in result.sides
    }


def history_start() -> list[tuple[int, str, datetime, int]]:
    return [(A, "Start", day(0), 1), (B, "Start", day(0), 1)]


def test_a_listens_before_b_gives_one_infection(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            *history_start(),
            (A, "Mojave 3", day(100), 5),  # tuż przed B: aktywny nosiciel
            (B, "Mojave 3", day(150), 12),  # B jest fanem, po okresie rozruchu
        ],
        listeners={"Mojave 3": 10_000},
    )

    result = duet_infections(clean_db, "ala", "basia")

    ala_to_basia, basia_to_ala = result.sides
    assert (ala_to_basia.source, ala_to_basia.target) == ("ala", "basia")
    [infection] = ala_to_basia.infections
    assert infection.model_dump() == {
        "artist_id": 1,  # id według kolejności alfabetycznej: „Mojave 3” przed „Start”
        "artist_name": "Mojave 3",
        "listeners": 10_000,
        "weight": 0.25,
        "source_first_at": day(100),
        "source_plays": 5,
        "target_first_at": day(150),
        "target_plays": 12,
    }
    assert ala_to_basia.balance == 0.25
    assert basia_to_ala.infections == [] and basia_to_ala.balance == 0


def test_first_play_during_startup_is_not_an_infection(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            (A, "Start", day(-200), 1),
            (B, "Start", day(0), 1),
            (A, "Mojave 3", day(0), 5),  # A jest aktywnym nosicielem
            (B, "Mojave 3", day(30), 12),  # ale to pierwsze 60 dni historii B
        ],
    )

    assert found(clean_db) == {"ala->basia": [], "basia->ala": []}


@pytest.mark.parametrize(
    ("a_plays", "b_plays"),
    [
        pytest.param((day(100), 4), (day(150), 12), id="A słuchał za mało"),
        pytest.param((day(100), 5), (day(150), 9), id="B nie jest fanem"),
        # A słuchał 5 razy, ale pół roku przed B: wtedy nie był aktywnym nosicielem
        pytest.param((day(100), 5), (day(300), 12), id="A słuchał dawno"),
    ],
)
def test_thresholds(
    clean_db: Engine, a_plays: tuple[datetime, int], b_plays: tuple[datetime, int]
) -> None:
    load(clean_db, [*history_start(), (A, "Mojave 3", *a_plays), (B, "Mojave 3", *b_plays)])

    assert found(clean_db) == {"ala->basia": [], "basia->ala": []}


def test_source_history_must_cover_the_whole_window(clean_db: Engine) -> None:
    # A zaczyna scrobblować 30 dni przed pierwszym odsłuchem B: nie wiemy, czego słuchał
    # wcześniej, więc nawet 5 odsłuchów w tym czasie nie wystarczy
    load(
        clean_db,
        [
            (A, "Start", day(120), 1),
            (B, "Start", day(0), 1),
            (A, "Mojave 3", day(121), 5),
            (B, "Mojave 3", day(150), 12),
        ],
    )

    assert found(clean_db) == {"ala->basia": [], "basia->ala": []}


def test_artist_with_one_listener_does_not_dominate_the_balance(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            *history_start(),
            (A, "Nikt", day(100), 5),
            (B, "Nikt", day(150), 12),
            (A, "Radiohead", day(100), 5),
            (B, "Radiohead", day(160), 12),
        ],
        listeners={"Nikt": 1, "Radiohead": 10_000_000},
    )

    result = duet_infections(clean_db, "ala", "basia")

    weights = {i.artist_name: i.weight for i in result.sides[0].infections}
    assert weights == {"Nikt": 0.5, "Radiohead": pytest.approx(1 / 7, abs=1e-4)}
    assert result.sides[0].balance == pytest.approx(0.5 + 1 / 7, abs=1e-3)


def test_infections_go_both_ways(clean_db: Engine) -> None:
    load(
        clean_db,
        [
            *history_start(),
            (A, "Mojave 3", day(100), 5),
            (B, "Mojave 3", day(150), 12),
            (B, "Hole", day(200), 6),
            (A, "Hole", day(220), 15),
        ],
    )

    assert found(clean_db) == {"ala->basia": ["Mojave 3"], "basia->ala": ["Hole"]}
