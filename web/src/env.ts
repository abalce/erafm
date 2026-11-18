import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	// Wartości wpisywane do plików przy budowaniu (static): zbudowany frontend nie ma
	// własnego serwera, który mógłby je odczytać później
	PUBLIC_API_URL: {
		public: true,
		static: true,
		description:
			'Adres serwera API bez /api, np. http://localhost:8000. Pusty: API pod tym samym adresem co strona.'
	},
	PUBLIC_DEMO_USERS: {
		public: true,
		static: true,
		description: 'Nicki profili demo na ekranie startowym, po przecinku. Może być puste.',
		schema: (value) =>
			(value ?? '')
				.split(',')
				.map((nick) => nick.trim())
				.filter(Boolean)
	}
});
