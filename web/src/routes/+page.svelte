<script lang="ts">
	import { onMount } from 'svelte';
	import { PUBLIC_API_URL } from '$app/env/public';

	type Wynik =
		| { stan: 'ladowanie' }
		| { stan: 'ok'; db: string }
		| { stan: 'blad'; komunikat: string };

	let wynik: Wynik = $state({ stan: 'ladowanie' });

	onMount(async () => {
		try {
			const res = await fetch(`${PUBLIC_API_URL}/health`);
			if (!res.ok) {
				wynik = { stan: 'blad', komunikat: `API zwróciło status ${res.status}` };
				return;
			}
			const dane = await res.json();
			wynik = { stan: 'ok', db: dane.db };
		} catch {
			wynik = { stan: 'blad', komunikat: 'API nie odpowiada' };
		}
	});
</script>

<h1>Status</h1>

{#if wynik.stan === 'ladowanie'}
	<p>Ładowanie…</p>
{:else if wynik.stan === 'ok'}
	<p>Baza danych: {wynik.db}</p>
{:else}
	<p>Błąd: {wynik.komunikat}</p>
{/if}
