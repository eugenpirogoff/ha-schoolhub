"""Check that every Drei Köche child matches a SWOP child.

Dashboards combine a child's SWOP and Drei Köche entities by their shared
entity ID prefix (e.g. calendar.anna_timetable and calendar.anna_lunch). This
check raises repair issues when a Drei Köche child matches no SWOP child, was
only matched despite a spelling difference, or ended up with a different
entity ID prefix (which a repair flow can fix).
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er, issue_registry as ir

from .const import CONF_SERVICE, DOMAIN, SERVICE_SWOP
from .coordinator import DreiKoecheCoordinator, SchoolhubConfigEntry, SwopCoordinator
from .entity import entity_unique_id
from .models import MatchKind, Student, match_student

ISSUE_NOT_FOUND = "student_not_found"
ISSUE_SIMILAR = "student_similar_name"
ISSUE_PREFIX = "entity_prefix_mismatch"
ISSUES = (ISSUE_NOT_FOUND, ISSUE_SIMILAR, ISSUE_PREFIX)


def issue_id(issue: str, entry_id: str) -> str:
    """Return the issue ID of a check for one Drei Köche entry."""
    return f"{issue}_{entry_id}"


def _full_name(student: Student) -> str:
    return f"{student.first_name} {student.last_name}".strip()


@callback
def async_clear_issues(hass: HomeAssistant, entry_id: str) -> None:
    """Remove all check issues of an entry."""
    for issue in ISSUES:
        ir.async_delete_issue(hass, DOMAIN, issue_id(issue, entry_id))


@callback
def async_check_students(hass: HomeAssistant) -> None:
    """Check every loaded Drei Köche entry against the loaded SWOP entries."""
    # Entries still being set up count once their data is there: logins are set
    # up in parallel, and the last one to finish must see all the others.
    entries: list[SchoolhubConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state in (ConfigEntryState.LOADED, ConfigEntryState.SETUP_IN_PROGRESS)
        and hasattr(entry, "runtime_data")
    ]
    swop_entries = [
        entry
        for entry in entries
        if isinstance(entry.runtime_data, SwopCoordinator) and entry.runtime_data.data
    ]
    # A login counts as ready once it has data, even if it has no children.
    ready = {entry.entry_id for entry in swop_entries}
    swop = [
        (entry, child.student)
        for entry in swop_entries
        for child in entry.runtime_data.data.students.values()
    ]
    if any(
        entry.data.get(CONF_SERVICE) == SERVICE_SWOP
        and not entry.disabled_by
        and entry.entry_id not in ready
        for entry in hass.config_entries.async_entries(DOMAIN)
    ):
        # A SWOP login is failing or still starting: its children are unknown,
        # so wait instead of reporting them as missing.
        return
    for entry in entries:
        coordinator = entry.runtime_data
        if not isinstance(coordinator, DreiKoecheCoordinator) or not coordinator.data:
            continue
        if not swop:
            # Nothing to compare with: SchoolHub is used for lunch only.
            async_clear_issues(hass, entry.entry_id)
            continue
        _async_check_entry(hass, entry, coordinator.data.student, swop)


@callback
def _async_check_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    student: Student,
    swop: list[tuple[SchoolhubConfigEntry, Student]],
) -> None:
    match = match_student(student, [candidate for _, candidate in swop])

    if match is None:
        ir.async_delete_issue(hass, DOMAIN, issue_id(ISSUE_SIMILAR, entry.entry_id))
        ir.async_delete_issue(hass, DOMAIN, issue_id(ISSUE_PREFIX, entry.entry_id))
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id(ISSUE_NOT_FOUND, entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_NOT_FOUND,
            translation_placeholders={
                "name": _full_name(student),
                "entry": entry.title,
                "swop_names": ", ".join(_full_name(candidate) for _, candidate in swop),
            },
        )
        return
    ir.async_delete_issue(hass, DOMAIN, issue_id(ISSUE_NOT_FOUND, entry.entry_id))

    if match.how == MatchKind.SIMILAR:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id(ISSUE_SIMILAR, entry.entry_id),
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_SIMILAR,
            translation_placeholders={
                "name": _full_name(student),
                "swop_name": _full_name(match.student),
                "similarity": f"{match.similarity:.0%}",
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id(ISSUE_SIMILAR, entry.entry_id))

    swop_entry = next(e for e, candidate in swop if candidate is match.student)
    registry = er.async_get(hass)
    lunch = registry.async_get_entity_id(
        "calendar", DOMAIN, entity_unique_id(entry.unique_id, student.key, "meals")
    )
    timetable = registry.async_get_entity_id(
        "calendar",
        DOMAIN,
        entity_unique_id(swop_entry.unique_id, match.student.key, "timetable"),
    )
    old_prefix = _prefix(lunch, LUNCH_SUFFIXES)
    new_prefix = _prefix(timetable, TIMETABLE_SUFFIXES)
    if old_prefix and new_prefix and old_prefix != new_prefix:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id(ISSUE_PREFIX, entry.entry_id),
            data={
                "entry_id": entry.entry_id,
                "old_prefix": old_prefix,
                "new_prefix": new_prefix,
                "name": _full_name(student),
                "swop_name": _full_name(match.student),
            },
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_PREFIX,
            translation_placeholders={"name": _full_name(match.student)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, issue_id(ISSUE_PREFIX, entry.entry_id))


# Entity ID endings as generated in English and German. IDs changed by the user
# to anything else are left alone.
LUNCH_SUFFIXES = ("_lunch", "_essen")
TIMETABLE_SUFFIXES = ("_timetable", "_stundenplan")


def _prefix(entity_id: str | None, suffixes: tuple[str, ...]) -> str | None:
    """Return the child prefix of e.g. "calendar.anna_maria_lunch" ("anna_maria")."""
    if not entity_id:
        return None
    object_id = entity_id.split(".", 1)[1]
    for suffix in suffixes:
        if object_id.endswith(suffix) and len(object_id) > len(suffix):
            return object_id.removesuffix(suffix)
    return None
