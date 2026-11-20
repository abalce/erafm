"""Tagi Last.fm, które nie mówią nic o gatunku: odsiewamy je przy odczycie, nie przy zapisie.

Zapisane tagi zostają w bazie w surowej postaci, więc listę można zmieniać bez ponownego
pobierania. Narodowości („polish”, „korean”) zostawiamy: to ciekawe filtry.
"""

import re

IGNORED_TAGS = frozenset(
    {
        "seen live",
        "favorites",
        "favourites",
        "favorite",
        "favourite",
        "my favorite",
        "my favourite",
        "favorite artists",
        "favourite artists",
        "favorite bands",
        "favourite bands",
        "female vocalists",
        "male vocalists",
        "female vocalist",
        "male vocalist",
        "female",
        "male",
        "vocalists",
        "awesome",
        "amazing",
        "beautiful",
        "love",
        "cool",
        "good",
        "best",
        "epic",
        "music",
        "all",
        "albums i own",
        "spotify",
        "under 2000 listeners",
    }
)

# Dekady i lata: „80s”, „1990s”, „00s”
DECADE_PATTERN = r"^[0-9]{2}([0-9]{2})?s$"
_DECADE = re.compile(DECADE_PATTERN)


def is_genre_tag(name: str) -> bool:
    """Czy tag nadaje się do filtrowania po gatunku (albo narodowości)."""
    name = name.strip().lower()
    return bool(name) and name not in IGNORED_TAGS and not _DECADE.match(name)
