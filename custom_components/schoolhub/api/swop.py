"""Async client for SWOP school portals (usually https://<school>.swop.schule)."""

from __future__ import annotations

from datetime import date
import logging
import re
import time
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from ._http import endpoint_name, fetch
from .errors import SchoolhubApiError, SchoolhubAuthError

_LOGGER = logging.getLogger(__name__)

SERVICE = "SWOP"
SWOP_DOMAIN = "swop.schule"

# A DNS host name, optionally with a port.
_LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(?:\.{_LABEL})*(?::\d{{1,5}})?")
_SCHEME = re.compile(r"^(https?)://")
_LABEL_ONLY = re.compile(_LABEL)
_PATH_START = re.compile(r"[/?#]")


class _SessionExpired(Exception):
    """Internal signal to log in again."""


class _NotJson(_SessionExpired):
    """Internal signal: an HTML page instead of JSON (login page or maintenance)."""


def normalize_url(value: str) -> str:
    """Return the portal address ("https://host") from user input.

    Accepts a full URL (any path is dropped), a host name, or just the SWOP
    subdomain ("my-school" becomes https://my-school.swop.schule).
    Raises ValueError if the input is not a usable address.
    """
    value = value.strip().lower()
    scheme = "https"
    if match := _SCHEME.match(value):
        scheme = match[1]
        value = value[match.end() :]
    host = _PATH_START.split(value, maxsplit=1)[0].rstrip(".")
    if not _HOST.fullmatch(host):
        raise ValueError(f"Not a valid address: {value!r}")
    if "." not in host.split(":")[0]:
        host = f"{host}.{SWOP_DOMAIN}"
    return f"{scheme}://{host}"


def school_name(url: str) -> str:
    """Return a readable school name from its portal address."""
    host = urlsplit(url).hostname or url
    return host.split(".")[0].replace("-", " ").title()


class SwopClient:
    """Session-based client for one SWOP login.

    The session must have its own cookie jar, because SWOP keeps the login in
    a cookie.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        username: str,
        password: str,
    ) -> None:
        self._session = session
        self._base_url = normalize_url(url)
        self._username = username
        self._password = password
        self._logged_in = False

    @property
    def base_url(self) -> str:
        """Return the portal address."""
        return self._base_url

    @property
    def school(self) -> str:
        """Return the portal host, which identifies the school."""
        return self._base_url.split("://", 1)[1]

    async def async_login(self) -> None:
        """Log in and store the session cookie."""
        try:
            data = await self._request(
                "POST",
                "/session/login/post.json",
                json={"loginname": self._username, "password": self._password},
            )
        except _NotJson as err:
            # A maintenance page or captive portal says nothing about the password.
            raise SchoolhubApiError("SWOP answered the login with a web page") from err
        except _SessionExpired as err:
            raise SchoolhubAuthError("SWOP rejected the login") from err
        if not isinstance(data, dict) or not data.get("success"):
            raise SchoolhubAuthError("SWOP rejected the login")
        self._logged_in = True

    async def async_get_mydata(self) -> dict[str, Any]:
        """Return the logged-in user, including the children they can see."""
        return await self._get("/swop_mydata.json")

    async def async_get_basedata(self) -> dict[str, Any]:
        """Return lookup tables for teachers, subjects, rooms and lesson times."""
        return await self._get("/swop_basedata.json")

    async def async_get_timetable(
        self, klassenzug_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Return the timetable entries of a class between two dates."""
        return await self._get(
            f"/swop_zeitabschnitte/{start.isoformat()}/{end.isoformat()}.json",
            {"klassenzug_id": klassenzug_id},
        )

    async def async_get_lesson_records(
        self, klassenzug_id: int, start: date, end: date
    ) -> list[dict[str, Any]]:
        """Return lesson records (topics and homework) of a class."""
        return await self._get(
            f"/swop_udoks/{start.isoformat()}/{end.isoformat()}.json",
            {"klassenzug_id": klassenzug_id},
        )

    async def async_get_navigation(self) -> dict[str, Any]:
        """Return the portal navigation."""
        return await self._get("/swop_info_navigation.json", _no_cache())

    async def async_get_page(self, menue_id: int) -> dict[str, Any]:
        """Return the modules shown on a portal page."""
        return await self._get(f"/swop_info/{menue_id}.json")

    async def async_get_news_page(
        self, menue_id: int, module_id: int, page: int = 0
    ) -> dict[str, Any]:
        """Return one page (10 posts, newest first) of a news module."""
        return await self._get(
            f"/swop_info_news_posts_page/{menue_id}/{module_id}/{page}.json",
            _no_cache(),
        )

    async def async_get_chats(self) -> dict[str, Any]:
        """Return the messenger chats of the logged-in user."""
        return await self._get("/swop_messenger_my_chats.json", _no_cache())

    async def async_get_chat_settings(self) -> dict[str, Any]:
        """Return per-chat settings, e.g. which chats the user ignores."""
        return await self._get("/swop_meine_chat_einstellungen.json")

    async def async_get_latest_message_ids(
        self, messenger_subdomain: str, member_access_token: str, hostname: str
    ) -> dict[str, Any]:
        """Return, per chat, the newest message id and how far the user has read.

        The messenger runs on its own host (e.g. messenger.swop.schule). It only
        reports ids; opening a chat, which marks it read, is never done here.
        """
        if not _LABEL_ONLY.fullmatch(messenger_subdomain):
            raise SchoolhubApiError("Invalid messenger host")
        response = await fetch(
            self._session,
            "POST",
            f"https://{messenger_subdomain}.{SWOP_DOMAIN}/api/client-get-latest-message-ids",
            service=SERVICE,
            json={"member_access_token": member_access_token, "SWOP_HOSTNAME": hostname},
            # aiohttp would resend the token on a 307/308 to any host.
            allow_redirects=False,
        )
        if 300 <= response.status < 400:
            raise SchoolhubApiError(f"SWOP messenger redirected (HTTP {response.status})")
        if response.status >= 400:
            # The messenger token is separate from the login; a refusal here
            # must not ask the user to log in again.
            raise SchoolhubApiError(f"SWOP messenger returned HTTP {response.status}")
        return response.json()

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a JSON resource, logging in first and again if the session expired."""
        if self._logged_in:
            try:
                return await self._request("GET", path, params=params)
            except _SessionExpired:
                _LOGGER.debug("SWOP session expired, logging in again")
                self._logged_in = False
        await self.async_login()
        try:
            return await self._request("GET", path, params=params)
        except _SessionExpired as err:
            # The login was just accepted, so this is not a password problem.
            raise SchoolhubApiError("SWOP refused a request right after logging in") from err

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # Redirects are never followed: aiohttp resends the body (the password
        # on login) on a 307/308, even to another host or to plain http.
        response = await fetch(
            self._session,
            method,
            f"{self._base_url}{path}",
            service=SERVICE,
            allow_redirects=False,
            **kwargs,
        )
        if 300 <= response.status < 400:
            if method == "GET":
                # An expired session may be redirected to the login page.
                raise _SessionExpired
            raise SchoolhubApiError(
                f"SWOP redirected {endpoint_name(path)} (HTTP {response.status})"
            )
        # An expired session is answered with 401 or with the HTML login page.
        if response.status == 401:
            raise _SessionExpired
        if response.status >= 400:
            raise SchoolhubApiError(
                f"SWOP returned HTTP {response.status} from {endpoint_name(path)}"
            )
        if not response.is_json:
            raise _NotJson
        data = response.json()
        if method == "GET" and isinstance(data, dict) and data.get("success") is False:
            # Some endpoints answer an expired session with {"success": false}.
            raise _SessionExpired
        return data


def _no_cache() -> dict[str, int]:
    """Return a cache-busting parameter, as the SWOP web app sends it."""
    return {"nc": time.time_ns() // 1_000_000}
