from pathlib import Path
import sys
import json
import datetime
import subprocess

# ============================================================
# SchoolHub Basis-Pfad für Imports
# ============================================================

FETCH_DIR = Path(__file__).resolve().parent
BASE_DIR = FETCH_DIR.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# SchoolHub Imports
# ============================================================

from auth.auth_swop import get_auth

from core.cache import get_cache
from core.cache import save_cache

from core.paths import OUTPUT_DIR

from core.date_ranges import (
    get_fetch_range
)

# ============================================================
# SWOP API
# ============================================================

BASE_URL = (
    "https://regenbogen-grundschule.swop.schule"
)

OUTPUT_FILE = (
    OUTPUT_DIR /
    "timetable.json"
)

# ============================================================
# UDOK Cache
# ============================================================

UDOK_CACHE_NAME = (
    "swop_udoks_clean"
)

# ============================================================
# Lookups
# ============================================================

def get_teacher_name(
    basedata,
    ecuser_id
):

    return (
        basedata
        .get(
            "lehrernamen_by_id",
            {}
        )
        .get(
            str(ecuser_id)
        )
    )


def get_subject_name(
    basedata,
    schulfach_id
):

    subjects = (
        basedata
        .get(
            "schulfaecher_by_id",
            {}
        )
    )

    subject = subjects.get(
        str(schulfach_id),
        {}
    )

    if isinstance(subject, dict):

        return (
            subject.get("bezeichnung")
            or subject.get("name")
            or str(schulfach_id)
        )

    return str(subject)


def get_room_name(
    basedata,
    raum_id
):

    rooms = (
        basedata
        .get(
            "raeume_by_id",
            {}
        )
    )

    room = rooms.get(
        str(raum_id),
        {}
    )

    if isinstance(room, dict):

        return (
            room.get("bezeichnung")
            or room.get("name")
            or str(raum_id)
        )

    return str(room)


def get_lesson_data(
    basedata,
    tagesabschnitt_id
):

    lesson = (
        basedata
        .get(
            "tagesabschnitte_by_id",
            {}
        )
        .get(
            str(tagesabschnitt_id),
            {}
        )
        .get(
            "tagesabschnitt",
            {}
        )
    )

    return {

        "lesson_number":
            lesson.get(
                "stunde_nr"
            ),

        "lesson_name":
            lesson.get(
                "bezeichnung"
            ),

        "start":
            lesson.get(
                "beginn",
                ""
            )[11:16],

        "end":
            lesson.get(
                "ende",
                ""
            )[11:16]
    }


# ============================================================
# UDOK Lookup
# ============================================================

def build_udok_lookup(
    udok_cache
):

    lookup = {}

    for child_name, child_data in (
        udok_cache
        .get(
            "children",
            {}
        )
        .items()
    ):

        lookup[
            child_name
        ] = {}

        for entry in (
            child_data.get(
                "udoks",
                []
            )
        ):

            udok = (
                entry.get(
                    "unterrichtsdokumentation",
                    {}
                )
            )

            date = udok.get(
                "datum"
            )

            lesson_number = udok.get(
                "unterrichtsstunde"
            )

            if date is None:
                continue

            if lesson_number is None:
                continue

            lookup[
                child_name
            ][
                (
                    date,
                    lesson_number
                )
            ] = udok

    return lookup


# ============================================================
# Homework
# ============================================================

def build_homework(
    udok,
    basedata
):

    homework_text = (
        udok.get(
            "hausaufgaben",
            ""
        )
        .strip()
    )

    if not homework_text:

        return None

    return {

        "assigned_date":
            udok.get(
                "datum"
            ),

        "due_date":
            udok.get(
                "hausaufgaben_zu_erledigen_bis"
            ),

        "lesson_number":
            udok.get(
                "unterrichtsstunde"
            ),

        "subject":
            get_subject_name(
                basedata,
                udok.get(
                    "schulfach_id"
                )
            ),

        "teacher":
            get_teacher_name(
                basedata,
                udok.get(
                    "ecuser_id"
                )
            ),

        "text":
            homework_text
    }


# ============================================================
# Documentation
# ============================================================

def build_documentation(
    udok,
    basedata
):

    documentation_text = (
        udok.get(
            "bemerkung",
            ""
        )
        .strip()
    )

    if not documentation_text:

        return None

    return {

        "text":
            documentation_text,

        "updated_at":
            udok.get(
                "updated_at"
            ),

        "documented_by":
            get_teacher_name(
                basedata,
                udok.get(
                    "unterrichtet_von_ecuser_id"
                )
            )
    }


# ============================================================
# Fetch
# ============================================================

def fetch_swop_timetable():

    # ========================================================
    # SWOP verwendet einen gemeinsamen Familien-Account.
    #
    # child1 dient hier ausschließlich als Einstiegspunkt,
    # damit der gemeinsame SWOP-Account authentifiziert wird.
    #
    # Die tatsächlich vorhandenen Kinder werden anschließend
    # vollständig aus "erziehungsberechtigt_fuer" übernommen.
    # ========================================================

    opener = get_auth(
        "child1"
    )

    # ========================================================
    # SWOP Metadata
    # ========================================================

    child1_config = get_cache(
        "swop_family"
    )

    mydata = (
        child1_config
        .get(
            "mydata",
            {}
        )
    )

    basedata = ensure_swop_basedata()

    children = (
        mydata
        .get(
            "erziehungsberechtigt_fuer",
            []
        )
    )

    if not children:

        raise RuntimeError(
            "No children found in SWOP metadata"
        )

    # ========================================================
    # UDOK Source of Truth
    # ========================================================

    udok_cache = ensure_udok_cache()

    udok_lookup = build_udok_lookup(
        udok_cache
    )

    # ========================================================
    # Fetch range
    # ========================================================

    start_date, end_date = (
        get_fetch_range(
            "swop"
        )
    )

    # ========================================================
    # Fetch timetable for all children
    # ========================================================

    all_children = {}

    for index, child_data in enumerate(
        children
    ):

        child_name = (
            f"child{index + 1}"
        )

        klassenzug_id = (
            child_data.get(
                "klassenzug_id"
            )
        )

        if not klassenzug_id:

            raise RuntimeError(
                f"No klassenzug_id found "
                f"for {child_name}"
            )

        url = (
            f"{BASE_URL}"
            f"/swop_zeitabschnitte"
            f"/{start_date}"
            f"/{end_date}.json"
            f"?klassenzug_id={klassenzug_id}"
        )

        with opener.open(
            url,
            timeout=30
        ) as response:

            timetable = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        child_timetable = {}

        child_udoks = (
            udok_lookup.get(
                child_name,
                {}
            )
        )

        documentation_count = 0
        homework_count = 0

        for entry in timetable:

            date = (
                entry.get(
                    "erster_tag"
                )
            )

            if not date:
                continue

            lesson_info = (
                get_lesson_data(
                    basedata,
                    entry.get(
                        "tagesabschnitt_id"
                    )
                )
            )

            lesson_number = (
                lesson_info[
                    "lesson_number"
                ]
            )

            lesson = {

                "lesson_number":
                    lesson_number,

                "lesson_name":
                    lesson_info[
                        "lesson_name"
                    ],

                "start":
                    lesson_info[
                        "start"
                    ],

                "end":
                    lesson_info[
                        "end"
                    ],

                "subject":
                    get_subject_name(
                        basedata,
                        entry.get(
                            "schulfach_id"
                        )
                    ),

                "teacher":
                    get_teacher_name(
                        basedata,
                        entry.get(
                            "ecuser_id"
                        )
                    ),

                "room":
                    get_room_name(
                        basedata,
                        entry.get(
                            "raum_id"
                        )
                    ),

                "cancelled":
                    entry.get(
                        "abes_ausfall",
                        False
                    )
            }

            # =================================================
            # Attach UDOK data
            # =================================================

            udok = child_udoks.get(
                (
                    date,
                    lesson_number
                )
            )

            if udok:

                documentation = (
                    build_documentation(
                        udok,
                        basedata
                    )
                )

                if documentation:

                    lesson[
                        "documentation"
                    ] = documentation

                    documentation_count += 1

                homework = (
                    build_homework(
                        udok,
                        basedata
                    )
                )

                if homework:

                    lesson[
                        "homework"
                    ] = homework

                    homework_count += 1

            child_timetable.setdefault(
                date,
                []
            ).append(
                lesson
            )

        # ====================================================
        # Sort lessons
        # ====================================================

        for date in child_timetable:

            child_timetable[
                date
            ].sort(
                key=lambda x: (
                    x.get("lesson_number") or 999
                )
            )

        all_children[
            child_name
        ] = {

            "schueler_id":
                child_data.get(
                    "schueler_id"
                ),

            "klassenzug_id":
                klassenzug_id,

            "timetable":
                child_timetable,

            "documentation_count":
                documentation_count,

            "homework_count":
                homework_count
        }

    # ========================================================
    # Source of Truth Cache
    # ========================================================

    fetched_at = (
        datetime.datetime.now()
        .astimezone()
        .isoformat()
    )

    cache_data = {

        "fetched_at":
            fetched_at,

        "children":
            all_children
    }

    save_cache(
        "swop_timetable",
        cache_data
    )

    # ========================================================
    # Build timetable.json
    # ========================================================

    result = {

        "fetched_at":
            fetched_at,

        "children": {}
    }

    total_documentation_count = 0
    total_homework_count = 0

    for child_name, child_data in (
        all_children.items()
    ):

        result[
            "children"
        ][
            child_name
        ] = child_data[
            "timetable"
        ]

        total_documentation_count += (
            child_data[
                "documentation_count"
            ]
        )

        total_homework_count += (
            child_data[
                "homework_count"
            ]
        )

    # ========================================================
    # Write Output
    # ========================================================

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            result,
            f,
            indent=2,
            ensure_ascii=False
        )

    # ========================================================
    # Statistics
    # ========================================================

    lesson_count = sum(
        len(lessons)
        for child_data
        in result[
            "children"
        ].values()
        for lessons
        in child_data.values()
    )

    return {

        "data":
            result,

        "children_count":
            len(all_children),

        "lesson_count":
            lesson_count,

        "documentation_count":
            total_documentation_count,

        "homework_count":
            total_homework_count
    }

# ============================================================
# BASEDATA Cache
# ============================================================

def ensure_swop_basedata():
    """
    Stellt sicher, dass die SWOP Basedata vorhanden ist.

    Die Basedata ändern sich nur selten und werden daher nur
    geladen, wenn noch kein Cache vorhanden ist.
    """

    basedata = get_cache(
        "swop_basedata"
    )

    if basedata:
        return basedata

    print(
        "SWOP Basedata nicht gefunden."
    )

    print(
        "Lade SWOP Basedata..."
    )

    basedata_script = (
        Path(__file__).resolve().parent
        / "fetch_swop_basedata.py"
    )

    if not basedata_script.exists():

        raise RuntimeError(
            f"SWOP Basedata fetcher not found: {basedata_script}"
        )

    subprocess.run(
        [
            sys.executable,
            str(basedata_script)
        ],
        cwd=str(FETCH_DIR),
        check=True
    )

    basedata = get_cache(
        "swop_basedata"
    )

    if not basedata:

        raise RuntimeError(
            "SWOP Basedata konnte nach dem Fetch "
            "nicht geladen werden."
        )

    return basedata

# ============================================================
# UDOK Cache
# ============================================================

def ensure_udok_cache():
    """
    Stellt sicher, dass ein aktueller UDOK-Cache vorhanden ist.

    Der UDOK-Fetcher wird ausgeführt, wenn der Cache fehlt
    oder älter als 1 Stunde ist.
    """

    max_age = datetime.timedelta(
        hours=1
    )

    udok_cache = get_cache(
        UDOK_CACHE_NAME
    )

    refresh_required = False

    # --------------------------------------------------------
    # Cache fehlt oder enthält keine Kinder
    # --------------------------------------------------------

    if not udok_cache.get("children"):
        refresh_required = True

    else:

        fetched_at = udok_cache.get(
            "fetched_at"
        )

        if not fetched_at:

            refresh_required = True

        else:

            try:

                fetched_at = datetime.datetime.fromisoformat(
                    fetched_at
                )

                age = (
                    datetime.datetime.now(
                        datetime.timezone.utc
                    )
                    - fetched_at.astimezone(
                        datetime.timezone.utc
                    )
                )

                if age > max_age:
                    refresh_required = True

            except ValueError:

                refresh_required = True

    # --------------------------------------------------------
    # Cache aktualisieren
    # --------------------------------------------------------

    if refresh_required:

        print(
            "SWOP UDOK cache fehlt oder ist älter als 1 Stunde."
        )

        print(
            "Aktualisiere UDOK cache..."
        )

        udok_script = (
            Path(__file__).resolve().parent
            / "fetch_swop_udoks.py"
        )

        if not udok_script.exists():

            raise RuntimeError(
                f"UDOK fetcher not found: {udok_script}"
            )

        subprocess.run(
            [
                sys.executable,
                str(udok_script)
            ],
            cwd=str(FETCH_DIR),
            check=True
        )

        # Cache nach dem Fetch erneut laden
        udok_cache = get_cache(
            UDOK_CACHE_NAME
        )

        if not udok_cache.get("children"):

            raise RuntimeError(
                "SWOP UDOK cache konnte nach dem "
                "Fetch nicht geladen werden."
            )

    else:

        print(
            "SWOP UDOK cache is current."
        )

    return udok_cache

# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    result = fetch_swop_timetable()

    data = result[
        "data"
    ]

    today = (
        datetime.date.today()
        .isoformat()
    )

    print()
    print(
        "=== SWOP TIMETABLE ==="
    )

    print()

    print(
        "Children:"
    )

    print(
        result[
            "children_count"
        ]
    )

    print()

    print(
        "Lessons:"
    )

    print(
        result[
            "lesson_count"
        ]
    )

    print()

    print(
        "Documentation attached:"
    )

    print(
        result[
            "documentation_count"
        ]
    )

    print()

    print(
        "Homework attached:"
    )

    print(
        result[
            "homework_count"
        ]
    )

    print()

    print(
        "Output:"
    )

    print(
        OUTPUT_FILE
    )

    print()

    for child_name, child_data in (
        data.get(
            "children",
            {}
        ).items()
    ):

        lessons = (
            child_data.get(
                today,
                []
            )
        )

        print()

        print(
            f"{child_name} - "
            f"Lessons today: "
            f"{len(lessons)}"
        )

        if lessons:

            print()

            print(
                "First lesson:"
            )

            print()

            print(
                json.dumps(
                    lessons[0],
                    indent=2,
                    ensure_ascii=False
                )
            )