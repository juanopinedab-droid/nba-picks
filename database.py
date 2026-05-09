import sqlite3
from datetime import date

DB_PATH = "picks_history.db"


def get_conn():
    return sqlite3.connect(DB_PATH)


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
                commence_time  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bankroll_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                date       TEXT,
                balance    REAL,
                note       TEXT
            )
        """)

        # Migración: agregar columnas que pueden faltar en DBs existentes
        existing = {r[1] for r in conn.execute("PRAGMA table_info(picks)").fetchall()}
        for col, definition in [
            ("stake_cop",     "REAL DEFAULT 0"),
            ("profit_cop",    "REAL DEFAULT 0"),
            ("sport",         "TEXT DEFAULT 'nba'"),
            ("commence_time", "TEXT"),
        ]:
            if col not in existing:
                conn.execute(f"ALTER TABLE picks ADD COLUMN {col} {definition}")

        conn.commit()


def save_pick(pick: dict, stake_cop: float = 0):
    today = str(date.today())

    # Deduplicación: no guardar el mismo pick dos veces en el mismo día
    with get_conn() as conn:
        existing = conn.execute("""
            SELECT id FROM picks
            WHERE date = ? AND selection = ? AND bet_type = ?
        """, (today, pick["selection"], pick["bet_type"])).fetchone()

        if existing:
            # Si ya existe, actualizar stake si se envió uno
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
    """Marcar WIN / LOSS / PUSH y calcular profit automáticamente."""
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
        else:  # PUSH
            profit = 0

        conn.execute(
            "UPDATE picks SET result = ?, profit_cop = ? WHERE id = ?",
            (result.upper(), round(profit, 2), pick_id)
        )
        conn.commit()

    print(f"  ✓ Pick #{pick_id} → {result.upper()} | "
          f"Profit: {'+'if profit>=0 else ''}{profit:,.0f} COP")


def get_current_bankroll(initial: float) -> float:
    """
    Calcula el bankroll real sumando ganancias/pérdidas de todos los picks resueltos.
    """
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


def get_pending_with_details() -> list[dict]:
    """Picks pendientes con todos los campos necesarios para el resolver."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, bet_type, selection, odds,
                   stake_cop, sport, commence_time
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
        }
        for r in rows
    ]


def get_roi_summary() -> dict:
    """ROI real por tipo de apuesta."""
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
