"""Diagnostics: counts and status only, without logins or children's names."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import SchoolhubConfigEntry, SwopCoordinator

TO_REDACT = {CONF_PASSWORD, CONF_URL, CONF_USERNAME, "title", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SchoolhubConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    entry_data = async_redact_data(entry.as_dict(), TO_REDACT)
    # Not loaded (setup retry, setup error, unloaded): there is no coordinator.
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None:
        return {"entry": entry_data, "state": entry.state.value}
    summary: dict[str, Any]
    if isinstance(coordinator, SwopCoordinator):
        data = coordinator.data
        summary = {
            "children": [
                {
                    "class": child.student.class_name,
                    "lessons": len(child.lessons),
                    "cancelled_lessons": sum(lesson.cancelled for lesson in child.lessons),
                    "homework": len(child.homework),
                    "class_news": len(child.class_news),
                }
                for child in data.students.values()
            ],
            "school_news": len(data.school_news),
        }
    else:
        data = coordinator.data
        summary = {"class": data.student.class_name, "meals": len(data.meals)}
    return {
        "entry": entry_data,
        "last_update_success": coordinator.last_update_success,
        # Only the type: exception texts can contain request details.
        "last_exception": (
            type(coordinator.last_exception).__name__ if coordinator.last_exception else None
        ),
        "data": summary,
    }
