"""Testy historii jednego artysty u jednej osoby."""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.main import app, get_db


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def load(db: Engine) -> None:
    """Ala słucha Radiohead w dwóch tygodniach; Björk jest w bazie, ale Ala jej nie zna."""
    plays = [
        (2, datetime(2024, 1, 3, 12, tzinfo=UTC)),  # Karma Police jako pierwszy odsłuch
        (1, datetime(2024, 1, 3, 13, tzinfo=UTC)),
        (1, datetime(2024, 1, 4, 13, tzinfo=UTC)),
        (1, datetime(2024, 3, 6, 12, tzinfo=UTC)),
    ]
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) "
                "VALUES (1, 'ala', 'Ala', now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO artist (id, name, name_key, listeners) VALUES "
                "(1, 'Radiohead', 'radiohead', 8000000), (2, 'Björk', 'björk', NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) VALUES "
                "(1, 1, 'Creep', 'creep'), (2, 1, 'Karma Police', 'karma police')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "VALUES (1, :at, :track, 1)"
            ),
            [{"track": track, "at": at} for track, at in plays],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()


def test_artist_history(client: TestClient, clean_db: Engine) -> None:
    load(clean_db)

    response = client.get("/api/users/Ala/artists/1")

    assert response.status_code == 200
    assert response.json() == {
        "username": "ala",
        "artist_id": 1,
        "name": "Radiohead",
        "listeners": 8000000,
        "image_url": None,
        "total_plays": 4,
        "first_at": "2024-01-03T12:00:00Z",
        "last_at": "2024-03-06T12:00:00Z",
        "first_track": "Karma Police",
        "weeks": [{"week": "2024-01-01", "plays": 3}, {"week": "2024-03-04", "plays": 1}],
    }


def test_artist_the_person_never_played(client: TestClient, clean_db: Engine) -> None:
    load(clean_db)

    body = client.get("/api/users/ala/artists/2").json()

    assert (body["name"], body["total_plays"], body["first_at"], body["weeks"]) == (
        "Björk",
        0,
        None,
        [],
    )


def test_unknown_artist(client: TestClient, clean_db: Engine) -> None:
    load(clean_db)

    response = client.get("/api/users/ala/artists/999")

    assert (response.status_code, response.json()) == (404, {"detail": "artist_not_found"})


def test_artist_for_unknown_person(client: TestClient, clean_db: Engine) -> None:
    load(clean_db)

    response = client.get("/api/users/nikt/artists/1")

    assert (response.status_code, response.json()) == (404, {"detail": "not_imported"})


def test_weeks_follow_polish_time(client: TestClient, clean_db: Engine) -> None:
    load(clean_db)
    # niedziela 23:30 UTC to w Polsce już poniedziałek: odsłuch idzie do nowego tygodnia
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "VALUES (1, :at, 1, 1)"
            ),
            {"at": datetime(2024, 3, 10, 23, 30, tzinfo=UTC)},
        )
    with Session(clean_db) as session:
        recompute_user_stats(session, 1)
        session.commit()

    weeks = client.get("/api/users/ala/artists/1").json()["weeks"]

    assert weeks[-1] == {"week": "2024-03-11", "plays": 1}
