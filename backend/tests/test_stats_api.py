"""Testy endpointu statystyk na bazie testowej."""

from collections.abc import Iterator
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.main import app, get_db

# (id utworu, czas odsłuchu w UTC); utwory 1 i 2 są Radiohead, utwór 3 jest Björk
PLAYS = [
    (1, datetime(2024, 1, 10, 12, 0, tzinfo=UTC)),
    (1, datetime(2024, 1, 20, 12, 0, tzinfo=UTC)),
    # 31 stycznia 23:30 UTC to w Polsce już 1 lutego
    (3, datetime(2024, 1, 31, 23, 30, tzinfo=UTC)),
    (1, datetime(2024, 4, 2, 8, 0, tzinfo=UTC)),
    (2, datetime(2024, 4, 2, 8, 5, tzinfo=UTC)),
    (3, datetime(2024, 4, 3, 9, 0, tzinfo=UTC)),
]


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_user(db: Engine, *, synced: bool, job_status: str) -> None:
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name, last_synced_at) "
                "VALUES (1, 'przykladowa', 'Przykladowa', CASE WHEN :synced THEN now() END)"
            ),
            {"synced": synced},
        )
        conn.execute(
            text(
                "INSERT INTO sync_job (user_id, kind, status, to_ts) "
                "VALUES (1, 'full', :status, now())"
            ),
            {"status": job_status},
        )


def add_history(db: Engine) -> None:
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO artist (id, name, name_key, image_url) VALUES "
                "(1, 'Radiohead', 'radiohead', NULL), (2, 'Björk', 'björk', 'https://img/b.jpg')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) VALUES "
                "(1, 1, 'Creep', 'creep'), (2, 1, 'Karma Police', 'karma police'), "
                "(3, 2, 'Jóga', 'jóga')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "SELECT 1, :played_at, :track_id, artist_id FROM track WHERE id = :track_id"
            ),
            [{"played_at": at, "track_id": track_id} for track_id, at in PLAYS],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()


def test_stats_for_imported_user(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=True, job_status="done")
    add_history(clean_db)

    response = client.get("/users/Przykladowa/stats")

    assert response.status_code == 200
    body = response.json()
    assert (body["username"], body["display_name"]) == ("przykladowa", "Przykladowa")
    assert body["totals"] == {
        "scrobbles": 6,
        "artists": 2,
        "tracks": 3,
        "first_scrobble_at": "2024-01-10T12:00:00Z",
    }
    assert body["top_artists"] == [
        {"id": 1, "name": "Radiohead", "plays": 4, "image_url": None},
        {"id": 2, "name": "Björk", "plays": 2, "image_url": "https://img/b.jpg"},
    ]
    assert body["top_tracks"] == [
        {"id": 1, "name": "Creep", "artist_name": "Radiohead", "plays": 3},
        {"id": 3, "name": "Jóga", "artist_name": "Björk", "plays": 2},
        {"id": 2, "name": "Karma Police", "artist_name": "Radiohead", "plays": 1},
    ]


def test_monthly_series_is_continuous_and_in_app_timezone(
    client: TestClient, clean_db: Engine
) -> None:
    add_user(clean_db, synced=True, job_status="done")
    add_history(clean_db)

    monthly = client.get("/users/przykladowa/stats").json()["monthly"]

    # Styczeń, luty (odsłuch z 31 stycznia 23:30 UTC), marzec bez słuchania, kwiecień
    assert monthly[:4] == [
        {"month": "2024-01-01", "plays": 2},
        {"month": "2024-02-01", "plays": 1},
        {"month": "2024-03-01", "plays": 0},
        {"month": "2024-04-01", "plays": 3},
    ]
    # Seria ciągnie się zerami do bieżącego miesiąca
    today = date.today()
    assert monthly[-1]["month"] == today.replace(day=1).isoformat()
    assert len(monthly) == (today.year - 2024) * 12 + today.month
    assert sum(m["plays"] for m in monthly) == 6


def test_limit_shortens_top_lists(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=True, job_status="done")
    add_history(clean_db)

    body = client.get("/users/przykladowa/stats", params={"limit": 1}).json()

    assert [a["name"] for a in body["top_artists"]] == ["Radiohead"]
    assert [t["name"] for t in body["top_tracks"]] == ["Creep"]


@pytest.mark.parametrize("limit", [0, 51])
def test_limit_out_of_range(client: TestClient, limit: int) -> None:
    response = client.get("/users/przykladowa/stats", params={"limit": limit})

    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_limit"}


def test_stats_during_refresh_are_served(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=True, job_status="running")
    add_history(clean_db)

    assert client.get("/users/przykladowa/stats").status_code == 200


@pytest.mark.parametrize(
    ("job_status", "status_code", "detail"),
    [("running", 409, "import_in_progress"), ("failed", 404, "not_imported")],
)
def test_stats_without_finished_import(
    client: TestClient, clean_db: Engine, job_status: str, status_code: int, detail: str
) -> None:
    add_user(clean_db, synced=False, job_status=job_status)

    response = client.get("/users/przykladowa/stats")

    assert response.status_code == status_code
    assert response.json() == {"detail": detail}


def test_stats_of_unknown_user(client: TestClient, clean_db: Engine) -> None:
    response = client.get("/users/nieznana/stats")

    assert response.status_code == 404
    assert response.json() == {"detail": "not_imported"}
