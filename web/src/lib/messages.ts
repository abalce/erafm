// Polskie teksty dla kodów błędów z API (backend zwraca same kody)

const MESSAGES: Record<string, string> = {
	user_not_found: 'Na Last.fm nie ma konta o takim nicku. Sprawdź pisownię.',
	history_hidden:
		'Ta osoba ukryła historię odsłuchów w ustawieniach prywatności Last.fm, więc nie możemy jej pobrać.',
	lastfm_error: 'Last.fm chwilowo nie odpowiada. Spróbuj ponownie za chwilę.',
	internal_error: 'Coś poszło nie tak po naszej stronie. Spróbuj ponownie.',
	invalid_username: 'To nie wygląda na nick Last.fm.',
	not_imported: 'Nie mamy jeszcze historii tej osoby.',
	api_unavailable:
		'Nie udało się połączyć z serwerem Erafm. Sprawdź, czy działa, i spróbuj ponownie.'
};

const FALLBACK = 'Coś poszło nie tak. Spróbuj ponownie.';

export function errorMessage(code: string): string {
	return MESSAGES[code] ?? FALLBACK;
}

/** Czy ponowienie tej samej prośby ma sens (przy złym nicku nie ma). */
export function canRetry(code: string): boolean {
	return !['user_not_found', 'history_hidden', 'invalid_username'].includes(code);
}
