"""Which Drei Köche failures count as wrong credentials."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.schoolhub.api.drei_koeche import API_BASE, DreiKoecheClient
from custom_components.schoolhub.api.errors import SchoolhubApiError, SchoolhubAuthError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import load_fixture, mock_drei_koeche

ORDERS = re.compile(rf"{re.escape(API_BASE)}/preorderhistory/")


def _client(hass: HomeAssistant) -> DreiKoecheClient:
    return DreiKoecheClient(async_get_clientsession(hass), "K.Muster0001", "secret")


def _mock_login(aioclient_mock: AiohttpClientMocker, **login: Any) -> None:
    aioclient_mock.post(f"{API_BASE}/loginclient", json=load_fixture("dk_login_client"))
    aioclient_mock.post(f"{API_BASE}/login", **login)


async def test_refused_login_is_auth_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_login(aioclient_mock, json=load_fixture("dk_login_failed"))
    with pytest.raises(SchoolhubAuthError, match="Passwort falsch"):
        await _client(hass).async_login()


@pytest.mark.parametrize(
    "login",
    [
        # Success reply without a usable user record.
        {"json": {"subcode": 0, "title": "Anmeldung", "detail": "erfolgreich", "user": {}}},
        {"json": {"subcode": 0, "detail": "erfolgreich"}},
        {"json": {"detail": "Wartungsarbeiten"}},
        {"json": ["unexpected"]},
        {"status": 401},
        {"status": 403},
        # Body that is not valid UTF-8.
        {"content": b"\xff\xfe{}", "headers": {"Content-Type": "application/json"}},
    ],
)
async def test_other_login_failures_are_api_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, login: dict[str, Any]
) -> None:
    _mock_login(aioclient_mock, **login)
    with pytest.raises(SchoolhubApiError) as err:
        await _client(hass).async_login()
    assert not isinstance(err.value, SchoolhubAuthError)


@pytest.mark.parametrize(
    "client_reply", [{"status": 401}, {"status": 403}, {"json": {"detail": "no token"}}]
)
async def test_rejected_client_login_is_api_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, client_reply: dict[str, Any]
) -> None:
    aioclient_mock.post(f"{API_BASE}/loginclient", **client_reply)
    with pytest.raises(SchoolhubApiError) as err:
        await _client(hass).async_login()
    assert not isinstance(err.value, SchoolhubAuthError)


async def test_orders_retry_once_after_rejected_token(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_drei_koeche(aioclient_mock)
    client = _client(hass)
    await client.async_login()

    # Rejected again right after a fresh login: a service error, not a reauth.
    aioclient_mock.clear_requests()
    _mock_login(aioclient_mock, json=load_fixture("dk_login_child1"))
    aioclient_mock.get(ORDERS, status=403)
    with pytest.raises(SchoolhubApiError) as err:
        await client.async_get_orders(date(2026, 9, 21), date(2026, 9, 25))
    assert not isinstance(err.value, SchoolhubAuthError)
    paths = [
        str(call[1]).removeprefix(API_BASE).split("/")[1] for call in aioclient_mock.mock_calls
    ]
    assert paths == ["preorderhistory", "loginclient", "login", "preorderhistory"]


async def test_http_error_names_endpoint_without_contract_id(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_drei_koeche(aioclient_mock)
    client = _client(hass)
    await client.async_login()
    aioclient_mock.clear_requests()
    aioclient_mock.get(ORDERS, status=500)
    with pytest.raises(SchoolhubApiError) as err:
        await client.async_get_orders(date(2026, 9, 21), date(2026, 9, 25))
    assert str(err.value) == "Drei Köche returned HTTP 500 from preorderhistory"
