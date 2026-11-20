<script lang="ts">
	import { getToday, type OnThisDay } from '#lib/api.ts';
	import { formatDate, formatDayMonth, formatPlays, formatYearsAgo } from '#lib/format.ts';

	let { nick, date = null }: { nick: string; date?: string | null } = $props();

	// Karta jest dodatkiem: przy błędzie albo braku danych po prostu jej nie ma
	let data: OnThisDay | null = $state(null);

	$effect(() => {
		let cancelled = false;
		data = null;
		getToday(nick, date)
			.then((result) => {
				if (!cancelled) data = result;
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	});
</script>

{#if data && (data.years.length > 0 || data.anniversaries.length > 0)}
	<section class="today" aria-labelledby="today-title">
		<h2 id="today-title">Tego dnia · {formatDayMonth(data.date)}</h2>

		{#if data.years.length > 0}
			<ol class="years">
				{#each data.years as year (year.year)}
					<li>
						<p class="when">
							<span>{formatYearsAgo(year.years_ago)}</span>
							<span class="muted">{year.year}</span>
						</p>
						<p>
							<strong>{formatPlays(year.plays)}</strong>, najwięcej:
							{year.top_artists.map((artist) => artist.name).join(', ')}
						</p>
						<p class="muted">
							Najczęściej „{year.top_track.name}” ({year.top_track.artist_name}),
							{formatPlays(year.top_track.plays)}
						</p>
					</li>
				{/each}
			</ol>
		{/if}

		{#if data.anniversaries.length > 0}
			<h3>Rocznice pierwszego odsłuchu</h3>
			<ul class="anniversaries">
				{#each data.anniversaries as anniversary (anniversary.artist_id)}
					<li>
						<strong>{anniversary.artist_name}</strong>
						<span class="muted">
							· {formatYearsAgo(anniversary.years_ago)}, {formatDate(anniversary.first_at)}
						</span>
						<p>
							Zaczęło się od „{anniversary.first_track}”. Od tamtej pory {formatPlays(
								anniversary.total_plays
							)}.
						</p>
					</li>
				{/each}
			</ul>
		{/if}
	</section>
{/if}

<style>
	.today {
		padding-top: var(--space-5);
	}

	h2 {
		margin-bottom: var(--space-4);
		font-size: 1.375rem;
	}

	h3 {
		margin: var(--space-5) 0 var(--space-3);
		font-size: 1.125rem;
	}

	.years {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr));
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.years li {
		display: flex;
		flex-direction: column;
		gap: var(--space-2);
		padding: var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
	}

	.when {
		display: flex;
		justify-content: space-between;
		gap: var(--space-3);
		font-family: var(--font-display);
		font-size: 1.125rem;
		font-weight: 600;
	}

	.muted {
		font-size: 0.9375rem;
	}

	.anniversaries {
		display: flex;
		flex-direction: column;
		gap: var(--space-3);
		margin: 0;
		padding: 0;
		list-style: none;
	}

	.anniversaries p {
		margin-top: var(--space-1);
	}
</style>
