"""Klient API Last.fm: limit zapytań, ponawianie po błędach i typowane odpowiedzi."""

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from pydantic import BaseModel
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential
from tenacity.wait import wait_base

API_URL = "https://ws.audioscrobbler.com/2.0/"
USER_AGENT = "Erafm/0.1 (projekt studencki)"
# Regulamin Last.fm: maks. 5 zapytań na sekundę; zostawiamy zapas
MAX_REQUESTS_PER_SECOND = 4
MAX_ATTEMPTS = 5
MAX_PAGE_SIZE = 200

# httpx na poziomie INFO wypisuje pełny adres każdego zapytania, a w nim klucz API.
# Sekret nie może trafiać do logów, więc zostawiamy tylko ostrzeżenia i błędy.
logging.getLogger("httpx").setLevel(logging.WARNING)


# --- Błędy -----------------------------------------------------------------------------


class LastfmError(Exception):
    """Błąd z Last.fm. `code` to kod z ich dokumentacji albo status HTTP błędu serwera."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Last.fm {code}: {message}")
        self.code = code
        self.message = message


class NotFound(LastfmError):
    """Kod 6: Last.fm nie zna tego, o co pytamy (użytkownika albo artysty)."""


class UserNotFound(NotFound):
    """Kod 6 w zapytaniach o użytkownika: nie ma takiego konta."""


class ArtistNotFound(NotFound):
    """Kod 6 w zapytaniach o artystę: Last.fm nie ma takiego artysty."""


class HistoryHidden(LastfmError):
    """Kod 17: użytkownik ukrył historię odsłuchów w ustawieniach prywatności."""


class RetryableError(LastfmError):
    """Błąd przejściowy: przekroczony limit zapytań albo chwilowy problem serwera."""


# 8: operacja nieudana, 11 i 16: chwilowa niedostępność, 29: przekroczony limit zapytań
_RETRYABLE_CODES = {8, 11, 16, 29}


def _error_from_code(code: int, message: str) -> LastfmError:
    if code == 6:
        return NotFound(code, message)
    if code == 17:
        return HistoryHidden(code, message)
    if code in _RETRYABLE_CODES:
        return RetryableError(code, message)
    return LastfmError(code, message)


def _parse_response(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = None
    # Last.fm zgłasza błędy w treści JSON, często z kodem HTTP 4xx
    if isinstance(data, dict) and "error" in data:
        raise _error_from_code(int(data["error"]), str(data.get("message", "")))
    if response.status_code >= 500:
        raise RetryableError(response.status_code, "błąd serwera Last.fm")
    response.raise_for_status()
    if not isinstance(data, dict):
        raise LastfmError(response.status_code, "odpowiedź nie jest obiektem JSON")
    return data


# --- Odpowiedzi ------------------------------------------------------------------------


def _empty_to_none(value: str | None) -> str | None:
    # Last.fm wysyła pusty tekst zamiast braku wartości
    return value or None


def _from_unix(value: str | int) -> datetime:
    return datetime.fromtimestamp(int(value), UTC)


class UserInfo(BaseModel):
    username: str  # pisownia jak na profilu Last.fm
    playcount: int
    registered_at: datetime

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "UserInfo":
        user = data["user"]
        return cls(
            username=user["name"],
            playcount=int(user["playcount"]),
            registered_at=_from_unix(user["registered"]["unixtime"]),
        )


class Scrobble(BaseModel):
    artist_name: str
    artist_mbid: str | None
    track_name: str
    album_name: str | None
    played_at: datetime

    @classmethod
    def from_api(cls, track: dict[str, Any]) -> "Scrobble":
        return cls(
            artist_name=track["artist"]["#text"],
            artist_mbid=_empty_to_none(track["artist"].get("mbid")),
            track_name=track["name"],
            album_name=_empty_to_none(track.get("album", {}).get("#text")),
            played_at=_from_unix(track["date"]["uts"]),
        )


class ArtistInfo(BaseModel):
    listeners: int
    playcount: int  # odsłuchy artysty na całym Last.fm
    mbid: str | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "ArtistInfo":
        artist = data["artist"]
        return cls(
            listeners=int(artist["stats"]["listeners"]),
            playcount=int(artist["stats"]["playcount"]),
            mbid=_empty_to_none(artist.get("mbid")),
        )


class ArtistTag(BaseModel):
    name: str
    weight: int  # 1-100; 100 ma najczęściej nadawany tag artysty

    @classmethod
    def list_from_api(cls, data: dict[str, Any]) -> list["ArtistTag"]:
        tags = data["toptags"].get("tag", [])
        # Przy jednym wyniku Last.fm zwraca obiekt zamiast listy
        if isinstance(tags, dict):
            tags = [tags]
        return [cls(name=t["name"], weight=int(t["count"])) for t in tags]


class RecentTracksPage(BaseModel):
    page: int
    total_pages: int
    total: int
    scrobbles: list[Scrobble]

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "RecentTracksPage":
        recent = data["recenttracks"]
        attr = recent["@attr"]
        tracks = recent.get("track", [])
        # Przy jednym wyniku Last.fm zwraca obiekt zamiast listy
        if isinstance(tracks, dict):
            tracks = [tracks]
        return cls(
            page=int(attr["page"]),
            total_pages=int(attr["totalPages"]),
            total=int(attr["total"]),
            # Utwór grany właśnie teraz nie ma daty i nie jest jeszcze scrobblem
            scrobbles=[Scrobble.from_api(t) for t in tracks if "date" in t],
        )


# --- Klient ----------------------------------------------------------------------------


class LastfmClient:
    """Asynchroniczny klient Last.fm.

    Użycie: `async with LastfmClient(klucz) as lfm: info = await lfm.get_user_info("nick")`.
    """

    def __init__(
        self,
        api_key: str,
        *,
        http: httpx.AsyncClient | None = None,
        max_rate: float = MAX_REQUESTS_PER_SECOND,
        retry_wait: wait_base | None = None,
    ) -> None:
        self._api_key = api_key
        self._http = http or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=httpx.Timeout(15.0)
        )
        self._limiter = AsyncLimiter(max_rate, time_period=1)
        # Rosnące przerwy między próbami: 1 s, 2 s, 4 s… maks. 30 s
        self._retry_wait = retry_wait or wait_exponential(multiplier=1, min=1, max=30)

    async def __aenter__(self) -> "LastfmClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(
        self, method: str, not_found: type[NotFound], **params: str | int
    ) -> dict[str, Any]:
        """Woła metodę API. `not_found` to wyjątek dla kodu 6, zależny od tego, o co pytamy."""
        query = {"method": method, "api_key": self._api_key, "format": "json", **params}
        try:
            async for attempt in AsyncRetrying(
                retry=retry_if_exception_type((RetryableError, httpx.TransportError)),
                stop=stop_after_attempt(MAX_ATTEMPTS),
                wait=self._retry_wait,
                reraise=True,
            ):
                with attempt:
                    async with self._limiter:
                        response = await self._http.get(API_URL, params=query)
                    return _parse_response(response)
        except NotFound as exc:
            raise not_found(exc.code, exc.message) from None
        raise AssertionError("niemożliwe: AsyncRetrying zawsze zwraca wynik albo rzuca błąd")

    async def get_user_info(self, username: str) -> UserInfo:
        return UserInfo.from_api(await self._call("user.getinfo", UserNotFound, user=username))

    async def get_artist_info(self, name: str) -> ArtistInfo:
        # autocorrect=0: chcemy danych dokładnie tego artysty, którego mamy w scrobblach
        data = await self._call("artist.getinfo", ArtistNotFound, artist=name, autocorrect=0)
        return ArtistInfo.from_api(data)

    async def get_artist_top_tags(self, name: str) -> list[ArtistTag]:
        data = await self._call("artist.gettoptags", ArtistNotFound, artist=name, autocorrect=0)
        return ArtistTag.list_from_api(data)

    async def get_recent_tracks(
        self,
        username: str,
        *,
        page: int = 1,
        limit: int = MAX_PAGE_SIZE,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> RecentTracksPage:
        params: dict[str, str | int] = {"user": username, "page": page, "limit": limit}
        if from_ts is not None:
            params["from"] = int(from_ts.timestamp())
        if to_ts is not None:
            params["to"] = int(to_ts.timestamp())
        data = await self._call("user.getrecenttracks", UserNotFound, **params)
        return RecentTracksPage.from_api(data)
