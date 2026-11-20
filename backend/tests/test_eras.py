"""Testy wykrywania er: algorytm na sztucznych tygodniach i endpoint na bazie testowej."""

import random
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from backend.aggregates import recompute_user_stats
from backend.eras import Block, Detection, detect_eras, stream_artists, stream_shares
from backend.main import app, get_db

START = date(2024, 1, 1)  # poniedziałek
X = 1000  # id artysty, który ma ery


def week(i: int) -> date:
    return START + timedelta(weeks=i)


def history(
    n_weeks: int, x_weeks: dict[int, int], silent: set[int] = frozenset()
) -> dict[date, dict[int, int]]:
    """Tło: co tydzień 20 artystów po 2 odsłuchy, rotacja co 5 tygodni, więc nikt z tła
    nie dominuje. `x_weeks` dokłada artyście X podaną liczbę odsłuchów w danym tygodniu,
    a tygodnie z `silent` nie mają odsłuchów."""
    plays: dict[date, dict[int, int]] = {}
    for i in range(n_weeks):
        if i in silent:
            continue
        background = {(i % 5) * 20 + k: 2 for k in range(20)}
        if i in x_weeks:
            background[X] = x_weeks[i]
        plays[week(i)] = background
    return plays


def detect(plays: dict[date, dict[int, int]], n_weeks: int):
    return detect_eras(plays, START, week(n_weeks - 1))


def test_ten_weeks_of_one_artist_make_exactly_one_era() -> None:
    found = detect(history(40, {i: 30 for i in range(15, 25)}), 40)

    assert len(found.eras) == 1
    era = found.eras[0]
    assert (era.artist_id, era.first, era.last) == (X, 15, 24)
    assert era.plays == 300
    assert era.share == pytest.approx(300 / 700)
    assert found.episodes == []
    assert found.silences == []


def test_silence_breaks_an_era() -> None:
    x_weeks = {i: 30 for i in [*range(10, 15), *range(18, 23)]}
    found = detect(history(40, x_weeks, silent={15, 16, 17}), 40)

    assert [(e.first, e.last) for e in found.eras] == [(10, 14), (18, 22)]


def test_short_break_with_normal_listening_does_not_break_an_era() -> None:
    # te same tygodnie X co wyżej, ale w przerwie słuchasz innych artystów
    x_weeks = {i: 30 for i in [*range(10, 15), *range(18, 23)]}
    found = detect(history(40, x_weeks), 40)

    assert [(e.first, e.last, e.owned_weeks) for e in found.eras] == [(10, 22, 10)]


def test_short_burst_is_an_episode_not_an_era() -> None:
    found = detect(history(40, {20: 30, 21: 30}), 40)

    assert found.eras == []
    assert [(e.artist_id, e.first, e.last) for e in found.episodes] == [(X, 20, 21)]


def test_quiet_weeks_count_as_silence() -> None:
    plays = history(40, {})
    for i in range(20, 26):
        plays[week(i)] = {1: 5}  # mniej niż 20 odsłuchów w tygodniu

    found = detect(plays, 40)

    assert found.silences == [(20, 25)]


def test_short_silence_is_not_reported() -> None:
    found = detect(history(40, {}, silent={20, 21, 22}), 40)

    assert found.silences == []


def test_diverse_listening_has_no_eras() -> None:
    found = detect(history(40, {}), 40)

    assert found.eras == [] and found.episodes == []
    assert found.threshold == 0.05  # dolna granica progu


def test_threshold_adapts_to_the_person() -> None:
    # X ma zaledwie 10% udziału w tygodniu: przy stałym progu 15% nie byłoby ery
    found = detect(history(40, {i: 5 for i in range(15, 25)}), 40)

    assert [(e.first, e.last) for e in found.eras] == [(15, 24)]
    assert found.threshold < 0.15


def test_stream_artists_start_with_era_artists_then_most_played() -> None:
    y, z = 2000, 3000
    plays = history(40, {i: 30 for i in range(5, 15)})  # era X: 300 odsłuchów
    for i in range(25, 30):
        plays[week(i)][y] = 40  # era Y: 200 odsłuchów, mniej niż X
    for i in range(40):
        plays[week(i)][z] = 1  # Z bez ery, ale 40 odsłuchów: więcej niż ktokolwiek z tła

    found = detect(plays, 40)

    assert [e.artist_id for e in found.eras] == [X, y]
    # tło ma po 16 odsłuchów na artystę, więc remis rozstrzyga mniejsze id
    assert stream_artists(found, limit=4) == [X, y, z, 0]
    assert stream_artists(found, limit=1) == [X]


def test_stream_shares_are_smoothed_and_zero_in_silence() -> None:
    found = detect(history(40, {20: 30}, silent={30}), 40)

    [shares] = stream_shares(found, [X])

    one_week = 30 / 70 / 5  # jeden tydzień z udziałem 30/70 w oknie pięciu tygodni
    assert len(shares) == 40
    assert shares[18] == shares[20] == shares[22] == pytest.approx(one_week, abs=1e-4)
    assert shares[17] == shares[23] == 0
    assert shares[30] == 0


# --- endpoint ---


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def add_user(db: Engine, *, synced: bool, job_status: str = "done") -> None:
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


def add_radiohead_era(db: Engine) -> None:
    """Sześć tygodni samego Radiohead od 1.01.2024: co tydzień 20× Creep i 5× Karma Police."""
    with db.begin() as conn:
        conn.execute(
            text("INSERT INTO artist (id, name, name_key) VALUES (1, 'Radiohead', 'radiohead')")
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) VALUES "
                "(1, 1, 'Creep', 'creep'), (2, 1, 'Karma Police', 'karma police')"
            )
        )
        # środy w południe; minuty rozdzielają odsłuchy, bo czas jest częścią klucza
        plays = [
            (track_id, datetime(2024, 1, 3, 12, tzinfo=UTC) + timedelta(weeks=w, minutes=m))
            for w in range(6)
            for track_id, minutes in [(1, range(20)), (2, range(30, 35))]
            for m in minutes
        ]
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "VALUES (1, :played_at, :track_id, 1)"
            ),
            [{"played_at": at, "track_id": track_id} for track_id, at in plays],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()


def test_eras_endpoint(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=True)
    add_radiohead_era(clean_db)

    response = client.get("/api/users/Przykladowa/eras")

    assert response.status_code == 200
    body = response.json()
    assert body["start"] == "2024-01-01"
    assert body["eras"] == [
        {
            "artist_id": 1,
            "artist_name": "Radiohead",
            "image_url": None,
            "start": "2024-01-01",
            "end": "2024-02-11",
            "plays": 150,
            "share": 1.0,
            "first_play_at": "2024-01-03T12:00:00Z",
            "last_play_at": "2024-02-07T12:34:00Z",
            "top_tracks": [{"name": "Creep", "plays": 120}, {"name": "Karma Police", "plays": 30}],
            "artist_first": {"track_name": "Creep", "played_at": "2024-01-03T12:00:00Z"},
        }
    ]
    assert body["episodes"] == []
    streams = body["streams"]
    assert streams["artists"] == [{"id": 1, "name": "Radiohead"}]
    assert streams["weeks"][:2] == ["2024-01-01", "2024-01-08"]
    assert len(streams["shares"][0]) == len(streams["weeks"])
    assert streams["shares"][0][:6] == [1.0] * 6
    # od lutego 2024 do dziś nie ma odsłuchów, więc to jedna długa cisza
    assert body["silences"][0]["start"] == "2024-02-12"


def test_era_details_top_five_and_first_play(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=True)
    with clean_db.begin() as conn:
        conn.execute(
            text("INSERT INTO artist (id, name, name_key) VALUES (1, 'Radiohead', 'radiohead')")
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) "
                "SELECT k, 1, 'Utwór ' || k, 'utwór ' || k FROM generate_series(1, 7) AS k"
            )
        )
        # sześć tygodni od 1.01.2024: utwór k ma co tydzień 8 - k odsłuchów (28 tygodniowo)
        plays = [
            (k, datetime(2024, 1, 3, 12, tzinfo=UTC) + timedelta(weeks=w, minutes=k * 10 + j))
            for w in range(6)
            for k in range(1, 8)
            for j in range(8 - k)
        ]
        plays += [
            # pierwszy raz w ogóle, dawno przed erą (ten tydzień ma za mało odsłuchów na erę)
            (7, datetime(2023, 6, 1, 12, tzinfo=UTC)),
            # w Polsce to już 1 stycznia 2024, więc odsłuch należy do ery
            (1, datetime(2023, 12, 31, 23, 30, tzinfo=UTC)),
        ]
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "VALUES (1, :played_at, :track_id, 1)"
            ),
            [{"played_at": at, "track_id": track_id} for track_id, at in plays],
        )
    with Session(clean_db) as session:
        recompute_user_stats(session, 1)
        session.commit()

    [era] = client.get("/api/users/przykladowa/eras").json()["eras"]

    assert (era["start"], era["end"]) == ("2024-01-01", "2024-02-11")
    assert era["top_tracks"] == [
        {"name": "Utwór 1", "plays": 43},
        {"name": "Utwór 2", "plays": 36},
        {"name": "Utwór 3", "plays": 30},
        {"name": "Utwór 4", "plays": 24},
        {"name": "Utwór 5", "plays": 18},
    ]
    assert era["first_play_at"] == "2023-12-31T23:30:00Z"
    assert era["artist_first"] == {"track_name": "Utwór 7", "played_at": "2023-06-01T12:00:00Z"}


def load_weekly_history(db: Engine, rows: list[tuple[int, int, int]]) -> None:
    """Osoba z historią (numer tygodnia od START, artysta, odsłuchy); każdy artysta ma
    jeden utwór o tym samym id, a odsłuchy są w środę tygodnia co sekundę."""
    add_user(db, synced=True)
    artists = sorted({artist for _, artist, _ in rows})
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO artist (id, name, name_key) "
                "SELECT a, 'Artysta ' || a, 'artysta ' || a FROM unnest(CAST(:ids AS bigint[])) a"
            ),
            {"ids": artists},
        )
        conn.execute(
            text(
                "INSERT INTO track (id, artist_id, name, name_key) "
                "SELECT a, a, 'Utwór ' || a, 'utwór ' || a FROM unnest(CAST(:ids AS bigint[])) a"
            ),
            {"ids": artists},
        )
        conn.execute(
            text(
                "INSERT INTO scrobble (user_id, played_at, track_id, artist_id) "
                "SELECT 1, timestamptz '2024-01-03 12:00+00' + :week * interval '7 days' "
                "       + (:artist * 60 + n) * interval '1 second', :artist, :artist "
                "FROM generate_series(1, :plays) AS n"
            ),
            [{"week": w, "artist": a, "plays": n} for w, a, n in rows],
        )
    with Session(db) as session:
        recompute_user_stats(session, 1)
        session.commit()


def assert_same_as_full_history(body: dict, rows: list[tuple[int, int, int]]) -> Detection:
    """Endpoint czyta z bazy tylko część wierszy tygodniowych; jego wynik ma być taki sam
    jak wykrywanie er na pełnej historii."""
    full: dict[date, dict[int, int]] = {}
    for w, artist, plays in rows:
        full.setdefault(week(w), {})[artist] = plays
    expected = detect_eras(full, START, date.fromisoformat(body["end"]) - timedelta(days=6))

    def periods(blocks: list[Block]) -> list[tuple[str, int, int]]:
        return [(str(expected.weeks[b.first]), b.artist_id, b.plays) for b in blocks]

    def got(eras: list[dict]) -> list[tuple[str, int, int]]:
        return [(e["start"], e["artist_id"], e["plays"]) for e in eras]

    assert got(body["eras"]) == periods(expected.eras)
    assert got(body["episodes"]) == periods(expected.episodes)
    assert body["threshold"] == round(expected.threshold, 3)
    streamed = stream_artists(expected)
    assert [a["id"] for a in body["streams"]["artists"]] == streamed
    assert body["streams"]["shares"] == stream_shares(expected, streamed)
    return expected


def test_pruned_rows_give_the_same_result_as_full_history(
    client: TestClient, clean_db: Engine
) -> None:
    """Dane losowe, ale powtarzalne: szeroki gust, kilka obsesji i cicha przerwa."""
    rng = random.Random(7)
    # (artysta, tygodnie obsesji); artysta 3 wraca po przerwie
    obsessions = [(3, range(6, 14)), (11, range(32, 40)), (7, range(40, 44)), (3, range(44, 50))]
    rows = []
    for w in range(52):
        if 25 <= w < 30:
            continue  # cisza
        obsessed = {a for a, weeks in obsessions if w in weeks}
        for artist in sorted(set(rng.sample(range(1, 61), 25)) | obsessed):
            plays = rng.randint(1, 6) + (rng.randint(15, 40) if artist in obsessed else 0)
            rows.append((w, artist, plays))
    load_weekly_history(clean_db, rows)

    body = client.get("/api/users/przykladowa/eras").json()

    expected = assert_same_as_full_history(body, rows)
    # dane naprawdę mają ery i epizody, więc porównanie coś znaczy
    assert len(expected.eras) >= 2 and expected.episodes


def test_small_shares_next_to_a_peak_still_count(client: TestClient, clean_db: Engine) -> None:
    """Artysta R ma jeden mocny tydzień otoczony tygodniami z 2% udziału. Te słabe
    tygodnie same nie przekraczają 5%, ale wchodzą do średnich sąsiadów i robią z R erę;
    przycinanie nie może ich zgubić."""
    r = 1201  # id zaraz po tle: odsłuchy są przesunięte o id minut, więc musi być małe
    rows = []
    for w in range(30):
        # tło: co tydzień 40 nowych artystów po jednym odsłuchu, nikt nie dominuje
        rows += [(w, w * 40 + k + 1, 1) for k in range(40)]
    rows += [(10, r, 1), (11, r, 1), (12, r, 30), (13, r, 1), (14, r, 1)]
    load_weekly_history(clean_db, rows)

    body = client.get("/api/users/przykladowa/eras").json()

    expected = assert_same_as_full_history(body, rows)
    assert [(b.artist_id, b.first, b.last) for b in expected.eras] == [(r, 10, 14)]


def test_eras_for_unknown_user(client: TestClient) -> None:
    assert client.get("/api/users/nikt/eras").json() == {"detail": "not_imported"}


def test_eras_during_first_import(client: TestClient, clean_db: Engine) -> None:
    add_user(clean_db, synced=False, job_status="running")

    response = client.get("/api/users/przykladowa/eras")

    assert (response.status_code, response.json()) == (409, {"detail": "import_in_progress"})
