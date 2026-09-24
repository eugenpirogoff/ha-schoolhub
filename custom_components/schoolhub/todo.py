"""Homework to-do list per child.

Items come from SWOP. Ticking one off is stored in Home Assistant only; SWOP
is never changed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SERVICE_SWOP
from .coordinator import SchoolhubConfigEntry, SwopCoordinator
from .entity import (
    HOMEWORK_DAYS,
    SchoolhubEntity,
    async_add_student_entities,
    day_label,
    default_homework_day,
    homework_day,
    school_week,
    short_date,
    student_device,
    text,
)
from .models import Homework, SwopStudentData, is_overdue, subject_icon

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up homework lists for a SWOP config entry."""
    coordinator = entry.runtime_data
    if not isinstance(coordinator, SwopCoordinator):
        return
    async_add_student_entities(
        entry,
        coordinator,
        async_add_entities,
        lambda key: [
            HomeworkTodoList(coordinator, key),
            HomeworkOfDayTodoList(coordinator, key),
        ],
    )


def _summary(homework: Homework) -> str:
    """Return the item title: only the subject, e.g. "📖 Deutsch".

    The due date is the item's due date, shown by the to-do card itself, and
    the task is the description, so nothing is said twice.
    """
    return f"{subject_icon(homework.subject)} {homework.subject}"


def _description(hass: HomeAssistant, homework: Homework, today: date) -> str:
    """Return the task and one line with when it was given and by whom.

    E.g. "Lesen S. 12" and "📅 aufgegeben gestern (23.09.) · 👤 Ögün, A.".
    """
    assigned = day_label(hass, homework.assigned, today)
    if assigned in (text(hass, "today"), text(hass, "yesterday")):
        assigned += f" ({short_date(hass, homework.assigned)})"
    details = f"📅 {text(hass, 'assigned')} {assigned}"
    if not homework.due:
        details += f" · {text(hass, 'no_due_date')}"
    if homework.teacher:
        details += f" · 👤 {homework.teacher}"
    return f"{homework.text}\n{details}"


class HomeworkTodoList(SchoolhubEntity[SwopCoordinator], TodoListEntity):
    """Homework of one child."""

    _attr_supported_features = TodoListEntityFeature.UPDATE_TODO_ITEM
    _entity_key = "homework"

    def __init__(self, coordinator: SwopCoordinator, student_key: str) -> None:
        self._student_key = student_key
        student = coordinator.data.students[student_key].student
        super().__init__(
            coordinator,
            self._entity_key,
            student_device(SERVICE_SWOP, student),
            student_key,
        )

    @property
    def _child(self) -> SwopStudentData | None:
        return self.coordinator.data.students.get(self._student_key)

    @property
    def available(self) -> bool:
        return super().available and self._child is not None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Overdue homework drops off the list at midnight (local time). The
        # list only depends on the date, so there is nothing to redo in between.
        self.async_on_remove(
            async_track_time_change(self.hass, self._midnight, hour=0, minute=0, second=0)
        )

    @callback
    def _midnight(self, _now: datetime) -> None:
        self.async_write_ha_state()

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return homework until its due date has passed, ticked off or not.

        Ticked-off items stay visible (as completed) until they are due, so
        what a child finished today is still there to see.
        """
        child = self._child
        if not child:
            return []
        today = dt_util.now().date()
        return [self._to_item(item, today) for item in self._homework(child, today)]

    def _homework(self, child: SwopStudentData, today: date) -> list[Homework]:
        """Return the homework this list shows."""
        return [item for item in child.homework if not is_overdue(item, today)]

    def _to_item(self, homework: Homework, today: date) -> TodoItem:
        return TodoItem(
            uid=homework.uid,
            summary=_summary(homework),
            status=TodoItemStatus.COMPLETED
            if self.coordinator.state.is_done(homework.uid)
            else TodoItemStatus.NEEDS_ACTION,
            due=homework.due,
            description=_description(self.hass, homework, today),
        )

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Tick off or reopen homework. Only the status can be changed."""
        child = self._child
        homework = next((h for h in child.homework if h.uid == item.uid), None) if child else None
        if homework is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="unknown_homework"
            )
        # A status-only update sends back the item's summary, a rename the new
        # one. Older versions added date labels after " · " (e.g. an automation
        # or a list rendered before an update): accept those, reject the rest.
        summary = _summary(homework)
        if item.summary not in (None, summary) and not item.summary.startswith(f"{summary} · "):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="homework_read_only"
            )
        self.coordinator.state.set_done(item.uid, item.status == TodoItemStatus.COMPLETED)
        # Refresh the list and the open-homework sensor.
        self.coordinator.async_update_listeners()


class HomeworkOfDayTodoList(HomeworkTodoList):
    """Homework of the weekday picked for a child (see the homework day select).

    Shows what is due or was given on that day of the current school week. On
    the default day (today; Monday at weekends) it also shows everything still
    open, so homework given earlier and due later is not missed.
    """

    _entity_key = "homework_of_day"

    def _homework(self, child: SwopStudentData, today: date) -> list[Homework]:
        picked = homework_day(self.coordinator, self._student_key)
        monday, _ = school_week()
        day = monday + timedelta(days=HOMEWORK_DAYS.index(picked))
        show_open = picked == default_homework_day()
        return [
            item
            for item in super()._homework(child, today)
            if day in (item.assigned, item.due)
            or (show_open and not self.coordinator.state.is_done(item.uid))
        ]
