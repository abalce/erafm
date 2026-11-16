import { PUBLIC_API_URL } from '$app/env/public';

// Kształty odpowiedzi API; ich źródłem są modele Pydantic w backendzie
// (backend/src/backend/importer.py i stats.py)

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
		response = await fetch(`${PUBLIC_API_URL}${path}`, init);
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
