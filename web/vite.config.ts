import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},

			// Frontend to zwykłe pliki statyczne, które podaje API (backend/src/backend/main.py).
			// Strony renderują się w przeglądarce, więc każdy adres dostaje index.html.
			adapter: adapter({ fallback: 'index.html' })
		})
	]
});
