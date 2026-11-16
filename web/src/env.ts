import { defineEnvVars } from '@sveltejs/kit/env';

export const variables = defineEnvVars({
	PUBLIC_API_URL: { public: true },
	PUBLIC_DEMO_USERS: {
		public: true,
		description: 'Nicki profili demo na ekranie startowym, po przecinku. Może być puste.',
		schema: (value) =>
			(value ?? '')
				.split(',')
				.map((nick) => nick.trim())
				.filter(Boolean)
	}
});
