"""Odkrywanie: rekomendacje nowych artystów z podobnych do tego, czego osoba słucha teraz.

1. Ziarna: 30 najczęściej słuchanych artystów z 90 dni przed ostatnim scrobblem.
2. Kandydat dostaje za każde ziarno match(ziarno, kandydat) × ln(1 + odsłuchy ziarna).
3. Odpadają artyści, których osoba zna (co najmniej `known_plays` odsłuchów).
4. Uzasadnienie „bo słuchasz…” to ziarna o największym wkładzie.

Podobnych pobiera worker (similar.py). Gdy części ziaren jeszcze brakuje, wynik liczymy
z tego, co jest, i podajemy, ile ziaren czeka.
"""

from dataclasses import dataclass

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .similar import DEFAULT_SEEDS, SeedParams, seeds_of
from .stats import find_imported_user


@dataclass(frozen=True)
class RecommendParams:
    top: int = 20
    because: int = 3  # ile ziaren w uzasadnieniu
    known_plays: int = 3  # od tylu odsłuchów artysta jest „znany”


DEFAULT_PARAMS = RecommendParams()


class Recommendation(BaseModel):
    artist_id: int
    name: str
    score: float
    because: list[str]  # ziarna o największym wkładzie, od największego


class Recommendations(BaseModel):
    username: str
    seeds: int  # ile ziaren mamy
    pending: int  # ile ziaren nie ma jeszcze pobranych podobnych
    items: list[Recommendation]


# Wkład każdej pary (ziarno, kandydat); znanych osobie odrzucamy przez first_listen
_SCORES = text("""
    WITH seeds AS (
        SELECT * FROM unnest(CAST(:seed_ids AS bigint[]), CAST(:seed_plays AS int[]))
            AS s (artist_id, plays)
    ),
    contributions AS (
        SELECT sim.similar_id, seed.name AS seed_name,
               sim.match * ln(1 + s.plays) AS contribution
        FROM seeds s
        JOIN artist_similar sim ON sim.artist_id = s.artist_id
        JOIN artist seed ON seed.id = s.artist_id
        LEFT JOIN first_listen f ON f.user_id = :user_id AND f.artist_id = sim.similar_id
        WHERE coalesce(f.total_plays, 0) < :known_plays
    )
    SELECT c.similar_id AS artist_id, a.name, sum(c.contribution) AS score,
           (array_agg(c.seed_name ORDER BY c.contribution DESC, c.seed_name))
               [1:CAST(:because AS int)] AS because
    FROM contributions c
    JOIN artist a ON a.id = c.similar_id
    GROUP BY c.similar_id, a.name
    ORDER BY score DESC, c.similar_id
    LIMIT :top
""")


def recommendations(
    db: Engine,
    username: str,
    params: RecommendParams = DEFAULT_PARAMS,
    seed_params: SeedParams = DEFAULT_SEEDS,
) -> Recommendations:
    """Rekomendacje osoby o znormalizowanym nicku (wyjątki jak w stats.find_imported_user)."""
    with Session(db) as session:
        user = find_imported_user(session, username)
        seeds = seeds_of(session, user.id, seed_params)
        ready = [seed for seed in seeds if seed.fetched]
        items = []
        if ready:
            rows = session.execute(
                _SCORES,
                {
                    "user_id": user.id,
                    "seed_ids": [seed.artist_id for seed in ready],
                    "seed_plays": [seed.plays for seed in ready],
                    "known_plays": params.known_plays,
                    "because": params.because,
                    "top": params.top,
                },
            )
            items = [
                Recommendation(
                    artist_id=row.artist_id,
                    name=row.name,
                    score=round(row.score, 4),
                    because=row.because,
                )
                for row in rows
            ]
        return Recommendations(
            username=user.username,
            seeds=len(seeds),
            pending=len(seeds) - len(ready),
            items=items,
        )
