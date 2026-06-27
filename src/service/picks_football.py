from ..core import database, config, bankroll
from ..football import collector_football as collector
from ..football import analyzer_football as analyzer


def execute(params: dict, ctx) -> dict:
    partido = params.get("partido")

    ctx.log_line("Conectando a The Odds API (EPL)...")
    ctx.set_progress(0.05)

    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "pega_tu_key_aqui":
        raise ValueError("Falta ODDS_API_KEY en el archivo .env")

    games = collector.get_todays_epl_matches()
    ctx.set_progress(0.15)
    ctx.log_line(f"{len(games)} partido(s) encontrados")

    if partido:
        filtro = partido.lower()
        games = [g for g in games
                 if filtro in g["home_team"].lower() or filtro in g["away_team"].lower()]

    if not games:
        ctx.set_progress(1.0)
        ctx.log_line("Sin partidos EPL hoy")
        return {"games": [], "picks": [], "bankroll": 0}

    ctx.set_progress(0.20)
    ctx.log_line("Descargando stats EPL (ESPN)...")
    collector.get_all_team_season_stats()
    ctx.set_progress(0.35)

    analyzer_opts = {}
    for key in ("min_edge", "min_prob_win", "min_prob_draw",
                "min_prob_ou", "min_prob_btts",
                "allow_win", "allow_draw", "allow_over", "allow_under", "allow_btts"):
        if key in params:
            analyzer_opts[key] = params[key]

    live_bankroll = database.get_current_bankroll(config.get_bankroll())
    all_picks = []
    game_results = []
    total_games = len(games)

    for i, game in enumerate(games):
        home = game["home_team"]
        away = game["away_team"]

        home_season = collector.get_team_season_stats(home)
        away_season = collector.get_team_season_stats(away)

        if not home_season or not away_season:
            ctx.log_line(f"Sin datos para {away} @ {home}")
            continue

        home_recent = collector.get_team_recent_xg(home)
        away_recent = collector.get_team_recent_xg(away)

        home_stats = {
            **home_season,
            "xg_recent":    home_recent["xg_recent"]  if home_recent else None,
            "xga_recent":   home_recent["xga_recent"] if home_recent else None,
            "recent_games": home_recent["games"]      if home_recent else 0,
        }
        away_stats = {
            **away_season,
            "xg_recent":    away_recent["xg_recent"]  if away_recent else None,
            "xga_recent":   away_recent["xga_recent"] if away_recent else None,
            "recent_games": away_recent["games"]      if away_recent else 0,
        }

        result = analyzer.analyze_match(game, home_stats, away_stats, opts=analyzer_opts if analyzer_opts else None)
        result["commence"] = game.get("commence", "")

        if result and result.get("picks"):
            game_results.append(result)
            all_picks.extend(result["picks"])
            ctx.log_line(f"{away} @ {home}: {len(result['picks'])} pick(s)")

        ctx.set_progress(0.35 + 0.55 * (i + 1) / total_games)

    if all_picks:
        stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks)
        for pick in all_picks:
            stake = stake_map.get(pick["selection"], 0)
            pick["stake_cop"] = stake
            database.save_pick(pick, stake_cop=stake)

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Generacion completada")

    return {
        "games":    game_results,
        "picks":    all_picks,
        "bankroll": live_bankroll,
    }
