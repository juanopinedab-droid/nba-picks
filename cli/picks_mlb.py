# -*- coding: utf-8 -*-
import sys, io, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
if sys.stdout.encoding != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

"""
MLB Picks del dia — uso personal
Ejecutar:            python cli/picks_mlb.py
Solo manana:         python cli/picks_mlb.py --manana
Marcar resultado:    python cli/picks_mlb.py --resultado 3 WIN
Ver pendientes:      python cli/picks_mlb.py --pendientes
Ver historial:       python cli/picks_mlb.py --historial
"""

import argparse
from datetime import date

from src.core import database, config
from src.mlb import collector
from src.core.types import JobContext
from src.service.picks_mlb import execute as run_pipeline


BOLD  = "\033[1m"
RESET = "\033[0m"


def print_header():
    today    = date.today().strftime("%d/%m/%Y")
    print(f"\n{BOLD}{'='*58}{RESET}")
    print(f"{BOLD}  MLB PICKS{RESET}")
    print(f"{BOLD}      {today}{RESET}")
    print(f"{BOLD}{'='*58}{RESET}\n")


def main():
    parser = argparse.ArgumentParser(description="MLB Picks del dia")
    parser.add_argument("--partido", type=str, help="Filtrar por equipo")
    parser.add_argument("--manana", action="store_true", help="Solo juegos de manana")
    parser.add_argument("--pendientes", action="store_true")
    parser.add_argument("--historial", action="store_true")
    parser.add_argument("--resultado", nargs=2, metavar=("ID", "RESULT"), help="Marcar resultado")
    args = parser.parse_args()

    if args.pendientes:
        _show_pending()
        return

    if args.historial:
        _show_history()
        return

    if args.resultado:
        _mark_result(args.resultado[0], args.resultado[1])
        return

    ctx = JobContext(job_id="cli", progress=0.0, log=[])

    params = {}
    if args.partido:
        params["partido"] = args.partido
    if args.manana:
        params["solo_manana"] = True

    try:
        result = run_pipeline(params, ctx)
    except Exception as e:
        print(f"[ERROR] {e}")
        return

    print_header()

    picks = result.get("picks", [])
    picks_ml = result.get("picks_ml", [])
    picks_rl = result.get("picks_rl", [])

    if not picks and not picks_ml and not picks_rl:
        print("  No se encontraron picks hoy.\n")
        return

    for pick in picks:
        _print_pick(pick)

    for pick in picks_ml:
        _print_ml_pick(pick)

    for pick in picks_rl:
        _print_rl_pick(pick)

    print(f"\n{BOLD}Total: {len(picks) + len(picks_ml) + len(picks_rl)} picks{RESET}\n")


def _print_pick(pick: dict):
    home  = pick.get("home_team", "?")
    away  = pick.get("away_team", "?")
    direction = pick.get("direction", "?")
    line     = pick.get("line", "?")
    edge     = pick.get("edge", 0)
    conf     = pick.get("confianza", pick.get("confidence", "?"))
    prob     = pick.get("our_prob", 0)
    odds_val = pick.get("odds", -110)

    color = "\033[92m" if edge > 0 else "\033[91m"
    print(f"  {BOLD}{away} @ {home}{RESET}")
    print(f"    {color}{direction} {line}  edge: {edge:+.1%}  prob: {prob:.1%}  odds: {odds_val}  conf: {conf}{RESET}")
    if pick.get("home_pitcher") and isinstance(pick["home_pitcher"], dict):
        hp = pick["home_pitcher"].get("name", "?")
        ap = pick["away_pitcher"].get("name", "?") if isinstance(pick.get("away_pitcher"), dict) else "?"
        print(f"    {ap} @ {hp}")
    print()


def _print_ml_pick(pick: dict):
    team  = pick.get("team", "?")
    edge  = pick.get("edge", 0)
    prob  = pick.get("our_prob", 0)
    odds_val = pick.get("odds", -110)
    color = "\033[92m" if edge > 0 else "\033[91m"
    print(f"  {BOLD}ML {team}{RESET}")
    print(f"    {color}edge: {edge:+.1%}  prob: {prob:.1%}  odds: {odds_val}{RESET}\n")


def _print_rl_pick(pick: dict):
    team    = pick.get("team", "?")
    point   = pick.get("rl_point", -1.5)
    edge    = pick.get("edge", 0)
    prob    = pick.get("our_prob", 0)
    odds_val= pick.get("odds", -110)
    color   = "\033[92m" if edge > 0 else "\033[91m"
    print(f"  {BOLD}RL {team} {point:+.1f}{RESET}")
    print(f"    {color}edge: {edge:+.1%}  prob: {prob:.1%}  odds: {odds_val}{RESET}\n")


def _show_pending():
    picks = database.get_pending(sport="mlb")
    print(f"\n{BOLD}Picks MLB pendientes ({len(picks)}){RESET}\n")
    for p in picks:
        print(f"  [{p['id']}] {p['game']}  {p['selection']}  {p['odds']}  {p['confidence']}")


def _show_history():
    picks = database.get_history(limit=50, sport="mlb")
    wins  = sum(1 for p in picks if p.get("result") == "WIN")
    losses = sum(1 for p in picks if p.get("result") == "LOSS")
    total = wins + losses
    wr = f"{wins/total:.1%}" if total else "--"
    print(f"\n{BOLD}Historial MLB: {wins}W-{losses}L ({wr}){RESET}\n")
    for p in picks[:20]:
        res = p.get("result", "?")
        print(f"  [{p['id']}] {p['game']}  {p['selection']}  {res}  {p.get('profit_cop', 0):+.0f} COP")


def _mark_result(pick_id: str, result: str):
    result = result.upper()
    if result not in ("WIN", "LOSS", "PUSH"):
        print(f"Resultado invalido: {result}. Usar WIN, LOSS, o PUSH.")
        return
    database.mark_result(int(pick_id), result.upper())
    print(f"  Pick #{pick_id} marcado como {result}")


if __name__ == "__main__":
    main()
