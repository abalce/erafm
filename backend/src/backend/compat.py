"""Kompatybilność dwóch osób: jak bardzo pokrywają się ich gusta, w całości i rok po roku.

Porównujemy tylko wspólny okres: od tygodnia, w którym zaczęła scrobblować osoba, która
dołączyła później. Inaczej starsze lata jednej osoby nie miałyby z czym się porównać.

Zgodność to podobieństwo cosinusowe wektorów ln(1 + odsłuchy) po artystach: logarytm
tłumi jednego „katowanego” artystę. Rok tygodnia to rok jego poniedziałku (dane
z `weekly_artist_stats`; różnica względem dokładnych dni to ułamek punktu procentowego).
Wszystko liczy baza: zwraca kilka liczb zamiast dziesiątek tysięcy wierszy.
"""

from dataclasses import dataclass
from datetime import date

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .stats import find_imported_user


@dataclass(frozen=True)
class CompatParams:
    top_shared: int = 10
    top_disputed: int = 3
    # Sporny: najwięcej odsłuchów u jednej osoby przy najwyżej tylu u drugiej
    max_other_plays: int = 2
    # Rok pokazujemy, gdy obie osoby mają w nim tyle odsłuchów; przy mniejszych to szum
    min_year_plays: int = 100


DEFAULT_PARAMS = CompatParams()


class YearCompat(BaseModel):
    year: int
    similarity: float  # 0-1
    shared_artists: int
    plays_first: int
    plays_second: int


class SharedArtist(BaseModel):
    artist_id: int
    name: str
    plays_first: int
    plays_second: int


class DisputedArtist(BaseModel):
    artist_id: int
    name: str
    plays: int  # u osoby, która go słucha
    other_plays: int  # u drugiej osoby: najwyżej max_other_plays


class Compatibility(BaseModel):
    first: str
    second: str
    since: date | None  # początek wspólnego okresu (poniedziałek); None bez odsłuchów
    similarity: float  # 0-1, cały wspólny okres
    shared_artists: int
    years: list[YearCompat]
    # Od najbardziej łączącego: według mniejszego z dwóch udziałów artysty
    connecting: list[SharedArtist]
    disputed_first: list[DisputedArtist]  # słucha pierwsza osoba, druga prawie wcale
    disputed_second: list[DisputedArtist]


_SINCE = text("""
    SELECT CASE WHEN count(*) = 2 THEN max(started) END
    FROM (
        SELECT min(week) AS started
        FROM weekly_artist_stats
        WHERE user_id IN (:first, :second)
        GROUP BY user_id
    ) starts
""")

# GROUPING SETS liczy naraz każdy rok i cały okres (rok 0). FULL JOIN łączy artystów
# obu osób; iloczyn jest tylko dla wspólnych, a normy biorą wszystkich artystów osoby.
_SIMILARITY = text("""
    WITH plays AS (
        SELECT user_id, coalesce(extract(year FROM week)::int, 0) AS year, artist_id,
               sum(plays) AS plays
        FROM weekly_artist_stats
        WHERE user_id IN (:first, :second) AND week >= :since
        GROUP BY GROUPING SETS ((user_id, extract(year FROM week)::int, artist_id),
                                (user_id, artist_id))
    ),
    a AS (SELECT year, artist_id, plays, ln(1 + plays) AS v FROM plays WHERE user_id = :first),
    b AS (SELECT year, artist_id, plays, ln(1 + plays) AS v FROM plays WHERE user_id = :second)
    SELECT year,
           coalesce(sum(a.v * b.v) / nullif(sqrt(sum(a.v ^ 2)) * sqrt(sum(b.v ^ 2)), 0), 0)
               AS similarity,
           count(a.v * b.v) AS shared_artists,
           coalesce(sum(a.plays), 0) AS plays_first,
           coalesce(sum(b.plays), 0) AS plays_second
    FROM a FULL JOIN b USING (year, artist_id)
    GROUP BY year
    ORDER BY year
""")

# Trzy rankingi z jednej tabeli odsłuchów: łączący (wspólni, według mniejszego z udziałów)
# i sporni każdej osoby. Każde okno najpierw układa artystów spełniających warunek, więc
# ich numery idą od 1. Remisy rozstrzyga id artysty, żeby wynik był powtarzalny.
_ARTISTS = text("""
    WITH plays AS (
        SELECT artist_id,
               coalesce(sum(plays) FILTER (WHERE user_id = :first), 0) AS pa,
               coalesce(sum(plays) FILTER (WHERE user_id = :second), 0) AS pb
        FROM weekly_artist_stats
        WHERE user_id IN (:first, :second) AND week >= :since
        GROUP BY artist_id
    ),
    totals AS (SELECT sum(pa) AS ta, sum(pb) AS tb FROM plays),
    ranked AS (
        SELECT artist_id, pa, pb,
               CASE WHEN pa > 0 AND pb > 0 THEN row_number() OVER (
                   ORDER BY (pa > 0 AND pb > 0) DESC,
                            least(pa::float8 / ta, pb::float8 / tb) DESC, artist_id
               ) END AS connecting_rank,
               CASE WHEN pb <= :max_other THEN row_number() OVER (
                   ORDER BY (pb <= :max_other) DESC, pa DESC, artist_id
               ) END AS disputed_first_rank,
               CASE WHEN pa <= :max_other THEN row_number() OVER (
                   ORDER BY (pa <= :max_other) DESC, pb DESC, artist_id
               ) END AS disputed_second_rank
        FROM plays, totals
    )
    SELECT r.artist_id, a.name, r.pa, r.pb,
           r.connecting_rank, r.disputed_first_rank, r.disputed_second_rank
    FROM ranked r
    JOIN artist a ON a.id = r.artist_id
    WHERE r.connecting_rank <= :top_shared
       OR r.disputed_first_rank <= :top_disputed
       OR r.disputed_second_rank <= :top_disputed
""")


def compatibility(
    session: Session,
    first_id: int,
    second_id: int,
    first: str,
    second: str,
    params: CompatParams = DEFAULT_PARAMS,
) -> Compatibility:
    ids = {"first": first_id, "second": second_id}
    since = session.scalar(_SINCE, ids)
    if since is None:
        return Compatibility(
            first=first,
            second=second,
            since=None,
            similarity=0.0,
            shared_artists=0,
            years=[],
            connecting=[],
            disputed_first=[],
            disputed_second=[],
        )

    overall = None
    years = []
    for row in session.execute(_SIMILARITY, {**ids, "since": since}):
        if row.year == 0:
            overall = row
        elif min(row.plays_first, row.plays_second) >= params.min_year_plays:
            years.append(
                YearCompat(
                    year=row.year,
                    similarity=round(row.similarity, 4),
                    shared_artists=row.shared_artists,
                    plays_first=row.plays_first,
                    plays_second=row.plays_second,
                )
            )

    rows = session.execute(
        _ARTISTS,
        {
            **ids,
            "since": since,
            "top_shared": params.top_shared,
            "top_disputed": params.top_disputed,
            "max_other": params.max_other_plays,
        },
    ).all()

    def ranked(rank: str, limit: int) -> list:
        chosen = [r for r in rows if getattr(r, rank) is not None and getattr(r, rank) <= limit]
        return sorted(chosen, key=lambda r: getattr(r, rank))

    return Compatibility(
        first=first,
        second=second,
        since=since,
        similarity=round(overall.similarity, 4) if overall else 0.0,
        shared_artists=overall.shared_artists if overall else 0,
        years=years,
        connecting=[
            SharedArtist(artist_id=r.artist_id, name=r.name, plays_first=r.pa, plays_second=r.pb)
            for r in ranked("connecting_rank", params.top_shared)
        ],
        disputed_first=[
            DisputedArtist(artist_id=r.artist_id, name=r.name, plays=r.pa, other_plays=r.pb)
            for r in ranked("disputed_first_rank", params.top_disputed)
        ],
        disputed_second=[
            DisputedArtist(artist_id=r.artist_id, name=r.name, plays=r.pb, other_plays=r.pa)
            for r in ranked("disputed_second_rank", params.top_disputed)
        ],
    )


def duet_compatibility(
    db: Engine, first: str, second: str, params: CompatParams = DEFAULT_PARAMS
) -> Compatibility:
    """Kompatybilność dwóch osób o znormalizowanych nickach (wyjątki jak
    w stats.find_imported_user)."""
    with Session(db) as session:
        a = find_imported_user(session, first)
        b = find_imported_user(session, second)
        return compatibility(session, a.id, b.id, a.username, b.username, params)
