from flask import Blueprint, jsonify, request

from ...core import database

bp = Blueprint("pending", __name__)


@bp.route("/pending")
def list_pending():
    return jsonify(database.get_pending_with_details())


@bp.route("/pending/<int:pick_id>/result", methods=["POST"])
def mark_result(pick_id):
    data = request.json
    result = data.get("result", "").upper()
    if result not in ("WIN", "LOSS", "PUSH"):
        return jsonify({"error": "Resultado invalido. Usa WIN, LOSS o PUSH"}), 400
    database.mark_result(pick_id, result)
    return jsonify({"ok": True})
