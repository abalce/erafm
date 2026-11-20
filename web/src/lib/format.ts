// useGrouping 'always': polski format domyślnie nie rozdziela liczb czterocyfrowych,
// a obok siebie „2420” i „54 967” wyglądają niespójnie
const numberFormat = new Intl.NumberFormat('pl-PL', { useGrouping: 'always' });
const dateFormat = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'long' });

/** 54967 -> „54 967” */
export function formatNumber(value: number): string {
	return numberFormat.format(value);
}

/** Znacznik czasu z API -> „23 października 2022” */
export function formatDate(iso: string): string {
	return dateFormat.format(new Date(iso));
}

const monthFormat = new Intl.DateTimeFormat('pl-PL', {
	month: 'long',
	year: 'numeric',
	timeZone: 'UTC'
});

/** Pierwszy dzień miesiąca w UTC -> „marzec 2024” */
export function formatMonth(month: Date): string {
	return monthFormat.format(month);
}

const pluralRules = new Intl.PluralRules('pl-PL');

/** 1 -> „1 odsłuch”, 3 -> „3 odsłuchy”, 54967 -> „54 967 odsłuchów” */
export function formatPlays(count: number): string {
	const form = { one: 'odsłuch', few: 'odsłuchy' }[pluralRules.select(count) as string];
	return `${formatNumber(count)} ${form ?? 'odsłuchów'}`;
}

// Daty z API typu `2024-05-27` to północ UTC, więc formatujemy je w UTC: w strefie
// z ujemnym przesunięciem wypadłby dzień wcześniej
const dayFormat = new Intl.DateTimeFormat('pl-PL', { dateStyle: 'long', timeZone: 'UTC' });
const monthOnlyFormat = new Intl.DateTimeFormat('pl-PL', { month: 'long', timeZone: 'UTC' });

/** `2024-05-27` -> „27 maja 2024” */
export function formatDay(day: string): string {
	return dayFormat.format(new Date(day));
}

/** Okres w miesiącach: „maj 2024”, „maj–sierpień 2024”, „grudzień 2024 – styczeń 2025” */
export function formatMonthRange(start: string, end: string): string {
	const from = new Date(start);
	const to = new Date(end);
	const sameYear = from.getUTCFullYear() === to.getUTCFullYear();
	if (sameYear && from.getUTCMonth() === to.getUTCMonth()) return formatMonth(from);
	if (sameYear) return `${monthOnlyFormat.format(from)}–${formatMonth(to)}`;
	return `${formatMonth(from)} – ${formatMonth(to)}`;
}

const percentFormat = new Intl.NumberFormat('pl-PL', {
	style: 'percent',
	maximumFractionDigits: 1
});

/** 0.096 -> „9,6%” */
export function formatPercent(value: number): string {
	return percentFormat.format(value);
}

/** Liczba z odmienionym rzeczownikiem: formatCount(5, ['era', 'ery', 'er']) -> „5 er” */
export function formatCount(count: number, [one, few, many]: [string, string, string]): string {
	const rule = pluralRules.select(count);
	return `${formatNumber(count)} ${rule === 'one' ? one : rule === 'few' ? few : many}`;
}

/**
 * Czas między dwoma chwilami w latach i miesiącach, a gdy to mniej niż miesiąc, w dniach:
 * „1 rok i 7 miesięcy”, „3 miesiące”, „12 dni”
 */
export function formatDuration(fromIso: string, toIso: string): string {
	const from = new Date(fromIso);
	const to = new Date(toIso);
	let months =
		(to.getUTCFullYear() - from.getUTCFullYear()) * 12 + (to.getUTCMonth() - from.getUTCMonth());
	if (to.getUTCDate() < from.getUTCDate()) months -= 1;
	if (months < 1) {
		const days = Math.max(0, Math.floor((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000)));
		return formatCount(days, ['dzień', 'dni', 'dni']);
	}
	const years = Math.floor(months / 12);
	const rest = months % 12;
	const parts = [
		years > 0 ? formatCount(years, ['rok', 'lata', 'lat']) : '',
		rest > 0 ? formatCount(rest, ['miesiąc', 'miesiące', 'miesięcy']) : ''
	];
	return parts.filter(Boolean).join(' i ');
}

const dayMonthFormat = new Intl.DateTimeFormat('pl-PL', {
	day: 'numeric',
	month: 'long',
	timeZone: 'UTC'
});

/** `2026-11-20` -> „20 listopada” */
export function formatDayMonth(day: string): string {
	return dayMonthFormat.format(new Date(day));
}

/** 1 -> „1 rok temu”, 3 -> „3 lata temu”, 6 -> „6 lat temu” */
export function formatYearsAgo(years: number): string {
	return `${formatCount(years, ['rok', 'lata', 'lat'])} temu`;
}
