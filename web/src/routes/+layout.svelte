<script lang="ts">
	import { resolve } from '$app/paths';
	import favicon from '#lib/assets/favicon.svg';
	import '../app.css';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();
</script>

<svelte:head>
	<title>Erafm</title>
	<link rel="icon" href={favicon} />
</svelte:head>

<div class="page">
	<header>
		<a class="wordmark" href={resolve('/')}>era<span>fm</span></a>
	</header>

	<main>
		{@render children()}
	</main>

	<footer class="muted">
		Dane o odsłuchach pochodzą z <a href="https://www.last.fm" rel="external">Last.fm</a>.
	</footer>
</div>

<style>
	.page {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
		max-width: var(--page-width);
		margin: 0 auto;
		padding: 0 var(--space-4);
	}

	header {
		padding: var(--space-5) 0;
	}

	.wordmark {
		font-family: var(--font-display);
		font-size: 1.5rem;
		font-weight: 600;
		letter-spacing: -0.02em;
		color: var(--text);
		text-decoration: none;
	}

	.wordmark span {
		color: var(--accent);
	}

	main {
		flex: 1;
		padding-bottom: var(--space-7);
	}

	footer {
		padding: var(--space-5) 0;
		border-top: 1px solid var(--border);
		font-size: 0.875rem;
	}
</style>
