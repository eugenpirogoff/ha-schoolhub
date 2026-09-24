"""Setting up SWOP and Drei Köche entries and the entities they create."""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Any
from unittest.mock import patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from yarl import URL

from custom_components.schoolhub.api import SchoolhubApiError
from custom_components.schoolhub.api.drei_koeche import API_BASE
from custom_components.schoolhub.api.swop import SwopClient
from custom_components.schoolhub.const import DOMAIN, EVENT_NEW_POST, UPDATE_INTERVAL
from custom_components.schoolhub.diagnostics import async_get_config_entry_diagnostics
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN, TodoServices
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    CONF_PASSWORD,
    CONF_USERNAME,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import (
    async_create_clientsession,
    async_get_clientsession,
)

from .conftest import (
    JSON,
    MESSENGER_LATEST,
    SWOP_HOST,
    SWOP_URL,
    load_fixture,
    mock_drei_koeche,
    mock_swop,
    setup_integration,
)

HTML = "<html>Wartungsarbeiten</html>"
HTML_TYPE = {"Content-Type": "text/html"}


def _private_words(entry: MockConfigEntry) -> set[str]:
    """Children's names, logins, school host and password, lower-cased."""
    words = {entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD], SWOP_HOST, "musterschule"}
    mydata = load_fixture("swop_mydata")
    for person in (mydata, *mydata["erziehungsberechtigt_fuer"]):
        words.update(person["vorname"].split())
        words.add(person["nachname"])
        words.add(person.get("loginname", ""))
    for child in (1, 2):
        user = load_fixture(f"dk_login_child{child}")["user"]
        words.update(user["firstname"].split())
        words.update((user["lastname"], user["username"], user["objectName"]))
        words.add(str(user["contractId"]))
    return {word.lower() for word in words if word}


def assert_private_data_hidden(diagnostics: dict[str, Any], entry: MockConfigEntry) -> None:
    """No name, login, host or password appears in the diagnostics, in any casing."""
    text = json.dumps(diagnostics, ensure_ascii=False).lower()
    # Whole words only: "ben" is part of the "subentries" key.
    leaked = sorted(
        word for word in _private_words(entry) if re.search(rf"\b{re.escape(word)}\b", text)
    )
    assert not leaked, leaked


async def test_swop_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert swop_entry.state is ConfigEntryState.LOADED

    for child in ("anna_maria", "ben"):
        for entity_id in (
            f"calendar.{child}_timetable",
            f"sensor.{child}_next_lesson",
            f"sensor.{child}_open_homework",
            f"sensor.{child}_class_news",
            f"todo.{child}_homework",
        ):
            assert hass.states.get(entity_id) is not None, entity_id

    # Monday 09:00: the lesson that is running right now.
    next_lesson = hass.states.get("sensor.anna_maria_next_lesson")
    start = datetime.fromisoformat(next_lesson.attributes["start"])
    end = datetime.fromisoformat(next_lesson.attributes["end"])
    now = datetime.fromisoformat("2026-09-21T09:00:00+02:00")
    assert start <= now < end
    assert next_lesson.state not in ("unknown", "unavailable")

    assert hass.states.get("sensor.anna_maria_class_news").state == "Klassennachricht 3a 1"
    assert hass.states.get("sensor.ben_class_news").state == "Klassennachricht 2c 1"
    assert hass.states.get("sensor.musterschule_school_news").state == "Schulnachricht 1"

    # All recent posts, each with its text, for a scrollable news list.
    posts = hass.states.get("sensor.anna_maria_class_news").attributes["posts"]
    assert [post["title"] for post in posts] == [f"Klassennachricht 3a {n}" for n in (1, 2, 3)]
    assert posts[2]["text"] == "Liebe Eltern,\nKlassennachricht 3a Text 3."

    calendar = hass.states.get("calendar.anna_maria_timetable")
    assert calendar.state == "on"
    assert calendar.attributes["message"] == next_lesson.state

    # All fetched lessons for timetable grids; week_start is the current school
    # week (Mon 2026-09-21), from which a dashboard can go back and forth.
    lessons = calendar.attributes["lessons"]
    assert calendar.attributes["week_start"] == "2026-09-21"
    week = [lesson for lesson in lessons if "2026-09-21" <= lesson["day"] <= "2026-09-25"]
    assert week and len(week) < len(lessons)
    assert week[0]["weekday"] == 0 and week[0]["start"] < week[0]["end"]
    assert all(lesson["icon"] for lesson in week)


async def test_calendar_events(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    result = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            ATTR_ENTITY_ID: "calendar.anna_maria_timetable",
            "start_date_time": "2026-09-14T00:00:00+02:00",
            "end_date_time": "2026-09-15T00:00:00+02:00",
        },
        blocking=True,
        return_response=True,
    )
    events = result["calendar.anna_maria_timetable"]["events"]
    recorded = load_fixture("swop_timetable_child1")
    assert len(events) == sum(1 for lesson in recorded if lesson["erster_tag"] == "2026-09-14")
    assert events[0]["start"] == "2026-09-14T08:00:00+02:00"
    assert events[0]["summary"].endswith("(cancelled)")
    assert events[1]["summary"].endswith("(Substitution)")
    assert "Homework: " in events[1]["description"]


async def test_tick_off_homework(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    open_before = int(hass.states.get("sensor.anna_maria_open_homework").state)
    assert open_before > 0
    items = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.anna_maria_homework", "status": ["needs_action"]},
        blocking=True,
        return_response=True,
    )
    open_items = [
        item
        for item in items["todo.anna_maria_homework"]["items"]
        if item.get("due", "") >= "2026-09-21"
    ]
    # Title: the subject; below: the task, when it was given and the teacher.
    first = open_items[0]
    assert first["summary"].split(" ", 1)[1] in {
        "Deutsch",
        "Mathematik",
        "Englisch",
        "Sachunterricht",
    }
    task, details = first["description"].splitlines()
    assert task.startswith("Hausaufgabe ")
    assert details.startswith("📅 assigned ")
    assert " · 👤 " in details
    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {
            ATTR_ENTITY_ID: "todo.anna_maria_homework",
            "item": open_items[0]["uid"],
            "status": "completed",
        },
        blocking=True,
    )
    assert int(hass.states.get("sensor.anna_maria_open_homework").state) == open_before - 1

    # The ticked-off item stays in the list, as completed.
    items = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.anna_maria_homework"},
        blocking=True,
        return_response=True,
    )
    ticked = next(
        item for item in items["todo.anna_maria_homework"]["items"] if item["uid"] == first["uid"]
    )
    assert ticked["status"] == "completed"

    # The tick survives a reload (it is stored in HA, not SWOP).
    assert await hass.config_entries.async_reload(swop_entry.entry_id)
    await hass.async_block_till_done()
    assert int(hass.states.get("sensor.anna_maria_open_homework").state) == open_before - 1


async def test_new_post_fires_event(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    events = async_capture_events(hass, EVENT_NEW_POST)

    news = load_fixture("swop_news_class_child1")
    new_post = {**news["news_posts"][0], "news_post_id": 4999, "id": 4999, "header": "Neu"}
    news["news_posts"].insert(0, new_post)
    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock, {"swop_news_class_child1": news})

    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Existing posts at install time are the baseline; only the new one fires.
    assert len(events) == 1
    assert events[0].data == {
        "config_entry_id": swop_entry.entry_id,
        "scope": "class",
        "students": ["Anna Maria"],
        "class": "3a",
        "post_id": 4999,
        "title": "Neu",
        "text": "Liebe Eltern,\nKlassennachricht 3a Text 1.",
        "author": "Lehrkraft A.",
        "published": "2026-09-22T06:20:33+00:00",
    }
    assert hass.states.get("sensor.anna_maria_class_news").state == "Neu"


CLASS_NEWS_URL = re.compile(re.escape(f"{SWOP_URL}/swop_info_news_posts_page/301/20/0.json"))


async def test_failed_news_at_setup_fires_no_events_later(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    # Mocks match in registration order, so this failure shadows the fixture.
    aioclient_mock.get(CLASS_NEWS_URL, status=500)
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert swop_entry.runtime_data.last_update_success
    assert hass.states.get("sensor.anna_maria_class_news").state == "unknown"
    events = async_capture_events(hass, EVENT_NEW_POST)

    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # The failed fetch set no baseline, so the posts become it now: no events.
    assert events == []
    news = hass.states.get("sensor.anna_maria_class_news")
    assert news.state == "Klassennachricht 3a 1"
    assert len(news.attributes["posts"]) == 3


async def test_failed_news_after_refresh_keeps_posts(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    before = hass.states.get("sensor.anna_maria_class_news")
    events = async_capture_events(hass, EVENT_NEW_POST)

    async def tick() -> None:
        frozen_time.tick(UPDATE_INTERVAL["swop"])
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    aioclient_mock.clear_requests()
    aioclient_mock.get(CLASS_NEWS_URL, status=500)
    mock_swop(aioclient_mock)
    await tick()
    assert swop_entry.runtime_data.last_update_success
    after = hass.states.get("sensor.anna_maria_class_news")
    assert after.state == before.state == "Klassennachricht 3a 1"
    assert after.attributes == before.attributes

    # News comes back unchanged: nothing was new.
    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock)
    await tick()
    assert events == []


async def test_drei_koeche_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    # Setting up one entry sets up the whole domain, so both load.
    with patch(
        "custom_components.schoolhub.async_create_clientsession",
        wraps=async_create_clientsession,
    ) as create_session:
        await setup_integration(hass, swop_entry)
    assert drei_koeche_entry.state is ConfigEntryState.LOADED
    # Drei Köche sends a bearer token, so its session keeps no cookies.
    jars = [call.kwargs["cookie_jar"] for call in create_session.call_args_list]
    assert sum(isinstance(jar, aiohttp.DummyCookieJar) for jar in jars) == 1

    meal = hass.states.get("sensor.anna_maria_lunch_today")
    assert meal.state.startswith("Spaghetti Bolognese")
    assert meal.attributes["allergens"]
    assert len(meal.attributes["upcoming"]) == 7
    meals = hass.states.get("calendar.anna_maria_lunch").attributes["meals"]
    lunch_week = [m for m in meals if "2026-09-21" <= m["day"] <= "2026-09-25"]
    assert [meal["weekday"] for meal in lunch_week] == [0, 1, 2, 3, 4]
    assert lunch_week[0]["menu"] == meal.state

    # One device per child and service, both named after the child.
    timetable = entity_registry.async_get("calendar.anna_maria_timetable")
    lunch = entity_registry.async_get("calendar.anna_maria_lunch")
    swop_device = device_registry.async_get(timetable.device_id)
    lunch_device = device_registry.async_get(lunch.device_id)
    assert swop_device.name == lunch_device.name == "Anna Maria"
    assert (swop_device.model, swop_device.model_id) == ("SWOP", "3a")
    assert (lunch_device.model, lunch_device.model_id) == ("Drei Köche", "3a")
    assert lunch_device.config_entry_id == drei_koeche_entry.entry_id


async def test_wrong_password_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json", json={"success": False}, headers=JSON
    )
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()

    assert swop_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_expired_session_logs_in_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    logins = sum(1 for call in aioclient_mock.mock_calls if "login" in str(call[1]))

    # SWOP answers an expired session with its HTML login page, once.
    calls = 0

    async def expired_once(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return AiohttpClientMockResponse(
                method, url, text="<html>login</html>", headers={"Content-Type": "text/html"}
            )
        return AiohttpClientMockResponse(
            method, url, json=load_fixture("swop_mydata"), headers=JSON
        )

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{SWOP_URL}/swop_mydata.json", side_effect=expired_once)
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert calls == 2
    assert logins == 1
    assert sum(1 for call in aioclient_mock.mock_calls if "login" in str(call[1])) == 1
    assert swop_entry.state is ConfigEntryState.LOADED


async def test_redirect_on_expired_session_logs_in_again(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """A redirect to the login page on a GET means the session expired."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    calls = 0

    async def redirect_once(method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return AiohttpClientMockResponse(
                method, url, status=302, headers={"Location": f"{SWOP_URL}/login"}
            )
        return AiohttpClientMockResponse(
            method, url, json=load_fixture("swop_mydata"), headers=JSON
        )

    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{SWOP_URL}/swop_mydata.json", side_effect=redirect_once)
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert calls == 2
    assert sum(1 for call in aioclient_mock.mock_calls if "login" in str(call[1])) == 1
    assert not any(str(call[1]).endswith("/login") for call in aioclient_mock.mock_calls)
    assert swop_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.anna_maria_timetable").state != STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_redirect_after_login_again_is_no_auth_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(
        f"{SWOP_URL}/swop_mydata.json", status=302, headers={"Location": f"{SWOP_URL}/login"}
    )
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    logins = [call for call in aioclient_mock.mock_calls if "login" in str(call[1])]
    mydata = [call for call in aioclient_mock.mock_calls if "swop_mydata" in str(call[1])]
    assert (len(logins), len(mydata)) == (1, 2)
    assert swop_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.anna_maria_timetable").state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_maintenance_page_at_login_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    aioclient_mock.post(f"{SWOP_URL}/session/login/post.json", text=HTML, headers=HTML_TYPE)
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()

    assert swop_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_web_page_after_fresh_login_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    aioclient_mock.get(f"{SWOP_URL}/swop_mydata.json", text=HTML, headers=HTML_TYPE)
    mock_swop(aioclient_mock)
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()

    assert swop_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_web_page_after_login_again_is_no_auth_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    # The old session gets a web page, and so does the new one (maintenance).
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{SWOP_URL}/swop_mydata.json", text=HTML, headers=HTML_TYPE)
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    logins = [call for call in aioclient_mock.mock_calls if "login" in str(call[1])]
    mydata = [call for call in aioclient_mock.mock_calls if "swop_mydata" in str(call[1])]
    assert (len(logins), len(mydata)) == (1, 2)
    assert swop_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("calendar.anna_maria_timetable").state == STATE_UNAVAILABLE
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_unload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert await hass.config_entries.async_unload(swop_entry.entry_id)
    assert swop_entry.state is ConfigEntryState.NOT_LOADED


async def test_unreachable_service_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    aioclient_mock.post(f"{SWOP_URL}/session/login/post.json", exc=aiohttp.ClientError)
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()
    assert swop_entry.state is ConfigEntryState.SETUP_RETRY


async def test_failed_refresh_makes_entities_unavailable(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert hass.states.get("calendar.anna_maria_timetable").state != STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(re.compile(r".*"), status=500)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("calendar.anna_maria_timetable").state == STATE_UNAVAILABLE


async def test_rejected_drei_koeche_login_starts_reauth(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    drei_koeche_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, drei_koeche_entry)

    # The token is rejected and logging in again fails too.
    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API_BASE}/loginclient", json=load_fixture("dk_login_client"))
    aioclient_mock.post(f"{API_BASE}/login", json=load_fixture("dk_login_failed"))
    aioclient_mock.get(re.compile(rf"{re.escape(API_BASE)}/preorderhistory/"), status=401)
    frozen_time.tick(UPDATE_INTERVAL["drei_koeche"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_diagnostics_hide_logins_and_names(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, swop_entry)

    assert_private_data_hidden(diagnostics, swop_entry)
    assert diagnostics["last_update_success"] is True
    assert diagnostics["last_exception"] is None
    assert [child["class"] for child in diagnostics["data"]["children"]] == ["3a", "2c"]


async def test_drei_koeche_diagnostics_hide_logins_and_names(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, drei_koeche_entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, drei_koeche_entry)

    assert_private_data_hidden(diagnostics, drei_koeche_entry)
    assert diagnostics["last_update_success"] is True
    assert diagnostics["data"]["class"] == "3a"


async def test_diagnostics_of_entry_that_is_not_loaded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    """An entry waiting for a setup retry has no coordinator; diagnostics still work."""
    aioclient_mock.post(f"{SWOP_URL}/session/login/post.json", text=HTML, headers=HTML_TYPE)
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()
    assert swop_entry.state is ConfigEntryState.SETUP_RETRY

    diagnostics = await async_get_config_entry_diagnostics(hass, swop_entry)

    assert diagnostics["state"] == ConfigEntryState.SETUP_RETRY.value
    assert "data" not in diagnostics
    assert_private_data_hidden(diagnostics, swop_entry)


async def test_errors_and_diagnostics_hide_request_paths(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    drei_koeche_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed request logs only the endpoint, never the child's contract id."""
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, drei_koeche_entry)

    aioclient_mock.clear_requests()
    aioclient_mock.get(re.compile(rf"{re.escape(API_BASE)}/preorderhistory/"), status=500)
    frozen_time.tick(UPDATE_INTERVAL["drei_koeche"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Called directly: the frozen clock has expired the HTTP client's access token.
    diagnostics = await async_get_config_entry_diagnostics(hass, drei_koeche_entry)
    assert diagnostics["last_update_success"] is False
    assert diagnostics["last_exception"] == "UpdateFailed"
    assert_private_data_hidden(diagnostics, drei_koeche_entry)
    assert "HTTP 500 from preorderhistory" in caplog.text
    for text in (json.dumps(diagnostics), caplog.text):
        assert "9001" not in text
        assert "preorderhistory/" not in text


async def test_german_entity_ids(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    """Entity IDs follow the language HA had when SchoolHub was added."""
    hass.config.language = "de"
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, swop_entry)
    for entity_id in (
        "calendar.anna_maria_stundenplan",
        "calendar.anna_maria_essen",
        "todo.anna_maria_hausaufgaben",
        "sensor.anna_maria_nachste_stunde",
        "sensor.anna_maria_essen_heute",
        "sensor.musterschule_schulnachrichten",
    ):
        assert hass.states.get(entity_id), entity_id


async def test_new_messages(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    state = hass.states.get("binary_sensor.musterschule_new_messages")
    assert state.state == STATE_ON
    assert state.attributes["unread_chats"] == 1
    assert state.attributes["messenger_url"] == f"{SWOP_URL}/messenger"
    # Only ids are compared: no message text ends up in Home Assistant.
    assert "Nachricht" not in str(state.attributes)

    # All read now; then the messenger fails and the last count is kept.
    latest = load_fixture("swop_messenger_latest")
    for item in latest["latest_message_ids"]:
        item["read_messages_up_to_id"] = item["latest_message_id"]
    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock, {"swop_messenger_latest": latest})
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.musterschule_new_messages").state == STATE_OFF

    aioclient_mock.clear_requests()
    aioclient_mock.post(MESSENGER_LATEST, status=403, json={"error": "Unauthorized"})
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert swop_entry.state is ConfigEntryState.LOADED
    assert hass.states.get("binary_sensor.musterschule_new_messages").state == STATE_OFF


async def test_new_messages_sensor_when_messenger_fails_at_setup(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """The sensor exists (unavailable) if the messenger is down at setup."""
    aioclient_mock.post(MESSENGER_LATEST, status=500, json={"error": "down"})
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert swop_entry.state is ConfigEntryState.LOADED
    state = hass.states.get("binary_sensor.musterschule_new_messages")
    assert state.state == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock)
    frozen_time.tick(UPDATE_INTERVAL["swop"])
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.musterschule_new_messages")
    assert state.state == STATE_ON
    assert state.attributes["unread_chats"] == 1


async def test_no_messenger_sensor_without_messenger(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    basedata = load_fixture("swop_basedata")
    basedata["global_options"]["swop_messenger"] = "false"
    mock_swop(aioclient_mock, {"swop_basedata": basedata})
    await setup_integration(hass, swop_entry)
    assert hass.states.get("binary_sensor.musterschule_new_messages") is None


async def test_messenger_redirect_is_not_followed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The messenger token is never resent to a redirect target."""
    target = "https://evil.example/steal"
    aioclient_mock.post(MESSENGER_LATEST, status=308, json={}, headers=JSON | {"Location": target})
    aioclient_mock.post(target, json={}, headers=JSON)
    client = SwopClient(async_get_clientsession(hass), SWOP_URL, "a", "b")
    with pytest.raises(SchoolhubApiError):
        await client.async_get_latest_message_ids("messenger", "token", "host")
    assert [str(call[1]) for call in aioclient_mock.mock_calls] == [MESSENGER_LATEST]


async def test_week_homework_for_a_per_day_view(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    """The timetable lists this week's homework, given or due, with its tick."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    homework = hass.states.get("calendar.anna_maria_timetable").attributes["week_homework"]
    assert homework
    for item in homework:
        assigned_this_week = "2026-09-21" <= item["assigned"] <= "2026-09-25"
        due_this_week = item["due"] is not None and "2026-09-21" <= item["due"] <= "2026-09-25"
        assert assigned_this_week or due_this_week
        assert item["done"] is False and item["icon"]

    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {
            ATTR_ENTITY_ID: "todo.anna_maria_homework",
            "item": homework[0]["uid"],
            "status": "completed",
        },
        blocking=True,
    )
    homework = hass.states.get("calendar.anna_maria_timetable").attributes["week_homework"]
    assert homework[0]["done"] is True
