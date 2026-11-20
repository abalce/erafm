"""Ery artystów: okresy, w których jeden artysta wyraźnie dominował w słuchaniu.

Algorytm działa na tygodniach z `weekly_artist_stats`:

1. Tydzień jest aktywny, jeśli ma co najmniej `min_week_plays` odsłuchów. Pozostałe
   tygodnie przerywają ery, a ich dłuższe ciągi to „cisza”.
2. Udział artysty w tygodniu to jego odsłuchy / wszystkie odsłuchy tygodnia. Wygładzamy
   go średnią z aktywnych tygodni w oknie `smoothing_weeks` wokół danego tygodnia.
3. Tydzień należy do artysty z największym wygładzonym udziałem, jeśli ten udział
   przekracza próg osoby: kwantyl `share_quantile` najlepszych udziałów ze wszystkich jej
   tygodni, ale nie mniej niż `min_share`. Stały próg nie działa, bo osoby słuchające stu
   artystów tygodniowo prawie nigdy nie mają artysty z 15% udziału.
4. Tygodnie tego samego artysty sklejamy, jeśli dzieli je najwyżej `max_gap_weeks`
   aktywnych tygodni bez właściciela.
5. Blok z co najmniej `min_era_weeks` tygodniami artysty to era, krótszy (od
   `min_episode_weeks`) to epizod, a pojedyncze tygodnie odpadają.

Wszystko liczymy na żądanie: to kilkaset tygodni po ok. sto artystów, więc ułamek sekundy,
a zmiana parametrów nie wymaga przeliczania żadnej tabeli.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from .config import settings
from .stats import find_imported_user

WEEK = timedelta(days=7)


@dataclass(frozen=True)
class EraParams:
    min_week_plays: int = 20
    smoothing_weeks: int = 5
    share_quantile: float = 0.70
    min_share: float = 0.05
    max_gap_weeks: int = 3
    min_era_weeks: int = 4
    min_episode_weeks: int = 2
    min_silence_weeks: int = 4


DEFAULT_PARAMS = EraParams()


@dataclass
class Block:
    """Okres jednego artysty; indeksy wskazują tygodnie w `Detection.weeks`."""

    artist_id: int
    first: int
    last: int
    owned_weeks: int  # tygodnie, w których artysta był na szczycie (bez przerw)
    plays: int = 0  # odsłuchy artysty w całym okresie, razem z przerwami
    share: float = 0.0  # te odsłuchy / wszystkie odsłuchy w okresie


class WeekTable:
    """Ciągła seria tygodni osoby z odsłuchami i wygładzonymi udziałami artystów."""

    def __init__(
        self,
        plays_by_week: dict[date, dict[int, int]],
        start: date,
        end: date,
        params: EraParams,
        totals_by_week: dict[date, int] | None = None,
    ) -> None:
        self.weeks = [start + i * WEEK for i in range((end - start) // WEEK + 1)]
        self.index = {week: i for i, week in enumerate(self.weeks)}
        self.plays = [dict(plays_by_week.get(week, {})) for week in self.weeks]
        # Sumy tygodni podane osobno, gdy `plays_by_week` ma tylko część artystów
        self.totals = (
            [sum(p.values()) for p in self.plays]
            if totals_by_week is None
            else [totals_by_week.get(week, 0) for week in self.weeks]
        )
        self.active = [total >= params.min_week_plays for total in self.totals]
        half = params.smoothing_weeks // 2
        # Średnią liczymy tylko z aktywnych tygodni okna: cisza obok nie zaniża udziału
        self.windows = [
            [
                j
                for j in range(max(0, i - half), min(len(self.weeks), i + half + 1))
                if self.active[j]
            ]
            for i in range(len(self.weeks))
        ]

    def add_rows(self, rows: list[tuple[date, int, int]]) -> None:
        """Uzupełnia odsłuchy (tydzień, artysta, odsłuchy), np. pełną historię artystów,
        których pominęło przycinanie."""
        for week, artist_id, plays in rows:
            self.plays[self.index[week]][artist_id] = plays

    def smoothed(self, i: int, artist_id: int) -> float:
        """Wygładzony udział artysty w tygodniu `i`; w nieaktywnym tygodniu zero."""
        if not self.active[i]:
            return 0.0
        window = self.windows[i]
        return sum(self.plays[j].get(artist_id, 0) / self.totals[j] for j in window) / len(window)


@dataclass
class Detection:
    table: WeekTable
    threshold: float
    eras: list[Block]
    episodes: list[Block]
    silences: list[tuple[int, int]]  # (pierwszy, ostatni) indeks tygodnia

    @property
    def weeks(self) -> list[date]:
        return self.table.weeks

    def count_plays(self) -> None:
        """Liczy odsłuchy i udział artysty w każdym okresie (wołane ponownie, gdy tabela
        dostanie pełną historię artystów z er)."""
        table = self.table
        for block in [*self.eras, *self.episodes]:
            span = range(block.first, block.last + 1)
            block.plays = sum(table.plays[j].get(block.artist_id, 0) for j in span)
            block.share = block.plays / sum(table.totals[j] for j in span)


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[int(q * (len(ordered) - 1))]


def detect_eras(
    plays_by_week: dict[date, dict[int, int]],
    start: date,
    end: date,
    params: EraParams = DEFAULT_PARAMS,
    totals_by_week: dict[date, int] | None = None,
) -> Detection:
    """Wykrywa ery w tygodniach od `start` do `end` (poniedziałki, włącznie).
    `plays_by_week` to {tydzień: {id artysty: odsłuchy}}; brakujące tygodnie mają zero.

    Wystarczą wiersze artystów, którzy w promieniu `prune_reach_weeks` tygodni mieli
    choć raz udział co najmniej `min_share` (patrz `prune_reach_weeks`); wtedy sumy tygodni
    trzeba podać w `totals_by_week`. Odsłuchy w okresach liczy potem `count_plays`."""
    table = WeekTable(plays_by_week, start, end, params, totals_by_week)
    weeks, plays, active = table.weeks, table.plays, table.active

    # Najlepszy kandydat w każdym aktywnym tygodniu; remis rozstrzyga mniejsze id artysty.
    # Tydzień bez kandydatów (wszystkich przycięto) i tak trafia do kwantyla: z udziałem
    # zero, bo każdy jego artysta ma poniżej `min_share`, więc progu to nie zmienia.
    best: dict[int, tuple[int, float]] = {}
    for i in range(len(weeks)):
        if active[i]:
            share, neg_id = max(((table.smoothed(i, a), -a) for a in plays[i]), default=(0.0, 0))
            best[i] = (-neg_id, share)

    threshold = params.min_share
    if best:
        quantile = _quantile([share for _, share in best.values()], params.share_quantile)
        threshold = max(params.min_share, quantile)

    blocks: list[Block] = []
    current: Block | None = None
    for i, (artist_id, share) in sorted(best.items()):
        if share < threshold:
            continue
        if (
            current is not None
            and current.artist_id == artist_id
            and i - current.last - 1 <= params.max_gap_weeks
            and all(active[current.last + 1 : i])
        ):
            current.last = i
            current.owned_weeks += 1
        else:
            current = Block(artist_id, i, i, 1)
            blocks.append(current)

    silences: list[tuple[int, int]] = []
    run_start: int | None = None
    for i, is_active in enumerate([*active, True]):  # strażnik domyka ostatni ciąg
        if not is_active and run_start is None:
            run_start = i
        elif is_active and run_start is not None:
            if i - run_start >= params.min_silence_weeks:
                silences.append((run_start, i - 1))
            run_start = None

    found = Detection(
        table=table,
        threshold=threshold,
        eras=[b for b in blocks if b.owned_weeks >= params.min_era_weeks],
        episodes=[
            b for b in blocks if params.min_episode_weeks <= b.owned_weeks < params.min_era_weeks
        ],
        silences=silences,
    )
    found.count_plays()
    return found


def prune_reach_weeks(params: EraParams) -> int:
    """Promień przycinania wierszy przed wykrywaniem er.

    Artysta wygrywa tydzień `i` tylko ze średnim udziałem ≥ `min_share` w oknie ±h tygodni
    (h = połowa wygładzania), więc w tym oknie ma jakiś tydzień `k` z udziałem ≥ `min_share`.
    Jego średnią liczymy z tygodni odległych od `k` najwyżej o 2h, więc wiersze artystów
    bez takiego tygodnia w promieniu 2h nie zmieniają wyniku: ich średnie i tak są poniżej
    progu. U osób słuchających szeroko zostaje ok. 5% wierszy."""
    return 2 * (params.smoothing_weeks // 2)


def stream_artists(
    found: Detection, limit: int = 10, most_played: list[int] | None = None
) -> list[int]:
    """Artyści do streamgraphu: najpierw wszyscy z er (od największej liczby odsłuchów
    w erach), potem dopełnienie najczęściej słuchanymi z całej historii. Ta kolejność
    wyznacza też kolory na froncie, więc artyści z er dostają te same kolory co na osi.
    `most_played` to gotowy ranking z bazy; bez niego liczymy go z tabeli tygodni."""
    era_plays: dict[int, int] = defaultdict(int)
    for era in found.eras:
        era_plays[era.artist_id] += era.plays
    chosen = sorted(era_plays, key=lambda a: (-era_plays[a], a))[:limit]

    if most_played is None:
        total_plays: dict[int, int] = defaultdict(int)
        for week in found.table.plays:
            for artist_id, plays in week.items():
                total_plays[artist_id] += plays
        most_played = sorted(total_plays, key=lambda a: (-total_plays[a], a))
    rest = [a for a in most_played if a not in era_plays]
    return chosen + rest[: limit - len(chosen)]


def stream_shares(found: Detection, artist_ids: list[int]) -> list[list[float]]:
    """Wygładzone udziały tygodniowe: jedna lista na artystę, tyle liczb, ile tygodni."""
    table = found.table
    return [
        [round(table.smoothed(i, artist_id), 4) for i in range(len(table.weeks))]
        for artist_id in artist_ids
    ]


class EraTrack(BaseModel):
    name: str
    plays: int


class FirstPlay(BaseModel):
    """Pierwszy odsłuch artysty w całej historii osoby."""

    track_name: str
    played_at: datetime


class Era(BaseModel):
    artist_id: int
    artist_name: str
    image_url: str | None
    start: date  # poniedziałek pierwszego tygodnia
    end: date  # niedziela ostatniego tygodnia
    plays: int
    share: float
    first_play_at: datetime  # pierwszy i ostatni odsłuch artysty w tym okresie
    last_play_at: datetime
    top_tracks: list[EraTrack]  # najwyżej TOP_TRACKS, od najczęściej granego
    artist_first: FirstPlay


class Silence(BaseModel):
    start: date
    end: date


class StreamArtist(BaseModel):
    id: int
    name: str


class Streams(BaseModel):
    """Udziały artystów tydzień po tygodniu do streamgraphu pod osią er."""

    artists: list[StreamArtist]
    weeks: list[date]  # poniedziałki, ciągła seria od początku osi
    shares: list[list[float]]  # shares[artysta][tydzień], 0-1, wygładzone


class UserEras(BaseModel):
    username: str
    start: date | None  # zakres osi: od tygodnia pierwszego odsłuchu do bieżącego
    end: date | None
    threshold: float
    eras: list[Era]
    episodes: list[Era]
    silences: list[Silence]
    streams: Streams


_WEEK_TOTALS = text("""
    SELECT week, sum(plays) AS total
    FROM weekly_artist_stats
    WHERE user_id = :user_id
    GROUP BY week
""")

# Tylko wiersze, które mogą wpłynąć na wykrywanie er (patrz prune_reach_weeks): okno RANGE
# na dacie tygodnia szuka największego udziału artysty w promieniu :reach tygodni
_CANDIDATE_WEEKS = text("""
    WITH totals AS (
        SELECT week, sum(plays) AS total
        FROM weekly_artist_stats
        WHERE user_id = :user_id
        GROUP BY week
    ),
    shares AS (
        SELECT w.week, w.artist_id, w.plays,
               max(w.plays::float8 / t.total) OVER (
                   PARTITION BY w.artist_id
                   ORDER BY w.week
                   RANGE BETWEEN CAST(:reach AS interval) PRECEDING
                             AND CAST(:reach AS interval) FOLLOWING
               ) AS nearby_share
        FROM weekly_artist_stats w
        JOIN totals t USING (week)
        WHERE w.user_id = :user_id
    )
    SELECT week, artist_id, plays FROM shares WHERE nearby_share >= :min_share
""")

# Pełna historia tygodniowa wybranych artystów: ery liczą z niej odsłuchy w okresach,
# a streamgraph udziały w każdym tygodniu
_ARTIST_WEEKS = text("""
    SELECT week, artist_id, plays
    FROM weekly_artist_stats
    WHERE user_id = :user_id AND artist_id = ANY(:ids)
""")

_MOST_PLAYED = text("""
    SELECT artist_id
    FROM first_listen
    WHERE user_id = :user_id
    ORDER BY total_plays DESC, artist_id
    LIMIT :limit
""")

STREAMS = 10

_ARTISTS = text("SELECT id, name, image_url FROM artist WHERE id = ANY(:ids)")

TOP_TRACKS = 5

# Szczegóły wszystkich okresów jednym zapytaniem: unnest robi z tablic tabelę okresów
# (numer, artysta, pierwszy dzień, dzień po ostatnim). Granice są datami w strefie
# aplikacji; AT TIME ZONE zamienia je na chwile w czasie. Okna liczą pierwszy i ostatni
# odsłuch całego okresu, zanim odetniemy utwory spoza pierwszej piątki.
_PERIOD_TRACKS = text("""
    WITH periods AS (
        SELECT *
        FROM unnest(
            CAST(:idx AS int[]),
            CAST(:artist_ids AS bigint[]),
            CAST(:starts AS date[]),
            CAST(:ends AS date[])
        ) AS p (idx, artist_id, start_day, end_day)
    ),
    tracks AS (
        SELECT p.idx, s.track_id, count(*) AS plays,
               min(s.played_at) AS first_at, max(s.played_at) AS last_at
        FROM periods p
        JOIN scrobble s
          ON s.user_id = :user_id
         AND s.artist_id = p.artist_id
         AND s.played_at >= p.start_day::timestamp AT TIME ZONE :timezone
         AND s.played_at < p.end_day::timestamp AT TIME ZONE :timezone
        GROUP BY p.idx, s.track_id
    ),
    ranked AS (
        SELECT *,
               row_number() OVER (PARTITION BY idx ORDER BY plays DESC, track_id) AS rank,
               min(first_at) OVER (PARTITION BY idx) AS period_first,
               max(last_at) OVER (PARTITION BY idx) AS period_last
        FROM tracks
    )
    SELECT r.idx, t.name, r.plays, r.period_first, r.period_last
    FROM ranked r
    JOIN track t ON t.id = r.track_id
    WHERE r.rank <= :top
    ORDER BY r.idx, r.rank
""")

# Pierwszy utwór każdego artysty: first_listen zna chwilę pierwszego odsłuchu, więc
# wystarczy dobrać scrobble z dokładnie tą chwilą (szybko dzięki kluczowi po czasie)
_ARTIST_FIRST = text("""
    SELECT DISTINCT ON (f.artist_id) f.artist_id, t.name AS track_name, f.first_at AS played_at
    FROM first_listen f
    JOIN scrobble s
      ON s.user_id = f.user_id AND s.artist_id = f.artist_id AND s.played_at = f.first_at
    JOIN track t ON t.id = s.track_id
    WHERE f.user_id = :user_id AND f.artist_id = ANY(:ids)
    ORDER BY f.artist_id, s.track_id
""")


def _current_week(timezone: str) -> date:
    today = datetime.now(ZoneInfo(timezone)).date()
    return today - timedelta(days=today.weekday())


def user_eras(db: Engine, username: str, params: EraParams = DEFAULT_PARAMS) -> UserEras:
    """Ery osoby o znormalizowanym nicku (wyjątki jak w stats.find_imported_user)."""
    timezone = settings.app_timezone
    with Session(db) as session:
        user = find_imported_user(session, username)
        totals = {
            row.week: row.total for row in session.execute(_WEEK_TOTALS, {"user_id": user.id})
        }
        if not totals:
            return UserEras(
                username=user.username,
                start=None,
                end=None,
                threshold=params.min_share,
                eras=[],
                episodes=[],
                silences=[],
                streams=Streams(artists=[], weeks=[], shares=[]),
            )

        plays_by_week: dict[date, dict[int, int]] = defaultdict(dict)
        candidates = session.execute(
            _CANDIDATE_WEEKS,
            {
                "user_id": user.id,
                "reach": f"{prune_reach_weeks(params)} weeks",
                "min_share": params.min_share,
            },
        )
        for week, artist_id, plays in candidates:
            plays_by_week[week][artist_id] = plays

        start = min(totals)
        end = max(max(totals), _current_week(timezone))
        found = detect_eras(plays_by_week, start, end, params, totals)

        # Pełne tygodnie artystów z er i kandydatów do streamgraphu, potem dokładne liczby.
        # Ranking najczęściej słuchanych jest dłuższy o artystów z er, bo ich pomijamy.
        era_artists = {b.artist_id for b in [*found.eras, *found.episodes]}
        most_played = list(
            session.scalars(_MOST_PLAYED, {"user_id": user.id, "limit": STREAMS + len(era_artists)})
        )
        full = sorted(era_artists | set(most_played[: STREAMS + len(era_artists)]))
        found.table.add_rows(
            [
                (row.week, row.artist_id, row.plays)
                for row in session.execute(_ARTIST_WEEKS, {"user_id": user.id, "ids": full})
            ]
        )
        found.count_plays()

        streamed = stream_artists(found, STREAMS, most_played)
        ids = sorted({b.artist_id for b in [*found.eras, *found.episodes]} | set(streamed))
        artists = {row.id: row for row in session.execute(_ARTISTS, {"ids": ids})}

        blocks = [*found.eras, *found.episodes]
        details: dict[int, list] = defaultdict(list)
        for row in session.execute(
            _PERIOD_TRACKS,
            {
                "user_id": user.id,
                "timezone": timezone,
                "top": TOP_TRACKS,
                "idx": list(range(len(blocks))),
                "artist_ids": [b.artist_id for b in blocks],
                "starts": [found.weeks[b.first] for b in blocks],
                "ends": [found.weeks[b.last] + WEEK for b in blocks],
            },
        ):
            details[row.idx].append(row)
        firsts = {
            row.artist_id: FirstPlay(track_name=row.track_name, played_at=row.played_at)
            for row in session.execute(
                _ARTIST_FIRST,
                {"user_id": user.id, "ids": sorted({b.artist_id for b in blocks})},
            )
        }

        def era(idx: int) -> Era:
            block = blocks[idx]
            # Okres zawsze ma odsłuchy swojego artysty: tylko takie tygodnie należą do ery
            rows = details[idx]
            artist = artists[block.artist_id]
            return Era(
                artist_id=block.artist_id,
                artist_name=artist.name,
                image_url=artist.image_url,
                start=found.weeks[block.first],
                end=found.weeks[block.last] + timedelta(days=6),
                plays=block.plays,
                share=round(block.share, 3),
                first_play_at=rows[0].period_first,
                last_play_at=rows[0].period_last,
                top_tracks=[EraTrack(name=row.name, plays=row.plays) for row in rows],
                artist_first=firsts[block.artist_id],
            )

        n_eras = len(found.eras)
        return UserEras(
            username=user.username,
            start=start,
            end=end + timedelta(days=6),
            threshold=round(found.threshold, 3),
            eras=[era(i) for i in range(n_eras)],
            episodes=[era(i) for i in range(n_eras, len(blocks))],
            silences=[
                Silence(start=found.weeks[a], end=found.weeks[b] + timedelta(days=6))
                for a, b in found.silences
            ],
            streams=Streams(
                artists=[StreamArtist(id=a, name=artists[a].name) for a in streamed],
                weeks=found.weeks,
                shares=stream_shares(found, streamed),
            ),
        )
