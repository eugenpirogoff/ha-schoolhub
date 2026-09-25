"""Checks that Drei Köche children match SWOP children, and the rename repair."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.schoolhub.checks import (
    ISSUE_NOT_FOUND,
    ISSUE_PREFIX,
    ISSUE_SIMILAR,
    issue_id,
)
from custom_components.schoolhub.const import DOMAIN
from custom_components.schoolhub.models import (
    MatchKind,
    Student,
    match_student,
)
from homeassistant.config_entries import ConfigEntryDisabler, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component

from .conftest import (
    JSON,
    SWOP_URL,
    load_fixture,
    mock_drei_koeche,
    mock_swop,
    setup_integration,
)

SWOP_CHILDREN = [Student("Jonas Paul", "Muster"), Student("Zoë Sophie", "Muster")]


def test_match_same_name_ignoring_case_and_accents() -> None:
    match = match_student(Student("zoe  sophie", "MUSTER"), SWOP_CHILDREN)
    assert match and match.student.first_name == "Zoë Sophie" and match.how == MatchKind.EXACT


def test_match_first_name_only() -> None:
    match = match_student(Student("Jonas", "Muster"), SWOP_CHILDREN)
    assert match and match.student.first_name == "Jonas Paul" and match.how == MatchKind.FIRST_NAME


def test_match_typo() -> None:
    match = match_student(Student("Jonaz Paul", "Muster"), SWOP_CHILDREN)
    assert match and match.student.first_name == "Jonas Paul" and match.how == MatchKind.SIMILAR
    assert 0.8 <= match.similarity < 1


def test_no_match_for_other_child_or_ambiguous_siblings() -> None:
    assert match_student(Student("Max", "Mustermann"), SWOP_CHILDREN) is None
    siblings = [Student("Lina", "Muster"), Student("Leni", "Muster")]
    assert match_student(Student("Lena", "Muster"), siblings) is None
    # A sibling not in SWOP is not mistaken for one who is.
    assert match_student(Student("Mia", "Muster"), [Student("Max", "Muster")]) is None
    assert match_student(Student("Ella", "Muster"), [Student("Emma", "Muster")]) is None
    assert match_student(Student("Maria", "Muster"), [Student("Anna Maria", "Muster")]) is None


@pytest.mark.parametrize(("name", "sibling"), [("Paul", "Paula"), ("Mia", "Mila"), ("Jan", "Jana")])
def test_no_typo_match_for_added_or_dropped_letter(name: str, sibling: str) -> None:
    assert match_student(Student(name, "Muster"), [Student(sibling, "Muster")]) is None
    assert match_student(Student(sibling, "Muster"), [Student(name, "Muster")]) is None


def test_typo_match_for_single_swop_child() -> None:
    match = match_student(Student("Jonaz Paul", "Muster"), [Student("Jonas Paul", "Muster")])
    assert match and match.how == MatchKind.SIMILAR


def test_same_child_in_two_swop_logins() -> None:
    both_parents = [*SWOP_CHILDREN, Student("Jonas Paul", "Muster")]
    match = match_student(Student("Jonas", "Muster"), both_parents)
    assert match and match.how == MatchKind.FIRST_NAME


async def _setup_both(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    **user: Any,
) -> None:
    mock_swop(aioclient_mock)
    mock_drei_koeche(aioclient_mock, **user)
    await setup_integration(hass, swop_entry)


def _issue(hass: HomeAssistant, issue: str, entry: MockConfigEntry) -> ir.IssueEntry | None:
    return ir.async_get(hass).async_get_issue(DOMAIN, issue_id(issue, entry.entry_id))


async def test_matching_names_raise_no_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry)
    assert not [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN]


async def test_unknown_child_is_reported(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Max", lastname="Mustermann")
    issue = _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry)
    assert issue and not issue.is_fixable
    assert issue.translation_placeholders["name"] == "Max Mustermann"
    assert "Anna Maria Muster" in issue.translation_placeholders["swop_names"]


async def test_typo_is_reported_and_prefix_can_be_fixed(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    # "Anna Marja" at Drei Köche, "Anna Maria" at SWOP.
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Anna Marja")
    assert _issue(hass, ISSUE_SIMILAR, drei_koeche_entry)
    prefix = _issue(hass, ISSUE_PREFIX, drei_koeche_entry)
    assert prefix and prefix.is_fixable
    assert prefix.data == {
        "entry_id": drei_koeche_entry.entry_id,
        "old_prefix": "anna_marja",
        "new_prefix": "anna_maria",
        "name": "Anna Marja Muster",
        "swop_name": "Anna Maria Muster",
    }
    assert hass.states.get("calendar.anna_marja_lunch")

    # Fix it the way the UI does, through the repairs API.
    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": prefix.issue_id}
    )
    result = await response.json()
    assert result["step_id"] == "confirm"
    placeholders = result["description_placeholders"]
    assert "`calendar.anna_marja_lunch` → `calendar.anna_maria_lunch`" in placeholders["renames"]
    # The user sees both names before confirming it is the same child.
    assert (placeholders["name"], placeholders["swop_name"]) == (
        "Anna Marja Muster",
        "Anna Maria Muster",
    )
    response = await client.post(f"/api/repairs/issues/fix/{result['flow_id']}", json={})
    assert (await response.json())["type"] == "create_entry"
    await hass.async_block_till_done()

    assert hass.states.get("calendar.anna_maria_lunch")
    assert hass.states.get("sensor.anna_maria_lunch_today")
    assert _issue(hass, ISSUE_PREFIX, drei_koeche_entry) is None
    # The spelling difference itself stays reported.
    assert _issue(hass, ISSUE_SIMILAR, drei_koeche_entry)


async def test_lunch_only_raises_no_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    mock_drei_koeche(aioclient_mock, firstname="Max", lastname="Mustermann")
    assert await hass.config_entries.async_setup(drei_koeche_entry.entry_id)
    await hass.async_block_till_done()
    assert not [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN]


async def test_issues_are_removed_with_the_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Max", lastname="Mustermann")
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry)
    # An unloaded lunch login is not checked, so its issue goes; a reload
    # raises it again on the next check.
    assert await hass.config_entries.async_unload(drei_koeche_entry.entry_id)
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None
    assert await hass.config_entries.async_setup(drei_koeche_entry.entry_id)
    await hass.async_block_till_done()
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry)
    assert await hass.config_entries.async_remove(drei_koeche_entry.entry_id)
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None


async def test_issues_are_removed_when_the_lunch_login_is_disabled(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Max", lastname="Mustermann")
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry)
    assert await hass.config_entries.async_set_disabled_by(
        drei_koeche_entry.entry_id, ConfigEntryDisabler.USER
    )
    await hass.async_block_till_done()
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None


async def test_issues_are_removed_with_the_last_swop_login(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Max", lastname="Mustermann")
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry)
    assert await hass.config_entries.async_remove(swop_entry.entry_id)
    await hass.async_block_till_done()
    # Without SWOP there is nothing to compare with: SchoolHub is used for lunch only.
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None
    assert drei_koeche_entry.state is ConfigEntryState.LOADED


async def test_failing_swop_login_raises_no_issue(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    # SWOP rejects the login, so its children are unknown for now.
    aioclient_mock.post(
        f"{SWOP_URL}/session/login/post.json", json={"success": False}, headers=JSON
    )
    mock_drei_koeche(aioclient_mock, firstname="Max", lastname="Mustermann")
    await hass.config_entries.async_setup(swop_entry.entry_id)
    await hass.async_block_till_done()
    assert drei_koeche_entry.state is ConfigEntryState.LOADED
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None


async def test_swop_login_without_children_does_not_block_the_check(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    # An issue from an earlier check must not stay frozen once SWOP has loaded.
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id(ISSUE_NOT_FOUND, drei_koeche_entry.entry_id),
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_NOT_FOUND,
    )
    mydata = load_fixture("swop_mydata")
    mock_swop(aioclient_mock, {"swop_mydata": {**mydata, "erziehungsberechtigt_fuer": []}})
    mock_drei_koeche(aioclient_mock, firstname="Max", lastname="Mustermann")
    await setup_integration(hass, swop_entry)
    assert swop_entry.state is ConfigEntryState.LOADED
    assert not swop_entry.runtime_data.data.students
    # No SWOP children to compare with: treated like lunch only.
    assert _issue(hass, ISSUE_NOT_FOUND, drei_koeche_entry) is None


async def test_custom_entity_id_is_left_alone(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Anna")
    assert _issue(hass, ISSUE_PREFIX, drei_koeche_entry)
    # The user gave the lunch calendar an ID of their own.
    er.async_get(hass).async_update_entity(
        "calendar.anna_lunch", new_entity_id="calendar.mittag_anna"
    )
    await drei_koeche_entry.runtime_data.async_refresh()
    assert _issue(hass, ISSUE_PREFIX, drei_koeche_entry) is None


async def test_fix_is_refused_when_the_new_entity_id_is_taken(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Anna")
    prefix = _issue(hass, ISSUE_PREFIX, drei_koeche_entry)
    assert prefix
    er.async_get(hass).async_get_or_create(
        "calendar", "demo", "taken", suggested_object_id="anna_maria_lunch"
    )
    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": prefix.issue_id}
    )
    flow_id = (await response.json())["flow_id"]
    response = await client.post(f"/api/repairs/issues/fix/{flow_id}", json={})
    result = await response.json()
    assert (result["type"], result["reason"]) == ("abort", "entity_id_taken")
    assert hass.states.get("calendar.anna_lunch")


async def test_fix_is_refused_when_a_state_only_entity_holds_the_new_id(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    swop_entry: MockConfigEntry,
    drei_koeche_entry: MockConfigEntry,
) -> None:
    await _setup_both(hass, aioclient_mock, swop_entry, firstname="Anna")
    prefix = _issue(hass, ISSUE_PREFIX, drei_koeche_entry)
    assert prefix
    # An entity without a unique ID only exists in the state machine.
    hass.states.async_set("sensor.anna_maria_lunch_today", "taken")
    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    response = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": prefix.issue_id}
    )
    flow_id = (await response.json())["flow_id"]
    response = await client.post(f"/api/repairs/issues/fix/{flow_id}", json={})
    result = await response.json()
    assert (result["type"], result["reason"]) == ("abort", "entity_id_taken")
    # Nothing was renamed, not even the entities whose new ID was free.
    assert hass.states.get("calendar.anna_lunch")
    assert hass.states.get("sensor.anna_lunch_today")
    assert not hass.states.get("calendar.anna_maria_lunch")
