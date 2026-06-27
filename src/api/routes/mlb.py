from flask import Blueprint, jsonify, request

from ...core import database, config
from ...service.orchestrator import JobManager

bp = Blueprint("mlb", __name__)

_state = {
    "generating": False,
    "picks": [],
    "picks_ml": [],
    "picks_rl": [],
    "picks_f5": [],
    "games": [],
    "timestamp": None,
    "bankroll": 0,
    "log": [],
    "job_id": None,
}


def _has_running_mlb_job() -> bool:
    with database.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'picks_mlb' AND status = 'running'"
        ).fetchone()
        return row is not None and row[0] > 0


@bp.route("/mlb/run", methods=["POST"])
def run_mlb():
    if _has_running_mlb_job():
        return jsonify({"status": "already_running"}), 409

    data = request.get_json() or {}

    params = {}
    for key in ("season", "min_edge", "allow_over", "allow_under",
                "max_picks", "max_total_line", "bankroll", "partido",
                "min_confidence", "allow_runline", "allow_moneyline", "allow_f5"):
        if key in data and data[key] is not None:
            params[key] = data[key]

    _state["generating"] = True
    _state["picks"] = []
    _state["log"] = []
    _state["timestamp"] = None

    job_id = JobManager.submit("picks_mlb", params)
    _state["job_id"] = job_id

    return jsonify({"status": "started", "job_id": job_id})


@bp.route("/mlb/status", methods=["GET"])
def mlb_status():
    job_id = _state.get("job_id")
    result = None
    if job_id:
        job = JobManager.get_status(job_id)
        if job:
            _state["generating"] = job["status"] == "running"
            _state["log"] = job.get("log_tail", [])
            if job["status"] == "completed" and job.get("result"):
                result = job["result"]
                _state["picks"]    = result.get("picks", [])
                _state["picks_ml"] = result.get("picks_ml", [])
                _state["picks_rl"] = result.get("picks_rl", [])
                _state["picks_f5"] = result.get("picks_f5", [])
                _state["games"]    = result.get("games", [])
                _state["bankroll"] = result.get("bankroll", 0)
                _state["generating"] = False

    record = database.get_record()
    wins = record.get("WIN", {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    return jsonify({
        "generating": _state["generating"],
        "picks":      _state["picks"],
        "picks_ml":   _state.get("picks_ml", []),
        "picks_rl":   _state.get("picks_rl", []),
        "picks_f5":   _state.get("picks_f5", []),
        "games":      _state.get("games", []),
        "timestamp":  _state.get("timestamp"),
        "bankroll":   _state.get("bankroll", 0),
        "log":        _state.get("log", []),
        "record":     {"wins": wins, "losses": losses},
        "job_id":     job_id,
    })


@bp.route("/mlb/pending", methods=["GET"])
def mlb_pending():
    picks = database.get_pending()
    return jsonify({"picks": picks, "count": len(picks)})


@bp.route("/mlb/history", methods=["GET"])
def mlb_history():
    picks = database.get_pending_with_details()
    return jsonify({"picks": picks, "count": len(picks)})


def _update_state_from_result(result: dict):
    _state["picks"]    = result.get("picks", [])
    _state["picks_ml"] = result.get("picks_ml", [])
    _state["picks_rl"] = result.get("picks_rl", [])
    _state["picks_f5"] = result.get("picks_f5", [])
    _state["games"]    = result.get("games", [])
    _state["bankroll"] = result.get("bankroll", 0)
    _state["log"]      = []
    _state["generating"] = False
