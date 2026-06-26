from flask import Blueprint, jsonify, request

from ...service.bankroll import get_summary, deposit, withdraw, set_bankroll, get_history

bp = Blueprint("bankroll", __name__)


@bp.route("/bankroll", methods=["GET", "POST"])
def handle_bankroll():
    if request.method == "POST":
        data = request.json or {}
        val = data.get("bankroll")
        if val is not None:
            set_bankroll(float(val))
        return jsonify({"ok": True, "bankroll": get_summary()["bankroll"]})
    return jsonify(get_summary())


@bp.route("/bankroll/deposit", methods=["POST"])
def do_deposit():
    data = request.json or {}
    amount = abs(float(data.get("amount", 0)))
    note = data.get("note", "")
    return jsonify(deposit(amount, note))


@bp.route("/bankroll/withdraw", methods=["POST"])
def do_withdraw():
    data = request.json or {}
    amount = abs(float(data.get("amount", 0)))
    note = data.get("note", "")
    return jsonify(withdraw(amount, note))


@bp.route("/bankroll/history", methods=["GET"])
def do_history():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_history(limit))
