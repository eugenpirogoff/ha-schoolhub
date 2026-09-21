#!/usr/bin/env python3

from pathlib import Path
import sys
import json
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request

# ============================================================
# SchoolHub Basis-Pfad für Imports
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# SchoolHub Imports
# ============================================================

from auth.auth_swop import get_auth

from core.cache import get_cache
from core.paths import OUTPUT_DIR


# ============================================================
# SWOP API
# ============================================================

BASE_URL = "https://regenbogen-grundschule.swop.schule"

# ============================================================
# Output
# ============================================================

CLASS_NEWS_FILE = OUTPUT_DIR / "class_news.json"
SCHOOL_NEWS_FILE = OUTPUT_DIR / "school_news.json"


def fetch_json(opener, path, params=None):
    """Führt einen GET-Request aus und gibt die JSON-Antwort zurück."""
    url = f"{BASE_URL}{path}"

    if params:
        url += "?" + urlencode(params)

    request = Request(
        url,
        headers={
            "Accept": "application/json",
        },
    )

    with opener.open(request) as response:
        return json.loads(response.read().decode("utf-8"))


def get_news_module_id(opener, menue_id):
    """ 
    Ermittelt das EcmodulNews-Modul für eine Seite. 
    
    Die Module können sich in verschiedenen Bereichen der Seite 
    befinden (oben, links, rechts, unten, versteckt).
    Gesucht wird explizit nach dem Modultyp EcmodulNews.
    """ 
    data = fetch_json( 
        opener,
        f"/swop_info/{menue_id}.json",
    )

    modulelist = data.get("modulelist", {}) 

    for modules in modulelist.values(): 
        if not isinstance(modules, list):
            continue 

        for module in modules:
            ecmodule_menue = module.get("ecmodule_menue", {})

            if ecmodule_menue.get("modultyp") == "EcmodulNews": 
                return ecmodule_menue.get("ecmodule_menue_id")

    return None

def fetch_news_posts(opener, menue_id, module_id):
    """
    Lädt alle News-Seiten eines News-Moduls.

    Die einzelnen Posts werden unverändert übernommen,
    genau so wie SWOP sie liefert.
    """
    posts = []
    page = 0

    while True:
        data = fetch_json(
            opener,
            f"/swop_info_news_posts_page/{menue_id}/{module_id}/{page}.json",
            {
                "nc": int(datetime.now().timestamp() * 1000),
            },
        )

        page_posts = data.get("news_posts", [])

        if not page_posts:
            break

        # SWOP-Posts 1:1 übernehmen
        posts.extend(page_posts)

        page += 1

    return posts


def fetch_class_news(opener, mydata):
    """Lädt die Klassen-News für alle Kinder des gemeinsamen SWOP-Accounts."""
    result = {}

    children = mydata.get("erziehungsberechtigt_fuer", [])

    for index, child in enumerate(children, start=1):
        child_key = f"child{index}"

        menue_id = child.get("klassenseite_menue_id")

        if not menue_id:
            print(
                f"{child_key}: Keine klassenseite_menue_id gefunden"
            )

            result[child_key] = {
                "posts": []
            }

            continue

        print(
            f"{child_key}: Suche Klassen-News-Modul "
            f"(menue_id={menue_id})"
        )

        module_id = get_news_module_id(
            opener,
            menue_id,
        )

        if not module_id:
            print(
                f"{child_key}: Kein EcmodulNews-Modul gefunden"
            )

            result[child_key] = {
                "posts": []
            }

            continue

        print(
            f"{child_key}: Lade Klassen-News "
            f"(module_id={module_id})"
        )

        posts = fetch_news_posts(
            opener,
            menue_id,
            module_id,
        )

        print(
            f"{child_key}: {len(posts)} News gefunden"
        )

        result[child_key] = {
            "posts": posts
        }

    return result


def fetch_school_news(opener):
    """
    Ermittelt die interne Startseite und anschließend das
    zugehörige EcmodulNews-Modul dynamisch.

    Die IDs für die Schul-News werden nicht fest im Code
    hinterlegt, sondern aus der SWOP-Navigation bzw. der
    Seitenstruktur ermittelt.
    """

    # ---------------------------------------------------------
    # Interne Startseite ermitteln
    # ---------------------------------------------------------

    print("Schule: Ermittle interne Startseite...")

    navigation = fetch_json(
        opener,
        "/swop_info_navigation.json",
        {
            "nc": int(datetime.now().timestamp() * 1000),
        },
    )

    school_news_menue_id = (
        navigation
        .get("navigation", {})
        .get("interne_startseite_id")
    )

    if not school_news_menue_id:
        raise RuntimeError(
            "Keine interne_startseite_id in der SWOP-Navigation gefunden."
        )

    print(
        f"Schule: Interne Startseite gefunden "
        f"(menue_id={school_news_menue_id})"
    )

    # ---------------------------------------------------------
    # News-Modul der internen Startseite ermitteln
    # ---------------------------------------------------------

    print(
        "Schule: Suche EcmodulNews-Modul "
        f"(menue_id={school_news_menue_id})"
    )

    school_news_module_id = get_news_module_id(
        opener,
        school_news_menue_id,
    )

    if not school_news_module_id:
        raise RuntimeError(
            "Kein EcmodulNews-Modul auf der internen Startseite gefunden."
        )

    print(
        f"Schule: News-Modul gefunden "
        f"(module_id={school_news_module_id})"
    )

    # ---------------------------------------------------------
    # Schul-News laden
    # ---------------------------------------------------------

    print(
        "Schule: Lade Schul-News "
        f"(menue_id={school_news_menue_id}, "
        f"module_id={school_news_module_id})"
    )

    posts = fetch_news_posts(
        opener,
        school_news_menue_id,
        school_news_module_id,
    )

    print(
        f"Schule: {len(posts)} News gefunden"
    )

    return posts


def save_json(filename, data):
    """Speichert JSON mit UTF-8 und lesbarer Formatierung."""
    with open(filename, "w", encoding="utf-8") as file:
        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2,
        )


def fetch_swop_news():
    """
    Hauptfunktion.

    Der gemeinsame Familien-Account wird ausschließlich über child1
    angesprochen. Die Antwort enthält anschließend alle Kinder.
    """

    print("Login über gemeinsamen SWOP-Familienaccount...")

    # child1 dient hier bewusst nur als Einstiegspunkt für den
    # gemeinsamen SWOP-Account der Familie.
    opener = get_auth("child1")

    # auth_swop.py cached mydata unter dem Accountnamen.
    account_cache = get_cache("swop_family")

    if not account_cache:
        raise RuntimeError(
            "Kein SWOP-Account-Cache gefunden."
        )

    mydata = account_cache.get("mydata")

    if not mydata:
        raise RuntimeError(
            "Keine mydata-Daten im SWOP-Account-Cache gefunden."
        )

    fetched_at = datetime.now(
        timezone.utc
    ).isoformat()

    # ---------------------------------------------------------
    # Klassen-News
    # ---------------------------------------------------------

    class_news = fetch_class_news(
        opener,
        mydata,
    )

    class_news_output = {
        "fetched_at": fetched_at,
        "children": class_news,
    }

    save_json(
        CLASS_NEWS_FILE,
        class_news_output,
    )

    # ---------------------------------------------------------
    # Schul-News
    # ---------------------------------------------------------

    school_news = fetch_school_news(
        opener,
    )

    school_news_output = {
        "fetched_at": fetched_at,
        "posts": school_news,
    }

    save_json(
        SCHOOL_NEWS_FILE,
        school_news_output,
    )

    print()
    print("News erfolgreich aktualisiert.")
    print(f"  -> {CLASS_NEWS_FILE}")
    print(f"  -> {SCHOOL_NEWS_FILE}")


if __name__ == "__main__":
    fetch_swop_news()