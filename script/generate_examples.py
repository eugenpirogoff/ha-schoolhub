"""Generate the English example view from the German one.

examples/child_view.de.yaml is the source. The English file differs only in
entity IDs (Home Assistant names them in the language it had when SchoolHub
was added) and in the texts shown. Run after changing the German file:

    python script/generate_examples.py          # write child_view.en.yaml
    python script/generate_examples.py --check  # fail if it is out of date
"""

from __future__ import annotations

from pathlib import Path
import sys

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
SOURCE = EXAMPLES / "child_view.de.yaml"
TARGET = EXAMPLES / "child_view.en.yaml"

# Replaced in this order: longer strings before the shorter ones they contain.
REPLACEMENTS: list[tuple[str, str]] = [
    # Header comment.
    (
        "# Language: this file uses German entity IDs and German texts. Entity IDs\n"
        "# follow the language Home Assistant had when SchoolHub was added. With\n"
        "# English, use child_view.en.yaml instead (generated from this file by\n"
        "# script/generate_examples.py).\n",
        "# Language: this file uses English entity IDs and English texts. Entity IDs\n"
        "# follow the language Home Assistant had when SchoolHub was added. With\n"
        "# German, use child_view.de.yaml instead. This file is generated from it\n"
        "# by script/generate_examples.py; change the German file, not this one.\n",
    ),
    ('title "Schule"', 'title "School"'),
    ('search for "stundenplan" and "schulnachrichten"', 'search for "timetable" and "school_news"'),
    # Entity IDs.
    ("_stundenplan_woche", "_timetable_week"),
    ("_stundenplan", "_timetable"),
    ("_essen", "_lunch"),
    ("_offene_hausaufgaben", "_open_homework"),
    ("_hausaufgaben_des_tages", "_homework_of_the_day"),
    ("_hausaufgaben_tag", "_homework_day"),
    ("_klassennachrichten", "_class_news"),
    ("_schulnachrichten", "_school_news"),
    ("_neue_nachrichten", "_new_messages"),
    # Messenger banner.
    ('title="Neue Nachrichten in SWOP"', 'title="New messages in SWOP"'),
    ("'Es gibt ungelesene Nachrichten.'", "'There are unread messages.'"),
    ("' Chat hat'", "' chat has'"),
    ("' Chats haben'", "' chats have'"),
    ("' ungelesene Nachrichten.'", "' unread messages.'"),
    (">Im Messenger öffnen<", ">Open in the messenger<"),
    # Header.
    ("<small>· Klasse ", "<small>· Class "),
    # Timetable.
    ("heading: Stundenplan", "heading: Timetable"),
    ("heading: Woche", "heading: Week"),
    ("text: Diese Woche", "text: This week"),
    ('"Diese Woche" leads back', '"This week" leads back'),
    ("'übernächste Woche'", "'in 2 weeks'"),
    ("'letzte Woche'", "'last week'"),
    ("'diese Woche'", "'this week'"),
    ("'nächste Woche'", "'next week'"),
    ("' Wochen'", "' weeks'"),
    ("'vor ' ~ -weeks ~ ' weeks'", "-weeks ~ ' weeks ago'"),
    ("<b>KW {{", "<b>Week {{"),
    ("keine frühere Woche geladen", "no earlier week loaded"),
    ("keine spätere Woche geladen", "no later week loaded"),
    ("['Mo', 'Di', 'Mi', 'Do', 'Fr']", "['Mon', 'Tue', 'Wed', 'Thu', 'Fri']"),
    ("strftime('%d.%m.')", "strftime('%m/%d')"),
    ("'Mittag'", "'Lunch'"),
    ("<b>Kein Essen</b> = nichts bestellt", "<b>No lunch</b> = nothing ordered"),
    ("<b>Kein Essen</b>", "<b>No lunch</b>"),
    ('"Kein Essen" box', '"No lunch" box'),
    ("Der Stundenplan ist gerade nicht abrufbar.", "The timetable can't be loaded right now."),
    (
        "Für diese Woche gibt es keinen Stundenplan und kein Essen.",
        "There is no timetable and no lunch for this week.",
    ),
    ("🕘 Zeit", "🕘 Time"),
    ("<i>frei</i>", "<i>day off</i>"),
    (
        "📍 heute · ❌ entfällt · 🔄 Vertretung · 🍽️ Mittagessen",
        "📍 today · ❌ cancelled · 🔄 substitution · 🍽️ lunch",
    ),
    # Homework.
    ("heading: Hausaufgaben", "heading: Homework"),
    ('# "2 · offen"', '# "2 · open"'),
    ("name: offen", "name: open"),
    ("heading: Tag", "heading: Day"),
    ("# Hausaufgaben: buttons", "# Homework: buttons"),
    ("text: Mo\n", "text: Mon\n"),
    ("text: Di\n", "text: Tue\n"),
    ("text: Mi\n", "text: Wed\n"),
    ("text: Do\n", "text: Thu\n"),
    ("text: Fr\n", "text: Fri\n"),
    # News.
    ("heading: Klasse", "heading: Class"),
    ("heading: Schule", "heading: School"),
    ("Klasse and Schule", "Class and School"),
    ("Keine Nachrichten.", "No news."),
]


def generate() -> str:
    """Return the English file for the current German one."""
    text = SOURCE.read_text(encoding="utf-8")
    for german, english in REPLACEMENTS:
        if german not in text:
            raise ValueError(f"not found in {SOURCE.name}: {german!r}")
        text = text.replace(german, english)
    return text


def main() -> int:
    """Write the English file, or with --check only compare it."""
    text = generate()
    if "--check" in sys.argv[1:]:
        if TARGET.read_text(encoding="utf-8") != text:
            print(f"{TARGET.name} is out of date: run script/generate_examples.py")
            return 1
        return 0
    TARGET.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
