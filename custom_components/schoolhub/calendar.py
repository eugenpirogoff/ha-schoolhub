"""Calendars: timetable (SWOP) and ordered meals (Drei Köche) per child."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import SERVICE_DREI_KOECHE, SERVICE_SWOP
from .coordinator import DreiKoecheCoordinator, SchoolhubConfigEntry, SwopCoordinator
from .entity import (
    SchoolhubEntity,
    async_add_student_entities,
    school_week,
    student_device,
    text,
)
from .models import Lesson, Meal, SwopStudentData, subject_icon

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up calendars for a config entry."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, SwopCoordinator):
        async_add_student_entities(
            entry,
            coordinator,
            async_add_entities,
            lambda key: [TimetableCalendar(coordinator, key)],
        )
    else:
        async_add_entities([MealCalendar(coordinator)])


def _overlaps(event: CalendarEvent, start: datetime, end: datetime) -> bool:
    return event.start_datetime_local < end and event.end_datetime_local > start


def _next_event(events: list[CalendarEvent]) -> CalendarEvent | None:
    """Return the event in progress, or else the next one."""
    now = dt_util.now()
    return next((event for event in events if event.end_datetime_local > now), None)


class TimetableCalendar(SchoolhubEntity[SwopCoordinator], CalendarEntity):
    """Lessons of one child."""

    _unrecorded_attributes = frozenset({"lessons", "week_start", "week_homework"})

    def __init__(self, coordinator: SwopCoordinator, student_key: str) -> None:
        self._student_key = student_key
        student = coordinator.data.students[student_key].student
        super().__init__(
            coordinator,
            "timetable",
            student_device(SERVICE_SWOP, student),
            student_key,
        )

    @property
    def _child(self) -> SwopStudentData | None:
        return self.coordinator.data.students.get(self._student_key)

    @property
    def available(self) -> bool:
        return super().available and self._child is not None

    @property
    def event(self) -> CalendarEvent | None:
        # Cancelled lessons are listed, but do not turn the calendar on.
        return _next_event(self._events(include_cancelled=False))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the lessons of all fetched weeks and this week's homework, for dashboards.

        week_start is the Monday of the current school week (from Saturday on,
        the coming week); a dashboard can show other weeks relative to it.
        """
        child = self._child
        if not child:
            return {}
        monday, friday = school_week()
        return {
            "week_start": monday.isoformat(),
            "lessons": [
                {
                    "day": lesson.day.isoformat(),
                    "weekday": lesson.day.weekday(),
                    "number": lesson.number,
                    "start": lesson.start.strftime("%H:%M") if lesson.start else None,
                    "end": lesson.end.strftime("%H:%M") if lesson.end else None,
                    "subject": lesson.subject,
                    "icon": subject_icon(lesson.subject),
                    "room": lesson.room,
                    "cancelled": lesson.cancelled,
                    "substituted": lesson.substituted,
                }
                for lesson in child.lessons
            ],
            # Homework given or due this week, e.g. for a per-day homework view.
            "week_homework": [
                {
                    "uid": item.uid,
                    "subject": item.subject,
                    "icon": subject_icon(item.subject),
                    "text": item.text,
                    "assigned": item.assigned.isoformat(),
                    "due": item.due.isoformat() if item.due else None,
                    "done": self.coordinator.state.is_done(item.uid),
                }
                for item in child.homework
                if monday <= item.assigned <= friday
                or (item.due is not None and monday <= item.due <= friday)
            ],
        }

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [event for event in self._events() if _overlaps(event, start_date, end_date)]

    def _events(self, include_cancelled: bool = True) -> list[CalendarEvent]:
        child = self._child
        if not child:
            return []
        return [
            self._to_event(lesson)
            for lesson in child.lessons
            if include_cancelled or not lesson.cancelled
        ]

    def _to_event(self, lesson: Lesson) -> CalendarEvent:
        summary = lesson.subject
        if lesson.cancelled:
            summary = f"{summary} ({text(self.hass, 'cancelled')})"
        elif lesson.substituted:
            summary = f"{summary} ({text(self.hass, 'substituted')})"
        details = [
            (text(self.hass, "teacher"), lesson.teacher),
            (text(self.hass, "comment"), lesson.comment),
            (text(self.hass, "topic"), lesson.topic),
            (text(self.hass, "homework"), lesson.homework),
        ]
        start: date | datetime
        end: date | datetime
        if lesson.start and lesson.end and lesson.start < lesson.end:
            tz = dt_util.get_default_time_zone()
            start = datetime.combine(lesson.day, lesson.start, tzinfo=tz)
            end = datetime.combine(lesson.day, lesson.end, tzinfo=tz)
        else:
            start, end = lesson.day, lesson.day + timedelta(days=1)
        return CalendarEvent(
            start=start,
            end=end,
            summary=summary,
            description="\n".join(f"{label}: {value}" for label, value in details if value) or None,
            location=lesson.room,
            uid=lesson.uid,
        )


class MealCalendar(SchoolhubEntity[DreiKoecheCoordinator], CalendarEntity):
    """Ordered lunches of one child."""

    _unrecorded_attributes = frozenset({"meals", "week_start"})

    def __init__(self, coordinator: DreiKoecheCoordinator) -> None:
        student = coordinator.data.student
        super().__init__(
            coordinator,
            "meals",
            student_device(SERVICE_DREI_KOECHE, student),
            student.key,
        )

    @property
    def event(self) -> CalendarEvent | None:
        return _next_event(self._events())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the ordered meals of all fetched weeks, e.g. for a timetable.

        week_start is the Monday of the current school week, as on the timetable.
        """
        monday, _ = school_week()
        return {
            "week_start": monday.isoformat(),
            "meals": [
                {
                    "day": meal.day.isoformat(),
                    "weekday": meal.day.weekday(),
                    "menu": meal.menu,
                    "allergens": meal.allergens,
                }
                for meal in self.coordinator.data.meals
            ],
        }

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [event for event in self._events() if _overlaps(event, start_date, end_date)]

    def _events(self) -> list[CalendarEvent]:
        return [self._to_event(meal) for meal in self.coordinator.data.meals]

    def _to_event(self, meal: Meal) -> CalendarEvent:
        return CalendarEvent(
            start=meal.day,
            end=meal.day + timedelta(days=1),
            summary=meal.menu,
            description=f"{text(self.hass, 'allergens')}: {meal.allergens}"
            if meal.allergens
            else None,
            uid=f"drei-koeche-{meal.order_id}",
        )
