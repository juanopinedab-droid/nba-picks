import json
import os
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path

from . import database

ROOT = Path(__file__).parent.parent.parent
OLD_DB    = ROOT / "picks_history.db"
NEW_DB    = ROOT / "data" / "picks.db"
SETTINGS  = ROOT / "settings.json"
CACHE     = ROOT / "src" / "football" / "football_cache.json"
MODEL_LR  = ROOT / "src" / "nba" / "model_lr.py"


def run():
    print("  Migracion v1 → v2")
    print(f"  Destino: {NEW_DB}")

    # 1. Backup de la DB actual
    if OLD_DB.exists():
        bak = ROOT / "picks_history.db.bak"
        shutil.copy2(OLD_DB, bak)
        print(f"  Backup: {bak}")

        if not NEW_DB.exists():
            os.makedirs(NEW_DB.parent, exist_ok=True)
            shutil.copy2(OLD_DB, NEW_DB)
            print(f"  Copiado: {OLD_DB} → {NEW_DB}")

    # 2. Crear tablas nuevas + migrar columnas
    database.setup()
    database.migrate_bankroll_log_if_old()
    print("  Tablas v2 creadas.")

    # 3. Migrar settings.json → settings
    if SETTINGS.exists():
        try:
            with open(SETTINGS, encoding="utf-8") as f:
                data = json.load(f)
            for key, value in data.items():
                database.set_setting(key, value)
            print(f"  settings.json → settings ({len(data)} keys)")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️  Error leyendo settings.json: {e}")

    # 4. Migrar football_cache.json → cache
    if CACHE.exists():
        try:
            with open(CACHE, encoding="utf-8") as f:
                data = json.load(f)
            cache_date = data.get("date", str(date.today()))
            cache_key = f"football_stats:{cache_date}"
            database.cache_set(cache_key, data, ttl_hours=24)
            print(f"  football_cache.json → cache (key={cache_key})")
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️  Error leyendo football_cache.json: {e}")

    # 5. Migrar model_lr.py → model_params
    if MODEL_LR.exists():
        try:
            params = _read_model_lr(MODEL_LR)
            if params:
                database.save_model_params("logistic_regression", params)
                print("  model_lr.py → model_params (logistic_regression)")
        except Exception as e:
            print(f"  ⚠️  Error leyendo model_lr.py: {e}")

    print("  Migracion completada.")


def _read_model_lr(path: Path) -> dict | None:
    namespace = {}
    with open(path, encoding="utf-8") as f:
        exec(f.read(), namespace)
    coef = namespace.get("_COEF")
    intercept = namespace.get("_INTERCEPT")
    means = namespace.get("_MEANS")
    scales = namespace.get("_SCALES")
    if coef is None or intercept is None:
        return None
    return {
        "coef": coef,
        "intercept": intercept,
        "means": means,
        "scales": scales,
    }


if __name__ == "__main__":
    run()
