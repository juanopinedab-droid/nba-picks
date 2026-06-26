from ..core import database, config
from ..mlb import collector, analyzer
from ..mlb.analyzer import ModelParams


def execute(params: dict, ctx) -> dict:
    season        = params.get("season", "").strip()
    min_edge      = params.get("min_edge")
    allow_over    = params.get("allow_over")
    allow_under   = params.get("allow_under")
    max_picks     = params.get("max_picks")
    max_total_line= params.get("max_total_line")
    bankroll_v    = params.get("bankroll")
    partido       = params.get("partido")
    min_confidence= params.get("min_confidence")
    allow_runline = params.get("allow_runline")
    allow_moneyline=params.get("allow_moneyline")
    allow_f5      = params.get("allow_f5")

    if min_edge is not None:
        config.set_mlb_min_edge(float(min_edge))
    if allow_over is not None:
        config.set_mlb_allow_over(bool(allow_over))
    if allow_under is not None:
        config.set_mlb_allow_under(bool(allow_under))
    if max_picks is not None:
        config.set_mlb_max_picks(int(max_picks))
    if max_total_line is not None:
        config.set_mlb_max_total_line(float(max_total_line))
    if bankroll_v is not None:
        config.set_mlb_bankroll(float(bankroll_v))
    if min_confidence is not None:
        config.set_mlb_min_confidence(str(min_confidence))
    if allow_runline is not None:
        config.set_mlb_allow_runline(bool(allow_runline))
    if allow_moneyline is not None:
        config.set_mlb_allow_moneyline(bool(allow_moneyline))
    if allow_f5 is not None:
        config.set_mlb_allow_f5(bool(allow_f5))

    ctx.log_line("Iniciando análisis MLB...")
    ctx.set_progress(0.02)

    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "pega_tu_key_aqui":
        raise ValueError("Falta ODDS_API_KEY en el archivo .env")

    ctx.log_line("Conectando a The Odds API y MLB Stats API...")
    games = collector.get_todays_mlb_games()
    ctx.set_progress(0.15)

    if partido:
        filtro = partido.lower()
        games = [g for g in games
                 if filtro in (g.get("home_team", "") or "").lower()
                 or filtro in (g.get("away_team", "") or "").lower()]

    ctx.log_line(f"{len(games)} juego(s) MLB encontrados")
    ctx.set_progress(0.20)

    if not games:
        ctx.log_line("No hay juegos MLB programados para hoy ni mañana.")
        ctx.set_progress(1.0)
        return {
            "games": [], "picks": [], "picks_ml": [], "picks_rl": [],
            "picks_f5": [], "bankroll": config.get_mlb_bankroll(),
            "season": season or config.MLB_SEASON,
            "record": _record_dict(),
        }

    model_params = ModelParams(
        min_edge=config.get_mlb_min_edge(),
        allow_over=config.get_mlb_allow_over(),
        allow_under=config.get_mlb_allow_under(),
        max_picks=config.get_mlb_max_picks(),
        max_total_line=config.get_mlb_max_total_line(),
        allow_runline=config.get_mlb_allow_runline(),
        allow_moneyline=config.get_mlb_allow_moneyline(),
        allow_f5=config.get_mlb_allow_f5(),
        min_confidence=config.get_mlb_min_confidence(),
    )

    ctx.log_line("Analizando partidos con modelo MLB...")
    result = analyzer.analyze_games_with_params(games, model_params)
    ctx.set_progress(0.75)

    live_bankroll = database.get_current_bankroll(config.get_mlb_bankroll())

    all_picks = result["picks"] + result["picks_ml"] + result["picks_rl"] + result["picks_f5"]
    if all_picks:
        from ..core import bankroll
        stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks)
        for pick in all_picks:
            stake = stake_map.get(pick.get("selection", ""), 0)
            pick["stake_cop"] = stake
            _save_mlb_pick(pick, stake)

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Análisis MLB completado")

    return {
        "games":   games[:12],
        "picks":   result["picks"],
        "picks_ml":result["picks_ml"],
        "picks_rl":result["picks_rl"],
        "picks_f5":result["picks_f5"],
        "bankroll":live_bankroll,
        "season":  season or config.MLB_SEASON,
        "games_analyzed": result["games_analyzed"],
        "record":  _record_dict(),
    }


def _save_mlb_pick(pick: dict, stake_cop: float = 0):
    home = pick.get("home_team", "?")
    away = pick.get("away_team", "?")
    game_str = f"{away} @ {home}"
    sport = "mlb"

    bet_type = pick.get("bet_type", "TOTAL")
    direction = pick.get("direction", "")
    line = pick.get("line", 0)
    odds_val = int(pick.get("odds", -110) or -110)

    if bet_type == "ML":
        team = pick.get("team", home if direction == "HOME" else away)
        selection = f"ML {team}"
        db_bet_type = "MONEYLINE"
    elif bet_type == "RL":
        team = pick.get("team", home if direction == "HOME" else away)
        rl_point = pick.get("rl_point", -1.5)
        selection = f"RL {team} {rl_point:+.1f}"
        db_bet_type = "RUNLINE"
    elif bet_type == "F5":
        selection = f"{direction} {line}"
        db_bet_type = "F5"
    else:
        selection = f"{direction} {line}"
        db_bet_type = "TOTAL"

    database.save_pick({
        "game":         game_str,
        "bet_type":     db_bet_type,
        "selection":    selection,
        "odds":         odds_val,
        "our_prob":     pick.get("our_prob", 0),
        "implied_prob": pick.get("fair_market", 0),
        "edge":         pick.get("edge", 0),
        "confidence":   pick.get("confianza", pick.get("confidence", "BAJA")),
        "sport":        sport,
        "commence_time": pick.get("commence_iso", pick.get("game_time", "")),
        "context_flags": {
            "game_pk":       pick.get("game_pk"),
            "direction":     direction,
            "home_pitcher":  pick.get("home_pitcher"),
            "away_pitcher":  pick.get("away_pitcher"),
        },
    }, stake_cop=stake_cop)


def _record_dict() -> dict:
    rec = database.get_record()
    return {
        "wins":   rec.get("WIN",  {}).get("count", 0),
        "losses": rec.get("LOSS", {}).get("count", 0),
    }
