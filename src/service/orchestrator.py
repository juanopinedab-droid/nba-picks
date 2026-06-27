import json
import threading
import traceback
import uuid
from datetime import datetime

from ..core import database
from ..core.types import JobContext

_JOB_REGISTRY = {
    "picks_nba":      "src.service.picks_nba",
    "picks_football": "src.service.picks_football",
    "picks_mlb":      "src.service.picks_mlb",
    "picks_polymarket":"src.service.picks_polymarket",
    "resolve":        "src.service.resolver",
    "backtest":       "src.service.backtest",
    "calibrate":      "src.service.calibrate",
    "dummy":          "src.service.dummy",
}


class JobManager:
    _lock = threading.Lock()
    _running: dict[str, threading.Thread] = {}

    @classmethod
    def submit(cls, job_type: str, params: dict | None = None) -> str:
        if job_type not in _JOB_REGISTRY:
            raise ValueError(f"Tipo de job desconocido: {job_type}")

        job_id = str(uuid.uuid4())[:12]
        with database.get_conn() as conn:
            conn.execute(
                "INSERT INTO jobs (id, type, status, params_json, created_at) "
                "VALUES (?, ?, 'pending', ?, ?)",
                (job_id, job_type, json.dumps(params) if params else None, datetime.now().isoformat())
            )
            conn.commit()

        thread = threading.Thread(target=cls._execute, args=(job_id, job_type, params), daemon=True)
        with cls._lock:
            cls._running[job_id] = thread
        thread.start()
        return job_id

    @classmethod
    def get_status(cls, job_id: str) -> dict:
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT id, type, status, progress, log, result_json, error, created_at, started_at, finished_at "
                "FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if not row:
            return {"error": "Job no encontrado"}
        log_raw = row[4] or ""
        return {
            "id":         row[0],
            "type":       row[1],
            "status":     row[2],
            "progress":   row[3] or 0,
            "log_tail":   _tail(log_raw, 30),
            "result":     json.loads(row[5]) if row[5] else None,
            "error":      row[6],
            "created_at": row[7],
            "started_at": row[8],
            "finished_at":row[9],
        }

    @classmethod
    def get_history(cls, limit: int = 20, job_type: str | None = None) -> list[dict]:
        with database.get_conn() as conn:
            if job_type:
                rows = conn.execute(
                    "SELECT id, type, status, progress, created_at, finished_at "
                    "FROM jobs WHERE type = ? ORDER BY created_at DESC LIMIT ?",
                    (job_type, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, type, status, progress, created_at, finished_at "
                    "FROM jobs ORDER BY created_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
        return [
            {
                "id": r[0], "type": r[1], "status": r[2],
                "progress": r[3] or 0, "created_at": r[4], "finished_at": r[5],
            }
            for r in rows
        ]

    @classmethod
    def cleanup_cache(cls):
        database.get_conn().execute("DELETE FROM cache WHERE expires_at < datetime('now')")

    @classmethod
    def _execute(cls, job_id: str, job_type: str, params: dict | None):
        ctx = JobContext(job_id=job_id)
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), job_id)
                )
                conn.commit()

            module_name = _JOB_REGISTRY[job_type]
            mod = __import__(module_name, fromlist=["execute"])
            result = mod.execute(params or {}, ctx)

            ctx.set_progress(1.0)
            ctx._flush()
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'completed', finished_at = ?, result_json = ? WHERE id = ?",
                    (datetime.now().isoformat(), json.dumps(result), job_id)
                )
                conn.commit()

        except Exception:
            err = traceback.format_exc()
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE jobs SET status = 'failed', finished_at = ?, error = ?, log = ? WHERE id = ?",
                    (datetime.now().isoformat(), err, _join_log(ctx.log), job_id)
                )
                conn.commit()

        finally:
            with cls._lock:
                cls._running.pop(job_id, None)


def _tail(text: str, lines: int) -> list[str]:
    parts = text.split("\n")
    return [l for l in parts if l][-lines:]


def _join_log(log: list[str]) -> str:
    return "\n".join(log[-200:])
