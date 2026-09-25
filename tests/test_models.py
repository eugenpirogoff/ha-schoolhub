"""Parsers against recorded (anonymized) API responses."""

from __future__ import annotations

from datetime import date, time, timedelta

import pytest

from custom_components.schoolhub.api import normalize_url, school_name
from custom_components.schoolhub.entity import school_label
from custom_components.schoolhub.models import (
    SwopLookup,
    count_unread_chats,
    find_news_module,
    html_to_text,
    name_key,
    parse_drei_koeche_student,
    parse_homework,
    parse_lessons,
    parse_meals,
    parse_menu,
    parse_news,
    parse_students,
    subject_icon,
)

from .conftest import load_fixture


def test_students_of_family_login() -> None:
    students = parse_students(load_fixture("swop_mydata"))
    assert [(s.first_name, s.class_name) for s in students] == [("Anna Maria", "3a"), ("Ben", "2c")]
    assert students[0].klassenzug_id == 11
    assert students[0].class_page_id == 301


def test_student_login_is_the_child_itself() -> None:
    child = load_fixture("swop_mydata")["erziehungsberechtigt_fuer"][1]
    assert [s.first_name for s in parse_students(child)] == ["Ben"]


def test_children_match_across_services() -> None:
    swop = parse_students(load_fixture("swop_mydata"))[0]
    drei_koeche = parse_drei_koeche_student(load_fixture("dk_login_child1")["user"])
    assert swop.key == drei_koeche.key == "anna maria muster"
    assert name_key("Zoë  Marie", "MUSTER") == "zoe marie muster"


def test_lessons() -> None:
    lookup = SwopLookup(load_fixture("swop_basedata"))
    lessons = parse_lessons(
        load_fixture("swop_timetable_child1"), load_fixture("swop_homework_child1"), lookup
    )
    assert len(lessons) == 60
    assert lessons == sorted(lessons, key=lambda lesson: (lesson.day, lesson.number))

    first, second = lessons[0], lessons[1]
    # "2000-01-01T08:00:00Z" in the base data is local wall-clock time.
    assert (first.day, first.number, first.start, first.end) == (
        date(2026, 9, 14),
        0,
        time(8, 0),
        time(8, 10),
    )
    assert first.cancelled and not first.substituted
    assert second.substituted and second.comment == "Vertretung"
    assert second.teacher and second.room == "Raum 101"


def test_lesson_joins_all_records_of_a_lesson() -> None:
    lookup = SwopLookup(load_fixture("swop_basedata"))
    lessons = parse_lessons(
        load_fixture("swop_timetable_child1"), load_fixture("swop_homework_child1"), lookup
    )
    second = lessons[1]
    assert second.homework is not None
    assert len(second.homework.splitlines()) > 1


def test_homework() -> None:
    lookup = SwopLookup(load_fixture("swop_basedata"))
    homework = parse_homework(load_fixture("swop_homework_child1"), lookup)
    assert len(homework) == 15
    assert homework == sorted(homework, key=lambda item: (item.due or item.assigned, item.subject))
    assert len({item.uid for item in homework}) == len(homework)
    first = homework[0]
    assert first.assigned == date(2026, 9, 14)
    assert first.due == date(2026, 9, 15)
    assert first.subject == "Deutsch"


def test_deleted_and_duplicate_records_are_ignored() -> None:
    lookup = SwopLookup(load_fixture("swop_basedata"))
    records = load_fixture("swop_homework_child1")
    with_homework = [r for r in records if r["unterrichtsdokumentation"]["hausaufgaben"]]
    deleted = {"unterrichtsdokumentation": {**with_homework[0]["unterrichtsdokumentation"]}}
    deleted["unterrichtsdokumentation"]["geloescht"] = True
    duplicate = with_homework[1]
    items = parse_homework([deleted, duplicate, duplicate], lookup)
    assert [item.uid for item in items] == [
        f"swop-homework-{duplicate['unterrichtsdokumentation']['id']}"
    ]


def test_news() -> None:
    assert find_news_module(load_fixture("swop_info_class")) == 20
    assert find_news_module({"modulelist": {"mitte": None}}) is None
    posts = parse_news(load_fixture("swop_news_class_child1"))
    assert [post.post_id for post in posts] == [4000, 4001, 4002]
    assert posts[0].text == "Liebe Eltern,\nKlassennachricht 3a Text 1."
    assert posts[0].published is not None and posts[0].published.tzinfo is not None
    assert parse_news(load_fixture("swop_news_empty")) == []


def test_meals() -> None:
    meals = parse_meals(load_fixture("dk_preorderhistory_child1"))
    assert len(meals) == 8
    assert meals[0].day == date(2026, 9, 21)
    assert "<" not in meals[0].menu and "1x" not in meals[0].menu


def test_menu_cleanup() -> None:
    name, allergens = parse_menu(
        "<b>1x</b>&nbsp;&nbsp;Veg. Pasta <sup><b>a,g,i,V</b></sup>"
        " Nudeln<sup><b>a,VB</b></sup>[BIO]"
    )
    assert name == "Veg. Pasta Nudeln [BIO]"
    assert allergens == "a,g,i,V,VB"


def test_subject_icons() -> None:
    assert subject_icon("Deutsch") == "📖"
    assert subject_icon("Ma 3b") == "🔢"
    assert subject_icon("Musik") == "🎵"
    assert subject_icon("Musisch-ästhetische Erziehung") == "🎭"
    assert subject_icon("Fö 3a") == "🧩"
    assert subject_icon("Chinesisch") == "📝"


def test_html_to_text() -> None:
    assert html_to_text("<p>A&nbsp;&amp; B<br>C</p><p></p>") == "A & B\nC"
    assert html_to_text(None) == ""


def test_html_to_text_strips_encoded_tags() -> None:
    assert html_to_text("A&lt;img src=x onerror=alert(1)&gt;B") == "AB"
    assert html_to_text("&lt;script&gt;x&lt;/script&gt;") == "x"
    assert html_to_text("&lt;b") == ""
    assert html_to_text("<p>a &lt; b &amp;&amp; c > d</p>") == "a < b && c > d"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (
            "https://regenbogen-grundschule.swop.schule",
            "https://regenbogen-grundschule.swop.schule",
        ),
        (
            " HTTPS://Regenbogen-Grundschule.swop.schule/login?next=/ ",
            "https://regenbogen-grundschule.swop.schule",
        ),
        ("regenbogen-grundschule.swop.schule/", "https://regenbogen-grundschule.swop.schule"),
        ("regenbogen-grundschule", "https://regenbogen-grundschule.swop.schule"),
        ("http://swop.example.de:8080/x", "http://swop.example.de:8080"),
    ],
)
def test_normalize_url(value: str, expected: str) -> None:
    assert normalize_url(value) == expected


@pytest.mark.parametrize("value", ["", "https://", "ftp://a.b", "my school", "a..b", "-x.de"])
def test_normalize_url_rejects(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_url(value)


def test_school_names() -> None:
    assert school_name("https://regenbogen-grundschule.swop.schule") == "Regenbogen Grundschule"
    assert school_label("https://regenbogen-grundschule.swop.schule") == (
        "Ludwigsfelde - Regenbogen Grundschule"
    )
    assert school_label("https://musterschule.swop.schule") == "Musterschule"


def test_count_unread_chats() -> None:
    chats = load_fixture("swop_messenger_chats")["chats"]
    settings = load_fixture("swop_messenger_settings")["chat_einstellungen"]
    latest = load_fixture("swop_messenger_latest")["latest_message_ids"]
    # chat-a read, chat-b unread, chat-c closed, chat-d ignored.
    assert count_unread_chats(chats, settings, latest) == 1
    assert count_unread_chats(chats, [], latest) == 2
    assert count_unread_chats(chats, settings, []) == 0


def test_timestamps_without_offset_are_utc() -> None:
    """Post dates always carry a time zone, so they compare and display correctly."""
    news = parse_news(
        {"news_posts": [{"news_post_id": 1, "header": "A", "created_at": "2026-09-22T06:20:33"}]}
    )
    assert news[0].published is not None
    assert news[0].published.utcoffset() == timedelta(0)
