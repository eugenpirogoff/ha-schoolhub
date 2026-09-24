"""Data models and parsers for SWOP and Drei Köche responses.

Everything here is pure (no Home Assistant imports), so it can be tested
directly against recorded API responses.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time
from difflib import SequenceMatcher
from enum import StrEnum
import heapq
import html
import re
from typing import Any
import unicodedata

# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Student:
    """A child, identified across services by first and last name."""

    first_name: str
    last_name: str
    class_name: str | None = None

    @property
    def key(self) -> str:
        """Return a stable key used to match the same child across services."""
        return name_key(self.first_name, self.last_name)

    @property
    def display_name(self) -> str:
        """Return the name shown in Home Assistant."""
        return self.first_name


@dataclass(frozen=True, slots=True, kw_only=True)
class SwopStudent(Student):
    """A child as seen by SWOP."""

    klassenzug_id: int
    class_page_id: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Lesson:
    """One lesson in the timetable."""

    uid: str
    day: date
    number: int | None
    start: time | None
    end: time | None
    subject: str
    teacher: str | None
    room: str | None
    cancelled: bool = False
    substituted: bool = False
    comment: str | None = None
    topic: str | None = None
    homework: str | None = None


@dataclass(frozen=True, slots=True)
class Homework:
    """Homework given in a lesson."""

    uid: str
    assigned: date
    due: date | None
    subject: str
    teacher: str | None
    text: str


@dataclass(frozen=True, slots=True)
class NewsPost:
    """A post from a class or school news feed."""

    post_id: int
    title: str
    text: str
    author: str | None
    published: datetime | None


@dataclass(frozen=True, slots=True, kw_only=True)
class Meal:
    """An ordered lunch."""

    day: date
    menu: str
    allergens: str | None = None
    # Distinguishes several orders on the same day.
    order_id: str


@dataclass(slots=True)
class SwopStudentData:
    """Everything SWOP knows about one child."""

    student: SwopStudent
    lessons: list[Lesson] = field(default_factory=list)
    homework: list[Homework] = field(default_factory=list)
    class_news: list[NewsPost] = field(default_factory=list)


@dataclass(slots=True)
class SwopData:
    """Result of one SWOP refresh."""

    students: dict[str, SwopStudentData]
    school_news: list[NewsPost] = field(default_factory=list)
    # Messenger chats with unread messages; None if the messenger is unavailable.
    unread_chats: int | None = None


@dataclass(slots=True)
class DreiKoecheData:
    """Result of one Drei Köche refresh."""

    student: Student
    meals: list[Meal] = field(default_factory=list)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

_NAME_SEPARATORS = re.compile(r"[-_.,]")
_BLOCK_END = re.compile(r"<br\s*/?>|</p>|</li>|</div>", re.IGNORECASE)
# A tag starts with a letter, "/" or "!", so a lone "<" as in "a < b" is text.
_TAG = re.compile(r"<[a-zA-Z/!][^>]*(?:>|$)")
_CLASS_GROUP = re.compile(r"Schueler Klasse (.+)")
_SUP = re.compile(r"<sup>(.*?)</sup>", re.S)
_PORTIONS = re.compile(r"^\d+x\s*")
_GLUED_BRACKET = re.compile(r"(\S)\[")


def _text(value: object) -> str | None:
    """Return a stripped string, or None if the value is blank or not a string."""
    return (value.strip() or None) if isinstance(value, str) else None


def fold_name(text: str) -> str:
    """Normalize a name for comparison: lower case, no accents, single spaces.

    "Zoë-Sophie  MÜLLER" becomes "zoe sophie muller"; "ß" becomes "ss".
    """
    text = unicodedata.normalize("NFKD", text.casefold().replace("ß", "ss"))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(_NAME_SEPARATORS.sub(" ", text).split())


def name_key(first_name: str, last_name: str) -> str:
    """Return a normalized "first last" key, e.g. "zoe marie muster"."""
    return fold_name(f"{first_name} {last_name}")


class MatchKind(StrEnum):
    """How a Drei Köche child was matched to a SWOP child."""

    EXACT = "exact"
    FIRST_NAME = "first_name"
    SIMILAR = "similar"


# Minimum similarity (0-1) of both first and last name for a typo match, and
# the lead it needs over the next best candidate so siblings aren't confused.
SIMILAR_MIN_RATIO = 0.85
SIMILAR_MIN_LEAD = 0.1


@dataclass(frozen=True, slots=True)
class StudentMatch:
    """A child of one service matched to a child of another."""

    student: Student
    how: MatchKind
    similarity: float = 1.0


def match_student(student: Student, candidates: Iterable[Student]) -> StudentMatch | None:
    """Find the same child among candidates, tolerating name differences.

    Tried in order:
    1. the same names, ignoring case, accents and spacing;
    2. the same last name, and one side gives only the leading first names
       ("Jonas" and "Jonas Paul", but not "Maria" and "Anna Maria");
    3. a clearly best, very similar spelling of first and last name, for
       typos such as "Jonaz" and "Jonas". First and last names are compared
       separately, so a shared last name alone never makes siblings similar,
       and each first name must keep its length: a typo swaps a letter, while
       an added or dropped one ("Paul" and "Paula") is a different name.
    """
    unique: dict[str, Student] = {}
    for candidate in candidates:
        # The same child can be listed by several SWOP logins.
        unique.setdefault(candidate.key, candidate)
    if student.key in unique:
        return StudentMatch(unique[student.key], MatchKind.EXACT)

    first, last = fold_name(student.first_name), fold_name(student.last_name)
    folded = [(c, fold_name(c.first_name), fold_name(c.last_name)) for c in unique.values()]

    partial = [
        candidate
        for candidate, other_first, other_last in folded
        if other_last == last and _leading_names_match(first.split(), other_first.split())
    ]
    if len(partial) == 1:
        return StudentMatch(partial[0], MatchKind.FIRST_NAME)

    def similarity(item: tuple[Student, str, str]) -> float:
        _, other_first, other_last = item
        return min(
            SequenceMatcher(None, first, other_first).ratio(),
            SequenceMatcher(None, last, other_last).ratio(),
        )

    best = heapq.nlargest(2, ((similarity(item), item[0]) for item in folded), key=lambda p: p[0])
    if (
        best
        and best[0][0] >= SIMILAR_MIN_RATIO
        and _same_name_lengths(first, fold_name(best[0][1].first_name))
        and (len(best) == 1 or best[0][0] - best[1][0] >= SIMILAR_MIN_LEAD)
    ):
        return StudentMatch(best[0][1], MatchKind.SIMILAR, round(best[0][0], 2))
    return None


def _leading_names_match(first: list[str], other: list[str]) -> bool:
    """Return whether the shorter list of first names starts the longer one."""
    shorter, longer = sorted((first, other), key=len)
    return bool(shorter) and longer[: len(shorter)] == shorter


def _same_name_lengths(first: str, other: str) -> bool:
    """Return whether both first names have words of the same lengths."""
    return [len(name) for name in first.split()] == [len(name) for name in other.split()]


def html_to_text(value: str | None) -> str:
    """Convert a small HTML fragment to plain text.

    Entities are decoded before tags are stripped, so an encoded tag such as
    "&lt;img&gt;" is removed too and never reaches a dashboard as markup.
    """
    if not value:
        return ""
    text = html.unescape(value).replace("\xa0", " ")
    text = _TAG.sub("", _BLOCK_END.sub("\n", text))
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _time_of_day(value: str | None) -> time | None:
    """Parse SWOP lesson times such as "2000-01-01T08:00:00Z".

    The "Z" is misleading: the value is the local wall-clock time.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).time()
    except ValueError:
        return None


def _joined(records: list[dict[str, Any]], key: str) -> str | None:
    """Join a text field of several records, skipping blanks and duplicates."""
    texts = dict.fromkeys(text for record in records if (text := _text(record.get(key))))
    return "\n".join(texts) or None


def _parse_date(value: str | None) -> date | None:
    """Parse a date such as "2026-09-21"; a time part is ignored."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_datetime(value: str | None) -> datetime | None:
    """Parse an ISO timestamp such as "2026-09-22T06:20:33Z"."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# SWOP
# --------------------------------------------------------------------------


class SwopLookup:
    """Resolve ids from the SWOP base data to names."""

    def __init__(self, basedata: dict[str, Any]) -> None:
        self._teachers: dict[str, str] = basedata.get("lehrernamen_by_id") or {}
        self._subjects: dict[str, Any] = basedata.get("schulfaecher_by_id") or {}
        self._rooms: dict[str, Any] = basedata.get("raeume_by_id") or {}
        self._periods: dict[str, Any] = basedata.get("tagesabschnitte_by_id") or {}

    def teacher(self, teacher_id: int | str | None) -> str | None:
        """Return a teacher's short name, e.g. "Muster, A."."""
        if teacher_id is None:
            return None
        return self._teachers.get(str(teacher_id))

    def subject(self, subject_id: int | str | None) -> str:
        """Return a subject's name, or its id if unknown."""
        if subject_id is None:
            return "?"
        subject = self._subjects.get(str(subject_id))
        if isinstance(subject, dict):
            return subject.get("bezeichnung") or subject.get("name") or str(subject_id)
        return str(subject) if subject else str(subject_id)

    def room(self, room_id: int | str | None) -> str | None:
        """Return a room's name."""
        if room_id is None:
            return None
        room = self._rooms.get(str(room_id))
        if isinstance(room, dict):
            room = room.get("bezeichnung") or room.get("name")
        return _text(room)

    def period(self, period_id: int | str | None) -> tuple[int | None, time | None, time | None]:
        """Return lesson number, start and end of a period of the day."""
        period = (self._periods.get(str(period_id)) or {}).get("tagesabschnitt") or {}
        return (
            period.get("stunde_nr"),
            _time_of_day(period.get("beginn")),
            _time_of_day(period.get("ende")),
        )


def parse_students(mydata: dict[str, Any]) -> list[SwopStudent]:
    """Return the children visible to a SWOP login.

    A parent or family login lists them in "erziehungsberechtigt_fuer"; a
    student login is the child itself.
    """
    entries = mydata.get("erziehungsberechtigt_fuer") or []
    if not entries and mydata.get("is_schueler"):
        entries = [mydata]
    students = []
    for entry in entries:
        klassenzug_id = entry.get("klassenzug_id")
        if not klassenzug_id:
            continue
        students.append(
            SwopStudent(
                first_name=_text(entry.get("vorname")) or "",
                last_name=_text(entry.get("nachname")) or "",
                class_name=_class_name(entry.get("ecgroups") or []),
                klassenzug_id=klassenzug_id,
                class_page_id=entry.get("klassenseite_menue_id"),
            )
        )
    return students


def _class_name(groups: list[str]) -> str | None:
    """Return the class from SWOP groups such as "Schueler Klasse 4b"."""
    return next((m[1] for group in groups if (m := _CLASS_GROUP.fullmatch(group))), None)


def _records_by_lesson(
    records: list[dict[str, Any]],
) -> dict[tuple[date, int], list[dict[str, Any]]]:
    """Index lesson records by day and every lesson number they cover."""
    index: defaultdict[tuple[date, int], list[dict[str, Any]]] = defaultdict(list)
    for record in _live_records(records):
        day = _parse_date(record.get("datum"))
        first = record.get("unterrichtsstunde")
        if day is None or first is None:
            continue
        last = record.get("unterrichtsstunde_bis")
        last = first if last is None or last < first else last
        for number in range(first, last + 1):
            index[day, number].append(record)
    return index


def _live_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Unwrap lesson records, dropping deleted ones and duplicates."""
    seen: set[Any] = set()
    result = []
    for entry in records:
        record = entry.get("unterrichtsdokumentation", entry)
        if record.get("geloescht"):
            continue
        record_id = record.get("id")
        if record_id is not None:
            if record_id in seen:
                continue
            seen.add(record_id)
        result.append(record)
    return result


# Timetable flags that mark a substitution.
_SUBSTITUTION_FLAGS = (
    "is_first_teacher_substituted",
    "is_second_teacher_substituted",
    "is_third_teacher_substituted",
    "is_subject_substituted",
    "is_room_substituted",
)


def parse_lessons(
    timetable: list[dict[str, Any]],
    records: list[dict[str, Any]],
    lookup: SwopLookup,
) -> list[Lesson]:
    """Combine timetable entries with lesson records (topic, homework)."""
    by_lesson = _records_by_lesson(records)
    lessons = []
    for entry in timetable:
        day = _parse_date(entry.get("erster_tag"))
        if day is None or entry.get("id") is None:
            continue
        number, start, end = lookup.period(entry.get("tagesabschnitt_id"))
        lesson_records = by_lesson.get((day, number), []) if number is not None else []
        lessons.append(
            Lesson(
                uid=f"swop-lesson-{entry.get('id')}",
                day=day,
                number=number,
                start=start,
                end=end,
                subject=entry.get("bezeichnung") or lookup.subject(entry.get("schulfach_id")),
                teacher=lookup.teacher(entry.get("ecuser_id")),
                room=lookup.room(entry.get("raum_id")),
                cancelled=bool(entry.get("abes_ausfall")),
                substituted=any(entry.get(flag) for flag in _SUBSTITUTION_FLAGS),
                comment=_text(entry.get("kommentar_oeffentlich")),
                topic=_joined(lesson_records, "bemerkung"),
                homework=_joined(lesson_records, "hausaufgaben"),
            )
        )
    lessons.sort(key=lambda lesson: (lesson.day, lesson.number is None, lesson.number or 0))
    return lessons


def parse_homework(records: list[dict[str, Any]], lookup: SwopLookup) -> list[Homework]:
    """Return homework from lesson records, sorted by due date."""
    homework = []
    for record in _live_records(records):
        text = _text(record.get("hausaufgaben"))
        assigned = _parse_date(record.get("datum"))
        if not text or assigned is None or record.get("id") is None:
            continue
        homework.append(
            Homework(
                uid=f"swop-homework-{record.get('id')}",
                assigned=assigned,
                due=_parse_date(record.get("hausaufgaben_zu_erledigen_bis")),
                subject=lookup.subject(record.get("schulfach_id")),
                teacher=lookup.teacher(record.get("ecuser_id")),
                text=text,
            )
        )
    homework.sort(key=lambda item: (item.due or item.assigned, item.subject))
    return homework


# Emoji per subject, matched by name prefix (lower case); the first match wins.
# Covers common German primary and secondary school subjects and abbreviations.
SUBJECT_ICONS: tuple[tuple[str, str], ...] = (
    ("deu", "📖"),
    ("eng", "🇬🇧"),
    ("ma", "🔢"),
    ("sach", "🌱"),
    ("su ", "🌱"),
    ("sport", "⚽"),
    ("schw", "🏊"),
    ("kunst", "🎨"),
    ("ku ", "🎨"),
    ("musik", "🎵"),
    ("musisch", "🎭"),
    ("lese", "📚"),
    ("klassentalk", "💬"),
    ("teamstart", "🤝"),
    ("bewegt", "🏃"),
    ("faustlos", "🕊️"),
    ("fö", "🧩"),
    ("gesellschaft", "🌍"),
    ("gewi", "🌍"),
    ("natur", "🔬"),
    ("lebensgestaltung", "✨"),
    ("religion", "✨"),
    ("ethik", "✨"),
    ("wirtschaft", "🛠️"),
    ("projekt", "🚀"),
    ("ausflug", "🚌"),
    ("anfangs", "✏️"),
)
DEFAULT_SUBJECT_ICON = "📝"


def subject_icon(subject: str) -> str:
    """Return an emoji for a subject, e.g. "📖" for "Deutsch"."""
    name = subject.casefold()
    return next(
        (icon for prefix, icon in SUBJECT_ICONS if name.startswith(prefix)),
        DEFAULT_SUBJECT_ICON,
    )


def is_overdue(homework: Homework, today: date) -> bool:
    """Return whether homework was due before today."""
    return (homework.due or homework.assigned) < today


def count_unread_chats(
    chats: list[dict[str, Any]], settings: list[dict[str, Any]], latest: list[dict[str, Any]]
) -> int:
    """Count open, not ignored chats with messages the user hasn't read.

    Mirrors the SWOP web app: a chat is unread when its newest message id is
    above the id read up to. Only ids are compared, never message contents.
    """
    ignored = {s.get("chat_id") for s in settings if s.get("ignore_chat")}
    open_chats = {
        chat.get("chat_join_as_member_token")
        for chat in chats
        if not chat.get("closed_datetime") and chat.get("id") not in ignored
    }
    return sum(
        1
        for item in latest
        if item.get("chat_join_as_member_token") in open_chats
        and (newest := item.get("latest_message_id"))
        and (item.get("read_messages_up_to_id") or 0) < newest
    )


def find_news_module(page: dict[str, Any]) -> int | None:
    """Return the id of the news module on a portal page, if it has one."""
    menus = (
        module.get("ecmodule_menue") or {}
        for modules in (page.get("modulelist") or {}).values()
        if isinstance(modules, list)
        for module in modules
    )
    return next(
        (menu.get("ecmodule_menue_id") for menu in menus if menu.get("modultyp") == "EcmodulNews"),
        None,
    )


def parse_news(page: dict[str, Any]) -> list[NewsPost]:
    """Return the posts of a news page, newest first."""
    posts = []
    for post in page.get("news_posts") or []:
        post_id = post.get("news_post_id") or post.get("id")
        if post_id is None:
            continue
        posts.append(
            NewsPost(
                post_id=post_id,
                title=_text(post.get("header")) or "",
                text=html_to_text(post.get("content") or post.get("abstract")),
                author=post.get("full_name"),
                published=_parse_datetime(post.get("created_at")),
            )
        )
    return posts


# --------------------------------------------------------------------------
# Drei Köche
# --------------------------------------------------------------------------


def parse_drei_koeche_student(user: dict[str, Any]) -> Student:
    """Return the child a Drei Köche login belongs to."""
    return Student(
        first_name=_text(user.get("firstname")) or "",
        last_name=_text(user.get("lastname")) or "",
        class_name=user.get("groupName"),
    )


def parse_menu(value: str) -> tuple[str, str | None]:
    """Split a menu such as "<b>1x</b> Pasta <sup>a,g</sup>" into name and allergens."""
    codes = dict.fromkeys(
        code
        for part in _SUP.findall(value)
        for raw in html_to_text(part).split(",")
        if (code := raw.strip())
    )
    name = html_to_text(_SUP.sub(" ", value)).replace("\n", " ")
    name = _GLUED_BRACKET.sub(r"\1 [", _PORTIONS.sub("", name))
    return " ".join(name.split()), ",".join(codes) or None


def parse_meals(orders: dict[str, Any]) -> list[Meal]:
    """Return ordered meals sorted by day."""
    meals = []
    for order in orders.get("data") or []:
        try:
            day = datetime.strptime(order["deliverDate"], "%d.%m.%y").date()
        except KeyError, TypeError, ValueError:
            continue
        menu, allergens = parse_menu(order.get("menuName") or "")
        order_id = order.get("idx") or order.get("nr") or len(meals)
        meals.append(
            Meal(day=day, menu=menu, allergens=allergens, order_id=f"{day.isoformat()}-{order_id}")
        )
    meals.sort(key=lambda meal: meal.day)
    return meals
