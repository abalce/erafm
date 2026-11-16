/** Te same zasady co w backendzie: bez spacji i ukośników, najwyżej 64 znaki. */
export function isValidUsername(nick: string): boolean {
	return /^[^\s/]{1,64}$/.test(nick);
}
