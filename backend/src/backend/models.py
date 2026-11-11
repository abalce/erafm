"""Modele bazy danych: użytkownicy, artyści, utwory, scrobble i zadania importu."""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Stałe nazwy indeksów i ograniczeń, żeby migracje Alembic były przewidywalne
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class LfmUser(Base):
    """Konto Last.fm, którego historię zaimportowaliśmy (także znajomi w grupie)."""

    __tablename__ = "lfm_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Nick małymi literami: Last.fm nie rozróżnia wielkości liter
    username: Mapped[str] = mapped_column(String(64), unique=True)
    # Nick w oryginalnej pisowni, do wyświetlania
    display_name: Mapped[str] = mapped_column(String(64))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    playcount: Mapped[int | None] = mapped_column(Integer)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Artist(Base):
    __tablename__ = "artist"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    # Znormalizowana nazwa (małe litery, bez spacji na brzegach): po niej rozpoznajemy artystę
    name_key: Mapped[str] = mapped_column(Text, unique=True)
    # Identyfikator MusicBrainz; Last.fm często go nie podaje
    mbid: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Track(Base):
    __tablename__ = "track"
    __table_args__ = (UniqueConstraint("artist_id", "name_key", name="uq_track_artist_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artist.id"))
    name: Mapped[str] = mapped_column(Text)
    name_key: Mapped[str] = mapped_column(Text)
    # Album z pierwszego odsłuchu; ten sam utwór bywa na singlu i na albumie
    album_name: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Scrobble(Base):
    """Jeden odsłuch. W bazie to hypertable TimescaleDB podzielona po played_at."""

    __tablename__ = "scrobble"
    __table_args__ = (Index("ix_scrobble_user_artist_played", "user_id", "artist_id", "played_at"),)

    # Kolejność w kluczu (użytkownik, czas) obsługuje też zapytania „historia osoby w czasie”
    user_id: Mapped[int] = mapped_column(ForeignKey("lfm_user.id"), primary_key=True)
    played_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("track.id"), primary_key=True)
    # Kopia artysty z utworu: agregaty po artyście bez łączenia z tabelą track
    artist_id: Mapped[int] = mapped_column(ForeignKey("artist.id"))


class SyncJob(Base):
    """Zadanie importu historii; pozwala wznowić przerwany import od zapisanej strony."""

    __tablename__ = "sync_job"
    __table_args__ = (
        CheckConstraint("kind IN ('full', 'incremental')", name="kind"),
        CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="status"),
        Index("ix_sync_job_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("lfm_user.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    # Okno czasowe importu: `to_ts` zamrożone na starcie, żeby nowe odsłuchy nie przesuwały stron
    from_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_pages: Mapped[int | None] = mapped_column(Integer)
    last_page_done: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
