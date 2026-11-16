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
