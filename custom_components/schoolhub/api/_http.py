"""HTTP plumbing shared by the API clients."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

import aiohttp

from .errors import SchoolhubApiError, SchoolhubConnectionError

TIMEOUT = aiohttp.ClientTimeout(total=30)
HEADERS = {"Accept": "application/json", "User-Agent": "SchoolHub (Home Assistant)"}


@dataclass(frozen=True, slots=True)
class Response:
    """A fully read HTTP response."""

    status: int
    content_type: str
    text: str
    service: str

    @property
    def is_json(self) -> bool:
        """Return whether the server declared a JSON body."""
        return "json" in self.content_type

    def json(self) -> Any:
        """Return the decoded JSON body."""
        try:
            return json.loads(self.text)
        except ValueError as err:
            raise SchoolhubApiError(f"{self.service} returned invalid JSON") from err


def endpoint_name(path: str) -> str:
    """Return the first path segment without ids or extension, safe for error texts."""
    return path.lstrip("/").split("/")[0].removesuffix(".json")


async def fetch(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    service: str,
    headers: dict[str, str] | None = None,
    **kwargs: Any,
) -> Response:
    """Send a request and read the whole response, mapping network errors."""
    try:
        async with session.request(
            method, url, headers=HEADERS | (headers or {}), timeout=TIMEOUT, **kwargs
        ) as response:
            return Response(
                status=response.status,
                content_type=response.content_type or "",
                text=await response.text(errors="replace"),
                service=service,
            )
    except (TimeoutError, aiohttp.ClientError) as err:
        raise SchoolhubConnectionError(f"Cannot reach {service}") from err
