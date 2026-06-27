from flask import Blueprint, jsonify

from ...core import database, config

bp = Blueprint("history", __name__)


@bp.route("/history")
def get_history():
    record = database.get_record()
    summary = database.get_roi_summary()
    with database.get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, bet_type, selection, odds,
                   result, stake_cop, profit_cop, confidence
            FROM picks
            WHERE result != 'PENDING'
            ORDER BY date DESC LIMIT 100
        """).fetchall()

    wins = record.get("WIN", {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    pushes = record.get("PUSH", {}).get("count", 0)
    profit = sum(v["profit"] for v in record.values())

    return jsonify({
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "profit": profit,
        "bankroll": database.get_current_bankroll(config.get_bankroll()),
        "roi_summary": summary,
        "history": [
            {
                "id": r[0], "date": r[1], "game": r[2], "bet_type": r[3],
                "selection": r[4], "odds": r[5], "result": r[6],
                "stake_cop": r[7] or 0, "profit_cop": r[8] or 0,
                "confidence": r[9] or "",
            }
            for r in rows
        ],
    })
