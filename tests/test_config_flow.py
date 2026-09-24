"""Config flow: adding SWOP and Drei Köche logins, reauth and options."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.schoolhub.api import SchoolhubAuthError, SchoolhubConnectionError
from custom_components.schoolhub.api.drei_koeche import API_BASE
from custom_components.schoolhub.const import (
    CONF_SERVICE,
    CONF_WEEKS_FUTURE,
    CONF_WEEKS_PAST,
    DOMAIN,
    SERVICE_DREI_KOECHE,
    SERVICE_SWOP,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import JSON, SWOP_HOST, SWOP_URL, load_fixture, mock_drei_koeche, mock_swop

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

FLOW = "custom_components.schoolhub.config_flow"


async def _start(hass: HomeAssistant, service: str) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": service}
    )
    assert result["type"] is FlowResultType.FORM
    return result["flow_id"]


async def test_add_swop(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_swop(aioclient_mock)
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            # A pasted link is reduced to the portal address.
            CONF_URL: f"{SWOP_URL}/login?x=1",
            CONF_USERNAME: " Eltern.Muster ",
            CONF_PASSWORD: "secret",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "SWOP Musterschule"
    assert result["data"] == {
        CONF_SERVICE: SERVICE_SWOP,
        CONF_URL: SWOP_URL,
        CONF_USERNAME: "Eltern.Muster",
        CONF_PASSWORD: "secret",
    }
    assert result["result"].unique_id == f"swop_{SWOP_HOST}_eltern.muster"


async def test_add_swop_wrong_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json", json={"success": False}, headers=JSON
    )
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster", CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_add_swop_unknown_school(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{SWOP_URL}/session/login/post.json", status=404)
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster", CONF_PASSWORD: "x"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_add_swop_maintenance_page(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # A maintenance page or captive portal is not a wrong password.
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json",
        text="<html>Wartungsarbeiten</html>",
        headers={"Content-Type": "text/html"},
    )
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster", CONF_PASSWORD: "x"}
    )
    assert result["errors"] == {"base": "cannot_connect"}


async def test_add_swop_without_children(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mydata = load_fixture("swop_mydata")
    mydata["erziehungsberechtigt_fuer"] = []
    mock_swop(aioclient_mock, {"swop_mydata": mydata})
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster", CONF_PASSWORD: "secret"}
    )
    assert result["errors"] == {"base": "no_students"}


async def test_add_swop_twice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "ELTERN.MUSTER", CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_add_drei_koeche(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_drei_koeche(aioclient_mock)
    flow_id = await _start(hass, SERVICE_DREI_KOECHE)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "K.Muster0001", CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Drei Köche Anna Maria"
    assert result["data"][CONF_SERVICE] == SERVICE_DREI_KOECHE


async def test_add_drei_koeche_wrong_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{API_BASE}/loginclient", json=load_fixture("dk_login_client"))
    aioclient_mock.post(f"{API_BASE}/login", json=load_fixture("dk_login_failed"))
    flow_id = await _start(hass, SERVICE_DREI_KOECHE)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "K.Muster0001", CONF_PASSWORD: "wrong"}
    )
    assert result["errors"] == {"base": "invalid_auth"}


@pytest.mark.parametrize("entry_fixture", ["swop_entry", "drei_koeche_entry"])
async def test_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    request: pytest.FixtureRequest,
    entry_fixture: str,
) -> None:
    entry: MockConfigEntry = request.getfixturevalue(entry_fixture)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    # A wrong password keeps the form open.
    with (
        patch(f"{FLOW}.SwopClient.async_login", side_effect=SchoolhubAuthError),
        patch(f"{FLOW}.DreiKoecheClient.async_login", side_effect=SchoolhubAuthError),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"


@pytest.mark.parametrize(
    ("side_effect", "error"),
    [
        (SchoolhubAuthError, "invalid_auth"),
        (SchoolhubConnectionError, "cannot_connect"),
        (RuntimeError, "unknown"),
    ],
)
@pytest.mark.parametrize(
    ("service", "client", "user_input"),
    [
        (SERVICE_SWOP, "SwopClient", {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster"}),
        (SERVICE_DREI_KOECHE, "DreiKoecheClient", {CONF_USERNAME: "K.Muster0001"}),
    ],
)
async def test_errors_then_success(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    client: str,
    user_input: dict[str, str],
    side_effect: type[Exception],
    error: str,
) -> None:
    flow_id = await _start(hass, service)
    user_input = {**user_input, CONF_PASSWORD: "secret"}
    with patch(f"{FLOW}.{client}.async_login", side_effect=side_effect):
        result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_add_drei_koeche_twice(
    hass: HomeAssistant, drei_koeche_entry: MockConfigEntry
) -> None:
    flow_id = await _start(hass, SERVICE_DREI_KOECHE)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: "k.muster0001", CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options(hass: HomeAssistant, swop_entry: MockConfigEntry) -> None:
    result = await hass.config_entries.options.async_init(swop_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WEEKS_PAST: 1.0, CONF_WEEKS_FUTURE: 3.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert swop_entry.options == {CONF_WEEKS_PAST: 1, CONF_WEEKS_FUTURE: 3}


@pytest.mark.parametrize("url", ["My School!", "ftp://x.swop.schule", "", "https://"])
async def test_add_swop_invalid_url(hass: HomeAssistant, url: str) -> None:
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_USERNAME: "a", CONF_PASSWORD: "b"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}


@pytest.mark.parametrize(
    "url", ["http://musterschule.swop.schule", "HTTP://swop.example.de:8080/x"]
)
async def test_add_swop_rejects_http(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, url: str
) -> None:
    """A new login never sends the password over plain http."""
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_USERNAME: "a", CONF_PASSWORD: "b"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "https_required"}
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    "url", ["https://10.0.0.5", "https://192.168.1.1:8443", "https://[::1]", "10.0.0.5/login"]
)
async def test_add_swop_rejects_ip_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, url: str
) -> None:
    """A new login needs a host name, not an IP address."""
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: url, CONF_USERNAME: "a", CONF_PASSWORD: "b"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_URL: "invalid_url"}
    assert aioclient_mock.call_count == 0


async def test_add_swop_does_not_follow_redirect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 307 on the login must not resend the password to the redirect target."""
    target = "http://evil.example/steal"
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json",
        status=307,
        json={"success": True},
        headers=JSON | {"Location": target},
    )
    aioclient_mock.post(target, json={"success": True}, headers=JSON)
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: SWOP_URL, CONF_USERNAME: "eltern.muster", CONF_PASSWORD: "secret"}
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [
        f"{SWOP_URL}/session/login/post.json"
    ]


async def test_add_swop_by_subdomain(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_swop(aioclient_mock)
    flow_id = await _start(hass, SERVICE_SWOP)
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_URL: "Musterschule", CONF_USERNAME: "a", CONF_PASSWORD: "b"}
    )
    assert result["data"][CONF_URL] == SWOP_URL


async def test_school_is_prefilled(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": SERVICE_SWOP}
    )
    schema = result["data_schema"].schema
    key = next(key for key in schema if key == CONF_URL)
    assert key.description == {"suggested_value": "https://regenbogen-grundschule.swop.schule"}
    config = schema[key].config
    assert config["custom_value"] is True
    assert config["options"] == [
        {
            "value": "https://regenbogen-grundschule.swop.schule",
            "label": "Ludwigsfelde - Regenbogen Grundschule",
        }
    ]


async def test_configured_schools_are_offered(
    hass: HomeAssistant, swop_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": SERVICE_SWOP}
    )
    schema = result["data_schema"].schema
    config = schema[next(key for key in schema if key == CONF_URL)].config
    assert [option["value"] for option in config["options"]] == [
        "https://regenbogen-grundschule.swop.schule",
        SWOP_URL,
    ]
