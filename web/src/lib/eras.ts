import type { Era, UserEras } from '#lib/api.ts';

export type EraKind = 'era' | 'episode';

/** Era albo epizod przygotowane do rysowania i listy */
export interface EraItem {
	key: string;
	kind: EraKind;
	era: Era;
	color: string;
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
