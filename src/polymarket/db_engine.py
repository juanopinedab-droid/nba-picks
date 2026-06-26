import sqlite3
import os
import json
from datetime import datetime, timedelta

DB_PATH = "data/polymarket.db"


def _safe_json_loads(s: str, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else [])
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def setup():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT DEFAULT (datetime('now')),
                closed_at       TEXT,
                event_slug      TEXT NOT NULL,
                market_slug     TEXT NOT NULL,
                question        TEXT NOT NULL,
                token_id        TEXT,
                side            TEXT NOT NULL CHECK (side IN ('BUY', 'SELL')),
                shares          REAL NOT NULL,
                entry_price     REAL NOT NULL,
                current_price   REAL,
                exit_price      REAL,
                cost_usd        REAL NOT NULL,
                pnl_usd         REAL DEFAULT 0,
                pnl_pct         REAL DEFAULT 0,
                status          TEXT DEFAULT 'open' CHECK (status IN ('open', 'closed', 'cancelled')),
                our_prob        REAL,
                edge_at_entry   REAL,
                strategy        TEXT,
                rationale_json  TEXT,
                job_id          TEXT,
                closed_reason   TEXT,
                notes           TEXT
            );

            CREATE TABLE IF NOT EXISTS pm_bankroll_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT DEFAULT (datetime('now')),
                type            TEXT NOT NULL CHECK (type IN ('INITIAL', 'DEPOSIT', 'WITHDRAW', 'WIN', 'LOSS', 'PARTIAL_CLOSE')),
                amount          REAL NOT NULL,
                balance_after   REAL NOT NULL,
                position_id     INTEGER,
                note            TEXT,
                FOREIGN KEY (position_id) REFERENCES positions(id)
            );

            CREATE TABLE IF NOT EXISTS market_cache (
                key             TEXT PRIMARY KEY,
                value           TEXT NOT NULL,
                expires_at      TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS strategy_params (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT NOT NULL UNIQUE,
                strategy_type   TEXT NOT NULL DEFAULT 'meta_consensus',
                params_json     TEXT NOT NULL,
                is_active       INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS price_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id        TEXT NOT NULL,
                recorded_at     TEXT DEFAULT (datetime('now')),
                best_bid        REAL,
                best_ask        REAL,
                last_trade      REAL,
                volume_24h      REAL,
                spread          REAL
            );
            CREATE INDEX IF NOT EXISTS idx_price_history_token
                ON price_history(token_id, recorded_at);

            CREATE TABLE IF NOT EXISTS pm_settings (
                key             TEXT PRIMARY KEY,
                value           TEXT NOT NULL,
                updated_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS pm_ai_research (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at          TEXT DEFAULT (datetime('now')),
                question            TEXT NOT NULL,
                market_slug         TEXT,
                event_slug          TEXT,
                market_price        REAL,
                prompt_customization TEXT,
                fixed_data          TEXT,
                topics_json         TEXT,
                sub_reports_json    TEXT,
                fundamental_shift   REAL,
                rationale           TEXT,
                max_steps           INTEGER DEFAULT 3,
                top_reports_json    TEXT,
                visualizations_json TEXT,
                principal_model     TEXT DEFAULT 'deepseek-v4-pro',
                subagent_model      TEXT DEFAULT 'deepseek-v4-flash',
                max_subagents       INTEGER DEFAULT 9,
                status              TEXT DEFAULT 'completed' CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                round_number        INTEGER DEFAULT 0,
                conviction_score    REAL DEFAULT 0.0,
                max_rounds          INTEGER DEFAULT 5,
                viz_agents_spawned   INTEGER DEFAULT 0,
                max_visualizations   INTEGER DEFAULT 3,
                max_mispricing_calls INTEGER DEFAULT 2,
                mispricing_report_json TEXT,
                mispricing_calls    INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_ai_research_date
                ON pm_ai_research(created_at DESC);
        """)
        # Migrations for databases created before V5 ReAct refactor
        cols = [c[1] for c in conn.execute("PRAGMA table_info(pm_ai_research)").fetchall()]
        if "round_number" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN round_number INTEGER DEFAULT 0")
        if "conviction_score" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN conviction_score REAL DEFAULT 0.0")
        if "max_rounds" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_rounds INTEGER DEFAULT 5")
        if "viz_agents_spawned" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN viz_agents_spawned INTEGER DEFAULT 0")
        if "mispricing_report_json" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN mispricing_report_json TEXT")
        if "mispricing_calls" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN mispricing_calls INTEGER DEFAULT 0")
        if "max_visualizations" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_visualizations INTEGER DEFAULT 3")
        if "max_mispricing_calls" not in cols:
            conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_mispricing_calls INTEGER DEFAULT 2")
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict:
    if row is None:
        return {}
    return dict(row)


class PositionDB:

    @staticmethod
    def open_position(position: dict) -> int:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO positions (
                    event_slug, market_slug, question, token_id,
                    side, shares, entry_price, current_price,
                    cost_usd, our_prob, edge_at_entry,
                    strategy, rationale_json, job_id, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    position.get("event_slug", ""),
                    position.get("market_slug", ""),
                    position.get("question", ""),
                    position.get("token_id", ""),
                    position.get("side", "BUY"),
                    position.get("shares", 0.0),
                    position.get("entry_price", 0.0),
                    position.get("current_price", 0.0),
                    position.get("cost_usd", 0.0),
                    position.get("our_prob", 0.0),
                    position.get("edge_at_entry", 0.0),
                    position.get("strategy", ""),
                    position.get("rationale_json", "{}"),
                    position.get("job_id", ""),
                    position.get("notes", ""),
                ),
            )
            conn.commit()
            return cur.lastrowid

    @staticmethod
    def close_position(position_id: int, exit_price: float,
                       reason: str = "") -> dict:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?", (position_id,)
            ).fetchone()
            if not row:
                return {}
            pos = _row_to_dict(row)
            if pos["status"] != "open":
                return pos

            share_pnl = (exit_price - pos["entry_price"]) * pos["shares"]
            pnl_pct = ((exit_price / pos["entry_price"]) - 1) * 100 if pos["entry_price"] else 0

            conn.execute(
                """UPDATE positions
                   SET closed_at = datetime('now'),
                       exit_price = ?, pnl_usd = ?, pnl_pct = ?,
                       status = 'closed', closed_reason = ?
                   WHERE id = ?""",
                (exit_price, round(share_pnl, 2), round(pnl_pct, 2),
                 reason, position_id),
            )

            current_balance = BankrollDB._get_balance_raw(conn)
            log_type = "WIN" if share_pnl >= 0 else "LOSS"
            new_balance = round(current_balance + share_pnl, 2)
            # Store absolute value: the CASE WHEN in _get_balance_raw
            # negates LOSS entries, so we store positive amounts here.
            conn.execute(
                """INSERT INTO pm_bankroll_log
                   (type, amount, balance_after, position_id, note)
                   VALUES (?, ?, ?, ?, ?)""",
                (log_type, round(abs(share_pnl), 2), new_balance,
                 position_id, reason),
            )
            conn.commit()
            return {**pos, "exit_price": exit_price, "pnl_usd": round(share_pnl, 2),
                    "pnl_pct": round(pnl_pct, 2), "status": "closed",
                    "closed_reason": reason}

    @staticmethod
    def update_market_prices(positions: list[dict]) -> None:
        with get_conn() as conn:
            for p in positions:
                conn.execute(
                    "UPDATE positions SET current_price = ? WHERE id = ?",
                    (p.get("current_price", 0.0), p.get("id")),
                )
            conn.commit()

    @staticmethod
    def get_positions(status: str = "open", limit: int = 50) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = ? ORDER BY opened_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    @staticmethod
    def get_position(position_id: int) -> dict:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM positions WHERE id = ?", (position_id,)
            ).fetchone()
            return _row_to_dict(row)

    @staticmethod
    def get_pnl_summary() -> dict:
        with get_conn() as conn:
            open_positions = conn.execute(
                "SELECT COUNT(*) as cnt, SUM(cost_usd) as total_cost FROM positions WHERE status = 'open'"
            ).fetchone()
            closed_positions = conn.execute(
                "SELECT COUNT(*) as cnt, SUM(pnl_usd) as total_pnl FROM positions WHERE status = 'closed'"
            ).fetchone()
            balance = BankrollDB.get_balance()
            return {
                "open_count": open_positions["cnt"] or 0,
                "open_cost_usd": open_positions["total_cost"] or 0.0,
                "closed_count": closed_positions["cnt"] or 0,
                "closed_pnl_usd": closed_positions["total_pnl"] or 0.0,
                "balance": balance,
            }


class BankrollDB:

    @staticmethod
    def _get_balance_raw(conn: sqlite3.Connection) -> float:
        row = conn.execute(
            """SELECT COALESCE(SUM(CASE
                WHEN type IN ('INITIAL', 'DEPOSIT', 'WIN') THEN amount
                WHEN type IN ('WITHDRAW', 'LOSS') THEN -amount
                ELSE 0
            END), 0) as balance FROM pm_bankroll_log"""
        ).fetchone()
        return row["balance"] if row else 0.0

    @staticmethod
    def get_balance() -> float:
        with get_conn() as conn:
            return BankrollDB._get_balance_raw(conn)

    @staticmethod
    def get_setting(key: str, default: str = "") -> str:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM pm_settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    @staticmethod
    def set_setting(key: str, value: str) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO pm_settings (key, value, updated_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       updated_at = excluded.updated_at""",
                (key, str(value)),
            )
            conn.commit()

    @staticmethod
    def deposit(amount: float, note: str = "") -> dict:
        with get_conn() as conn:
            current = BankrollDB._get_balance_raw(conn)
            new_balance = round(current + amount, 2)
            cur = conn.execute(
                """INSERT INTO pm_bankroll_log (type, amount, balance_after, note)
                   VALUES ('DEPOSIT', ?, ?, ?)""",
                (round(amount, 2), new_balance, note),
            )
            conn.commit()
            return {"id": cur.lastrowid, "type": "DEPOSIT", "amount": amount,
                    "balance_after": new_balance}

    @staticmethod
    def withdraw(amount: float, note: str = "") -> dict:
        with get_conn() as conn:
            current = BankrollDB._get_balance_raw(conn)
            new_balance = round(current - amount, 2)
            cur = conn.execute(
                """INSERT INTO pm_bankroll_log (type, amount, balance_after, note)
                   VALUES ('WITHDRAW', ?, ?, ?)""",
                (round(amount, 2), new_balance, note),
            )
            conn.commit()
            return {"id": cur.lastrowid, "type": "WITHDRAW", "amount": amount,
                    "balance_after": new_balance}

    @staticmethod
    def init_bankroll(amount: float) -> None:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT COUNT(*) as cnt FROM pm_bankroll_log"
            ).fetchone()
            if existing["cnt"] == 0:
                conn.execute(
                    """INSERT INTO pm_bankroll_log (type, amount, balance_after, note)
                       VALUES ('INITIAL', ?, ?, 'Initial deposit')""",
                    (round(amount, 2), round(amount, 2)),
                )
                conn.commit()

    @staticmethod
    def get_history(limit: int = 50) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM pm_bankroll_log ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]


class MarketCache:

    @staticmethod
    def get(key: str) -> dict | None:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value, expires_at FROM market_cache WHERE key = ?",
                (key,),
            ).fetchone()
            if not row:
                return None
            if row["expires_at"] and row["expires_at"] < datetime.utcnow().isoformat():
                return None
            return json.loads(row["value"])

    @staticmethod
    def set(key: str, value: dict, ttl_seconds: int = 300) -> None:
        expires_at = None
        if ttl_seconds > 0:
            expires_at = (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat()
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO market_cache (key, value, expires_at, created_at)
                   VALUES (?, ?, ?, datetime('now'))
                   ON CONFLICT(key) DO UPDATE SET
                       value = excluded.value,
                       expires_at = excluded.expires_at,
                       created_at = excluded.created_at""",
                (key, json.dumps(value), expires_at),
            )
            conn.commit()

    @staticmethod
    def cleanup() -> int:
        with get_conn() as conn:
            cur = conn.execute(
                "DELETE FROM market_cache WHERE expires_at < datetime('now')"
            )
            conn.commit()
            return cur.rowcount


class PriceHistoryDB:

    @staticmethod
    def record(token_id: str, bid: float, ask: float,
               last: float, volume: float, spread: float) -> None:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO price_history (token_id, best_bid, best_ask,
                   last_trade, volume_24h, spread)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (token_id, bid, ask, last, volume, spread),
            )
            conn.commit()

    @staticmethod
    def get_history(token_id: str, days: int = 7) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM price_history
                   WHERE token_id = ?
                     AND recorded_at >= datetime('now', ?)
                   ORDER BY recorded_at ASC""",
                (token_id, f"-{days} days"),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]


class ResearchDB:

    @staticmethod
    def save(session: dict) -> int:
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO pm_ai_research (
                    question, market_slug, event_slug, market_price,
                    prompt_customization, fixed_data, topics_json,
                    sub_reports_json, fundamental_shift, rationale,
                    max_steps, top_reports_json, visualizations_json,
                    principal_model, subagent_model, max_subagents,
                    status, round_number, conviction_score, max_rounds,
                    viz_agents_spawned, max_visualizations, max_mispricing_calls
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.get("question", ""),
                    session.get("market_slug", ""),
                    session.get("event_slug", ""),
                    session.get("market_price"),
                    session.get("prompt_customization", ""),
                    session.get("fixed_data", ""),
                    json.dumps(session.get("topics", [])),
                    json.dumps(session.get("subagent_reports", [])),
                    session.get("fundamental_shift", 0.0),
                    session.get("rationale", ""),
                    session.get("max_steps", 3),
                    json.dumps(session.get("top_reports", [])),
                    json.dumps(session.get("visualizations", [])),
                    session.get("principal_model", "deepseek-v4-pro"),
                    session.get("subagent_model", "deepseek-v4-flash"),
                    session.get("max_subagents", 9),
                    session.get("status", "completed"),
                    session.get("round_number", 0),
                    session.get("conviction_score", 0.0),
                    session.get("max_rounds", 5),
                    session.get("viz_agents_spawned", 0),
                    session.get("max_visualizations", 3),
                    session.get("max_mispricing_calls", 2),
                ),
            )
            conn.commit()
            return cur.lastrowid

    @staticmethod
    def update(session_id: int, data: dict) -> None:
        with get_conn() as conn:
            sets = []
            params = []
            field_map = {
                "topics": ("topics_json", lambda v: json.dumps(v)),
                "subagent_reports": ("sub_reports_json", lambda v: json.dumps(v)),
                "fundamental_shift": ("fundamental_shift", lambda v: v),
                "rationale": ("rationale", lambda v: v),
                "status": ("status", lambda v: v),
                "top_reports": ("top_reports_json", lambda v: json.dumps(v)),
                "visualizations": ("visualizations_json", lambda v: json.dumps(v)),
                "principal_model": ("principal_model", lambda v: v),
                "subagent_model": ("subagent_model", lambda v: v),
                "max_subagents": ("max_subagents", lambda v: v),
                "round_number": ("round_number", lambda v: v),
                "conviction_score": ("conviction_score", lambda v: v),
                "max_rounds": ("max_rounds", lambda v: v),
                "max_visualizations": ("max_visualizations", lambda v: v),
                "max_mispricing_calls": ("max_mispricing_calls", lambda v: v),
                "viz_agents_spawned": ("viz_agents_spawned", lambda v: v),
                "mispricing_report": ("mispricing_report_json", lambda v: json.dumps(v) if v else None),
                "mispricing_calls": ("mispricing_calls", lambda v: v),
                "markdown_report": ("rationale", lambda v: v),
            }
            for key, (col, transform) in field_map.items():
                if key in data:
                    sets.append(f"{col} = ?")
                    params.append(transform(data[key]))
            if not sets:
                return
            params.append(session_id)
            conn.execute(
                f"UPDATE pm_ai_research SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            conn.commit()

    @staticmethod
    def get_history(limit: int = 30) -> list[dict]:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT id, created_at, question, market_slug, event_slug,
                          market_price, fundamental_shift, status,
                          conviction_score, round_number
                   FROM pm_ai_research
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]

    @staticmethod
    def get_session(session_id: int) -> dict | None:
        with get_conn() as conn:
            cols = [c[1] for c in conn.execute("PRAGMA table_info(pm_ai_research)").fetchall()]
            if "max_steps" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_steps INTEGER DEFAULT 3")
                conn.commit()
            if "top_reports_json" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN top_reports_json TEXT")
                conn.commit()
            if "visualizations_json" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN visualizations_json TEXT")
                conn.commit()
            if "principal_model" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN principal_model TEXT DEFAULT 'deepseek-v4-pro'")
                conn.commit()
            if "subagent_model" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN subagent_model TEXT DEFAULT 'deepseek-v4-flash'")
                conn.commit()
            if "max_subagents" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_subagents INTEGER DEFAULT 9")
                conn.commit()
            if "round_number" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN round_number INTEGER DEFAULT 0")
                conn.commit()
            if "conviction_score" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN conviction_score REAL DEFAULT 0.0")
                conn.commit()
            if "max_rounds" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_rounds INTEGER DEFAULT 5")
                conn.commit()
            if "mispricing_report_json" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN mispricing_report_json TEXT")
                conn.commit()
            if "mispricing_calls" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN mispricing_calls INTEGER DEFAULT 0")
                conn.commit()
            if "max_visualizations" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_visualizations INTEGER DEFAULT 3")
                conn.commit()
            if "max_mispricing_calls" not in cols:
                conn.execute("ALTER TABLE pm_ai_research ADD COLUMN max_mispricing_calls INTEGER DEFAULT 2")
                conn.commit()
            row = conn.execute(
                "SELECT * FROM pm_ai_research WHERE id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            session = _row_to_dict(row)
            session["topics"] = json.loads(session.get("topics_json", "[]"))
            session["subagent_reports"] = json.loads(session.get("sub_reports_json", "[]"))
            session["top_reports"] = json.loads(session.get("top_reports_json", "[]"))
            session["visualizations"] = _safe_json_loads(session.get("visualizations_json", "[]"))
            session["mispricing_report"] = _safe_json_loads(session.get("mispricing_report_json", "{}"))
            session["conviction_score"] = session.get("conviction_score", 0.0)
            session["round_number"] = session.get("round_number", 0)
            session["max_rounds"] = session.get("max_rounds", 5)
            return session
