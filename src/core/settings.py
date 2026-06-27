import json
from pathlib import Path
from . import database

SETTINGS_FILE = Path(__file__).parent.parent.parent / "settings.json"


def load_settings() -> dict:
    data = database.load_all_settings()
    if data:
        return data
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                database.set_setting(key, value)
            return {k: str(v) for k, v in data.items()}
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_setting(key: str, value):
    database.set_setting(key, value)


def apply_saved():
    data = load_settings()
    if not data:
        return
    from . import config
    if "bankroll" in data:
        config._set_bankroll_raw(float(data["bankroll"]))
    if "min_edge" in data:
        config._set_min_edge_raw(float(data["min_edge"]))
    if "fetch_props" in data:
        config._set_fetch_props_raw(bool(data["fetch_props"]))
