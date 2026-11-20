"""„Tego dnia X lat temu” i rocznice pierwszego odsłuchu artysty.

Dni liczymy w strefie aplikacji: odsłuch o 23:30 UTC to w Polsce już następny dzień.
"""

from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .config import settings
from .stats import find_imported_user

TOP_ARTISTS = 3
# Rocznice: artyści poznani w tym samym tygodniu kalendarzowym (± tyle dni), którzy
# zostali na dłużej (co najmniej tyle odsłuchów), z pominięciem początku konta: wtedy
# „pierwszy odsłuch” to zwykle artysta znany już wcześniej, a nie odkrycie
ANNIVERSARY_DAYS = 3
ANNIVERSARY_MIN_PLAYS = 20
STARTUP_DAYS = 60
MAX_ANNIVERSARIES = 5


class DayArtist(BaseModel):
    name: str
    plays: int


class DayTrack(BaseModel):
    name: str
    artist_name: str
    plays: int


class YearAgo(BaseModel):
    year: int
    years_ago: int
    day: date  # ten sam dzień kalendarzowy w tamtym roku
    plays: int
    top_artists: list[DayArtist]  # najwyżej TOP_ARTISTS
    top_track: DayTrack


class Anniversary(BaseModel):
    artist_id: int
    artist_name: str
    first_at: datetime
    years_ago: int
    first_track: str
    total_plays: int


class OnThisDay(BaseModel):
    username: str
    date: date
    years: list[YearAgo]  # od najbliższego roku, tylko lata z odsłuchami
    anniversaries: list[Anniversary]


def same_day_in_year(day: date, year: int) -> date:
    """Ten sam dzień kalendarzowy w innym roku; 29 lutego w roku nieprzestępnym to 28."""
    try:
        return day.replace(year=year)
    except ValueError:
        return day.replace(year=year, day=28)


def month_days_around(day: date, radius: int) -> list[str]:
    """Dni kalendarzowe „MM-DD” w promieniu `radius` dni. Gdy okno obejmuje koniec lutego,
    dokłada 29 lutego: rocznica z roku przestępnego też się należy."""
    days = {(day + timedelta(days=d)).strftime("%m-%d") for d in range(-radius, radius + 1)}
    if "02-28" in days:
        days.add("02-29")
    return sorted(days)


def years_between(earlier: date, later: date) -> int:
    """Pełne lata od `earlier` do najbliższej rocznicy w okolicy `later` (w promieniu
    tygodnia rocznica może wypaść tuż przed albo tuż po dniu `later`)."""
    years = later.year - earlier.year
    anniversary = same_day_in_year(earlier, earlier.year + years)
    if (anniversary - later).days > 180:
        years -= 1
    elif (later - anniversary).days > 180:
        years += 1
    return years


def today_in_app_timezone() -> date:
    return datetime.now(ZoneInfo(settings.app_timezone)).date()


_DAY_PLAYS = text("""
    SELECT d.day, a.name AS artist_name, t.name AS track_name, s.track_id
    FROM unnest(CAST(:days AS date[])) AS d (day)
    JOIN scrobble s
      ON s.user_id = :user_id
     AND s.played_at >= d.day::timestamp AT TIME ZONE :timezone
     AND s.played_at < (d.day + 1)::timestamp AT TIME ZONE :timezone
    JOIN track t ON t.id = s.track_id
    JOIN artist a ON a.id = s.artist_id
""")

# Pierwszy utwór dobieramy jak w erach: scrobble z dokładnie tą chwilą co first_at
_ANNIVERSARIES = text("""
    WITH account AS (
        SELECT min(first_at) AS started_at FROM first_listen WHERE user_id = :user_id
    )
    SELECT DISTINCT ON (f.total_plays, f.artist_id)
           f.artist_id, a.name AS artist_name, f.first_at, f.total_plays,
           t.name AS first_track
    FROM first_listen f
    CROSS JOIN account
    JOIN artist a ON a.id = f.artist_id
    JOIN scrobble s
      ON s.user_id = f.user_id AND s.artist_id = f.artist_id AND s.played_at = f.first_at
    JOIN track t ON t.id = s.track_id
    WHERE f.user_id = :user_id
      AND f.total_plays >= :min_plays
      AND f.first_at >= account.started_at + make_interval(days => :startup_days)
      AND (f.first_at AT TIME ZONE :timezone)::date <= :latest
      AND to_char(f.first_at AT TIME ZONE :timezone, 'MM-DD') = ANY(:month_days)
    ORDER BY f.total_plays DESC, f.artist_id, s.track_id
    LIMIT :limit
""")


def on_this_day(db: Engine, username: str, day: date | None = None) -> OnThisDay:
    """„Tego dnia” dla osoby o znormalizowanym nicku (wyjątki jak w
    stats.find_imported_user). Bez `day` bierze dzisiejszą datę w strefie aplikacji."""
    day = day or today_in_app_timezone()
    timezone = settings.app_timezone
    with Session(db) as session:
        user = find_imported_user(session, username)
        first_year = session.scalar(
            text(
                "SELECT extract(year FROM min(first_at) AT TIME ZONE :timezone)::int "
                "FROM first_listen WHERE user_id = :user_id"
            ),
            {"user_id": user.id, "timezone": timezone},
        )
        past_days = (
            [same_day_in_year(day, y) for y in range(day.year - 1, first_year - 1, -1)]
            if first_year is not None
            else []
        )

        plays: dict[date, list] = {d: [] for d in past_days}
        for row in session.execute(
            _DAY_PLAYS, {"user_id": user.id, "timezone": timezone, "days": past_days}
        ):
            plays[row.day].append(row)

        years = []
        for past in past_days:
            rows = plays[past]
            if not rows:
                continue
            artists = Counter(row.artist_name for row in rows)
            tracks = Counter((row.track_id, row.track_name, row.artist_name) for row in rows)
            # remisy rozstrzyga nazwa (artyści) i id utworu, żeby wynik był powtarzalny
            top_artists = sorted(artists.items(), key=lambda item: (-item[1], item[0]))
            (_, track_name, artist_name), track_plays = min(
                tracks.items(), key=lambda item: (-item[1], item[0][0])
            )
            years.append(
                YearAgo(
                    year=past.year,
                    years_ago=day.year - past.year,
                    day=past,
                    plays=len(rows),
                    top_artists=[
                        DayArtist(name=name, plays=n) for name, n in top_artists[:TOP_ARTISTS]
                    ],
                    top_track=DayTrack(name=track_name, artist_name=artist_name, plays=track_plays),
                )
            )

        anniversaries = [
            Anniversary(
                artist_id=row.artist_id,
                artist_name=row.artist_name,
                first_at=row.first_at,
                years_ago=years_between(row.first_at.astimezone(ZoneInfo(timezone)).date(), day),
                first_track=row.first_track,
                total_plays=row.total_plays,
            )
            for row in session.execute(
                _ANNIVERSARIES,
                {
                    "user_id": user.id,
                    "timezone": timezone,
                    "min_plays": ANNIVERSARY_MIN_PLAYS,
                    "startup_days": STARTUP_DAYS,
                    # rocznica co najmniej pierwsza: poznani w ostatnich miesiącach odpadają
                    "latest": same_day_in_year(day, day.year - 1)
                    + timedelta(days=ANNIVERSARY_DAYS),
                    "month_days": month_days_around(day, ANNIVERSARY_DAYS),
                    "limit": MAX_ANNIVERSARIES,
                },
            )
        ]

        return OnThisDay(username=user.username, date=day, years=years, anniversaries=anniversaries)
