"""Pickers for a child's dashboard: the homework day and the timetable week.

Both go back to their default (today, this week) by themselves.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.select import SelectEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_call_later, async_track_time_change

from .const import CONF_WEEKS_FUTURE, CONF_WEEKS_PAST, DEFAULTS, SERVICE_SWOP
from .coordinator import SchoolhubConfigEntry, SwopCoordinator
from .entity import (
    HOMEWORK_DAYS,
    SchoolhubEntity,
    async_add_student_entities,
    homework_day,
    student_device,
)

PARALLEL_UPDATES = 0

# A day or week picked by hand switches back to today / this week after this
# long, so the view usually opens on the present.
RESET_AFTER = timedelta(minutes=15)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the homework day and timetable week pickers per SWOP child."""
    coordinator = entry.runtime_data
    if not isinstance(coordinator, SwopCoordinator):
        return
    async_add_student_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda key: [
            HomeworkDaySelect(coordinator, key),
            TimetableWeekSelect(coordinator, key),
        ],
    )


class ResettingSelect(SchoolhubEntity[SwopCoordinator], SelectEntity):
    """A child's picker that returns to its default after a while and at midnight."""

    _entity_key: str

    def __init__(self, coordinator: SwopCoordinator, student_key: str) -> None:
        self._student_key = student_key
        student = coordinator.data.students[student_key].student
        super().__init__(
            coordinator,
            self._entity_key,
            student_device(SERVICE_SWOP, student),
            student_key,
        )
        self._cancel_reset: CALLBACK_TYPE | None = None

    @property
    def available(self) -> bool:
        return super().available and self._student_key in self.coordinator.data.students

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_track_time_change(self.hass, self._reset, hour=0, minute=0, second=0)
        )
        self.async_on_remove(self._cancel_pending_reset)

    async def async_select_option(self, option: str) -> None:
        """Show another option; switch back to the default after a while."""
        self._cancel_pending_reset()
        self._cancel_reset = async_call_later(self.hass, RESET_AFTER, self._reset)
        self._show(option)

    @callback
    def _reset(self, _now: datetime | None = None) -> None:
        self._cancel_reset = None
        self._show(None)

    @callback
    def _show(self, option: str | None) -> None:
        """Show an option (None: the default) and write the new state."""
        raise NotImplementedError

    @callback
    def _cancel_pending_reset(self) -> None:
        if self._cancel_reset:
            self._cancel_reset()
            self._cancel_reset = None


class HomeworkDaySelect(ResettingSelect):
    """Which weekday the homework view shows. Resets to today by itself.

    The pick is kept on the coordinator, where the day's homework list reads it.
    """

    _attr_options = HOMEWORK_DAYS
    _entity_key = "homework_day"

    @property
    def current_option(self) -> str:
        return homework_day(self.coordinator, self._student_key)

    @callback
    def _show(self, option: str | None) -> None:
        """Pick a day (None: today) and update the picker and the day's list."""
        if option is None:
            self.coordinator.homework_days.pop(self._student_key, None)
        else:
            self.coordinator.homework_days[self._student_key] = option
        self.coordinator.async_update_listeners()


class TimetableWeekSelect(ResettingSelect):
    """Which week a timetable view shows, relative to the current school week.

    Options are week offsets ("-2" … "0" … "4") within the fetched weeks, so
    select.select_previous / select_next work as arrows. Resets to "0".
    """

    _entity_key = "timetable_week"

    def __init__(self, coordinator: SwopCoordinator, student_key: str) -> None:
        super().__init__(coordinator, student_key)
        options = coordinator.config_entry.options
        defaults = DEFAULTS[SERVICE_SWOP]
        past = int(options.get(CONF_WEEKS_PAST, defaults[CONF_WEEKS_PAST]))
        future = int(options.get(CONF_WEEKS_FUTURE, defaults[CONF_WEEKS_FUTURE]))
        self._attr_options = [str(offset) for offset in range(-past, future + 1)]
        self._attr_current_option = "0"

    @callback
    def _show(self, option: str | None) -> None:
        self._attr_current_option = option or "0"
        self.async_write_ha_state()
