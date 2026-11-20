"""Odkrywanie: rekomendacje nowych artystów z podobnych do tego, czego osoba słucha teraz.

1. Ziarna: 30 najczęściej słuchanych artystów z 90 dni przed ostatnim scrobblem.
2. Kandydat dostaje za każde ziarno match(ziarno, kandydat) × ln(1 + odsłuchy ziarna).
3. Odpadają artyści, których osoba zna (co najmniej `known_plays` odsłuchów).
4. Uzasadnienie „bo słuchasz…” to ziarna o największym wkładzie.
5. Z puli najlepszych kandydatów suwak popularności i filtr tagu wybierają, co pokazać.

Podobnych pobiera worker (similar.py). Gdy części ziaren jeszcze brakuje, wynik liczymy
z tego, co jest, i podajemy, ile ziaren czeka.
"""

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .similar import DEFAULT_SEEDS, SeedParams, seeds_of
from .stats import find_imported_user
from .tags import is_genre_tag, tag_key


class Popularity(StrEnum):
    """Pozycje suwaka „mainstream–nisza”: pasma liczby słuchaczy na Last.fm."""

    KNOWN = "known"  # ponad 1 mln
    ALL = "all"
    LESSER = "lesser"  # do 300 tys.
    NICHE = "niche"  # do 100 tys.


# (od, do) słuchaczy dla każdego pasma; None to brak granicy
BANDS: dict[Popularity, tuple[int | None, int | None]] = {
    Popularity.KNOWN: (1_000_001, None),
    Popularity.ALL: (None, None),
    Popularity.LESSER: (None, 300_000),
    Popularity.NICHE: (None, 100_000),
}


@dataclass(frozen=True)
class RecommendParams:
    top: int = 20
    because: int = 3  # ile ziaren w uzasadnieniu
    known_plays: int = 3  # od tylu odsłuchów artysta jest „znany”
    # Z tylu najlepszych kandydatów wybierają suwak i filtr tagu: dalsi mają śladowy wynik
    pool: int = 200
    tag_min_weight: int = 25  # waga tagu (na 100), od której kandydat „ma” tag
    tags_shown: int = 12


DEFAULT_PARAMS = RecommendParams()


class Recommendation(BaseModel):
    artist_id: int
    name: str
    listeners: int | None
    score: float
    because: list[str]  # ziarna o największym wkładzie, od największego


class Recommendations(BaseModel):
    username: str
    popularity: Popularity
    tag: str | None
    seeds: int  # ile ziaren mamy
    pending: int  # ile ziaren nie ma jeszcze pobranych podobnych
    # Kandydaci z puli bez danych z Last.fm (słuchacze, tagi): widać ich tylko bez filtrów
    missing_data: int
    tags: list[str]  # najczęstsze tagi gatunkowe w puli, do filtra
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
    SELECT c.similar_id AS artist_id, a.name, a.listeners, a.enriched_at IS NOT NULL AS enriched,
           sum(c.contribution) AS score,
           (array_agg(c.seed_name ORDER BY c.contribution DESC, c.seed_name))
               [1:CAST(:because AS int)] AS because
    FROM contributions c
    JOIN artist a ON a.id = c.similar_id
    GROUP BY c.similar_id, a.name, a.listeners, a.enriched_at
    ORDER BY score DESC, c.similar_id
    LIMIT :pool
""")

_TAGS = text("""
    SELECT at.artist_id, t.name
    FROM artist_tag at
    JOIN tag t ON t.id = at.tag_id
    WHERE at.artist_id = ANY(:ids) AND at.weight >= :min_weight
""")


def in_band(listeners: int | None, popularity: Popularity) -> bool:
    low, high = BANDS[popularity]
    if low is None and high is None:
        return True
    # Bez liczby słuchaczy nie wiemy, czy artysta jest niszowy: nie zgadujemy
    if listeners is None:
        return False
    return (low is None or listeners >= low) and (high is None or listeners <= high)


def recommendations(
    db: Engine,
    username: str,
    popularity: Popularity = Popularity.ALL,
    tag: str | None = None,
    params: RecommendParams = DEFAULT_PARAMS,
    seed_params: SeedParams = DEFAULT_SEEDS,
) -> Recommendations:
    """Rekomendacje osoby o znormalizowanym nicku (wyjątki jak w stats.find_imported_user).
    `tag` to nazwa małymi literami; kandydat musi mieć go z wagą co najmniej tag_min_weight."""
    with Session(db) as session:
        user = find_imported_user(session, username)
        seeds = seeds_of(session, user.id, seed_params)
        ready = [seed for seed in seeds if seed.fetched]
        pool = []
        if ready:
            pool = session.execute(
                _SCORES,
                {
                    "user_id": user.id,
                    "seed_ids": [seed.artist_id for seed in ready],
                    "seed_plays": [seed.plays for seed in ready],
                    "known_plays": params.known_plays,
                    "because": params.because,
                    "pool": params.pool,
                },
            ).all()

        # Tagi po kluczu (k-pop = kpop); do wyświetlenia najczęstsza pisownia w puli
        tags_of: dict[int, set[str]] = {row.artist_id: set() for row in pool}
        spellings: dict[str, Counter[str]] = {}
        if pool:
            for artist_id, name in session.execute(
                _TAGS, {"ids": list(tags_of), "min_weight": params.tag_min_weight}
            ):
                if is_genre_tag(name):
                    key = tag_key(name)
                    tags_of[artist_id].add(key)
                    spellings.setdefault(key, Counter())[name] += 1

        counts = Counter(key for keys in tags_of.values() for key in keys)
        wanted = tag_key(tag) if tag else None
        items = [
            Recommendation(
                artist_id=row.artist_id,
                name=row.name,
                listeners=row.listeners,
                score=round(row.score, 4),
                because=row.because,
            )
            for row in pool
            if in_band(row.listeners, popularity)
            and (wanted is None or wanted in tags_of[row.artist_id])
        ][: params.top]

        return Recommendations(
            username=user.username,
            popularity=popularity,
            tag=tag,
            seeds=len(seeds),
            pending=len(seeds) - len(ready),
            missing_data=sum(1 for row in pool if not row.enriched),
            # Remisy alfabetycznie, żeby lista nie skakała między odświeżeniami
            tags=[
                spellings[key].most_common(1)[0][0]
                for key, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            ][: params.tags_shown],
            items=items,
        )
