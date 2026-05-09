import math
import config

# Varianza típica de cada stat en NBA (coeficiente de variación)
# Cuanto más alta, más impredecible es la stat
_STAT_CV = {
    "PTS":  0.38,
    "REB":  0.45,
    "AST":  0.50,
    "FG3M": 0.70,
}

# Ajuste B2B: reducimos la proyección del jugador si su equipo jugó ayer
_B2B_REDUCTION = 0.08  # 8% menos

# Sensibilidad de cada stat al pace del partido (0=no afecta, 1=efecto completo)
# Total alto → más posesiones → más oportunidades de scoring
_PACE_SENSITIVITY = {
    "PTS":  1.0,
    "FG3M": 0.8,
    "REB":  0.6,
    "AST":  0.4,
}
_NBA_AVG_TOTAL = 225.0  # Total promedio NBA para normalizar

_LEAGUE_AVG_DEF  = 112.5  # DEF_RATING promedio NBA 2024-25
_DEF_SENSITIVITY = {"PTS": 0.8, "FG3M": 0.65, "REB": 0.25, "AST": 0.20}

# Constantes para modelo de totales basado en pace
_NBA_AVG_PACE   = 98.5   # Posesiones por 48 min, promedio liga 2024-25
_TOTAL_STD      = 13.0   # Desviación estándar histórica de totales NBA (pts)


def _normal_cdf(x: float, mean: float, std: float) -> float:
    """Probabilidad acumulada normal — sin dependencias externas."""
    if std <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1 + math.erf((x - mean) / (std * math.sqrt(2))))


def analyze_player_props(
    props: list[dict],
    player_stats: dict,
    home_b2b: bool,
    away_b2b: bool,
    home_team: str,
    away_team: str,
    game_total: float | None = None,
    recent_avgs: dict | None = None,
    home_stats: dict | None = None,
    away_stats: dict | None = None,
) -> list[dict]:
    """
    Evalúa props de jugadores comparando el promedio de temporada con la línea.
    Solo devuelve props con probabilidad >= 58% (umbral conservador).
    """
    MIN_PROB       = 0.58
    MIN_GP         = 15
    MIN_AVG        = 2.0
    MIN_MIN        = 20.0   # ignorar jugadores con < 20 min/j (rol muy variable)
    TOTAL_BAJO     = 215.0  # por debajo de esto, juego defensivo
    EDGE_EXTRA_DEF = 0.03   # edge adicional requerido en partidos defensivos para PTS/FG3M

    # Abreviaturas de los equipos que juegan hoy
    import config as _cfg
    home_abbr = _cfg.TEAM_MAP.get(home_team, "")
    away_abbr = _cfg.TEAM_MAP.get(away_team, "")

    picks = []

    for prop in props:
        player_name = prop["player"]
        nba_col     = prop["nba_col"]
        line        = prop["line"]

        stats = player_stats.get(player_name)
        if not stats:
            continue

        avg     = stats.get(nba_col, 0.0)
        gp      = stats.get("GP", 0)
        minutes = stats.get("MIN", 0.0)

        if gp < MIN_GP or avg < MIN_AVG or minutes < MIN_MIN:
            continue

        # Promedio reciente (últimos 5 juegos PO o RS) — más representativo que el avg de temporada
        recent      = (recent_avgs or {}).get(player_name)
        recent_val  = recent.get(nba_col) if recent and nba_col in recent else None
        recent_src  = recent.get("_source", "") if recent else ""

        # Base de proyección: reciente si existe, si no el promedio de temporada
        base = recent_val if recent_val is not None else avg

        # B2B aplicado SOLO si el equipo del jugador juega B2B
        player_team = stats.get("TEAM_ABBR", "")
        player_is_b2b = (
            (player_team == home_abbr and home_b2b) or
            (player_team == away_abbr and away_b2b)
        )
        b2b_factor = 1 - _B2B_REDUCTION if player_is_b2b else 1.0
        b2b_note   = f" (B2B: -{_B2B_REDUCTION:.0%})" if player_is_b2b else ""

        # Ajuste por pace: total alto → más posesiones → más stats esperadas
        pace_note = ""
        if game_total:
            sensitivity = _PACE_SENSITIVITY.get(nba_col, 0.5)
            pace_mult = 1 + (game_total / _NBA_AVG_TOTAL - 1) * sensitivity
            pace_delta = (pace_mult - 1) * 100
            pace_note = f" (pace {game_total:.0f}: {'+' if pace_delta >= 0 else ''}{pace_delta:.1f}%)"
        else:
            pace_mult = 1.0

        # Ajuste por calidad defensiva del rival
        opp_stats_for_def = (
            away_stats if player_team == home_abbr else
            home_stats if player_team == away_abbr else
            None
        )
        opp_def = opp_stats_for_def.get("def_rating") if opp_stats_for_def else None
        def_mult = 1.0
        def_note = ""
        if opp_def is not None:
            def_sensitivity = _DEF_SENSITIVITY.get(nba_col, 0.3)
            def_adj  = (opp_def - _LEAGUE_AVG_DEF) / _LEAGUE_AVG_DEF * def_sensitivity
            def_mult = 1 + def_adj
            def_note = f" (def rival: {opp_def:.1f}, {def_adj*100:+.1f}%)"

        projection = base * b2b_factor * pace_mult * def_mult
        cv  = _STAT_CV.get(nba_col, 0.45)
        std = base * cv * pace_mult  # std intrínseco al jugador, no ajustado por defensa

        prob_over  = 1 - _normal_cdf(line, projection, std)
        prob_under = 1 - prob_over

        # Probabilidades implícitas de la casa (sin vig)
        raw_over  = american_to_prob(prop["over"])
        raw_under = american_to_prob(prop["under"])
        impl_over, impl_under = remove_vig(raw_over, raw_under)

        best_prob    = max(prob_over, prob_under)
        is_over      = prob_over >= prob_under
        our_prob     = prob_over  if is_over else prob_under
        impl_prob    = impl_over  if is_over else impl_under
        direction    = "Over"     if is_over else "Under"
        odds_to_use  = prop["over"] if is_over else prop["under"]

        edge = our_prob - impl_prob

        # Partidos defensivos (total bajo): exigir más edge en stats de anotación
        juego_defensivo = game_total and game_total < TOTAL_BAJO
        edge_min = config.MIN_EDGE
        if juego_defensivo and nba_col in ("PTS", "FG3M"):
            edge_min += EDGE_EXTRA_DEF

        if our_prob < MIN_PROB or edge < edge_min:
            continue

        diff = projection - line
        diff_str = (
            f"proyectamos {projection:.1f} vs línea {line:.1f} "
            f"({'+' if diff > 0 else ''}{diff:.1f}){b2b_note}{pace_note}{def_note}"
        )

        confidence = "ALTA" if edge >= 0.08 else "MEDIA" if edge >= 0.05 else "BAJA"

        # Load management: si el reciente está >25% abajo del promedio de temporada,
        # puede indicar minutos reducidos → bajar confianza máxima a MEDIA
        if recent_val is not None and avg > 0 and recent_val < avg * 0.75:
            if confidence == "ALTA":
                confidence = "MEDIA"

        # Línea de promedio: muestra reciente Y temporada para contexto
        if recent_val is not None:
            avg_str = (
                f"{recent_src}: {recent_val:.1f}  |  Temporada: {avg:.1f} {prop['label']}/j"
            )
        else:
            playoff_gp = stats.get("playoff_gp", 0)
            blend_pct  = stats.get("blend_pct", 0)
            avg_label  = (
                f"Blend {blend_pct}% PO + {100-blend_pct}% RS ({playoff_gp} PO games)"
                if playoff_gp > 0 else "Regular Season"
            )
            avg_str = f"Promedio {avg_label}: {avg:.1f} {prop['label']}/j ({gp} partidos)"

        picks.append({
            "game":         f"{away_team} @ {home_team}",
            "bet_type":     f"PROP {prop['label'].upper()}",
            "selection":    f"{player_name} — {direction} {line} {prop['label']}",
            "odds":         odds_to_use,
            "our_prob":     our_prob,
            "implied_prob": impl_prob,
            "edge":         edge,
            "kelly_stake":  round(max(0, (our_prob - impl_prob) / (1 - impl_prob)) * 0.5, 3),
            "confidence":   confidence,
            "reasons": [
                avg_str,
                diff_str,
                f"Probabilidad estimada: {our_prob:.1%} | Casa: {impl_prob:.1%}",
                f"Fuente: {prop['bookmaker']}",
            ],
            "bookmaker": prop["bookmaker"],
        })

    # Ordenar por edge descendente
    picks.sort(key=lambda p: p["edge"], reverse=True)
    return picks


def american_to_prob(odds: int) -> float:
    """Probabilidad implícita de las cuotas americanas (incluye el vig)."""
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def remove_vig(prob_home: float, prob_away: float) -> tuple[float, float]:
    """Elimina el margen de la casa para obtener probabilidades reales."""
    total = prob_home + prob_away
    return prob_home / total, prob_away / total


def net_rating_to_prob(net_rating_diff: float) -> float:
    """
    Convierte diferencia de Net Rating en probabilidad de victoria.
    Usa función sigmoide centrada en 50%.

    k = 0.08  →  calibrado con backtest sobre 2374 partidos (2023-24, 2024-25).
    Minimiza Brier Score (0.2151) y corrige sobreconfianza del modelo anterior (k=0.8).

    Ejemplos con k=0.08:
      net_diff =  3  (solo ventaja local)    → 56%  win prob
      net_diff =  6  (favorito claro)        → 65%
      net_diff = 10  (gran favorito)         → 73%
    """
    k = 0.08  # Calibrado con backtest.py — NO cambiar sin re-ejecutar backtest
    return 1 / (1 + math.exp(-k * net_rating_diff))


def analyze_game(game: dict, home_stats: dict, away_stats: dict,
                 home_b2b: bool, away_b2b: bool,
                 home_rest: int, away_rest: int) -> dict:
    """
    Analiza un partido y calcula picks con edge positivo.
    Devuelve un dict con todos los picks válidos del juego.
    """
    picks = []
    reasons = []

    # --- Fuente de datos (regular season / playoffs / blend) ---
    def _data_source_label(stats: dict) -> str:
        pgp = stats.get("playoff_gp", 0)
        pct = stats.get("blend_pct", 0)
        if pgp == 0:
            return "Regular Season"
        return f"Blend {pct}% Playoffs + {100-pct}% RS ({pgp} PO games)"

    reasons.append(f"Datos LOCAL:     {_data_source_label(home_stats)}")
    reasons.append(f"Datos VISITANTE: {_data_source_label(away_stats)}")

    # --- Net Rating: split home/away + blend 60% temporada / 40% forma reciente ---
    _W_SEASON = 0.60
    _W_RECENT = 0.40

    def _blended_nr(stats: dict, label: str, location: str) -> tuple[float, str]:
        """
        Usa el NRtg del split correspondiente (home para LOCAL, away para VISITANTE).
        Si no hay split disponible, cae a NRtg global.
        Luego mezcla con forma reciente (60/40).
        """
        # Elegir NRtg base según si el equipo juega de local o visitante
        if location == "home" and stats.get("net_rating_home") is not None:
            season_nr   = stats["net_rating_home"]
            split_label = "casa"
        elif location == "away" and stats.get("net_rating_away") is not None:
            season_nr   = stats["net_rating_away"]
            split_label = "visita"
        else:
            season_nr   = stats["net_rating"]
            split_label = "global"

        global_nr = stats["net_rating"]
        recent_nr = stats.get("recent_nr")
        n_games   = stats.get("recent_games", 0)

        if recent_nr is not None:
            blended = round(_W_SEASON * season_nr + _W_RECENT * recent_nr, 2)
            note = (f"NRtg {label} [{split_label}]: {season_nr:+.1f} | "
                    f"global {global_nr:+.1f} | "
                    f"últ.{n_games}j {recent_nr:+.1f} → blend {blended:+.1f}")
            return blended, note
        note = (f"NRtg {label} [{split_label}]: {season_nr:+.1f} "
                f"(global {global_nr:+.1f})")
        return season_nr, note

    home_nr, home_nr_note = _blended_nr(home_stats, "LOCAL",     location="home")
    away_nr, away_nr_note = _blended_nr(away_stats, "VISITANTE", location="away")
    reasons.append(home_nr_note)
    reasons.append(away_nr_note)

    adjustments_home = config.HOME_ADVANTAGE_POINTS
    reasons.append(f"Ventaja local: +{config.HOME_ADVANTAGE_POINTS:.1f} pts")

    if home_b2b:
        adjustments_home -= config.B2B_PENALTY_POINTS
        reasons.append(f"Back-to-back LOCAL: -{config.B2B_PENALTY_POINTS:.1f} pts")
    if away_b2b:
        adjustments_home += config.B2B_PENALTY_POINTS
        reasons.append(f"Back-to-back VISITANTE: +{config.B2B_PENALTY_POINTS:.1f} pts")

    # Descanso: efecto no-lineal con techo ~1.5 pts (tanh evita bonus irreales por semanas libres)
    rest_bonus = math.tanh((home_rest - away_rest) / 2) * 1.5
    if abs(rest_bonus) >= 0.5:
        adjustments_home += rest_bonus
        reasons.append(
            f"Descanso ({home_rest}d vs {away_rest}d): {rest_bonus:+.1f} pts"
        )

    home_nr_adjusted = home_nr + adjustments_home
    net_diff = home_nr_adjusted - away_nr

    our_prob_home = net_rating_to_prob(net_diff)
    our_prob_away = 1 - our_prob_home

    # --- Probabilidades implícitas sin vig ---
    # Usar consenso de libros si está disponible; si no, calcular del libro único
    n_books = game.get("consensus_books", 0)
    if n_books >= 2 and game.get("consensus_impl_home") is not None:
        implied_home = game["consensus_impl_home"]
        implied_away = game["consensus_impl_away"]
        impl_source  = f"consenso {n_books} casas"
    else:
        raw_prob_home = american_to_prob(game["h2h_home"])
        raw_prob_away = american_to_prob(game["h2h_away"])
        implied_home, implied_away = remove_vig(raw_prob_home, raw_prob_away)
        impl_source  = game.get("bookmaker", "1 casa")

    # --- Movimiento de línea: edge por libro ---
    books_home = game.get("impl_home_by_book", [])
    books_away = game.get("impl_away_by_book", [])
    n_total    = len(books_home)

    def _book_agreement(our_prob: float, impl_by_book: list[float]) -> tuple[int, str]:
        """Cuántas casas muestran edge positivo para nuestro pick."""
        if not impl_by_book:
            return 0, ""
        agree = sum(1 for p in impl_by_book if our_prob - p >= config.MIN_EDGE)
        return agree, f"Edge en {agree}/{len(impl_by_book)} casas"

    # --- Evaluar MONEYLINE ---
    edge_home = our_prob_home - implied_home
    edge_away = our_prob_away - implied_away

    agree_home, agree_home_str = _book_agreement(our_prob_home, books_home)
    agree_away, agree_away_str = _book_agreement(our_prob_away, books_away)

    # Filtro de movimiento: descartar si menos de la mitad de casas confirman el edge
    home_ok = n_total < 2 or agree_home >= max(1, n_total // 2)
    away_ok  = n_total < 2 or agree_away >= max(1, n_total // 2)

    if edge_home >= config.MIN_EDGE and our_prob_home >= 0.52 and home_ok:
        picks.append(_build_pick(
            game=game,
            bet_type="MONEYLINE",
            selection=f"{game['home_team']} (LOCAL)",
            odds=game["h2h_home"],
            our_prob=our_prob_home,
            implied_prob=implied_home,
            edge=edge_home,
            reasons=reasons + [
                f"NRtg ajustado: {home_nr_adjusted:+.1f} (LOCAL) vs {away_nr:+.1f} (VISIT.)",
                f"Nuestra prob: {our_prob_home:.1%} | Mercado ({impl_source}): {implied_home:.1%}",
                agree_home_str,
            ],
        ))

    if edge_away >= config.MIN_EDGE and our_prob_away >= 0.52 and away_ok:
        picks.append(_build_pick(
            game=game,
            bet_type="MONEYLINE",
            selection=f"{game['away_team']} (VISITANTE)",
            odds=game["h2h_away"],
            our_prob=our_prob_away,
            implied_prob=implied_away,
            edge=edge_away,
            reasons=reasons + [
                f"NRtg ajustado: {away_nr:+.1f} (VISIT.) vs {home_nr_adjusted:+.1f} (LOCAL)",
                f"Nuestra prob: {our_prob_away:.1%} | Mercado ({impl_source}): {implied_away:.1%}",
                agree_away_str,
            ],
        ))

    # --- Evaluar SPREAD (si hay datos) ---
    if game["spread_home_pts"] is not None:
        spread_pick = _analyze_spread(
            game, net_diff, our_prob_home, reasons
        )
        if spread_pick:
            picks.append(spread_pick)

    # --- Evaluar TOTAL con modelo de pace ---
    total_pick = _analyze_total(
        game, home_stats, away_stats,
        home_b2b, away_b2b, reasons
    )
    if total_pick:
        picks.append(total_pick)

    return {
        "game":            f"{game['away_team']} @ {game['home_team']}",
        "home_record":     f"{home_stats['wins']}-{home_stats['losses']}",
        "away_record":     f"{away_stats['wins']}-{away_stats['losses']}",
        "home_net_rating": home_nr,   # ya es el blended
        "away_net_rating": away_nr,   # ya es el blended
        "net_diff":        net_diff,
        "home_b2b":        home_b2b,
        "away_b2b":        away_b2b,
        "bookmaker":       game["bookmaker"],
        "picks":           picks,
    }


def _analyze_spread(game: dict, net_diff: float,
                    our_prob_home: float, reasons: list) -> dict | None:
    """
    Evalúa si hay valor en el spread usando Net Rating diferencial.
    Regla: Net Rating diff / 2 ≈ spread esperado.
    """
    market_spread = game["spread_home_pts"]  # Negativo = favorito local
    our_spread    = -net_diff / 2            # Negativo = predecimos que home gana por eso

    spread_diff = our_spread - market_spread

    # Si predecimos que el local gana por MÁS de lo que dice la línea
    if spread_diff < -1.5 and our_prob_home > 0.55:
        spread_edge = min(abs(spread_diff) / 20, 0.12)  # Normalizado
        if spread_edge >= config.MIN_EDGE:
            return _build_pick(
                game=game,
                bet_type="SPREAD",
                selection=f"{game['home_team']} {market_spread:+.1f}",
                odds=game["spread_home"],
                our_prob=our_prob_home,
                implied_prob=american_to_prob(game["spread_home"]),
                edge=spread_edge,
                reasons=reasons + [
                    f"Spread mercado: {market_spread:+.1f} | Nuestro estimado: {our_spread:+.1f}",
                ],
            )

    # Si predecimos que el visitante cubre
    if spread_diff > 1.5 and our_prob_home < 0.45:
        spread_edge = min(abs(spread_diff) / 20, 0.12)
        if spread_edge >= config.MIN_EDGE:
            away_spread_pts = game["spread_away_pts"]
            return _build_pick(
                game=game,
                bet_type="SPREAD",
                selection=f"{game['away_team']} {away_spread_pts:+.1f}",
                odds=game["spread_away"],
                our_prob=1 - our_prob_home,
                implied_prob=american_to_prob(game["spread_away"]),
                edge=spread_edge,
                reasons=reasons + [
                    f"Spread mercado: {away_spread_pts:+.1f} | Nuestro estimado: {-our_spread:+.1f}",
                ],
            )

    return None


def _analyze_total(game: dict, home_stats: dict, away_stats: dict,
                   home_b2b: bool, away_b2b: bool, reasons: list) -> dict | None:
    """
    Evalúa Over/Under usando pace y ratings ofensivos/defensivos reales de cada equipo.

    Fórmula estándar de proyección de totales NBA:
      combined_pace  = (home_pace + away_pace) / 2
      home_pts_proj  = (home_off_rtg + away_def_rtg) / 2  *  combined_pace / 100
      away_pts_proj  = (away_off_rtg + home_def_rtg) / 2  *  combined_pace / 100
      projected_total = home_pts_proj + away_pts_proj

    La intuición: blend del ataque propio vs la defensa rival, escalado por posesiones reales.
    Un partido OKC (pace 101) vs BOS (pace 96) tendrá ~98.5 posesiones → ~225 puntos.
    Un partido MEM (pace 104) vs ATL (pace 102) tendrá ~103 posesiones → más scoring.
    """
    if not game.get("total_over") or not game.get("total_under"):
        return None

    total_line = game.get("total_line")
    if total_line is None:
        return None

    # Stats necesarias del collector
    home_pace = home_stats.get("pace")
    away_pace = away_stats.get("pace")
    home_off  = home_stats.get("off_rating")
    away_off  = away_stats.get("off_rating")
    home_def  = home_stats.get("def_rating")
    away_def  = away_stats.get("def_rating")

    if any(v is None for v in [home_pace, away_pace, home_off, away_off, home_def, away_def]):
        return None  # Sin datos suficientes para el modelo

    combined_pace = (home_pace + away_pace) / 2

    # Puntos proyectados: blend ataque propio + defensa rival, escalado por pace
    home_pts = (home_off + away_def) / 2 * combined_pace / 100
    away_pts  = (away_off + home_def) / 2 * combined_pace / 100

    # Ajuste B2B: la fatiga baja el ataque y sube los puntos concedidos
    # Efecto neto en el total: ~2-3 pts menos por equipo en B2B
    if home_b2b:
        home_pts *= 0.975   # -2.5% anotación por fatiga
        away_pts *= 1.015   # +1.5% rival aprovecha defensa baja
    if away_b2b:
        away_pts *= 0.975
        home_pts *= 1.015

    projected_total = home_pts + away_pts

    # Std dev del total: escala con pace (juegos más rápidos = más varianza)
    pace_factor = combined_pace / _NBA_AVG_PACE
    total_std   = _TOTAL_STD * pace_factor

    # Probabilidades con distribución normal
    p_over  = 1 - _normal_cdf(total_line, projected_total, total_std)
    p_under = 1 - p_over

    # Implied sin vig
    raw_over  = american_to_prob(game["total_over"])
    raw_under = american_to_prob(game["total_under"])
    impl_over, impl_under = remove_vig(raw_over, raw_under)

    best_is_over = p_over >= p_under
    our_prob  = p_over    if best_is_over else p_under
    impl_prob = impl_over if best_is_over else impl_under
    direction = "Over"    if best_is_over else "Under"
    odds      = game["total_over"] if best_is_over else game["total_under"]

    edge = our_prob - impl_prob

    if our_prob < 0.52 or edge < config.MIN_EDGE:
        return None

    diff_str = f"{projected_total - total_line:+.1f}"
    b2b_note = ""
    if home_b2b or away_b2b:
        b2b_note = f" | B2B {'LOCAL' if home_b2b else 'VISIT.'}: ajuste fatiga aplicado"

    return _build_pick(
        game=game,
        bet_type="TOTAL",
        selection=f"{direction} {total_line}",
        odds=odds,
        our_prob=our_prob,
        implied_prob=impl_prob,
        edge=edge,
        reasons=reasons + [
            (f"Pace: LOCAL {home_pace:.1f} | VISIT. {away_pace:.1f} "
             f"→ combinado {combined_pace:.1f} pos/48min"),
            (f"Pts proyectados: LOCAL {home_pts:.1f} + VISIT. {away_pts:.1f} "
             f"= {projected_total:.1f}{b2b_note}"),
            (f"Línea: {total_line} | Proyección: {projected_total:.1f} ({diff_str}) "
             f"| σ={total_std:.1f}"),
            f"P({direction}): {our_prob:.1%} | Casa: {impl_prob:.1%}",
        ],
    )


def _build_pick(game: dict, bet_type: str, selection: str, odds: int,
                our_prob: float, implied_prob: float, edge: float,
                reasons: list) -> dict:
    confidence = (
        "ALTA"   if edge >= 0.08 else
        "MEDIA"  if edge >= 0.05 else
        "BAJA"
    )

    kelly = max(0, (our_prob * (1 + 100 / abs(odds) if odds < 0 else odds / 100)
                    - (1 - our_prob)) / (100 / abs(odds) if odds < 0 else odds / 100))
    kelly_half = round(kelly * 0.5, 3)  # Half Kelly por seguridad

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
        "bookmaker":    game["bookmaker"],
    }
