"""Repair flow: give a child's Drei Köche entities the SWOP entity ID prefix."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow, RepairsFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .checks import async_check_students


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None] | None
) -> RepairsFlow:
    """Return the flow that fixes an issue."""
    return RenamePrefixFlow(data or {})


class RenamePrefixFlow(RepairsFlow):
    """Rename e.g. sensor.anna_lunch_today to sensor.anna_maria_lunch_today."""

    def __init__(self, data: dict[str, str | int | float | None]) -> None:
        self._entry_id = str(data["entry_id"])
        self._old = str(data["old_prefix"])
        self._new = str(data["new_prefix"])
        self._name = str(data.get("name") or "")
        self._swop_name = str(data.get("swop_name") or "")

    async def async_step_init(self, user_input: dict[str, str] | None = None) -> RepairsFlowResult:
        """Start with the confirmation."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> RepairsFlowResult:
        """Show the renames and apply them when confirmed."""
        renames = self._renames()
        if user_input is not None:
            if self._any_target_taken(renames):
                return self.async_abort(reason="entity_id_taken")
            registry = er.async_get(self.hass)
            for old, new in renames.items():
                registry.async_update_entity(old, new_entity_id=new)
            async_check_students(self.hass)
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            description_placeholders={
                "name": self._name,
                "swop_name": self._swop_name,
                "old_prefix": self._old,
                "new_prefix": self._new,
                "renames": "\n".join(f"- `{old}` → `{new}`" for old, new in renames.items()),
            },
        )

    def _any_target_taken(self, renames: dict[str, str]) -> bool:
        """Return True if any new ID is in use, so no rename is applied half-way."""
        registry = er.async_get(self.hass)
        return any(
            new in renames
            or registry.async_is_registered(new)
            or not self.hass.states.async_available(new)
            for new in renames.values()
        )

    def _renames(self) -> dict[str, str]:
        """Return old → new entity IDs of the entry's entities with the old prefix."""
        registry = er.async_get(self.hass)
        renames = {}
        for entity in er.async_entries_for_config_entry(registry, self._entry_id):
            platform, object_id = entity.entity_id.split(".", 1)
            if object_id.startswith(f"{self._old}_"):
                rest = object_id.removeprefix(f"{self._old}_")
                renames[entity.entity_id] = f"{platform}.{self._new}_{rest}"
        return renames
