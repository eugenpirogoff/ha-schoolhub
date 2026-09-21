from pathlib import Path
import sys
import json

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

# ============================================================
# SWOP API
# ============================================================

BASE_URL = (
    "https://regenbogen-grundschule.swop.schule"
)

BASEDATA_URL = (
    f"{BASE_URL}/swop_basedata.json"
)

# ============================================================
# Fetch
# ============================================================

def fetch_swop_basedata(child_name):

    opener = get_auth(
        child_name
    )

    with opener.open(
        BASEDATA_URL,
        timeout=30
    ) as response:

        response_data = (
            response
            .read()
            .decode("utf-8")
        )

    basedata = json.loads(
        response_data
    )

    save_cache(
        "swop_basedata",
        basedata
    )

    return basedata

# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    child_name = "child1"

    basedata = fetch_swop_basedata(
        child_name
    )

    print()
    print(
        "=== SWOP BASEDATA ==="
    )

    print()

    print(
        "Top-Level Keys:"
    )

    print(
        len(
            basedata.keys()
        )
    )

    print()

    for key in sorted(
        basedata.keys()
    )[:20]:

        print(
            "-",
            key
        )

    print()

    print(
        "Cache file:"
    )

    print(
        "metadata_swop_basedata.json"
    )

    print()

    cache = get_cache(
        "swop_basedata"
    )

    print(
        "Cache loaded:"
    )

    print(
        bool(cache)
    )
