"""SchoolHub: SWOP school portal and Drei Köche school lunch for Home Assistant."""

from __future__ import annotations

import aiohttp

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
)

from .api import DreiKoecheClient, SwopClient
from .checks import async_check_students, async_clear_issues
from .const import CONF_SERVICE, SERVICE_DREI_KOECHE, SERVICE_SWOP
from .coordinator import DreiKoecheCoordinator, SchoolhubConfigEntry, SwopCoordinator
from .entity import school_identifier, student_identifier
from .store import LocalState

PLATFORMS: dict[str, list[Platform]] = {
    SERVICE_SWOP: [
        Platform.BINARY_SENSOR,
        Platform.CALENDAR,
        Platform.SELECT,
        Platform.SENSOR,
        Platform.TODO,
    ],
    SERVICE_DREI_KOECHE: [Platform.CALENDAR, Platform.SENSOR],
}


async def async_setup_entry(hass: HomeAssistant, entry: SchoolhubConfigEntry) -> bool:
    """Set up one SWOP or Drei Köche login."""
    coordinator: SwopCoordinator | DreiKoecheCoordinator
    if entry.data[CONF_SERVICE] == SERVICE_SWOP:
        # SWOP keeps the login in a cookie, so each login gets its own jar.
        session = async_create_clientsession(hass, cookie_jar=aiohttp.CookieJar())
        client = SwopClient(
            session,
            entry.data[CONF_URL],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
        )
        coordinator = SwopCoordinator(hass, entry, client)
    else:
        # Drei Köche uses a bearer token, so its session stores no cookies.
        # Created during entry setup, it is detached when the entry unloads.
        coordinator = DreiKoecheCoordinator(
            hass,
            entry,
            DreiKoecheClient(
                async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar()),
                entry.data[CONF_USERNAME],
                entry.data[CONF_PASSWORD],
            ),
        )

    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS[entry.data[CONF_SERVICE]])
    # Check that SWOP and Drei Köche children match, and keep each child's
    # class on its device current (e.g. a new school year), now and after
    # each refresh.

    # Listeners also run for local changes (a tick, a picked day); only new
    # data from a refresh needs these checks.
    last_data: object = None

    @callback
    def async_refreshed() -> None:
        nonlocal last_data
        if coordinator.data is last_data:
            return
        last_data = coordinator.data
        async_check_students(hass)
        async_update_classes(hass, entry)

    async_refreshed()
    entry.async_on_unload(coordinator.async_add_listener(async_refreshed))
    return True


@callback
def async_update_classes(hass: HomeAssistant, entry: SchoolhubConfigEntry) -> None:
    """Update the class shown on each child's device if it changed."""
    coordinator = entry.runtime_data
    if not coordinator.last_update_success:
        return
    service = entry.data[CONF_SERVICE]
    students = (
        [child.student for child in coordinator.data.students.values()]
        if isinstance(coordinator, SwopCoordinator)
        else [coordinator.data.student]
    )
    registry = dr.async_get(hass)
    for student in students:
        device = registry.async_get_device_by_identifier(
            student_identifier(service, student.key), entry.entry_id
        )
        if device and device.model_id != student.class_name:
            registry.async_update_device(device.id, model_id=student.class_name)


async def async_unload_entry(hass: HomeAssistant, entry: SchoolhubConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, PLATFORMS[entry.data[CONF_SERVICE]]
    )
    if not unloaded:
        return False
    if isinstance(entry.runtime_data, SwopCoordinator):
        await entry.runtime_data.state.async_flush()
    else:
        # A disabled or unloaded lunch login is not checked, so drop its issues.
        async_clear_issues(hass, entry.entry_id)
    # The other logins are checked again without this one (e.g. removing the
    # last SWOP login leaves nothing to compare lunch logins with).
    async_check_students(hass)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SchoolhubConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow deleting the device of a child the service no longer returns."""
    if entry.state is not ConfigEntryState.LOADED:
        # Not loaded: the current children are unknown, so keep every device.
        return False
    coordinator = entry.runtime_data
    service = entry.data[CONF_SERVICE]
    if isinstance(coordinator, SwopCoordinator):
        current = {student_identifier(service, key) for key in coordinator.data.students}
        current.add(school_identifier(coordinator.client.school))
    else:
        current = {student_identifier(service, coordinator.data.student.key)}
    return not device.identifiers & current


async def async_remove_entry(hass: HomeAssistant, entry: SchoolhubConfigEntry) -> None:
    """Delete stored state and check issues when a login is removed."""
    await LocalState.async_remove(hass, entry.entry_id)
    async_clear_issues(hass, entry.entry_id)
    # E.g. removing the last SWOP login leaves nothing to compare lunch logins with.
    async_check_students(hass)
