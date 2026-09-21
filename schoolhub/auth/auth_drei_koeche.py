from pathlib import Path
import sys
import base64
import json
import time
import urllib.request

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
# Drei Köche API
# ============================================================

API_BASE = "https://rest.drei-koeche.de/frontend/public/index.php/api/v2"

LOGIN_CLIENT_URL = f"{API_BASE}/loginclient"
LOGIN_URL = f"{API_BASE}/login"

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "SchoolHub/1.0"
}


# ============================================================
# Helpers
# ============================================================

def _post(url, payload, headers=None):

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=DEFAULT_HEADERS | (headers or {}),
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_account_name(child_name):
    return get_child(child_name)["drei_koeche_account"]


# ============================================================
# Login Client
# ============================================================

def login_client():

    return _post(
        LOGIN_CLIENT_URL,
        {
            "usrClient": "3K-FRONTEND",
            "pwdClient": "chicken"
        }
    )


# ============================================================
# Login User
# ============================================================

def login(child_name):

    account_name = _get_account_name(child_name)
    account = get_account(account_name)

    client_jwt = login_client()["token"]

    headers = DEFAULT_HEADERS.copy()
    headers["Authorization"] = f"Bearer {client_jwt}"

    login_data = _post(
        LOGIN_URL,
        {
            "username": account["username"],
            "password": account["password"],
            "rememberMe": ""
        },
        {
            "Authorization": f"Bearer {client_jwt}"
        }
    )

    if login_data.get("subcode") != 0:
        raise RuntimeError(
            f"Drei Köche Login fehlgeschlagen: {login_data.get('detail')}"
        )

    save_cache(account_name, login_data)

    return login_data


# ============================================================
# JWT
# ============================================================

def token_is_valid(token):

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)

        jwt_data = json.loads(
            base64.urlsafe_b64decode(payload)
        )

        return jwt_data["exp"] > time.time()

    except Exception:
        return False


# ============================================================
# Auth
# ============================================================

def get_auth(child_name):

    account_name = _get_account_name(child_name)

    cache = get_cache(account_name)

    if not cache:
        print("No cache found -> login")
        return login(child_name)

    token = cache.get("user", {}).get("token")

    if not token:
        print("No token found -> login")
        return login(child_name)

    if not token_is_valid(token):
        print("Token expired -> login")
        return login(child_name)

    print("Using cached token")

    return cache


# ============================================================
# Test
# ============================================================

if __name__ == "__main__":

    auth = get_auth("child1")

    print()
    print("=== USER ===")
    print(auth["user"]["firstname"], auth["user"]["lastname"])
    print()
    print("Contract ID:", auth["user"]["contractId"])
    print("Object:", auth["user"]["objectName"])