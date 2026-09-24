"""Summary sensors: next lesson, open homework, news, today's meal."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import MAX_ATTRIBUTE_ITEMS, MAX_TEXT_LENGTH, SERVICE_DREI_KOECHE, SERVICE_SWOP
from .coordinator import DreiKoecheCoordinator, SchoolhubConfigEntry, SwopCoordinator
from .entity import (
    ClockMixin,
    SchoolhubEntity,
    async_add_student_entities,
    school_device,
    student_device,
)
from .models import (
    DreiKoecheData,
    Homework,
    Lesson,
    NewsPost,
    SwopData,
    SwopStudentData,
    is_overdue,
)

PARALLEL_UPDATES = 0

# HA limits a state to 255 characters.
MAX_STATE_LENGTH = 255


@dataclass(frozen=True, kw_only=True)
class SchoolhubSensorDescription[T](SensorEntityDescription):
    """A sensor computed from a data object of type T."""

    value_fn: Callable[[T], StateType]
    attrs_fn: Callable[[T], dict[str, Any]]
    # Re-evaluate every minute, for values that depend on the time of day.
    clock: bool = False


@dataclass(frozen=True, slots=True)
class ChildContext:
    """What a child's sensor needs: its data and which homework is ticked off."""

    child: SwopStudentData
    is_done: Callable[[str], bool]


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------


def _truncate(value: str | None, length: int = MAX_STATE_LENGTH) -> str | None:
    if value is None or len(value) <= length:
        return value
    return value[: length - 1] + "…"


def _local(day: date, moment: time | None) -> datetime | None:
    """Return a lesson time on a day as a local datetime."""
    if moment is None:
        return None
    return datetime.combine(day, moment, tzinfo=dt_util.get_default_time_zone())


def _next_lesson(context: ChildContext) -> Lesson | None:
    """Return the lesson in progress or the next one that takes place."""
    now = dt_util.now()
    return next(
        (
            lesson
            for lesson in context.child.lessons
            if not lesson.cancelled
            and (end := _local(lesson.day, lesson.end)) is not None
            and end > now
        ),
        None,
    )


def _next_lesson_attrs(context: ChildContext) -> dict[str, Any]:
    lesson = _next_lesson(context)
    if lesson is None:
        return {}
    start, end = _local(lesson.day, lesson.start), _local(lesson.day, lesson.end)
    today = dt_util.now().date()
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "lesson_number": lesson.number,
        "teacher": lesson.teacher,
        "room": lesson.room,
        "substituted": lesson.substituted,
        "today": [
            {
                "number": item.number,
                "start": item.start.strftime("%H:%M") if item.start else None,
                "subject": item.subject,
                "cancelled": item.cancelled,
            }
            for item in context.child.lessons
            if item.day == today
        ],
    }


def open_homework(context: ChildContext) -> list[Homework]:
    """Return homework that is not ticked off and not overdue."""
    today = dt_util.now().date()
    return [
        item
        for item in context.child.homework
        if not context.is_done(item.uid) and not is_overdue(item, today)
    ]


def _open_homework_attrs(context: ChildContext) -> dict[str, Any]:
    return {
        "items": [
            {
                "subject": item.subject,
                "text": _truncate(item.text, MAX_TEXT_LENGTH),
                "due": (item.due or item.assigned).isoformat(),
            }
            for item in open_homework(context)[:MAX_ATTRIBUTE_ITEMS]
        ]
    }


def _latest_title(posts: list[NewsPost]) -> str | None:
    return _truncate(posts[0].title) if posts else None


def _post_attributes(posts: list[NewsPost]) -> dict[str, Any]:
    if not posts:
        return {}
    latest = posts[0]
    return {
        "author": latest.author,
        "published": latest.published.isoformat() if latest.published else None,
        "text": _truncate(latest.text, MAX_TEXT_LENGTH),
        "posts": [
            {
                "title": post.title,
                "author": post.author,
                "published": post.published.isoformat() if post.published else None,
                "text": _truncate(post.text, MAX_TEXT_LENGTH),
            }
            for post in posts[:MAX_ATTRIBUTE_ITEMS]
        ],
    }


def _meals_today(data: DreiKoecheData) -> str | None:
    today = dt_util.now().date()
    return _truncate(" | ".join(meal.menu for meal in data.meals if meal.day == today) or None)


def _meal_attrs(data: DreiKoecheData) -> dict[str, Any]:
    today = dt_util.now().date()
    allergens = ",".join(m.allergens for m in data.meals if m.day == today and m.allergens)
    return {
        "allergens": allergens or None,
        "upcoming": [
            {"date": meal.day.isoformat(), "menu": meal.menu, "allergens": meal.allergens}
            for meal in data.meals
            if meal.day > today
        ][:MAX_ATTRIBUTE_ITEMS],
    }


# ---------------------------------------------------------------------------
# Descriptions
# ---------------------------------------------------------------------------

CHILD_SENSORS: tuple[SchoolhubSensorDescription[ChildContext], ...] = (
    SchoolhubSensorDescription(
        key="next_lesson",
        translation_key="next_lesson",
        value_fn=lambda context: (
            _truncate(lesson.subject) if (lesson := _next_lesson(context)) else None
        ),
        attrs_fn=_next_lesson_attrs,
        clock=True,
    ),
    SchoolhubSensorDescription(
        key="open_homework",
        translation_key="open_homework",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda context: len(open_homework(context)),
        attrs_fn=_open_homework_attrs,
        clock=True,
    ),
    SchoolhubSensorDescription(
        key="class_news",
        translation_key="class_news",
        value_fn=lambda context: _latest_title(context.child.class_news),
        attrs_fn=lambda context: _post_attributes(context.child.class_news),
    ),
)

SCHOOL_NEWS = SchoolhubSensorDescription[SwopData](
    key="school_news",
    translation_key="school_news",
    value_fn=lambda data: _latest_title(data.school_news),
    attrs_fn=lambda data: _post_attributes(data.school_news),
)

MEAL_TODAY = SchoolhubSensorDescription[DreiKoecheData](
    key="meal_today",
    translation_key="meal_today",
    value_fn=_meals_today,
    attrs_fn=_meal_attrs,
    clock=True,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SchoolhubConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for a config entry."""
    coordinator = entry.runtime_data
    if isinstance(coordinator, SwopCoordinator):
        async_add_entities([SchoolNewsSensor(coordinator, SCHOOL_NEWS)])
        async_add_student_entities(
            entry,
            coordinator,
            async_add_entities,
            lambda key: [
                ChildSensor(coordinator, description, key) for description in CHILD_SENSORS
            ],
        )
    else:
        async_add_entities([MealSensor(coordinator, MEAL_TODAY)])


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


class _DescribedSensor[CoordinatorT: SwopCoordinator | DreiKoecheCoordinator, T](
    ClockMixin, SchoolhubEntity[CoordinatorT], SensorEntity
):
    """A sensor whose state and attributes come from its description."""

    entity_description: SchoolhubSensorDescription[T]

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.entity_description.clock:
            self._start_clock()

    def _source(self) -> T | None:
        raise NotImplementedError

    @property
    def native_value(self) -> StateType:
        source = self._source()
        return self.entity_description.value_fn(source) if source is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        source = self._source()
        return self.entity_description.attrs_fn(source) if source is not None else {}


class ChildSensor(_DescribedSensor[SwopCoordinator, ChildContext]):
    """A sensor of one SWOP child."""

    # Teacher and author names are personal; lesson details change every lesson.
    _unrecorded_attributes = frozenset(
        {
            "today",
            "items",
            "text",
            "posts",
            "author",
            "teacher",
            "room",
            "start",
            "end",
            "lesson_number",
            "substituted",
        }
    )

    def __init__(
        self,
        coordinator: SwopCoordinator,
        description: SchoolhubSensorDescription[ChildContext],
        student_key: str,
    ) -> None:
        self.entity_description = description
        self._student_key = student_key
        student = coordinator.data.students[student_key].student
        super().__init__(
            coordinator,
            description.key,
            student_device(SERVICE_SWOP, student),
            student_key,
        )

    @property
    def available(self) -> bool:
        return super().available and self._student_key in self.coordinator.data.students

    def _source(self) -> ChildContext | None:
        child = self.coordinator.data.students.get(self._student_key)
        return ChildContext(child, self.coordinator.state.is_done) if child else None


class SchoolNewsSensor(_DescribedSensor[SwopCoordinator, SwopData]):
    """Latest post of the school-wide news."""

    _unrecorded_attributes = frozenset({"author", "text", "posts"})

    def __init__(
        self, coordinator: SwopCoordinator, description: SchoolhubSensorDescription[SwopData]
    ) -> None:
        self.entity_description = description
        super().__init__(
            coordinator, description.key, school_device(coordinator), coordinator.client.school
        )

    def _source(self) -> SwopData:
        return self.coordinator.data


class MealSensor(_DescribedSensor[DreiKoecheCoordinator, DreiKoecheData]):
    """Today's ordered lunch, with the next days as attribute."""

    _unrecorded_attributes = frozenset({"upcoming"})

    def __init__(
        self,
        coordinator: DreiKoecheCoordinator,
        description: SchoolhubSensorDescription[DreiKoecheData],
    ) -> None:
        self.entity_description = description
        student = coordinator.data.student
        super().__init__(
            coordinator,
            description.key,
            student_device(SERVICE_DREI_KOECHE, student),
            student.key,
        )

    def _source(self) -> DreiKoecheData:
        return self.coordinator.data
