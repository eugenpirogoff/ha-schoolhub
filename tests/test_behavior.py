"""Behavior over time and edge cases: clock, cancellations, read-only homework."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.schoolhub import async_remove_config_entry_device
from custom_components.schoolhub.const import CONF_WEEKS_FUTURE, CONF_WEEKS_PAST, DOMAIN
from custom_components.schoolhub.models import parse_meals
from custom_components.schoolhub.sensor import ChildSensor, SchoolNewsSensor
from homeassistant.components.todo import DOMAIN as TODO_DOMAIN, TodoServices
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from .conftest import load_fixture, mock_drei_koeche, mock_swop, setup_integration

INTEGRATION = Path(__file__).parents[1] / "custom_components" / "schoolhub"


def _keys(data: object, prefix: str = "") -> set[str]:
    """Return all key paths of a nested translation dict."""
    if not isinstance(data, dict):
        return {prefix}
    return set().union(*(_keys(value, f"{prefix}/{key}") for key, value in data.items()))


def test_translations_match() -> None:
    """strings.json is the English translation; German has exactly the same keys."""
    en = json.loads((INTEGRATION / "translations/en.json").read_text())
    de = json.loads((INTEGRATION / "translations/de.json").read_text())
    assert json.loads((INTEGRATION / "strings.json").read_text()) == en
    assert _keys(de) == _keys(en)


async def test_every_entity_has_a_translated_name(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, swop_entry)
    names = json.loads((INTEGRATION / "strings.json").read_text())["entity"]
    entries = [e for e in entity_registry.entities.values() if e.platform == DOMAIN]
    # Per child: timetable, homework, homework of the day, 3 sensors, homework
    # day and timetable week pickers; school: news, new messages; lunch:
    # calendar and sensor.
    assert len(entries) == 2 * 8 + 2 + 2
    for entry in entries:
        assert entry.translation_key in names[entry.domain], entry.entity_id


async def test_next_lesson_follows_the_clock(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    before = hass.states.get("sensor.anna_maria_next_lesson").attributes["end"]

    frozen_time.move_to(before)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    after = hass.states.get("sensor.anna_maria_next_lesson").attributes
    assert after["start"] >= before


async def test_cancelled_lesson_does_not_turn_calendar_on(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    # The first lesson on 2026-09-14 (08:00-08:10) is cancelled in the fixture.
    frozen_time.move_to("2026-09-14 08:05:00+02:00")
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    calendar = hass.states.get("calendar.anna_maria_timetable")
    assert calendar.state == "off"
    assert calendar.attributes["start_time"] == "2026-09-14 08:10:00"
    assert hass.states.get("sensor.anna_maria_next_lesson").attributes["lesson_number"] == 1


@pytest.mark.parametrize(
    ("now", "week_start"),
    [
        # Friday still shows the current week.
        ("2026-09-25 12:00:00+02:00", "2026-09-21"),
        # From Saturday on, the calendars show the coming week.
        ("2026-09-26 12:00:00+02:00", "2026-09-28"),
    ],
)
async def test_calendar_week_rolls_over_on_saturday(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
    now: str,
    week_start: str,
) -> None:
    frozen_time.move_to(now)
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, swop_entry)

    for entity_id in ("calendar.anna_maria_timetable", "calendar.anna_maria_lunch"):
        attributes = hass.states.get(entity_id).attributes
        assert attributes["week_start"] == week_start, entity_id


async def test_homework_cannot_be_renamed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    items = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.anna_maria_homework"},
        blocking=True,
        return_response=True,
    )
    uid = items["todo.anna_maria_homework"]["items"][0]["uid"]
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.UPDATE_ITEM,
            {ATTR_ENTITY_ID: "todo.anna_maria_homework", "item": uid, "rename": "Nix"},
            blocking=True,
        )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.ADD_ITEM,
            {ATTR_ENTITY_ID: "todo.anna_maria_homework", "item": "Neu"},
            blocking=True,
        )


async def test_homework_can_be_ticked_with_an_older_summary(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    """Ticking works with the summary older versions showed; renames are rejected.

    Older versions put date labels after the subject ("📖 Deutsch · fällig
    morgen"), so an automation or an open list can still send that.
    """
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    entity_id = "todo.anna_maria_homework"

    async def items() -> list[dict]:
        response = await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.GET_ITEMS,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
            return_response=True,
        )
        return response[entity_id]["items"]

    item = (await items())[0]
    uid, subject = item["uid"], item["summary"]
    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {
            ATTR_ENTITY_ID: entity_id,
            "item": uid,
            "rename": f"{subject} · fällig morgen · 🆕 neu",
            "status": "completed",
        },
        blocking=True,
    )
    assert {i["uid"]: i["status"] for i in await items()}[uid] == "completed"

    for rename in ("Nix", f"{subject}X", f"{subject}X · fällig morgen", subject.split(" ", 1)[1]):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                TODO_DOMAIN,
                TodoServices.UPDATE_ITEM,
                {ATTR_ENTITY_ID: entity_id, "item": uid, "rename": rename},
                blocking=True,
            )


async def test_overdue_homework_is_hidden_and_counts_match(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    # Today is Monday 2026-09-21. Add one homework due today (still listed) and
    # one due the Friday before (overdue, hidden) to the recorded ones.
    records = load_fixture("swop_homework_child1")
    template = next(r for r in records if r["unterrichtsdokumentation"]["hausaufgaben"])
    for record_id, assigned, due in (
        (29001, "2026-09-18", "2026-09-21"),
        (29002, "2026-09-17", "2026-09-18"),
    ):
        record = copy.deepcopy(template)
        record["unterrichtsdokumentation"].update(
            id=record_id, datum=assigned, hausaufgaben_zu_erledigen_bis=due
        )
        records.append(record)
    mock_swop(aioclient_mock, {"swop_homework_child1": records})
    await setup_integration(hass, swop_entry)

    todo = hass.states.get("todo.anna_maria_homework")
    sensor = hass.states.get("sensor.anna_maria_open_homework")
    # Nine recorded homework items are due today or later, plus the one added.
    assert todo.state == "10"
    assert sensor.state == "10"

    response = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: "todo.anna_maria_homework"},
        blocking=True,
        return_response=True,
    )
    items = response["todo.anna_maria_homework"]["items"]
    assert len(items) == 10
    assert all(item["due"] >= "2026-09-21" for item in items if "due" in item)
    uids = {item["uid"] for item in items}
    assert "swop-homework-29001" in uids
    assert "swop-homework-29002" not in uids


async def test_overdue_homework_drops_off_at_midnight(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    # Two homework items are due on 2026-09-22; seven are due later.
    frozen_time.move_to("2026-09-22 23:58:00+02:00")
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    calls = aioclient_mock.call_count
    todo = hass.states.get("todo.anna_maria_homework")
    assert todo.state == "9"
    reported = todo.last_reported

    # Time passing during the day writes no state.
    frozen_time.tick(60)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("todo.anna_maria_homework").last_reported == reported

    # At midnight the list updates without a coordinator refresh.
    frozen_time.move_to("2026-09-23 00:00:00+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("todo.anna_maria_homework").state == "7"
    assert aioclient_mock.call_count == calls


async def test_options_change_the_fetched_weeks(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, swop_entry: MockConfigEntry
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)

    result = await hass.config_entries.options.async_init(swop_entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WEEKS_PAST: 0, CONF_WEEKS_FUTURE: 0}
    )
    await hass.async_block_till_done()

    urls = [str(call[1]) for call in aioclient_mock.mock_calls if "zeitabschnitte" in str(call[1])]
    # Now 2026-09-21 (Monday): only the current week.
    assert "/swop_zeitabschnitte/2026-09-21/2026-09-27.json" in urls[-1]


async def test_remove_device_of_departed_child(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    assert await async_setup_component(hass, "config", {})
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    stale = device_registry.async_get_or_create(
        config_entry_id=swop_entry.entry_id,
        identifiers={(DOMAIN, "swop_student_carla_muster")},
        name="Carla",
    )
    current = entity_registry.async_get("calendar.anna_maria_timetable").device_id
    client = await hass_ws_client(hass)

    async def remove(device_id: str) -> bool:
        await client.send_json_auto_id(
            {
                "type": "config/device_registry/remove_config_entry",
                "config_entry_id": swop_entry.entry_id,
                "device_id": device_id,
            }
        )
        return (await client.receive_json())["success"]

    assert not await remove(current)
    assert await remove(stale.id)
    assert device_registry.async_get(stale.id) is None


async def test_remove_device_of_unloaded_entry_is_refused(
    hass: HomeAssistant, swop_entry: MockConfigEntry, device_registry: dr.DeviceRegistry
) -> None:
    swop_entry.add_to_hass(hass)
    device = device_registry.async_get_or_create(
        config_entry_id=swop_entry.entry_id,
        identifiers={(DOMAIN, "swop_student_carla_muster")},
        name="Carla",
    )
    assert not await async_remove_config_entry_device(hass, swop_entry, device)


def test_several_meals_on_one_day_get_distinct_ids() -> None:
    orders = load_fixture("dk_preorderhistory_child1")
    first = orders["data"][0]
    orders["data"].append({**first, "idx": "M99", "nr": 2, "menuName": "Obst"})
    day = datetime.strptime(first["deliverDate"], "%d.%m.%y").date()
    meals = [meal for meal in parse_meals(orders) if meal.day == day]
    assert len(meals) == 2
    assert meals[0].order_id != meals[1].order_id


async def test_ticks_survive_a_refresh_with_missing_children(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    coordinator = swop_entry.runtime_data
    first, second = coordinator.data.students.values()
    uid = first.homework[0].uid
    coordinator.state.set_done(uid, True)

    async def refresh(overrides: dict[str, object]) -> None:
        aioclient_mock.clear_requests()
        mock_swop(aioclient_mock, overrides)
        await coordinator.async_refresh()
        assert coordinator.last_update_success

    mydata = load_fixture("swop_mydata")
    children = mydata["erziehungsberechtigt_fuer"]

    # SWOP briefly returns no children, or only one of them: keep the tick.
    await refresh({"swop_mydata": {**mydata, "erziehungsberechtigt_fuer": []}})
    assert coordinator.state.is_done(uid)
    await refresh({"swop_mydata": {**mydata, "erziehungsberechtigt_fuer": children[1:]}})
    assert list(coordinator.data.students) == [second.student.key]
    assert coordinator.state.is_done(uid)

    # All children are back, but the homework is gone: now the tick is dropped.
    await refresh({"swop_homework_child1": []})
    assert not coordinator.state.is_done(uid)


async def test_ticks_survive_several_refreshes_with_a_missing_child(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    coordinator = swop_entry.runtime_data
    uid = list(coordinator.data.students.values())[1].homework[0].uid
    coordinator.state.set_done(uid, True)
    mydata = load_fixture("swop_mydata")
    only_first = {**mydata, "erziehungsberechtigt_fuer": mydata["erziehungsberechtigt_fuer"][:1]}

    async def refresh(overrides: dict[str, object] | None = None) -> None:
        aioclient_mock.clear_requests()
        mock_swop(aioclient_mock, overrides)
        await coordinator.async_refresh()
        assert coordinator.last_update_success

    # The second child is missing twice in a row: the tick stays.
    await refresh({"swop_mydata": only_first})
    await refresh({"swop_mydata": only_first})
    assert len(coordinator.data.students) == 1
    assert coordinator.state.is_done(uid)

    # A full refresh without that homework still drops the tick.
    await refresh()
    assert coordinator.state.is_done(uid)
    await refresh({"swop_homework_child2": []})
    assert not coordinator.state.is_done(uid)


async def test_ticks_survive_a_reload_with_a_missing_child(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
) -> None:
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    coordinator = swop_entry.runtime_data
    uid = list(coordinator.data.students.values())[1].homework[0].uid
    coordinator.state.set_done(uid, True)

    # After the reload, the first refresh only returns the first child.
    mydata = load_fixture("swop_mydata")
    aioclient_mock.clear_requests()
    mock_swop(
        aioclient_mock,
        {
            "swop_mydata": {
                **mydata,
                "erziehungsberechtigt_fuer": mydata["erziehungsberechtigt_fuer"][:1],
            }
        },
    )
    assert await hass.config_entries.async_reload(swop_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = swop_entry.runtime_data
    assert len(coordinator.data.students) == 1
    assert coordinator.state.is_done(uid)


async def test_stored_state_without_known_children_loads(
    hass: HomeAssistant,
    hass_storage: dict[str, Any],
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
) -> None:
    """State stored by an older version has no list of known children."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    uid = next(iter(swop_entry.runtime_data.data.students.values())).homework[0].uid
    assert await hass.config_entries.async_unload(swop_entry.entry_id)
    await hass.async_block_till_done()
    hass_storage[f"{DOMAIN}.{swop_entry.entry_id}"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"{DOMAIN}.{swop_entry.entry_id}",
        "data": {"done": [uid, "gone"], "seen": {}},
    }

    assert await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()
    state = swop_entry.runtime_data.state
    assert state.is_done(uid)
    assert not state.is_done("gone")
    await state.async_flush()
    stored = hass_storage[f"{DOMAIN}.{swop_entry.entry_id}"]["data"]
    assert stored["students"] == sorted(swop_entry.runtime_data.data.students)


async def test_child_added_later_gets_entities(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    mydata = load_fixture("swop_mydata")
    children = mydata["erziehungsberechtigt_fuer"]
    mock_swop(
        aioclient_mock, {"swop_mydata": {**mydata, "erziehungsberechtigt_fuer": children[:1]}}
    )
    await setup_integration(hass, swop_entry)
    new_entities = (
        "calendar.ben_timetable",
        "sensor.ben_next_lesson",
        "sensor.ben_open_homework",
        "sensor.ben_class_news",
        "todo.ben_homework",
    )
    assert hass.states.get("calendar.anna_maria_timetable")
    assert not any(hass.states.get(entity_id) for entity_id in new_entities)
    anna = entity_registry.async_get("calendar.anna_maria_timetable").unique_id

    # The second child shows up on the next refresh, without a reload.
    aioclient_mock.clear_requests()
    mock_swop(aioclient_mock)
    await swop_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    for entity_id in new_entities:
        assert hass.states.get(entity_id), entity_id
    assert entity_registry.async_get("calendar.anna_maria_timetable").unique_id == anna
    assert len(hass.states.async_entity_ids("calendar")) == 2


def test_personal_and_fast_changing_attributes_are_not_recorded() -> None:
    """Names and per-lesson details stay out of the recorder."""
    child = ChildSensor._Entity__combined_unrecorded_attributes  # type: ignore[attr-defined]
    assert {
        "teacher",
        "room",
        "start",
        "end",
        "lesson_number",
        "substituted",
        "author",
        "today",
        "items",
        "text",
        "posts",
    } <= child
    news = SchoolNewsSensor._Entity__combined_unrecorded_attributes  # type: ignore[attr-defined]
    assert {"author", "text", "posts"} <= news


@pytest.mark.parametrize(
    ("language", "details"),
    [
        ("en", "📅 assigned today (09/21) · 👤 "),
        ("de", "📅 aufgegeben heute (21.09.) · 👤 "),
    ],
)
async def test_homework_shows_when_it_was_given_and_is_due(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    language: str,
    details: str,
) -> None:
    """Each fact once: subject as title, task and when it was given below, due date as due."""
    hass.config.language = language
    records = load_fixture("swop_homework_child1")
    record = {
        **records[0]["unterrichtsdokumentation"],
        "id": 29100,
        "datum": "2026-09-21",
        "hausaufgaben": "Lesen S. 12",
        "hausaufgaben_zu_erledigen_bis": "2026-09-22",
    }
    records.append({"unterrichtsdokumentation": record})
    mock_swop(aioclient_mock, {"swop_homework_child1": records})
    await setup_integration(hass, swop_entry)

    entity_id = next(
        state.entity_id
        for state in hass.states.async_all("todo")
        if state.entity_id.startswith("todo.anna_maria")
    )
    response = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: entity_id},
        blocking=True,
        return_response=True,
    )
    item = next(i for i in response[entity_id]["items"] if i["uid"] == "swop-homework-29100")
    assert " · " not in item["summary"]
    assert item["due"] == "2026-09-22"
    task, line = item["description"].splitlines()
    assert task == "Lesen S. 12"
    assert line.startswith(details)


async def test_homework_day_picker(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """The picker starts on today, can show another day and returns to today."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    entity_id = "select.anna_maria_homework_day"
    # NOW is Monday.
    assert hass.states.get(entity_id).state == "monday"
    assert hass.states.get("select.ben_homework_day").state == "monday"

    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: entity_id, "option": "wednesday"}, blocking=True
    )
    assert hass.states.get(entity_id).state == "wednesday"

    # Back to today after 15 minutes without another pick.
    frozen_time.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "monday"

    # And at midnight it moves on to the new day.
    frozen_time.move_to("2026-09-22 00:00:01+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "tuesday"


async def test_homework_day_picker_on_weekends(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    frozen_time.move_to("2026-09-26 10:00:00+02:00")
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    assert hass.states.get("select.anna_maria_homework_day").state == "monday"


async def test_homework_of_the_day_follows_the_day_picker(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """The day list shows what is due or given on the picked day; today also all open."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    day_list, full_list = "todo.anna_maria_homework_of_the_day", "todo.anna_maria_homework"

    async def uids(entity_id: str) -> set[str]:
        response = await hass.services.async_call(
            TODO_DOMAIN,
            TodoServices.GET_ITEMS,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
            return_response=True,
        )
        return {item["uid"] for item in response[entity_id]["items"]}

    # Today (Monday): everything still open.
    assert await uids(day_list) == await uids(full_list)

    # Wednesday: due on Wednesday (20044, 20059) or given on it (20051, ...).
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: "select.anna_maria_homework_day", "option": "wednesday"},
        blocking=True,
    )
    wednesday = {f"swop-homework-{n}" for n in (20044, 20059, 20051, 20052, 20055, 20060, 20061)}
    assert await uids(day_list) == wednesday
    assert hass.states.get(day_list).state == str(len(wednesday))
    # The other child's list does not change.
    assert await uids("todo.ben_homework_of_the_day") == await uids("todo.ben_homework")

    # Ticking in the day list ticks the homework everywhere.
    await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.UPDATE_ITEM,
        {ATTR_ENTITY_ID: day_list, "item": "swop-homework-20044", "status": "completed"},
        blocking=True,
    )
    response = await hass.services.async_call(
        TODO_DOMAIN,
        TodoServices.GET_ITEMS,
        {ATTR_ENTITY_ID: full_list, "status": ["completed"]},
        blocking=True,
        return_response=True,
    )
    assert [item["uid"] for item in response[full_list]["items"]] == ["swop-homework-20044"]

    # Back on today, ticked-off homework only shows if due or given today.
    frozen_time.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert await uids(day_list) == await uids(full_list) - {"swop-homework-20044"}


async def test_timetable_week_picker(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Arrows step through the fetched weeks, stop at the ends and return to this week."""
    mock_swop(aioclient_mock)
    await setup_integration(hass, swop_entry)
    entity_id = "select.anna_maria_timetable_week"
    state = hass.states.get(entity_id)
    assert state.state == "0"
    assert state.attributes["options"] == ["-2", "-1", "0", "1", "2", "3", "4"]

    for _ in range(3):
        await hass.services.async_call(
            "select", "select_previous", {ATTR_ENTITY_ID: entity_id, "cycle": False}, blocking=True
        )
    assert hass.states.get(entity_id).state == "-2"

    await hass.services.async_call(
        "select", "select_next", {ATTR_ENTITY_ID: entity_id, "cycle": False}, blocking=True
    )
    assert hass.states.get(entity_id).state == "-1"

    frozen_time.tick(timedelta(minutes=16))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "0"

    # The options follow the fetched weeks (changing them reloads the entry).
    hass.config_entries.async_update_entry(
        swop_entry, options={CONF_WEEKS_PAST: 1, CONF_WEEKS_FUTURE: 1}
    )
    assert await hass.config_entries.async_reload(swop_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).attributes["options"] == ["-1", "0", "1"]


async def test_lunch_today_changes_at_midnight_only(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    drei_koeche_entry: MockConfigEntry,
    frozen_time: FrozenDateTimeFactory,
) -> None:
    """Time-dependent sensors are rewritten when their value changes, not every minute."""
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, drei_koeche_entry)
    entity_id = "sensor.anna_maria_lunch_today"
    monday = hass.states.get(entity_id)
    tuesday_menu = monday.attributes["upcoming"][0]["menu"]

    frozen_time.tick(timedelta(minutes=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).last_reported == monday.last_reported

    frozen_time.move_to("2026-09-22 00:00:01+02:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state.startswith(tuesday_menu.split(" (")[0][:20])
