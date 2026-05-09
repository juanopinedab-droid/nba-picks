"""
resolver.py — Resolución automática de resultados NBA.

Consulta la ESPN API para obtener marcadores finales y stats de jugadores,
y marca cada pick pendiente como WIN / LOSS / PUSH sin intervención manual.

Uso directo:
    python resolver.py
    python resolver.py --fecha 2026-05-08

Integrado en picks.py:
    python picks.py --resolver
"""

import re
import sys
import time
import argparse
import requests
from datetime import datetime, date, timedelta

import database
import config

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
        r = requests.get(_ESPN_SCOREBOARD, params={"dates": date_str}, timeout=15)
        r.raise_for_status()
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
        r = requests.get(_ESPN_SUMMARY, params={"event": espn_id}, timeout=15)
        r.raise_for_status()
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
            else:
                print(f"  ❓  [{pick_id}] No se pudo resolver: {selection}")
                summary["ERROR"] += 1

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
    args = parser.parse_args()

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
