<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import {
		ApiError,
		getStats,
		getStatus,
		startSync,
		type ImportStatus,
		type UserStats
	} from '#lib/api.ts';
	import { formatDate, formatNumber } from '#lib/format.ts';
	import { canRetry, errorMessage } from '#lib/messages.ts';

	// Co ile pytamy API o postęp importu
	const POLL_MS = 2000;

	type View =
		| { kind: 'loading' }
		| { kind: 'importing'; progress: number }
		| { kind: 'ready'; stats: UserStats; refreshing: boolean; refreshFailed: boolean }
		| { kind: 'error'; code: string };

	const nick = $derived(page.params.nick ?? '');
	let view: View = $state({ kind: 'loading' });
	// Zwiększenie licznika uruchamia całą ścieżkę od nowa (przycisk „Spróbuj ponownie”)
	let attempt = $state(0);

	$effect(() => {
		const current = nick;
		void attempt;

		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | undefined;
		let statsShown = false;
		view = { kind: 'loading' };

		async function showStats(refreshing: boolean, refreshFailed: boolean) {
			const stats = await getStats(current);
			if (cancelled) return;
			statsShown = true;
			view = { kind: 'ready', stats, refreshing, refreshFailed };
		}

		async function handle(status: ImportStatus) {
			if (cancelled) return;
			const hasHistory = status.last_synced_at !== null;

			if (status.status === 'done') {
				await showStats(false, false);
				return;
			}
			if (status.status === 'failed') {
				if (hasHistory) await showStats(false, true);
				else view = { kind: 'error', code: status.error ?? 'unknown_error' };
				return;
			}

			// pending albo running: przy pierwszym imporcie pasek postępu, przy odświeżaniu
			// od razu dotychczasowe dane, a nowe po zakończeniu
			if (!hasHistory) view = { kind: 'importing', progress: status.progress };
			else if (!statsShown) await showStats(true, false);
			if (!cancelled) timer = setTimeout(poll, POLL_MS);
		}

		async function poll() {
			try {
				await handle(await getStatus(current));
			} catch (error) {
				fail(error);
			}
		}

		function fail(error: unknown) {
			// Gdy dane są już na ekranie, nieudane odpytanie o postęp ich nie zasłania
			if (cancelled || statsShown) return;
			view = { kind: 'error', code: error instanceof ApiError ? error.code : 'unknown_error' };
		}

		// Otwarcie profilu od razu zleca import albo odświeżenie historii
		startSync(current).then(handle).catch(fail);

		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	});
</script>

<svelte:head>
	<title>{nick} · Erafm</title>
</svelte:head>

{#if view.kind === 'loading'}
	<p class="muted" role="status">Ładowanie…</p>
{:else if view.kind === 'importing'}
	<section class="importing">
		<h1>{nick}</h1>
		<p class="lead">Pobieramy historię odsłuchów z Last.fm.</p>
		<div class="progress-row">
			<progress max="100" value={view.progress} aria-label="Postęp importu"></progress>
			<span class="percent" role="status">{view.progress}%</span>
		</div>
		<p class="muted">
			Cała historia to zwykle kilka minut. Możesz zostawić tę kartę otwartą albo wrócić później pod
			ten sam adres.
		</p>
	</section>
{:else if view.kind === 'ready'}
	{@const totals = view.stats.totals}
	<section>
		<h1>{view.stats.display_name}</h1>
		{#if view.refreshing}
			<p class="muted" role="status">Sprawdzamy, czy doszły nowe odsłuchy…</p>
		{:else if view.refreshFailed}
			<p class="muted" role="status">
				Nie udało się pobrać najnowszych odsłuchów. Pokazujemy dane z ostatniego udanego pobrania.
			</p>
		{/if}

		<dl class="totals">
			<div class="stat">
				<dt>Odsłuchy</dt>
				<dd>{formatNumber(totals.scrobbles)}</dd>
			</div>
			<div class="stat">
				<dt>Artyści</dt>
				<dd>{formatNumber(totals.artists)}</dd>
			</div>
			<div class="stat">
				<dt>Utwory</dt>
				<dd>{formatNumber(totals.tracks)}</dd>
			</div>
			<div class="stat">
				<dt>Pierwszy odsłuch</dt>
				<dd class="date">
					{totals.first_scrobble_at ? formatDate(totals.first_scrobble_at) : 'brak'}
				</dd>
			</div>
		</dl>

		{#if totals.scrobbles === 0}
			<p class="muted">To konto nie ma jeszcze żadnych odsłuchów na Last.fm.</p>
		{/if}
	</section>
{:else}
	<section class="problem">
		<h1>{nick}</h1>
		<p class="lead" role="alert">{errorMessage(view.code)}</p>
		<div class="actions">
			{#if canRetry(view.code)}
				<button class="button" type="button" onclick={() => attempt++}>Spróbuj ponownie</button>
			{/if}
			<a href={resolve('/')}>Wpisz inny nick</a>
		</div>
	</section>
{/if}

<style>
	section {
		padding-top: var(--space-5);
	}

	h1 {
		font-size: clamp(2rem, 6vw, 3rem);
		overflow-wrap: anywhere;
	}

	.lead {
		margin-top: var(--space-3);
		font-size: 1.125rem;
	}

	.importing,
	.problem {
		max-width: 36rem;
	}

	.progress-row {
		display: flex;
		align-items: center;
		gap: var(--space-4);
		margin: var(--space-5) 0 var(--space-4);
	}

	progress {
		flex: 1;
		height: 0.75rem;
		border: none;
		border-radius: 999px;
		background: var(--surface-raised);
		overflow: hidden;
		appearance: none;
	}

	progress::-webkit-progress-bar {
		background: var(--surface-raised);
	}

	progress::-webkit-progress-value {
		background: var(--accent);
		border-radius: 999px;
		transition: width 0.4s ease;
	}

	progress::-moz-progress-bar {
		background: var(--accent);
		border-radius: 999px;
	}

	.percent {
		min-width: 3.5ch;
		font-variant-numeric: tabular-nums;
		font-weight: 600;
		text-align: right;
	}

	.totals {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
		gap: var(--space-3);
		margin: var(--space-5) 0;
	}

	.stat {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}

	dt {
		color: var(--text-muted);
		font-size: 0.875rem;
	}

	dd {
		margin: 0;
		font-family: var(--font-display);
		font-size: 2rem;
		font-variant-numeric: tabular-nums;
		line-height: 1.1;
	}

	dd.date {
		font-size: 1.25rem;
		line-height: 1.3;
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-5);
		margin-top: var(--space-5);
	}
</style>
