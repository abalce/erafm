"""jeden aktywny import i licznik duplikatów

Revision ID: fe99623cfb20
Revises: bce2f5283261
Create Date: 2026-11-15 17:23:02.253123

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "fe99623cfb20"
down_revision: str | Sequence[str] | None = "bce2f5283261"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Zmiana nazwy zamiast usunięcia i dodania kolumny (autogenerate tego nie rozpoznaje):
    # import idzie od najstarszej strony, więc liczymy zapisane strony, a nie numer ostatniej
    op.alter_column("sync_job", "last_page_done", new_column_name="pages_done")
    op.add_column(
        "sync_job",
        sa.Column("duplicates_skipped", sa.Integer(), server_default="0", nullable=False),
    )
    # Częściowy indeks unikalny: jedna osoba może mieć najwyżej jeden trwający import
    op.create_index(
        "uq_sync_job_active_user",
        "sync_job",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_sync_job_active_user",
        table_name="sync_job",
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )
    op.drop_column("sync_job", "duplicates_skipped")
    op.alter_column("sync_job", "pages_done", new_column_name="last_page_done")
