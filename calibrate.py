"""
calibrate.py — Calibración automática desde tu historial real

Lee picks_history.db, analiza dónde ganas y dónde pierdes,
y sugiere ajustes concretos a MIN_EDGE, confianza y tipos de apuesta.

Uso:
    python calibrate.py           # análisis completo
    python calibrate.py --apply   # aplicar recomendaciones al .env automáticamente
"""

import argparse
import sqlite3
import math
import re
from pathlib import Path

DB_PATH  = "picks_history.db"
ENV_PATH = Path(".env")

BOLD   = "\033[1m"
RESET  = "\033[0m"
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
GRAY   = "\033[90m"

MIN_SAMPLE = 10   # mínimo de picks para considerar un segmento significativo


# ─── DB ──────────────────────────────────────────────────────────────────────

def get_resolved() -> list[dict]:
    if not Path(DB_PATH).exists():
        return []
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("""
        SELECT bet_type, confidence, edge, our_prob, implied_prob,
               odds, stake_cop, profit_cop, result, date
        FROM picks
        WHERE result IN ('WIN','LOSS','PUSH')
        ORDER BY date
    """).fetchall()
    conn.close()
    return [
        {
            "bet_type":    r[0],
            "confidence":  r[1],
            "edge":        r[2] or 0,
            "our_prob":    r[3] or 0,
            "implied_prob":r[4] or 0,
            "odds":        r[5] or 0,
            "stake":       r[6] or 0,
            "profit":      r[7] or 0,
            "result":      r[8],
            "date":        r[9],
        }
        for r in rows
    ]


# ─── MÉTRICAS ────────────────────────────────────────────────────────────────

def _stats(picks: list[dict]) -> dict:
    if not picks:
        return {"n": 0, "wins": 0, "win_rate": 0, "wagered": 0, "profit": 0, "roi": 0}
    wins    = sum(1 for p in picks if p["result"] == "WIN")
    wagered = sum(p["stake"] for p in picks)
    profit  = sum(p["profit"] for p in picks)
    resolved = sum(1 for p in picks if p["result"] in ("WIN", "LOSS"))
    return {
        "n":        len(picks),
        "wins":     wins,
        "win_rate": wins / resolved if resolved else 0,
        "wagered":  wagered,
        "profit":   profit,
        "roi":      (profit / wagered * 100) if wagered > 0 else 0,
    }


def _color_roi(roi: float) -> str:
    if roi >= 5:   return GREEN
    if roi >= 0:   return YELLOW
    return RED


def _color_wr(wr: float) -> str:
    if wr >= 0.55: return GREEN
    if wr >= 0.48: return YELLOW
    return RED


def _significance(n: int) -> str:
    if n >= 30: return f"{GREEN}●{RESET}"    # significativo
    if n >= 10: return f"{YELLOW}◐{RESET}"   # moderado
    return f"{RED}○{RESET}"                  # muestra pequeña


# ─── ANÁLISIS ────────────────────────────────────────────────────────────────

def section_overview(picks: list[dict]):
    s = _stats(picks)
    print(f"\n{BOLD}  RESUMEN GENERAL{RESET}  ({s['n']} picks resueltos)")
    print(f"  {'━'*48}")
    wr_c  = _color_wr(s["win_rate"])
    roi_c = _color_roi(s["roi"])
    print(f"  Win rate:  {wr_c}{s['win_rate']:.1%}{RESET}   "
          f"({s['wins']}W / {s['n']-s['wins']}L)")
    print(f"  ROI:       {roi_c}{s['roi']:+.1f}%{RESET}   "
          f"(profit {s['profit']:+,.0f} COP)")
    if s["n"] < MIN_SAMPLE:
        print(f"\n  {YELLOW}⚠️  Muestra pequeña ({s['n']} picks). "
              f"Las conclusiones son orientativas hasta tener {MIN_SAMPLE}+.{RESET}")


def section_by_confidence(picks: list[dict]) -> dict:
    print(f"\n{BOLD}  POR NIVEL DE CONFIANZA{RESET}")
    print(f"  {'━'*48}")
    print(f"  {'NIVEL':<10} {'N':>4} {'W%':>6} {'ROI':>7}  SIG  RECOMENDACIÓN")
    print(f"  {'─'*10} {'─'*4} {'─'*6} {'─'*7}  {'─'*3}  {'─'*20}")

    recommendations = {}
    for level in ("ALTA", "MEDIA", "BAJA"):
        group = [p for p in picks if p["confidence"] == level]
        s     = _stats(group)
        sig   = _significance(s["n"])
        wr_c  = _color_wr(s["win_rate"])
        roi_c = _color_roi(s["roi"])

        if s["n"] == 0:
            print(f"  {level:<10} {'—':>4}")
            continue

        # Recomendación
        if s["n"] >= MIN_SAMPLE:
            if s["roi"] < -10:
                rec = f"{RED}❌ Desactivar{RESET}"
                recommendations[level] = "drop"
            elif s["roi"] < 0:
                rec = f"{YELLOW}⚠️  Subir edge{RESET}"
                recommendations[level] = "raise_edge"
            else:
                rec = f"{GREEN}✓  Mantener{RESET}"
                recommendations[level] = "keep"
        else:
            rec = f"{GRAY}Sin datos suficientes{RESET}"

        print(f"  {level:<10} {s['n']:>4} "
              f"{wr_c}{s['win_rate']:>5.1%}{RESET} "
              f"{roi_c}{s['roi']:>+6.1f}%{RESET}  {sig}   {rec}")

    return recommendations


def section_by_bet_type(picks: list[dict]) -> dict:
    print(f"\n{BOLD}  POR TIPO DE APUESTA{RESET}")
    print(f"  {'━'*48}")
    print(f"  {'TIPO':<22} {'N':>4} {'W%':>6} {'ROI':>7}  SIG")
    print(f"  {'─'*22} {'─'*4} {'─'*6} {'─'*7}  {'─'*3}")

    # Agrupar: PROP PTS, PROP REB, etc. → categoría "PROP"
    def normalize(bt: str) -> str:
        if bt.startswith("PROP"):
            return "PROP (todos)"
        return bt

    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for p in picks:
        groups[normalize(p["bet_type"])].append(p)

    disable_types = set()
    for bt, group in sorted(groups.items()):
        s   = _stats(group)
        sig = _significance(s["n"])
        wr_c  = _color_wr(s["win_rate"])
        roi_c = _color_roi(s["roi"])
        print(f"  {bt:<22} {s['n']:>4} "
              f"{wr_c}{s['win_rate']:>5.1%}{RESET} "
              f"{roi_c}{s['roi']:>+6.1f}%{RESET}  {sig}")
        if s["n"] >= MIN_SAMPLE and s["roi"] < -15:
            disable_types.add(bt)

    # Props por stat individual
    prop_picks = [p for p in picks if p["bet_type"].startswith("PROP")]
    if prop_picks:
        print(f"\n  {GRAY}  Desglose props:{RESET}")
        stat_groups: dict[str, list] = defaultdict(list)
        for p in prop_picks:
            stat_groups[p["bet_type"]].append(p)
        for stat, group in sorted(stat_groups.items()):
            s   = _stats(group)
            roi_c = _color_roi(s["roi"])
            print(f"    {stat:<20} {s['n']:>3}  "
                  f"{roi_c}{s['roi']:>+6.1f}% ROI{RESET}  "
                  f"{s['win_rate']:.0%} W")

    return disable_types


def section_by_edge(picks: list[dict]) -> float | None:
    print(f"\n{BOLD}  POR RANGO DE EDGE{RESET}")
    print(f"  {'━'*48}")
    print(f"  {'EDGE':<14} {'N':>4} {'W%':>6} {'ROI':>7}  SIG  ESTADO")
    print(f"  {'─'*14} {'─'*4} {'─'*6} {'─'*7}  {'─'*3}  {'─'*12}")

    buckets = [
        ("4–6%",   0.04, 0.06),
        ("6–8%",   0.06, 0.08),
        ("8–12%",  0.08, 0.12),
        ("12%+",   0.12, 1.00),
    ]

    best_edge = None
    for label, lo, hi in buckets:
        group = [p for p in picks if lo <= p["edge"] < hi]
        s     = _stats(group)
        sig   = _significance(s["n"])
        wr_c  = _color_wr(s["win_rate"])
        roi_c = _color_roi(s["roi"])

        if s["n"] == 0:
            print(f"  {label:<14}    0")
            continue

        estado = (f"{GREEN}✓ rentable{RESET}" if s["roi"] >= 0
                  else f"{RED}✗ negativo{RESET}")
        print(f"  {label:<14} {s['n']:>4} "
              f"{wr_c}{s['win_rate']:>5.1%}{RESET} "
              f"{roi_c}{s['roi']:>+6.1f}%{RESET}  {sig}  {estado}")

        # El edge mínimo recomendado es el primer bucket rentable
        if s["n"] >= MIN_SAMPLE and s["roi"] >= 0 and best_edge is None:
            best_edge = lo

    return best_edge


def section_calibration(picks: list[dict]):
    """¿Cuando decimos X% de prob, ganamos X%?"""
    print(f"\n{BOLD}  CALIBRACIÓN DE PROBABILIDAD{RESET}")
    print(f"  {'━'*48}")
    print(f"  {'PROB ESTIMADA':<16} {'N':>4} {'W% REAL':>9}  DESVIACIÓN")
    print(f"  {'─'*16} {'─'*4} {'─'*9}  {'─'*12}")

    buckets = [
        ("50–55%", 0.50, 0.55),
        ("55–60%", 0.55, 0.60),
        ("60–65%", 0.60, 0.65),
        ("65–70%", 0.65, 0.70),
        ("70%+",   0.70, 1.00),
    ]
    for label, lo, hi in buckets:
        group    = [p for p in picks if lo <= p["our_prob"] < hi]
        resolved = [p for p in group if p["result"] in ("WIN","LOSS")]
        if not resolved:
            print(f"  {label:<16}    0")
            continue
        real_wr  = sum(1 for p in resolved if p["result"]=="WIN") / len(resolved)
        mid_prob = (lo + hi) / 2
        deviation = real_wr - mid_prob
        dev_c = GREEN if abs(deviation) < 0.05 else (YELLOW if abs(deviation) < 0.10 else RED)
        print(f"  {label:<16} {len(resolved):>4} "
              f"{real_wr:>8.1%}   "
              f"{dev_c}{deviation:>+.1%}{RESET}")

    print(f"\n  {GRAY}Desviación < ±5% = bien calibrado.  "
          f"> ±10% = el modelo sobre/sub-estima en ese rango.{RESET}")


def section_recommendations(picks: list[dict], conf_recs: dict,
                             bad_types: set, best_edge: float | None):
    print(f"\n{BOLD}  RECOMENDACIONES{RESET}")
    print(f"  {'━'*48}")

    recs = []

    # MIN_EDGE
    current_edge = _read_env("MIN_EDGE", 0.04)
    if best_edge and best_edge > current_edge:
        recs.append({
            "key":     "MIN_EDGE",
            "current": str(current_edge),
            "new":     str(best_edge),
            "reason":  f"picks con edge < {best_edge:.0%} son negativos en tu historial",
        })
    elif best_edge is None and len(picks) >= MIN_SAMPLE:
        recs.append({
            "key":     "MIN_EDGE",
            "current": str(current_edge),
            "new":     str(round(current_edge + 0.02, 2)),
            "reason":  "ningún bucket de edge muestra ROI positivo — sube el umbral",
        })

    # FETCH_PROPS
    prop_picks = [p for p in picks if p["bet_type"].startswith("PROP")]
    if len(prop_picks) >= MIN_SAMPLE:
        prop_stats = _stats(prop_picks)
        if prop_stats["roi"] < -15:
            recs.append({
                "key":     "FETCH_PROPS",
                "current": "true",
                "new":     "false",
                "reason":  f"props con ROI {prop_stats['roi']:+.1f}% — desactivar ahorra requests",
            })

    if not recs:
        print(f"  {GREEN}✓  Tu configuración actual está alineada con los resultados.{RESET}")
        if len(picks) < MIN_SAMPLE:
            print(f"  {GRAY}  (con más picks las recomendaciones serán más precisas){RESET}")
        return recs

    print(f"  Variable         Actual    →  Nueva      Motivo")
    print(f"  {'─'*16} {'─'*8}     {'─'*8}   {'─'*28}")
    for r in recs:
        print(f"  {r['key']:<16} {r['current']:<8}  →  {CYAN}{r['new']:<8}{RESET}  {r['reason']}")

    return recs


# ─── APLICAR AL .ENV ─────────────────────────────────────────────────────────

def _read_env(key: str, default):
    if not ENV_PATH.exists():
        return default
    for line in ENV_PATH.read_text().splitlines():
        if line.startswith(f"{key}="):
            val = line.split("=", 1)[1].strip()
            try:
                return type(default)(val)
            except Exception:
                return val
    return default


def apply_recommendations(recs: list[dict]):
    if not recs:
        return

    if not ENV_PATH.exists():
        print(f"\n  {RED}❌  No se encontró .env — crea el archivo primero.{RESET}")
        return

    content = ENV_PATH.read_text()
    changed = []

    for r in recs:
        key, new_val = r["key"], r["new"]
        pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={new_val}", content)
        else:
            content += f"\n{key}={new_val}"
        changed.append(f"{key}={new_val}")

    ENV_PATH.write_text(content)
    print(f"\n  {GREEN}✓  .env actualizado:{RESET}")
    for c in changed:
        print(f"     {c}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Calibración desde historial NBA")
    parser.add_argument("--apply", action="store_true",
                        help="Aplicar recomendaciones al .env automáticamente")
    args = parser.parse_args()

    picks = get_resolved()

    print(f"\n{BOLD}  🔬  CALIBRACIÓN NBA PICKS{RESET}")

    if not picks:
        print(f"\n  {YELLOW}Sin picks resueltos en la base de datos.{RESET}")
        print(f"  Marca resultados con: python picks.py --resultado <ID> WIN/LOSS\n")
        return

    section_overview(picks)
    conf_recs  = section_by_confidence(picks)
    bad_types  = section_by_bet_type(picks)
    best_edge  = section_by_edge(picks)
    section_calibration(picks)
    recs       = section_recommendations(picks, conf_recs, bad_types, best_edge)

    print(f"\n  {GRAY}● = muestra significativa (30+)  "
          f"◐ = moderada (10–29)  ○ = pequeña (<10){RESET}\n")

    if args.apply:
        apply_recommendations(recs)
    elif recs:
        print(f"  {YELLOW}→  Para aplicar automáticamente: "
              f"python calibrate.py --apply{RESET}\n")


if __name__ == "__main__":
    main()
