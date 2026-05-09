# -*- coding: utf-8 -*-
import sys, io
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

"""
NBA Picks del día — uso personal
Ejecutar:            python picks.py
Marcar resultado:    python picks.py --resultado 3 WIN
Ver pendientes:      python picks.py --pendientes
Ver historial:       python picks.py --historial
Resolver automático: python picks.py --resolver
"""

import argparse
from datetime import date

import database
import collector
import analyzer
import parlays
import bankroll
import config
import resolver


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

    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "pega_tu_key_aqui":
        print("  ❌  Falta ODDS_API_KEY en el archivo .env")
        print("      Regístrate en https://the-odds-api.com y pega tu key.\n")
        return

    print("  Obteniendo datos...\n")

    try:
        games = collector.get_todays_odds()
    except Exception as e:
        print(f"  ❌  Error al obtener odds: {e}\n")
        return

    if not games:
        print("  📭  No hay juegos NBA programados para hoy ni mañana.\n")
        return

    if partido:
        filtro = partido.lower()
        games = [g for g in games
                 if filtro in g["home_team"].lower() or filtro in g["away_team"].lower()]
        if not games:
            print(f"  ❌  No hay juego con '{partido}' hoy.\n")
            return

    print(f"  Juegos encontrados: {len(games)}\n")

    # Bankroll real calculado desde resultados históricos en DB
    live_bankroll = database.get_current_bankroll(config.BANKROLL)
    print(f"  Bankroll actual: ${live_bankroll:,.0f} COP "
          f"(inicial: ${config.BANKROLL:,.0f} COP)\n")

    # Stats de jugadores (una sola llamada para todos)
    player_stats = collector.get_all_player_season_stats()

    # Reporte de lesiones (ESPN API, gratuita)
    print("  [Lesiones] Obteniendo reporte...", end=" ", flush=True)
    injury_report = collector.get_injury_report()
    print(f"OK ({len(injury_report)} jugadores con limitaciones)" if injury_report else "Sin datos")

    all_game_picks  = []
    all_prop_picks  = []
    game_results    = []

    for game in games:
        home = game["home_team"]
        away = game["away_team"]

        home_stats = collector.get_team_stats(home)
        away_stats = collector.get_team_stats(away)

        if not home_stats or not away_stats:
            print(f"  ⚠️  Sin stats para {away} @ {home}, saltando...")
            continue

        home_b2b  = collector.is_back_to_back(home)
        away_b2b  = collector.is_back_to_back(away)
        home_rest = collector.get_rest_days(home)
        away_rest = collector.get_rest_days(away)

        # Forma reciente (reutiliza gamelog ya cacheado por is_back_to_back)
        home_form = collector.get_team_recent_form(home)
        away_form = collector.get_team_recent_form(away)

        # Ajuste por lesiones: copias para no mutar el caché
        home_impact = collector.get_team_injury_impact(home, injury_report, player_stats)
        away_impact = collector.get_team_injury_impact(away, injury_report, player_stats)
        home_stats_adj = {
            **home_stats,
            "net_rating":    home_stats["net_rating"] + home_impact["adjustment"],
            "recent_nr":     home_form["recent_nr"] if home_form else None,
            "recent_games":  home_form["games"]     if home_form else 0,
        }
        away_stats_adj = {
            **away_stats,
            "net_rating":    away_stats["net_rating"] + away_impact["adjustment"],
            "recent_nr":     away_form["recent_nr"] if away_form else None,
            "recent_games":  away_form["games"]     if away_form else 0,
        }

        # Análisis de partido (moneyline + spread)
        result = analyzer.analyze_game(
            game, home_stats_adj, away_stats_adj,
            home_b2b, away_b2b, home_rest, away_rest
        )
        result["home_injured_out"]          = home_impact["out"]
        result["home_injured_questionable"] = home_impact["questionable"]
        result["away_injured_out"]          = away_impact["out"]
        result["away_injured_questionable"] = away_impact["questionable"]
        # Inyectar commence_time y sport para resolver automático después
        commence = game.get("commence_time", "")
        for pick in result["picks"]:
            pick["commence_time"] = commence
            pick["sport"]         = "nba"

        game_results.append(result)
        all_game_picks.extend(result["picks"])

        # Análisis de props — cuesta 1 request por juego
        if config.FETCH_PROPS:
            raw_props = collector.get_player_props(game["game_id"])
            if raw_props:
                # Fetch últimos partidos de cada jugador (cacheado por nombre)
                stat_cols = ["PTS", "REB", "AST", "FG3M"]
                recent_avgs = {}
                for prop in raw_props:
                    name = prop["player"]
                    if name not in recent_avgs:
                        recent_avgs[name] = collector.get_player_recent_avg(name, stat_cols)

                prop_picks = analyzer.analyze_player_props(
                    raw_props, player_stats,
                    home_b2b, away_b2b, home, away,
                    game_total=game.get("total_line"),
                    recent_avgs=recent_avgs,
                    home_stats=home_stats_adj,
                    away_stats=away_stats_adj,
                )
                # Inyectar commence_time y sport también en props
                for pick in prop_picks:
                    pick["commence_time"] = commence
                    pick["sport"]         = "nba"
                all_prop_picks.extend(prop_picks)

    # ── SECCIÓN 1: PARTIDOS ───────────────────────────────────
    print(f"\n{'━'*58}")
    print(f"{BOLD}  MONEYLINE / SPREAD{RESET}")
    print(f"{'━'*58}")

    pick_counter = 1

    for result in game_results:
        if not result["picks"]:
            continue
        print_game_summary(result)
        for pick in result["picks"]:
            print_pick(pick, pick_counter)
            database.save_pick(pick)
            pick_counter += 1
        print()

    no_pick_games = [r["game"] for r in game_results if not r["picks"]]
    if no_pick_games:
        print(f"\n  Sin edge en: {', '.join(no_pick_games)}")

    if not all_game_picks:
        print()
        print_no_picks()

    # ── SECCIÓN 2: PLAYER PROPS ───────────────────────────────
    if config.FETCH_PROPS:
        print(f"\n{'━'*58}")
        print(f"{BOLD}  PLAYER PROPS  (Puntos / Rebotes / Asistencias / Triples){RESET}")
        print(f"{'━'*58}")

        if all_prop_picks:
            prop_counter = 1
            for pick in all_prop_picks:
                print_props_pick(pick, prop_counter)
                database.save_pick(pick)
                prop_counter += 1
        else:
            print("\n  📭  No hay props con edge suficiente hoy.")

    # ── SECCIÓN 3: BANKROLL ───────────────────────────────────
    # Calcular stakes y guardar picks con monto real
    all_picks_today = all_game_picks + all_prop_picks
    stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks_today)

    # Re-guardar picks con stake (la primera vez se guardaron sin stake)
    for pick in all_picks_today:
        stake = stake_map.get(pick["selection"], 0)
        database.save_pick(pick, stake_cop=stake)

    bankroll.print_bankroll_section(live_bankroll, all_picks_today, BOLD, RESET)

    # ── SECCIÓN 4: COMBINADAS ─────────────────────────────────
    print(f"\n{'━'*58}")
    print(f"{BOLD}  COMBINADAS  (Partido + Props){RESET}")
    print(f"{'━'*58}")
    parlay_list = parlays.build_parlays(all_game_picks, all_prop_picks)
    parlays.print_parlays(parlay_list, BOLD, RESET)

    total_picks = len(all_game_picks) + len(all_prop_picks)
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
    live_br = database.get_current_bankroll(config.BANKROLL)

    wins   = record.get("WIN",  {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    pushes = record.get("PUSH", {}).get("count", 0)
    total  = wins + losses

    profit_total = sum(v["profit"] for v in record.values())

    print(f"\n{BOLD}  HISTORIAL COMPLETO{RESET}")
    print(f"{'━'*58}")
    print(f"  Bankroll inicial: ${config.BANKROLL:,.0f} COP")
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
