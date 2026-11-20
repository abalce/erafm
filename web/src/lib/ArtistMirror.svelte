<script lang="ts">
	import * as Plot from '@observablehq/plot';
	import type { Attachment } from 'svelte/attachments';
	import type { ArtistHistory } from '#lib/api.ts';
	import { formatDay, formatPlays } from '#lib/format.ts';

	// Pierwsza osoba nad osią, druga pod nią, na wspólnej skali czasu
	let { people }: { people: [ArtistHistory, ArtistHistory] } = $props();

	const DAY = 24 * 60 * 60 * 1000;
	// Wykres zaczyna się tyle dni przed pierwszym odsłuchem którejkolwiek osoby
	const LEAD_DAYS = 90;
	const COLORS = ['var(--accent)', 'var(--era-2)'];

	let width = $state(0);

	const firsts = $derived(
		people
			.filter((person) => person.first_at !== null)
			.map((person) => ({ nick: person.username, at: new Date(person.first_at!) }))
	);

	const summary = $derived(
		`Odsłuchy ${people[0].name} tydzień po tygodniu: ${people[0].username} nad osią, ` +
			`${people[1].username} pod osią. ` +
			people
				.map((person) =>
					person.first_at
						? `${person.username}: pierwszy odsłuch ${formatDay(person.first_at.slice(0, 10))}, razem ${formatPlays(person.total_plays)}.`
						: `${person.username} nie słucha tego artysty.`
				)
				.join(' ')
	);

	// Jak przy innych wykresach: Plot zwraca gotowy SVG, załącznik wstawia go do kontenera
	const renderChart: Attachment<HTMLDivElement> = (container) => {
		if (width === 0 || firsts.length === 0) return;
		const start = new Date(Math.min(...firsts.map((f) => f.at.getTime())) - LEAD_DAYS * DAY);
		const bars = people.flatMap((person, i) =>
			person.weeks
				.map((w) => ({ week: new Date(w.week), plays: w.plays, person: i }))
				.filter((w) => w.week >= start)
				.map((w) => ({
					...w,
					end: new Date(w.week.getTime() + 7 * DAY),
					value: i === 0 ? w.plays : -w.plays
				}))
		);
		const peak = Math.max(1, ...bars.map((b) => b.plays));

		const chart = Plot.plot({
			width,
			height: 300,
			marginLeft: 44,
			style: { background: 'transparent', color: 'var(--text-muted)', fontSize: '12px' },
			x: { type: 'utc', label: null, domain: [start, new Date()] },
			y: {
				label: null,
				domain: [-peak, peak],
				grid: true,
				tickFormat: (v: number) => String(Math.abs(v))
			},
			marks: [
				Plot.rectY(bars, {
					x1: 'week',
					x2: 'end',
					y: 'value',
					fill: (d: (typeof bars)[number]) => COLORS[d.person],
					title: (d: (typeof bars)[number]) =>
						`${people[d.person].username} · tydzień od ${formatDay(d.week.toISOString().slice(0, 10))}\n${formatPlays(d.plays)}`,
					tip: { fill: 'var(--surface-raised)', stroke: 'var(--border)' }
				}),
				Plot.ruleY([0], { stroke: 'var(--border)' }),
				Plot.ruleX(firsts, { x: 'at', stroke: 'var(--text-muted)', strokeDasharray: '4 3' }),
				// podpis nad osią dla pierwszej osoby i pod osią dla drugiej
				...people.map((person, i) =>
					Plot.text(
						firsts.filter((f) => f.nick === person.username),
						{
							x: 'at',
							y: i === 0 ? peak : -peak,
							text: () => `pierwszy odsłuch ${person.username}`,
							textAnchor: 'start',
							dx: 4,
							dy: i === 0 ? 6 : -6,
							fill: 'var(--text)'
						}
					)
				)
			]
		});

		container.append(chart);
		return () => chart.remove();
	};
</script>

<ul class="legend">
	{#each people as person, i (person.username)}
		<li>
			<span class="swatch" style:background={COLORS[i]}></span>
			{person.username}
			{i === 0 ? 'nad osią' : 'pod osią'}
		</li>
	{/each}
</ul>
<div
	class="chart"
	role="img"
	aria-label={summary}
	bind:clientWidth={width}
	{@attach renderChart}
></div>

<style>
	.legend {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-2) var(--space-5);
		margin: 0 0 var(--space-3);
		padding: 0;
		list-style: none;
		font-size: 0.875rem;
	}

	.legend li {
		display: flex;
		align-items: center;
		gap: var(--space-2);
	}

	.swatch {
		width: 0.75rem;
		height: 0.75rem;
		border-radius: 3px;
	}

	.chart {
		min-height: 300px;
	}

	.chart :global([aria-label='tip']) {
		color: var(--text);
	}
</style>
