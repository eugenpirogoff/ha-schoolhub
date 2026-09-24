"""Base entity and devices for SchoolHub."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util, slugify

from .api import school_name
from .const import DOMAIN, KNOWN_SCHOOLS, SERVICE_DREI_KOECHE, SERVICE_SWOP
from .coordinator import DreiKoecheCoordinator, SchoolhubConfigEntry, SwopCoordinator
from .models import Student

MANUFACTURER = "SchoolHub"
SERVICE_NAMES = {SERVICE_SWOP: "SWOP", SERVICE_DREI_KOECHE: "Drei Köche"}

# Values computed from the clock (next lesson, today's meal) are re-evaluated
# this often between refreshes.
CLOCK_INTERVAL = timedelta(minutes=1)

TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "today": "heute",
        "tomorrow": "morgen",
        "yesterday": "gestern",
        "assigned": "aufgegeben",
        "no_due_date": "ohne Fälligkeit",
        "cancelled": "entfällt",
        "substituted": "Vertretung",
        "teacher": "Lehrkraft",
        "topic": "Thema",
        "homework": "Hausaufgabe",
        "comment": "Hinweis",
        "allergens": "Allergene",
        "due": "fällig",
    },
    "en": {
        "today": "today",
        "tomorrow": "tomorrow",
        "yesterday": "yesterday",
        "assigned": "assigned",
        "no_due_date": "no due date",
        "cancelled": "cancelled",
        "substituted": "Substitution",
        "teacher": "Teacher",
        "topic": "Topic",
        "homework": "Homework",
        "comment": "Note",
        "allergens": "Allergens",
        "due": "due",
    },
}


WEEKDAYS = {
    "de": ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
}


# Options of the homework day picker, Monday to Friday.
HOMEWORK_DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday"]


def school_week() -> tuple[date, date]:
    """Return Monday and Friday of the current school week (from Saturday: the next)."""
    today = dt_util.now().date()
    monday = today - timedelta(days=today.weekday())
    if today.weekday() >= 5:
        monday += timedelta(weeks=1)
    return monday, monday + timedelta(days=4)


def default_homework_day() -> str:
    """Return today's weekday; on weekends Monday (the coming school week)."""
    weekday = dt_util.now().weekday()
    return HOMEWORK_DAYS[weekday] if weekday < len(HOMEWORK_DAYS) else HOMEWORK_DAYS[0]


def homework_day(coordinator: SwopCoordinator, student_key: str) -> str:
    """Return the weekday a child's homework view shows."""
    return coordinator.homework_days.get(student_key) or default_homework_day()


def _language(hass: HomeAssistant) -> str:
    return "de" if hass.config.language.startswith("de") else "en"


def short_date(hass: HomeAssistant, day: date) -> str:
    """Return a date without the year, e.g. "23.09." (en: "09/23")."""
    return day.strftime("%d.%m." if _language(hass) == "de" else "%m/%d")


def day_label(hass: HomeAssistant, day: date, today: date) -> str:
    """Return "heute"/"morgen"/"gestern", else e.g. "Mi 23.09." (en: "Wed 09/23")."""
    relative = {0: "today", 1: "tomorrow", -1: "yesterday"}.get((day - today).days)
    if relative:
        return text(hass, relative)
    return f"{WEEKDAYS[_language(hass)][day.weekday()]} {short_date(hass, day)}"


def text(hass: HomeAssistant, key: str) -> str:
    """Return a short text for dynamic content in the configured language."""
    return TEXTS[_language(hass)][key]


def entity_unique_id(entry_unique_id: str | None, subject_key: str, key: str) -> str:
    """Return the unique ID of an entity of a child (or school) in an entry."""
    return f"{entry_unique_id}_{slugify(subject_key)}_{key}"


def student_identifier(service: str, student_key: str) -> tuple[str, str]:
    """Return the device identifier of a child for one service."""
    return (DOMAIN, f"{service}_student_{slugify(student_key)}")


def school_label(url: str) -> str:
    """Return "City - School" for known schools, else a name from the address."""
    if url in KNOWN_SCHOOLS:
        city, name = KNOWN_SCHOOLS[url]
        return f"{city} - {name}"
    return school_name(url)


def school_identifier(school: str) -> tuple[str, str]:
    """Return the device identifier of a school."""
    return (DOMAIN, f"school_{school}")


def student_device(service: str, student: Student) -> DeviceInfo:
    """Return the device of a child for one service.

    Since HA 2026.8 a device belongs to exactly one config entry, so a child
    gets one device per service (both named after the child). The model is
    the service; the model ID is the child's class, e.g. "4b".
    """
    return DeviceInfo(
        identifiers={student_identifier(service, student.key)},
        name=student.display_name,
        manufacturer=MANUFACTURER,
        model=SERVICE_NAMES[service],
        model_id=student.class_name,
        entry_type=DeviceEntryType.SERVICE,
    )


def school_device(coordinator: SwopCoordinator) -> DeviceInfo:
    """Return the device of the school itself (school-wide news)."""
    school = coordinator.client.school
    return DeviceInfo(
        identifiers={school_identifier(school)},
        name=school_label(coordinator.client.base_url),
        manufacturer=MANUFACTURER,
        model="SWOP",
        configuration_url=coordinator.client.base_url,
        entry_type=DeviceEntryType.SERVICE,
    )


def async_add_student_entities(
    entry: SchoolhubConfigEntry,
    coordinator: SwopCoordinator,
    async_add_entities: AddConfigEntryEntitiesCallback,
    create: Callable[[str], Iterable[Entity]],
) -> None:
    """Add the entities of each SWOP child, also of children that appear later.

    `create` returns the entities of one child by its key. Children that leave
    keep their entities (they become unavailable).
    """
    known: set[str] = set()

    @callback
    def add_new_students() -> None:
        new = [key for key in coordinator.data.students if key not in known]
        if not new:
            return
        known.update(new)
        async_add_entities([entity for key in new for entity in create(key)])

    add_new_students()
    entry.async_on_unload(coordinator.async_add_listener(add_new_students))


class SchoolhubEntity[CoordinatorT: SwopCoordinator | DreiKoecheCoordinator](
    CoordinatorEntity[CoordinatorT]
):
    """Base class: one entity of one child (or of the school)."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: CoordinatorT,
        key: str,
        device: DeviceInfo,
        subject_key: str,
    ) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = entity_unique_id(entry.unique_id, subject_key, key)
        self._attr_device_info = device


class ClockMixin:
    """Re-render the state every minute, for values that depend on the time."""

    hass: HomeAssistant
    async_on_remove: Callable[[Callable[[], None]], None]
    async_write_ha_state: Callable[[], None]

    def _start_clock(self) -> None:
        self.async_on_remove(async_track_time_interval(self.hass, self._tick, CLOCK_INTERVAL))

    @callback
    def _tick(self, _now: Any) -> None:
        self.async_write_ha_state()
