<script lang="ts">
	import * as Plot from '@observablehq/plot';
	import type { Attachment } from 'svelte/attachments';
	import type { MonthlyPlays } from '#lib/api.ts';
	import { formatMonth, formatNumber, formatPlays } from '#lib/format.ts';

	let { data }: { data: MonthlyPlays[] } = $props();

	// Szerokość kontenera: wykres rysuje się od nowa, gdy zmienia się rozmiar okna
	let width = $state(0);

	// „2024-03-01” to północ UTC; skala też jest w UTC, więc miesiące się nie przesuwają
	const months = $derived(data.map((d) => ({ month: new Date(d.month), plays: d.plays })));

	const summary = $derived.by(() => {
		if (months.length === 0) return 'Brak odsłuchów';
		const peak = months.reduce((best, d) => (d.plays > best.plays ? d : best));
		const first = formatMonth(months[0].month);
		const last = formatMonth(months[months.length - 1].month);
		// Miesiące w mianowniku, bo Intl nie odmienia ich przez przypadki („od wrzesień”)
		return (
			`Odsłuchy w kolejnych miesiącach. Pierwszy miesiąc: ${first}, ostatni: ${last}. ` +
			`Najwięcej: ${formatMonth(peak.month)}, ${formatPlays(peak.plays)}.`
		);
	});

	// Plot zwraca gotowy element SVG. Załącznik (attachment) wstawia go do kontenera
	// i uruchamia się ponownie, gdy zmienią się dane albo szerokość.
	const renderChart: Attachment<HTMLDivElement> = (container) => {
		if (width === 0) return;

		const chart = Plot.plot({
			width,
			height: 260,
			marginLeft: 48,
			// Kolory ze zmiennych CSS: wykres idzie za motywem strony
			style: { background: 'transparent', color: 'var(--text-muted)', fontSize: '12px' },
			x: {
				type: 'utc',
				label: null,
				ticks: 'year',
				tickFormat: (d: Date) => String(d.getUTCFullYear())
			},
			y: { label: null, grid: true, tickFormat: formatNumber },
			marks: [
				Plot.rectY(months, {
					x: 'month',
					interval: 'month',
					y: 'plays',
					fill: 'var(--accent)',
					insetLeft: 0.5,
					insetRight: 0.5,
					title: (d: (typeof months)[number]) => `${formatMonth(d.month)}\n${formatPlays(d.plays)}`,
					tip: { fill: 'var(--surface-raised)', stroke: 'var(--border)' }
				}),
				Plot.ruleY([0], { stroke: 'var(--border)' })
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

<style>
	.chart {
		min-height: 260px;
	}

	/* Tekst podpowiedzi ma być czytelny na ciemnym tle, a nie w kolorze osi */
	.chart :global([aria-label='tip']) {
		color: var(--text);
	}
</style>
