import sqlite3
import json
import os
from datetime import date, datetime, timedelta

DB_PATH = "data/picks.db"


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def setup():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS picks (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                date           TEXT,
                game           TEXT,
                bet_type       TEXT,
                selection      TEXT,
                odds           INTEGER,
                our_prob       REAL,
                implied_prob   REAL,
                edge           REAL,
                confidence     TEXT,
                stake_cop      REAL DEFAULT 0,
                result         TEXT DEFAULT 'PENDING',
                profit_cop     REAL DEFAULT 0,
                sport          TEXT DEFAULT 'nba',
                commence_time  TEXT,
                closing_odds   INTEGER,
                clv            REAL,
                feedback_notes TEXT,
                job_id         TEXT,
                resolved_at    TEXT,
                actual_score   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bankroll_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT DEFAULT (datetime('now')),
                type            TEXT NOT NULL,
                amount          REAL NOT NULL,
                balance_after   REAL NOT NULL,
                pick_id         INTEGER,
                note            TEXT,
                FOREIGN KEY (pick_id) REFERENCES picks(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id              TEXT PRIMARY KEY,
                type            TEXT NOT NULL,
                status          TEXT DEFAULT 'pending',
                params_json     TEXT,
                created_at      TEXT DEFAULT (datetime('now')),
                started_at      TEXT,
                finished_at     TEXT,
                progress        REAL DEFAULT 0,
                log             TEXT,
                result_json     TEXT,
                error           TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                key         TEXT PRIMARY KEY,
                value       TEXT NOT NULL,
                expires_at  TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_params (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                model_type      TEXT NOT NULL,
                created_at      TEXT DEFAULT (datetime('now')),
                params_json     TEXT NOT NULL,
                metrics_json    TEXT,
                is_active       INTEGER DEFAULT 1
            )
        """)

        existing = {r[1] for r in conn.execute("PRAGMA table_info(picks)").fetchall()}
        for col, definition in [
            ("stake_cop",      "REAL DEFAULT 0"),
            ("profit_cop",     "REAL DEFAULT 0"),
            ("sport",          "TEXT DEFAULT 'nba'"),
            ("commence_time",  "TEXT"),
            ("closing_odds",   "INTEGER"),
            ("clv",            "REAL"),
            ("feedback_notes", "TEXT"),
            ("job_id",         "TEXT"),
            ("resolved_at",    "TEXT"),
            ("actual_score",   "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {definition}")

        conn.commit()


def migrate_bankroll_log_if_old():
    """Si la tabla bankroll_log tiene el schema viejo (date/balance/note), la recrea."""
    with get_conn() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(bankroll_log)").fetchall()}
        if cols and "created_at" not in cols:
            conn.execute("DROP TABLE IF EXISTS bankroll_log")
            conn.execute("""
                CREATE TABLE bankroll_log (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at      TEXT DEFAULT (datetime('now')),
                    type            TEXT NOT NULL,
                    amount          REAL NOT NULL,
                    balance_after   REAL NOT NULL,
                    pick_id         INTEGER,
                    note            TEXT,
                    FOREIGN KEY (pick_id) REFERENCES picks(id)
                )
            """)
            conn.commit()


# ─── CRUD ──────────────────────────────────────────────────────────────────────


def save_pick(pick: dict, stake_cop: float = 0):
    today = str(date.today())

    with get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM picks
            WHERE date = ? AND selection = ? AND bet_type = ?
        """, (today, pick["selection"], pick["bet_type"])).fetchone()

        if existing:
            if stake_cop:
                conn.execute(
                    "UPDATE picks SET stake_cop = ? WHERE id = ?",
                    (stake_cop, existing[0])
                )
                conn.commit()
            return

        conn.execute("""
            INSERT INTO picks
              (date, game, bet_type, selection, odds,
               our_prob, implied_prob, edge, confidence,
               stake_cop, sport, commence_time)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            today,
            pick["game"],
            pick["bet_type"],
            pick["selection"],
            pick["odds"],
            pick["our_prob"],
            pick["implied_prob"],
            pick["edge"],
            pick["confidence"],
            stake_cop,
            pick.get("sport", "nba"),
            pick.get("commence_time", ""),
        ))
        conn.commit()


def mark_result(pick_id: int, result: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT odds, stake_cop FROM picks WHERE id = ?", (pick_id,)
        ).fetchone()

        if not row:
            print(f"  ❌ Pick #{pick_id} no encontrado.")
            return

        odds, stake = row
        stake = stake or 0

        if result.upper() == "WIN":
            if odds < 0:
                profit = stake * (100 / abs(odds))
            else:
                profit = stake * (odds / 100)
        elif result.upper() == "LOSS":
            profit = -stake
        else:
            profit = 0

        conn.execute(
            "UPDATE picks SET result = ?, profit_cop = ?, resolved_at = ? WHERE id = ?",
            (result.upper(), round(profit, 2), datetime.now().isoformat(), pick_id)
        )
        conn.commit()

        _log_bankroll_transaction(conn, result.upper(), profit, pick_id)

    result_label = {"WIN": "WIN", "LOSS": "LOSS", "PUSH": "PUSH"}.get(result.upper(), result.upper())
    print(f"  ✓ Pick #{pick_id} → {result_label} | "
          f"Profit: {'+' if profit >= 0 else ''}{profit:,.0f} COP")


def _log_bankroll_transaction(conn: sqlite3.Connection, tx_type: str, amount: float, pick_id: int = None):
    conn.execute(
        "INSERT INTO bankroll_log (type, amount, balance_after, pick_id) VALUES (?, ?, ?, ?)",
        (tx_type, amount, 0, pick_id)
    )


def log_bankroll_manual(tx_type: str, amount: float, note: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO bankroll_log (type, amount, balance_after, note) VALUES (?, ?, ?, ?)",
            (tx_type, amount, 0, note)
        )
        conn.commit()


def get_current_bankroll(initial: float) -> float:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COALESCE(SUM(profit_cop), 0)
            FROM picks
            WHERE result IN ('WIN', 'LOSS', 'PUSH')
        """).fetchone()
    return initial + (row[0] if row else 0)


def get_record():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT result, COUNT(*) as n, COALESCE(SUM(profit_cop), 0) as profit
            FROM picks
            WHERE result != 'PENDING'
            GROUP BY result
        """).fetchall()
    return {r[0]: {"count": r[1], "profit": r[2]} for r in rows}


def get_pending():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, selection, odds, stake_cop
            FROM picks WHERE result = 'PENDING'
            ORDER BY date DESC
        """).fetchall()
    return rows


def get_resolved_without_feedback() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, bet_type, selection
            FROM picks
            WHERE result IN ('WIN', 'LOSS', 'PUSH')
              AND (feedback_notes IS NULL OR feedback_notes = '')
            ORDER BY date ASC
        """).fetchall()
    return [
        {"id": r[0], "date": r[1], "game": r[2], "bet_type": r[3], "selection": r[4]}
        for r in rows
    ]


def save_feedback(pick_id: int, notes: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE picks SET feedback_notes = ? WHERE id = ?",
            (notes, pick_id)
        )
        conn.commit()


def save_closing_odds(pick_id: int, closing_odds: int, clv: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE picks SET closing_odds = ?, clv = ? WHERE id = ?",
            (closing_odds, round(clv, 4), pick_id)
        )
        conn.commit()


def get_clv_summary() -> dict | None:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*)                                          as n,
                AVG(clv)                                          as avg_clv,
                SUM(CASE WHEN clv > 0 THEN 1 ELSE 0 END)         as positive,
                AVG(CASE WHEN result='WIN'  THEN clv END)         as avg_clv_win,
                AVG(CASE WHEN result='LOSS' THEN clv END)         as avg_clv_loss
            FROM picks
            WHERE clv IS NOT NULL
        """).fetchone()
    if not row or not row[0]:
        return None
    n = row[0]
    return {
        "n":              n,
        "avg_clv":        row[1] or 0,
        "positive_pct":   (row[2] or 0) / n,
        "avg_clv_win":    row[3],
        "avg_clv_loss":   row[4],
    }


def get_pending_with_details() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, bet_type, selection, odds,
                   stake_cop, sport, commence_time, closing_odds, clv
            FROM picks
            WHERE result = 'PENDING'
            ORDER BY commence_time ASC
        """).fetchall()
    return [
        {
            "id":            r[0],
            "date":          r[1],
            "game":          r[2],
            "bet_type":      r[3],
            "selection":     r[4],
            "odds":          r[5],
            "stake_cop":     r[6],
            "sport":         r[7] or "nba",
            "commence_time": r[8] or "",
            "closing_odds":  r[9],
            "clv":           r[10],
        }
        for r in rows
    ]


def get_roi_summary() -> dict:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                bet_type,
                COUNT(*) as total,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                COALESCE(SUM(stake_cop), 0) as wagered,
                COALESCE(SUM(profit_cop), 0) as profit
            FROM picks
            WHERE result IN ('WIN','LOSS','PUSH')
            GROUP BY bet_type
        """).fetchall()
    return [
        {
            "tipo":    r[0],
            "total":   r[1],
            "wins":    r[2],
            "wagered": r[3],
            "profit":  r[4],
            "roi":     (r[4] / r[3] * 100) if r[3] > 0 else 0,
        }
        for r in rows
    ]


# ─── SETTINGS (reemplaza settings.json) ────────────────────────────────────────


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        return row[0]
    return default


def set_setting(key: str, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, str(value))
        )
        conn.commit()


def load_all_settings() -> dict:
    with get_conn() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return {r[0]: r[1] for r in rows}


# ─── CACHE (reemplaza football_cache.json) ─────────────────────────────────────


def cache_get(key: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    if row[1] and row[1] < datetime.now().isoformat():
        cache_delete(key)
        return None
    return json.loads(row[0])


def cache_set(key: str, data: dict, ttl_hours: int = 24):
    expires = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO cache (key, value, expires_at, created_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "expires_at = excluded.expires_at, created_at = excluded.created_at",
            (key, json.dumps(data, ensure_ascii=False), expires)
        )
        conn.commit()


def cache_delete(key: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM cache WHERE key = ?", (key,))
        conn.commit()


# ─── MODEL PARAMS (reemplaza model_lr.py) ──────────────────────────────────────


def save_model_params(model_type: str, params: dict, metrics: dict = None, is_active: bool = True):
    if is_active:
        set_setting("active_model_type", model_type)
    with get_conn() as conn:
        if is_active:
            conn.execute("UPDATE model_params SET is_active = 0 WHERE is_active = 1")
        conn.execute(
            "INSERT INTO model_params (model_type, params_json, metrics_json, is_active) "
            "VALUES (?, ?, ?, ?)",
            (
                model_type,
                json.dumps(params),
                json.dumps(metrics) if metrics else None,
                1 if is_active else 0,
            )
        )
        conn.commit()


def get_active_model_params() -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT model_type, params_json FROM model_params WHERE is_active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return None
    return {"model_type": row[0], "params": json.loads(row[1])}
