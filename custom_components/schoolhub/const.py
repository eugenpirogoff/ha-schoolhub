"""Constants for the SchoolHub integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "schoolhub"

CONF_SERVICE: Final = "service"
CONF_WEEKS_PAST: Final = "weeks_past"
CONF_WEEKS_FUTURE: Final = "weeks_future"

SERVICE_SWOP: Final = "swop"
SERVICE_DREI_KOECHE: Final = "drei_koeche"

# Schools offered in the setup form: address -> (city, school name).
# Any other SWOP portal can be typed in. The first one is pre-selected.
KNOWN_SCHOOLS: Final[dict[str, tuple[str, str]]] = {
    "https://regenbogen-grundschule.swop.schule": ("Ludwigsfelde", "Regenbogen Grundschule"),
}
# Example address in the setup texts (kept out of the translations).
EXAMPLE_URL: Final = "https://my-school.swop.schule"

DEFAULTS: Final = {
    SERVICE_SWOP: {CONF_WEEKS_PAST: 2, CONF_WEEKS_FUTURE: 4},
    SERVICE_DREI_KOECHE: {CONF_WEEKS_PAST: 2, CONF_WEEKS_FUTURE: 4},
}

UPDATE_INTERVAL: Final = {
    SERVICE_SWOP: timedelta(minutes=30),
    SERVICE_DREI_KOECHE: timedelta(hours=3),
}

# Base data (teachers, subjects, rooms, lesson times) changes rarely.
BASEDATA_MAX_AGE: Final = timedelta(hours=24)

# Fired once for every news post that appears after the first refresh.
EVENT_NEW_POST: Final = f"{DOMAIN}_new_post"

NEWS_SCOPE_CLASS: Final = "class"
NEWS_SCOPE_SCHOOL: Final = "school"

# Keep the recorded state attributes small.
MAX_ATTRIBUTE_ITEMS: Final = 10
MAX_TEXT_LENGTH: Final = 1000
