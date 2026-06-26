from datetime import date as date_cls
from ..core import database, resolver
from ..core import config


def execute(params: dict, ctx) -> dict:
    target_date = None
    fecha_str = params.get("fecha", "").strip()
    if fecha_str:
        try:
            target_date = date_cls.fromisoformat(fecha_str)
        except ValueError:
            raise ValueError(f"Fecha invalida: {fecha_str} (usa YYYY-MM-DD)")

    ctx.log_line("Resolviendo picks pendientes...")
    ctx.set_progress(0.1)

    pending = database.get_pending_with_details()
    nba_pending = [p for p in pending if p.get("sport") == "nba"]
    ctx.log_line(f"{len(nba_pending)} pick(s) NBA pendientes")

    if not nba_pending:
        ctx.set_progress(1.0)
        ctx.log_line("Sin picks pendientes para resolver")
        return {"resolved": 0, "wins": 0, "losses": 0, "pushes": 0, "profit": 0}

    ctx.set_progress(0.3)

    summary = resolver.auto_resolve_all(target_date=target_date)

    ctx.set_progress(1.0)

    wins = summary.get("WIN", 0)
    losses = summary.get("LOSS", 0)
    pushes = summary.get("PUSH", 0)
    total = wins + losses + pushes

    ctx.log_line(f"Resueltos: {total} (W:{wins} L:{losses} P:{pushes})")

    return {
        "resolved": total,
        "wins":     wins,
        "losses":   losses,
        "pushes":   pushes,
        "profit":   summary.get("profit", 0),
        "details":  summary,
    }
