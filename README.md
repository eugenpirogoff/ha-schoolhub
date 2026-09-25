# SchoolHub for Home Assistant

A Home Assistant integration for German school services:

- **SWOP** school portal (`https://<school>.swop.schule`): timetable, homework,
  class news and school news
- **Drei Köche** school catering: ordered lunches

The integration is set up entirely in the Home Assistant UI. For the example
dashboard you copy one file into the dashboard editor and fill in three names.

## What you get

For every child:

| Entity | Example | Content |
|---|---|---|
| Calendar | `calendar.kind_timetable` | Lessons with teacher, room, topic and homework. Cancelled lessons and substitutions are marked. The attribute `lessons` lists all fetched lessons and `week_start` the Monday of the current school week (from Saturday: the coming week), e.g. for a timetable grid in a markdown card. |
| To-do list | `todo.kind_homework` | Homework until its due date: the subject as title, the task and when it was given as description, the due date as the item's due date. You can tick items off in Home Assistant; SWOP is never changed. |
| Select | `select.kind_timetable_week` | Which week a timetable view shows, as weeks from the current one (`-2` … `0` … `4`, following the fetched weeks). Use `select.select_previous` / `select_next` as arrows. Goes back to `0` after 15 minutes and at midnight. |
| Select | `select.kind_homework_day` | Weekday (Monday to Friday) for the list below. Starts on today and goes back to today 15 minutes after a pick and at midnight. |
| To-do list | `todo.kind_homework_of_the_day` | The homework of the picked day: due or given that day, and on today also everything still open. Ticking works like in the full list. |
| Sensor | `sensor.kind_next_lesson` | The lesson in progress or the next one, with start, end, room and today's lessons |
| Sensor | `sensor.kind_open_homework` | Number of open homework items, with the items as attribute |
| Sensor | `sensor.kind_class_news` | Latest class news post, with the last 10 posts (title, author, date, text) as attribute `posts` |
| Calendar | `calendar.kind_lunch` | Ordered lunches (Drei Köche). The attribute `meals` lists all fetched meals. |
| Sensor | `sensor.kind_lunch_today` | Today's lunch, with allergens and the next days |

For the school:

- `sensor.<school>_school_news`: the latest school-wide post.
- `binary_sensor.<school>_new_messages`: on while a SWOP messenger chat has unread
  messages, with the number of such chats and a link to the messenger. SchoolHub
  only compares message ids; it never opens a chat, so nothing is marked as read,
  and message texts are not stored. Only created if the school uses the messenger.

Entity IDs follow the language Home Assistant has when SchoolHub is added. The
examples above are English; in German they are e.g. `calendar.kind_stundenplan`,
`todo.kind_hausaufgaben`, `calendar.kind_essen` and
`sensor.<school>_schulnachrichten`.

For bug reports, download diagnostics from the integration's ⋮ menu. They contain
counts and status only, no logins or names.

Entity names follow your Home Assistant language (German or English).

### Event for new news posts

For every new class or school news post, SchoolHub fires the event
`schoolhub_new_post`:

```yaml
event_type: schoolhub_new_post
data:
  config_entry_id: "01J…"  # the SchoolHub entry that saw the post
  scope: class            # or "school"
  students: ["Kind"]      # children in that class; empty for school news
  class: "3a"             # null for school news
  post_id: 12345
  title: "Wandertag"
  text: "Liebe Eltern, …"
  author: "Lehrkraft A."
  published: "2026-09-22T06:20:33+00:00"
```

Posts that already exist when you add the integration do not fire events. The
recorder stores these events, see [Privacy](#privacy) to exclude them.

Example automation:

```yaml
automation:
  - alias: New school news
    triggers:
      - trigger: event
        event_type: schoolhub_new_post
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.title }}"
          message: "{{ trigger.event.data.text[:200] }}"
```

## Installation

Requires Home Assistant 2026.9 or newer.

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eugenpirogoff&repository=ha-schoolhub&category=integration)

1. Click the button above, or in HACS → ⋮ → **Custom repositories** add this
   repository's URL with type **Integration**.
2. Install **SchoolHub** and restart Home Assistant.

### Manual

Copy `custom_components/schoolhub` into your Home Assistant `config/custom_components/`
folder and restart Home Assistant.

## Setup

[![Open your Home Assistant instance and start setting up SchoolHub.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=schoolhub)

**Settings → Devices & services → Add integration → SchoolHub.** Add SchoolHub once
for every login:

- **SWOP:** pick your school from the list or enter the address of its SWOP portal
  (for example `https://my-school.swop.schule`; just `my-school` works too). Nothing
  is preselected, so check that the school is yours. Then use the same login as on
  the website. A parent or family login shows all its children.

  Known schools are listed as "City - School" in `KNOWN_SCHOOLS` in
  [`const.py`](custom_components/schoolhub/const.py). Pull requests that add more are
  welcome.
- **Drei Köche:** one login per child. Add SchoolHub again and choose Drei Köche for
  each further child.

**Changing a login:** to change the address, login name or password yourself, use
⋮ → **Reconfigure** on the entry.

**New school year:** a new class appears by itself after the next refresh. If a child
leaves the school, its entities become unavailable; remove them under Settings →
Devices & services → SchoolHub → the child's device → ⋮ → **Delete**.

### How children are matched between SWOP and Drei Köche

Each Drei Köche login belongs to one child. SchoolHub finds that child among the
SWOP children by name, trying these steps in order:

1. **Same name**, ignoring upper/lower case, accents and spacing
   ("Zoë Sophie" = "zoe  sophie").
2. **First name only** on one side, with the same last name
   ("Jonas" = "Jonas Paul").
3. **Similar spelling** for typos ("Jonaz" ≈ "Jonas"), but only if exactly one
   SWOP child clearly fits best, so siblings are never mixed up. A typo swaps a
   letter; names with an added or dropped letter ("Paul" and "Paula") never match.

Both services then use the same entity names (`kind_…`), which is how dashboards
combine a child's timetable and lunch. SchoolHub checks this after every refresh
and reports problems under **Settings → Repairs**:

- **No SWOP child found:** shows both names, so you can correct the spelling at one
  of the services.
- **Matched despite different spelling:** a note that a typo was assumed.
- **Different entity names:** e.g. `calendar.jonas_lunch` next to
  `calendar.jonas_paul_timetable`. Select **Fix** to rename the Drei Köche
  entities to the SWOP names in one step.

**Options** (⋮ → Configure on the integration) set how many weeks before and after
the current week are fetched. The defaults are 2 weeks back and 4 ahead for both.

SWOP is refreshed every 30 minutes and Drei Köche every 3 hours. If a password
changes, Home Assistant asks you to log in again.

## Dashboard example

A ready-made dashboard view for one child, in two languages:

- [`examples/child_view.de.yaml`](examples/child_view.de.yaml): German texts and German
  entity IDs
- [`examples/child_view.en.yaml`](examples/child_view.en.yaml): English texts and
  English entity IDs

Pick the one in the language Home Assistant had when you added SchoolHub, since entity
IDs follow that language (e.g. `calendar.jonas_paul_stundenplan` or
`calendar.jonas_paul_timetable`). Add it once per child, whether you have one or several:

- **New messages:** an orange banner at the top while the SWOP messenger has unread
  messages, with a link to it.
- **Stundenplan:** a colorful timetable grid (period and time × Monday to Friday) with
  dates, today highlighted in light blue, and ◀ / ▶ arrows to look at past and coming weeks (e.g. the lunch plan);
  it returns to the current week by itself. Each subject has an emoji. Today is bold, cancelled lessons are
  struck through and substitutions are in italics. A 🍽️ row shows the ordered
  Drei Köche lunch in the lunch break.
- **Hausaufgaben:** buttons Mo–Fr to pick a day (today is preselected; only the picked day is filled),
  and one list with that day's homework to tick off. Each item shows the subject,
  the task, when it was given and its due date, each once.
- **Info:** all recent class news ("Klasse") and school news ("Schule"), side by side on screens
  from 1000 px wide, as a list you can scroll through. Each post opens with a tap; the newest one is open.

1. Create a dashboard (e.g. "Schule").
2. For each child, open a view in the dashboard editor (the existing empty one for the
   first child, "+" for each further child), choose ⋮ → **Edit in YAML** and paste the
   file.
3. Replace the placeholders with find & replace:
   - `KIND_ID`: the child's first name(s) as in SWOP, in lower case, spaces as `_`,
     without accents (e.g. `jonas_paul`)
   - `KIND_NAME`: the name for the tab and header (e.g. `Jonas`)
   - `SCHULE_ID`: the school's name as on its device, written the same way (e.g.
     `ludwigsfelde_regenbogen_grundschule`)

   To check them, search for "timetable" (German: "stundenplan") under Settings →
   Entities. The placeholders occur nowhere else in the file. Details are at the top
   of the file. It only uses built-in cards.

For larger section titles and buttons, also install the optional theme
[`examples/theme.yaml`](examples/theme.yaml): copy it to `<config>/themes/`, make sure
`configuration.yaml` has `frontend: themes: !include_dir_merge_named themes`, and run
the action `frontend.reload_themes`. The views already use it
(`theme: SchoolHub`); without it they show the default sizes. It only changes sizes,
so colors and dark mode stay as they are.

## Privacy

Logins are stored in Home Assistant like those of any other integration. SchoolHub
only talks to the two services and to nothing else. Ticked-off homework and seen
news post IDs are stored locally in `.storage/schoolhub.<entry>`.

Home Assistant's recorder stores events in its database, including
`schoolhub_new_post` with the children's names and the post text. To keep it out:

```yaml
recorder:
  exclude:
    event_types:
      - schoolhub_new_post
```

Automations still receive the event.

## Development

Requires Python 3.14 (like Home Assistant 2026.9).

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
script/check              # ruff, example sync and tests
script/check --hassfest   # also Home Assistant's hassfest validation
```

`script/hassfest` runs Home Assistant's own integration check without Docker: the
first run checks out Home Assistant core (the version installed in `.venv`) into
`.hassfest/`. The GitHub workflows run the same checks, plus the HACS validation, on
pull requests or when started by hand.

The tests run against anonymized recorded API responses in `tests/fixtures/`. They
need no network access and no logins.

The English example view is generated from the German one. After changing
`examples/child_view.de.yaml`, run `python script/generate_examples.py` (a test checks
that both are in sync).

## Credits

The icon is `mdi:school` from [Material Design Icons](https://pictogrammers.com/library/mdi/)
(Apache License 2.0).

## License

Add the applicable license information here.
