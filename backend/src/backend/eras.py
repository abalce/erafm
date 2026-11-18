"""Ery artystów: okresy, w których jeden artysta wyraźnie dominował w słuchaniu.

Algorytm działa na tygodniach z `weekly_artist_stats`:

1. Tydzień jest aktywny, jeśli ma co najmniej `min_week_plays` odsłuchów. Pozostałe
   tygodnie przerywają ery, a ich dłuższe ciągi to „cisza”.
2. Udział artysty w tygodniu to jego odsłuchy / wszystkie odsłuchy tygodnia. Wygładzamy
   go średnią z aktywnych tygodni w oknie `smoothing_weeks` wokół danego tygodnia.
3. Tydzień należy do artysty z największym wygładzonym udziałem, jeśli ten udział
   przekracza próg osoby: kwantyl `share_quantile` najlepszych udziałów ze wszystkich jej
   tygodni, ale nie mniej niż `min_share`. Stały próg nie działa, bo osoby słuchające stu
   artystów tygodniowo prawie nigdy nie mają artysty z 15% udziału.
4. Tygodnie tego samego artysty sklejamy, jeśli dzieli je najwyżej `max_gap_weeks`
   aktywnych tygodni bez właściciela.
5. Blok z co najmniej `min_era_weeks` tygodniami artysty to era, krótszy (od
   `min_episode_weeks`) to epizod, a pojedyncze tygodnie odpadają.

Wszystko liczymy na żądanie: to kilkaset tygodni po ok. sto artystów, więc ułamek sekundy,
a zmiana parametrów nie wymaga przeliczania żadnej tabeli.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .config import settings
from .stats import find_imported_user

WEEK = timedelta(days=7)


@dataclass(frozen=True)
class EraParams:
    min_week_plays: int = 20
    smoothing_weeks: int = 5
    share_quantile: float = 0.70
    min_share: float = 0.05
    max_gap_weeks: int = 3
    min_era_weeks: int = 4
    min_episode_weeks: int = 2
    min_silence_weeks: int = 4


DEFAULT_PARAMS = EraParams()


@dataclass
class Block:
    """Okres jednego artysty; indeksy wskazują tygodnie w `Detection.weeks`."""

    artist_id: int
    first: int
    last: int
    owned_weeks: int  # tygodnie, w których artysta był na szczycie (bez przerw)
    plays: int = 0  # odsłuchy artysty w całym okresie, razem z przerwami
    share: float = 0.0  # te odsłuchy / wszystkie odsłuchy w okresie


@dataclass
class Detection:
    weeks: list[date]  # poniedziałki, ciągła seria bez dziur
    threshold: float
    eras: list[Block]
    episodes: list[Block]
    silences: list[tuple[int, int]]  # (pierwszy, ostatni) indeks tygodnia


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def detect_eras(
    plays_by_week: dict[date, dict[int, int]],
    start: date,
    end: date,
    params: EraParams = DEFAULT_PARAMS,
) -> Detection:
    """Wykrywa ery w tygodniach od `start` do `end` (poniedziałki, włącznie).
    `plays_by_week` to {tydzień: {id artysty: odsłuchy}}; brakujące tygodnie mają zero."""
    weeks = [start + i * WEEK for i in range((end - start) // WEEK + 1)]
    plays = [plays_by_week.get(week, {}) for week in weeks]
    totals = [sum(p.values()) for p in plays]
    active = [total >= params.min_week_plays for total in totals]

    half = params.smoothing_weeks // 2
    windows = [
        [j for j in range(max(0, i - half), min(len(weeks), i + half + 1)) if active[j]]
        for i in range(len(weeks))
    ]

    def smoothed(i: int, artist_id: int) -> float:
        return sum(plays[j].get(artist_id, 0) / totals[j] for j in windows[i]) / len(windows[i])

    # Najlepszy kandydat w każdym aktywnym tygodniu; remis rozstrzyga mniejsze id artysty
    best: dict[int, tuple[int, float]] = {}
    for i in range(len(weeks)):
        if active[i]:
            share, neg_id = max((smoothed(i, a), -a) for a in plays[i])
            best[i] = (-neg_id, share)

    threshold = params.min_share
    if best:
        quantile = _quantile([share for _, share in best.values()], params.share_quantile)
        threshold = max(params.min_share, quantile)

    blocks: list[Block] = []
    current: Block | None = None
    for i, (artist_id, share) in sorted(best.items()):
        if share < threshold:
            continue
        if (
            current is not None
            and current.artist_id == artist_id
            and i - current.last - 1 <= params.max_gap_weeks
            and all(active[current.last + 1 : i])
        ):
            current.last = i
            current.owned_weeks += 1
        else:
            current = Block(artist_id, i, i, 1)
            blocks.append(current)

    for block in blocks:
        span = range(block.first, block.last + 1)
        block.plays = sum(plays[j].get(block.artist_id, 0) for j in span)
        block.share = block.plays / sum(totals[j] for j in span)

    silences: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, is_active in enumerate([*active, True]):  # strażnik domyka ostatni ciąg
        if not is_active and run_start is None:
            run_start = i
        elif is_active and run_start is not None:
            if i - run_start >= params.min_silence_weeks:
                silences.append((run_start, i - 1))
            run_start = None

    return Detection(
        weeks=weeks,
        threshold=threshold,
        eras=[b for b in blocks if b.owned_weeks >= params.min_era_weeks],
        episodes=[
            b for b in blocks if params.min_episode_weeks <= b.owned_weeks < params.min_era_weeks
        ],
        silences=silences,
    )


class EraTrack(BaseModel):
    name: str
    plays: int


class Era(BaseModel):
    artist_id: int
    artist_name: str
    image_url: str | None
    start: date  # poniedziałek pierwszego tygodnia
    end: date  # niedziela ostatniego tygodnia
    plays: int
    share: float
    top_track: EraTrack | None


class Silence(BaseModel):
    start: date
    end: date


class UserEras(BaseModel):
    username: str
    start: date | None  # zakres osi: od tygodnia pierwszego odsłuchu do bieżącego
    end: date | None
    threshold: float
    eras: list[Era]
    episodes: list[Era]
    silences: list[Silence]


_WEEKLY = text("SELECT week, artist_id, plays FROM weekly_artist_stats WHERE user_id = :user_id")

_ARTISTS = text("SELECT id, name, image_url FROM artist WHERE id = ANY(:ids)")

# Granice okresu są datami w strefie aplikacji; AT TIME ZONE zamienia je na chwile w czasie
_TOP_TRACK = text("""
    SELECT t.name, count(*) AS plays
    FROM scrobble s
    JOIN track t ON t.id = s.track_id
    WHERE s.user_id = :user_id
      AND s.artist_id = :artist_id
      AND s.played_at >= (:start)::timestamp AT TIME ZONE :timezone
      AND s.played_at < (:end)::timestamp AT TIME ZONE :timezone
    GROUP BY t.id, t.name
    ORDER BY plays DESC, t.id
    LIMIT 1
""")


def _current_week(timezone: str) -> date:
    today = datetime.now(ZoneInfo(timezone)).date()
    return today - timedelta(days=today.weekday())


def user_eras(db: Engine, username: str, params: EraParams = DEFAULT_PARAMS) -> UserEras:
    """Ery osoby o znormalizowanym nicku (wyjątki jak w stats.find_imported_user)."""
    timezone = settings.app_timezone
    with Session(db) as session:
        user = find_imported_user(session, username)
        plays_by_week: dict[date, dict[int, int]] = defaultdict(dict)
        for week, artist_id, plays in session.execute(_WEEKLY, {"user_id": user.id}):
            plays_by_week[week][artist_id] = plays
        if not plays_by_week:
            return UserEras(
                username=user.username,
                start=None,
                end=None,
                threshold=params.min_share,
                eras=[],
                episodes=[],
                silences=[],
            )

        start = min(plays_by_week)
        end = max(max(plays_by_week), _current_week(timezone))
        found = detect_eras(plays_by_week, start, end, params)

        ids = sorted({b.artist_id for b in [*found.eras, *found.episodes]})
        artists = {row.id: row for row in session.execute(_ARTISTS, {"ids": ids})}

        def era(block: Block) -> Era:
            first, last = found.weeks[block.first], found.weeks[block.last]
            top = session.execute(
                _TOP_TRACK,
                {
                    "user_id": user.id,
                    "artist_id": block.artist_id,
                    "start": first,
                    "end": last + WEEK,
                    "timezone": timezone,
                },
            ).first()
            artist = artists[block.artist_id]
            return Era(
                artist_id=block.artist_id,
                artist_name=artist.name,
                image_url=artist.image_url,
                start=first,
                end=last + timedelta(days=6),
                plays=block.plays,
                share=round(block.share, 3),
                top_track=EraTrack(name=top.name, plays=top.plays) if top else None,
            )

        return UserEras(
            username=user.username,
            start=start,
            end=end + timedelta(days=6),
            threshold=round(found.threshold, 3),
            eras=[era(b) for b in found.eras],
            episodes=[era(b) for b in found.episodes],
            silences=[
                Silence(start=found.weeks[a], end=found.weeks[b] + timedelta(days=6))
                for a, b in found.silences
            ],
        )
