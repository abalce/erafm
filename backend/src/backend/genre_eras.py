"""Ery gatunkowe: okresy, w których jeden gatunek (tag Last.fm) wyraźnie przeważał.

Algorytm jest ten sam co dla artystów (eras.detect_eras); zmienia się tylko to, kto
„słucha” się w tygodniu:

1. Każdy odsłuch artysty rozkłada się na jego tagi gatunkowe proporcjonalnie do wag tagów
   (razem 1), więc artysta z dziesięcioma tagami nie liczy się dziesięć razy.
2. Waga tagu jest mnożona przez jego rzadkość w historii osoby: ln(liczba artystów osoby
   z tagami / liczba tych z danym tagiem). Bez tego „pop” i „rock” wygrywają prawie każdy
   tydzień, a era „rock przez trzy lata” nic nie mówi.
3. Tagi różniące się tylko pisownią (k-pop, kpop) to jeden tag (tags.tag_key).

Udziały tagów w tygodniach liczy baza; do Pythona trafiają tylko tygodnie, które mogą
wpłynąć na wynik (to samo przycinanie co w erach artystów).
"""

from collections import defaultdict
from datetime import date, timedelta

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .config import settings
from .eras import (
    _WEEK_TOTALS,
    DEFAULT_PARAMS,
    EraParams,
    Silence,
    _current_week,
    detect_eras,
    prune_reach_weeks,
)
from .stats import find_imported_user
from .tags import DECADE_PATTERN, IGNORED_TAGS, TAG_KEY_STRIP

TOP_ARTISTS = 3


class GenreArtist(BaseModel):
    artist_id: int
    name: str
    plays: int  # odsłuchy artysty w tym okresie, przypisane do tego gatunku


class GenreEra(BaseModel):
    tag: str  # najczęstsza pisownia wśród artystów osoby
    start: date  # poniedziałek pierwszego tygodnia
    end: date  # niedziela ostatniego tygodnia
    plays: int  # odsłuchy przypisane gatunkowi w okresie (po ważeniu, zaokrąglone)
    share: float  # ich udział we wszystkich odsłuchach okresu
    top_artists: list[GenreArtist]  # kto ten gatunek w tym okresie „robił”


class UserGenreEras(BaseModel):
    username: str
    start: date | None
    end: date | None
    threshold: float
    eras: list[GenreEra]
    episodes: list[GenreEra]
    silences: list[Silence]


# Rozkład odsłuchu artysty na tagi: wagi tagów (scalonych po kluczu, wyższa waga wygrywa)
# razy rzadkość tagu w historii osoby, znormalizowane do 1 dla każdego artysty
_DIST = """
    user_artists AS (SELECT artist_id FROM first_listen WHERE user_id = :user_id),
    raw AS (
        SELECT at.artist_id, t.name,
               regexp_replace(lower(t.name), :key_strip, '', 'g') AS tag_key, at.weight
        FROM artist_tag at
        JOIN tag t ON t.id = at.tag_id
        JOIN user_artists ua ON ua.artist_id = at.artist_id
        WHERE at.weight > 0
          AND NOT (lower(t.name) = ANY(:ignored))
          AND lower(t.name) !~ :decade
    ),
    merged AS (SELECT artist_id, tag_key, max(weight) AS weight FROM raw GROUP BY 1, 2),
    tagged AS (SELECT count(DISTINCT artist_id) AS artists FROM merged),
    df AS (SELECT tag_key, count(*) AS artists FROM merged GROUP BY 1),
    weighted AS (
        SELECT m.artist_id, m.tag_key,
               m.weight * ln(tagged.artists::float8 / df.artists) AS w
        FROM merged m
        JOIN df USING (tag_key)
        CROSS JOIN tagged
    ),
    dist AS (
        SELECT artist_id, tag_key, w / sum(w) OVER (PARTITION BY artist_id) AS f
        FROM weighted
        WHERE w > 0
    )
"""

_TAG_WEEKS = f"""
    WITH {_DIST},
    tag_weeks AS (
        SELECT ws.week, d.tag_key, sum(ws.plays * d.f) AS plays
        FROM weekly_artist_stats ws
        JOIN dist d ON d.artist_id = ws.artist_id
        WHERE ws.user_id = :user_id
        GROUP BY 1, 2
    )
"""

# Tylko tygodnie tagów, które w promieniu :reach miały choć raz udział ≥ :min_share
_CANDIDATES = text(
    _TAG_WEEKS
    + """,
    totals AS (
        SELECT week, sum(plays) AS total FROM weekly_artist_stats
        WHERE user_id = :user_id GROUP BY week
    ),
    shares AS (
        SELECT tw.week, tw.tag_key, tw.plays,
               max(tw.plays / t.total) OVER (
                   PARTITION BY tw.tag_key
                   ORDER BY tw.week
                   RANGE BETWEEN CAST(:reach AS interval) PRECEDING
                             AND CAST(:reach AS interval) FOLLOWING
               ) AS nearby_share
        FROM tag_weeks tw
        JOIN totals t USING (week)
    )
    SELECT week, tag_key, plays FROM shares WHERE nearby_share >= :min_share
"""
)

_FULL_WEEKS = text(
    _TAG_WEEKS + "SELECT week, tag_key, plays FROM tag_weeks WHERE tag_key = ANY(:keys)"
)

# Najczęstsza pisownia każdego klucza wśród artystów osoby
_NAMES = text(
    "WITH "
    + _DIST.strip()
    + """
    SELECT DISTINCT ON (tag_key) tag_key, name
    FROM (SELECT tag_key, name, count(*) AS n FROM raw GROUP BY 1, 2) spellings
    WHERE tag_key = ANY(:keys)
    ORDER BY tag_key, n DESC, name
"""
)

# Artyści, którzy w każdym okresie najbardziej „robili” jego gatunek
_PERIOD_ARTISTS = text(
    f"""
    WITH {_DIST},
    periods AS (
        SELECT * FROM unnest(
            CAST(:idx AS int[]), CAST(:keys AS text[]),
            CAST(:starts AS date[]), CAST(:ends AS date[])
        ) AS p (idx, tag_key, start_week, end_week)
    ),
    contributions AS (
        SELECT p.idx, ws.artist_id, sum(ws.plays * d.f) AS plays
        FROM periods p
        JOIN dist d ON d.tag_key = p.tag_key
        JOIN weekly_artist_stats ws
          ON ws.user_id = :user_id AND ws.artist_id = d.artist_id
         AND ws.week BETWEEN p.start_week AND p.end_week
        GROUP BY 1, 2
    ),
    ranked AS (
        SELECT *, row_number() OVER (PARTITION BY idx ORDER BY plays DESC, artist_id) AS rank
        FROM contributions
    )
    SELECT r.idx, r.artist_id, a.name, r.plays
    FROM ranked r JOIN artist a ON a.id = r.artist_id
    WHERE r.rank <= :top
    ORDER BY r.idx, r.rank
"""
)


def _common(user_id: int) -> dict:
    return {
        "user_id": user_id,
        "key_strip": TAG_KEY_STRIP,
        "ignored": sorted(IGNORED_TAGS),
        "decade": DECADE_PATTERN,
    }


def user_genre_eras(db: Engine, username: str, params: EraParams = DEFAULT_PARAMS) -> UserGenreEras:
    """Ery gatunkowe osoby o znormalizowanym nicku (wyjątki jak w stats.find_imported_user)."""
    timezone = settings.app_timezone
    with Session(db) as session:
        user = find_imported_user(session, username)
        common = _common(user.id)
        totals = {r.week: r.total for r in session.execute(_WEEK_TOTALS, {"user_id": user.id})}
        if not totals:
            return UserGenreEras(
                username=user.username,
                start=None,
                end=None,
                threshold=params.min_share,
                eras=[],
                episodes=[],
                silences=[],
            )

        # detect_eras rozpoznaje „artystów” po liczbach: klucze tagów dostają numery
        ids: dict[str, int] = {}
        plays_by_week: dict[date, dict[int, float]] = defaultdict(dict)
        candidates = session.execute(
            _CANDIDATES,
            {
                **common,
                "reach": f"{prune_reach_weeks(params)} weeks",
                "min_share": params.min_share,
            },
        )
        # Numery według klucza, żeby remisy rozstrzygały się zawsze tak samo
        rows = sorted(candidates, key=lambda r: r.tag_key)
        for row in rows:
            tag_id = ids.setdefault(row.tag_key, len(ids) + 1)
            plays_by_week[row.week][tag_id] = row.plays

        start = min(totals)
        end = max(max(totals), _current_week(timezone))
        found = detect_eras(plays_by_week, start, end, params, totals)

        blocks = [*found.eras, *found.episodes]
        keys = {tag_id: key for key, tag_id in ids.items()}
        wanted = sorted({keys[b.artist_id] for b in blocks})
        if wanted:
            found.table.add_rows(
                [
                    (r.week, ids[r.tag_key], r.plays)
                    for r in session.execute(_FULL_WEEKS, {**common, "keys": wanted})
                    if r.week in found.table.index
                ]
            )
            found.count_plays()
        names = dict(session.execute(_NAMES, {**common, "keys": wanted}).all()) if wanted else {}

        artists: dict[int, list[GenreArtist]] = defaultdict(list)
        if blocks:
            for row in session.execute(
                _PERIOD_ARTISTS,
                {
                    **common,
                    "idx": list(range(len(blocks))),
                    "keys": [keys[b.artist_id] for b in blocks],
                    "starts": [found.weeks[b.first] for b in blocks],
                    "ends": [found.weeks[b.last] for b in blocks],
                    "top": TOP_ARTISTS,
                },
            ):
                artists[row.idx].append(
                    GenreArtist(artist_id=row.artist_id, name=row.name, plays=round(row.plays))
                )

        def era(idx: int) -> GenreEra:
            block = blocks[idx]
            key = keys[block.artist_id]
            return GenreEra(
                tag=names.get(key, key),
                start=found.weeks[block.first],
                end=found.weeks[block.last] + timedelta(days=6),
                plays=round(block.plays),
                share=round(block.share, 3),
                top_artists=artists[idx],
            )

        n_eras = len(found.eras)
        return UserGenreEras(
            username=user.username,
            start=start,
            end=end + timedelta(days=6),
            threshold=round(found.threshold, 3),
            eras=[era(i) for i in range(n_eras)],
            episodes=[era(i) for i in range(n_eras, len(blocks))],
            silences=[
                Silence(start=found.weeks[a], end=found.weeks[b] + timedelta(days=6))
                for a, b in found.silences
            ],
        )
