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
