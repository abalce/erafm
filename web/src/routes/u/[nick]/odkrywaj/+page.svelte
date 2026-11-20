<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import { ApiError, getRecommendations, type Popularity, type Recommendations } from '#lib/api.ts';
	import { formatList, formatListeners } from '#lib/format.ts';
	import { canRetry, errorMessage } from '#lib/messages.ts';

	// Co ile pytamy ponownie, gdy worker jeszcze pobiera podobnych artystów
	const POLL_MS = 3000;

	// Pozycje suwaka: polska nazwa w adresie, wartość w API i opis pasma
	const POSITIONS: { slug: string; value: Popularity; label: string; hint: string }[] = [
		{ slug: 'znani', value: 'known', label: 'Znani', hint: 'ponad 1 mln słuchaczy na Last.fm' },
		{ slug: 'wszyscy', value: 'all', label: 'Wszyscy', hint: 'bez względu na popularność' },
		{
			slug: 'mniej-znani',
			value: 'lesser',
			label: 'Mniej znani',
			hint: 'do 300 tys. słuchaczy na Last.fm'
		},
		{ slug: 'nisza', value: 'niche', label: 'Nisza', hint: 'do 100 tys. słuchaczy na Last.fm' }
	];

	const nick = $derived(page.params.nick ?? '');
	// Wybór żyje w adresie: link można komuś wysłać, a „wstecz” cofa zmianę filtra
	const position = $derived(
		POSITIONS.find((p) => p.slug === page.url.searchParams.get('popularnosc')) ?? POSITIONS[1]
	);
	const tag = $derived(page.url.searchParams.get('tag')?.trim().toLowerCase() || null);

	type View =
		| { kind: 'loading' }
		| { kind: 'ready'; data: Recommendations; refreshing: boolean }
		| { kind: 'error'; code: string };

	let view = $state<View>({ kind: 'loading' });
	let attempt = $state(0);

	$effect(() => {
		const current = nick;
		const popularity = position.value;
		const currentTag = tag;
		void attempt;
		let cancelled = false;
		let timer: ReturnType<typeof setTimeout> | undefined;
		// Przy zmianie filtra stara lista zostaje na ekranie, przygaszona, do nowej odpowiedzi.
		// untrack: odczyt bieżącego widoku nie może uruchamiać efektu od nowa
		untrack(() => {
			view = view.kind === 'ready' ? { ...view, refreshing: true } : { kind: 'loading' };
		});

		async function load() {
			try {
				const data = await getRecommendations(current, popularity, currentTag);
				if (cancelled) return;
				view = { kind: 'ready', data, refreshing: false };
				// Worker dociąga podobnych artystów: lista dojdzie sama
				if (data.pending > 0) timer = setTimeout(load, POLL_MS);
			} catch (error) {
				if (cancelled) return;
				view = { kind: 'error', code: error instanceof ApiError ? error.code : 'unknown_error' };
			}
		}
		void load();

		return () => {
			cancelled = true;
			clearTimeout(timer);
		};
	});

	const needsProfile = (code: string) => code === 'not_imported' || code === 'import_in_progress';

	function choose(slug: string, nextTag: string | null) {
		const parts = [];
		if (slug !== 'wszyscy') parts.push(`popularnosc=${slug}`);
		if (nextTag) parts.push(`tag=${encodeURIComponent(nextTag)}`);
		const suffix = parts.length > 0 ? `?${parts.join('&')}` : '';
		// resolve() zna tylko ścieżki, a wybór żyje w parametrach adresu, więc dokładamy je ręcznie;
		// reset: false zostawia przewinięcie i fokus na klikniętym przycisku
		// eslint-disable-next-line svelte/no-navigation-without-resolve
		goto(`${resolve('/u/[nick]/odkrywaj', { nick })}${suffix}`, { reset: false });
	}

	const youtube = (name: string) =>
		`https://www.youtube.com/results?search_query=${encodeURIComponent(name)}`;
</script>

<svelte:head>
	<title>Odkrywaj · {nick} · Erafm</title>
</svelte:head>

<section>
	<h1>{nick}</h1>
	<p class="lead">Nowi artyści podobni do tego, czego ta osoba słucha ostatnio.</p>
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
{:else}
	{@const data = view.data}
	<section class="controls">
		<div class="positions" role="group" aria-label="Popularność artystów">
			{#each POSITIONS as option (option.slug)}
				<button
					type="button"
					aria-pressed={option.slug === position.slug}
					onclick={() => choose(option.slug, tag)}>{option.label}</button
				>
			{/each}
		</div>
		<p class="muted hint">{position.hint}</p>

		{#if data.tags.length > 0 || tag}
			<div class="tags" role="group" aria-label="Gatunek">
				<button
					type="button"
					aria-pressed={tag === null}
					onclick={() => choose(position.slug, null)}>Wszystkie gatunki</button
				>
				{#each tag && !data.tags.includes(tag) ? [tag, ...data.tags] : data.tags as name (name)}
					<button
						type="button"
						aria-pressed={name === tag}
						onclick={() => choose(position.slug, name === tag ? null : name)}>{name}</button
					>
				{/each}
			</div>
		{/if}
	</section>

	{#if data.pending > 0}
		<p class="muted" role="status">
			Szukamy podobnych do {data.pending} z {data.seeds} ostatnio słuchanych artystów. Lista uzupełni
			się sama.
		</p>
	{/if}

	<section class:refreshing={view.refreshing} aria-busy={view.refreshing}>
		{#if data.items.length > 0}
			<ol class="items">
				{#each data.items as item, i (item.artist_id)}
					<li>
						<span class="rank">{i + 1}</span>
						<div class="text">
							<p class="name">{item.name}</p>
							<p class="muted">
								{#if item.listeners !== null}
									<span>{formatListeners(item.listeners)}</span>
									<span aria-hidden="true">·</span>
								{/if}
								<span>podobny do: {formatList(item.because)}</span>
							</p>
						</div>
						<a
							class="listen"
							href={youtube(item.name)}
							target="_blank"
							rel="external noopener noreferrer">Posłuchaj</a
						>
					</li>
				{/each}
			</ol>
		{:else if data.pending === 0}
			<p class="lead">Nic nie pasuje do tych filtrów.</p>
			{#if data.missing_data > 0}
				<p class="muted">
					Części artystów brakuje jeszcze danych z Last.fm (liczby słuchaczy i tagów). Zajrzyj za
					kwadrans.
				</p>
			{/if}
		{/if}
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

	.problem {
		max-width: 36rem;
	}

	.actions {
		margin-top: var(--space-5);
	}

	/* cztery równe pola w jednym rzędzie, także na telefonie */
	.positions {
		display: grid;
		grid-template-columns: repeat(4, 1fr);
		max-width: 32rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		overflow: hidden;
	}

	.positions button {
		padding: var(--space-2) var(--space-1);
		border: none;
		border-right: 1px solid var(--border);
		background: var(--surface);
		font-size: 0.9375rem;
		white-space: nowrap;
		cursor: pointer;
	}

	.positions button:last-child {
		border-right: none;
	}

	.positions button[aria-pressed='true'] {
		background: var(--accent);
		color: var(--on-accent);
		font-weight: 600;
	}

	.hint {
		margin-top: var(--space-2);
		font-size: 0.875rem;
	}

	.tags {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2);
		margin-top: var(--space-4);
	}

	.tags button {
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--border);
		border-radius: 999px;
		background: var(--surface);
		cursor: pointer;
	}

	.tags button:hover,
	.positions button:hover {
		border-color: var(--accent);
	}

	.tags button[aria-pressed='true'] {
		border-color: var(--accent);
		color: var(--accent-strong);
		font-weight: 600;
	}

	.refreshing {
		opacity: 0.5;
		transition: opacity 0.2s ease;
	}

	.items {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.items li {
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-3) 0;
		border-bottom: 1px solid var(--border);
	}

	.rank {
		flex: none;
		width: 1.75rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
		text-align: right;
	}

	.text {
		flex: 1;
		min-width: 0;
	}

	.name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.text .muted {
		font-size: 0.875rem;
		overflow-wrap: anywhere;
	}

	.listen {
		flex: none;
		font-size: 0.875rem;
	}
</style>
