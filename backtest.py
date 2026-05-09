"""
Backtester gratuito — usa nba_api para validar el modelo.

Qué hace:
  1. Descarga game logs de 2 temporadas (regular season)
  2. Para cada partido, calcula las features que el modelo usaría EN ESE MOMENTO
     (rolling stats de los últimos 15 juegos anteriores a esa fecha)
  3. Corre el modelo sigmoid con diferentes valores de k
  4. Encuentra el k óptimo que minimiza el Brier Score
  5. Muestra calibración, accuracy y ROI simulado

Uso:
  python backtest.py                  → corre el backtest completo
  python backtest.py --download       → solo descarga datos (necesario la primera vez)
  python backtest.py --seasons 1      → usa solo la temporada más reciente
"""

import argparse
import math
import sqlite3
import time
from datetime import datetime, timedelta
from collections import defaultdict

from nba_api.stats.endpoints import LeagueGameLog

BACKTEST_DB   = "backtest_data.db"
SEASONS       = ["2023-24", "2024-25"]
ROLLING_GAMES = 15          # Ventana de forma reciente
HOME_ADV      = 3.0         # Puntos de ventaja local (ajustable)
K_RANGE       = [round(x * 0.01, 2) for x in range(5, 30)]  # 0.05 a 0.29


# ─── BASE DE DATOS LOCAL ──────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(BACKTEST_DB)


def setup_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                season       TEXT,
                game_id      TEXT,
                game_date    TEXT,
                team_abbr    TEXT,
                is_home      INTEGER,
                wl           TEXT,
                pts          INTEGER,
                opp_pts      INTEGER,
                plus_minus   REAL,
                fga          INTEGER,
                fta          INTEGER,
                oreb         INTEGER,
                tov          INTEGER
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_team ON games(team_abbr, game_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game ON games(game_id)")
        conn.commit()


def games_exist(season: str) -> bool:
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM games WHERE season=?", (season,)
        ).fetchone()[0]
    return n > 0


# ─── DESCARGA ─────────────────────────────────────────────────────────────────

def download_season(season: str):
    print(f"  Descargando {season}...", end=" ", flush=True)
    time.sleep(1)

    try:
        log = LeagueGameLog(
            season=season,
            season_type_all_star="Regular Season",
            direction="ASC",
        )
        df = log.get_data_frames()[0]
    except Exception as e:
        print(f"ERROR: {e}")
        return

    rows = []
    for _, row in df.iterrows():
        matchup = str(row.get("MATCHUP", ""))
        is_home = int("vs." in matchup)

        # Oponente: extraer pts del mismo game_id, equipo contrario
        # Por ahora guardamos pts y luego cruzamos
        rows.append((
            season,
            str(row.get("GAME_ID",     "")),
            str(row.get("GAME_DATE",   "")),
            str(row.get("TEAM_ABBREVIATION", "")),
            is_home,
            str(row.get("WL",          "")),
            int(row.get("PTS",          0)),
            0,          # opp_pts: se calcula después
            float(row.get("PLUS_MINUS", 0)),
            int(row.get("FGA",          0)),
            int(row.get("FTA",          0)),
            int(row.get("OREB",         0)),
            int(row.get("TOV",          0)),
        ))

    with get_conn() as conn:
        conn.execute("DELETE FROM games WHERE season=?", (season,))
        conn.executemany("""
            INSERT INTO games
              (season, game_id, game_date, team_abbr, is_home, wl,
               pts, opp_pts, plus_minus, fga, fta, oreb, tov)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        # Calcular opp_pts cruzando mismo game_id
        conn.execute("""
            UPDATE games SET opp_pts = (
                SELECT g2.pts FROM games g2
                WHERE g2.game_id = games.game_id
                  AND g2.team_abbr != games.team_abbr
                  AND g2.season = games.season
                LIMIT 1
            )
            WHERE season = ?
        """, (season,))
        conn.commit()

    n = len(rows) // 2
    print(f"OK ({n} partidos)")


def download_all(seasons: list[str]):
    print("\n  📥  Descargando datos históricos...\n")
    setup_db()
    for s in seasons:
        if games_exist(s):
            print(f"  {s}: ya descargado, saltando.")
        else:
            download_season(s)
            time.sleep(1.5)
    print()


# ─── FEATURES ─────────────────────────────────────────────────────────────────

def load_team_gamelogs() -> dict:
    """Carga todos los juegos agrupados por equipo, ordenados por fecha."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT team_abbr, game_date, game_id, is_home, wl, plus_minus
            FROM games
            ORDER BY team_abbr, game_date ASC
        """).fetchall()

    logs = defaultdict(list)
    for r in rows:
        logs[r[0]].append({
            "date":        r[1],
            "game_id":     r[2],
            "is_home":     r[3],
            "wl":          r[4],
            "plus_minus":  r[5],
        })
    return dict(logs)


def rolling_avg_plus_minus(team_log: list, before_date: str, n: int = ROLLING_GAMES) -> float | None:
    """
    Promedio ponderado de PLUS_MINUS en los últimos N juegos ANTES de before_date.
    Juegos más recientes pesan más (decay exponencial).
    Retorna None si no hay suficientes datos.
    """
    prior = [g for g in team_log if g["date"] < before_date]
    if len(prior) < 5:      # Mínimo 5 juegos para estimar
        return None

    recent = prior[-n:]
    weights = [math.exp(-0.1 * (len(recent) - 1 - i)) for i in range(len(recent))]
    total_w = sum(weights)

    return sum(g["plus_minus"] * w for g, w in zip(recent, weights)) / total_w


def is_b2b(team_log: list, game_date: str) -> bool:
    prior = [g for g in team_log if g["date"] < game_date]
    if not prior:
        return False
    last_date_str = prior[-1]["date"]
    try:
        last = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        curr = datetime.strptime(game_date,     "%Y-%m-%d").date()
        return (curr - last).days == 1
    except ValueError:
        return False


def rest_days(team_log: list, game_date: str) -> int:
    prior = [g for g in team_log if g["date"] < game_date]
    if not prior:
        return 3
    last_date_str = prior[-1]["date"]
    try:
        last = datetime.strptime(last_date_str, "%Y-%m-%d").date()
        curr = datetime.strptime(game_date,     "%Y-%m-%d").date()
        return (curr - last).days
    except ValueError:
        return 3


# ─── MODELO ───────────────────────────────────────────────────────────────────

def sigmoid(x: float, k: float) -> float:
    return 1 / (1 + math.exp(-k * x))


def predict(home_pm: float, away_pm: float,
            home_b2b: bool, away_b2b: bool,
            home_rest: int, away_rest: int,
            k: float) -> float:
    """Replica exacta de analyzer.py — misma lógica, mismo modelo."""
    adj = HOME_ADV
    if home_b2b:
        adj -= 3.5
    if away_b2b:
        adj += 3.5
    adj += (home_rest - away_rest) * 0.5

    net_diff = (home_pm + adj) - away_pm
    return sigmoid(net_diff, k)


# ─── MÉTRICAS ─────────────────────────────────────────────────────────────────

def brier_score(predictions: list[tuple[float, int]]) -> float:
    """Brier Score: 0 = perfecto, 0.25 = aleatorio."""
    return sum((p - o) ** 2 for p, o in predictions) / len(predictions)


def calibration_buckets(predictions: list[tuple[float, int]], n_buckets: int = 10) -> list[dict]:
    """¿Cuando decimos 70%, gana el equipo 70% de las veces?"""
    buckets = defaultdict(lambda: {"pred": [], "outcomes": []})
    step = 1 / n_buckets

    for prob, outcome in predictions:
        bucket = min(int(prob / step), n_buckets - 1)
        buckets[bucket]["pred"].append(prob)
        buckets[bucket]["outcomes"].append(outcome)

    result = []
    for i in range(n_buckets):
        if buckets[i]["pred"]:
            avg_pred   = sum(buckets[i]["pred"]) / len(buckets[i]["pred"])
            actual_win = sum(buckets[i]["outcomes"]) / len(buckets[i]["outcomes"])
            n          = len(buckets[i]["pred"])
            result.append({
                "rango":    f"{i*10:.0f}-{(i+1)*10:.0f}%",
                "pred_avg": avg_pred,
                "actual":   actual_win,
                "n":        n,
                "error":    actual_win - avg_pred,
            })
    return result


def simulated_roi(predictions: list[tuple[float, int, float]],
                  edge_min: float = 0.04) -> dict:
    """
    ROI simulado asumiendo juego a -110 (cuota estándar).
    Solo cuenta picks donde nuestro edge >= edge_min.
    predictions: [(our_prob, actual_outcome, implied_prob), ...]
    """
    wins = losses = wagered = profit = 0

    for our_prob, outcome, impl_prob in predictions:
        edge = our_prob - impl_prob
        if edge < edge_min:
            continue

        wagered += 1
        if outcome == 1:
            profit += 100 / 110    # Paga ~0.909 unidades
            wins   += 1
        else:
            profit -= 1
            losses += 1

    total = wins + losses
    return {
        "picks_filtrados": total,
        "wins":            wins,
        "losses":          losses,
        "win_rate":        wins / total if total else 0,
        "roi":             profit / wagered if wagered else 0,
        "profit_units":    profit,
    }


# ─── BACKTEST PRINCIPAL ───────────────────────────────────────────────────────

def run_backtest(seasons: list[str]):
    print("\n  🔬  Construyendo features históricas...\n")

    team_logs = load_team_gamelogs()

    # Cargar todos los juegos con sus dos equipos
    with get_conn() as conn:
        game_ids = conn.execute("""
            SELECT DISTINCT game_id, game_date, season
            FROM games
            WHERE season IN ({})
            ORDER BY game_date ASC
        """.format(",".join("?" * len(seasons))), seasons).fetchall()

    dataset = []
    skipped = 0

    for game_id, game_date, season in game_ids:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT team_abbr, is_home, wl, pts, opp_pts
                FROM games
                WHERE game_id=? AND season=?
            """, (game_id, season)).fetchall()

        if len(rows) != 2:
            continue

        home_row = next((r for r in rows if r[1] == 1), None)
        away_row = next((r for r in rows if r[1] == 0), None)

        if not home_row or not away_row:
            continue

        home_abbr = home_row[0]
        away_abbr = away_row[0]

        home_log = team_logs.get(home_abbr, [])
        away_log = team_logs.get(away_abbr, [])

        home_pm = rolling_avg_plus_minus(home_log, game_date)
        away_pm = rolling_avg_plus_minus(away_log, game_date)

        if home_pm is None or away_pm is None:
            skipped += 1
            continue

        home_b2b_  = is_b2b(home_log, game_date)
        away_b2b_  = is_b2b(away_log, game_date)
        home_rest_ = rest_days(home_log, game_date)
        away_rest_ = rest_days(away_log, game_date)

        home_won  = 1 if home_row[2] == "W" else 0
        impl_prob = 0.5238  # -110 estándar sin vig → ~52.4%

        dataset.append({
            "game_id":    game_id,
            "game_date":  game_date,
            "season":     season,
            "home_abbr":  home_abbr,
            "away_abbr":  away_abbr,
            "home_pm":    home_pm,
            "away_pm":    away_pm,
            "home_b2b":   home_b2b_,
            "away_b2b":   away_b2b_,
            "home_rest":  home_rest_,
            "away_rest":  away_rest_,
            "home_won":   home_won,
            "impl_prob":  impl_prob,
        })

    total = len(dataset)
    print(f"  Partidos con datos suficientes: {total}  (saltados por muestra chica: {skipped})\n")

    if total < 50:
        print("  ❌ Muy pocos datos. Ejecuta --download primero.\n")
        return

    # ── Buscar k óptimo ──────────────────────────────────────
    print(f"  🔍  Optimizando parámetro k ({len(K_RANGE)} valores)...\n")

    best_k      = 0.15
    best_bs     = float("inf")
    k_results   = []

    for k in K_RANGE:
        preds = []
        for g in dataset:
            p = predict(
                g["home_pm"], g["away_pm"],
                g["home_b2b"], g["away_b2b"],
                g["home_rest"], g["away_rest"],
                k
            )
            preds.append((p, g["home_won"]))

        bs = brier_score(preds)
        accuracy = sum(1 for p, o in preds if (p >= 0.5) == (o == 1)) / len(preds)
        k_results.append({"k": k, "brier": bs, "accuracy": accuracy})

        if bs < best_bs:
            best_bs = bs
            best_k  = k

    # ── Resultados con k óptimo ───────────────────────────────
    preds_full = []
    for g in dataset:
        p = predict(
            g["home_pm"], g["away_pm"],
            g["home_b2b"], g["away_b2b"],
            g["home_rest"], g["away_rest"],
            best_k
        )
        preds_full.append((p, g["home_won"], g["impl_prob"]))

    accuracy = sum(1 for p, o, _ in preds_full if (p >= 0.5) == (o == 1)) / len(preds_full)
    calib    = calibration_buckets([(p, o) for p, o, _ in preds_full])
    roi_4    = simulated_roi(preds_full, edge_min=0.04)
    roi_6    = simulated_roi(preds_full, edge_min=0.06)
    roi_8    = simulated_roi(preds_full, edge_min=0.08)

    # ── Output ────────────────────────────────────────────────
    print(f"{'━'*60}")
    print(f"  RESULTADOS DEL BACKTEST")
    print(f"{'━'*60}")
    print(f"\n  Temporadas:    {', '.join(seasons)}")
    print(f"  Partidos:      {total}")
    print(f"\n  🎯 k ÓPTIMO ENCONTRADO: {best_k}")
    print(f"     (el modelo usa actualmente k=0.15)")

    if best_k != 0.15:
        print(f"\n  ⚠️  ACCIÓN REQUERIDA: cambia k de 0.15 a {best_k} en analyzer.py línea 148")
    else:
        print(f"\n  ✅  El k actual (0.15) ya es el óptimo.")

    print(f"\n  Brier Score:   {best_bs:.4f}  (0.25 = aleatorio | 0.00 = perfecto)")
    print(f"  Accuracy:      {accuracy:.1%}  (% de ganadores predichos correctamente)")

    print(f"\n{'─'*60}")
    print(f"  CALIBRACIÓN  — ¿Las probabilidades son reales?")
    print(f"{'─'*60}")
    print(f"  {'Rango pred':>12}  {'Pred avg':>9}  {'Real %':>9}  {'Error':>8}  {'N':>5}")
    print(f"  {'─'*12}  {'─'*9}  {'─'*9}  {'─'*8}  {'─'*5}")

    for b in calib:
        if b["n"] < 10:
            continue
        error_str = f"{b['error']:+.1%}"
        flag = "  ⚠️" if abs(b["error"]) > 0.05 else ""
        print(f"  {b['rango']:>12}  {b['pred_avg']:>8.1%}  {b['actual']:>8.1%}  "
              f"{error_str:>8}  {b['n']:>5}{flag}")

    print(f"\n{'─'*60}")
    print(f"  ROI SIMULADO  (asumiendo -110 para ambos lados)")
    print(f"{'─'*60}")
    print(f"  {'Edge mín':>10}  {'Picks':>7}  {'Win%':>7}  {'ROI':>8}  {'Profit (u)':>12}")
    print(f"  {'─'*10}  {'─'*7}  {'─'*7}  {'─'*8}  {'─'*12}")

    for label, r in [("≥ 4%", roi_4), ("≥ 6%", roi_6), ("≥ 8%", roi_8)]:
        if r["picks_filtrados"] == 0:
            print(f"  {label:>10}  {'—':>7}")
            continue
        roi_str  = f"{r['roi']:+.1%}"
        prof_str = f"{r['profit_units']:+.2f}"
        print(f"  {label:>10}  {r['picks_filtrados']:>7}  {r['win_rate']:>7.1%}"
              f"  {roi_str:>8}  {prof_str:>12}")

    print(f"\n{'━'*60}")
    print(f"  CONCLUSIÓN")
    print(f"{'━'*60}")

    if roi_4["roi"] > 0:
        print(f"\n  ✅ El modelo tiene ROI positivo histórico con edge ≥ 4%.")
        print(f"     Continúa con el sistema actual.")
    else:
        print(f"\n  ❌ ROI negativo con edge ≥ 4%. El modelo necesita ajustes.")
        print(f"     Considera subir MIN_EDGE a 0.06 o 0.08 en .env")
        best_roi = max([(roi_4, "4%"), (roi_6, "6%"), (roi_8, "8%")],
                       key=lambda x: x[0]["roi"])
        if best_roi[0]["roi"] > 0:
            print(f"     Con edge ≥ {best_roi[1]} el ROI es positivo: {best_roi[0]['roi']:+.1%}")

    print(f"\n  k óptimo: {best_k}  |  Brier: {best_bs:.4f}  |  Accuracy: {accuracy:.1%}\n")


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true",
                        help="Solo descargar datos históricos")
    parser.add_argument("--seasons",  type=int, default=2,
                        help="Cuántas temporadas usar (1 o 2)")
    args = parser.parse_args()

    seasons = SEASONS[-args.seasons:]

    if args.download:
        download_all(seasons)
        return

    # Si no hay datos descargados, descargar primero
    setup_db()
    needs_download = any(not games_exist(s) for s in seasons)
    if needs_download:
        download_all(seasons)

    run_backtest(seasons)


if __name__ == "__main__":
    main()
