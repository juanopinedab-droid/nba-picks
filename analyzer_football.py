"""
analyzer_football.py — Modelo Poisson con xG para Premier League.

Metodología estándar en analítica de fútbol:
  1. Calcular fuerza de ataque y defensa relativa a la liga
  2. Estimar λ (goles esperados) para local y visitante
  3. Construir matriz de probabilidades conjuntas con distribución Poisson
  4. Derivar P(1X2), P(Over/Under 2.5), P(BTTS)
  5. Comparar con probabilidades implícitas del mercado → edge
"""

import math
import config

# ─── CONSTANTES DEL MODELO ────────────────────────────────────────────────────

# Premier League 2024-25 promedios de xG por partido
# (la ventaja de local ya está capturada en esta diferencia home vs away)
LEAGUE_AVG_XG_HOME = 1.55
LEAGUE_AVG_XG_AWAY = 1.20

# Blend temporada / forma reciente (misma lógica que modelo NBA)
_W_SEASON = 0.60
_W_RECENT = 0.40

# Probabilidad mínima para generar un pick
_MIN_PROB_WIN  = 0.52   # para LOCAL / VISITANTE
_MIN_PROB_DRAW = 0.28   # el empate rara vez supera 33%, umbral más bajo
_MIN_PROB_OU   = 0.52   # para Over/Under
_MIN_PROB_BTTS = 0.52   # para BTTS


# ─── UTILIDADES ───────────────────────────────────────────────────────────────

def _poisson_pmf(k: int, lam: float) -> float:
    """P(X = k) para Poisson(λ). Implementación sin scipy."""
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def american_to_prob(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def remove_vig_2way(p_a: float, p_b: float) -> tuple[float, float]:
    total = p_a + p_b
    return p_a / total, p_b / total


def remove_vig_3way(p_home: float, p_draw: float, p_away: float) -> tuple[float, float, float]:
    total = p_home + p_draw + p_away
    return p_home / total, p_draw / total, p_away / total


# ─── MODELO POISSON ───────────────────────────────────────────────────────────

def _blend_xg(season: float, recent: float | None) -> float:
    if recent is None:
        return season
    return round(_W_SEASON * season + _W_RECENT * recent, 4)


def compute_lambdas(home_stats: dict, away_stats: dict) -> tuple[float, float]:
    """
    Calcula λ local y λ visitante usando fuerza relativa a promedios de liga.

    Fórmula estándar Poisson para fútbol:
      λ_home = (atk_home / league_avg_xg_home) * (xga_away / league_avg_xg_home) * league_avg_xg_home
             = atk_home * (xga_away / league_avg_xg_home)
      λ_away = (atk_away / league_avg_xg_away) * (xga_home / league_avg_xg_away) * league_avg_xg_away
             = atk_away * (xga_home / league_avg_xg_away)
    """
    # Blend temporada / reciente para cada equipo
    xg_home  = _blend_xg(home_stats["xg_per_match"],  home_stats.get("xg_recent"))
    xga_home = _blend_xg(home_stats["xga_per_match"], home_stats.get("xga_recent"))
    xg_away  = _blend_xg(away_stats["xg_per_match"],  away_stats.get("xg_recent"))
    xga_away = _blend_xg(away_stats["xga_per_match"], away_stats.get("xga_recent"))

    # Strengths relativas a los promedios de liga
    atk_home = xg_home  / LEAGUE_AVG_XG_HOME
    def_home = xga_home / LEAGUE_AVG_XG_AWAY   # defensa local vs ataque visitante
    atk_away = xg_away  / LEAGUE_AVG_XG_AWAY
    def_away = xga_away / LEAGUE_AVG_XG_HOME   # defensa visitante vs ataque local

    lambda_home = atk_home * def_away * LEAGUE_AVG_XG_HOME
    lambda_away = atk_away * def_home * LEAGUE_AVG_XG_AWAY

    return round(lambda_home, 4), round(lambda_away, 4)


def match_probs(lambda_home: float, lambda_away: float, max_goals: int = 8) -> dict:
    """
    Construye la matriz de probabilidades conjuntas Poisson y deriva todos los mercados.
    """
    p_home = p_draw = p_away = p_over25 = 0.0

    for h in range(max_goals + 1):
        ph = _poisson_pmf(h, lambda_home)
        for a in range(max_goals + 1):
            pa   = _poisson_pmf(a, lambda_away)
            prob = ph * pa
            if h > a:
                p_home  += prob
            elif h == a:
                p_draw  += prob
            else:
                p_away  += prob
            if h + a >= 3:
                p_over25 += prob

    # BTTS: ambos equipos marcan al menos 1 gol
    p_home_scores = 1.0 - _poisson_pmf(0, lambda_home)
    p_away_scores = 1.0 - _poisson_pmf(0, lambda_away)
    p_btts_yes    = p_home_scores * p_away_scores

    # Normalizar 1X2 a 1.0 (puede diferir levemente por el truncado en max_goals)
    total_12x = p_home + p_draw + p_away
    if total_12x > 0:
        p_home /= total_12x
        p_draw /= total_12x
        p_away /= total_12x

    return {
        "p_home":     round(p_home,     4),
        "p_draw":     round(p_draw,     4),
        "p_away":     round(p_away,     4),
        "p_over25":   round(p_over25,   4),
        "p_under25":  round(1 - p_over25, 4),
        "p_btts_yes": round(p_btts_yes, 4),
        "p_btts_no":  round(1 - p_btts_yes, 4),
    }


# ─── ANÁLISIS PRINCIPAL ───────────────────────────────────────────────────────

def _build_pick(game: dict, bet_type: str, selection: str, odds: int,
                our_prob: float, implied_prob: float, edge: float,
                reasons: list) -> dict:
    confidence = (
        "ALTA"  if edge >= 0.08 else
        "MEDIA" if edge >= 0.05 else
        "BAJA"
    )
    # Kelly fraccionario (half Kelly)
    if odds < 0:
        b = 100 / abs(odds)
    else:
        b = odds / 100
    kelly = max(0.0, (our_prob * (b + 1) - 1) / b)
    kelly_half = round(kelly * 0.5, 3)

    return {
        "game":         f"{game['away_team']} @ {game['home_team']}",
        "bet_type":     bet_type,
        "selection":    selection,
        "odds":         odds,
        "our_prob":     our_prob,
        "implied_prob": implied_prob,
        "edge":         edge,
        "kelly_stake":  kelly_half,
        "confidence":   confidence,
        "reasons":      reasons,
        "bookmaker":    game.get("bookmaker", ""),
        "sport":        "football",
    }


def analyze_match(game: dict, home_stats: dict, away_stats: dict) -> dict:
    """
    Analiza un partido EPL y devuelve todos los picks con edge positivo.

    home_stats / away_stats deben tener:
      xg_per_match, xga_per_match (temporada)
      xg_recent, xga_recent       (últimos 5j, opcionales)
    """
    home = game["home_team"]
    away = game["away_team"]
    picks = []

    # ── xG blend para el display ──────────────────────────────────────────────
    home_xg_blend  = _blend_xg(home_stats["xg_per_match"],  home_stats.get("xg_recent"))
    home_xga_blend = _blend_xg(home_stats["xga_per_match"], home_stats.get("xga_recent"))
    away_xg_blend  = _blend_xg(away_stats["xg_per_match"],  away_stats.get("xg_recent"))
    away_xga_blend = _blend_xg(away_stats["xga_per_match"], away_stats.get("xga_recent"))

    def _xg_note(team_stats: dict, label: str) -> str:
        s_xg  = team_stats["xg_per_match"]
        r_xg  = team_stats.get("xg_recent")
        b_xg  = _blend_xg(s_xg, r_xg)
        s_xga = team_stats["xga_per_match"]
        r_xga = team_stats.get("xga_recent")
        b_xga = _blend_xg(s_xga, r_xga)
        recent_note = f" | Últ.{team_stats.get('recent_games', '')}j: {r_xg:.2f}xG" if r_xg else ""
        return (f"{label}: xG {s_xg:.2f}(t){recent_note} → blend {b_xg:.2f}  |  "
                f"xGA {s_xga:.2f}(t) → blend {b_xga:.2f}")

    # ── Lambdas y probs ───────────────────────────────────────────────────────
    lambda_home, lambda_away = compute_lambdas(home_stats, away_stats)
    probs = match_probs(lambda_home, lambda_away)

    base_reasons = [
        _xg_note(home_stats, f"LOCAL  {home}"),
        _xg_note(away_stats, f"VISIT. {away}"),
        f"λ local: {lambda_home:.2f} | λ visitante: {lambda_away:.2f}",
    ]

    # ── Probabilidades implícitas del mercado ─────────────────────────────────
    n_books = game.get("consensus_books", 0)
    if n_books >= 2 and game.get("consensus_impl_home") is not None:
        impl_home = game["consensus_impl_home"]
        impl_draw = game["consensus_impl_draw"]
        impl_away = game["consensus_impl_away"]
        impl_src  = f"consenso {n_books} casas"
    else:
        rh = american_to_prob(game["h2h_home"])
        rd = american_to_prob(game["h2h_draw"])
        ra = american_to_prob(game["h2h_away"])
        impl_home, impl_draw, impl_away = remove_vig_3way(rh, rd, ra)
        impl_src  = game.get("bookmaker", "1 casa")

    # ── Evaluar 1X2 ──────────────────────────────────────────────────────────
    candidates_12x = [
        (probs["p_home"], impl_home, game["h2h_home"], f"{home} (LOCAL)",    _MIN_PROB_WIN),
        (probs["p_draw"], impl_draw, game["h2h_draw"], "EMPATE",             _MIN_PROB_DRAW),
        (probs["p_away"], impl_away, game["h2h_away"], f"{away} (VISITANTE)",_MIN_PROB_WIN),
    ]
    min_edge = getattr(config, "FOOTBALL_MIN_EDGE", config.MIN_EDGE)

    for our_prob, impl_prob, odds, label, min_prob in candidates_12x:
        if odds is None:
            continue
        edge = our_prob - impl_prob
        if our_prob >= min_prob and edge >= min_edge:
            picks.append(_build_pick(
                game=game,
                bet_type="1X2",
                selection=label,
                odds=odds,
                our_prob=our_prob,
                implied_prob=impl_prob,
                edge=edge,
                reasons=base_reasons + [
                    f"P(resultado): {our_prob:.1%} | Mercado ({impl_src}): {impl_prob:.1%}",
                ],
            ))

    # ── Evaluar Over/Under 2.5 ────────────────────────────────────────────────
    if game.get("total_over") and game.get("total_under"):
        raw_over  = american_to_prob(game["total_over"])
        raw_under = american_to_prob(game["total_under"])
        impl_over, impl_under = remove_vig_2way(raw_over, raw_under)
        total_line = game.get("total_line", 2.5)

        # Recalcular over para la línea exacta del mercado (puede ser 2.5, 3.5, etc.)
        p_over_line  = sum(
            _poisson_pmf(h, lambda_home) * _poisson_pmf(a, lambda_away)
            for h in range(9) for a in range(9)
            if h + a > total_line
        )
        p_under_line = 1 - p_over_line

        for our_prob, impl_prob, odds, label in [
            (p_over_line,  impl_over,  game["total_over"],  f"Over {total_line:.1f} goles"),
            (p_under_line, impl_under, game["total_under"], f"Under {total_line:.1f} goles"),
        ]:
            edge = our_prob - impl_prob
            if our_prob >= _MIN_PROB_OU and edge >= min_edge:
                picks.append(_build_pick(
                    game=game,
                    bet_type="TOTAL GOLES",
                    selection=label,
                    odds=odds,
                    our_prob=our_prob,
                    implied_prob=impl_prob,
                    edge=edge,
                    reasons=base_reasons + [
                        f"Goles esperados: {lambda_home:.2f} + {lambda_away:.2f} = {lambda_home+lambda_away:.2f}",
                        f"P({label}): {our_prob:.1%} | Casa: {impl_prob:.1%}",
                    ],
                ))

    # ── Evaluar BTTS ──────────────────────────────────────────────────────────
    if game.get("btts_yes") and game.get("btts_no"):
        raw_yes = american_to_prob(game["btts_yes"])
        raw_no  = american_to_prob(game["btts_no"])
        impl_yes, impl_no = remove_vig_2way(raw_yes, raw_no)

        for our_prob, impl_prob, odds, label in [
            (probs["p_btts_yes"], impl_yes, game["btts_yes"], "BTTS — Sí (ambos anotan)"),
            (probs["p_btts_no"],  impl_no,  game["btts_no"],  "BTTS — No"),
        ]:
            edge = our_prob - impl_prob
            if our_prob >= _MIN_PROB_BTTS and edge >= min_edge:
                picks.append(_build_pick(
                    game=game,
                    bet_type="BTTS",
                    selection=label,
                    odds=odds,
                    our_prob=our_prob,
                    implied_prob=impl_prob,
                    edge=edge,
                    reasons=base_reasons + [
                        f"P(local marca): {1 - _poisson_pmf(0, lambda_home):.1%}  "
                        f"P(visitante marca): {1 - _poisson_pmf(0, lambda_away):.1%}",
                        f"P(BTTS): {our_prob:.1%} | Casa: {impl_prob:.1%}",
                    ],
                ))

    return {
        "game":         f"{away} @ {home}",
        "home_team":    home,
        "away_team":    away,
        "lambda_home":  lambda_home,
        "lambda_away":  lambda_away,
        "p_home":       probs["p_home"],
        "p_draw":       probs["p_draw"],
        "p_away":       probs["p_away"],
        "home_xg":      home_xg_blend,
        "away_xg":      away_xg_blend,
        "home_xga":     home_xga_blend,
        "away_xga":     away_xga_blend,
        "bookmaker":    game.get("bookmaker", ""),
        "picks":        picks,
    }
