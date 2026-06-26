from flask import Blueprint, jsonify, request

from ...core import database
from ...service.orchestrator import JobManager

bp = Blueprint("football", __name__)

_state = {
    "generating": False,
    "picks": [],
    "games": [],
    "timestamp": None,
    "log": [],
    "job_id": None,
}


def _has_running_football_job() -> bool:
    with database.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'picks_football' AND status = 'running'"
        ).fetchone()
        return row is not None and row[0] > 0


@bp.route("/picks/football", methods=["POST"])
def generate_football():
    if _has_running_football_job():
        return jsonify({"status": "already_running"}), 409

    data = request.get_json() or {}

    params = {}
    for key in ("min_edge", "min_prob_win", "min_prob_draw",
                "min_prob_ou", "min_prob_btts", "partido",
                "allow_win", "allow_draw", "allow_over", "allow_under", "allow_btts"):
        if key in data and data[key] is not None:
            params[key] = data[key]

    _state["generating"] = True
    _state["log"] = ["Iniciando EPL..."]
    job_id = JobManager.submit("picks_football", params)
    _state["job_id"] = job_id
    return jsonify({"status": "started", "job_id": job_id})


@bp.route("/picks/football/status")
def football_status():
    job_id = _state.get("job_id")
    if job_id:
        job = JobManager.get_status(job_id)
        if job:
            _state["generating"] = job["status"] == "running"
            _state["log"] = job.get("log_tail", [])
            if job["status"] == "completed" and job.get("result"):
                result = job["result"]
                _state.update({
                    "picks": result.get("picks", []),
                    "games": result.get("games", []),
                    "generating": False,
                })

    return jsonify({
        "generating": _state["generating"],
        "timestamp": _state["timestamp"],
        "picks": _state["picks"],
        "games": _state["games"],
        "log": _state["log"],
        "job_id": job_id,
    })
