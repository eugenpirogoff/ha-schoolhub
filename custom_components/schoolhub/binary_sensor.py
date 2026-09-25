"""New messages in the SWOP messenger (only whether there are any, not their content)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SchoolhubConfigEntry, SwopCoordinator
from .entity import SchoolhubEntity, school_device

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the messenger sensor if the school uses the SWOP messenger.

    It is created even if the messenger could not be reached yet; it stays
    unavailable until a refresh gets the unread chats.
    """
    coordinator = entry.runtime_data
    if isinstance(coordinator, SwopCoordinator) and coordinator.messenger_enabled:
        async_add_entities([NewMessagesSensor(coordinator)])


class NewMessagesSensor(SchoolhubEntity[SwopCoordinator], BinarySensorEntity):
    """On while at least one messenger chat has unread messages."""

    def __init__(self, coordinator: SwopCoordinator) -> None:
        super().__init__(
            coordinator, "new_messages", school_device(coordinator), coordinator.client.school
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.unread_chats is not None

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.unread_chats)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "unread_chats": self.coordinator.data.unread_chats,
            "messenger_url": f"{self.coordinator.client.base_url}/messenger",
        }
