<script lang="ts">
	import { resolve } from '$app/paths';
	import { page } from '$app/state';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const nick = $derived(page.params.nick ?? '');
	const tabs = $derived([
		{ label: 'Profil', route: '/u/[nick]', href: resolve('/u/[nick]', { nick }) },
		{ label: 'Ery', route: '/u/[nick]/ery', href: resolve('/u/[nick]/ery', { nick }) }
	]);
</script>

<nav aria-label="Podstrony profilu">
	{#each tabs as tab (tab.route)}
		<a href={tab.href} aria-current={page.route.id === tab.route ? 'page' : undefined}>
			{tab.label}
		</a>
	{/each}
</nav>

{@render children()}

<style>
	nav {
		display: flex;
		gap: var(--space-2);
		border-bottom: 1px solid var(--border);
	}

	a {
		padding: var(--space-2) var(--space-3);
		margin-bottom: -1px;
		border-bottom: 2px solid transparent;
		color: var(--text-muted);
		font-weight: 600;
		text-decoration: none;
	}

	a:hover {
		color: var(--text);
	}

	a[aria-current='page'] {
		border-bottom-color: var(--accent);
		color: var(--text);
	}
</style>
