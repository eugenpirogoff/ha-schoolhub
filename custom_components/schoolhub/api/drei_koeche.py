"""Async client for the Drei Köche school catering service."""

from __future__ import annotations

import base64
from datetime import date
import json
import time
from typing import Any

import aiohttp

from ._http import endpoint_name, fetch
from .errors import SchoolhubApiError, SchoolhubAuthError

SERVICE = "Drei Köche"
API_BASE = "https://rest.drei-koeche.de/frontend/public/index.php/api/v2"

# Public credentials of the Drei Köche web frontend. Every browser sends these
# to get a client token before the user logs in.
FRONTEND_CLIENT = {"usrClient": "3K-FRONTEND", "pwdClient": "chicken"}

# Renew the user token this many seconds before it expires.
TOKEN_MARGIN = 60


class _TokenRejectedError(SchoolhubApiError):
    """A request was answered with 401/403; not proof of wrong credentials."""


class DreiKoecheClient:
    """Client for one Drei Köche login (one child)."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._token: str | None = None
        self._user: dict[str, Any] = {}

    @property
    def user(self) -> dict[str, Any]:
        """Return the user record of the last login (name, class, contract)."""
        return self._user

    async def async_login(self) -> dict[str, Any]:
        """Log in and return the user record."""
        client = await self._request("POST", "/loginclient", json=FRONTEND_CLIENT)
        if not isinstance(client, dict) or not isinstance(client.get("token"), str):
            raise SchoolhubApiError("Drei Köche sent no client token")
        data = await self._request(
            "POST",
            "/login",
            token=client["token"],
            json={"username": self._username, "password": self._password, "rememberMe": ""},
        )
        # Only an explicit refusal (subcode other than 0) means wrong credentials;
        # anything else is a service problem and must not start a reauth.
        match data:
            case {"subcode": 0, "user": {"token": str(token), "contractId": _} as user}:
                self._user, self._token = user, token
                return user
            case {"subcode": int(subcode)} if subcode != 0:
                detail = data.get("detail")
                raise SchoolhubAuthError(
                    detail if isinstance(detail, str) else "Drei Köche login failed"
                )
            case _:
                raise SchoolhubApiError("Drei Köche sent an unexpected login reply")

    async def async_get_orders(self, start: date, end: date) -> dict[str, Any]:
        """Return the ordered meals between two dates."""
        if not self._token_is_valid():
            await self.async_login()
        path = f"/preorderhistory/{self._user['contractId']}/{start.isoformat()}TO{end.isoformat()}"
        try:
            orders = await self._request("GET", path, token=self._token)
        except _TokenRejectedError:
            # Retry once with a fresh token; a second rejection is a service error.
            await self.async_login()
            orders = await self._request("GET", path, token=self._token)
        if not isinstance(orders, dict):
            raise SchoolhubApiError("Drei Köche sent no order list")
        return orders

    def _token_is_valid(self) -> bool:
        if not self._token:
            return False
        expires = _jwt_expiry(self._token)
        return expires is not None and expires - TOKEN_MARGIN > time.time()

    async def _request(
        self, method: str, path: str, token: str | None = None, **kwargs: Any
    ) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else None
        response = await fetch(
            self._session, method, f"{API_BASE}{path}", service=SERVICE, headers=headers, **kwargs
        )
        if response.status in (401, 403):
            raise _TokenRejectedError(f"Drei Köche returned HTTP {response.status}")
        if response.status >= 400:
            raise SchoolhubApiError(
                f"Drei Köche returned HTTP {response.status} from {endpoint_name(path)}"
            )
        return response.json()


def _jwt_expiry(token: str) -> float | None:
    """Return the expiry timestamp of a JWT without verifying it."""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return float(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except IndexError, KeyError, TypeError, ValueError:
        return None
