from flask import Blueprint, jsonify, request

from ...core import database, config
from ...nba import collector
from ...service.orchestrator import JobManager

bp = Blueprint("picks", __name__)

_state = {
    "generating": False,
    "picks": [],
    "props": [],
    "games": [],
    "timestamp": None,
    "bankroll": 0,
    "log": [],
    "job_id": None,
}


def _has_running_nba_job() -> bool:
    with database.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE type = 'picks_nba' AND status = 'running'"
        ).fetchone()
        return row is not None and row[0] > 0


@bp.route("/picks/nba", methods=["POST"])
def generate_nba():
    if _has_running_nba_job():
        return jsonify({"status": "already_running"}), 409
    data = request.json or {}
    season = data.get("season", "").strip()
    min_edge = data.get("min_edge")
    fetch_props = data.get("fetch_props")
    bankroll_val = data.get("bankroll")
    if season:
        collector.set_season(season)
    if min_edge is not None:
        config.set_min_edge(float(min_edge))
    if fetch_props is not None:
        config.set_fetch_props(bool(fetch_props))
    if bankroll_val is not None:
        config.set_bankroll(float(bankroll_val))

    params = {}
    if season:
        params["season"] = season
    if min_edge is not None:
        params["min_edge"] = min_edge
    if fetch_props is not None:
        params["fetch_props"] = fetch_props
    if bankroll_val is not None:
        params["bankroll"] = bankroll_val

    job_id = JobManager.submit("picks_nba", params)
    _state["generating"] = True
    _state["log"] = [f"Iniciando (season: {collector.get_active_season()})..."]
    _state["job_id"] = job_id

    return jsonify({"status": "started", "job_id": job_id, "season": collector.get_active_season()})


@bp.route("/picks/status")
def picks_status():
    job_id = _state.get("job_id")
    result = None
    if job_id:
        job = JobManager.get_status(job_id)
        if job:
            _state["generating"] = job["status"] == "running"
            _state["log"] = job.get("log_tail", [])
            if job["status"] == "completed" and job.get("result"):
                result = job["result"]
                _state.update({
                    "picks": result.get("picks", []),
                    "props": result.get("props", []),
                    "games": result.get("games", []),
                    "bankroll": result.get("bankroll", 0),
                    "generating": False,
                })

    record = database.get_record()
    wins = record.get("WIN", {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    return jsonify({
        "generating": _state["generating"],
        "season": collector.get_active_season(),
        "min_edge": config.get_min_edge(),
        "fetch_props": config.get_fetch_props(),
        "bankroll": config.get_bankroll(),
        "timestamp": _state["timestamp"],
        "picks": _state["picks"],
        "props": _state["props"],
        "games": _state["games"],
        "log": _state["log"],
        "record": {"wins": wins, "losses": losses},
        "job_id": job_id,
    })
