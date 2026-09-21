from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_FILE = BASE_DIR / "config.yaml"
CACHE_DIR = BASE_DIR / "cache"

if Path("/homeassistant").exists():
    OUTPUT_DIR = Path("/homeassistant/www/schoolhub")
elif Path("/config").exists():
    OUTPUT_DIR = Path("/config/www/schoolhub")
else:
    OUTPUT_DIR = BASE_DIR / "output"