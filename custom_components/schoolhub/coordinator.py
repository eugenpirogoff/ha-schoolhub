"""Coordinators that refresh SWOP and Drei Köche data."""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DreiKoecheClient, SchoolhubApiError, SchoolhubAuthError, SwopClient
from .const import (
    BASEDATA_MAX_AGE,
    CONF_WEEKS_FUTURE,
    CONF_WEEKS_PAST,
    DEFAULTS,
    DOMAIN,
    EVENT_NEW_POST,
    MAX_TEXT_LENGTH,
    NEWS_SCOPE_CLASS,
    NEWS_SCOPE_SCHOOL,
    SERVICE_DREI_KOECHE,
    SERVICE_SWOP,
    UPDATE_INTERVAL,
)
from .models import (
    DreiKoecheData,
    NewsPost,
    SwopData,
    SwopLookup,
    SwopStudent,
    SwopStudentData,
    count_unread_chats,
    find_news_module,
    parse_drei_koeche_student,
    parse_homework,
    parse_lessons,
    parse_meals,
    parse_news,
    parse_students,
)
from .store import LocalState

_LOGGER = logging.getLogger(__name__)

type SchoolhubConfigEntry = ConfigEntry[SwopCoordinator | DreiKoecheCoordinator]


def _expect[T](value: object, kind: type[T]) -> T:
    """Raise if a response does not have the expected JSON type."""
    if not isinstance(value, kind):
        raise SchoolhubApiError(f"Expected {kind.__name__}, got {type(value).__name__}")
    return value


def _auth_failed(service: str) -> ConfigEntryAuthFailed:
    return ConfigEntryAuthFailed(
        translation_domain=DOMAIN,
        translation_key="auth_failed",
        translation_placeholders={"service": service},
    )


def _update_failed(service: str, err: Exception) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"service": service, "error": str(err)},
    )


def _messenger_config(basedata: dict[str, Any]) -> tuple[str, str] | None:
    """Return the messenger subdomain and school host, if the messenger is on."""
    options = basedata.get("global_options") or {}
    hostname = (basedata.get("local_config") or {}).get("HOSTNAME")
    subdomain = options.get("messenger_subdomain")
    if options.get("swop_messenger") != "true" or not subdomain or not hostname:
        return None
    return subdomain, hostname


def fetch_range(entry: ConfigEntry, service: str) -> tuple[date, date]:
    """Return first and last day to fetch: whole weeks around the current one."""
    weeks_past = entry.options.get(CONF_WEEKS_PAST, DEFAULTS[service][CONF_WEEKS_PAST])
    weeks_future = entry.options.get(CONF_WEEKS_FUTURE, DEFAULTS[service][CONF_WEEKS_FUTURE])
    today = dt_util.now().date()
    monday = today - timedelta(days=today.weekday())
    return (
        monday - timedelta(weeks=weeks_past),
        monday + timedelta(weeks=weeks_future + 1, days=-1),
    )


class SwopCoordinator(DataUpdateCoordinator[SwopData]):
    """Fetch timetable, homework and news for all children of a SWOP login."""

    config_entry: SchoolhubConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SchoolhubConfigEntry,
        client: SwopClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} SWOP {client.school}",
            update_interval=UPDATE_INTERVAL[SERVICE_SWOP],
        )
        self.client = client
        self.state = LocalState(hass, entry.entry_id)
        self._lookup: SwopLookup | None = None
        self._lookup_fetched: datetime | None = None
        # Page id -> news module id (None: page has no news).
        self._news_modules: dict[int, int | None] = {}
        self._school_page: int | None = None
        # (messenger subdomain, school host name) if the school uses the messenger.
        self._messenger: tuple[str, str] | None = None
        # Pages whose news could not be fetched in the current refresh.
        self._failed_pages: set[int] = set()
        # Student key -> weekday picked for the homework view ("monday".."friday").
        # Not stored: the view starts on today again after a restart.
        self.homework_days: dict[str, str] = {}

    async def _async_setup(self) -> None:
        """Load ticked-off homework and seen news before the first refresh."""
        await self.state.async_load()

    @property
    def messenger_enabled(self) -> bool:
        """Return whether the school uses the SWOP messenger (known from basedata)."""
        return self._messenger is not None

    async def _async_update_data(self) -> SwopData:
        try:
            lookup = await self._async_lookup()
            mydata = _expect(await self.client.async_get_mydata(), dict)
            students = parse_students(mydata)
            start, end = fetch_range(self.config_entry, SERVICE_SWOP)
            data = SwopData(students={})
            self._failed_pages = set()
            for student in students:
                timetable = _expect(
                    await self.client.async_get_timetable(student.klassenzug_id, start, end),
                    list,
                )
                records = _expect(
                    await self.client.async_get_lesson_records(student.klassenzug_id, start, end),
                    list,
                )
                data.students[student.key] = SwopStudentData(
                    student=student,
                    lessons=parse_lessons(timetable, records, lookup),
                    homework=parse_homework(records, lookup),
                    class_news=await self._async_class_news(student),
                )
            data.school_news = await self._async_school_news()
            data.unread_chats = await self._async_unread_chats(mydata.get("member_access_token"))
        except SchoolhubAuthError as err:
            raise _auth_failed("SWOP") from err
        except SchoolhubApiError as err:
            raise _update_failed("SWOP", err) from err

        # Only prunes when every child seen so far is present (see LocalState).
        self.state.prune_done(
            set(data.students),
            {item.uid for child in data.students.values() for item in child.homework},
        )
        self._fire_new_posts(data)
        return data

    async def _async_lookup(self) -> SwopLookup:
        now = dt_util.utcnow()
        if (
            self._lookup is None
            or self._lookup_fetched is None
            or now - self._lookup_fetched > BASEDATA_MAX_AGE
        ):
            basedata = _expect(await self.client.async_get_basedata(), dict)
            self._lookup = SwopLookup(basedata)
            self._messenger = _messenger_config(basedata)
            self._lookup_fetched = now
        return self._lookup

    async def _async_unread_chats(self, member_access_token: object) -> int | None:
        """Return how many messenger chats have unread messages.

        None if the school has no messenger. Like news, the messenger is
        optional: errors keep the previous count instead of failing the refresh.
        """
        if self._messenger is None or not isinstance(member_access_token, str):
            return None
        subdomain, hostname = self._messenger
        try:
            chats = _expect(await self.client.async_get_chats(), dict)
            settings = _expect(await self.client.async_get_chat_settings(), dict)
            latest = _expect(
                await self.client.async_get_latest_message_ids(
                    subdomain, member_access_token, hostname
                ),
                dict,
            )
        except SchoolhubAuthError:
            raise
        except SchoolhubApiError as err:
            _LOGGER.debug("Could not check the SWOP messenger: %s", err)
            return self.data.unread_chats if self.data else None
        return count_unread_chats(
            chats.get("chats") or [],
            settings.get("chat_einstellungen") or [],
            latest.get("latest_message_ids") or [],
        )

    async def _async_news(self, page_id: int) -> list[NewsPost]:
        """Return the newest posts of the news module on a page.

        News is optional: errors keep the previous posts instead of failing
        the whole refresh.
        """
        try:
            if page_id not in self._news_modules:
                page = await self.client.async_get_page(page_id)
                self._news_modules[page_id] = find_news_module(_expect(page, dict))
            module_id = self._news_modules[page_id]
            if module_id is None:
                return []
            return parse_news(
                _expect(await self.client.async_get_news_page(page_id, module_id), dict)
            )
        except SchoolhubAuthError:
            raise
        except SchoolhubApiError as err:
            _LOGGER.debug("Could not fetch news of page %s: %s", page_id, err)
            self._failed_pages.add(page_id)
            return self._previous_news(page_id)

    async def _async_class_news(self, student: SwopStudent) -> list[NewsPost]:
        if student.class_page_id is None:
            return []
        return await self._async_news(student.class_page_id)

    async def _async_school_news(self) -> list[NewsPost]:
        if self._school_page is None:
            try:
                navigation = _expect(await self.client.async_get_navigation(), dict)
            except SchoolhubAuthError:
                raise
            except SchoolhubApiError as err:
                _LOGGER.debug("Could not fetch SWOP navigation: %s", err)
                return self.data.school_news if self.data else []
            self._school_page = (navigation.get("navigation") or {}).get("interne_startseite_id")
            if self._school_page is None:
                return []
        return await self._async_news(self._school_page)

    def _previous_news(self, page_id: int) -> list[NewsPost]:
        if not self.data:
            return []
        if page_id == self._school_page:
            return self.data.school_news
        for child in self.data.students.values():
            if child.student.class_page_id == page_id:
                return child.class_news
        return []

    def _fire_new_posts(self, data: SwopData) -> None:
        # Siblings in the same class share one feed and get one event.
        feeds: dict[int, tuple[list[SwopStudent], list[NewsPost]]] = {}
        for child in data.students.values():
            page = child.student.class_page_id
            if page is not None:
                feeds.setdefault(page, ([], child.class_news))[0].append(child.student)
        if self._school_page is not None:
            feeds[self._school_page] = ([], data.school_news)

        for page, (students, posts) in feeds.items():
            if page in self._failed_pages:
                continue
            scope = NEWS_SCOPE_CLASS if students else NEWS_SCOPE_SCHOOL
            for post in reversed(self.state.new_posts(f"{scope}:{page}", posts)):
                self.hass.bus.async_fire(
                    EVENT_NEW_POST,
                    {
                        "config_entry_id": self.config_entry.entry_id,
                        "scope": scope,
                        "students": [student.display_name for student in students],
                        "class": students[0].class_name if students else None,
                        "post_id": post.post_id,
                        "title": post.title,
                        "text": post.text[:MAX_TEXT_LENGTH],
                        "author": post.author,
                        "published": post.published.isoformat() if post.published else None,
                    },
                )


class DreiKoecheCoordinator(DataUpdateCoordinator[DreiKoecheData]):
    """Fetch the ordered meals of one Drei Köche login."""

    config_entry: SchoolhubConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SchoolhubConfigEntry, client: DreiKoecheClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} Drei Köche {entry.title}",
            update_interval=UPDATE_INTERVAL[SERVICE_DREI_KOECHE],
        )
        self.client = client

    async def _async_update_data(self) -> DreiKoecheData:
        start, end = fetch_range(self.config_entry, SERVICE_DREI_KOECHE)
        try:
            orders = await self.client.async_get_orders(start, end)
        except SchoolhubAuthError as err:
            raise _auth_failed("Drei Köche") from err
        except SchoolhubApiError as err:
            raise _update_failed("Drei Köche", err) from err
        return DreiKoecheData(
            student=parse_drei_koeche_student(self.client.user),
            meals=parse_meals(orders),
        )
