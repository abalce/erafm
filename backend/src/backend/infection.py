"""Zarażenia w duecie: kto komu prawdopodobnie podrzucił jakiego artystę.

Zarażenie A → B artystą X zachodzi, gdy:

1. B jest fanem X: ma co najmniej `fan_plays` odsłuchów,
2. A był „aktywnym nosicielem”: słuchał X co najmniej `carrier_plays` razy w ciągu
   `carrier_window_days` dni tuż przed pierwszym odsłuchem X u B,
3. pierwszy odsłuch X u B wypada po okresie rozruchu B (pierwsze `startup_days` dni
   historii, gdy wszystko wygląda na nowe, bo nie znamy wcześniejszych odsłuchów),
4. historia A obejmuje całe okno z punktu 2: jeśli A nie scrobblował jeszcze w tym czasie,
   nie wiemy, czy słuchał X.

Aktywny nosiciel zamiast „A słuchał kiedykolwiek wcześniej”: przy samym „wcześniej”
osoba z dłuższą historią „zaraża” każdym znanym zespołem, którego słuchała lata przed
drugą osobą. To korelacja w czasie, nie dowód: w interfejsie piszemy „prawdopodobnie”.
"""

import math
from dataclasses import dataclass
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .stats import find_imported_user


@dataclass(frozen=True)
class InfectionParams:
    fan_plays: int = 10
    carrier_plays: int = 5
    carrier_window_days: int = 90
    startup_days: int = 60
    # Artysta jeszcze niewzbogacony (bez liczby słuchaczy) dostaje wagę jak przy 100 tys.
    default_listeners: int = 100_000
    min_listeners: int = 100


DEFAULT_PARAMS = InfectionParams()


def niche_weight(listeners: int | None, params: InfectionParams = DEFAULT_PARAMS) -> float:
    """Waga zarażenia: im mniej znany artysta, tym bardziej to „podrzucenie”, a nie radio.
    1 / log10(słuchacze), z dolną granicą słuchaczy, żeby artysta z 1 słuchaczem nie
    dostał wagi nieskończonej (100 słuchaczy: 0,5; 100 tys.: 0,2; 5 mln: ok. 0,15)."""
    count = params.default_listeners if listeners is None else listeners
    return 1 / math.log10(max(count, params.min_listeners))


class Infection(BaseModel):
    artist_id: int
    artist_name: str
    listeners: int | None
    weight: float
    source_first_at: datetime  # pierwszy odsłuch u zarażającego (w całej historii)
    source_plays: int  # jego odsłuchy w oknie tuż przed pierwszym odsłuchem u zarażonego
    target_first_at: datetime  # pierwszy odsłuch u zarażonego
    target_plays: int  # wszystkie odsłuchy u zarażonego


class InfectionSide(BaseModel):
    source: str  # nick zarażającego
    target: str  # nick zarażonego
    infections: list[Infection]  # od najwcześniejszego zarażenia
    balance: float  # suma wag


class DuetInfections(BaseModel):
    sides: list[InfectionSide]  # A → B i B → A


_INFECTIONS = text("""
    WITH starts AS (
        SELECT (SELECT min(first_at) FROM first_listen WHERE user_id = :source) AS source_start,
               (SELECT min(first_at) FROM first_listen WHERE user_id = :target) AS target_start
    ),
    candidates AS (
        SELECT t.artist_id, t.first_at AS target_first_at, t.total_plays AS target_plays
        FROM first_listen t
        CROSS JOIN starts
        WHERE t.user_id = :target
          AND t.total_plays >= :fan_plays
          AND t.first_at >= starts.target_start + make_interval(days => :startup_days)
          AND t.first_at - make_interval(days => :window_days) >= starts.source_start
    )
    SELECT c.artist_id, a.name AS artist_name, a.listeners,
           s.first_at AS source_first_at, carrier.plays AS source_plays,
           c.target_first_at, c.target_plays
    FROM candidates c
    JOIN artist a ON a.id = c.artist_id
    JOIN first_listen s ON s.user_id = :source AND s.artist_id = c.artist_id
    CROSS JOIN LATERAL (
        SELECT count(*) AS plays
        FROM scrobble
        WHERE user_id = :source
          AND artist_id = c.artist_id
          AND played_at >= c.target_first_at - make_interval(days => :window_days)
          AND played_at < c.target_first_at
    ) carrier
    WHERE carrier.plays >= :carrier_plays
    ORDER BY c.target_first_at, c.artist_id
""")


def infections(
    session: Session, source_id: int, target_id: int, params: InfectionParams = DEFAULT_PARAMS
) -> list[Infection]:
    """Zarażenia od osoby `source_id` do osoby `target_id`."""
    rows = session.execute(
        _INFECTIONS,
        {
            "source": source_id,
            "target": target_id,
            "fan_plays": params.fan_plays,
            "carrier_plays": params.carrier_plays,
            "window_days": params.carrier_window_days,
            "startup_days": params.startup_days,
        },
    )
    return [
        Infection(
            artist_id=row.artist_id,
            artist_name=row.artist_name,
            listeners=row.listeners,
            weight=round(niche_weight(row.listeners, params), 4),
            source_first_at=row.source_first_at,
            source_plays=row.source_plays,
            target_first_at=row.target_first_at,
            target_plays=row.target_plays,
        )
        for row in rows
    ]


def duet_infections(
    db: Engine, first: str, second: str, params: InfectionParams = DEFAULT_PARAMS
) -> DuetInfections:
    """Zarażenia w obie strony dla dwóch osób o znormalizowanych nickach (wyjątki jak
    w stats.find_imported_user)."""
    with Session(db) as session:
        a = find_imported_user(session, first)
        b = find_imported_user(session, second)
        sides = []
        for source, target in [(a, b), (b, a)]:
            found = infections(session, source.id, target.id, params)
            sides.append(
                InfectionSide(
                    source=source.username,
                    target=target.username,
                    infections=found,
                    balance=round(sum(i.weight for i in found), 4),
                )
            )
        return DuetInfections(sides=sides)
