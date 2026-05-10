"""
resolver.py — Resolución automática de resultados NBA + CLV tracking.

Consulta la ESPN API para obtener marcadores finales y stats de jugadores,
y marca cada pick pendiente como WIN / LOSS / PUSH sin intervención manual.
También guarda cuotas de cierre para calcular Closing Line Value (CLV).

Uso directo:
    python resolver.py                      # resuelve pendientes
    python resolver.py --fecha 2026-05-08   # fecha específica
    python resolver.py --cerrar             # guarda cuotas de cierre (antes del partido)

Integrado en picks.py:
    python picks.py --resolver
    python picks.py --cerrar
"""

import re
import sys
import time
import argparse
import requests
from datetime import datetime, date, timedelta

import database
import config
import feedback as _feedback
from utils import http_get

# ─── ENDPOINTS ESPN ───────────────────────────────────────────────────────────

_ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
_ESPN_SUMMARY    = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"

BOLD  = "\033[1m"
RESET = "\033[0m"


# ─── ESPN: MARCADORES POR FECHA ───────────────────────────────────────────────

def fetch_nba_scores(target_date: date) -> list[dict]:
    """
    Marcadores finales NBA para una fecha (formato hora del Este).
    Retorna lista de dicts con home_team, away_team, scores, espn_id y status.
    """
    date_str = target_date.strftime("%Y%m%d")
    try:
        r = http_get(_ESPN_SCOREBOARD, params={"dates": date_str}, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  ESPN scoreboard error ({date_str}): {e}")
        return []

    games = []
    for event in data.get("events", []):
        comp   = event.get("competitions", [{}])[0]
        status = event.get("status", {}).get("type", {}).get("name", "")

        home = away = None
        home_score = away_score = 0

        for team in comp.get("competitors", []):
            name  = team.get("team", {}).get("displayName", "")
            score = int(team.get("score", 0) or 0)
            if team.get("homeAway") == "home":
                home       = name
                home_score = score
            else:
                away       = name
                away_score = score

        if home and away:
            games.append({
                "espn_id":    event.get("id", ""),
                "home_team":  home,
                "away_team":  away,
                "home_score": home_score,
                "away_score": away_score,
                "final":      status == "STATUS_FINAL",
            })
    return games


# ─── ESPN: STATS DE JUGADORES ─────────────────────────────────────────────────

def fetch_player_stats(espn_id: str) -> dict[str, dict]:
    """
    Stats de todos los jugadores de un partido desde el resumen ESPN.
    Retorna {nombre_lower: {PTS, REB, AST, FG3M}}.
    """
    try:
        r = http_get(_ESPN_SUMMARY, params={"event": espn_id}, timeout=15)
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  ESPN summary error ({espn_id}): {e}")
        return {}

    result: dict[str, dict] = {}

    for team_block in data.get("boxscore", {}).get("players", []):
        for stat_group in team_block.get("statistics", []):
            labels   = stat_group.get("labels", [])
            athletes = stat_group.get("athletes", [])

            for athlete in athletes:
                name  = athlete.get("athlete", {}).get("displayName", "")
                stats = athlete.get("stats", [])
                if not name or not stats:
                    continue

                row = dict(zip(labels, stats))

                def _safe_float(val) -> float:
                    """Convierte '28' o '10-20' (FG) → toma el primer número."""
                    if not val:
                        return 0.0
                    val = str(val).split("-")[0].split(":")[0]
                    try:
                        return float(val)
                    except ValueError:
                        return 0.0

                # ESPN usa "3PT" con formato "made-attempted"
                fg3m_raw = row.get("3PT") or row.get("3PM") or "0"
                fg3m     = _safe_float(fg3m_raw)

                result[name.lower()] = {
                    "PTS":  _safe_float(row.get("PTS")),
                    "REB":  _safe_float(row.get("REB")),
                    "AST":  _safe_float(row.get("AST")),
                    "FG3M": fg3m,
                }

    return result


# ─── MATCHING PICK ↔ PARTIDO ──────────────────────────────────────────────────

def _match_game(pick_game: str, scores: list[dict]) -> dict | None:
    """
    Encuentra el marcador final que corresponde al partido del pick.
    pick_game = "Los Angeles Lakers @ Boston Celtics"
    """
    try:
        away_raw, home_raw = pick_game.split(" @ ", 1)
    except ValueError:
        return None

    away_l = away_raw.strip().lower()
    home_l = home_raw.strip().lower()

    for s in scores:
        sh = s["home_team"].lower()
        sa = s["away_team"].lower()
        # Exact match primero
        if sh == home_l and sa == away_l:
            return s
        # Partial match (cubre abreviaturas / variantes de nombre)
        if (home_l in sh or sh in home_l) and (away_l in sa or sa in away_l):
            return s

    return None


# ─── PARSERS DE SELECCIÓN ─────────────────────────────────────────────────────

def _parse_moneyline(sel: str) -> str | None:
    """'Boston Celtics (LOCAL)' → 'Boston Celtics'"""
    m = re.match(r'^(.+?)\s+\((LOCAL|VISITANTE)\)$', sel)
    return m.group(1).strip() if m else None


def _parse_spread(sel: str) -> tuple[str, float] | None:
    """'Boston Celtics -5.5' → ('Boston Celtics', -5.5)"""
    m = re.match(r'^(.+?)\s+([+-]\d+\.?\d*)$', sel)
    return (m.group(1).strip(), float(m.group(2))) if m else None


def _parse_total(sel: str) -> tuple[str, float] | None:
    """'Over 224.5' → ('Over', 224.5)"""
    m = re.match(r'^(Over|Under)\s+(\d+\.?\d*)$', sel, re.IGNORECASE)
    return (m.group(1).capitalize(), float(m.group(2))) if m else None


def _parse_prop(sel: str) -> tuple[str, str, float, str] | None:
    """'LeBron James Over 25.5 PTS' → ('LeBron James', 'Over', 25.5, 'PTS')"""
    m = re.match(
        r'^(.+?)\s+(Over|Under)\s+(\d+\.?\d*)\s+(PTS|REB|AST|FG3M)$',
        sel, re.IGNORECASE
    )
    if m:
        return m.group(1).strip(), m.group(2).capitalize(), float(m.group(3)), m.group(4).upper()
    return None


# ─── RESOLUCIÓN POR TIPO DE APUESTA ──────────────────────────────────────────

def _team_match(team_pick: str, team_espn: str) -> bool:
    a = team_pick.lower()
    b = team_espn.lower()
    return a == b or a in b or b in a


def resolve_moneyline(selection: str, gr: dict) -> str | None:
    team = _parse_moneyline(selection)
    if not team:
        return None
    hs, as_ = gr["home_score"], gr["away_score"]
    if _team_match(team, gr["home_team"]):
        return "WIN" if hs > as_ else ("PUSH" if hs == as_ else "LOSS")
    if _team_match(team, gr["away_team"]):
        return "WIN" if as_ > hs else ("PUSH" if hs == as_ else "LOSS")
    return None


def resolve_spread(selection: str, gr: dict) -> str | None:
    parsed = _parse_spread(selection)
    if not parsed:
        return None
    team, line = parsed
    hs, as_ = gr["home_score"], gr["away_score"]

    if _team_match(team, gr["home_team"]):
        margin = (hs + line) - as_
    elif _team_match(team, gr["away_team"]):
        margin = (as_ + line) - hs
    else:
        return None

    if margin > 0:   return "WIN"
    if margin < 0:   return "LOSS"
    return "PUSH"


def resolve_total(selection: str, gr: dict) -> str | None:
    parsed = _parse_total(selection)
    if not parsed:
        return None
    direction, line = parsed
    total = gr["home_score"] + gr["away_score"]

    if total == line:                     return "PUSH"
    if direction == "Over":               return "WIN" if total > line else "LOSS"
    return "WIN" if total < line else "LOSS"


def resolve_prop(selection: str, player_stats: dict) -> str | None:
    parsed = _parse_prop(selection)
    if not parsed:
        return None
    player, direction, line, stat = parsed
    player_l = player.lower()

    # Buscar jugador con match flexible
    pstats = None
    for name_key, ps in player_stats.items():
        if player_l in name_key or name_key in player_l:
            pstats = ps
            break
    if pstats is None:
        return None

    actual = pstats.get(stat, 0.0)
    if actual == line:                    return "PUSH"
    if direction == "Over":               return "WIN" if actual > line else "LOSS"
    return "WIN" if actual < line else "LOSS"


# ─── RESOLVER PRINCIPAL ───────────────────────────────────────────────────────

def auto_resolve_all(target_date: date | None = None) -> dict:
    """
    Revisa todos los picks PENDING de deporte 'nba' y los resuelve
    automáticamente si el partido ya terminó (> 3 horas desde inicio).

    Si se pasa target_date, solo resuelve picks de esa fecha.
    Retorna resumen {WIN, LOSS, PUSH, SKIP, ERROR}.
    """
    pending = database.get_pending_with_details()
    nba_pending = [p for p in pending if p.get("sport", "nba") == "nba"]

    if not nba_pending:
        print("  ✅  No hay picks NBA pendientes.")
        return {}

    now = datetime.now().astimezone()
    summary = {"WIN": 0, "LOSS": 0, "PUSH": 0, "SKIP": 0, "ERROR": 0}

    # Agrupar por fecha del partido
    picks_by_date: dict[date, list] = {}
    for pick in nba_pending:
        commence = pick.get("commence_time", "")
        if not commence:
            print(f"  ⚠️  Pick #{pick['id']} sin commence_time — no se puede resolver automáticamente.")
            summary["SKIP"] += 1
            continue
        try:
            utc_dt   = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone()

            # Filtrar por fecha específica si se pidió
            if target_date and local_dt.date() != target_date:
                continue

            # Solo resolver si el partido empezó hace más de 3 horas
            if (now - local_dt).total_seconds() < 3 * 3600:
                summary["SKIP"] += 1
                continue

            game_date = local_dt.date()
        except Exception:
            summary["SKIP"] += 1
            continue

        picks_by_date.setdefault(game_date, []).append(pick)

    if not picks_by_date:
        print("  ⏳  Los partidos aún no han terminado (< 3h desde el inicio).")
        return summary

    # ── Por cada fecha, obtener marcadores y resolver ──────────────────────────
    for game_date, date_picks in sorted(picks_by_date.items()):
        print(f"\n  📅  {BOLD}{game_date.strftime('%d/%m/%Y')}{RESET} "
              f"— {len(date_picks)} pick(s) pendientes")

        scores = fetch_nba_scores(game_date)
        if not scores:
            print(f"       ⚠️  Sin datos ESPN para {game_date}")
            summary["SKIP"] += len(date_picks)
            continue

        final_scores = [s for s in scores if s["final"]]
        boxscore_cache: dict[str, dict] = {}  # espn_id → player_stats
        feedback_queue: dict[str, dict] = {}  # espn_id → {pick_ids, home_team, away_team}

        for pick in date_picks:
            pick_id   = pick["id"]
            bet_type  = pick["bet_type"]
            selection = pick["selection"]
            game_str  = pick["game"]

            game_result = _match_game(game_str, final_scores)
            if not game_result:
                print(f"  ⏳  [{pick_id}] {game_str} — partido no finalizado aún")
                summary["SKIP"] += 1
                continue

            hs = game_result["home_score"]
            as_ = game_result["away_score"]
            score_str = f"{game_result['away_team']} {as_} – {hs} {game_result['home_team']}"

            # ── Resolver según tipo ────────────────────────────────────────────
            result = None

            if bet_type == "MONEYLINE":
                result = resolve_moneyline(selection, game_result)

            elif bet_type == "SPREAD":
                result = resolve_spread(selection, game_result)

            elif bet_type == "TOTAL":
                result = resolve_total(selection, game_result)

            elif bet_type in ("PTS", "REB", "AST", "FG3M"):
                espn_id = game_result["espn_id"]
                if espn_id not in boxscore_cache:
                    print(f"       Descargando boxscore {game_str}...", end=" ", flush=True)
                    boxscore_cache[espn_id] = fetch_player_stats(espn_id)
                    time.sleep(0.5)
                    print("OK")
                result = resolve_prop(selection, boxscore_cache[espn_id])

            # ── Marcar en DB ───────────────────────────────────────────────────
            if result:
                database.mark_result(pick_id, result)
                icon = "✅" if result == "WIN" else ("❌" if result == "LOSS" else "🔄")
                print(f"  {icon}  [{pick_id}] {selection}  →  {result}")
                print(f"       {score_str}")
                summary[result] += 1

                # Acumular para feedback post-fecha
                eid = game_result["espn_id"]
                if eid not in feedback_queue:
                    feedback_queue[eid] = {
                        "pick_ids":  [],
                        "home_team": game_result["home_team"],
                        "away_team": game_result["away_team"],
                    }
                feedback_queue[eid]["pick_ids"].append(pick_id)
            else:
                print(f"  ❓  [{pick_id}] No se pudo resolver: {selection}")
                summary["ERROR"] += 1

        # ── Feedback automático por partido ────────────────────────────────────
        if feedback_queue:
            print(f"\n  🗞️   Obteniendo contexto ESPN para {len(feedback_queue)} partido(s)...")
            for eid, info in feedback_queue.items():
                time.sleep(0.5)
                _feedback.attach_feedback(
                    eid,
                    info["pick_ids"],
                    info["home_team"],
                    info["away_team"],
                )

    return summary


def print_resolve_summary(summary: dict):
    if not summary:
        return
    total_resolved = summary.get("WIN", 0) + summary.get("LOSS", 0) + summary.get("PUSH", 0)
    if not total_resolved and not summary.get("SKIP") and not summary.get("ERROR"):
        return

    print(f"\n{'━'*50}")
    print(f"{BOLD}  RESOLUCIÓN AUTOMÁTICA{RESET}")
    print(f"{'━'*50}")
    if summary.get("WIN"):    print(f"  ✅  WIN:          {summary['WIN']}")
    if summary.get("LOSS"):   print(f"  ❌  LOSS:         {summary['LOSS']}")
    if summary.get("PUSH"):   print(f"  🔄  PUSH:         {summary['PUSH']}")
    if summary.get("SKIP"):   print(f"  ⏳  Sin resolver: {summary['SKIP']}")
    if summary.get("ERROR"):  print(f"  ❓  Error:        {summary['ERROR']}")
    print(f"{'━'*50}\n")


# ─── CLV — CLOSING LINE VALUE ────────────────────────────────────────────────

def _american_to_prob(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _fetch_current_nba_odds() -> list[dict]:
    """Cuotas actuales NBA desde The Odds API (1 request)."""
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        "apiKey":     config.ODDS_API_KEY,
        "regions":    "us",
        "markets":    "h2h,spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }
    try:
        r = http_get(url, params=params, timeout=15)
        remaining = r.headers.get("x-requests-remaining", "?")
        print(f"  [API] Requests restantes: {remaining}")
        return r.json()
    except Exception as e:
        print(f"  ⚠️  Odds API error: {e}")
        return []


def _find_closing_odds(pick: dict, odds_games: list) -> int | None:
    """
    Busca en la lista de juegos actuales la cuota de cierre para un pick.
    Devuelve cuota en formato americano, o None si el juego ya no está disponible.
    """
    import collector as _col

    game_str = pick["game"]   # "Away @ Home"
    bet_type = pick["bet_type"]
    selection = pick["selection"]

    try:
        away_raw, home_raw = game_str.split(" @ ", 1)
    except ValueError:
        return None

    away_l = away_raw.strip().lower()
    home_l = home_raw.strip().lower()

    # Encontrar el juego en la respuesta de la API
    target = None
    for g in odds_games:
        gh = g.get("home_team", "").lower()
        ga = g.get("away_team", "").lower()
        if (home_l in gh or gh in home_l) and (away_l in ga or ga in away_l):
            target = g
            break

    if not target:
        return None  # Juego ya empezó o no está disponible

    # Extraer odds con la misma lógica que el collector
    odds_data = _col._extract_best_odds(
        target.get("bookmakers", []),
        target["home_team"],
        target["away_team"],
    )
    if not odds_data:
        return None

    home_name = target["home_team"]
    away_name = target["away_team"]

    if bet_type == "MONEYLINE":
        team = _parse_moneyline(selection)
        if team and _team_match(team, home_name):
            return odds_data.get("h2h_home")
        if team and _team_match(team, away_name):
            return odds_data.get("h2h_away")

    elif bet_type == "SPREAD":
        parsed = _parse_spread(selection)
        if parsed:
            team, _ = parsed
            if _team_match(team, home_name):
                return odds_data.get("spread_home")
            if _team_match(team, away_name):
                return odds_data.get("spread_away")

    elif bet_type == "TOTAL":
        parsed = _parse_total(selection)
        if parsed:
            direction, _ = parsed
            return odds_data.get("total_over") if direction == "Over" else odds_data.get("total_under")

    return None


def save_closing_odds_for_pending() -> int:
    """
    Guarda las cuotas de cierre (≈ cuota actual) para todos los picks NBA
    pendientes que aún no tienen closing_odds registrado.
    Ejecutar 1-2 horas antes del inicio de los partidos.
    Retorna el número de picks actualizados.
    """
    pending = database.get_pending_with_details()
    # Solo picks NBA con commence_time y sin closing_odds ya guardado
    targets = [
        p for p in pending
        if p.get("sport", "nba") == "nba"
        and p.get("commence_time")
        and p.get("closing_odds") is None
    ]

    if not targets:
        print("  ✅  Todos los picks pendientes ya tienen cuota de cierre.")
        return 0

    print(f"  Buscando cuotas de cierre para {len(targets)} picks...\n")
    odds_games = _fetch_current_nba_odds()
    if not odds_games:
        return 0

    updated = 0
    for pick in targets:
        closing = _find_closing_odds(pick, odds_games)
        if closing is None:
            print(f"  ⏳  [{pick['id']}] {pick['selection'][:45]:<45} — juego no disponible aún")
            continue

        opening = pick["odds"]
        # CLV = prob_implícita(cierre) - prob_implícita(apertura)
        # Positivo = mercado se movió a nuestro favor = buena señal
        clv = _american_to_prob(closing) - _american_to_prob(opening)
        database.save_closing_odds(pick["id"], closing, clv)

        arrow  = "▲" if clv >= 0 else "▼"
        color  = "\033[92m" if clv >= 0 else "\033[91m"
        reset  = "\033[0m"
        clv_str = f"{clv:+.1%}"
        print(f"  {color}{arrow}{reset}  [{pick['id']}] {pick['selection'][:40]:<40}"
              f"  apertura {opening:+d}  →  cierre {closing:+d}  "
              f"CLV {color}{clv_str}{reset}")
        updated += 1

    print(f"\n  {updated} picks actualizados.")
    return updated


def print_clv_summary():
    """Muestra resumen de CLV del historial completo."""
    data = database.get_clv_summary()
    if not data or data["n"] == 0:
        print("  Sin datos de CLV aún. Ejecuta --cerrar antes de cada jornada.")
        return

    avg   = data["avg_clv"]
    pct   = data["positive_pct"]
    color = "\033[92m" if avg >= 0 else "\033[91m"
    reset = "\033[0m"

    print(f"\n  {'━'*50}")
    print(f"  {BOLD}📈  CLOSING LINE VALUE (CLV){RESET}")
    print(f"  {'━'*50}")
    print(f"  Picks con CLV registrado: {data['n']}")
    print(f"  CLV promedio:   {color}{avg:+.2%}{reset}  "
          f"({'POSITIVO ✅' if avg >= 0 else 'NEGATIVO ⚠️'})")
    print(f"  % picks con CLV > 0:  {pct:.0%}")
    if data["avg_clv_win"] is not None:
        print(f"  CLV promedio en WINs:   {data['avg_clv_win']:+.2%}")
    if data["avg_clv_loss"] is not None:
        print(f"  CLV promedio en LOSSes: {data['avg_clv_loss']:+.2%}")

    # Interpretación
    print(f"\n  {'─'*50}")
    if avg >= 0.02:
        msg = "🔥 Modelo con edge real. El mercado confirma tus selecciones."
    elif avg >= 0:
        msg = "✅ CLV levemente positivo. Sigue acumulando datos."
    elif avg >= -0.02:
        msg = "⚠️  CLV cerca de cero. Revisar criterios de selección."
    else:
        msg = "❌ CLV negativo. El mercado se mueve en contra. Recalibrar modelo."
    print(f"  {msg}")
    print(f"  {'━'*50}\n")


# ─── ENTRY POINT ─────────────────────────────────────────────────────────────

def main():
    import io
    if sys.stdout.encoding != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    database.setup()

    parser = argparse.ArgumentParser(description="Resolver automático de picks NBA")
    parser.add_argument(
        "--fecha", metavar="YYYY-MM-DD",
        help="Resolver solo picks de esta fecha (default: todos los pendientes)"
    )
    parser.add_argument(
        "--backfill-feedback", action="store_true",
        help="Aplicar feedback ESPN a todos los picks resueltos que aún no lo tienen"
    )
    args = parser.parse_args()

    if args.backfill_feedback:
        print(f"\n{BOLD}{'━'*50}{RESET}")
        print(f"{BOLD}  🗞️   BACKFILL FEEDBACK — Picks históricos{RESET}")
        print(f"{BOLD}{'━'*50}{RESET}\n")
        _feedback.backfill_feedback_all()
        return

    target = None
    if args.fecha:
        try:
            target = date.fromisoformat(args.fecha)
        except ValueError:
            print(f"  ❌  Fecha inválida: {args.fecha} (usa YYYY-MM-DD)")
            return

    print(f"\n{BOLD}{'━'*50}{RESET}")
    print(f"{BOLD}  🔍  RESOLVER — Resultados NBA{RESET}")
    print(f"{BOLD}{'━'*50}{RESET}\n")

    summary = auto_resolve_all(target_date=target)
    print_resolve_summary(summary)


if __name__ == "__main__":
    main()
