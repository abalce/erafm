<script lang="ts">
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import {
		ApiError,
		getDuet,
		getEras,
		type Duet,
		type InfectionSide,
		type UserEras
	} from '#lib/api.ts';
	import CompatChart from '#lib/CompatChart.svelte';
	import DuetEras from '#lib/DuetEras.svelte';
	import { duetColors, eraItems } from '#lib/eras.ts';
	import { formatCount, formatDay, formatPercent, formatPlays } from '#lib/format.ts';
	import { watchImport, type ImportState } from '#lib/importWatch.ts';
	import { canRetry, errorMessage } from '#lib/messages.ts';
	import TopList from '#lib/TopList.svelte';
	import { isValidUsername } from '#lib/username.ts';

	// Tyle zarażeń widać od razu; resztę pokazuje przycisk
	const SHOWN_INFECTIONS = 10;

	// Para żyje w adresie: /duet?u=nick1,nick2; pierwsza osoba to „ty”
	const nicks = $derived(
		(page.url.searchParams.get('u') ?? '')
			.split(',')
			.map((nick) => nick.trim().toLowerCase())
			.filter(Boolean)
	);
	const pair = $derived(
		nicks.length === 2 && nicks[0] !== nicks[1] && nicks.every(isValidUsername)
			? ([nicks[0], nicks[1]] as const)
			: null
	);

	// Formularz startuje od nicków z adresu, np. /duet?u=solejuk2115 z linku na profilu
	let first = $state('');
	let second = $state('');
	let formError = $state('');
	$effect(() => {
		first = nicks[0] ?? '';
		second = nicks[1] ?? '';
	});

	function compare(event: SubmitEvent) {
		event.preventDefault();
		const a = first.trim().toLowerCase();
		const b = second.trim().toLowerCase();
		if (!isValidUsername(a) || !isValidUsername(b)) {
			formError = 'Wpisz dwa nicki bez spacji i ukośników.';
		} else if (a === b) {
			formError = 'Wpisz dwie różne osoby.';
		} else {
			// resolve() zna tylko ścieżki, a para żyje w parametrze ?u=, więc dokładamy go ręcznie
			// eslint-disable-next-line svelte/no-navigation-without-resolve
			goto(`${resolve('/duet')}?u=${encodeURIComponent(a)},${encodeURIComponent(b)}`);
		}
	}

	let people = $state<Record<string, ImportState>>({});
	let duet = $state<Duet | null>(null);
	let duetError = $state<string | null>(null);
	// Ery obu osób do wspólnej osi; to dodatek, więc błąd po prostu ją ukrywa
	let eras = $state<UserEras[] | null>(null);
	let attempt = $state(0);
	let expanded = $state<Record<string, boolean>>({});

	$effect(() => {
		const current = pair;
		void attempt;
		people = {};
		duet = null;
		duetError = null;
		eras = null;
		expanded = {};
		if (!current) return;

		let cancelled = false;
		let request = 0;
		const states: Record<string, ImportState> = {};

		// Duet liczymy, gdy historie obu osób są w bazie; po zakończeniu odświeżania liczymy
		// jeszcze raz, żeby objąć nowe odsłuchy
		async function load() {
			const mine = ++request;
			try {
				const result = await getDuet(current![0], current![1]);
				if (!cancelled && mine === request) duet = result;
				const both = await Promise.all(current!.map((nick) => getEras(nick)));
				if (!cancelled && mine === request) eras = both;
			} catch (error) {
				if (cancelled || mine !== request || duet) return;
				duetError = error instanceof ApiError ? error.code : 'unknown_error';
			}
		}

		const stops = current.map((nick) =>
			watchImport(nick, (state) => {
				states[nick] = state;
				people = { ...states };
				if (state.kind === 'ready' && current.every((n) => states[n]?.kind === 'ready')) {
					void load();
				}
			})
		);

		return () => {
			cancelled = true;
			stops.forEach((stop) => stop());
		};
	});

	const waiting = $derived(
		pair !== null && !duet && !pair.every((nick) => people[nick]?.kind === 'ready')
	);
	// Osoby, których historii nie da się pobrać (np. nie ma takiego konta)
	const failed = $derived(pair ? pair.filter((nick) => people[nick]?.kind === 'error') : []);
	const eraRows = $derived.by(() => {
		if (!eras) return null;
		const withHistory = eras.filter((person) => person.start !== null && person.end !== null);
		if (withHistory.length < 2) return null;
		const colors = duetColors(withHistory);
		return {
			rows: withHistory.map((person) => ({
				nick: person.username,
				items: eraItems(person, colors),
				silences: person.silences,
				start: person.start!
			})),
			start: withHistory.map((person) => person.start!).sort()[0],
			end: withHistory.map((person) => person.end!).sort()[withHistory.length - 1]
		};
	});
	const bestYear = $derived(
		duet && duet.compatibility.years.length > 1
			? duet.compatibility.years.reduce((best, y) => (y.similarity > best.similarity ? y : best))
			: null
	);
	const currentYear = new Date().getFullYear();
	const maxBalance = $derived(
		duet ? Math.max(...duet.infections.sides.map((side) => side.balance), 0.0001) : 1
	);

	// Widok jednego artysty w tym duecie
	function artistHref(artistId: number): string {
		const nicks = pair ? pair.map(encodeURIComponent).join(',') : '';
		return `${resolve('/duet/artysta')}?u=${nicks}&id=${artistId}`;
	}

	function sortedInfections(side: InfectionSide) {
		return [...side.infections].sort(
			(a, b) => b.target_plays - a.target_plays || a.artist_id - b.artist_id
		);
	}
</script>

<svelte:head>
	<title>{pair ? `${pair[0]} i ${pair[1]}` : 'Duet'} · Erafm</title>
</svelte:head>

{#snippet personStatus(nick: string)}
	{@const state = people[nick] ?? { kind: 'loading' }}
	<li>
		<span class="who">{nick}</span>
		{#if state.kind === 'loading'}
			<span class="muted">Sprawdzamy historię…</span>
		{:else if state.kind === 'importing'}
			<span class="progress-row">
				<progress max="100" value={state.progress} aria-label="Postęp importu: {nick}"></progress>
				<span class="percent">{state.progress}%</span>
			</span>
		{:else if state.kind === 'ready'}
			<span class="muted">Historia gotowa</span>
		{:else}
			<span class="error" role="alert">{errorMessage(state.code)}</span>
		{/if}
	</li>
{/snippet}

{#snippet infectionColumn(side: InfectionSide)}
	{@const list = sortedInfections(side)}
	{@const open = expanded[side.source] ?? false}
	<section class="column" aria-labelledby="side-{side.source}">
		<h3 id="side-{side.source}">{side.source} → {side.target}</h3>
		<p class="muted">
			prawdopodobnie zaraził(a) {formatCount(list.length, ['artystą', 'artystami', 'artystami'])}
		</p>
		{#if list.length === 0}
			<p class="empty muted">W tę stronę nic nie wyszło.</p>
		{:else}
			<TopList
				items={(open ? list : list.slice(0, SHOWN_INFECTIONS)).map((infection) => ({
					id: infection.artist_id,
					name: infection.artist_name,
					href: artistHref(infection.artist_id),
					detail:
						`${side.target} od ${formatDay(infection.target_first_at.slice(0, 10))} · ` +
						`${side.source}: ${formatPlays(infection.source_plays)} w 90 dniach przed`,
					plays: infection.target_plays
				}))}
			/>
			{#if list.length > SHOWN_INFECTIONS}
				<button
					class="more"
					type="button"
					onclick={() => (expanded = { ...expanded, [side.source]: !open })}
				>
					{open ? 'Pokaż mniej' : `Pokaż wszystkie (${list.length})`}
				</button>
			{/if}
		{/if}
	</section>
{/snippet}

{#if !pair}
	<section class="intro">
		<h1>Duet</h1>
		<p class="lead">
			Jak bardzo pasują do siebie gusta dwóch osób i kto komu prawdopodobnie podrzucił artystów.
		</p>
		<form onsubmit={compare} novalidate>
			<div class="fields">
				<label>
					<span>Pierwsza osoba</span>
					<input
						type="text"
						autocomplete="off"
						autocapitalize="none"
						spellcheck="false"
						placeholder="nick Last.fm"
						bind:value={first}
						oninput={() => (formError = '')}
					/>
				</label>
				<label>
					<span>Druga osoba</span>
					<input
						type="text"
						autocomplete="off"
						autocapitalize="none"
						spellcheck="false"
						placeholder="nick Last.fm"
						bind:value={second}
						oninput={() => (formError = '')}
					/>
				</label>
			</div>
			<button class="button" type="submit">Porównaj</button>
			{#if formError}
				<p class="error" role="alert">{formError}</p>
			{:else if nicks.length > 0 && nicks.length !== 1}
				<p class="error" role="alert">{errorMessage('invalid_duet')}</p>
			{/if}
		</form>
	</section>
{:else}
	<section class="intro">
		<h1>{pair[0]} i {pair[1]}</h1>
		{#if waiting}
			{#if failed.length > 0}
				<p class="lead">Tej pary nie da się porównać.</p>
			{:else}
				<p class="lead">Pobieramy historie obu osób. Pierwszy import trwa zwykle kilka minut.</p>
			{/if}
			<ul class="people">
				{#each pair as nick (nick)}
					{@render personStatus(nick)}
				{/each}
			</ul>
			{#if failed.length > 0}
				<div class="actions">
					{#if failed.some( (nick) => canRetry(people[nick].kind === 'error' ? people[nick].code : '') )}
						<button class="button" type="button" onclick={() => attempt++}>Spróbuj ponownie</button>
					{/if}
					<!-- formularz z nickami, które dały się wczytać -->
					<a
						href="{resolve('/duet')}?u={pair
							.filter((nick) => !failed.includes(nick))
							.map(encodeURIComponent)
							.join(',')}">Zmień osoby</a
					>
				</div>
			{/if}
		{/if}
		{#if duetError}
			<p class="lead error" role="alert">{errorMessage(duetError)}</p>
			{#if canRetry(duetError)}
				<button class="button" type="button" onclick={() => attempt++}>Spróbuj ponownie</button>
			{/if}
		{/if}
	</section>

	{#if duet}
		{@const compat = duet.compatibility}
		{@const [ab, ba] = duet.infections.sides}
		<section>
			<p class="score">{formatPercent(compat.similarity)}</p>
			<p class="lead">
				zgodności gustów
				{#if compat.since}od tygodnia {formatDay(compat.since)}{/if}
				· {formatCount(compat.shared_artists, [
					'wspólny artysta',
					'wspólni artyści',
					'wspólnych artystów'
				])}
			</p>
			{#if compat.years.length > 0}
				<h2>Rok po roku</h2>
				{#if bestYear}
					<p class="muted">
						Najbliżej było w {bestYear.year}: {formatPercent(bestYear.similarity)}.
					</p>
				{/if}
				<CompatChart years={compat.years} {currentYear} />
			{/if}
		</section>

		{#if eraRows}
			<section>
				<h2>Ery obu osób</h2>
				<p class="muted">
					Okresy, w których jeden artysta wyraźnie przeważał. Ten sam artysta ma ten sam kolor u obu
					osób.
				</p>
				<DuetEras rows={eraRows.rows} start={eraRows.start} end={eraRows.end} />
				<p class="links">
					{#each eraRows.rows as row (row.nick)}
						<a href={resolve('/u/[nick]/ery', { nick: row.nick })}>Ery {row.nick}</a>
					{/each}
				</p>
			</section>
		{/if}

		<section>
			<h2>Kto komu podrzucił artystów</h2>
			<p class="muted note">
				Zarażenie to artysta, którego jedna osoba słuchała tuż przed tym, jak druga zaczęła go
				słuchać i została fanem. To zbieżność w czasie, a nie dowód, stąd „prawdopodobnie”. Bilans
				waży mniej znanych artystów mocniej niż tych z radia.
			</p>
			<div class="balance" aria-label="Bilans zarażeń">
				{#each [ab, ba] as side (side.source)}
					<div class="balance-row">
						<span class="who">{side.source}</span>
						<span class="bar-track">
							<span class="bar" style:width="{(side.balance / maxBalance) * 100}%"></span>
						</span>
						<span class="value">{side.balance.toFixed(2).replace('.', ',')}</span>
					</div>
				{/each}
			</div>
			<div class="columns">
				{@render infectionColumn(ab)}
				{@render infectionColumn(ba)}
			</div>
		</section>

		{#if compat.connecting.length > 0}
			<section>
				<h2>Wspólni ulubieni</h2>
				<p class="muted">
					Najbardziej łączy {pair[0]} i {pair[1]}:
					<strong class="highlight">{compat.connecting[0].name}</strong>.
				</p>
				<TopList
					items={compat.connecting.map((artist) => ({
						id: artist.artist_id,
						name: artist.name,
						href: artistHref(artist.artist_id),
						detail: `${pair[0]}: ${formatPlays(artist.plays_first)} · ${pair[1]}: ${formatPlays(artist.plays_second)}`,
						plays: artist.plays_first + artist.plays_second
					}))}
				/>
			</section>
		{/if}

		<section>
			<h2>Sporni</h2>
			<p class="muted">Artyści, których jedna osoba słucha dużo, a druga prawie wcale.</p>
			<div class="columns">
				{#each [{ owner: pair[0], other: pair[1], list: compat.disputed_first }, { owner: pair[1], other: pair[0], list: compat.disputed_second }] as group (group.owner)}
					<section class="column">
						<h3>Tylko {group.owner}</h3>
						{#if group.list.length === 0}
							<p class="empty muted">Brak takich artystów.</p>
						{:else}
							<TopList
								items={group.list.map((artist) => ({
									id: artist.artist_id,
									name: artist.name,
									href: artistHref(artist.artist_id),
									detail: `${group.other}: ${formatPlays(artist.other_plays)}`,
									plays: artist.plays
								}))}
							/>
						{/if}
					</section>
				{/each}
			</div>
		</section>

		<p class="again">
			<a href="{resolve('/duet')}?u={encodeURIComponent(pair[0])}">Porównaj inne osoby</a>
		</p>
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
		margin: var(--space-5) 0 var(--space-3);
		font-size: 1.375rem;
	}

	h3 {
		font-size: 1.125rem;
		overflow-wrap: anywhere;
	}

	.lead {
		margin-top: var(--space-3);
		font-size: 1.125rem;
	}

	.intro {
		max-width: 40rem;
	}

	form {
		margin-top: var(--space-5);
	}

	.fields {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 14rem), 1fr));
		gap: var(--space-3);
		margin-bottom: var(--space-4);
	}

	label {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		font-size: 0.875rem;
		font-weight: 600;
	}

	input {
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		background: var(--surface);
		font-weight: 400;
	}

	.error {
		margin-top: var(--space-2);
		color: var(--danger);
	}

	.people {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: var(--space-5) 0 0;
		padding: 0;
		list-style: none;
	}

	.people li {
		display: grid;
		grid-template-columns: minmax(8rem, auto) 1fr;
		align-items: center;
		gap: var(--space-3);
	}

	.who {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	.progress-row {
		display: flex;
		align-items: center;
		gap: var(--space-3);
	}

	progress {
		flex: 1;
		height: 0.625rem;
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
	}

	progress::-moz-progress-bar {
		background: var(--accent);
		border-radius: 999px;
	}

	.percent {
		min-width: 3.5ch;
		font-variant-numeric: tabular-nums;
		text-align: right;
	}

	.score {
		font-family: var(--font-display);
		font-size: clamp(3rem, 12vw, 5rem);
		font-weight: 600;
		line-height: 1;
		color: var(--accent);
	}

	.note {
		max-width: 46rem;
		margin-bottom: var(--space-4);
	}

	.balance {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		margin-bottom: var(--space-4);
	}

	.balance-row {
		display: grid;
		grid-template-columns: minmax(7rem, 10rem) 1fr 3.5rem;
		align-items: center;
		gap: var(--space-3);
	}

	.bar-track {
		height: 0.75rem;
		border-radius: 999px;
		background: var(--surface-raised);
		overflow: hidden;
	}

	.bar {
		display: block;
		height: 100%;
		border-radius: 999px;
		background: var(--accent);
	}

	.value {
		font-variant-numeric: tabular-nums;
		text-align: right;
	}

	.columns {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 20rem), 1fr));
		gap: 0 var(--space-6);
	}

	.column {
		padding-top: var(--space-4);
	}

	.empty {
		margin-top: var(--space-3);
	}

	.more {
		margin-top: var(--space-3);
		padding: var(--space-2) var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		background: var(--surface);
		cursor: pointer;
	}

	.more:hover {
		border-color: var(--accent);
	}

	.highlight {
		color: var(--text);
	}

	.actions {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: var(--space-5);
		margin-top: var(--space-5);
	}

	.links {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-5);
	}

	.again {
		margin-top: var(--space-6);
	}
</style>
