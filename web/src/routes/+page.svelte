<script lang="ts">
	import { PUBLIC_DEMO_USERS } from '$app/env/public';
	import { goto } from '$app/navigation';
	import { resolve } from '$app/paths';
	import { isValidUsername } from '#lib/username.ts';

	let nick = $state('');
	let invalid = $state(false);

	function openProfile(event: SubmitEvent) {
		event.preventDefault();
		// Małe litery: Last.fm nie rozróżnia wielkości, a adres profilu ma być jeden
		const trimmed = nick.trim().toLowerCase();
		if (!isValidUsername(trimmed)) {
			invalid = true;
			return;
		}
		goto(resolve('/u/[nick]', { nick: trimmed }));
	}
</script>

<section class="hero">
	<h1>Twoja historia słuchania, rozłożona na ery</h1>
	<p class="lead muted">
		Wpisz nick z Last.fm. Pobierzemy całą historię odsłuchów i pokażemy, co z niej wynika.
	</p>

	<form onsubmit={openProfile} novalidate>
		<label for="nick">Nick Last.fm</label>
		<div class="row">
			<input
				id="nick"
				name="nick"
				type="text"
				autocomplete="off"
				autocapitalize="none"
				spellcheck="false"
				placeholder="np. rj"
				aria-invalid={invalid}
				aria-describedby={invalid ? 'nick-error' : undefined}
				bind:value={nick}
				oninput={() => (invalid = false)}
			/>
			<button class="button" type="submit">Pokaż profil</button>
		</div>
		{#if invalid}
			<p id="nick-error" class="error">Wpisz nick bez spacji i ukośników.</p>
		{/if}
	</form>
</section>

{#if PUBLIC_DEMO_USERS.length > 0}
	<section class="demo">
		<h2>Albo zobacz gotowy profil</h2>
		<ul>
			{#each PUBLIC_DEMO_USERS as demoNick (demoNick)}
				<li>
					<a class="tile" href={resolve('/u/[nick]', { nick: demoNick })}>
						<span class="tile-name">{demoNick}</span>
						<span class="muted">profil demo</span>
					</a>
				</li>
			{/each}
		</ul>
	</section>
{/if}

<style>
	.hero {
		padding: var(--space-6) 0 var(--space-5);
		max-width: 38rem;
	}

	h1 {
		font-size: clamp(2rem, 6vw, 3.25rem);
	}

	.lead {
		margin-top: var(--space-4);
		font-size: 1.125rem;
	}

	form {
		margin-top: var(--space-6);
	}

	label {
		display: block;
		margin-bottom: var(--space-2);
		font-size: 0.875rem;
		font-weight: 600;
	}

	.row {
		display: flex;
		flex-wrap: wrap;
		gap: var(--space-3);
	}

	input {
		flex: 1 1 14rem;
		min-width: 0;
		padding: var(--space-3) var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius-small);
		background: var(--surface);
	}

	input::placeholder {
		color: var(--text-muted);
	}

	input[aria-invalid='true'] {
		border-color: var(--danger);
	}

	.error {
		margin-top: var(--space-2);
		color: var(--danger);
		font-size: 0.875rem;
	}

	.demo {
		margin-top: var(--space-6);
	}

	h2 {
		font-size: 1.25rem;
	}

	ul {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(13rem, 1fr));
		gap: var(--space-3);
		margin: var(--space-4) 0 0;
		padding: 0;
		list-style: none;
	}

	.tile {
		display: flex;
		flex-direction: column;
		gap: var(--space-1);
		padding: var(--space-4);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		background: var(--surface);
		color: var(--text);
		text-decoration: none;
	}

	.tile:hover {
		border-color: var(--accent);
		background: var(--surface-raised);
		color: var(--text);
	}

	.tile-name {
		font-weight: 600;
		overflow-wrap: anywhere;
	}
</style>
