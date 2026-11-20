"""Testy podpowiedzi znajomych do duetu, z udawanym Last.fm (respx)."""

from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from backend.friends import clear_cache
from backend.main import app, get_db


@pytest.fixture
def client(clean_db: Engine) -> Iterator[TestClient]:
    clear_cache()
    app.dependency_overrides[get_db] = lambda: clean_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    clear_cache()


def friend(name: str, image: str = "") -> dict:
    return {
        "name": name,
        "realname": "Ktoś Prawdziwy",
        "image": [{"size": "small", "#text": ""}, {"size": "extralarge", "#text": image}],
    }


def lastfm(mock: respx.MockRouter) -> respx.Route:
    return mock.get(host="ws.audioscrobbler.com", path="/2.0/")


def friends_json(*users: dict) -> dict:
    return {"friends": {"user": list(users), "@attr": {"user": "ala", "total": str(len(users))}}}


def add_known_user(db: Engine, username: str) -> None:
    with db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO lfm_user (username, display_name, last_synced_at) "
                "VALUES (:u, :u, now())"
            ),
            {"u": username},
        )


def test_friends_known_first_then_alphabetical(client: TestClient, clean_db: Engine) -> None:
    add_known_user(clean_db, "zosia")
    with respx.mock as mock:
        route = lastfm(mock).respond(
            json=friends_json(friend("Zosia", "https://img/z.png"), friend("Basia"), friend("Cela"))
        )
        response = client.get("/api/users/Ala/friends")

    assert response.status_code == 200
    assert response.json() == [
        {
            "username": "zosia",
            "display_name": "Zosia",
            "image_url": "https://img/z.png",
            "in_erafm": True,
        },
        {"username": "basia", "display_name": "Basia", "image_url": None, "in_erafm": False},
        {"username": "cela", "display_name": "Cela", "image_url": None, "in_erafm": False},
    ]
    params = route.calls.last.request.url.params
    assert (params["method"], params["user"], params["limit"]) == ("user.getfriends", "ala", "50")


def test_single_friend_comes_as_an_object(client: TestClient) -> None:
    with respx.mock as mock:
        lastfm(mock).respond(json={"friends": {"user": friend("Kasia"), "@attr": {}}})
        body = client.get("/api/users/ala/friends").json()

    assert [f["username"] for f in body] == ["kasia"]


def test_no_friends(client: TestClient) -> None:
    with respx.mock as mock:
        lastfm(mock).respond(json=friends_json())
        assert client.get("/api/users/ala/friends").json() == []


def test_friends_are_cached(client: TestClient, clean_db: Engine) -> None:
    with respx.mock as mock:
        route = lastfm(mock).respond(json=friends_json(friend("Kasia")))
        first = client.get("/api/users/ala/friends").json()
        # Kasia trafia do Erafm: lista z pamięci, ale znacznik liczymy na bieżąco
        add_known_user(clean_db, "kasia")
        second = client.get("/api/users/ala/friends").json()

    assert route.call_count == 1
    assert (first[0]["in_erafm"], second[0]["in_erafm"]) == (False, True)


def test_unknown_user(client: TestClient) -> None:
    with respx.mock as mock:
        lastfm(mock).respond(400, json={"error": 6, "message": "User not found"})
        response = client.get("/api/users/nikt/friends")

    assert (response.status_code, response.json()) == (404, {"detail": "user_not_found"})


def test_lastfm_down(client: TestClient) -> None:
    with respx.mock as mock:
        lastfm(mock).mock(side_effect=httpx.ConnectError("brak sieci"))
        response = client.get("/api/users/ala/friends")

    assert (response.status_code, response.json()) == (502, {"detail": "lastfm_error"})


def test_invalid_nick(client: TestClient) -> None:
    response = client.get("/api/users/a b/friends")

    assert (response.status_code, response.json()) == (422, {"detail": "invalid_username"})
