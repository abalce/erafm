"""Duet: wszystko, co ekran porównania dwóch osób pokazuje naraz.

Import każdej osoby zleca i śledzi frontend przez /users/{nick}/sync i /status (jak na
profilu, razem z limitami importów). Tu tylko liczymy, gdy obie historie są w bazie.
"""

from pydantic import BaseModel
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from .compat import Compatibility, compatibility
from .infection import DuetInfections, infection_sides
from .stats import find_imported_user


class DuetPerson(BaseModel):
    username: str
    display_name: str


class Duet(BaseModel):
    people: list[DuetPerson]  # w kolejności z adresu: pierwsza osoba to „ty”
    infections: DuetInfections  # najpierw pierwsza → druga, potem druga → pierwsza
    compatibility: Compatibility


def duet(db: Engine, first: str, second: str) -> Duet:
    """Duet dwóch osób o znormalizowanych nickach. Rzuca NotImported albo
    ImportInProgress (jak stats.find_imported_user), gdy którejś historii jeszcze nie ma."""
    with Session(db) as session:
        a = find_imported_user(session, first)
        b = find_imported_user(session, second)
        return Duet(
            people=[
                DuetPerson(username=person.username, display_name=person.display_name)
                for person in (a, b)
            ],
            infections=infection_sides(session, a, b),
            compatibility=compatibility(session, a.id, b.id, a.username, b.username),
        )
