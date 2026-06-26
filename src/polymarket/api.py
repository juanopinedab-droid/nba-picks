import json
from flask import Blueprint, request, jsonify, Response

pm_bp = Blueprint("polymarket", __name__)


@pm_bp.route("/tags")
def get_tags():
    from . import collector
    return jsonify({"tags": collector.fetch_tags()})


@pm_bp.route("/markets")
def get_markets():
    from . import collector
    tag = request.args.get("tag", "")
    limit = int(request.args.get("limit", 50))
    min_volume = float(request.args.get("min_volume", 0))
    markets = collector.fetch_events(tag=tag, limit=limit, min_volume=min_volume)
    return jsonify({"markets": markets})


@pm_bp.route("/scanner", methods=["POST"])
def run_scanner():
    from ..service.orchestrator import JobManager
    params = request.get_json() or {}
    job_id = JobManager.submit("picks_polymarket", params)
    return jsonify({"job_id": job_id, "status": "pending"})


@pm_bp.route("/scanner/status/<job_id>")
def scanner_status(job_id):
    from ..service.orchestrator import JobManager
    status = JobManager.get_status(job_id)
    return jsonify(status)


@pm_bp.route("/portfolio")
def get_portfolio():
    from .portfolio import get_portfolio_summary
    try:
        return jsonify(get_portfolio_summary())
    except Exception:
        return jsonify({"positions": [], "open_count": 0,
                        "total_cost_usd": 0, "current_value_usd": 0,
                        "unrealized_pnl_usd": 0, "unrealized_pnl_pct": 0})


@pm_bp.route("/portfolio/open", methods=["POST"])
def open_position_route():
    from .portfolio import open_position
    from .db_engine import BankrollDB

    data = request.get_json() or {}
    bankroll = BankrollDB.get_balance()
    result = open_position(
        market=data["market"],
        analysis=data["analysis"],
        bankroll_usd=bankroll,
        fraction=data.get("fraction", 0.25)
    )
    return jsonify(result)


@pm_bp.route("/portfolio/close", methods=["POST"])
def close_position_route():
    from .portfolio import close_position
    data = request.get_json() or {}
    result = close_position(data["position_id"],
                           reason=data.get("reason", "manual"))
    return jsonify(result)


@pm_bp.route("/history")
def get_history():
    from .db_engine import PositionDB
    return jsonify({
        "positions": PositionDB.get_positions(status="closed"),
        "pnl_summary": PositionDB.get_pnl_summary()
    })


@pm_bp.route("/treasury")
def get_treasury():
    from .db_engine import BankrollDB
    balance = BankrollDB.get_balance()
    settings = {
        k: BankrollDB.get_setting(k)
        for k in ["bankroll_usd", "min_edge", "min_volume",
                   "min_liquidity", "max_days_to_resolution", "stake_fraction"]
    }
    return jsonify({"balance_usd": balance, "settings": settings})


@pm_bp.route("/treasury/deposit", methods=["POST"])
def deposit():
    from .db_engine import BankrollDB
    data = request.get_json() or {}
    result = BankrollDB.deposit(data["amount"], data.get("note", ""))
    return jsonify(result)


@pm_bp.route("/treasury/withdraw", methods=["POST"])
def withdraw():
    from .db_engine import BankrollDB
    data = request.get_json() or {}
    result = BankrollDB.withdraw(data["amount"], data.get("note", ""))
    return jsonify(result)


@pm_bp.route("/treasury/history")
def treasury_history():
    from .db_engine import BankrollDB
    return jsonify({"history": BankrollDB.get_history()})


@pm_bp.route("/laboratory/strategies", methods=["GET", "POST"])
def strategies():
    if request.method == "POST":
        from .db_engine import get_conn
        data = request.get_json() or {}
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO strategy_params "
                "(name, strategy_type, params_json, is_active, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now'))",
                (data["name"], data.get("strategy_type", "meta_consensus"),
                 json.dumps(data.get("params", {})), data.get("is_active", 0))
            )
            conn.commit()
        return jsonify({"status": "saved"})

    from .db_engine import get_conn
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM strategy_params ORDER BY updated_at DESC"
        ).fetchall()
    return jsonify({"strategies": [dict(r) for r in rows]})


@pm_bp.route("/laboratory/strategies/<int:strategy_id>", methods=["GET", "PUT", "DELETE"])
def strategy_detail(strategy_id):
    from .db_engine import get_conn

    if request.method == "DELETE":
        with get_conn() as conn:
            conn.execute("DELETE FROM strategy_params WHERE id = ?", (strategy_id,))
            conn.commit()
        return jsonify({"status": "deleted"})

    if request.method == "PUT":
        data = request.get_json() or {}
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_params WHERE id = ?", (strategy_id,)
            ).fetchone()
        if not row:
            return jsonify({"error": "Strategy not found"}), 404
        with get_conn() as conn:
            conn.execute(
                "UPDATE strategy_params SET name = ?, strategy_type = ?, "
                "params_json = ?, is_active = ?, updated_at = datetime('now') "
                "WHERE id = ?",
                (data.get("name", row["name"]),
                 data.get("strategy_type", row["strategy_type"]),
                 json.dumps(data.get("params", json.loads(row["params_json"]))),
                 data.get("is_active", row["is_active"]),
                 strategy_id)
            )
            conn.commit()
        return jsonify({"status": "updated"})

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_params WHERE id = ?", (strategy_id,)
        ).fetchone()
    if not row:
        return jsonify({"error": "Strategy not found"}), 404
    return jsonify({"strategy": dict(row)})


@pm_bp.route("/laboratory/backtest", methods=["POST"])
def run_backtest():
    return jsonify({
        "status": "not_implemented",
        "message": "Backtesting de estrategias disponible en v2"
    }), 501


@pm_bp.route("/price-history")
def price_history():
    slug = request.args.get("slug", "")
    days = int(request.args.get("days", 7))
    from . import collector
    history = collector.fetch_price_history(slug, days)
    return jsonify({"history": history})


def _save_research_session(question: str, price: float,
                           customization: str, fixed_data: str,
                           result: dict, max_steps: int = 3,
                           principal_model: str = "deepseek-v4-pro",
                           subagent_model: str = "deepseek-v4-flash",
                           max_subagents: int = 9) -> int:
    from .db_engine import ResearchDB
    return ResearchDB.save({
        "question": question,
        "market_price": price,
        "prompt_customization": customization,
        "fixed_data": fixed_data,
        "topics": result.get("topics", []),
        "subagent_reports": result.get("subagent_reports", []),
        "fundamental_shift": result.get("fundamental_shift", 0.0),
        "rationale": result.get("rationale", ""),
        "status": "completed",
        "max_steps": max_steps,
        "top_reports": result.get("top_reports", []),
        "visualizations": result.get("visualizations", []),
        "mispricing_report": result.get("mispricing_report", {}),
        "principal_model": principal_model,
        "subagent_model": subagent_model,
        "max_subagents": max_subagents,
        "conviction_score": result.get("conviction_score", 0.0),
    })


@pm_bp.route("/ai-research/<int:session_id>/cancel", methods=["POST"])
def ai_research_cancel(session_id: int):
    from .ai_orchestrator import cancel_session
    ok = cancel_session(session_id)
    return {"cancelled": ok, "session_id": session_id}


@pm_bp.route("/ai-research/<int:session_id>/resume", methods=["POST"])
def ai_research_resume(session_id: int):
    from .db_engine import ResearchDB
    from .ai_orchestrator import (
        run_react_orchestrator,
        register_cancel_event,
        _cleanup_cancel_event
    )

    session = ResearchDB.get_session(session_id)
    if not session:
        return {"error": "Session not found"}, 404

    existing_reports = session.get("subagent_reports") or []
    if not existing_reports:
        return {"error": "No partial reports to resume from"}, 400

    ResearchDB.update(session_id, {"status": "running"})

    question = session.get("question", "")
    context = ""
    price = session.get("market_price", 0.5)
    customization = session.get("prompt_customization", "")
    fixed_data = session.get("fixed_data", "")
    max_steps = session.get("max_steps", 3)
    principal_model = session.get("principal_model", "deepseek-v4-pro")
    subagent_model = session.get("subagent_model", "deepseek-v4-flash")
    max_subagents = session.get("max_subagents", 9)
    max_rounds = session.get("max_rounds", 5)
    max_visualizations = session.get("max_visualizations", 0)
    max_mispricing_calls = session.get("max_mispricing_calls", 0)

    existing_state = {
        "subagent_reports": existing_reports,
        "round_number": session.get("round_number", 0),
        "agents_spawned": len(existing_reports),
        "viz_calls_made": session.get("viz_agents_spawned", session.get("viz_calls_made", 0)),
        "mispricing_calls": session.get("mispricing_calls", 0),
    }

    def generate():
        from queue import Queue
        from threading import Thread
        import asyncio as _asyncio

        event_id = 0
        result_queue = Queue()
        cancel_event = register_cancel_event(session_id)

        event_id += 1
        yield f"id: {event_id}\nevent: session_created\ndata: {json.dumps({'session_id': session_id, 'resumed': True})}\n\n"

        def run_orchestrator():
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)

            def on_event(event_type: str, payload: dict):
                result_queue.put((event_type, payload))

            try:
                result = loop.run_until_complete(
                    run_react_orchestrator(
                        question=question,
                        context=context,
                        price=price,
                        user_prompt_customization=customization,
                        fixed_data_block=fixed_data,
                        on_event=on_event,
                        max_steps=max_steps,
                        principal_model=principal_model,
                        subagent_model=subagent_model,
                        max_subagents=max_subagents,
                        max_rounds=max_rounds,
                        min_visualizations=min_visualizations,
                        min_mispricing_calls=min_mispricing_calls,
                        force_top_reports=force_top_reports,
                        cancel_event=cancel_event,
                        session_id=session_id,
                        existing_state=existing_state
                    )
                )
                result_queue.put(("_result", result))
            except Exception as e:
                result_queue.put(("_error", str(e)))
            finally:
                loop.close()

        Thread(target=run_orchestrator, daemon=True).start()

        while True:
            try:
                item = result_queue.get(timeout=15)
            except Exception:
                event_id += 1
                yield f"id: {event_id}\nevent: ping\ndata: {{}}\n\n"
                continue

            typ, payload = item
            if typ == "_result":
                try:
                    status = "failed" if payload.get("cancelled") else "completed"
                    ResearchDB.update(session_id, {
                        "subagent_reports": payload.get("subagent_reports", []),
                        "status": status,
                        "visualizations": payload.get("visualizations", []),
                        "mispricing_report": payload.get("mispricing_report", {}),
                        "top_reports": payload.get("top_reports", []),
                        "principal_model": principal_model,
                        "subagent_model": subagent_model,
                        "max_subagents": max_subagents,
                        "max_rounds": max_rounds,
                        "min_visualizations": min_visualizations,
                        "min_mispricing_calls": min_mispricing_calls,
                        "round_number": payload.get("rounds", 0),
                        "conviction_score": payload.get("conviction_score", 0.0),
                        "markdown_report": payload.get("markdown_report", ""),
                        "fundamental_shift": payload.get("conviction_score", 0.0),
                        "rationale": payload.get("markdown_report", ""),
                    })
                except Exception:
                    ResearchDB.update(session_id, {"status": "failed"})
                event_id += 1
                yield f"id: {event_id}\nevent: done\ndata: {json.dumps({'session_id': session_id})}\n\n"
                _cleanup_cancel_event(session_id)
                return
            elif typ == "_error":
                try:
                    ResearchDB.update(session_id, {"status": "failed"})
                except Exception:
                    pass
                yield f"event: error\ndata: {json.dumps({'error': payload})}\n\n"
                _cleanup_cancel_event(session_id)
                return
            else:
                event_id += 1
                yield f"id: {event_id}\nevent: {typ}\ndata: {json.dumps(payload)}\n\n"

        _cleanup_cancel_event(session_id)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@pm_bp.route("/ai-research/stream", methods=["POST"])
def ai_research_stream():
    data = request.get_json() or {}
    question = data.get("question", "")
    context = data.get("context", "")
    price = float(data.get("price", 0.5))
    customization = data.get("prompt_customization", "")
    fixed_data = data.get("fixed_data", "")
    max_steps = int(data.get("max_steps", 3))
    max_steps = max(0, min(20, max_steps))
    principal_model = data.get("principal_model", "deepseek-v4-pro")
    subagent_model = data.get("subagent_model", "deepseek-v4-flash")
    max_subagents = int(data.get("max_subagents", 9))
    max_subagents = max(3, min(15, max_subagents))
    max_rounds = int(data.get("max_rounds", 5))
    max_rounds = max(1, min(10, max_rounds))
    min_visualizations = int(data.get("min_visualizations", 0))
    min_visualizations = max(0, min(10, min_visualizations))
    min_mispricing_calls = int(data.get("min_mispricing_calls", 0))
    min_mispricing_calls = max(0, min(10, min_mispricing_calls))
    force_top_reports = bool(data.get("force_top_reports", True))

    def generate():
        from queue import Queue
        from threading import Thread
        import asyncio as _asyncio
        from .ai_orchestrator import (
            run_react_orchestrator,
            register_cancel_event,
            _cleanup_cancel_event
        )
        from .db_engine import ResearchDB

        event_id = 0
        result_queue = Queue()

        try:
            session_id = ResearchDB.save({
                "question": question,
                "market_price": price,
                "prompt_customization": customization,
                "fixed_data": fixed_data,
                "max_steps": max_steps,
                "principal_model": principal_model,
                "subagent_model": subagent_model,
                "max_subagents": max_subagents,
                "max_rounds": max_rounds,
                "min_visualizations": min_visualizations,
                "min_mispricing_calls": min_mispricing_calls,
                "status": "running",
            })
        except Exception:
            session_id = 0

        cancel_event = register_cancel_event(session_id) if session_id else None

        event_id += 1
        yield f"id: {event_id}\nevent: session_created\ndata: {json.dumps({'session_id': session_id})}\n\n"

        def run_orchestrator():
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)

            def on_event(event_type: str, payload: dict):
                result_queue.put((event_type, payload))

            try:
                result = loop.run_until_complete(
                    run_react_orchestrator(
                        question=question,
                        context=context,
                        price=price,
                        user_prompt_customization=customization,
                        fixed_data_block=fixed_data,
                        on_event=on_event,
                        max_steps=max_steps,
                        principal_model=principal_model,
                        subagent_model=subagent_model,
                        max_subagents=max_subagents,
                        max_rounds=max_rounds,
                        min_visualizations=min_visualizations,
                        min_mispricing_calls=min_mispricing_calls,
                        force_top_reports=force_top_reports,
                        cancel_event=cancel_event,
                        session_id=session_id
                    )
                )
                result_queue.put(("_result", result))
            except Exception as e:
                result_queue.put(("_error", str(e)))
            finally:
                loop.close()

        Thread(target=run_orchestrator, daemon=True).start()

        while True:
            try:
                item = result_queue.get(timeout=15)
            except Exception:
                event_id += 1
                yield f"id: {event_id}\nevent: ping\ndata: {{}}\n\n"
                continue

            typ, payload = item
            if typ == "_result":
                try:
                    status = "failed" if payload.get("cancelled") else "completed"
                    ResearchDB.update(session_id, {
                        "subagent_reports": payload.get("subagent_reports", []),
                        "status": status,
                        "visualizations": payload.get("visualizations", []),
                        "mispricing_report": payload.get("mispricing_report", {}),
                        "principal_model": principal_model,
                        "subagent_model": subagent_model,
                        "max_subagents": max_subagents,
                        "max_rounds": max_rounds,
                        "min_visualizations": min_visualizations,
                        "min_mispricing_calls": min_mispricing_calls,
                        "round_number": payload.get("rounds", 0),
                        "conviction_score": payload.get("conviction_score", 0.0),
                        "markdown_report": payload.get("markdown_report", ""),
                        "fundamental_shift": payload.get("conviction_score", 0.0),
                        "rationale": payload.get("markdown_report", ""),
                    })
                except Exception:
                    ResearchDB.update(session_id, {"status": "failed"})
                event_id += 1
                yield f"id: {event_id}\nevent: done\ndata: {json.dumps({'session_id': session_id})}\n\n"
                _cleanup_cancel_event(session_id)
                return
            elif typ == "_error":
                try:
                    ResearchDB.update(session_id, {"status": "failed"})
                except Exception:
                    pass
                yield f"event: error\ndata: {json.dumps({'error': payload})}\n\n"
                _cleanup_cancel_event(session_id)
                return
            else:
                event_id += 1
                yield f"id: {event_id}\nevent: {typ}\ndata: {json.dumps(payload)}\n\n"

        _cleanup_cancel_event(session_id)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        }
    )


@pm_bp.route("/ai-research/history")
def ai_research_history():
    from .db_engine import ResearchDB
    limit = request.args.get("limit", 30, type=int)
    return jsonify({"sessions": ResearchDB.get_history(limit)})


@pm_bp.route("/ai-research/<int:session_id>")
def ai_research_session(session_id: int):
    from .db_engine import ResearchDB
    session = ResearchDB.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    return jsonify({"session": session})


import os
import base64
from cryptography.fernet import Fernet

KEYS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "keys.json.enc")


def _get_fernet() -> Fernet:
    key = os.getenv("FERNET_KEY")
    if not key:
        key = base64.urlsafe_b64encode(os.urandom(32)).decode()
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"), "a") as f:
            f.write(f"\nFERNET_KEY={key}\n")
        os.environ["FERNET_KEY"] = key
    return Fernet(key.encode() if isinstance(key, str) else key)


@pm_bp.route("/keys", methods=["GET"])
def get_keys():
    try:
        if not os.path.exists(KEYS_FILE):
            return jsonify({"keys": {}, "configured": False})
        with open(KEYS_FILE, "rb") as f:
            encrypted = f.read()
        f = _get_fernet()
        decrypted = json.loads(f.decrypt(encrypted).decode())
        return jsonify({
            "keys": {k: "***" + v[-4:] if v else "" for k, v in decrypted.items()},
            "configured": bool(decrypted)
        })
    except Exception:
        return jsonify({"keys": {}, "configured": False})


@pm_bp.route("/keys", methods=["POST"])
def save_keys():
    try:
        data = request.get_json() or {}
        keys_to_save = {}
        for k in ["DEEPSEEK_API_KEY"]:
            if data.get(k):
                keys_to_save[k] = data[k]
        f = _get_fernet()
        encrypted = f.encrypt(json.dumps(keys_to_save).encode())
        with open(KEYS_FILE, "wb") as fh:
            fh.write(encrypted)
        if "DEEPSEEK_API_KEY" in keys_to_save:
            os.environ["DEEPSEEK_API_KEY"] = keys_to_save["DEEPSEEK_API_KEY"]
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
