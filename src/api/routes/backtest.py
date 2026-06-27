from flask import Blueprint, jsonify, request

from ...service.orchestrator import JobManager

bp = Blueprint("backtest", __name__)
_last_job_id: str | None = None


@bp.route("/backtest", methods=["POST"])
def run_backtest():
    global _last_job_id
    data = request.json or {}
    seasons = data.get("seasons", 2)
    download_only = data.get("download_only", False)
    params = {"seasons": seasons, "download_only": download_only}
    _last_job_id = JobManager.submit("backtest", params)
    return jsonify({"status": "started", "job_id": _last_job_id})


@bp.route("/backtest/status")
def backtest_status():
    global _last_job_id
    job_id = request.args.get("job_id") or _last_job_id
    if not job_id:
        return jsonify({"error": "Falta job_id"}), 400
    job = JobManager.get_status(job_id)
    if job.get("error"):
        return jsonify(job), 404
    return jsonify({
        "running": job["status"] == "running" or job["status"] == "pending",
        "done": job["status"] in ("completed", "failed"),
        "output": job.get("result", {}).get("output", "") if job.get("result") else "",
        "progress": job.get("progress", 0),
        "error": job.get("error"),
    })
