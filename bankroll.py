"""
Gestión de bankroll en COP.
Calcula cuánto apostar en cada pick según diferentes estrategias.
"""

import config

# ─── ESTRATEGIAS ─────────────────────────────────────────────────────────────

STRATEGIES = {
    "conservador": {
        "nombre":      "🛡️  CONSERVADOR",
        "descripcion": "1% fijo por pick. Solo confianza ALTA.",
        "stake_pct":   0.01,
        "solo_alta":   True,
        "max_picks":   2,
        "riesgo":      "BAJO",
        "para_quien":  "Empezando, aprendiendo el sistema.",
    },
    "moderado": {
        "nombre":      "⚖️  MODERADO",
        "descripcion": "2% fijo por pick. ALTA y MEDIA confianza.",
        "stake_pct":   0.02,
        "solo_alta":   False,
        "max_picks":   4,
        "riesgo":      "MEDIO",
        "para_quien":  "Ya tienes historial positivo de 2+ semanas.",
    },
    "kelly": {
        "nombre":      "📐 KELLY (recomendado)",
        "descripcion": "Tamaño proporcional al edge. Half Kelly.",
        "stake_pct":   None,   # usa kelly_stake del pick
        "solo_alta":   False,
        "max_picks":   5,
        "riesgo":      "MEDIO",
        "para_quien":  "Cuando confías en las probabilidades del modelo.",
    },
    "agresivo": {
        "nombre":      "🔥 AGRESIVO",
        "descripcion": "3% por pick. Todos los picks del día.",
        "stake_pct":   0.03,
        "solo_alta":   False,
        "max_picks":   999,
        "riesgo":      "ALTO",
        "para_quien":  "Solo si tienes historial comprobado y aguantas drawdowns.",
    },
}


def format_cop(amount: float) -> str:
    """Formatea un número como pesos colombianos."""
    return f"${amount:,.0f} COP"


def calc_payout(stake: float, odds: int) -> float:
    """Ganancia neta si el pick gana."""
    if odds < 0:
        return stake * (100 / abs(odds))
    return stake * (odds / 100)


def build_strategy_plan(
    bankroll: float,
    all_picks: list[dict],
    strategy_key: str,
) -> dict:
    cfg        = STRATEGIES[strategy_key]
    solo_alta  = cfg["solo_alta"]
    max_picks  = cfg["max_picks"]
    stake_pct  = cfg["stake_pct"]

    # Filtrar picks según estrategia
    eligible = [
        p for p in all_picks
        if (not solo_alta or p["confidence"] == "ALTA")
    ][:max_picks]

    if not eligible:
        return {"strategy": cfg, "bets": [], "total_risk": 0,
                "expected_profit": 0, "bankroll": bankroll}

    bets = []
    total_risk    = 0.0
    expected_profit = 0.0

    for pick in eligible:
        if stake_pct is not None:
            stake = bankroll * stake_pct
        else:
            # Kelly: usa el kelly_stake calculado por el modelo
            stake = bankroll * pick["kelly_stake"]

        stake    = max(stake, 500)      # Mínimo 500 COP
        stake    = round(stake, -2)     # Redondear a centenas

        payout   = calc_payout(stake, pick["odds"])
        ev       = pick["our_prob"] * payout - (1 - pick["our_prob"]) * stake

        total_risk      += stake
        expected_profit += ev

        bets.append({
            "pick":   pick,
            "stake":  stake,
            "payout": payout,
            "ev":     ev,
        })

    return {
        "strategy":        cfg,
        "bets":            bets,
        "total_risk":      total_risk,
        "expected_profit": expected_profit,
        "bankroll":        bankroll,
        "bankroll_after_win_all":  bankroll + sum(b["payout"] for b in bets),
        "bankroll_after_lose_all": bankroll - total_risk,
        "roi_if_win_all":  sum(b["payout"] for b in bets) / total_risk if total_risk else 0,
    }


def calc_stakes_moderado(bankroll: float, picks: list[dict]) -> dict:
    """
    Calcula stakes del camino MODERADO para guardar en DB.
    Retorna dict {selection: stake_cop}.
    """
    result = {}
    cfg    = STRATEGIES["moderado"]
    eligible = [p for p in picks if not cfg["solo_alta"] or p["confidence"] == "ALTA"]
    eligible = eligible[:cfg["max_picks"]]

    for pick in eligible:
        stake = bankroll * cfg["stake_pct"]
        stake = max(stake, 500)
        stake = round(stake, -2)
        result[pick["selection"]] = stake

    return result


def print_bankroll_section(
    bankroll: float,
    all_picks: list[dict],
    bold: str,
    reset: str,
):
    if not all_picks:
        return

    print(f"\n{'━'*58}")
    print(f"{bold}  💰 GESTIÓN DE BANKROLL — {format_cop(bankroll)}{reset}")
    print(f"{'━'*58}")

    for key, cfg in STRATEGIES.items():
        plan = build_strategy_plan(bankroll, all_picks, key)
        bets = plan["bets"]

        riesgo_color = {
            "BAJO":  "\033[92m",
            "MEDIO": "\033[93m",
            "ALTO":  "\033[91m",
        }.get(cfg["riesgo"], "")
        color_reset = "\033[0m"

        print(f"\n  {bold}{cfg['nombre']}{reset}")
        print(f"  {cfg['descripcion']}")
        print(f"  Riesgo: {riesgo_color}{cfg['riesgo']}{color_reset}  |  Para: {cfg['para_quien']}")

        if not bets:
            print(f"  ⚠️  No hay picks que cumplan los filtros de esta estrategia.")
            continue

        print(f"\n  {'PICK':<38} {'APOSTAR':>10}  {'GANARÍAS':>10}  {'EV':>8}")
        print(f"  {'─'*38} {'─'*10}  {'─'*10}  {'─'*8}")

        for b in bets:
            sel    = b["pick"]["selection"][:37]
            stake  = format_cop(b["stake"])
            payout = format_cop(b["payout"])
            ev     = f"{'+' if b['ev'] >= 0 else ''}{format_cop(b['ev'])}"
            print(f"  {sel:<38} {stake:>10}  {payout:>10}  {ev:>8}")

        print(f"\n  Total en riesgo:     {format_cop(plan['total_risk'])}")
        print(f"  Ganancia esperada:   {format_cop(plan['expected_profit'])}")
        print(f"  Bankroll si todo W:  {format_cop(plan['bankroll_after_win_all'])}")
        print(f"  Bankroll si todo L:  {format_cop(plan['bankroll_after_lose_all'])}")

    # Consejo del día
    print(f"\n{'━'*58}")
    print(f"  {bold}📌 REGLA DE ORO{reset}")
    print(f"  Nunca arriesgues más del 5% del bankroll total en un día.")
    print(f"  Hoy eso equivale a {format_cop(bankroll * 0.05)}.")
    print(f"  Con {format_cop(bankroll)}, empieza por el camino CONSERVADOR")
    print(f"  hasta tener 30+ picks registrados con resultado.")
    print(f"  Apuesta mínima: $500 COP por pick.")
