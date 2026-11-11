"""Ręczne sprawdzenie klienta Last.fm na prawdziwym API.

Użycie (z katalogu backend/): uv run python scripts/lastfm_smoke.py <nick>

Robi kilka zapytań i wypisuje samo podsumowanie; surowych odpowiedzi nigdzie nie zapisuje.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime, timedelta

from backend.config import settings
from backend.lastfm import LastfmClient, RecentTracksPage, UserNotFound

# Nick, którego na Last.fm na pewno nie ma: sprawdzamy na nim błąd 6
MISSING_USER = "erafm-nie-ma-takiego-konta-9f3a7c"
WINDOW = timedelta(days=7)


def describe(label: str, page: RecentTracksPage) -> None:
    scrobbles = page.scrobbles
    print(f"\n{label}")
    print(f"  strona {page.page} z {page.total_pages}, razem w oknie: {page.total}")
    print(f"  scrobbli na stronie: {len(scrobbles)}")
    if not scrobbles:
        return
    newest = max(s.played_at for s in scrobbles)
    oldest = min(s.played_at for s in scrobbles)
    print(f"  zakres dat: {oldest:%Y-%m-%d %H:%M} – {newest:%Y-%m-%d %H:%M} UTC")
    print(f"  bez albumu: {sum(s.album_name is None for s in scrobbles)}")
    print(f"  bez mbid artysty: {sum(s.artist_mbid is None for s in scrobbles)}")
    unnamed = sum(not s.artist_name or not s.track_name for s in scrobbles)
    print(f"  pusta nazwa artysty lub utworu: {unnamed}")


async def run(username: str) -> list[str]:
    problems: list[str] = []
    # Zamrożone okno, tak jak w imporcie: wszystkie strony liczone do tego samego momentu
    now = datetime.now(UTC).replace(microsecond=0)

    async with LastfmClient(settings.lastfm_api_key) as lfm:
        info = await lfm.get_user_info(username)
        print("user.getInfo")
        print(f"  nick: {info.username}")
        print(f"  playcount: {info.playcount}")
        print(f"  rejestracja: {info.registered_at:%Y-%m-%d}")

        first = await lfm.get_recent_tracks(username, page=1, to_ts=now)
        describe("user.getRecentTracks: pierwsza strona (najnowsze)", first)
        if first.total != info.playcount:
            problems.append(
                f"licznik historii ({first.total}) różni się od playcount ({info.playcount})"
            )

        if first.total_pages > 1:
            last = await lfm.get_recent_tracks(username, page=first.total_pages, to_ts=now)
            describe("user.getRecentTracks: ostatnia strona (najstarsze)", last)
            # To nie błąd: scrobbler potrafi dosłać odsłuchy sprzed założenia konta
            if last.scrobbles and min(s.played_at for s in last.scrobbles) < info.registered_at:
                registered = f"{info.registered_at:%Y-%m-%d %H:%M}"
                print(f"  uwaga: są scrobble sprzed rejestracji ({registered} UTC)")

        window_start = now - WINDOW
        window = await lfm.get_recent_tracks(username, from_ts=window_start, to_ts=now)
        describe(f"user.getRecentTracks: okno ostatnich {WINDOW.days} dni", window)
        outside = [s for s in window.scrobbles if not window_start <= s.played_at <= now]
        if outside:
            problems.append(f"{len(outside)} scrobbli poza zadanym oknem from/to")

        print("\nnieistniejący nick")
        try:
            await lfm.get_user_info(MISSING_USER)
        except UserNotFound as e:
            print(f"  UserNotFound, kod {e.code}")
        else:
            problems.append("nieistniejący nick nie zwrócił błędu 6")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Sprawdzenie klienta Last.fm na prawdziwym API")
    parser.add_argument("username", help="nick na Last.fm")
    args = parser.parse_args()

    problems = asyncio.run(run(args.username))
    print()
    if problems:
        print("RÓŻNICE:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("OK: format odpowiedzi zgadza się z klientem")
    return 0


if __name__ == "__main__":
    sys.exit(main())
