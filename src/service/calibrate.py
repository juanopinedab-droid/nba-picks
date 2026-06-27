import io
import sys
import re


def execute(params: dict, ctx) -> dict:
    apply_changes = params.get("apply", False)

    ctx.log_line("Importando modulo de calibracion...")

    import cli.calibrate as cal

    cal.DB_PATH = "data/picks.db"

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf

    try:
        ctx.log_line("Analizando historial de picks...")
        ctx.set_progress(0.1)

        picks = cal.get_resolved()
        ctx.log_line(f"{len(picks)} picks resueltos encontrados")

        if not picks:
            ctx.set_progress(1.0)
            ctx.log_line("Sin picks resueltos para calibrar")
            return {"output": "Sin picks resueltos en la base de datos.\n"}

        cal.section_overview(picks)
        ctx.set_progress(0.3)
        conf_recs = cal.section_by_confidence(picks)
        ctx.set_progress(0.5)
        bad_types = cal.section_by_bet_type(picks)
        ctx.set_progress(0.6)
        best_edge = cal.section_by_edge(picks)
        ctx.set_progress(0.7)
        cal.section_calibration(picks)
        ctx.set_progress(0.8)
        recs = cal.section_recommendations(picks, conf_recs, bad_types, best_edge)

        if apply_changes:
            cal.apply_recommendations(recs)
            ctx.log_line("Recomendaciones aplicadas al .env")

        output = _strip_ansi(buf.getvalue())

    finally:
        sys.stdout = old_stdout

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Calibracion completada")

    return {"output": output, "recommendations": recs if not apply_changes else []}


def _strip_ansi(text: str) -> str:
    return re.sub(r"\033\[[0-9;]*m", "", text)
