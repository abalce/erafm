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
