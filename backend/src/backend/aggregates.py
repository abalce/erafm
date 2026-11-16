"""Tabele wyliczane ze scrobbli: weekly_artist_stats i first_listen.

Po każdym imporcie osoby kasujemy jej wiersze i liczymy wszystko od zera. To ułamek
sekundy nawet dla 150 tys. odsłuchów, a tabele nie mogą się rozjechać z historią.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import settings

# date_trunc('week', ...) daje poniedziałek; AT TIME ZONE przelicza czas z UTC na strefę
# aplikacji, zanim obetniemy go do początku tygodnia
_WEEKLY_ARTIST_STATS = text("""
    INSERT INTO weekly_artist_stats (user_id, week, artist_id, plays)
    SELECT user_id,
           date_trunc('week', played_at AT TIME ZONE :timezone)::date AS week,
           artist_id,
           count(*)
    FROM scrobble
    WHERE user_id = :user_id
    GROUP BY user_id, week, artist_id
""")

_FIRST_LISTEN = text("""
    INSERT INTO first_listen (user_id, artist_id, first_at, last_at, total_plays)
    SELECT user_id, artist_id, min(played_at), max(played_at), count(*)
    FROM scrobble
    WHERE user_id = :user_id
    GROUP BY user_id, artist_id
""")


def recompute_user_stats(session: Session, user_id: int, timezone: str | None = None) -> None:
    """Przelicza od zera obie tabele dla jednej osoby. Nie zatwierdza transakcji:
    robi to wywołujący, razem ze swoimi zmianami."""
    params = {"user_id": user_id, "timezone": timezone or settings.app_timezone}
    session.execute(text("DELETE FROM weekly_artist_stats WHERE user_id = :user_id"), params)
    session.execute(text("DELETE FROM first_listen WHERE user_id = :user_id"), params)
    session.execute(_WEEKLY_ARTIST_STATS, params)
    session.execute(_FIRST_LISTEN, params)
