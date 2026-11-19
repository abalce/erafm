import type { Era, UserEras } from '#lib/api.ts';

export type EraKind = 'era' | 'episode';

/** Era albo epizod przygotowane do rysowania i listy */
export interface EraItem {
	key: string;
	kind: EraKind;
	era: Era;
	color: string;
}

const PALETTE_SIZE = 8;

/**
 * Stały kolor dla każdego artysty, który ma erę: ten sam artysta w kilku erach i epizodach
 * wygląda tak samo. Kolory dostają najpierw artyści z największą liczbą odsłuchów w erach.
 * Artyści z samymi epizodami są szarzy, żeby nie rozmywać er.
 */
function artistColors(eras: Era[]): Map<number, string> {
	const plays = new Map<number, number>();
	for (const era of eras) plays.set(era.artist_id, (plays.get(era.artist_id) ?? 0) + era.plays);
	const ranked = [...plays.entries()].sort((a, b) => b[1] - a[1] || a[0] - b[0]);
	return new Map(ranked.map(([id], i) => [id, `var(--era-${(i % PALETTE_SIZE) + 1})`]));
}

/** Ery i epizody w kolejności na osi czasu */
export function eraItems(data: UserEras): EraItem[] {
	const colors = artistColors(data.eras);
	const items = [
		...data.eras.map((era) => ({ kind: 'era' as const, era })),
		...data.episodes.map((era) => ({ kind: 'episode' as const, era }))
	].map(({ kind, era }) => ({
		key: `${kind}:${era.start}`,
		kind,
		era,
		color: colors.get(era.artist_id) ?? 'var(--era-other)'
	}));
	return items.sort((a, b) => a.era.start.localeCompare(b.era.start));
}
