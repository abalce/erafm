"""Modele bazy danych: użytkownicy, artyści, utwory, scrobble i zadania importu."""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
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
    # Dane z artist.getInfo: ilu ludzi słucha artysty i ile ma odsłuchów na całym Last.fm
    listeners: Mapped[int | None] = mapped_column(Integer)
    global_playcount: Mapped[int | None] = mapped_column(BigInteger)
    image_url: Mapped[str | None] = mapped_column(Text)
    # Kiedy ostatnio pytaliśmy Last.fm o artystę (także wtedy, gdy go nie znalazł)
    enriched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Tag(Base):
    __tablename__ = "tag"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # Nazwa małymi literami: Last.fm nie rozróżnia wielkości liter w tagach
    name: Mapped[str] = mapped_column(Text, unique=True)


class ArtistTag(Base):
    """Tag artysty w surowej postaci z Last.fm; tagi-śmieci odsiewamy dopiero przy odczycie."""

    __tablename__ = "artist_tag"
    __table_args__ = (Index("ix_artist_tag_tag", "tag_id"),)

    artist_id: Mapped[int] = mapped_column(ForeignKey("artist.id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tag.id"), primary_key=True)
    # Waga 1-100 z Last.fm: 100 ma najczęściej nadawany tag artysty
    weight: Mapped[int] = mapped_column(Integer)


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


class WeeklyArtistStats(Base):
    """Odsłuchy artysty u jednej osoby w jednym tygodniu. Tabela wyliczana ze scrobbli
    po każdym imporcie osoby (backend/aggregates.py), nigdy nie edytowana ręcznie."""

    __tablename__ = "weekly_artist_stats"

    user_id: Mapped[int] = mapped_column(ForeignKey("lfm_user.id"), primary_key=True)
    # Poniedziałek rozpoczynający tydzień, w strefie czasowej aplikacji
    week: Mapped[date] = mapped_column(Date, primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artist.id"), primary_key=True)
    plays: Mapped[int] = mapped_column(Integer)


class FirstListen(Base):
    """Historia jednej osoby z jednym artystą w pigułce: pierwszy i ostatni odsłuch
    oraz ich łączna liczba. Wyliczana ze scrobbli razem z weekly_artist_stats."""

    __tablename__ = "first_listen"

    user_id: Mapped[int] = mapped_column(ForeignKey("lfm_user.id"), primary_key=True)
    artist_id: Mapped[int] = mapped_column(ForeignKey("artist.id"), primary_key=True)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_plays: Mapped[int] = mapped_column(Integer)


class SyncJob(Base):
    """Zadanie importu historii; pozwala wznowić przerwany import od zapisanej strony."""

    __tablename__ = "sync_job"
    __table_args__ = (
        CheckConstraint("kind IN ('full', 'incremental')", name="kind"),
        CheckConstraint("status IN ('pending', 'running', 'done', 'failed')", name="status"),
        Index("ix_sync_job_user_created", "user_id", "created_at"),
        # Najwyżej jeden trwający import na osobę: pilnuje tego baza, nie kod
        Index(
            "uq_sync_job_active_user",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("lfm_user.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    # Okno czasowe importu: `to_ts` zamrożone na starcie, żeby nowe odsłuchy nie przesuwały stron
    from_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    to_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    total_pages: Mapped[int | None] = mapped_column(Integer)
    # Ile stron już zapisano. Import idzie od najstarszej strony (o najwyższym numerze)
    # do najnowszej, więc następna do pobrania to `total_pages - pages_done`.
    pages_done: Mapped[int] = mapped_column(Integer, server_default="0")
    # Odsłuchy pominięte jako powtórki tego samego utworu w odstępie poniżej 30 s
    duplicates_skipped: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
