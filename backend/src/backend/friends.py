"""Podpowiedzi do duetu: znajomi osoby z Last.fm, z zaznaczeniem, kto już jest w Erafm.

Listę znajomych serwer pobiera swoim kluczem API (klucz nigdy nie trafia do przeglądarki)
i pamięta przez godzinę: zmienia się rzadko, a formularz może pytać kilka razy.
"""

import asyncio
import time

from pydantic import BaseModel
from sqlalchemy import Engine, text
from tenacity import wait_fixed

from .config import settings
from .lastfm import Friend, LastfmClient

CACHE_SECONDS = 3600
MAX_FRIENDS = 50

_cache: dict[str, tuple[float, list[Friend]]] = {}


class FriendSuggestion(BaseModel):
    username: str  # nick w postaci z bazy (małe litery), gotowy do adresu duetu
    display_name: str  # pisownia z Last.fm
    image_url: str | None
    in_erafm: bool  # historia jest już w bazie: duet otworzy się bez importu


def clear_cache() -> None:
    _cache.clear()


_IN_ERAFM = text("""
    SELECT username FROM lfm_user
    WHERE username = ANY(:usernames) AND last_synced_at IS NOT NULL
""")


def _known(db: Engine, usernames: list[str]) -> set[str]:
    with db.connect() as conn:
        return set(conn.scalars(_IN_ERAFM, {"usernames": usernames}))


async def _fetch(username: str) -> list[Friend]:
    # Zapytanie ze strony: przy błędzie Last.fm krótkie przerwy zamiast nawet 30 s jak w imporcie
    async with LastfmClient(settings.lastfm_api_key, retry_wait=wait_fixed(0.5)) as lastfm:
        return await lastfm.get_friends(username, MAX_FRIENDS)


async def user_friends(db: Engine, username: str) -> list[FriendSuggestion]:
    """Znajomi osoby o znormalizowanym nicku: najpierw ci, którzy są już w Erafm, potem
    alfabetycznie. Rzuca UserNotFound i inne błędy Last.fm."""
    cached = _cache.get(username)
    if cached and cached[0] > time.monotonic():
        friends = cached[1]
    else:
        friends = await _fetch(username)
        _cache[username] = (time.monotonic() + CACHE_SECONDS, friends)

    names = [friend.username.lower() for friend in friends]
    known = await asyncio.to_thread(_known, db, names) if names else set()
    suggestions = [
        FriendSuggestion(
            username=friend.username.lower(),
            display_name=friend.username,
            image_url=friend.image_url,
            in_erafm=friend.username.lower() in known,
        )
        for friend in friends
    ]
    return sorted(suggestions, key=lambda s: (not s.in_erafm, s.username))
