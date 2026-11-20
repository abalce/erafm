import { ApiError, getStatus, startSync, type ImportStatus } from '#lib/api.ts';

// Co ile pytamy API o postęp importu
const POLL_MS = 2000;

/** Stan historii jednej osoby z punktu widzenia strony */
export type ImportState =
	| { kind: 'loading' }
	/** Pierwszy import: danych jeszcze nie pokazujemy (API nie daje częściowych liczb) */
	| { kind: 'importing'; progress: number }
	/**
	 * Historia jest w bazie i można ją pokazać. `refreshing`: właśnie dociągamy nowe
	 * odsłuchy (po zakończeniu przyjdzie kolejny stan `ready`); `refreshFailed`: ostatnie
	 * odświeżenie się nie udało, więc dane są z poprzedniego pobrania.
	 */
	| { kind: 'ready'; refreshing: boolean; refreshFailed: boolean }
	| { kind: 'error'; code: string };

/**
 * Zleca import albo odświeżenie historii osoby i śledzi postęp, wołając `onChange` przy
 * każdej zmianie stanu. Zwraca funkcję, która przestaje śledzić (np. przy zmianie nicka).
 * Gdy dane były już gotowe, późniejszy błąd odpytywania ich nie zasłania.
 */
export function watchImport(nick: string, onChange: (state: ImportState) => void): () => void {
	let stopped = false;
	let timer: ReturnType<typeof setTimeout> | undefined;
	let readySeen = false;
	let lastKey = '';

	function emit(state: ImportState) {
		if (stopped) return;
		// Odpytujemy co 2 s; ten sam stan nie musi przerysowywać strony
		const key = JSON.stringify(state);
		if (key === lastKey) return;
		lastKey = key;
		if (state.kind === 'ready') readySeen = true;
		onChange(state);
	}

	function handle(status: ImportStatus) {
		if (stopped) return;
		const hasHistory = status.last_synced_at !== null;

		if (status.status === 'done') {
			emit({ kind: 'ready', refreshing: false, refreshFailed: false });
			return;
		}
		if (status.status === 'failed') {
			if (hasHistory) emit({ kind: 'ready', refreshing: false, refreshFailed: true });
			else emit({ kind: 'error', code: status.error ?? 'unknown_error' });
			return;
		}
		// pending albo running: przy pierwszym imporcie postęp, przy odświeżaniu od razu
		// dotychczasowe dane
		emit(
			hasHistory
				? { kind: 'ready', refreshing: true, refreshFailed: false }
				: { kind: 'importing', progress: status.progress }
		);
		timer = setTimeout(poll, POLL_MS);
	}

	async function poll() {
		try {
			handle(await getStatus(nick));
		} catch (error) {
			fail(error);
		}
	}

	function fail(error: unknown) {
		if (stopped || readySeen) return;
		emit({ kind: 'error', code: error instanceof ApiError ? error.code : 'unknown_error' });
	}

	// Każde wejście od razu zleca import albo odświeżenie historii
	startSync(nick).then(handle).catch(fail);

	return () => {
		stopped = true;
		clearTimeout(timer);
	};
}
