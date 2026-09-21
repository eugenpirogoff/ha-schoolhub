from pathlib import Path
import sys
import json
import datetime

# ============================================================
# SchoolHub Basis-Pfad für Imports
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# SchoolHub Imports
# ============================================================

from auth.auth_swop import get_auth

from core.cache import save_cache
from core.cache import get_cache
from core.config import get_child

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
    "homework.json"
)

# ============================================================
# Lookups
# ============================================================

def get_teacher_name(
    basedata,
    teacher_id
):

    return (
        basedata
        .get(
            "lehrernamen_by_id",
            {}
        )
        .get(
            str(teacher_id)
        )
    )


def get_subject_name(
    basedata,
    subject_id
):

    subjects = (
        basedata
        .get(
            "schulfaecher_by_id",
            {}
        )
    )

    subject = subjects.get(
        str(subject_id),
        {}
    )

    if isinstance(subject, dict):

        return (
            subject.get("bezeichnung")
            or subject.get("name")
            or str(subject_id)
        )

    return str(subject)


# ============================================================
# Fetch
# ============================================================

def fetch_swop_udoks():

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
    # Metadata
    # ========================================================

    child1_config = get_child(
        "child1"
    )

    account_name = (
        child1_config["swop_account"]
    )

    mydata_cache = get_cache(
        account_name
    )

    mydata = (
        mydata_cache
        .get(
            "mydata",
            {}
        )
    )

    basedata = get_cache(
        "swop_basedata"
    )

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
    # Fetch range
    # ========================================================

    start_date, end_date = (
        get_fetch_range(
            "swop"
        )
    )

    # ========================================================
    # Fetch UDOKs for all children
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
            f"/swop_udoks"
            f"/{start_date}"
            f"/{end_date}.json"
            f"?klassenzug_id={klassenzug_id}"
        )

        with opener.open(
            url,
            timeout=30
        ) as response:

            raw_udoks = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        # ====================================================
        # Deduplicate by UDOK ID
        # ====================================================

        seen_udok_ids = set()

        udoks = []

        for entry in raw_udoks:

            udok = (
                entry.get(
                    "unterrichtsdokumentation",
                    {}
                )
            )

            udok_id = udok.get(
                "id"
            )

            if not udok_id:

                udoks.append(
                    entry
                )

                continue

            if udok_id in seen_udok_ids:
                continue

            seen_udok_ids.add(
                udok_id
            )

            udoks.append(
                entry
            )

        all_children[child_name] = {

            "schueler_id":
                child_data.get(
                    "schueler_id"
                ),

            "klassenzug_id":
                klassenzug_id,

            "udoks":
                udoks
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
        "swop_udoks_clean",
        cache_data
    )

    # ========================================================
    # Homework JSON
    # ========================================================

    homework_data = {

        "fetched_at":
            fetched_at,

        "children": {}
    }

    total_homework_count = 0

    for child_name, child_data in (
        all_children.items()
    ):

        child_homework = {}

        for entry in child_data[
            "udoks"
        ]:

            udok = (
                entry.get(
                    "unterrichtsdokumentation",
                    {}
                )
            )

            homework_text = (
                udok.get(
                    "hausaufgaben",
                    ""
                )
                .strip()
            )

            if not homework_text:
                continue

            due_date = (
                udok.get(
                    "hausaufgaben_zu_erledigen_bis"
                )
                or udok.get(
                    "datum"
                )
            )

            if not due_date:
                continue

            child_homework.setdefault(
                due_date,
                []
            )

            child_homework[
                due_date
            ].append({

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
            })

            total_homework_count += 1

        homework_data[
            "children"
        ][child_name] = child_homework

    # ========================================================
    # Write Homework JSON
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
            homework_data,
            f,
            indent=2,
            ensure_ascii=False
        )

    # ========================================================
    # Statistics
    # ========================================================

    downloaded_count = sum(
        len(
            child_data["udoks"]
        )
        for child_data
        in all_children.values()
    )

    return {

        "children_count":
            len(all_children),

        "downloaded_count":
            downloaded_count,

        "homework_count":
            total_homework_count
    }


# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    stats = fetch_swop_udoks()

    print()

    print(
        "=== SWOP UDOKS ==="
    )

    print()

    print(
        "Children:"
    )

    print(
        stats[
            "children_count"
        ]
    )

    print()

    print(
        "Downloaded:"
    )

    print(
        stats[
            "downloaded_count"
        ]
    )

    print()

    print(
        "Homework Entries:"
    )

    print(
        stats[
            "homework_count"
        ]
    )

    print()

    print(
        "Cache:"
    )

    print(
        "metadata_swop_udoks_clean.json"
    )

    print()

    print(
        "Output:"
    )

    print(
        "homework.json"
    )