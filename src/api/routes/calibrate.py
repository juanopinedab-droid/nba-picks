from flask import Blueprint, jsonify, request

from ...service.orchestrator import JobManager

bp = Blueprint("calibrate", __name__)
_last_job_id: str | None = None


@bp.route("/calibrate", methods=["POST"])
def run_calibrate():
    global _last_job_id
    data = request.json or {}
    apply_changes = data.get("apply", False)
    params = {"apply": apply_changes}
    _last_job_id = JobManager.submit("calibrate", params)
    return jsonify({"status": "started", "job_id": _last_job_id})


@bp.route("/calibrate/status")
def calibrate_status():
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
