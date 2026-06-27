# -*- coding: utf-8 -*-
import sys, io, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

"""
NBA Picks del día — uso personal
Ejecutar:            python cli/picks.py
Marcar resultado:    python cli/picks.py --resultado 3 WIN
Ver pendientes:      python cli/picks.py --pendientes
Ver historial:       python cli/picks.py --historial
Resolver automático: python cli/picks.py --resolver
"""

import argparse
from datetime import date

from src.core import database, config, bankroll, resolver
from src.nba import collector, analyzer, parlays
from src.service.picks_nba import execute as run_pipeline


CONF_EMOJI = {"ALTA": "🔥", "MEDIA": "✅", "BAJA": "⚠️"}
CONF_COLOR = {"ALTA": "\033[92m", "MEDIA": "\033[93m", "BAJA": "\033[90m"}
RESET      = "\033[0m"
BOLD       = "\033[1m"


def print_header():
    from datetime import timedelta
    today    = date.today().strftime("%d/%m/%Y")
    tomorrow = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
    print(f"\n{BOLD}{'━'*58}{RESET}")
    print(f"{BOLD}  🏀  NBA PICKS{RESET}")
    print(f"{BOLD}      {today} y {tomorrow}{RESET}")
    print(f"{BOLD}{'━'*58}{RESET}\n")


def _injury_str(out: list, questionable: list) -> str:
    parts = []
    if out:
        parts.append(f"❌ OUT: {', '.join(out)}")
    if questionable:
        parts.append(f"❓ Q: {', '.join(questionable)}")
    return "  " + " | ".join(parts) if parts else ""


def print_game_summary(result: dict):
    game   = result["game"]
    h_rec  = result["home_record"]
    a_rec  = result["away_record"]
    h_nr   = result["home_net_rating"]
    a_nr   = result["away_net_rating"]
    h_b2b  = " ⚡B2B" if result["home_b2b"] else ""
    a_b2b  = " ⚡B2B" if result["away_b2b"] else ""

    away_name, home_name = game.split(" @ ")

    h_inj = _injury_str(result.get("home_injured_out", []), result.get("home_injured_questionable", []))
    a_inj = _injury_str(result.get("away_injured_out", []), result.get("away_injured_questionable", []))

    print(f"  📋  {BOLD}{game}{RESET}")
    print(f"       {away_name} ({a_rec}) NRtg: {a_nr:+.1f}{a_b2b}{a_inj}")
    print(f"       {home_name} ({h_rec}) NRtg: {h_nr:+.1f}{h_b2b}{h_inj}")
    print(f"       Fuente: {result['bookmaker']}")


def print_pick(pick: dict, pick_num: int):
    conf     = pick["confidence"]
    emoji    = CONF_EMOJI[conf]
    color    = CONF_COLOR[conf]
    odds_str = f"{pick['odds']:+d}" if pick['odds'] > 0 else str(pick['odds'])

    print(f"\n  {emoji} {BOLD}PICK #{pick_num} — {pick['bet_type']}{RESET}")
    print(f"     {color}{BOLD}► {pick['selection']}  ({odds_str}){RESET}")
    print(f"     Confianza:  {color}{conf}{RESET}")
    print(f"     Edge:       {pick['edge']:.1%}  "
          f"(nuestra {pick['our_prob']:.1%} vs casa {pick['implied_prob']:.1%})")
    print(f"     Kelly 1/2:  {pick['kelly_stake']:.1%} del bankroll")
    print(f"\n     {BOLD}Por qué:{RESET}")
    for reason in pick["reasons"]:
        print(f"       • {reason}")


def print_no_picks():
    print("  📭  No hay picks con edge suficiente hoy.")
    print(f"       (mínimo requerido: {config.MIN_EDGE:.0%})")
    print("\n       Opciones:")
    print("       • Baja MIN_EDGE en .env para ver más picks (más riesgo)")
    print("       • No hay juegos hoy")
    print("       • Las líneas están eficientes hoy")


def print_footer(total_picks: int):
    record = database.get_record()
    wins   = record.get("WIN", 0)
    losses = record.get("LOSS", 0)
    total  = wins + losses
    roi_str = ""
    if total > 0:
        roi_str = f"  |  Historial: {wins}W-{losses}L ({wins/total:.0%} win rate)"

    print(f"\n{'━'*58}")
    print(f"  Total picks hoy: {total_picks}{roi_str}")
    print(f"  Para marcar resultado: python picks.py --resultado <ID> WIN/LOSS")
    print(f"  Para ver pendientes:   python picks.py --pendientes")
    print(f"{'━'*58}\n")


def print_props_pick(pick: dict, pick_num: int):
    conf     = pick["confidence"]
    emoji    = CONF_EMOJI[conf]
    color    = CONF_COLOR[conf]
    odds_str = f"{pick['odds']:+d}" if pick['odds'] > 0 else str(pick['odds'])

    print(f"\n  {emoji} {BOLD}PROP #{pick_num} — {pick['bet_type']}{RESET}")
    print(f"     {color}{BOLD}► {pick['selection']}  ({odds_str}){RESET}")
    print(f"     Confianza:  {color}{conf}{RESET}")
    print(f"     Edge:       {pick['edge']:.1%}  "
          f"(nuestra {pick['our_prob']:.1%} vs casa {pick['implied_prob']:.1%})")
    print(f"\n     {BOLD}Por qué:{RESET}")
    for reason in pick["reasons"]:
        print(f"       • {reason}")


def run_picks(partido: str | None = None):
    """Pipeline principal: datos → análisis → output."""
    print_header()

    from src.core.types import JobContext
    ctx = JobContext(job_id="cli")

    try:
        result = run_pipeline({"partido": partido}, ctx)
    except ValueError as e:
        print(f"  ❌  {e}")
        return
    except Exception as e:
        print(f"  ❌  Error: {e}")
        return

    if not result["games"]:
        print("  📭  No hay juegos NBA programados para hoy ni mañana.\n")
        return

    games         = result["games"]
    all_picks     = result["picks"]
    all_props     = result["props"]
    live_bankroll = result["bankroll"]
    parlay_list   = result["parlays"]

    print(f"  Juegos encontrados: {len(games)}\n")
    print(f"  Bankroll actual: ${live_bankroll:,.0f} COP "
          f"(inicial: ${config.get_bankroll():,.0f} COP)\n")

    # ── SECCIÓN 1: PARTIDOS ───────────────────────────────────
    print(f"\n{'━'*58}")
    print(f"{BOLD}  MONEYLINE / SPREAD{RESET}")
    print(f"{'━'*58}")

    pick_counter = 1

    for result in games:
        if not result["picks"]:
            continue
        print_game_summary(result)
        for pick in result["picks"]:
            print_pick(pick, pick_counter)
            pick_counter += 1
        print()

    no_pick_games = [r["game"] for r in games if not r["picks"]]
    if no_pick_games:
        print(f"\n  Sin edge en: {', '.join(no_pick_games)}")

    if not all_picks:
        print()
        print_no_picks()

    # ── SECCIÓN 2: PLAYER PROPS ───────────────────────────────
    if config.get_fetch_props():
        print(f"\n{'━'*58}")
        print(f"{BOLD}  PLAYER PROPS  (Puntos / Rebotes / Asistencias / Triples){RESET}")
        print(f"{'━'*58}")

        if all_props:
            prop_counter = 1
            for pick in all_props:
                print_props_pick(pick, prop_counter)
                prop_counter += 1
        else:
            print("\n  📭  No hay props con edge suficiente hoy.")

    # ── SECCIÓN 3: BANKROLL ───────────────────────────────────
    bankroll.print_bankroll_section(live_bankroll, all_picks + all_props, BOLD, RESET)

    # ── SECCIÓN 4: COMBINADAS ─────────────────────────────────
    print(f"\n{'━'*58}")
    print(f"{BOLD}  COMBINADAS  (Partido + Props){RESET}")
    print(f"{'━'*58}")
    parlays.print_parlays(parlay_list, BOLD, RESET)

    total_picks = len(all_picks)
    print_footer(total_picks)


def show_pending():
    pending = database.get_pending()
    if not pending:
        print("\n  No hay picks pendientes de resultado.\n")
        return

    print(f"\n{BOLD}  PICKS PENDIENTES{RESET}")
    print(f"{'━'*58}")
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
    live_br = database.get_current_bankroll(config.get_bankroll())

    wins   = record.get("WIN",  {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    pushes = record.get("PUSH", {}).get("count", 0)
    total  = wins + losses

    profit_total = sum(v["profit"] for v in record.values())

    print(f"\n{BOLD}  HISTORIAL COMPLETO{RESET}")
    print(f"{'━'*58}")
    print(f"  Bankroll inicial: ${config.get_bankroll():,.0f} COP")
    print(f"  Bankroll actual:  ${live_br:,.0f} COP  "
          f"({'+'if profit_total>=0 else ''}{profit_total:,.0f} COP)")
    print(f"\n  Wins:   {wins}")
    print(f"  Losses: {losses}")
    print(f"  Pushes: {pushes}")
    if total > 0:
        print(f"  Win %:  {wins/total:.1%}  ({total} resueltas)")

    if summary:
        print(f"\n  {'TIPO':<20} {'W':>4} {'L':>4} {'APOSTADO':>12} {'PROFIT':>12} {'ROI':>7}")
        print(f"  {'─'*20} {'─'*4} {'─'*4} {'─'*12} {'─'*12} {'─'*7}")
        for s in summary:
            l = s["total"] - s["wins"]
            roi_str = f"{s['roi']:+.1f}%"
            prof_str = f"{'+'if s['profit']>=0 else ''}{s['profit']:,.0f}"
            print(f"  {s['tipo']:<20} {s['wins']:>4} {l:>4} "
                  f"  {s['wagered']:>10,.0f}   {prof_str:>10}  {roi_str:>7}")

    # CLV — métrca de calidad del modelo
    resolver.print_clv_summary()
    print()


def main():
    database.setup()

    parser = argparse.ArgumentParser(description="NBA Picks Bot — uso personal")
    parser.add_argument("--resultado",  nargs=2, metavar=("ID", "RESULT"),
                        help="Marcar resultado: --resultado 3 WIN")
    parser.add_argument("--pendientes", action="store_true",
                        help="Ver picks sin resultado")
    parser.add_argument("--historial",  action="store_true",
                        help="Ver record histórico")
    parser.add_argument("--partido", metavar="EQUIPO",
                        help="Analizar solo el partido de ese equipo (parcial, ej: 'Lakers')")
    parser.add_argument("--resolver", action="store_true",
                        help="Resolver automáticamente picks pendientes con resultados ESPN")
    parser.add_argument("--cerrar", action="store_true",
                        help="Guardar cuotas de cierre para calcular CLV (correr antes del partido)")
    parser.add_argument("--fecha", metavar="YYYY-MM-DD",
                        help="Con --resolver: solo picks de esa fecha")
    parser.add_argument("--season", metavar="YYYY-YY",
                        help="Temporada NBA (ej: 2024-25). Default: valor en .env")
    args = parser.parse_args()

    if args.season:
        collector.set_season(args.season)

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

    elif args.cerrar:
        print(f"\n{BOLD}{'━'*58}{RESET}")
        print(f"{BOLD}  📉  CUOTAS DE CIERRE — CLV{RESET}")
        print(f"{BOLD}{'━'*58}{RESET}\n")
        resolver.save_closing_odds_for_pending()
        resolver.print_clv_summary()

    elif args.resolver:
        from datetime import date as _date
        target = None
        if args.fecha:
            try:
                target = _date.fromisoformat(args.fecha)
            except ValueError:
                print(f"  ❌  Fecha inválida: {args.fecha} (usa YYYY-MM-DD)")
                return
        print(f"\n{BOLD}{'━'*58}{RESET}")
        print(f"{BOLD}  🔍  RESOLVER — Resultados NBA{RESET}")
        print(f"{BOLD}{'━'*58}{RESET}\n")
        summary = resolver.auto_resolve_all(target_date=target)
        resolver.print_resolve_summary(summary)
        if any(summary.get(k) for k in ("WIN", "LOSS", "PUSH")):
            show_record()

    else:
        run_picks(partido=args.partido)


if __name__ == "__main__":
    main()
