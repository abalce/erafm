"""Testy tabel wyliczanych ze scrobbli: weekly_artist_stats i first_listen."""

from datetime import UTC, date, datetime

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats

# (artysta, czas odsłuchu w UTC)
PLAYS = [
    (1, datetime(2024, 3, 4, 10, 0, tzinfo=UTC)),  # poniedziałek
    (1, datetime(2024, 3, 6, 18, 30, tzinfo=UTC)),
    (2, datetime(2024, 3, 10, 12, 0, tzinfo=UTC)),  # niedziela tego samego tygodnia
    # Niedziela 23:30 UTC to w Polsce już poniedziałek 00:30: następny tydzień
    (1, datetime(2024, 3, 10, 23, 30, tzinfo=UTC)),
    (2, datetime(2024, 3, 12, 9, 0, tzinfo=UTC)),
]


def add_history(db: Engine, user_id: int, plays: list[tuple[int, datetime]]) -> None:
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (id, username, display_name) VALUES (:id, :nick, :nick) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": user_id, "nick": f"osoba{user_id}"},
        )
        for artist_id in sorted({artist_id for artist_id, _ in plays}):
            conn.execute(
                text(
                    "INSERT INTO artist (id, name, name_key) VALUES (:id, :n, :n) "
                    "ON CONFLICT DO NOTHING"
                ),
                {"id": artist_id, "n": f"artysta{artist_id}"},
            )
            conn.execute(
                text(
                    "INSERT INTO track (id, artist_id, name, name_key) "
                    "VALUES (:id, :id, 'utwór', 'utwór') ON CONFLICT DO NOTHING"
                ),
                {"id": artist_id},
            )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "VALUES (:user_id, :played_at, :artist_id, :artist_id)"
            ),
            [{"user_id": user_id, "played_at": at, "artist_id": a} for a, at in plays],
        )


def recompute(db: Engine, user_id: int, timezone: str = "Europe/Warsaw") -> None:
    with Session(db) as session:
        recompute_user_stats(session, user_id, timezone)
        session.commit()


def weekly(db: Engine, user_id: int) -> list[tuple]:
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT week, artist_id, plays FROM weekly_artist_stats "
                "WHERE user_id = :u ORDER BY week, artist_id"
            ),
            {"u": user_id},
        )
        return [tuple(row) for row in rows]


def first_listens(db: Engine, user_id: int) -> list[tuple]:
    with db.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT artist_id, first_at, last_at, total_plays FROM first_listen "
                "WHERE user_id = :u ORDER BY artist_id"
            ),
            {"u": user_id},
        )
        return [tuple(row) for row in rows]


def test_weeks_start_on_monday_in_app_timezone(clean_db: Engine) -> None:
    add_history(clean_db, 1, PLAYS)
    recompute(clean_db, 1)

    assert weekly(clean_db, 1) == [
        (date(2024, 3, 4), 1, 2),
        (date(2024, 3, 4), 2, 1),
        (date(2024, 3, 11), 1, 1),
        (date(2024, 3, 11), 2, 1),
    ]


def test_weeks_follow_the_given_timezone(clean_db: Engine) -> None:
    add_history(clean_db, 1, PLAYS)
    recompute(clean_db, 1, timezone="UTC")

    # W UTC niedzielny odsłuch z 23:30 zostaje w tygodniu od 4 marca
    assert weekly(clean_db, 1) == [
        (date(2024, 3, 4), 1, 3),
        (date(2024, 3, 4), 2, 1),
        (date(2024, 3, 11), 2, 1),
    ]


def test_first_listen_keeps_first_last_and_total(clean_db: Engine) -> None:
    add_history(clean_db, 1, PLAYS)
    recompute(clean_db, 1)

    assert first_listens(clean_db, 1) == [
        (1, PLAYS[0][1], PLAYS[3][1], 3),
        (2, PLAYS[2][1], PLAYS[4][1], 2),
    ]


def test_recompute_replaces_old_rows_and_leaves_other_users(clean_db: Engine) -> None:
    add_history(clean_db, 1, PLAYS)
    add_history(clean_db, 2, PLAYS[:1])
    recompute(clean_db, 1)
    recompute(clean_db, 2)

    # Z historii osoby 1 znika artysta 2; ponowne przeliczenie nie może zostawić jego wierszy
    with clean_db.begin() as conn:
        conn.execute(text("DELETE FROM scrobble WHERE user_id = 1 AND artist_id = 2"))
    recompute(clean_db, 1)
    recompute(clean_db, 1)  # powtórka niczego nie dubluje

    assert weekly(clean_db, 1) == [(date(2024, 3, 4), 1, 2), (date(2024, 3, 11), 1, 1)]
    assert [row[0] for row in first_listens(clean_db, 1)] == [1]
    assert weekly(clean_db, 2) == [(date(2024, 3, 4), 1, 1)]
