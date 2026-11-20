import type { Era, GenreEra, UserEras, UserGenreEras } from '#lib/api.ts';

export type EraKind = 'era' | 'episode';

/** Era albo epizod przygotowane do rysowania i listy */
export interface EraItem {
	key: string;
	kind: EraKind;
	/** Dla er gatunkowych: pola wspólne (nazwa, daty, odsłuchy), reszta pusta */
	era: Era;
	color: string;
	/** Tylko w erach gatunkowych: pełne dane okresu */
	genre?: GenreEra;
}

/** Strumień jednego artysty w streamgraphie */
export interface StreamSeries {
	id: number;
	name: string;
	color: string;
	/** Udział w kolejnych tygodniach, 0-1 */
	shares: number[];
}

const PALETTE_SIZE = 10;
const OTHER = 'var(--era-other)';

/**
 * Stały kolor artysty w całej aplikacji: na osi er i w streamgraphie. Kolejność bierzemy
 * z backendu (najpierw artyści z er, potem najczęściej słuchani). Gdyby artystów z er było
 * więcej niż strumieni, dostają kolejne kolory. Pozostali artyści z epizodów są szarzy.
 */
export function artistColors(data: UserEras): Map<number, string> {
	const order = data.streams.artists.map((artist) => artist.id);
	const eraPlays = new Map<number, number>();
	for (const era of data.eras) {
		eraPlays.set(era.artist_id, (eraPlays.get(era.artist_id) ?? 0) + era.plays);
	}
	const extra = [...eraPlays.entries()]
		.filter(([id]) => !order.includes(id))
		.sort((a, b) => b[1] - a[1] || a[0] - b[0])
		.map(([id]) => id);
	return new Map([...order, ...extra].map((id, i) => [id, `var(--era-${(i % PALETTE_SIZE) + 1})`]));
}

/** Ery i epizody w kolejności na osi czasu */
export function eraItems(data: UserEras, colors: Map<number, string>): EraItem[] {
	const items = [
		...data.eras.map((era) => ({ kind: 'era' as const, era })),
		...data.episodes.map((era) => ({ kind: 'episode' as const, era }))
	].map(({ kind, era }) => ({
		key: `${kind}:${era.start}`,
		kind,
		era,
		color: colors.get(era.artist_id) ?? OTHER
	}));
	return items.sort((a, b) => a.era.start.localeCompare(b.era.start));
}

export function streamSeries(data: UserEras, colors: Map<number, string>): StreamSeries[] {
	return data.streams.artists.map((artist, i) => ({
		id: artist.id,
		name: artist.name,
		color: colors.get(artist.id) ?? OTHER,
		shares: data.streams.shares[i]
	}));
}

/**
 * Wspólne kolory dla duetu: ten sam artysta ma ten sam kolor w rzędach obu osób.
 * Najpierw artyści, którzy mają ery albo epizody u obu osób, potem artyści z er na przemian
 * z każdej osoby (od największej liczby odsłuchów w erach), żeby osoba słuchająca więcej
 * nie zajęła całej palety. Kolorów jest 10 i nie zawijają się: w duecie ten sam kolor ma
 * znaczyć tego samego artystę. Pozostali są szarzy, tak jak epizody na profilu.
 */
export function duetColors(people: UserEras[]): Map<number, string> {
	const total = new Map<number, number>();
	const owners = new Map<number, Set<string>>();
	for (const person of people) {
		for (const era of [...person.eras, ...person.episodes]) {
			total.set(era.artist_id, (total.get(era.artist_id) ?? 0) + era.plays);
			owners.set(era.artist_id, (owners.get(era.artist_id) ?? new Set()).add(person.username));
		}
	}
	const byPlays = (plays: Map<number, number>) => (a: number, b: number) =>
		(plays.get(b) ?? 0) - (plays.get(a) ?? 0) || a - b;
	const order = [...owners.keys()].filter((id) => owners.get(id)!.size > 1).sort(byPlays(total));

	// Artyści z er każdej osoby, od najczęściej słuchanych w jej erach
	const queues = people.map((person) => {
		const plays = new Map<number, number>();
		for (const era of person.eras)
			plays.set(era.artist_id, (plays.get(era.artist_id) ?? 0) + era.plays);
		return [...plays.keys()].sort(byPlays(plays));
	});
	for (let i = 0; queues.some((queue) => i < queue.length); i++) {
		for (const queue of queues) {
			if (i < queue.length && !order.includes(queue[i])) order.push(queue[i]);
		}
	}
	return new Map(order.slice(0, PALETTE_SIZE).map((id, i) => [id, `var(--era-${i + 1})`]));
}

/**
 * Ery gatunkowe w tym samym kształcie co ery artystów, żeby oś czasu i lista działały bez
 * zmian. Kolory dostają gatunki z er, od największej liczby odsłuchów; 10 kolorów bez
 * zawijania, reszta (także gatunki z samymi epizodami) jest szara.
 */
export function genreItems(data: UserGenreEras): EraItem[] {
	const plays = new Map<string, number>();
	for (const era of data.eras) plays.set(era.tag, (plays.get(era.tag) ?? 0) + era.plays);
	const ranked = [...plays.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
	const colors = new Map(
		ranked.slice(0, PALETTE_SIZE).map(([tag], i) => [tag, `var(--era-${i + 1})`])
	);
	const tagIds = new Map<string, number>();
	const items = [
		...data.eras.map((genre) => ({ kind: 'era' as const, genre })),
		...data.episodes.map((genre) => ({ kind: 'episode' as const, genre }))
	].map(({ kind, genre }) => {
		// Ujemne id: nie myli się z artystami (np. przy podświetlaniu strumieni)
		const id = tagIds.get(genre.tag) ?? -(tagIds.size + 1);
		tagIds.set(genre.tag, id);
		const era: Era = {
			artist_id: id,
			artist_name: genre.tag,
			image_url: null,
			start: genre.start,
			end: genre.end,
			plays: genre.plays,
			share: genre.share,
			first_play_at: genre.start,
			last_play_at: genre.end,
			top_tracks: [],
			artist_first: { track_name: '', played_at: genre.start }
		};
		return {
			key: `${kind}:${genre.start}`,
			kind,
			era,
			genre,
			color: colors.get(genre.tag) ?? OTHER
		};
	});
	return items.sort((a, b) => a.era.start.localeCompare(b.era.start));
}
