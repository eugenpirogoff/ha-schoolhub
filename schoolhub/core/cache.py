import json

from core.paths import CACHE_DIR

def save_cache(account_name, data):
    CACHE_DIR.mkdir(exist_ok=True)
    cache_file = CACHE_DIR / f"metadata_{account_name}.json"
    with open(cache_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def load_cache(account_name):
    cache_file = CACHE_DIR / f"metadata_{account_name}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def get_cache(account_name):
    data = load_cache(account_name)
    if data is None:
        return {}
    return data