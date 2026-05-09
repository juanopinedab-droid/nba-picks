# -*- coding: utf-8 -*-
import sys, io
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

"""
picks_football.py — Picks del día para la Premier League.
Ejecutar: python picks_football.py

Marca resultados:  python picks_football.py --resultado <ID> WIN
Ver pendientes:    python picks_football.py --pendientes
Ver historial:     python picks_football.py --historial
Filtrar partido:   python picks_football.py --partido Arsenal
"""

import sys
import argparse
from datetime import date

import database
import collector_football as collector
import analyzer_football  as analyzer
import bankroll
import config


CONF_EMOJI = {"ALTA": "🔥", "MEDIA": "✅", "BAJA": "⚠️"}
CONF_COLOR = {"ALTA": "\033[92m", "MEDIA": "\033[93m", "BAJA": "\033[90m"}
RESET = "\033[0m"
BOLD  = "\033[1m"


def print_header():
    from datetime import timedelta
    today    = date.today().strftime("%d/%m/%Y")
    tomorrow = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
    print(f"\n{BOLD}{'━'*60}{RESET}")
    print(f"{BOLD}  ⚽  FOOTBALL PICKS — Premier League{RESET}")
    print(f"{BOLD}      {today} y {tomorrow}{RESET}")
    print(f"{BOLD}{'━'*60}{RESET}\n")


def _format_kickoff(commence_iso: str) -> str:
    from datetime import datetime as _dt
    try:
        utc = _dt.fromisoformat(commence_iso.replace("Z", "+00:00"))
        local = utc.astimezone()
        return local.strftime("%d/%m %H:%M")
    except Exception:
        return ""


def print_match_summary(result: dict):
    home    = result["home_team"]
    away    = result["away_team"]
    kickoff = _format_kickoff(result.get("commence", ""))
    ko_str  = f"  🕐 {kickoff}" if kickoff else ""
    print(f"  📋  {BOLD}{away} @ {home}{RESET}{ko_str}")
    print(f"       {home:<22}  xG: {result['home_xg']:.2f}  |  xGA: {result['home_xga']:.2f}")
    print(f"       {away:<22}  xG: {result['away_xg']:.2f}  |  xGA: {result['away_xga']:.2f}")
    print(f"       λ local: {result['lambda_home']:.2f}  |  "
          f"λ visitante: {result['lambda_away']:.2f}  |  "
          f"Fuente: {result['bookmaker']}")
    print(f"       P:  local {result['p_home']:.1%}  |  "
          f"empate {result['p_draw']:.1%}  |  "
          f"visitante {result['p_away']:.1%}")


def print_pick(pick: dict, pick_num: int, label: str = "PICK"):
    conf     = pick["confidence"]
    emoji    = CONF_EMOJI[conf]
    color    = CONF_COLOR[conf]
    odds_str = f"{pick['odds']:+d}" if pick['odds'] > 0 else str(pick['odds'])

    print(f"\n  {emoji} {BOLD}{label} #{pick_num} — {pick['bet_type']}{RESET}")
    print(f"     {color}{BOLD}► {pick['selection']}  ({odds_str}){RESET}")
    print(f"     Confianza:  {color}{conf}{RESET}")
    print(f"     Edge:       {pick['edge']:.1%}  "
          f"(nuestra {pick['our_prob']:.1%} vs casa {pick['implied_prob']:.1%})")
    print(f"     Kelly 1/2:  {pick['kelly_stake']:.1%} del bankroll")
    print(f"\n     {BOLD}Por qué:{RESET}")
    for reason in pick["reasons"]:
        if reason:
            print(f"       • {reason}")


def run_football_picks(partido: str | None = None):
    """Pipeline principal de fútbol: datos → modelo → output."""
    print_header()

    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "pega_tu_key_aqui":
        print("  ❌  Falta ODDS_API_KEY en el archivo .env\n")
        return

    print("  Obteniendo cuotas EPL...\n")

    try:
        games = collector.get_todays_epl_matches()
    except Exception as e:
        print(f"  ❌  Error al obtener odds: {e}\n")
        return

    if not games:
        print("  📭  No hay partidos EPL programados para hoy.\n")
        return

    if partido:
        filtro = partido.lower()
        games = [g for g in games
                 if filtro in g["home_team"].lower() or filtro in g["away_team"].lower()]
        if not games:
            print(f"  ❌  No hay partido con '{partido}' hoy.\n")
            return

    print(f"  Partidos encontrados: {len(games)}\n")

    live_bankroll = database.get_current_bankroll(config.BANKROLL)
    print(f"  Bankroll actual: ${live_bankroll:,.0f} COP\n")

    all_picks    = []
    game_results = []

    for game in games:
        home = game["home_team"]
        away = game["away_team"]

        print(f"  Obteniendo stats: {away} @ {home}...", end=" ", flush=True)

        home_season = collector.get_team_season_stats(home)
        away_season = collector.get_team_season_stats(away)

        if not home_season or not away_season:
            print(f"sin datos (Understat no tiene '{home}' o '{away}')")
            continue

        # Forma reciente (xG últimos 5 partidos desde Understat)
        home_recent = None
        away_recent = None
        try:
            home_recent = collector.get_team_recent_xg(home)
            away_recent = collector.get_team_recent_xg(away)
        except Exception:
            pass

        # Combinar stats de temporada + reciente en un solo dict
        home_stats = {
            **home_season,
            "xg_recent":   home_recent["xg_recent"]   if home_recent else None,
            "xga_recent":  home_recent["xga_recent"]  if home_recent else None,
            "recent_games": home_recent["games"]       if home_recent else 0,
        }
        away_stats = {
            **away_season,
            "xg_recent":   away_recent["xg_recent"]   if away_recent else None,
            "xga_recent":  away_recent["xga_recent"]  if away_recent else None,
            "recent_games": away_recent["games"]       if away_recent else 0,
        }

        print("OK")

        result = analyzer.analyze_match(game, home_stats, away_stats)
        result["commence"] = game.get("commence", "")
        game_results.append(result)
        all_picks.extend(result["picks"])

    # ── DISPLAY ───────────────────────────────────────────────────────────────
    print(f"\n{'━'*60}")
    print(f"{BOLD}  1X2 / TOTAL GOLES / BTTS{RESET}")
    print(f"{'━'*60}")

    pick_counter = 1
    has_any_pick = False

    for result in game_results:
        if not result["picks"]:
            continue
        has_any_pick = True
        print_match_summary(result)
        for pick in result["picks"]:
            print_pick(pick, pick_counter)
            database.save_pick(pick)
            pick_counter += 1
        print()

    no_pick_games = [r["game"] for r in game_results if not r["picks"]]
    if no_pick_games:
        print(f"\n  Sin edge en: {', '.join(no_pick_games)}")

    if not has_any_pick:
        print(f"\n  📭  No hay picks con edge suficiente hoy.")
        print(f"       (mínimo requerido: {getattr(config, 'FOOTBALL_MIN_EDGE', config.MIN_EDGE):.0%})")

    # ── BANKROLL ──────────────────────────────────────────────────────────────
    if all_picks:
        stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks)
        for pick in all_picks:
            stake = stake_map.get(pick["selection"], 0)
            database.save_pick(pick, stake_cop=stake)
        bankroll.print_bankroll_section(live_bankroll, all_picks, BOLD, RESET)

    # ── FOOTER ────────────────────────────────────────────────────────────────
    record = database.get_record()
    wins   = record.get("WIN",  {}).get("count", 0) if isinstance(record.get("WIN"), dict) else record.get("WIN", 0)
    losses = record.get("LOSS", {}).get("count", 0) if isinstance(record.get("LOSS"), dict) else record.get("LOSS", 0)
    total  = wins + losses
    roi_str = f"  |  Historial: {wins}W-{losses}L ({wins/total:.0%} win rate)" if total > 0 else ""

    print(f"\n{'━'*60}")
    print(f"  Total picks hoy: {len(all_picks)}{roi_str}")
    print(f"  Para marcar resultado: python picks_football.py --resultado <ID> WIN/LOSS")
    print(f"{'━'*60}\n")


def show_pending():
    pending = database.get_pending()
    if not pending:
        print("\n  No hay picks pendientes de resultado.\n")
        return
    print(f"\n{BOLD}  PICKS PENDIENTES{RESET}")
    print(f"{'━'*60}")
    for row in pending:
        pick_id, pick_date, game, selection, odds, stake = row
        odds_str  = f"{odds:+d}" if odds > 0 else str(odds)
        stake_str = f"  | Apostado: ${stake:,.0f} COP" if stake else ""
        print(f"  [{pick_id}] {pick_date} | {game}")
        print(f"       ► {selection} ({odds_str}){stake_str}")
    print()


def show_record():
    record  = database.get_record()
    summary = database.get_roi_summary()
    live_br = database.get_current_bankroll(config.BANKROLL)

    wins   = record.get("WIN",  {}).get("count", 0) if isinstance(record.get("WIN"), dict) else record.get("WIN", 0)
    losses = record.get("LOSS", {}).get("count", 0) if isinstance(record.get("LOSS"), dict) else record.get("LOSS", 0)
    pushes = record.get("PUSH", {}).get("count", 0) if isinstance(record.get("PUSH"), dict) else record.get("PUSH", 0)
    total  = wins + losses

    profit_total = sum(
        v["profit"] if isinstance(v, dict) else 0
        for v in record.values()
    )

    print(f"\n{BOLD}  HISTORIAL COMPLETO — FÚTBOL{RESET}")
    print(f"{'━'*60}")
    print(f"  Bankroll inicial: ${config.BANKROLL:,.0f} COP")
    print(f"  Bankroll actual:  ${live_br:,.0f} COP")
    print(f"\n  Wins:   {wins}")
    print(f"  Losses: {losses}")
    print(f"  Pushes: {pushes}")
    if total > 0:
        print(f"  Win %:  {wins/total:.1%}  ({total} resueltas)")

    if summary:
        print(f"\n  {'TIPO':<22} {'W':>4} {'L':>4} {'APOSTADO':>12} {'PROFIT':>12} {'ROI':>7}")
        print(f"  {'─'*22} {'─'*4} {'─'*4} {'─'*12} {'─'*12} {'─'*7}")
        for s in summary:
            l = s["total"] - s["wins"]
            roi_str  = f"{s['roi']:+.1f}%"
            prof_str = f"{'+'if s['profit']>=0 else ''}{s['profit']:,.0f}"
            print(f"  {s['tipo']:<22} {s['wins']:>4} {l:>4} "
                  f"  {s['wagered']:>10,.0f}   {prof_str:>10}  {roi_str:>7}")
    print()


def main():
    database.setup()

    parser = argparse.ArgumentParser(description="Football Picks — Premier League")
    parser.add_argument("--resultado",  nargs=2, metavar=("ID", "RESULT"),
                        help="Marcar resultado: --resultado 3 WIN")
    parser.add_argument("--pendientes", action="store_true")
    parser.add_argument("--historial",  action="store_true")
    parser.add_argument("--partido", metavar="EQUIPO",
                        help="Filtrar por equipo (ej: 'Arsenal')")
    args = parser.parse_args()

    if args.resultado:
        pick_id, result = args.resultado
        if result.upper() not in ("WIN", "LOSS", "PUSH"):
            print("  ❌  Resultado debe ser WIN, LOSS o PUSH")
            return
        database.mark_result(int(pick_id), result)
        show_record()

    elif args.pendientes:
        show_pending()

    elif args.historial:
        show_record()

    else:
        run_football_picks(partido=args.partido)


if __name__ == "__main__":
    main()
