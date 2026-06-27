from ..core import database, config, bankroll
from ..nba import collector, analyzer, parlays


def execute(params: dict, ctx) -> dict:
    season      = params.get("season", "").strip()
    min_edge    = params.get("min_edge")
    fetch_props = params.get("fetch_props")
    bankroll_v  = params.get("bankroll")
    partido     = params.get("partido")

    if season:
        collector.set_season(season)
    if min_edge is not None:
        config.set_min_edge(float(min_edge))
    if fetch_props is not None:
        config.set_fetch_props(bool(fetch_props))
    if bankroll_v is not None:
        config.set_bankroll(float(bankroll_v))

    season_label = collector.get_active_season()
    ctx.log_line(f"Iniciando (season: {season_label})...")
    ctx.set_progress(0.02)

    if not config.ODDS_API_KEY or config.ODDS_API_KEY == "pega_tu_key_aqui":
        raise ValueError("Falta ODDS_API_KEY en el archivo .env")

    ctx.log_line("Conectando a The Odds API...")
    games = collector.get_todays_odds()
    ctx.set_progress(0.10)

    if partido:
        filtro = partido.lower()
        games = [g for g in games
                 if filtro in g["home_team"].lower() or filtro in g["away_team"].lower()]

    ctx.log_line(f"{len(games)} juego(s) encontrados")

    if not games:
        ctx.log_line("No hay juegos NBA programados para hoy ni mañana.")
        ctx.set_progress(1.0)
        return {
            "games": [], "picks": [], "props": [], "parlays": [],
            "bankroll": config.get_bankroll(),
            "season": season_label,
            "record": _record_dict(),
        }

    ctx.set_progress(0.15)

    ctx.log_line("Descargando stats de jugadores (NBA API)...")
    player_stats = collector.get_all_player_season_stats()
    ctx.set_progress(0.25)

    ctx.log_line("Reporte de lesiones (ESPN)...")
    injury_report = collector.get_injury_report()
    ctx.set_progress(0.30)

    live_bankroll = database.get_current_bankroll(config.get_bankroll())

    all_game_picks: list = []
    all_prop_picks: list = []
    game_results:   list = []
    total_games = len(games)
    enable_props = config.get_fetch_props()

    for i, game in enumerate(games):
        home = game["home_team"]
        away = game["away_team"]

        home_stats = collector.get_team_stats(home)
        away_stats = collector.get_team_stats(away)

        if not home_stats or not away_stats:
            ctx.log_line(f"Sin stats para {away} @ {home}, saltando...")
            continue

        home_b2b  = collector.is_back_to_back(home)
        away_b2b  = collector.is_back_to_back(away)
        home_rest = collector.get_rest_days(home)
        away_rest = collector.get_rest_days(away)

        home_form  = collector.get_team_recent_form(home)
        away_form  = collector.get_team_recent_form(away)
        home_travel = collector.get_consecutive_away_games(home)
        away_travel = collector.get_consecutive_away_games(away)
        h2h_val    = collector.get_h2h_edge(home, away)

        home_impact = collector.get_team_injury_impact(home, injury_report, player_stats)
        away_impact = collector.get_team_injury_impact(away, injury_report, player_stats)

        home_stats_adj = {
            **home_stats,
            "net_rating":     home_stats["net_rating"] + home_impact["adjustment"],
            "recent_nr":      home_form["recent_nr"] if home_form else None,
            "recent_games":   home_form["games"]     if home_form else 0,
            "travel_fatigue": home_travel,
            "h2h_edge":       h2h_val,
        }
        away_stats_adj = {
            **away_stats,
            "net_rating":     away_stats["net_rating"] + away_impact["adjustment"],
            "recent_nr":      away_form["recent_nr"] if away_form else None,
            "recent_games":   away_form["games"]     if away_form else 0,
            "travel_fatigue": away_travel,
        }

        result = analyzer.analyze_game(
            game, home_stats_adj, away_stats_adj,
            home_b2b, away_b2b, home_rest, away_rest
        )
        result["home_injured_out"]          = home_impact["out"]
        result["home_injured_questionable"] = home_impact["questionable"]
        result["away_injured_out"]          = away_impact["out"]
        result["away_injured_questionable"] = away_impact["questionable"]

        commence = game.get("commence_time", "")
        for pick in result["picks"]:
            pick["commence_time"] = commence
            pick["sport"]         = "nba"

        game_results.append(result)
        all_game_picks.extend(result["picks"])

        if enable_props:
            try:
                raw_props = collector.get_player_props(game["game_id"])
                if raw_props:
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
                    for pick in prop_picks:
                        pick["commence_time"] = commence
                        pick["sport"]         = "nba"
                    all_prop_picks.extend(prop_picks)
            except Exception:
                pass

        n = len(result["picks"])
        ctx.log_line(f"{away} @ {home}: {n} pick(s)")
        ctx.set_progress(0.30 + 0.50 * (i + 1) / total_games)

    all_picks = all_game_picks + all_prop_picks
    stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks)
    for pick in all_picks:
        stake = stake_map.get(pick["selection"], 0)
        pick["stake_cop"] = stake
        database.save_pick(pick, stake_cop=stake)

    ctx.set_progress(0.85)

    parlay_list = parlays.build_parlays(all_game_picks, all_prop_picks)

    ctx.set_progress(1.0)
    ctx.log_line("[OK] Generacion completada")

    return {
        "games":    game_results,
        "picks":    all_game_picks,
        "props":    all_prop_picks,
        "parlays":  parlay_list,
        "bankroll": live_bankroll,
        "season":   season_label,
        "record":   _record_dict(),
    }


def _record_dict() -> dict:
    rec = database.get_record()
    return {
        "wins":   rec.get("WIN",  {}).get("count", 0),
        "losses": rec.get("LOSS", {}).get("count", 0),
    }
