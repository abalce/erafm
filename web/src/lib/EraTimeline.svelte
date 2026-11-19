<script lang="ts">
	import { scaleUtc, select, zoom, zoomIdentity, type ZoomBehavior, type ZoomTransform } from 'd3';
	import type { Attachment } from 'svelte/attachments';
	import type { Silence } from '#lib/api.ts';
	import type { EraItem } from '#lib/eras.ts';
	import { formatCount, formatMonthRange, formatPlays } from '#lib/format.ts';

	let {
		items,
		silences,
		start,
		end,
		selected,
		onselect
	}: {
		items: EraItem[];
		silences: Silence[];
		/** Pierwszy i ostatni dzień osi, np. `2023-03-27` */
		start: string;
		end: string;
		selected: string | null;
		onselect: (key: string) => void;
	} = $props();

	const DAY = 24 * 60 * 60 * 1000;
	// Najbliższe przybliżenie: tyle dni na całą szerokość osi
	const MIN_VISIBLE_DAYS = 60;

	const HEIGHT = 96;
	const TRACK_TOP = 8;
	const TRACK_HEIGHT = 52;
	const EPISODE_HEIGHT = 20;
	const AXIS_Y = 70;

	const uid = $props.id();
	let svg: SVGSVGElement | undefined = $state();
	let width = $state(0);
	let transform: ZoomTransform = $state(zoomIdentity);
	let behavior: ZoomBehavior<SVGSVGElement, unknown> | undefined;

	// Daty z API to północ UTC; koniec okresu to niedziela, więc rysujemy do poniedziałku
	const toDate = (day: string) => new Date(day);
	const after = (day: string) => new Date(new Date(day).getTime() + DAY);

	const x = $derived(
		scaleUtc()
			.domain([toDate(start), after(end)])
			.range([0, width])
	);
	const zx = $derived(transform.rescaleX(x));
	const ticks = $derived(zx.ticks(Math.max(2, Math.floor(width / 90))));

	const shortMonth = new Intl.DateTimeFormat('pl-PL', { month: 'short', timeZone: 'UTC' });
	function tickLabel(date: Date): string {
		if (date.getUTCMonth() === 0 && date.getUTCDate() === 1) return String(date.getUTCFullYear());
		if (date.getUTCDate() === 1) return shortMonth.format(date);
		return `${date.getUTCDate()} ${shortMonth.format(date)}`;
	}

	function title(item: EraItem): string {
		const name = item.kind === 'era' ? `Era ${item.era.artist_name}` : item.era.artist_name;
		return `${name} · ${formatMonthRange(item.era.start, item.era.end)} · ${formatPlays(item.era.plays)}`;
	}

	// Napis mieści się w pasie, gdy pas jest szerszy niż tekst (ok. 7 px na znak) z marginesem
	const fitsLabel = (item: EraItem, barWidth: number) =>
		item.kind === 'era' && barWidth > item.era.artist_name.length * 7 + 16;

	const eraCount = $derived(items.filter((item) => item.kind === 'era').length);
	const summary = $derived(
		`Oś czasu od ${formatMonthRange(start, start)} do ${formatMonthRange(end, end)}: ` +
			`${formatCount(eraCount, ['era', 'ery', 'er'])}, ` +
			`${formatCount(items.length - eraCount, ['epizod', 'epizody', 'epizodów'])}, ` +
			`${formatCount(silences.length, ['okres', 'okresy', 'okresów'])} ciszy. ` +
			'Ery można też przeglądać na liście pod osią.'
	);

	// d3-zoom obsługuje kółko myszy, przeciąganie i szczypanie na telefonie; wynik trzymamy
	// w stanie, a Svelte przerysowuje pasy i podziałkę. Przy zmianie szerokości wracamy do
	// całej historii, bo stare przesunięcie liczone było dla innej szerokości.
	const zoomable: Attachment<SVGSVGElement> = (element) => {
		if (width === 0) return;
		const days = (after(end).getTime() - toDate(start).getTime()) / DAY;
		const extent: [[number, number], [number, number]] = [
			[0, 0],
			[width, HEIGHT]
		];
		behavior = zoom<SVGSVGElement, unknown>()
			.scaleExtent([1, Math.max(1, days / MIN_VISIBLE_DAYS)])
			.extent(extent)
			.translateExtent(extent)
			.clickDistance(4)
			.on('zoom', (event: { transform: ZoomTransform }) => {
				transform = event.transform;
			});
		const selection = select(element);
		selection.call(behavior).call(behavior.transform, zoomIdentity);
		return () => {
			selection.on('.zoom', null);
		};
	};

	function showAll() {
		if (svg && behavior)
			select(svg).transition().duration(400).call(behavior.transform, zoomIdentity);
	}

	function onkeydown(event: KeyboardEvent, key: string) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			onselect(key);
		}
	}
</script>

<div class="timeline" bind:clientWidth={width}>
	{#if width > 0}
		<svg
			bind:this={svg}
			{width}
			height={HEIGHT}
			role="group"
			aria-label={summary}
			{@attach zoomable}
		>
			<defs>
				<pattern
					id="{uid}-silence"
					width="6"
					height="6"
					patternUnits="userSpaceOnUse"
					patternTransform="rotate(45)"
				>
					<line x1="0" y1="0" x2="0" y2="6" class="hatch" />
				</pattern>
				<clipPath id="{uid}-clip">
					<rect x="0" y="0" {width} height={HEIGHT} />
				</clipPath>
			</defs>

			<g clip-path="url(#{uid}-clip)">
				<line
					class="baseline"
					x1="0"
					x2={width}
					y1={TRACK_TOP + TRACK_HEIGHT / 2}
					y2={TRACK_TOP + TRACK_HEIGHT / 2}
				/>

				{#each silences as silence (silence.start)}
					<rect
						class="silence"
						x={zx(toDate(silence.start))}
						y={TRACK_TOP}
						width={zx(after(silence.end)) - zx(toDate(silence.start))}
						height={TRACK_HEIGHT}
						fill="url(#{uid}-silence)"
					>
						<title>Cisza · {formatMonthRange(silence.start, silence.end)}</title>
					</rect>
				{/each}

				{#each items as item (item.key)}
					{@const left = zx(toDate(item.era.start))}
					{@const barWidth = Math.max(2, zx(after(item.era.end)) - left)}
					{@const barHeight = item.kind === 'era' ? TRACK_HEIGHT : EPISODE_HEIGHT}
					<g
						class="item {item.kind}"
						class:selected={item.key === selected}
						role="button"
						tabindex="0"
						aria-label={title(item)}
						aria-pressed={item.key === selected}
						onclick={() => onselect(item.key)}
						onkeydown={(event) => onkeydown(event, item.key)}
					>
						<title>{title(item)}</title>
						<rect
							x={left}
							y={TRACK_TOP + (TRACK_HEIGHT - barHeight) / 2}
							width={barWidth}
							height={barHeight}
							rx={item.kind === 'era' ? 6 : 4}
							fill={item.color}
						/>
						{#if fitsLabel(item, barWidth)}
							<text x={left + 8} y={TRACK_TOP + TRACK_HEIGHT / 2} dy="0.35em">
								{item.era.artist_name}
							</text>
						{/if}
					</g>
				{/each}
			</g>

			<g class="axis">
				<line x1="0" x2={width} y1={AXIS_Y} y2={AXIS_Y} />
				{#each ticks as tick (tick.getTime())}
					<line x1={zx(tick)} x2={zx(tick)} y1={AXIS_Y} y2={AXIS_Y + 5} />
					<text x={zx(tick)} y={AXIS_Y + 20} text-anchor="middle">{tickLabel(tick)}</text>
				{/each}
			</g>
		</svg>
	{/if}
</div>

<div class="controls">
	<p class="muted hint">Przybliżaj kółkiem myszy albo dwoma palcami, przesuwaj przeciągając.</p>
	{#if transform.k > 1}
		<button class="reset" type="button" onclick={showAll}>Cała historia</button>
	{/if}
</div>

<style>
	.timeline {
		min-height: 96px;
		cursor: grab;
	}

	.timeline:active {
		cursor: grabbing;
	}

	svg {
		display: block;
		overflow: visible;
	}

	.baseline {
		stroke: var(--border);
	}

	.hatch {
		stroke: var(--text-muted);
		stroke-width: 1.5;
		opacity: 0.45;
	}

	.item {
		cursor: pointer;
		outline: none;
	}

	.item rect {
		transition: opacity 0.15s ease;
	}

	.episode rect {
		opacity: 0.6;
	}

	.item:hover rect,
	.item:focus-visible rect {
		opacity: 1;
		stroke: var(--text);
		stroke-width: 1.5;
	}

	.item.selected rect {
		opacity: 1;
		stroke: var(--text);
		stroke-width: 2.5;
	}

	.item text {
		fill: var(--bg);
		font-size: 13px;
		font-weight: 600;
		pointer-events: none;
	}

	.axis line {
		stroke: var(--border);
	}

	.axis text {
		fill: var(--text-muted);
		font-size: 12px;
	}

	.controls {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		justify-content: space-between;
		gap: var(--space-3);
		min-height: 2.5rem;
		margin-top: var(--space-2);
	}

	.hint {
		font-size: 0.875rem;
	}

	.reset {
		padding: var(--space-1) var(--space-3);
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		background: var(--surface);
		cursor: pointer;
	}

	.reset:hover {
		border-color: var(--accent);
	}
</style>
