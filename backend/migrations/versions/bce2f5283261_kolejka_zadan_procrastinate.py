"""kolejka zadań procrastinate

Revision ID: bce2f5283261
Revises: 686111da1e36
Create Date: 2026-11-11 23:00:03.509033

"""

from collections.abc import Sequence

from alembic import op
from procrastinate.schema import SchemaManager

# revision identifiers, used by Alembic.
revision: str = "bce2f5283261"
down_revision: str | Sequence[str] | None = "686111da1e36"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Usuwa wszystko, co procrastinate założył: tabele (z indeksami i wyzwalaczami),
# funkcje i typy. Każdy jego obiekt ma nazwę zaczynającą się od "procrastinate_".
DROP_PROCRASTINATE = """
DO $$
DECLARE
    obj record;
BEGIN
    FOR obj IN
        SELECT c.oid::regclass AS name FROM pg_class c
        WHERE c.relkind = 'r' AND c.relnamespace = current_schema()::regnamespace
          AND c.relname LIKE 'procrastinate\\_%'
    LOOP
        EXECUTE format('DROP TABLE %s CASCADE', obj.name);
    END LOOP;
    FOR obj IN
        SELECT p.oid::regprocedure AS name FROM pg_proc p
        WHERE p.pronamespace = current_schema()::regnamespace
          AND p.proname LIKE 'procrastinate\\_%'
    LOOP
        EXECUTE format('DROP FUNCTION %s CASCADE', obj.name);
    END LOOP;
    FOR obj IN
        SELECT t.oid::regtype AS name FROM pg_type t
        WHERE t.typnamespace = current_schema()::regnamespace
          AND t.typname LIKE 'procrastinate\\_%' AND t.typtype IN ('e', 'c')
    LOOP
        EXECUTE format('DROP TYPE %s CASCADE', obj.name);
    END LOOP;
END
$$;
"""


def _run_script(sql: str) -> None:
    # Skrypt ma wiele poleceń i znaki "%", więc omijamy parametry SQLAlchemy
    # i wysyłamy go wprost przez połączenie psycopg
    op.get_bind().connection.cursor().execute(sql)


def upgrade() -> None:
    """Upgrade schema."""
    # Schemat z zainstalowanej wersji procrastinate (przypiętej w pyproject.toml).
    # Przy podnoszeniu wersji biblioteki dopisujemy nową migrację z jej plikami SQL.
    _run_script(SchemaManager.get_schema())


def downgrade() -> None:
    """Downgrade schema."""
    _run_script(DROP_PROCRASTINATE)
