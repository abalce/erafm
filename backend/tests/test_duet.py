"""Testy endpointu duetu: łączenie zarażeń i kompatybilności oraz sprawdzanie adresu."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.main import app, get_db

START = datetime(2023, 1, 1, 12, tzinfo=UTC)


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def load_pair(db: Engine) -> None:
    """Ala podrzuca Basi Mojave 3: słucha go tuż przed pierwszym odsłuchem Basi."""
    plays = [
        (1, "Start", START, 1),
        (2, "Start", START, 1),
        (1, "Mojave 3", START + timedelta(days=100), 5),
        (2, "Mojave 3", START + timedelta(days=150), 12),
    ]
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) VALUES "
                "(1, 'ala', 'Ala', now()), (2, 'basia', 'Basia', now())"
            )
        )
        conn.execute(
            text(
                "INSERT INTO artist (id, name, name_key) VALUES "
                "(1, 'Mojave 3', 'mojave 3'), (2, 'Start', 'start')"
            )
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
            [{"user": u, "artist": a, "at": at, "count": n} for u, a, at, n in plays],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        recompute_user_stats(session, 2)
        session.commit()


def test_duet_combines_infections_and_compatibility(client: TestClient, clean_db: Engine) -> None:
    load_pair(clean_db)

    response = client.get("/api/duet", params={"u": "Ala, BASIA"})

    assert response.status_code == 200
    body = response.json()
    assert body["people"] == [
        {"username": "ala", "display_name": "Ala"},
        {"username": "basia", "display_name": "Basia"},
    ]
    sides = body["infections"]["sides"]
    assert [(s["source"], s["target"]) for s in sides] == [("ala", "basia"), ("basia", "ala")]
    assert [i["artist_name"] for i in sides[0]["infections"]] == ["Mojave 3"]
    assert sides[1]["infections"] == []
    compat = body["compatibility"]
    assert (compat["first"], compat["second"]) == ("ala", "basia")
    assert compat["shared_artists"] == 2
    assert 0 < compat["similarity"] <= 1


def test_order_in_the_address_decides_who_is_first(client: TestClient, clean_db: Engine) -> None:
    load_pair(clean_db)

    body = client.get("/api/duet", params={"u": "basia,ala"}).json()

    assert [p["username"] for p in body["people"]] == ["basia", "ala"]
    sides = body["infections"]["sides"]
    assert [(s["source"], len(s["infections"])) for s in sides] == [("basia", 0), ("ala", 1)]
    assert body["compatibility"]["first"] == "basia"


@pytest.mark.parametrize("u", ["ala", "ala,ala", "ala,Ala ", "ala,basia,celina", "ala,", "ala,a b"])
def test_invalid_pair(client: TestClient, u: str) -> None:
    response = client.get("/api/duet", params={"u": u})

    assert (response.status_code, response.json()) == (422, {"detail": "invalid_duet"})


def test_person_without_history(client: TestClient, clean_db: Engine) -> None:
    load_pair(clean_db)

    response = client.get("/api/duet", params={"u": "ala,nikt"})

    assert (response.status_code, response.json()) == (404, {"detail": "not_imported"})


def test_person_during_first_import(client: TestClient, clean_db: Engine) -> None:
    load_pair(clean_db)
    with clean_db.begin() as conn:
        conn.execute(
            text("INSERT INTO lfm_user (id, username, display_name) VALUES (3, 'celina', 'Celina')")
        )
        conn.execute(
            text(
                "INSERT INTO sync_job (user_id, kind, status, to_ts) "
                "VALUES (3, 'full', 'running', now())"
            )
        )

    response = client.get("/api/duet", params={"u": "ala,celina"})

    assert (response.status_code, response.json()) == (409, {"detail": "import_in_progress"})
