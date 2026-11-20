// Polskie teksty dla kodów błędów z API (backend zwraca same kody)

const MESSAGES: Record<string, string> = {
	user_not_found: 'Na Last.fm nie ma konta o takim nicku. Sprawdź pisownię.',
	history_hidden:
		'Ta osoba ukryła historię odsłuchów w ustawieniach prywatności Last.fm, więc nie możemy jej pobrać.',
	lastfm_error: 'Last.fm chwilowo nie odpowiada. Spróbuj ponownie za chwilę.',
	internal_error: 'Coś poszło nie tak po naszej stronie. Spróbuj ponownie.',
	invalid_username: 'To nie wygląda na nick Last.fm.',
	invalid_duet: 'Do duetu potrzebne są dwa różne nicki Last.fm.',
	artist_not_found: 'Nie mamy takiego artysty w bazie.',
	not_imported: 'Nie mamy jeszcze historii tej osoby.',
	import_queue_full:
		'Właśnie trwają importy innych osób, a serwer pobiera najwyżej kilka naraz. Spróbuj za kilka minut.',
	user_limit_reached:
		'Erafm ma już komplet osób: to mały projekt na darmowym serwerze. Możesz obejrzeć profile demo na stronie startowej.',
	api_unavailable:
		'Nie udało się połączyć z serwerem Erafm. Sprawdź, czy działa, i spróbuj ponownie.'
};

const FALLBACK = 'Coś poszło nie tak. Spróbuj ponownie.';

export function errorMessage(code: string): string {
	return MESSAGES[code] ?? FALLBACK;
}

/** Czy ponowienie tej samej prośby ma sens (przy złym nicku nie ma). */
export function canRetry(code: string): boolean {
	return ![
		'user_not_found',
		'history_hidden',
		'invalid_username',
		'invalid_duet',
		'artist_not_found',
		'user_limit_reached'
	].includes(code);
}
