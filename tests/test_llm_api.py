"""The SchoolHub LLM API: tools a conversation agent can call."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.schoolhub.const import DOMAIN
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import llm
from homeassistant.setup import async_setup_component

from .conftest import mock_drei_koeche, mock_swop, setup_integration

LLM_CONTEXT = llm.LLMContext(
    platform="test", context=Context(), language="en", assistant="conversation", device_id=None
)


async def _api(hass: HomeAssistant) -> llm.APIInstance:
    return await llm.async_get_api(hass, DOMAIN, LLM_CONTEXT)


async def _call(hass: HomeAssistant, name: str, **args: Any) -> dict[str, Any]:
    """Call a tool as an agent would: arguments validated by its schema."""
    api = await _api(hass)
    tool = next(tool for tool in api.tools if tool.name == name)
    tool_input = llm.ToolInput(tool_name=name, tool_args=tool.parameters(args))
    return await tool.async_call(hass, tool_input, LLM_CONTEXT)


@pytest.fixture
async def loaded(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> MockConfigEntry:
    """SWOP with two children, and a lunch login for the first."""
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock)
    await setup_integration(hass, swop_entry)
    return swop_entry


@pytest.mark.usefixtures("loaded")
async def test_prompt_names_children_without_middle_names(hass: HomeAssistant) -> None:
    api = await _api(hass)
    assert "Anna (class 3a), Ben (class 2c)" in api.api_prompt
    assert "Maria" not in api.api_prompt
    assert "Today is Monday, 2026-09-21; tomorrow is Tuesday, 2026-09-22." in api.api_prompt
    assert all(tool.integration == DOMAIN for tool in api.tools)
    assert [tool.name for tool in api.tools] == [
        "get_homework",
        "get_timetable",
        "get_lunch",
        "get_school_news",
    ]


async def test_prompt_without_children(hass: HomeAssistant) -> None:
    """The API is there with no login set up yet, and says so."""
    assert await async_setup_component(hass, DOMAIN, {})
    api = await _api(hass)
    assert "these children: none yet." in api.api_prompt


async def test_homework(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    result = await _call(hass, "get_homework", child="anna")
    assert result["today"] == "2026-09-21"
    [child] = result["children"]
    assert child["child"] == "Anna"
    items = child["homework"]
    due = [item["due"] for item in items if item["due"] != "no due date"]
    assert due == sorted(due)
    dated = next(item for item in items if item["due"] == "2026-09-23")
    assert dated["due_weekday"] == "Wednesday"
    assert dated["due_in_days"] == 2
    assert "done" not in dated

    # Ticked-off homework is left out unless asked for.
    coordinator = loaded.runtime_data
    anna = next(iter(coordinator.data.students.values()))
    coordinator.state.set_done(anna.homework[-1].uid, True)
    open_count = len((await _call(hass, "get_homework", child="Anna"))["children"][0]["homework"])
    with_done = (await _call(hass, "get_homework", child="Anna", include_done=True))["children"][0]
    assert len(with_done["homework"]) == open_count + 1
    assert sum(item.get("done", False) for item in with_done["homework"]) == 1


@pytest.mark.usefixtures("loaded")
async def test_timetable(hass: HomeAssistant) -> None:
    result = await _call(hass, "get_timetable", child="Ben", days="2")
    [child] = result["children"]
    assert child["child"] == "Ben"
    monday, tuesday = child["days"]
    assert (monday["date"], monday["date_in_days"]) == ("2026-09-21", 0)
    assert tuesday["date_weekday"] == "Tuesday"
    first = monday["lessons"][0]
    # Lesson 0 (before the first lesson) keeps its number.
    assert first["number"] == 0
    assert {"start", "end", "subject"} <= first.keys()


@pytest.mark.usefixtures("loaded")
async def test_timetable_outside_fetched_weeks(hass: HomeAssistant) -> None:
    result = await _call(hass, "get_timetable", child="Anna", date="2027-01-04")
    assert result["children"][0]["days"][0]["note"] == "No data fetched for this day"


@pytest.mark.usefixtures("loaded")
async def test_lunch(hass: HomeAssistant) -> None:
    """Lunch is matched to the SWOP child; children without a login are left out."""
    result = await _call(hass, "get_lunch", days=3)
    [child] = result["children"]
    assert (child["child"], child["class"]) == ("Anna", "3a")
    today = child["days"][0]
    # Allergen codes are not passed on.
    assert today["meals"] == [{"menu": "Spaghetti Bolognese (Rind, Tomate, Zwiebel) Reibekäse"}]
    assert today["lunch_ordered"] is True
    assert len(child["days"]) == 3


@pytest.mark.usefixtures("loaded")
async def test_lunch_not_ordered(hass: HomeAssistant) -> None:
    """A day without an order says so, instead of an empty list."""
    result = await _call(hass, "get_lunch", date="2026-09-26")  # a Saturday
    [day] = result["children"][0]["days"]
    assert day["lunch_ordered"] is False
    assert day["note"] == "No lunch ordered for Anna"
    assert "meals" not in day


@pytest.mark.usefixtures("loaded")
async def test_news(hass: HomeAssistant) -> None:
    result = await _call(hass, "get_school_news", limit=1)
    [school] = result["schools"]
    assert school["unread_chats"] == 1
    [post] = school["posts"]
    assert post["published_days_ago"] == 7
    assert post["text"].startswith("Liebe Eltern,")
    assert [item["child"] for item in result["classes"]] == ["Anna", "Ben"]

    only_school = await _call(hass, "get_school_news", scope="school")
    assert "classes" not in only_school


@pytest.mark.usefixtures("loaded")
async def test_unknown_child_and_bad_date(hass: HomeAssistant) -> None:
    with pytest.raises(HomeAssistantError, match="Known children: Anna, Ben"):
        await _call(hass, "get_homework", child="Carla")
    with pytest.raises(HomeAssistantError, match="tomorrow is 2026-09-22"):
        await _call(hass, "get_lunch", date="next week")


@pytest.mark.usefixtures("loaded")
async def test_full_name_selects_child(hass: HomeAssistant) -> None:
    result = await _call(hass, "get_timetable", child="Anna Maria")
    assert result["children"][0]["child"] == "Anna"


@pytest.mark.usefixtures("loaded")
async def test_not_offered_to_mcp_clients(hass: HomeAssistant) -> None:
    """MCP clients get no tools and no prompt, even with "all LLM APIs" on."""
    mcp_context = llm.LLMContext(
        platform="mcp_server",
        context=Context(),
        language="*",
        assistant="conversation",
        device_id=None,
    )
    api = await llm.async_get_api(hass, DOMAIN, mcp_context)
    assert api.tools == []
    assert api.api_prompt == ""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("morgen", "2026-09-22"),
        ("Tomorrow", "2026-09-22"),
        ("heute", "2026-09-21"),
        ("übermorgen", "2026-09-23"),
        ("Freitag", "2026-09-25"),
        ("monday", "2026-09-21"),
        ("2026-09-24", "2026-09-24"),
    ],
)
@pytest.mark.usefixtures("loaded")
async def test_date_words(hass: HomeAssistant, value: str, expected: str) -> None:
    """Small models often pass "morgen" or a weekday instead of a date."""
    result = await _call(hass, "get_lunch", date=value)
    assert result["children"][0]["days"][0]["date"] == expected
