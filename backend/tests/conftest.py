"""Wspólne przygotowanie testów: osobna baza testowa na tym samym serwerze Postgres."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.exc import OperationalError

from backend.config import settings

TEST_DATABASE = "statsmusic_test"
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


@pytest.fixture(scope="session")
def db() -> Iterator[Engine]:
    """Silnik bazy testowej ze schematem założonym przez prawdziwe migracje Alembic."""
    url = make_url(settings.database_url)
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": TEST_DATABASE}
            )
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE}"'))
    except OperationalError:
        pytest.fail("Baza niedostępna. Uruchom ją: docker compose up -d db")
    finally:
        admin.dispose()

    test_url = url.set(database=TEST_DATABASE)
    # Config bez pliku .ini: migracje nie przestawiają wtedy logowania w testach
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["database_url"] = test_url.render_as_string(hide_password=False)
    command.upgrade(config, "head")

    engine = create_engine(test_url)
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(db: Engine) -> Engine:
    """Baza testowa z pustymi tabelami aplikacji."""
    with db.begin() as conn:
        conn.execute(
            text("TRUNCATE lfm_user, artist, track, scrobble, sync_job RESTART IDENTITY CASCADE")
        )
    return db
