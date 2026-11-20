<script lang="ts">
	import * as Plot from '@observablehq/plot';
	import type { Attachment } from 'svelte/attachments';
	import type { YearCompat } from '#lib/api.ts';
	import { formatPercent } from '#lib/format.ts';

	let { years, currentYear }: { years: YearCompat[]; currentYear: number } = $props();

	let width = $state(0);

	const label = (year: number) => (year === currentYear ? `${year} (do dziś)` : String(year));

	const summary = $derived(
		years.length === 0
			? 'Za mało wspólnych odsłuchów, żeby porównać lata.'
			: 'Zgodność gustów w kolejnych latach: ' +
					years.map((y) => `${label(y.year)}: ${formatPercent(y.similarity)}`).join(', ') +
					'.'
	);

	// Jak przy wykresie miesięcznym: Plot zwraca gotowy SVG, załącznik wstawia go do kontenera
	const renderChart: Attachment<HTMLDivElement> = (container) => {
		if (width === 0 || years.length === 0) return;
		const points = years.map((y) => ({ year: y.year, value: y.similarity * 100 }));

		const chart = Plot.plot({
			width,
			height: 220,
			marginLeft: 40,
			marginTop: 24,
			style: { background: 'transparent', color: 'var(--text-muted)', fontSize: '12px' },
			x: {
				label: null,
				type: 'point',
				domain: years.map((y) => y.year),
				tickFormat: (year: number) => String(year),
				inset: 24
			},
			y: { label: null, domain: [0, 100], grid: true, tickFormat: (v: number) => `${v}%` },
			marks: [
				Plot.line(points, { x: 'year', y: 'value', stroke: 'var(--accent)', strokeWidth: 2.5 }),
				Plot.dot(points, { x: 'year', y: 'value', fill: 'var(--accent)', r: 5 }),
				Plot.text(points, {
					x: 'year',
					y: 'value',
					text: (d: (typeof points)[number]) => formatPercent(d.value / 100),
					dy: -14,
					fill: 'var(--text)',
					fontWeight: 600
				})
			]
		});

		container.append(chart);
		return () => chart.remove();
	};
</script>

<div
	class="chart"
	role="img"
	aria-label={summary}
	bind:clientWidth={width}
	{@attach renderChart}
></div>

{#if years.some((y) => y.year === currentYear)}
	<!-- na osi zwykły rok: dopisek przy podziałce nachodził na sąsiedni rok na telefonie -->
	<p class="muted note">Rok {currentYear} liczony do dziś.</p>
{/if}

<style>
	.note {
		font-size: 0.875rem;
	}

	.chart {
		min-height: 220px;
	}
</style>
