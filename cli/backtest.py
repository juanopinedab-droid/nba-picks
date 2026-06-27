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
  python cli/backtest.py                  → corre el backtest completo
  python cli/backtest.py --download       → solo descarga datos (necesario la primera vez)
  python cli/backtest.py --seasons 1      → usa solo la temporada más reciente
"""

import argparse
import math
import os
import sqlite3
import time
from datetime import datetime, timedelta
from collections import defaultdict

from nba_api.stats.endpoints import LeagueGameLog

BACKTEST_DB   = "backtest_data.db"
SEASONS       = ["2023-24", "2024-25"]
ROLLING_GAMES = 15          # Ventana de forma reciente
HOME_ADV      = 3.0         # Puntos de ventaja local (ajustable)
K_RANGE       = [round(x * 0.01, 2) for x in range(3, 51)]  # 0.03 a 0.50


# ─── BASE DE DATOS LOCAL ──────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(BACKTEST_DB)


def setup_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                season       TEXT,
                season_type  TEXT DEFAULT 'Regular Season',
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

        # Migración: agregar season_type si no existe en la tabla
        existing_cols = {r[1] for r in conn.execute("PRAGMA table_info(games)").fetchall()}
        if "season_type" not in existing_cols:
            conn.execute("ALTER TABLE games ADD COLUMN season_type TEXT DEFAULT 'Regular Season'")

        conn.commit()


def games_exist(season: str, season_type: str = "Regular Season") -> bool:
    with get_conn() as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM games WHERE season=? AND season_type=?",
            (season, season_type)
        ).fetchone()[0]
    return n > 0


# ─── DESCARGA ─────────────────────────────────────────────────────────────────

def download_season(season: str, season_type: str = "Regular Season"):
    label = "PO" if season_type == "Playoffs" else "RS"
    print(f"  Descargando {season} ({label})...", end=" ", flush=True)
    time.sleep(1)

    try:
        log = LeagueGameLog(
            season=season,
            season_type_all_star=season_type,
            direction="ASC",
        )
        df = log.get_data_frames()[0]
    except Exception as e:
        print(f"ERROR: {e}")
        return

    if df.empty:
        print("Sin datos")
        return

    rows = []
    for _, row in df.iterrows():
        matchup = str(row.get("MATCHUP", ""))
        is_home = int("vs." in matchup)
        rows.append((
            season,
            season_type,
            str(row.get("GAME_ID",           "")),
            str(row.get("GAME_DATE",         "")),
            str(row.get("TEAM_ABBREVIATION", "")),
            is_home,
            str(row.get("WL",                "")),
            int(row.get("PTS",                0)),
            0,
            float(row.get("PLUS_MINUS",       0)),
            int(row.get("FGA",                0)),
            int(row.get("FTA",                0)),
            int(row.get("OREB",               0)),
            int(row.get("TOV",                0)),
        ))

    with get_conn() as conn:
        conn.execute(
            "DELETE FROM games WHERE season=? AND season_type=?",
            (season, season_type)
        )
        conn.executemany("""
            INSERT INTO games
              (season, season_type, game_id, game_date, team_abbr, is_home, wl,
               pts, opp_pts, plus_minus, fga, fta, oreb, tov)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, rows)

        # Calcular opp_pts cruzando mismo game_id
        conn.execute("""
            UPDATE games SET opp_pts = (
                SELECT g2.pts FROM games g2
                WHERE g2.game_id = games.game_id
                  AND g2.team_abbr != games.team_abbr
                LIMIT 1
            )
            WHERE season = ? AND season_type = ?
        """, (season, season_type))
        conn.commit()

    n = len(rows) // 2
    print(f"OK ({n} partidos)")


def download_all(seasons: list[str]):
    print("\n  📥  Descargando datos históricos (Regular Season + Playoffs)...\n")
    setup_db()
    for s in seasons:
        for stype in ("Regular Season", "Playoffs"):
            if games_exist(s, stype):
                label = "PO" if stype == "Playoffs" else "RS"
                print(f"  {s} ({label}): ya descargado, saltando.")
            else:
                download_season(s, stype)
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
    """Replica de analyzer.py — misma lógica y ajustes."""
    adj = HOME_ADV
    if home_b2b:
        adj -= 3.5
    if away_b2b:
        adj += 3.5
    # Rest bonus: tanh evita bonus irreales (alineado con analyzer.py)
    rest_bonus = math.tanh((home_rest - away_rest) / 2) * 1.5
    if abs(rest_bonus) >= 0.5:
        adj += rest_bonus

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
                  edge_min: float = 0.04,
                  realistic: bool = False) -> dict:
    """
    ROI simulado.
    realistic=False → asume -110 plano (cota optimista).
    realistic=True  → payout basado en probabilidad del modelo con 5% vig
                       (asume el libro precio igual al modelo — cota conservadora).
    predictions: [(our_prob_home, home_won, implied_prob_home), ...]
    """
    wins = losses = wagered = profit = 0

    for our_prob, outcome, impl_prob in predictions:
        home_edge = our_prob - impl_prob
        away_edge = (1 - our_prob) - (1 - impl_prob)  # = impl_prob - our_prob

        bet_home = home_edge >= edge_min and our_prob >= 0.52
        bet_away = away_edge >= edge_min and (1 - our_prob) >= 0.52

        def _payout(win_prob: float) -> float:
            if realistic:
                # Fair odds minus 5% vig: payout = (1-p)/p * 0.95
                return max((1 - win_prob) / win_prob * 0.95, 0.15)
            return 100 / 110

        if bet_home:
            wagered += 1
            if outcome == 1:
                profit += _payout(our_prob)
                wins   += 1
            else:
                profit -= 1
                losses += 1
        elif bet_away:  # elif: nunca apostar los dos lados del mismo partido
            wagered += 1
            if outcome == 0:  # visitante ganó
                profit += _payout(1 - our_prob)
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
            SELECT DISTINCT game_id, game_date, season, season_type
            FROM games
            WHERE season IN ({})
            ORDER BY game_date ASC
        """.format(",".join("?" * len(seasons))), seasons).fetchall()

    dataset = []
    skipped = 0

    for game_id, game_date, season, season_type in game_ids:
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
            "game_id":     game_id,
            "game_date":   game_date,
            "season":      season,
            "season_type": season_type,
            "home_abbr":   home_abbr,
            "away_abbr":   away_abbr,
            "home_pm":     home_pm,
            "away_pm":     away_pm,
            "home_b2b":    home_b2b_,
            "away_b2b":    away_b2b_,
            "home_rest":   home_rest_,
            "away_rest":   away_rest_,
            "home_won":    home_won,
            "impl_prob":   impl_prob,
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
    roi_4r   = simulated_roi(preds_full, edge_min=0.04, realistic=True)
    roi_6r   = simulated_roi(preds_full, edge_min=0.06, realistic=True)
    roi_8r   = simulated_roi(preds_full, edge_min=0.08, realistic=True)

    # ── Breakdown por tipo de temporada ──────────────────────────────────────────
    rs_idx = [i for i, g in enumerate(dataset) if g["season_type"] == "Regular Season"]
    po_idx = [i for i, g in enumerate(dataset) if g["season_type"] == "Playoffs"]

    def _accuracy(indices):
        if not indices:
            return None, 0
        preds = [preds_full[i] for i in indices]
        acc   = sum(1 for p, o, _ in preds if (p >= 0.5) == (o == 1)) / len(preds)
        return acc, len(preds)

    rs_acc, rs_n = _accuracy(rs_idx)
    po_acc, po_n = _accuracy(po_idx)

    # ── Output ────────────────────────────────────────────────
    print(f"{'━'*60}")
    print(f"  RESULTADOS DEL BACKTEST")
    print(f"{'━'*60}")
    print(f"\n  Temporadas:    {', '.join(seasons)}")
    print(f"  Partidos:      {total}")
    print(f"\n  🎯 k ÓPTIMO ENCONTRADO: {best_k}")

    current_k = 0.08  # Sincronizar con analyzer.py:net_rating_to_prob() si cambia
    if best_k != current_k:
        print(f"\n  ⚠️  ACCIÓN REQUERIDA: cambia k={current_k} → k={best_k} en analyzer.py")
        print(f"     Busca: k = {current_k}  # Calibrado con backtest.py")
    else:
        print(f"\n  ✅  El k actual ({current_k}) ya es el óptimo.")

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
    print(f"  ROI SIMULADO")
    print(f"  Optimista = -110 plano | Realista = payout por prob. modelo (-5% vig)")
    print(f"{'─'*60}")
    print(f"  {'Edge mín':>10}  {'Picks':>6}  {'Win%':>6}  {'ROI opt':>8}  {'ROI real':>9}")
    print(f"  {'─'*10}  {'─'*6}  {'─'*6}  {'─'*8}  {'─'*9}")

    for label, r, rr in [(">=4%", roi_4, roi_4r), (">=6%", roi_6, roi_6r), (">=8%", roi_8, roi_8r)]:
        if r["picks_filtrados"] == 0:
            print(f"  {label:>10}  {'—':>6}")
            continue
        print(f"  {label:>10}  {r['picks_filtrados']:>6}  {r['win_rate']:>6.1%}"
              f"  {r['roi']:>+8.1%}  {rr['roi']:>+8.1%}")

    print(f"\n  * Optimista: asume -110 en todos los partidos (cota superior).")
    print(f"    Realista:   payout = (1-p)/p * 0.95 segun prob del modelo (cota inferior).")
    print(f"    ROI real estara en el rango [Realista, Optimista].")

    print(f"\n{'─'*60}")
    print(f"  ACCURACY RS vs PLAYOFFS")
    print(f"{'─'*60}")
    if rs_acc is not None:
        print(f"  Regular Season:  {rs_acc:.1%}  ({rs_n} partidos)")
    if po_acc is not None:
        print(f"  Playoffs:        {po_acc:.1%}  ({po_n} partidos)")
    if rs_acc and po_acc:
        diff = po_acc - rs_acc
        note = "modelo mejora en playoffs" if diff > 0 else "modelo es mejor en RS"
        print(f"  Diferencia PO-RS: {diff:+.1%}  ({note})")

    print(f"\n{'━'*60}")
    print(f"  CONCLUSION")
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

    # ── Entrenar Regresión Logística ──────────────────────────────────────────
    print(f"\n{'─'*60}")
    print(f"  ENTRENAMIENTO — Regresión Logística")
    print(f"{'─'*60}")

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler

        # Features: [net_diff_base, home_b2b, away_b2b, rest_tanh]
        # net_diff_base excluye B2B y rest (LR aprende esos pesos)
        X_train = []
        y_train = []
        for g in dataset:
            rest_tanh = math.tanh((g["home_rest"] - g["away_rest"]) / 2)
            net_base  = (g["home_pm"] + HOME_ADV) - g["away_pm"]
            X_train.append([net_base, float(g["home_b2b"]), float(g["away_b2b"]), rest_tanh])
            y_train.append(g["home_won"])

        scaler   = StandardScaler()
        X_scaled = scaler.fit_transform(X_train)

        lr = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
        lr.fit(X_scaled, y_train)

        coef      = lr.coef_[0].tolist()
        intercept = float(lr.intercept_[0])
        means     = scaler.mean_.tolist()
        scales    = scaler.scale_.tolist()

        # Evaluar LR vs sigmoid
        lr_preds = []
        for g, x in zip(dataset, X_train):
            x_s = [(f - m) / s for f, m, s in zip(x, means, scales)]
            z   = intercept + sum(c * xi for c, xi in zip(coef, x_s))
            p   = max(0.05, min(0.93, 1 / (1 + math.exp(-z))))
            lr_preds.append((p, g["home_won"], g["impl_prob"]))

        lr_accuracy = sum(1 for p, o, _ in lr_preds if (p >= 0.5) == (o == 1)) / len(lr_preds)
        lr_bs       = brier_score([(p, o) for p, o, _ in lr_preds])
        lr_roi4  = simulated_roi(lr_preds, edge_min=0.04)
        lr_roi4r = simulated_roi(lr_preds, edge_min=0.04, realistic=True)

        print(f"  LR entrenada: {len(dataset)} partidos  ({', '.join(seasons)})")
        print(f"  LR Accuracy:  {lr_accuracy:.1%}  (sigmoid: {accuracy:.1%})")
        print(f"  LR Brier:     {lr_bs:.4f}  (sigmoid: {best_bs:.4f})")
        print(f"  LR ROI@4%:    opt {lr_roi4['roi']:+.1%} | real {lr_roi4r['roi']:+.1%}"
              f"  (sigmoid: opt {roi_4['roi']:+.1%} | real {roi_4r['roi']:+.1%})")
        print(f"\n  Coeficientes (escala estandarizada):")
        feat_names = ["net_diff_base", "home_b2b", "away_b2b", "rest_tanh"]
        for name, c in zip(feat_names, coef):
            print(f"    {name:<18}: {c:+.4f}")

        # Guardar como model_lr.py (importable directamente por analyzer.py)
        n_train = len(dataset)
        model_code = f'''# Auto-generado por backtest.py — no editar manualmente
# Entrenado sobre {n_train} partidos de {', '.join(seasons)}
import math

_COEF      = {coef}
_INTERCEPT = {intercept}
_MEANS     = {means}
_SCALES    = {scales}


def lr_prob(features: list) -> float:
    """
    Probabilidad de victoria del equipo local.
    features = [net_diff_base, home_b2b (0.0/1.0), away_b2b (0.0/1.0), rest_tanh]
    net_diff_base: diferencial de Net Rating sin ajustes B2B/rest
    rest_tanh:     tanh((home_rest - away_rest) / 2)
    """
    scaled = [(f - m) / s for f, m, s in zip(features, _MEANS, _SCALES)]
    z = _INTERCEPT + sum(c * x for c, x in zip(_COEF, scaled))
    return max(0.05, min(0.93, 1 / (1 + math.exp(-z))))
'''
        model_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    '..', 'src', 'nba', 'model_lr.py')
        with open(model_path, "w", encoding="utf-8") as f:
            f.write(model_code)

        print(f"\n  Modelo guardado en: src/nba/model_lr.py")
        print(f"  analyzer.py usara LR automaticamente en el proximo python cli/picks.py")

    except ImportError:
        print("  scikit-learn no instalado.")
        print("  Ejecuta: pip install scikit-learn")
        print("  El modelo sigmoid sigue siendo el activo.")


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
