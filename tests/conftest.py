"""Shared fixtures: fake SWOP and Drei Köche servers built from recorded responses."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import re
from typing import Any
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.schoolhub.api.drei_koeche import API_BASE
from custom_components.schoolhub.const import (
    CONF_SERVICE,
    DOMAIN,
    SERVICE_DREI_KOECHE,
    SERVICE_SWOP,
)
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME
from homeassistant.core import HomeAssistant

FIXTURES = Path(__file__).parent / "fixtures"
SWOP_HOST = "musterschule.swop.schule"
SWOP_URL = f"https://{SWOP_HOST}"
MESSENGER_LATEST = "https://messenger.swop.schule/api/client-get-latest-message-ids"
JSON = {"Content-Type": "application/json; charset=utf-8"}

# Monday of the second recorded week, during the 2nd lesson (08:55-09:40).
NOW = "2026-09-21 09:00:00+02:00"


def load_fixture(name: str) -> Any:
    """Load a recorded, anonymized API response."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


_load = load_fixture


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def frozen_time(freezer: FrozenDateTimeFactory, hass: HomeAssistant) -> FrozenDateTimeFactory:
    """Pin the clock inside the recorded weeks, in the school's time zone."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    return freezer


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up an entry. Setting up one entry sets up all entries of the domain."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _url(path: str) -> re.Pattern[str]:
    return re.compile(re.escape(path) + r"(\?|$)")


def mock_swop(aioclient_mock: AiohttpClientMocker, overrides: dict[str, Any] | None = None) -> None:
    """Register a SWOP portal with two children in two classes.

    `overrides` replaces recorded responses by fixture name.
    """
    overrides = overrides or {}

    def load_fixture(name: str) -> Any:
        return overrides[name] if name in overrides else _load(name)

    mydata = load_fixture("swop_mydata")
    children = mydata["erziehungsberechtigt_fuer"]
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json", json=load_fixture("swop_login"), headers=JSON
    )
    aioclient_mock.get(_url(f"{SWOP_URL}/swop_mydata.json"), json=mydata, headers=JSON)
    aioclient_mock.get(
        _url(f"{SWOP_URL}/swop_basedata.json"), json=load_fixture("swop_basedata"), headers=JSON
    )
    aioclient_mock.get(
        _url(f"{SWOP_URL}/swop_info_navigation.json"),
        json=load_fixture("swop_navigation"),
        headers=JSON,
    )
    aioclient_mock.get(
        _url(f"{SWOP_URL}/swop_info/1.json"), json=load_fixture("swop_info_school"), headers=JSON
    )
    aioclient_mock.get(
        re.compile(re.escape(f"{SWOP_URL}/swop_info_news_posts_page/1/10/0.json")),
        json=load_fixture("swop_news_school"),
        headers=JSON,
    )
    aioclient_mock.get(
        _url(f"{SWOP_URL}/swop_messenger_my_chats.json"),
        json=load_fixture("swop_messenger_chats"),
        headers=JSON,
    )
    aioclient_mock.get(
        _url(f"{SWOP_URL}/swop_meine_chat_einstellungen.json"),
        json=load_fixture("swop_messenger_settings"),
        headers=JSON,
    )
    aioclient_mock.post(
        MESSENGER_LATEST, json=load_fixture("swop_messenger_latest"), headers=JSON, status=201
    )
    for index, child in enumerate(children, start=1):
        klassenzug = child["klassenzug_id"]
        page = child["klassenseite_menue_id"]
        aioclient_mock.get(
            re.compile(rf"{re.escape(SWOP_URL)}/swop_zeitabschnitte/.*klassenzug_id={klassenzug}"),
            json=load_fixture(f"swop_timetable_child{index}"),
            headers=JSON,
        )
        aioclient_mock.get(
            re.compile(rf"{re.escape(SWOP_URL)}/swop_udoks/.*klassenzug_id={klassenzug}"),
            json=load_fixture(f"swop_homework_child{index}"),
            headers=JSON,
        )
        aioclient_mock.get(
            _url(f"{SWOP_URL}/swop_info/{page}.json"),
            json=load_fixture("swop_info_class"),
            headers=JSON,
        )
        aioclient_mock.get(
            re.compile(re.escape(f"{SWOP_URL}/swop_info_news_posts_page/{page}/20/0.json")),
            json=load_fixture(f"swop_news_class_child{index}"),
            headers=JSON,
        )


def mock_drei_koeche(aioclient_mock: AiohttpClientMocker, child: int = 1, **user: Any) -> None:
    """Register the Drei Köche API for one child; `user` overrides e.g. the name."""
    login = load_fixture(f"dk_login_child{child}")
    login["user"].update(user)
    aioclient_mock.post(f"{API_BASE}/loginclient", json=load_fixture("dk_login_client"))
    aioclient_mock.post(f"{API_BASE}/login", json=login)
    aioclient_mock.get(
        re.compile(rf"{re.escape(API_BASE)}/preorderhistory/{login['user']['contractId']}/"),
        json=load_fixture(f"dk_preorderhistory_child{child}"),
    )


@pytest.fixture
def swop_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured SWOP login."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SWOP Musterschule",
        unique_id=f"swop_{SWOP_HOST}_eltern.muster",
        data={
            CONF_SERVICE: SERVICE_SWOP,
            CONF_URL: SWOP_URL,
            CONF_USERNAME: "eltern.muster",
            CONF_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def drei_koeche_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A configured Drei Köche login of the first child."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Drei Köche Anna Maria",
        unique_id="drei_koeche_k.muster0001",
        data={
            CONF_SERVICE: SERVICE_DREI_KOECHE,
            CONF_USERNAME: "K.Muster0001",
            CONF_PASSWORD: "secret",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Skip setting up entries in config flow tests."""
    with patch("custom_components.schoolhub.async_setup_entry", return_value=True):
        yield
