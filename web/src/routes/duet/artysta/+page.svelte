<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import {
		ApiError,
		getArtist,
		getDuet,
		type ArtistHistory,
		type Infection,
		type InfectionSide
	} from '#lib/api.ts';
	import ArtistMirror from '#lib/ArtistMirror.svelte';
	import { formatDate, formatNumber, formatPlays } from '#lib/format.ts';
	import { canRetry, errorMessage } from '#lib/messages.ts';
	import { isValidUsername } from '#lib/username.ts';

	// /duet/artysta?u=nick1,nick2&id=123: para jak na stronie duetu i id artysty
	const nicks = $derived(
		(page.url.searchParams.get('u') ?? '')
			.split(',')
			.map((nick) => nick.trim().toLowerCase())
			.filter(Boolean)
	);
	const artistId = $derived(Number(page.url.searchParams.get('id')));
	const pair = $derived(
		nicks.length === 2 && nicks[0] !== nicks[1] && nicks.every(isValidUsername)
			? ([nicks[0], nicks[1]] as const)
			: null
	);

	type View =
		| { kind: 'loading' }
		| {
				kind: 'ready';
				people: [ArtistHistory, ArtistHistory];
				infection: { side: InfectionSide; infection: Infection } | null;
		  }
		| { kind: 'error'; code: string };

	let view = $state<View>({ kind: 'loading' });
	let attempt = $state(0);

	$effect(() => {
		const current = pair;
		const id = artistId;
		void attempt;
		if (!current) {
			view = { kind: 'error', code: 'invalid_duet' };
			return;
		}
		if (!Number.isInteger(id) || id <= 0) {
			view = { kind: 'error', code: 'artist_not_found' };
			return;
		}
		let cancelled = false;
		view = { kind: 'loading' };

		// Zarażenie bierzemy z duetu, żeby reguła żyła w jednym miejscu; bez niego widok
		// i tak ma sens, więc błąd duetu go nie blokuje
		Promise.all([
			getArtist(current[0], id),
			getArtist(current[1], id),
			getDuet(current[0], current[1]).catch(() => null)
		])
			.then(([first, second, duet]) => {
				if (cancelled) return;
				let infection = null;
				for (const side of duet?.infections.sides ?? []) {
					const found = side.infections.find((i) => i.artist_id === id);
					if (found) infection = { side, infection: found };
				}
				view = { kind: 'ready', people: [first, second], infection };
			})
			.catch((error: unknown) => {
				if (!cancelled)
					view = { kind: 'error', code: error instanceof ApiError ? error.code : 'unknown_error' };
			});

		return () => {
			cancelled = true;
		};
	});
</script>

<svelte:head>
	<title>{view.kind === 'ready' ? `${view.people[0].name} · ` : ''}Duet · Erafm</title>
</svelte:head>

<p class="back">
	<a href="{resolve('/duet')}?u={pair ? pair.map(encodeURIComponent).join(',') : ''}"
		>← {pair ? `Duet ${pair[0]} i ${pair[1]}` : 'Duet'}</a
	>
</p>

{#if view.kind === 'loading'}
	<p class="muted" role="status">Ładowanie…</p>
{:else if view.kind === 'error'}
	<section class="problem">
		<p class="lead" role="alert">{errorMessage(view.code)}</p>
		{#if canRetry(view.code)}
			<button class="button" type="button" onclick={() => attempt++}>Spróbuj ponownie</button>
		{/if}
	</section>
{:else}
	{@const artist = view.people[0]}
	<section>
		<h1>{artist.name}</h1>
		{#if artist.listeners !== null}
			<p class="muted">{formatNumber(artist.listeners)} słuchaczy na Last.fm</p>
		{/if}
		{#if view.infection}
			{@const { side, infection } = view.infection}
			<p class="lead">
				{side.source} prawdopodobnie zaraził(a) {side.target}: {formatPlays(infection.source_plays)}
				w 90 dniach przed pierwszym odsłuchem {side.target}.
			</p>
		{/if}
	</section>

	<section>
		<ul class="people">
			{#each view.people as person (person.username)}
				<li>
					<h2>{person.username}</h2>
					{#if person.first_at}
						<p>
							Pierwszy odsłuch: {formatDate(person.first_at)}{#if person.first_track}, „{person.first_track}”{/if}
						</p>
						<p class="muted">Razem {formatPlays(person.total_plays)}</p>
					{:else}
						<p class="muted">Nie słucha tego artysty.</p>
					{/if}
				</li>
			{/each}
		</ul>
	</section>

	<section>
		<h2>Tydzień po tygodniu</h2>
		<ArtistMirror people={view.people} />
	</section>
{/if}

<style>
	section {
		padding-top: var(--space-5);
	}

	.back {
		margin-top: var(--space-3);
	}

	h1 {
		font-size: clamp(2rem, 6vw, 3rem);
		overflow-wrap: anywhere;
	}

	h2 {
		margin-bottom: var(--space-3);
		font-size: 1.25rem;
		overflow-wrap: anywhere;
	}

	.lead {
		margin-top: var(--space-3);
		font-size: 1.125rem;
	}

	.problem {
		max-width: 36rem;
	}

	.people {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 16rem), 1fr));
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.people li {
		padding: var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}

	.people p + p {
		margin-top: var(--space-1);
	}
</style>
