"""Blokady w Postgresie (pg_advisory_lock): jedna praca danego rodzaju naraz.

Blokada należy do połączenia z bazą: gdy worker padnie, Postgres zwalnia ją sam,
więc po awarii nic nie blokuje wznowienia pracy.
"""

from sqlalchemy import Connection, Engine, func, select

# Rodzaje blokad (pierwsza część klucza); druga część rozróżnia np. osoby
IMPORT_LOCK = 1  # import historii jednej osoby; druga część klucza to id osoby
ENRICHMENT_LOCK = 2  # wzbogacanie artystów; jedna blokada na całą aplikację


def try_lock(db: Engine, kind: int, key: int = 0) -> Connection | None:
    """Bierze blokadę; None, gdy trzyma ją ktoś inny. Zwrócone połączenie trzeba
    oddać przez `release_lock`."""
    conn = db.connect().execution_options(isolation_level="AUTOCOMMIT")
    try:
        if conn.scalar(select(func.pg_try_advisory_lock(kind, key))):
            return conn
    except BaseException:
        conn.close()
        raise
    conn.close()
    return None


def release_lock(conn: Connection, kind: int, key: int = 0) -> None:
    try:
        conn.scalar(select(func.pg_advisory_unlock(kind, key)))
    finally:
        conn.close()
