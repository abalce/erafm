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

# Klucz tagu bez myślników, podkreśleń i spacji: „k-pop”, „kpop” i „K Pop” to ten sam gatunek.
# Ten sam wzór w SQL: regexp_replace(lower(name), TAG_KEY_STRIP, '', 'g')
TAG_KEY_STRIP = "[-_ ]"
_TAG_KEY_STRIP = re.compile(TAG_KEY_STRIP)


def tag_key(name: str) -> str:
    return _TAG_KEY_STRIP.sub("", name.strip().lower())


# Dekady i lata: „80s”, „1990s”, „00s”
DECADE_PATTERN = r"^[0-9]{2}([0-9]{2})?s$"
_DECADE = re.compile(DECADE_PATTERN)


def is_genre_tag(name: str) -> bool:
    """Czy tag nadaje się do filtrowania po gatunku (albo narodowości)."""
    name = name.strip().lower()
    return bool(name) and name not in IGNORED_TAGS and not _DECADE.match(name)
