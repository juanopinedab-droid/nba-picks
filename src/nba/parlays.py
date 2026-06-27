"""
Generador de combinadas (parlays).
Mezcla picks de partido con props de jugadores.
Solo combina picks con confianza MEDIA o ALTA.
"""

from itertools import combinations


# Mínima probabilidad conjunta para mostrar la combinada
MIN_PARLAY_PROB = 0.30   # 30% — suficientemente realista
MAX_LEGS        = 3      # Máximo de selecciones por combinada
MAX_PARLAYS     = 6      # Cuántas combinadas mostrar


def to_decimal(american: int) -> float:
    if american < 0:
        return 1 + 100 / abs(american)
    return 1 + american / 100


def to_american(decimal: float) -> int:
    if decimal >= 2.0:
        return round((decimal - 1) * 100)
    return round(-100 / (decimal - 1))


def parlay_odds(picks: list[dict]) -> int:
    decimal = 1.0
    for p in picks:
        decimal *= to_decimal(p["odds"])
    return to_american(decimal)


def parlay_prob(picks: list[dict]) -> float:
    """Probabilidad conjunta asumiendo independencia entre picks."""
    prob = 1.0
    for p in picks:
        prob *= p["our_prob"]
    return prob


def implied_parlay_prob(picks: list[dict]) -> float:
    prob = 1.0
    for p in picks:
        prob *= p["implied_prob"]
    return prob


def is_same_game(a: dict, b: dict) -> bool:
    return a["game"] == b["game"]


def build_parlays(
    game_picks: list[dict],
    prop_picks: list[dict],
) -> list[dict]:
    """
    Genera combinadas de 2 y 3 patas con todos los picks del día.
    Sin restricciones: pueden ir picks del mismo partido juntos.
    Ordena por edge combinado descendente.
    """
    all_picks = [
        p for p in game_picks + prop_picks
        if p["confidence"] in ("ALTA", "MEDIA")
    ]

    if len(all_picks) < 2:
        return []

    parlays = []

    for n_legs in (2, 3):
        if len(all_picks) < n_legs:
            continue

        for combo in combinations(all_picks, n_legs):
            combo = list(combo)

            our_prob     = parlay_prob(combo)
            impl_prob    = implied_parlay_prob(combo)
            combined_odd = parlay_odds(combo)
            edge         = our_prob - impl_prob

            if our_prob < MIN_PARLAY_PROB or edge <= 0:
                continue

            games_in_combo = [p["game"] for p in combo]
            is_sgp = len(games_in_combo) != len(set(games_in_combo))

            parlays.append({
                "legs":       combo,
                "n_legs":     n_legs,
                "our_prob":   our_prob,
                "impl_prob":  impl_prob,
                "edge":       edge,
                "odds":       combined_odd,
                "payout_100": to_decimal(combined_odd) * 100 - 100,
                "is_sgp":     is_sgp,
            })

    parlays.sort(key=lambda x: x["edge"], reverse=True)
    return parlays[:MAX_PARLAYS]


def print_parlays(parlays: list[dict], bold: str, reset: str):
    if not parlays:
        print("\n  📭  No hay combinadas con edge positivo hoy.")
        return

    for i, parlay in enumerate(parlays, 1):
        odds_str = f"+{parlay['odds']}" if parlay['odds'] > 0 else str(parlay['odds'])
        sgp_warn = "  ⚠️  Same-game (correlación no garantizada)" if parlay["is_sgp"] else ""

        print(f"\n  🎯 {bold}COMBINADA #{i} — {parlay['n_legs']} PATAS{reset}{sgp_warn}")
        print(f"     Cuota combinada: {bold}{odds_str}{reset}")
        print(f"     Prob. estimada:  {parlay['our_prob']:.1%}  "
              f"(casa: {parlay['impl_prob']:.1%})")
        print(f"     Edge:            {parlay['edge']:.1%}")
        print(f"     Pago por $100:   ${parlay['payout_100']:.0f}")
        print(f"\n     {bold}Selecciones:{reset}")

        for leg in parlay["legs"]:
            leg_odds  = f"+{leg['odds']}" if leg['odds'] > 0 else str(leg['odds'])
            leg_type  = "🏀" if "PROP" not in leg["bet_type"] else "👤"
            print(f"       {leg_type} {leg['selection']}  ({leg_odds})")
            print(f"          {leg['game']}  — prob: {leg['our_prob']:.1%}")
