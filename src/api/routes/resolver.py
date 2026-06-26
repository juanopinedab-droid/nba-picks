import threading
from datetime import date as dt_date
from flask import Blueprint, jsonify, request

from ...core import database, resolver

bp = Blueprint("resolver", __name__)

_resolve_state = {"running": False, "log": [], "summary": {}}


@bp.route("/resolve", methods=["POST"])
def trigger_resolve():
    if _resolve_state["running"]:
        return jsonify({"status": "already_running"}), 409
    data = request.json or {}
    target_date = None
    if data.get("fecha"):
        try:
            target_date = dt_date.fromisoformat(data["fecha"])
        except ValueError:
            return jsonify({"error": "Fecha invalida. Usa YYYY-MM-DD"}), 400

    _resolve_state["running"] = True
    _resolve_state["log"] = ["Iniciando resolucion..."]
    _resolve_state["summary"] = {}

    def _run():
        import io, sys
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        try:
            summary = resolver.auto_resolve_all(target_date=target_date)
            _resolve_state["summary"] = summary
        except Exception as e:
            _resolve_state["log"].append(f"[X] Error: {e}")
        finally:
            sys.stdout = old_stdout
            _resolve_state["running"] = False

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@bp.route("/resolve/status")
def resolve_status():
    return jsonify({
        "running": _resolve_state["running"],
        "summary": _resolve_state["summary"],
        "log": _resolve_state["log"],
    })


@bp.route("/close", methods=["POST"])
def save_closing_odds():
    def _run():
        resolver.save_closing_odds_for_pending()

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@bp.route("/close/status")
def closing_status():
    data = database.get_clv_summary()
    return jsonify(data if data else {"n": 0})
