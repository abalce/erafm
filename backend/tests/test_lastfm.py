"""Testy klienta Last.fm na przygotowanych odpowiedziach (bez prawdziwych zapytań)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from tenacity import wait_none

from backend.lastfm import (
    MAX_ATTEMPTS,
    HistoryHidden,
    LastfmClient,
    RetryableError,
    UserNotFound,
)

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures" / "lastfm"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def load(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_client() -> LastfmClient:
    # Bez czekania między próbami i bez limitu, żeby testy były szybkie
    return LastfmClient("test-key", max_rate=1000, retry_wait=wait_none())


def lastfm_route(mock: respx.MockRouter) -> respx.Route:
    return mock.get(host="ws.audioscrobbler.com", path="/2.0/")


def error(code: int, message: str, status: int = 400) -> httpx.Response:
    return httpx.Response(status, json={"error": code, "message": message})


async def test_user_info_parsed() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).respond(json=load("user_getinfo.json"))
        async with make_client() as lfm:
            info = await lfm.get_user_info("przykladowa")

    assert info.username == "Przykladowa"
    assert info.playcount == 12345
    assert info.registered_at == datetime(2022, 10, 23, 19, 15, 19, tzinfo=UTC)
    params = route.calls.last.request.url.params
    assert params["method"] == "user.getinfo"
    assert params["user"] == "przykladowa"
    assert params["api_key"] == "test-key"
    assert params["format"] == "json"


async def test_recent_tracks_skips_now_playing_and_parses_fields() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).respond(json=load("user_getrecenttracks.json"))
        async with make_client() as lfm:
            page = await lfm.get_recent_tracks(
                "przykladowa",
                page=1,
                from_ts=datetime(2024, 1, 1, tzinfo=UTC),
                to_ts=datetime(2024, 12, 31, tzinfo=UTC),
            )

    assert (page.page, page.total_pages, page.total) == (1, 6173, 12345)
    # Utwór „grany teraz” nie ma daty, więc nie jest scrobblem
    assert [s.track_name for s in page.scrobbles] == ["Reckoner", "Singiel"]

    reckoner, singiel = page.scrobbles
    assert reckoner.artist_name == "Radiohead"
    assert reckoner.artist_mbid == "a74b1b7f-71a5-4011-9441-d0b5e4122711"
    assert reckoner.album_name == "In Rainbows"
    assert reckoner.played_at == datetime.fromtimestamp(1731340000, UTC)
    # Puste teksty z Last.fm zamieniamy na brak wartości
    assert singiel.artist_mbid is None
    assert singiel.album_name is None

    params = route.calls.last.request.url.params
    assert params["method"] == "user.getrecenttracks"
    assert params["limit"] == "200"
    assert params["from"] == str(int(datetime(2024, 1, 1, tzinfo=UTC).timestamp()))
    assert params["to"] == str(int(datetime(2024, 12, 31, tzinfo=UTC).timestamp()))


async def test_recent_tracks_single_result_as_object() -> None:
    with respx.mock as mock:
        lastfm_route(mock).respond(json=load("user_getrecenttracks_single.json"))
        async with make_client() as lfm:
            page = await lfm.get_recent_tracks("przykladowa")

    assert [s.track_name for s in page.scrobbles] == ["Jedyny Utwór"]


async def test_user_not_found() -> None:
    with respx.mock as mock:
        lastfm_route(mock).mock(return_value=error(6, "User not found", status=404))
        async with make_client() as lfm:
            with pytest.raises(UserNotFound):
                await lfm.get_user_info("nie-ma-takiego")


async def test_history_hidden() -> None:
    with respx.mock as mock:
        lastfm_route(mock).mock(return_value=error(17, "Login: User required to be logged in"))
        async with make_client() as lfm:
            with pytest.raises(HistoryHidden):
                await lfm.get_recent_tracks("ukryta")


async def test_rate_limit_is_retried() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).mock(
            side_effect=[
                error(29, "Rate limit exceeded", status=429),
                httpx.Response(200, json=load("user_getinfo.json")),
            ]
        )
        async with make_client() as lfm:
            info = await lfm.get_user_info("przykladowa")

    assert info.playcount == 12345
    assert route.call_count == 2


async def test_server_error_without_json_is_retried() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).mock(
            side_effect=[
                httpx.Response(503, text="Service Unavailable"),
                httpx.Response(200, json=load("user_getinfo.json")),
            ]
        )
        async with make_client() as lfm:
            await lfm.get_user_info("przykladowa")

    assert route.call_count == 2


async def test_gives_up_after_max_attempts() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).mock(return_value=error(29, "Rate limit exceeded", status=429))
        async with make_client() as lfm:
            with pytest.raises(RetryableError):
                await lfm.get_user_info("przykladowa")

    assert route.call_count == MAX_ATTEMPTS


async def test_user_not_found_is_not_retried() -> None:
    with respx.mock as mock:
        route = lastfm_route(mock).mock(return_value=error(6, "User not found", status=404))
        async with make_client() as lfm:
            with pytest.raises(UserNotFound):
                await lfm.get_user_info("nie-ma-takiego")

    assert route.call_count == 1
