from pathlib import Path
import sys
import json
import urllib.request
import http.cookiejar
from datetime import datetime

# ============================================================
# SchoolHub Basis-Pfad für Imports
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# ============================================================
# SchoolHub Imports
# ============================================================

from core.cache import get_cache, save_cache
from core.config import get_account, get_child

# ============================================================
# SWOP API
# ============================================================

BASE_URL = (
    "https://regenbogen-grundschule.swop.schule"
)

LOGIN_URL = (
    f"{BASE_URL}/session/login/post.json"
)

MYDATA_URL = (
    f"{BASE_URL}/swop_mydata.json"
)

# ============================================================
# Cookie Session
# ============================================================

def create_opener():

    cookie_jar = (
        http.cookiejar.CookieJar()
    )

    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(
            cookie_jar
        )
    )

    opener.addheaders = [
        (
            "User-Agent",
            "SchoolHub/1.0"
        ),
        (
            "Accept",
            "application/json"
        )
    ]

    return opener, cookie_jar

# ============================================================
# Session Check
# ============================================================

def session_is_valid(opener):

    try:

        with opener.open(
            MYDATA_URL,
            timeout=15
        ) as response:

            data = json.loads(
                response
                .read()
                .decode("utf-8")
            )

        return (
            data.get("success")
            is True
        )

    except Exception:

        return False

# ============================================================
# Login
# ============================================================

def login(child_name):

    child = get_child(
        child_name
    )

    account_name = (
        child["swop_account"]
    )

    account = get_account(
        account_name
    )

    opener, cookie_jar = (
        create_opener()
    )

    payload = {
        "loginname": (
            account["username"]
        ),
        "password": (
            account["password"]
        )
    }

    data = json.dumps(
        payload
    ).encode("utf-8")

    request = urllib.request.Request(
        LOGIN_URL,
        data=data,
        headers={
            "Content-Type":
                "application/json",
            "Accept":
                "application/json",
            "User-Agent":
                "SchoolHub/1.0"
        },
        method="POST"
    )

    with opener.open(
        request,
        timeout=15
    ) as response:

        login_data = json.loads(
            response
            .read()
            .decode("utf-8")
        )

    if not login_data.get(
        "success"
    ):

        raise RuntimeError(
            f"SWOP login failed: "
            f"{login_data}"
        )

    with opener.open(
        MYDATA_URL,
        timeout=15
    ) as response:

        mydata = json.loads(
            response
            .read()
            .decode("utf-8")
        )

    if not mydata.get(
        "success"
    ):

        raise RuntimeError(
            "SWOP login succeeded "
            "but session validation failed"
        )

    cache_data = {
        "last_login": (
            datetime.now()
            .astimezone()
            .isoformat()
        ),
        "mydata": mydata
    }

    save_cache(
        account_name,
        cache_data
    )

    return opener

# ============================================================
# Auth
# ============================================================

def get_auth(child_name):

    child = get_child(
        child_name
    )

    account_name = (
        child["swop_account"]
    )

    cache = get_cache(
        account_name
    )

    if cache:

        print(
            "Metadata cache found"
        )

    else:

        print(
            "No metadata cache found"
        )

    print(
        "Creating fresh SWOP session"
    )

    return login(
        child_name
    )

# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    child_name = "child1"

    opener = get_auth(
        child_name
    )

    child = get_child(
        child_name
    )

    account_name = (
        child["swop_account"]
    )

    cache = get_cache(
        account_name
    )

    mydata = cache.get(
        "mydata",
        {}
    )

    print()
    print(
        "=== SWOP LOGIN OK ==="
    )

    print()

    print(
        "Last Login:"
    )

    print(
        cache.get(
            "last_login"
        )
    )

    print()

    print(
        "Name:"
    )

    print(
        mydata.get(
            "full_name"
        )
    )

    print()

    print(
        "Role:"
    )

    if mydata.get(
        "is_eltern"
    ):
        print(
            "Parent"
        )

    elif mydata.get(
        "is_schueler"
    ):
        print(
            "Student"
        )

    else:
        print(
            "Unknown"
        )

    print()

    children = mydata.get(
        "erziehungsberechtigt_fuer",
        []
    )

    print(
        "Children:"
    )

    if not children:

        print(
            "- none"
        )

    else:

        for child_data in children:

            print(
                f"- {child_data.get('full_name')} "
                f"(schueler_id="
                f"{child_data.get('schueler_id')})"
            )

    print()

    print(
        "Top-Level Keys:"
    )

    print(
        len(
            mydata.keys()
        )
    )
