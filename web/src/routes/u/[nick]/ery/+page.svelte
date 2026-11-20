<script lang="ts">
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { ApiError, getEras, getGenreEras, type UserEras, type UserGenreEras } from '#lib/api.ts';
	import EraTimeline from '#lib/EraTimeline.svelte';
	import { artistColors, eraItems, genreItems, streamSeries, type EraItem } from '#lib/eras.ts';
	import {
		formatDate,
		formatDay,
		formatDuration,
		formatMonthRange,
		formatPercent,
		formatPlays
	} from '#lib/format.ts';
	import { canRetry, errorMessage } from '#lib/messages.ts';
	import TopList from '#lib/TopList.svelte';

	type View =
		| { kind: 'loading' }
		| { kind: 'ready'; mode: 'artists'; data: UserEras }
		| { kind: 'ready'; mode: 'genres'; data: UserGenreEras }
		| { kind: 'error'; code: string };

	const nick = $derived(page.params.nick ?? '');
	// Widok żyje w adresie (?widok=gatunki), więc „wstecz” wraca do artystów
	const mode = $derived(page.url.searchParams.get('widok') === 'gatunki' ? 'genres' : 'artists');
	let view = $state<View>({ kind: 'loading' });
	let attempt = $state(0);
	let selected = $state<string | null>(null);

	$effect(() => {
		const current = nick;
		const currentMode = mode;
		void attempt;
		let cancelled = false;
		view = { kind: 'loading' };
		selected = null;

		const request: Promise<View> =
			currentMode === 'genres'
				? getGenreEras(current).then((data) => ({ kind: 'ready', mode: 'genres', data }))
				: getEras(current).then((data) => ({ kind: 'ready', mode: 'artists', data }));
		request
			.then((ready) => {
				if (!cancelled) view = ready;
			})
			.catch((error: unknown) => {
				if (cancelled) return;
				view = { kind: 'error', code: error instanceof ApiError ? error.code : 'unknown_error' };
			});

		return () => {
			cancelled = true;
		};
	});

	const colors = $derived(
		view.kind === 'ready' && view.mode === 'artists'
			? artistColors(view.data)
			: new Map<number, string>()
	);
	const items = $derived(
		view.kind !== 'ready'
			? []
			: view.mode === 'artists'
				? eraItems(view.data, colors)
				: genreItems(view.data)
	);
	// Streamgraph tylko dla artystów: ery gatunkowe mają samą oś i listę
	const streams = $derived(
		view.kind === 'ready' && view.mode === 'artists' ? streamSeries(view.data, colors) : []
	);
	const weeks = $derived(
		view.kind === 'ready' && view.mode === 'artists' ? view.data.streams.weeks : []
	);

	function showMode(next: 'artists' | 'genres') {
		const suffix = next === 'genres' ? '?widok=gatunki' : '';
		// resolve() zna tylko ścieżki, a widok żyje w parametrze adresu, więc dokładamy go ręcznie
		// eslint-disable-next-line svelte/no-navigation-without-resolve
		goto(`${resolve('/u/[nick]/ery', { nick })}${suffix}`, { reset: false });
	}
	const eras = $derived(items.filter((item) => item.kind === 'era'));
	const episodes = $derived(items.filter((item) => item.kind === 'episode'));

	// Brak historii albo trwający import: dane pobiera strona profilu, więc odsyłamy tam
	const needsProfile = (code: string) => code === 'not_imported' || code === 'import_in_progress';

	function toggle(key: string) {
		selected = selected === key ? null : key;
	}

	// Klik na osi rozwija erę na liście; przewijamy tylko tyle, żeby było ją widać
	async function selectFromTimeline(key: string) {
		selected = key;
		await tick();
		document.getElementById(`era-${key}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
	}

	function itemName(item: EraItem): string {
		if (item.genre) return item.kind === 'era' ? `Okres ${item.genre.tag}` : item.genre.tag;
		return item.kind === 'era' ? `Era ${item.era.artist_name}` : item.era.artist_name;
	}
</script>

<svelte:head>
	<title>Ery · {nick} · Erafm</title>
</svelte:head>

{#snippet eraList(list: EraItem[])}
	<ol class="era-list">
		{#each list as item (item.key)}
			{@const era = item.era}
			<li id="era-{item.key}" class:open={item.key === selected}>
				<button
					type="button"
					class="era-row {item.kind}"
					aria-expanded={item.key === selected}
					onclick={() => toggle(item.key)}
				>
					<span class="swatch" style:background={item.color}></span>
					<span class="name">{itemName(item)}</span>
					<span class="when muted">{formatMonthRange(era.start, era.end)}</span>
				</button>
				{#if item.key === selected && item.genre}
					{@const genre = item.genre}
					<dl class="details">
						<div>
							<dt>Okres</dt>
							<dd>{formatDay(genre.start)} – {formatDay(genre.end)}</dd>
						</div>
						<div>
							<dt>Odsłuchy tego gatunku</dt>
							<dd>
								ok. {formatPlays(genre.plays)}, czyli {formatPercent(genre.share)} wszystkich w tym czasie
							</dd>
						</div>
						<div>
							<dt>Artyści tego gatunku w tym czasie</dt>
							<dd class="tracks">
								<TopList
									items={genre.top_artists.map((artist) => ({
										id: artist.artist_id,
										name: artist.name,
										plays: artist.plays
									}))}
								/>
							</dd>
						</div>
					</dl>
				{:else if item.key === selected}
					<dl class="details">
						<div>
							<dt>Okres</dt>
							<dd>{formatDay(era.start)} – {formatDay(era.end)}</dd>
						</div>
						<div>
							<dt>Odsłuchy</dt>
							<dd>
								{formatPlays(era.plays)}, czyli {formatPercent(era.share)} wszystkich w tym czasie
							</dd>
						</div>
						<div>
							<dt>Pierwszy i ostatni odsłuch w tym czasie</dt>
							<dd>{formatDate(era.first_play_at)} · {formatDate(era.last_play_at)}</dd>
						</div>
						<div>
							<dt>Od czego się zaczęło</dt>
							<dd>
								{era.artist_first.track_name}, {formatDate(era.artist_first.played_at)}
								<span class="muted">
									{#if Date.parse(era.artist_first.played_at) >= Date.parse(era.first_play_at)}
										(pierwszy odsłuch otworzył tę erę)
									{:else}
										({formatDuration(era.artist_first.played_at, era.first_play_at)} przed tym okresem)
									{/if}
								</span>
							</dd>
						</div>
						<div>
							<dt>Najczęściej grane w tym czasie</dt>
							<dd class="tracks">
								<TopList
									items={era.top_tracks.map((track, i) => ({
										id: i,
										name: track.name,
										plays: track.plays
									}))}
								/>
							</dd>
						</div>
					</dl>
				{/if}
			</li>
		{/each}
	</ol>
{/snippet}

<section>
	<h1>{nick}</h1>
	<p class="lead">
		{mode === 'genres'
			? 'Okresy, w których jeden gatunek wyraźnie przeważał nad resztą.'
			: 'Okresy, w których jeden artysta wyraźnie przeważał nad resztą.'}
	</p>
	<div class="modes" role="group" aria-label="Rodzaj er">
		<button type="button" aria-pressed={mode === 'artists'} onclick={() => showMode('artists')}
			>Artyści</button
		>
		<button type="button" aria-pressed={mode === 'genres'} onclick={() => showMode('genres')}
			>Gatunki</button
		>
	</div>
</section>

{#if view.kind === 'loading'}
	<p class="muted" role="status">Ładowanie…</p>
{:else if view.kind === 'error'}
	<section class="problem">
		<p class="lead" role="alert">{errorMessage(view.code)}</p>
		<div class="actions">
			{#if needsProfile(view.code)}
				<a class="button" href={resolve('/u/[nick]', { nick })}
					>Otwórz profil, żeby pobrać historię</a
				>
			{:else if canRetry(view.code)}
				<button class="button" type="button" onclick={() => attempt++}>Spróbuj ponownie</button>
			{/if}
		</div>
	</section>
{:else if view.data.start === null || view.data.end === null}
	<p class="muted">To konto nie ma jeszcze żadnych odsłuchów na Last.fm.</p>
{:else}
	<section>
		<EraTimeline
			{items}
			silences={view.data.silences}
			{streams}
			{weeks}
			start={view.data.start}
			end={view.data.end}
			{selected}
			onselect={selectFromTimeline}
		/>
		<ul class="legend muted">
			<li><span class="key era"></span>era: co najmniej 4 tygodnie</li>
			<li><span class="key episode"></span>epizod: 2–3 tygodnie</li>
			<li><span class="key silence"></span>cisza: prawie bez słuchania</li>
		</ul>
	</section>

	{#if items.length === 0}
		<p class="muted">
			Nie widać tu wyraźnych er: słuchanie rozkłada się równo między wiele
			{mode === 'genres' ? 'gatunków' : 'artystów'}.
		</p>
	{/if}

	{#if eras.length > 0}
		<section>
			<h2>Ery</h2>
			{@render eraList(eras)}
		</section>
	{/if}

	{#if episodes.length > 0}
		<section>
			<h2>Krótsze epizody</h2>
			{@render eraList(episodes)}
		</section>
	{/if}
{/if}

<style>
	section {
		padding-top: var(--space-5);
	}

	h1 {
		font-size: clamp(2rem, 6vw, 3rem);
		overflow-wrap: anywhere;
	}

	h2 {
		margin-bottom: var(--space-4);
		font-size: 1.375rem;
	}

	.modes {
		display: inline-flex;
		margin-top: var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		overflow: hidden;
	}

	.modes button {
		padding: var(--space-2) var(--space-5);
		border: none;
		background: var(--surface);
		cursor: pointer;
	}

	.modes button + button {
		border-left: 1px solid var(--border);
	}

	.modes button[aria-pressed='true'] {
		background: var(--accent);
		color: var(--on-accent);
		font-weight: 600;
	}

	.lead {
		margin-top: var(--space-3);
		font-size: 1.125rem;
	}

	.problem {
		max-width: 36rem;
	}

	.actions {
		margin-top: var(--space-5);
	}

	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2) var(--space-5);
		margin: var(--space-3) 0 0;
		padding: 0;
		list-style: none;
		font-size: 0.875rem;
	}

	.legend li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.key {
		display: inline-block;
		width: 1.25rem;
		border-radius: 3px;
	}

	.key.era {
		height: 0.875rem;
		background: var(--era-1);
	}

	.key.episode {
		height: 0.4rem;
		background: var(--era-1);
		opacity: 0.6;
	}

	.key.silence {
		height: 0.875rem;
		background: repeating-linear-gradient(45deg, var(--text-muted) 0 1.5px, transparent 1.5px 4px);
		opacity: 0.6;
	}

	.era-list {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.era-list li {
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		scroll-margin: var(--space-5);
	}

	.era-list li.open {
		border-color: var(--text-muted);
	}

	.era-row {
		display: grid;
		grid-template-columns: auto 1fr auto;
		align-items: center;
		gap: var(--space-3);
		width: 100%;
		padding: var(--space-3) var(--space-4);
		border: none;
		background: none;
		text-align: left;
		cursor: pointer;
	}

	.swatch {
		width: 0.875rem;
		height: 0.875rem;
		border-radius: 3px;
	}

	.episode .swatch {
		opacity: 0.6;
	}

	.name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.when {
		font-size: 0.875rem;
		text-align: right;
	}

	.details {
		display: grid;
		gap: var(--space-3);
		margin: 0;
		padding: 0 var(--space-4) var(--space-4) calc(var(--space-4) + 0.875rem + var(--space-3));
	}

	dt {
		color: var(--text-muted);
		font-size: 0.875rem;
	}

	dd {
		margin: 0;
	}

	@media (max-width: 30rem) {
		.era-row {
			grid-template-columns: auto 1fr;
		}

		.when {
			grid-column: 2;
			text-align: left;
		}
	}
</style>
