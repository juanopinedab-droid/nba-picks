import io
import sys
from datetime import datetime

_SEASONS_ALL = ["2023-24", "2024-25"]


def execute(params: dict, ctx) -> dict:
    num_seasons = int(params.get("seasons", 2))
    download_only = params.get("download_only", False)
    seasons = _SEASONS_ALL[-num_seasons:] if num_seasons <= 2 else _SEASONS_ALL

    ctx.log_line("Importando modulos de backtest...")

    import cli.backtest as bt

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        if download_only:
            ctx.log_line("Descargando datos historicos...")
            bt.download_all(seasons)
            ctx.log_line("Descarga completada")
        else:
            ctx.log_line("Descargando datos (si faltan)...")
            bt.download_all(seasons)
            ctx.set_progress(0.2)

            ctx.log_line("Ejecutando backtest...")
            bt.run_backtest(seasons)
            ctx.set_progress(0.9)

            _migrate_model_lr()
            ctx.log_line("Modelo LR migrado a model_params")

        output = buf.getvalue()

    finally:
        sys.stdout = old_stdout

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Backtest completado")

    return {"output": output, "seasons": seasons}


def _migrate_model_lr():
    from pathlib import Path
    from ..core import database

    model_path = Path(__file__).parent.parent / "nba" / "model_lr.py"
    if not model_path.exists():
        return

    try:
        ns = {}
        with open(model_path, encoding="utf-8") as f:
            exec(f.read(), ns)
        coef      = ns.get("_COEF")
        intercept = ns.get("_INTERCEPT")
        means     = ns.get("_MEANS")
        scales    = ns.get("_SCALES")
        if coef is not None and intercept is not None:
            params = {
                "coef": coef, "intercept": intercept,
                "means": means, "scales": scales,
            }
            database.save_model_params("logistic_regression", params)
    except Exception:
        pass
