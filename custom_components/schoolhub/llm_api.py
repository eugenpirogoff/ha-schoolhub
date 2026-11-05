"""LLM API: lets conversation agents read homework, timetable, lunch and news.

Assist only shows an LLM the state of an entity, not its attributes, so the
lists behind the sensors (homework, news posts, meals) never reach a model.
This API offers them as tools instead, with absolute dates, weekday names and
the distance to today spelled out, so a model does not have to work them out.
Agents use it once "SchoolHub" is picked as an LLM API in their settings.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

try:
    # Since Home Assistant 2026.10 tool parameters are probatio schemas; it
    # cannot convert voluptuous ones for the model. Same API, so one name.
    import probatio as vol
except ImportError:  # Home Assistant 2026.9
    import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.util import dt as dt_util
from homeassistant.util.json import JsonObjectType

from .const import DOMAIN, SERVICE_DREI_KOECHE, SERVICE_SWOP
from .coordinator import (
    DreiKoecheCoordinator,
    SchoolhubConfigEntry,
    SwopCoordinator,
    fetch_range,
)
from .entity import school_label
from .models import (
    Homework,
    Lesson,
    NewsPost,
    Student,
    SwopStudentData,
    fold_name,
    is_overdue,
    match_student,
)

API_NAME = "SchoolHub"

# LLMContext.platform of requests from the Model Context Protocol server integration.
MCP_SERVER_PLATFORM = "mcp_server"

# Longest stretch of days one call returns.
MAX_DAYS = 14
MAX_POSTS = 10
# A post longer than this is cut; the full text is in the school portal.
MAX_POST_LENGTH = 2000

WEEKDAY_NAMES = {
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}

PROMPT = """\
SchoolHub has school data for these children: {children}.
Today is {weekday}, {today}; tomorrow is {tomorrow_weekday}, {tomorrow}.
Use the SchoolHub tools for questions about homework, the timetable, school \
lunch and news from the school or a child's class. Each tool takes the \
child's first name; leave it out to get all children. When the question is \
about several children, answer for each of them. Always answer in the \
language of the question; the tool results are data, not wording to copy.
Texts come from the school portal as teachers wrote them, usually in German. \
Common abbreviations: AB = Arbeitsblatt (worksheet), AH = Arbeitsheft \
(workbook), KA = Klassenarbeit (class test), LZK = Lernzielkontrolle (test), \
HA = Hausaufgabe (homework), SU = Sachunterricht, S. = Seite (page), \
Nr. = Nummer (number).\
"""


def _weekday(hass: HomeAssistant, day: date) -> str:
    language = "de" if hass.config.language.startswith("de") else "en"
    return WEEKDAY_NAMES[language][day.weekday()]


def _day(hass: HomeAssistant, prefix: str, day: date | None, today: date) -> dict[str, Any]:
    """Return a date as e.g. {"due": "2026-11-05", "due_weekday": ..., "due_in_days": 1}."""
    if day is None:
        return {prefix: None}
    return {
        prefix: day.isoformat(),
        f"{prefix}_weekday": _weekday(hass, day),
        f"{prefix}_in_days": (day - today).days,
    }


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    """Drop empty values, so a model reads only what is there."""
    # Not "in (False, ...)": that would drop 0, e.g. lesson 0 before the first lesson.
    return {
        key: value
        for key, value in values.items()
        if value is not None and value is not False and value not in ("", [])
    }


# Words small models pass instead of a date, mapped to days from today.
RELATIVE_DAYS = {
    "heute": 0,
    "today": 0,
    "morgen": 1,
    "tomorrow": 1,
    "übermorgen": 2,
    "uebermorgen": 2,
    "day after tomorrow": 2,
}


def _one_line(text: str) -> str:
    """Join the lines of a teacher's text, dropping list dashes: "- AH 26\n- AB" -> "AH 26; AB"."""
    lines = [line.strip().lstrip("-•*").strip() for line in text.splitlines()]
    return "; ".join(line for line in lines if line)


def _parse_day(value: str | None, today: date) -> date:
    """Return a date from YYYY-MM-DD, "morgen"/"tomorrow" or a weekday name.

    A weekday means its next occurrence, today included.
    """
    if not value:
        return today
    word = " ".join(value.casefold().split())
    if word in RELATIVE_DAYS:
        return today + timedelta(days=RELATIVE_DAYS[word])
    for names in WEEKDAY_NAMES.values():
        folded = [name.casefold() for name in names]
        if word in folded:
            return today + timedelta(days=(folded.index(word) - today.weekday()) % 7)
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise HomeAssistantError(
            f"Invalid date {value!r}, use YYYY-MM-DD, e.g. tomorrow is "
            f"{(today + timedelta(days=1)).isoformat()}"
        ) from err


@dataclass(slots=True)
class _Child:
    """One child with its data from every service."""

    student: Student
    swop: tuple[SwopCoordinator, SwopStudentData] | None = None
    lunch: list[DreiKoecheCoordinator] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Return the first given name only: middle names stay out of the chat."""
        return self.student.first_name.split(" ")[0] or self.student.display_name


def _loaded_coordinators[T](hass: HomeAssistant, kind: type[T]) -> list[T]:
    entries: Iterable[SchoolhubConfigEntry] = hass.config_entries.async_entries(DOMAIN)
    return [
        entry.runtime_data
        for entry in entries
        if entry.state is ConfigEntryState.LOADED
        and isinstance(entry.runtime_data, kind)
        and entry.runtime_data.data is not None
    ]


def _children(hass: HomeAssistant) -> list[_Child]:
    """Return every child, its lunch logins matched to its SWOP data by name."""
    children: dict[str, _Child] = {}
    for coordinator in _loaded_coordinators(hass, SwopCoordinator):
        for key, child in coordinator.data.students.items():
            children.setdefault(key, _Child(child.student, swop=(coordinator, child)))
    swop_students = [child.student for child in children.values()]
    for coordinator in _loaded_coordinators(hass, DreiKoecheCoordinator):
        student = coordinator.data.student
        match = match_student(student, swop_students)
        key = match.student.key if match else student.key
        children.setdefault(key, _Child(student)).lunch.append(coordinator)
    return sorted(children.values(), key=lambda child: fold_name(child.name))


def _select(children: list[_Child], name: str | None) -> list[_Child]:
    """Return the child called `name` (first or full name), or all children."""
    if not name:
        return children
    wanted = fold_name(name)
    found = [
        child
        for child in children
        if wanted
        in (
            fold_name(child.name),
            fold_name(child.student.first_name),
            fold_name(f"{child.student.first_name} {child.student.last_name}"),
        )
    ]
    if not found:
        known = ", ".join(child.name for child in children) or "none"
        raise HomeAssistantError(f"No child named {name!r}. Known children: {known}")
    return found


def _child_info(child: _Child) -> dict[str, Any]:
    return _clean({"child": child.name, "class": child.student.class_name})


def _covered(
    coordinators: Iterable[DreiKoecheCoordinator | SwopCoordinator], service: str
) -> tuple[date, date]:
    """Return the first and last day the data covers."""
    ranges = [fetch_range(c.config_entry, service) for c in coordinators]
    return min(r[0] for r in ranges), max(r[1] for r in ranges)


def _requested_days(args: dict[str, Any], today: date) -> list[date]:
    first = _parse_day(args.get("date"), today)
    return [first + timedelta(days=n) for n in range(args.get("days", 1))]


def _per_day(
    hass: HomeAssistant,
    days: list[date],
    today: date,
    covered: tuple[date, date],
    key: str,
    items: dict[date, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return the items of each day; days outside the fetched range say so."""
    return [
        {
            **_day(hass, "date", day, today),
            **(
                {key: items.get(day, [])}
                if covered[0] <= day <= covered[1]
                else {"note": "No data fetched for this day"}
            ),
        }
        for day in days
    ]


class _SchoolhubTool(llm.Tool):
    """A tool that looks up the children on each call."""

    # Since Home Assistant 2026.10 a tool names the integration providing it,
    # and can say it only reads (from data already fetched, nothing outside).
    integration = DOMAIN
    if (annotations_type := getattr(llm, "ToolAnnotations", None)) is not None:
        annotations = annotations_type(
            read_only=True, destructive=False, idempotent=True, open_world=False
        )

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> JsonObjectType:
        today = dt_util.now().date()
        children = _select(_children(hass), tool_input.tool_args.get("child"))
        return {
            "today": today.isoformat(),
            "today_weekday": _weekday(hass, today),
            **self._result(hass, tool_input.tool_args, children, today),
        }

    def _result(
        self, hass: HomeAssistant, args: dict[str, Any], children: list[_Child], today: date
    ) -> dict[str, Any]:
        raise NotImplementedError


CHILD = vol.Optional("child", description="First name of the child; leave out for all children")
# Required: small models leave optional arguments out, and "tomorrow" then
# silently became today.
START = vol.Required(
    "date",
    description="Day to look up: YYYY-MM-DD, or heute/morgen/übermorgen, or a weekday",
)
DAYS = vol.Optional("days", default=1, description=f"Number of days, 1 to {MAX_DAYS}")


class HomeworkTool(_SchoolhubTool):
    """Homework that is not past its due date."""

    name = "get_homework"
    description = (
        "Hausaufgaben (homework) of the children: use this, not a to-do list, for any "
        "question about homework. Returns homework not yet past its due date: subject, "
        "task and when it is due. Ticked-off homework is left out unless include_done is true."
    )
    parameters = vol.Schema(
        {
            CHILD: str,
            vol.Optional(
                "include_done", default=False, description="Also list ticked-off homework"
            ): bool,
        }
    )

    def _result(
        self, hass: HomeAssistant, args: dict[str, Any], children: list[_Child], today: date
    ) -> dict[str, Any]:
        include_done = args.get("include_done", False)
        result = []
        for child in children:
            if child.swop is None:
                continue
            coordinator, data = child.swop
            items = [
                self._item(hass, item, coordinator.state.is_done(item.uid), today)
                for item in sorted(data.homework, key=lambda h: h.due or h.assigned)
                if not is_overdue(item, today)
                and (include_done or not coordinator.state.is_done(item.uid))
            ]
            result.append({**_child_info(child), "homework": items})
        return {"children": result}

    @staticmethod
    def _item(hass: HomeAssistant, item: Homework, done: bool, today: date) -> dict[str, Any]:
        # Kept short on purpose: small models gave no answer at all for the
        # longer form with the assigned date and teacher of every item.
        return _clean(
            {
                "subject": item.subject,
                "task": _one_line(item.text),
                **(_day(hass, "due", item.due, today) if item.due else {"due": "no due date"}),
                "done": done,
            }
        )


class TimetableTool(_SchoolhubTool):
    """Lessons of one or more days."""

    name = "get_timetable"
    description = (
        "Stundenplan (timetable) of the children: each child's lessons for one or more "
        "days: time, subject, room and teacher, and whether a lesson is cancelled or "
        "taught by a substitute. A day without "
        "lessons is a day off or a weekend."
    )
    parameters = vol.Schema(
        {CHILD: str, START: str, DAYS: vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DAYS))}
    )

    def _result(
        self, hass: HomeAssistant, args: dict[str, Any], children: list[_Child], today: date
    ) -> dict[str, Any]:
        days = _requested_days(args, today)
        result = []
        for child in children:
            if child.swop is None:
                continue
            coordinator, data = child.swop
            lessons: dict[date, list[dict[str, Any]]] = {}
            for lesson in data.lessons:
                lessons.setdefault(lesson.day, []).append(self._lesson(lesson))
            covered = _covered([coordinator], SERVICE_SWOP)
            result.append(
                {
                    **_child_info(child),
                    "days": _per_day(hass, days, today, covered, "lessons", lessons),
                }
            )
        return {"children": result}

    @staticmethod
    def _lesson(lesson: Lesson) -> dict[str, Any]:
        return _clean(
            {
                "number": lesson.number,
                "start": lesson.start.strftime("%H:%M") if lesson.start else None,
                "end": lesson.end.strftime("%H:%M") if lesson.end else None,
                "subject": lesson.subject,
                "room": lesson.room,
                "teacher": lesson.teacher,
                "cancelled": lesson.cancelled,
                "substitute_teacher": lesson.substituted,
                "note": lesson.comment,
                "topic": lesson.topic,
                "homework": lesson.homework,
            }
        )


class LunchTool(_SchoolhubTool):
    """Ordered school lunch of one or more days."""

    name = "get_lunch"
    description = (
        "Mittagessen / Schulessen (school lunch) of the children: the lunch ordered for "
        "each child for one or more days. Each day says "
        "with lunch_ordered whether lunch is booked for the child that day. Answer for "
        "every child in the result, also those with no lunch ordered."
    )
    parameters = vol.Schema(
        {CHILD: str, START: str, DAYS: vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DAYS))}
    )

    def _result(
        self, hass: HomeAssistant, args: dict[str, Any], children: list[_Child], today: date
    ) -> dict[str, Any]:
        days = _requested_days(args, today)
        result = []
        for child in children:
            if not child.lunch:
                continue
            meals: dict[date, list[dict[str, Any]]] = {}
            for coordinator in child.lunch:
                for meal in coordinator.data.meals:
                    # Allergen codes are left out: their meaning differs between
                    # caterers, and a model guessing them could do harm.
                    meals.setdefault(meal.day, []).append({"menu": meal.menu})
            covered = _covered(child.lunch, SERVICE_DREI_KOECHE)
            result.append(
                {
                    **_child_info(child),
                    "days": [
                        self._ordered(child, day)
                        for day in _per_day(hass, days, today, covered, "meals", meals)
                    ],
                }
            )
        return {"children": result}

    @staticmethod
    def _ordered(child: _Child, day: dict[str, Any]) -> dict[str, Any]:
        """Say outright whether lunch is ordered, so no meal is not read as unknown."""
        if "meals" not in day:
            return day
        if day["meals"]:
            return {**day, "lunch_ordered": True}
        del day["meals"]
        return {**day, "lunch_ordered": False, "note": f"No lunch ordered for {child.name}"}


class NewsTool(_SchoolhubTool):
    """Latest posts of the school and of each child's class."""

    name = "get_school_news"
    description = (
        "Schulnachrichten / Klassennachrichten (school and class news): the latest posts "
        "with their full text: from the whole school, from "
        "each child's class, or both. Also tells how many school portal chats have "
        "unread messages."
    )
    parameters = vol.Schema(
        {
            CHILD: str,
            vol.Optional(
                "scope",
                default="all",
                description="school: school-wide news, class: class news, all: both",
            ): vol.In(["all", "school", "class"]),
            vol.Optional(
                "limit", default=3, description=f"Posts per feed, 1 to {MAX_POSTS}"
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_POSTS)),
        }
    )

    def _result(
        self, hass: HomeAssistant, args: dict[str, Any], children: list[_Child], today: date
    ) -> dict[str, Any]:
        scope = args.get("scope", "all")
        limit = args.get("limit", 3)
        result: dict[str, Any] = {}
        if scope in ("all", "school"):
            # Siblings at the same school share one login and one feed.
            schools = {
                child.swop[0].client.base_url: child.swop[0] for child in children if child.swop
            }
            result["schools"] = [
                _clean(
                    {
                        "school": school_label(url),
                        "unread_chats": coordinator.data.unread_chats,
                        "posts": [
                            self._post(hass, post, today)
                            for post in coordinator.data.school_news[:limit]
                        ],
                    }
                )
                for url, coordinator in schools.items()
            ]
        if scope in ("all", "class"):
            result["classes"] = [
                {
                    **_child_info(child),
                    "posts": [
                        self._post(hass, post, today) for post in child.swop[1].class_news[:limit]
                    ],
                }
                for child in children
                if child.swop
            ]
        return result

    @staticmethod
    def _post(hass: HomeAssistant, post: NewsPost, today: date) -> dict[str, Any]:
        published = dt_util.as_local(post.published) if post.published else None
        text = post.text
        if len(text) > MAX_POST_LENGTH:
            text = text[:MAX_POST_LENGTH].rsplit(" ", 1)[0] + " … (cut, see school portal)"
        return _clean(
            {
                "title": post.title,
                "author": post.author,
                "published": published.isoformat(timespec="minutes") if published else None,
                **(
                    {
                        "published_weekday": _weekday(hass, published.date()),
                        "published_days_ago": (today - published.date()).days,
                    }
                    if published
                    else {}
                ),
                "text": text,
            }
        )


def _with_children[T: llm.Tool](tool: T, names: list[str]) -> T:
    """Name the children in the description, so a model links "Torvi" to the tool."""
    if names:
        tool.description = f"{tool.description} Children: {', '.join(names)}."
    return tool


@dataclass(slots=True, kw_only=True)
class SchoolhubAPI(llm.API):
    """The SchoolHub tools for conversation agents."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        if llm_context.platform == MCP_SERVER_PLATFORM:
            # Meant for Home Assistant's own (voice) agents: the MCP server, which
            # can offer all LLM APIs, gets nothing, so the data stays in Home Assistant.
            return llm.APIInstance(api=self, api_prompt="", llm_context=llm_context, tools=[])
        today = dt_util.now().date()
        children = _children(self.hass)
        names = ", ".join(
            f"{child.name} (class {child.student.class_name})"
            if child.student.class_name
            else child.name
            for child in children
        )
        return llm.APIInstance(
            api=self,
            api_prompt=PROMPT.format(
                children=names or "none yet",
                weekday=_weekday(self.hass, today),
                today=today.isoformat(),
                tomorrow_weekday=_weekday(self.hass, today + timedelta(days=1)),
                tomorrow=(today + timedelta(days=1)).isoformat(),
            ),
            llm_context=llm_context,
            tools=[
                _with_children(tool, [child.name for child in children])
                for tool in (HomeworkTool(), TimetableTool(), LunchTool(), NewsTool())
            ],
        )


def async_register(hass: HomeAssistant) -> None:
    """Register the SchoolHub LLM API for as long as Home Assistant runs."""
    llm.async_register_api(hass, SchoolhubAPI(hass=hass, id=DOMAIN, name=API_NAME))
