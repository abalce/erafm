<script lang="ts">
	import { formatNumber, formatPlays } from '#lib/format.ts';

	interface Item {
		id: number;
		name: string;
		/** Drobniejszy podpis pod nazwą, np. wykonawca utworu */
		detail?: string;
		plays: number;
		/** `undefined`: lista bez obrazków; `null`: jest miejsce na obrazek, ale go nie mamy */
		imageUrl?: string | null;
		/** Adres widoku pozycji (np. artysty w duecie); bez niego nazwa nie jest linkiem */
		href?: string;
	}

	let { items }: { items: Item[] } = $props();

	// Pasek pod pozycją pokazuje jej odsłuchy względem pierwszego miejsca
	const max = $derived(Math.max(1, ...items.map((item) => item.plays)));
</script>

<ol>
	{#each items as item, index (item.id)}
		<li>
			<span class="rank">{index + 1}</span>
			{#if item.imageUrl}
				<img src={item.imageUrl} alt="" loading="lazy" />
			{:else if item.imageUrl === null}
				<span class="placeholder" aria-hidden="true">{item.name.charAt(0).toUpperCase()}</span>
			{/if}
			<span class="text">
				{#if item.href}
					<!-- href przychodzi gotowy z resolve() u wywołującego -->
					<!-- eslint-disable-next-line svelte/no-navigation-without-resolve -->
					<a class="name" href={item.href}>{item.name}</a>
				{:else}
					<span class="name">{item.name}</span>
				{/if}
				{#if item.detail}
					<span class="detail">{item.detail}</span>
				{/if}
			</span>
			<span class="plays" title={formatPlays(item.plays)}>{formatNumber(item.plays)}</span>
			<span class="bar" style:width="{(item.plays / max) * 100}%"></span>
		</li>
	{/each}
</ol>

<style>
	ol {
		margin: 0;
		padding: 0;
		list-style: none;
	}

	li {
		position: relative;
		display: flex;
		align-items: center;
		gap: var(--space-3);
		padding: var(--space-3) 0 calc(var(--space-3) + 3px);
		border-bottom: 1px solid var(--border);
	}

	.rank {
		flex: none;
		width: 1.75rem;
		color: var(--text-muted);
		font-variant-numeric: tabular-nums;
		text-align: right;
	}

	img,
	.placeholder {
		flex: none;
		width: 2.5rem;
		height: 2.5rem;
		border-radius: var(--radius-small);
	}

	img {
		object-fit: cover;
	}

	.placeholder {
		display: grid;
		place-items: center;
		background: var(--surface-raised);
		color: var(--text-muted);
		font-family: var(--font-display);
		font-size: 1.125rem;
	}

	.text {
		display: flex;
		flex: 1;
		flex-direction: column;
		min-width: 0;
	}

	.name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}

	a.name {
		color: var(--text);
		text-decoration-color: var(--border);
	}

	a.name:hover {
		color: var(--accent-strong);
	}

	.detail {
		color: var(--text-muted);
		font-size: 0.875rem;
		overflow-wrap: anywhere;
	}

	.plays {
		flex: none;
		font-variant-numeric: tabular-nums;
	}

	.bar {
		position: absolute;
		bottom: -1px;
		left: 0;
		height: 3px;
		border-radius: 999px;
		background: var(--accent);
	}
</style>
