import { PUBLIC_API_URL } from '$app/env/public';

// Kształty odpowiedzi API; ich źródłem są modele Pydantic w backendzie
// (backend/src/backend/importer.py, stats.py, eras.py, today.py, duet.py, infection.py,
// compat.py, artist.py i friends.py)

export type ImportState = 'pending' | 'running' | 'done' | 'failed';

export interface ImportStatus {
	username: string;
	display_name: string;
	status: ImportState;
	/** Procent zapisanych stron, 0-100 */
	progress: number;
	/** Kod błędu, gdy status to `failed` */
	error: string | null;
	/** Niepuste, gdy historia osoby jest już w bazie (nawet jeśli właśnie się odświeża) */
	last_synced_at: string | null;
}

export interface Totals {
	scrobbles: number;
	artists: number;
	tracks: number;
	first_scrobble_at: string | null;
}

export interface TopArtist {
	id: number;
	name: string;
	plays: number;
	image_url: string | null;
}

export interface TopTrack {
	id: number;
	name: string;
	artist_name: string;
	plays: number;
}

export interface MonthlyPlays {
	/** Pierwszy dzień miesiąca, np. `2024-03-01` */
	month: string;
	plays: number;
}

export interface UserStats {
	username: string;
	display_name: string;
	totals: Totals;
	top_artists: TopArtist[];
	top_tracks: TopTrack[];
	monthly: MonthlyPlays[];
}

export interface EraTrack {
	name: string;
	plays: number;
}

export interface FirstPlay {
	track_name: string;
	played_at: string;
}

/** Era albo epizod: okres, w którym jeden artysta przeważał nad resztą */
export interface Era {
	artist_id: number;
	artist_name: string;
	image_url: string | null;
	/** Poniedziałek pierwszego tygodnia, np. `2024-05-27` */
	start: string;
	/** Niedziela ostatniego tygodnia */
	end: string;
	plays: number;
	/** Udział artysty we wszystkich odsłuchach okresu, 0-1 */
	share: number;
	/** Pierwszy i ostatni odsłuch artysty w tym okresie (znaczniki czasu) */
	first_play_at: string;
	last_play_at: string;
	/** Do pięciu utworów, od najczęściej granego */
	top_tracks: EraTrack[];
	/** Pierwszy odsłuch artysty w całej historii */
	artist_first: FirstPlay;
}

/** Co najmniej kilka tygodni prawie bez słuchania */
export interface Silence {
	start: string;
	end: string;
}

export interface StreamArtist {
	id: number;
	name: string;
}

/** Udziały artystów tydzień po tygodniu (streamgraph pod osią er) */
export interface Streams {
	/** Kolejność wyznacza kolory: najpierw artyści z er, potem najczęściej słuchani */
	artists: StreamArtist[];
	/** Poniedziałki kolejnych tygodni */
	weeks: string[];
	/** `shares[artysta][tydzień]`, wygładzone, 0-1 */
	shares: number[][];
}

export interface UserEras {
	username: string;
	/** Zakres osi: od tygodnia pierwszego odsłuchu do bieżącego; null, gdy brak odsłuchów */
	start: string | null;
	end: string | null;
	/** Próg udziału użyty dla tej osoby */
	threshold: number;
	eras: Era[];
	episodes: Era[];
	silences: Silence[];
	streams: Streams;
}

export interface DayArtist {
	name: string;
	plays: number;
}

/** Ten sam dzień kalendarzowy w jednym z poprzednich lat */
export interface YearAgo {
	year: number;
	years_ago: number;
	/** np. `2023-11-20`; 29 lutego w roku nieprzestępnym to 28 lutego */
	day: string;
	plays: number;
	top_artists: DayArtist[];
	top_track: { name: string; artist_name: string; plays: number };
}

/** Artysta poznany w tym tygodniu kalendarzowym kilka lat temu */
export interface Anniversary {
	artist_id: number;
	artist_name: string;
	first_at: string;
	years_ago: number;
	first_track: string;
	total_plays: number;
}

export interface OnThisDay {
	username: string;
	date: string;
	/** Od najbliższego roku; tylko lata z odsłuchami */
	years: YearAgo[];
	anniversaries: Anniversary[];
}

/** Prawdopodobne zarażenie: `source` słuchał(a) artysty tuż przed pierwszym odsłuchem `target` */
export interface Infection {
	artist_id: number;
	artist_name: string;
	listeners: number | null;
	/** Waga niszowości: mniej znani artyści ważą więcej */
	weight: number;
	source_first_at: string;
	/** Odsłuchy zarażającej osoby w 90 dniach przed pierwszym odsłuchem zarażonej */
	source_plays: number;
	target_first_at: string;
	target_plays: number;
}

export interface InfectionSide {
	source: string;
	target: string;
	infections: Infection[];
	/** Suma wag */
	balance: number;
}

export interface YearCompat {
	year: number;
	/** 0-1 */
	similarity: number;
	shared_artists: number;
	plays_first: number;
	plays_second: number;
}

export interface SharedArtist {
	artist_id: number;
	name: string;
	plays_first: number;
	plays_second: number;
}

export interface DisputedArtist {
	artist_id: number;
	name: string;
	plays: number;
	other_plays: number;
}

export interface Compatibility {
	first: string;
	second: string;
	/** Początek wspólnego okresu; null, gdy któraś osoba nie ma odsłuchów */
	since: string | null;
	similarity: number;
	shared_artists: number;
	years: YearCompat[];
	/** Od najbardziej łączącego */
	connecting: SharedArtist[];
	disputed_first: DisputedArtist[];
	disputed_second: DisputedArtist[];
}

export interface Duet {
	/** W kolejności z adresu */
	people: { username: string; display_name: string }[];
	/** Najpierw pierwsza → druga osoba, potem odwrotnie */
	infections: { sides: InfectionSide[] };
	compatibility: Compatibility;
}

/** Historia jednego artysty u jednej osoby */
export interface ArtistHistory {
	username: string;
	artist_id: number;
	name: string;
	listeners: number | null;
	image_url: string | null;
	/** 0, gdy osoba nie słuchała tego artysty */
	total_plays: number;
	first_at: string | null;
	last_at: string | null;
	first_track: string | null;
	/** Tylko tygodnie z odsłuchami (poniedziałki), od najstarszego */
	weeks: { week: string; plays: number }[];
}

/** Znajomy z Last.fm podpowiadany w formularzu duetu */
export interface FriendSuggestion {
	/** Małymi literami, gotowy do adresu */
	username: string;
	/** Pisownia z Last.fm */
	display_name: string;
	image_url: string | null;
	/** Historia jest już w bazie, więc duet otworzy się bez importu */
	in_erafm: boolean;
}

/** Błąd z API. `code` to kod w snake_case z backendu; polskie teksty są w `messages.ts`. */
export class ApiError extends Error {
	readonly code: string;
	readonly status: number;

	constructor(code: string, status: number) {
		super(code);
		this.code = code;
		this.status = status;
	}
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
	let response: Response;
	try {
		response = await fetch(`${PUBLIC_API_URL}/api${path}`, init);
	} catch {
		// fetch rzuca tylko wtedy, gdy nie udało się w ogóle połączyć
		throw new ApiError('api_unavailable', 0);
	}
	if (!response.ok) {
		let code = 'unknown_error';
		try {
			const body: unknown = await response.json();
			if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
				code = body.detail;
			}
		} catch {
			// odpowiedź bez JSON-a: zostaje kod ogólny
		}
		throw new ApiError(code, response.status);
	}
	return (await response.json()) as T;
}

function userPath(nick: string): string {
	return `/users/${encodeURIComponent(nick)}`;
}

/** Zleca import albo odświeżenie historii i zwraca jego stan. Bezpieczne do wielokrotnego wołania. */
export function startSync(nick: string): Promise<ImportStatus> {
	return request(`${userPath(nick)}/sync`, { method: 'POST' });
}

export function getStatus(nick: string): Promise<ImportStatus> {
	return request(`${userPath(nick)}/status`);
}

export function getStats(nick: string): Promise<UserStats> {
	return request(`${userPath(nick)}/stats`);
}

export function getEras(nick: string): Promise<UserEras> {
	return request(`${userPath(nick)}/eras`);
}

/** „Tego dnia” na dziś albo na podany dzień (`RRRR-MM-DD`) */
export function getToday(nick: string, date?: string | null): Promise<OnThisDay> {
	const query = date ? `?date=${encodeURIComponent(date)}` : '';
	return request(`${userPath(nick)}/today${query}`);
}

export function getDuet(first: string, second: string): Promise<Duet> {
	return request(`/duet?u=${encodeURIComponent(`${first},${second}`)}`);
}

export function getArtist(nick: string, artistId: number): Promise<ArtistHistory> {
	return request(`${userPath(nick)}/artists/${artistId}`);
}

/** Znajomi osoby z Last.fm; najpierw osoby już w Erafm */
export function getFriends(nick: string): Promise<FriendSuggestion[]> {
	return request(`${userPath(nick)}/friends`);
}
