from flask import Blueprint, request, jsonify
from ...service.orchestrator import JobManager

bp = Blueprint("jobs", __name__)


@bp.route("/jobs", methods=["POST"])
def submit_job():
    data = request.get_json(silent=True) or {}
    job_type = data.get("type")
    if not job_type:
        return jsonify({"error": "Falta el campo 'type'"}), 400
    try:
        job_id = JobManager.submit(job_type, data.get("params"))
        return jsonify({"job_id": job_id}), 201
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/jobs", methods=["GET"])
def list_jobs():
    job_type = request.args.get("type")
    limit = int(request.args.get("limit", 20))
    jobs = JobManager.get_history(limit=limit, job_type=job_type)
    return jsonify(jobs)


@bp.route("/jobs/<job_id>", methods=["GET"])
def get_job(job_id):
    status = JobManager.get_status(job_id)
    if status.get("error") == "Job no encontrado":
        return jsonify(status), 404
    return jsonify(status)
