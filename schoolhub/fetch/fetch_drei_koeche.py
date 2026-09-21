from pathlib import Path
import sys
import json
import re
import html
import urllib.request

from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# SchoolHub Basis-Pfad
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# SchoolHub Imports
# ============================================================

from auth.auth_drei_koeche import get_auth
from core.config import get_children
from core.date_ranges import get_fetch_range
from core.paths import OUTPUT_DIR

# ============================================================
# Drei Köche API
# ============================================================

API_BASE = "https://rest.drei-koeche.de/frontend/public/index.php/api/v2"

OUTPUT_FILE = OUTPUT_DIR / "ordered_meals.json"

DEFAULT_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SchoolHub/1.0"
}


# ============================================================
# Helpers
# ============================================================

def _get(url, token):

    request = urllib.request.Request(
        url,
        headers=DEFAULT_HEADERS | {
            "Authorization": f"Bearer {token}"
        },
        method="GET"
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def clean_menu_name(text):

    text = html.unescape(text)
    text = re.sub(r"<sup.*?</sup>", "", text)
    text = re.sub(r"<.*?>", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"^\d+x\s*", "", text)

    return text.strip()


def convert_date(date_str):

    return datetime.strptime(
        date_str,
        "%d.%m.%y"
    ).strftime("%Y-%m-%d")


# ============================================================
# API
# ============================================================

def fetch_preorderhistory(child_name):

    auth = get_auth(child_name)

    start_date, end_date = get_fetch_range(
        "drei_koeche"
    )

    url = (
        f"{API_BASE}/preorderhistory/"
        f"{auth['user']['contractId']}/"
        f"{start_date}TO{end_date}"
    )

    print(
        f"Fetching range: "
        f"{start_date} -> {end_date}"
    )

    return _get(
        url,
        auth["user"]["token"]
    )


# ============================================================
# Output
# ============================================================

def save_ordered_meals(all_meals):

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    output = {
        "fetched_at": datetime.now(
            ZoneInfo("Europe/Berlin")
        ).isoformat(),
        "children": {}
    }

    for child_name, meals_data in all_meals.items():

        output["children"][child_name] = {
            convert_date(meal["deliverDate"]): {
                "menu": clean_menu_name(
                    meal["menuName"]
                )
            }
            for meal in meals_data.get("data", [])
        }

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            output,
            f,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    children = get_children()

    all_meals = {}

    for child_name in children:

        print()
        print(f"=== {child_name} ===")

        meals_data = fetch_preorderhistory(
            child_name
        )

        all_meals[child_name] = meals_data

        meals = meals_data.get(
            "data",
            []
        )

        print(
            f"Meals found: {len(meals)}"
        )

        if meals:

            print("Next meal:")
            print(
                "Date:",
                convert_date(
                    meals[0]["deliverDate"]
                )
            )
            print(
                "Menu:",
                clean_menu_name(
                    meals[0]["menuName"]
                )
            )

    save_ordered_meals(
        all_meals
    )

    print()
    print("ordered_meals.json updated")