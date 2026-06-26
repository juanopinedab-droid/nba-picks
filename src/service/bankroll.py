from ..core import database, config


def get_summary() -> dict:
    live_br = database.get_current_bankroll(config.get_bankroll())
    record  = database.get_record()
    profit  = sum(v["profit"] for v in record.values())
    br = config.get_bankroll()
    return {
        "initial":   br,
        "current":   live_br,
        "profit":    profit,
        "roi":       (profit / br * 100) if br > 0 else 0,
        "bankroll":  br,
    }


def deposit(amount: float, note: str = "") -> dict:
    amount = abs(float(amount))
    if amount <= 0:
        return get_summary()
    new_br = config.get_bankroll() + amount
    config.set_bankroll(new_br)
    database.log_bankroll_manual("DEPOSIT", amount, note)
    return get_summary()


def withdraw(amount: float, note: str = "") -> dict:
    amount = abs(float(amount))
    if amount <= 0:
        return get_summary()
    current = config.get_bankroll()
    taken = min(amount, current)
    new_br = max(0, current - taken)
    config.set_bankroll(new_br)
    database.log_bankroll_manual("WITHDRAW", -taken, note)
    return get_summary()


def get_history(limit: int = 50) -> list[dict]:
    import sqlite3
    with database.get_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, type, amount, balance_after, pick_id, note "
            "FROM bankroll_log ORDER BY id DESC LIMIT ?",
            (limit,)
        ).fetchall()
    return [
        {
            "id":            r[0],
            "created_at":    r[1],
            "type":          r[2],
            "amount":         r[3],
            "balance_after": r[4],
            "pick_id":       r[5],
            "note":           r[6],
        }
        for r in rows
    ]


def set_bankroll(value: float) -> dict:
    old = config.get_bankroll()
    config.set_bankroll(float(value))
    database.log_bankroll_manual("INITIAL", float(value) - old, f"Ajuste manual: {old} → {value}")
    return get_summary()
