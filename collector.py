import time
import requests
from datetime import date, timedelta, datetime
from nba_api.stats.endpoints import LeagueDashTeamStats, TeamGameLog, LeagueDashPlayerStats, PlayerGameLog
from nba_api.stats.static import teams as nba_teams_static
from nba_api.stats.static import players as nba_players_static

import config
from utils import http_get

# Cache en memoria para no repetir llamadas a nba_api en la misma sesión
_regular_team_cache:      dict = {}
_playoff_team_cache:      dict = {}
_regular_home_cache:      dict = {}   # splits de local
_regular_away_cache:      dict = {}   # splits de visitante
_playoff_home_cache:      dict = {}
_playoff_away_cache:      dict = {}
_regular_player_cache:    dict = {}
_playoff_player_cache:    dict = {}
_gamelog_cache:           dict = {}
_player_recent_cache:     dict = {}  # {player_name: {stat: valor, _source, _games}}


# ─── UTILIDADES ──────────────────────────────────────────────────────────────

def _name_to_abbr_map() -> dict:
    static_teams = nba_teams_static.get_teams()
    return {t["full_name"]: t["abbreviation"] for t in static_teams}


def _blend(regular: dict, playoff: dict | None, playoff_gp: int) -> dict:
    """
    Mezcla stats de temporada regular con playoffs.
    A más juegos de playoffs, más peso tienen esos datos.
    Máximo 65% peso playoffs con 10+ partidos jugados.
    """
    if not playoff or playoff_gp == 0:
        return regular

    w_p = min(0.65, playoff_gp / 15)   # 0 → 0%  |  10 → 65%
    w_r = 1 - w_p

    numeric_keys = {k for k in regular if isinstance(regular[k], (int, float))}

    blended = dict(regular)
    for k in numeric_keys:
        if k in playoff:
            blended[k] = round(w_p * playoff[k] + w_r * regular[k], 2)

    # wins/losses son solo de regular season (para el record mostrado)
    blended["wins"]          = regular.get("wins", 0)
    blended["losses"]        = regular.get("losses", 0)
    blended["playoff_gp"]    = playoff_gp
    blended["playoff_w"]     = playoff.get("wins", 0)
    blended["playoff_l"]     = playoff.get("losses", 0)
    blended["blend_pct"]     = round(w_p * 100)

    return blended


def _fetch_team_stats_for_season_type(season_type: str) -> dict:
    """Llama a LeagueDashTeamStats y retorna dict keyed por abreviatura."""
    time.sleep(1)
    endpoint = LeagueDashTeamStats(
        season=config.NBA_SEASON,
        measure_type_detailed_defense="Advanced",
        per_mode_detailed="PerGame",
        season_type_all_star=season_type,
    )
    df = endpoint.get_data_frames()[0]

    abbr_col    = next((c for c in df.columns if "ABBREVIATION" in c), None)
    name_to_abbr = _name_to_abbr_map()
    result = {}

    for _, row in df.iterrows():
        abbr = row[abbr_col] if abbr_col else name_to_abbr.get(row.get("TEAM_NAME", ""), "")
        if not abbr:
            continue
        result[abbr] = {
            "net_rating": float(row.get("NET_RATING", row.get("E_NET_RATING", 0))),
            "off_rating": float(row.get("OFF_RATING", row.get("E_OFF_RATING", 0))),
            "def_rating": float(row.get("DEF_RATING", row.get("E_DEF_RATING", 0))),
            "pace":       float(row.get("PACE",       row.get("E_PACE",       0))),
            "wins":       int(row.get("W", 0)),
            "losses":     int(row.get("L", 0)),
        }

    return result


def _fetch_team_splits(season_type: str, location: str) -> dict:
    """
    Descarga Net Rating / Off / Def / Pace filtrado por LOCAL o VISITANTE.
    location: "Home" | "Road"
    Retorna dict keyed por abreviatura con net_rating, off_rating, def_rating, pace.
    """
    time.sleep(1)
    try:
        endpoint = LeagueDashTeamStats(
            season=config.NBA_SEASON,
            measure_type_detailed_defense="Advanced",
            per_mode_detailed="PerGame",
            season_type_all_star=season_type,
            location_nullable=location,
        )
        df = endpoint.get_data_frames()[0]
    except Exception as e:
        print(f"  ⚠️  Home/Away splits error ({season_type}/{location}): {e}")
        return {}

    abbr_col     = next((c for c in df.columns if "ABBREVIATION" in c), None)
    name_to_abbr = _name_to_abbr_map()
    result = {}

    for _, row in df.iterrows():
        abbr = row[abbr_col] if abbr_col else name_to_abbr.get(row.get("TEAM_NAME", ""), "")
        if not abbr:
            continue
        result[abbr] = {
            "net_rating": float(row.get("NET_RATING", row.get("E_NET_RATING", 0))),
            "off_rating": float(row.get("OFF_RATING", row.get("E_OFF_RATING", 0))),
            "def_rating": float(row.get("DEF_RATING", row.get("E_DEF_RATING", 0))),
            "pace":       float(row.get("PACE",       row.get("E_PACE",       0))),
        }
    return result


def _fetch_player_stats_for_season_type(season_type: str) -> dict:
    """Llama a LeagueDashPlayerStats y retorna dict keyed por nombre."""
    time.sleep(1)
    endpoint = LeagueDashPlayerStats(
        season=config.NBA_SEASON,
        per_mode_detailed="PerGame",
        season_type_all_star=season_type,
    )
    df = endpoint.get_data_frames()[0]

    result = {}
    for _, row in df.iterrows():
        name = row.get("PLAYER_NAME", "")
        if not name:
            continue
        result[name] = {
            "PTS":         float(row.get("PTS",         0)),
            "REB":         float(row.get("REB",         0)),
            "AST":         float(row.get("AST",         0)),
            "FG3M":        float(row.get("FG3M",        0)),
            "GP":          int(row.get("GP",            0)),
            "MIN":         float(row.get("MIN",         0)),
            "PLUS_MINUS":  float(row.get("PLUS_MINUS",  0)),
            "TEAM_ABBR":   str(row.get("TEAM_ABBREVIATION", "")),
        }

    return result


# ─── API PÚBLICA ─────────────────────────────────────────────────────────────

def get_todays_odds() -> list[dict]:
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        "apiKey":     config.ODDS_API_KEY,
        "regions":    "us",
        "markets":    "h2h,spreads,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    resp = http_get(url, params=params, timeout=15)

    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"  [API] Requests restantes este mes: {remaining}")

    today    = date.today()
    tomorrow = today + timedelta(days=1)
    games = []
    for g in resp.json():
        home = g["home_team"]
        away = g["away_team"]
        if home not in config.TEAM_MAP or away not in config.TEAM_MAP:
            continue

        # Filtrar juegos de HOY y MAÑANA en hora local
        try:
            utc_dt   = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone()
            if local_dt.date() not in (today, tomorrow):
                continue
        except Exception:
            pass  # si no se puede parsear la fecha, incluir el juego igual

        odds = _extract_best_odds(g["bookmakers"], home, away)
        if not odds:
            continue
        games.append({
            "game_id":   g["id"],
            "home_team": home,
            "away_team": away,
            "commence":  g["commence_time"],
            **odds,
        })

    return games


def _extract_best_odds(bookmakers: list, home: str, away: str) -> dict | None:
    best = {
        "h2h_home": None, "h2h_away": None,
        "spread_home": None, "spread_home_pts": None,
        "spread_away": None, "spread_away_pts": None,
        "total_line": None, "total_over": None, "total_under": None,
        "bookmaker": None,
    }

    sorted_books = sorted(
        bookmakers,
        key=lambda b: config.PREFERRED_BOOKS.index(b["key"])
        if b["key"] in config.PREFERRED_BOOKS else 99
    )

    # Acumular probabilidades implícitas sin vig de todos los libros disponibles
    implied_home_list: list[float] = []
    implied_away_list: list[float] = []

    for book in sorted_books:
        markets = {m["key"]: m["outcomes"] for m in book["markets"]}

        if "h2h" in markets:
            odds_home = odds_away = None
            for o in markets["h2h"]:
                if o["name"] == home:
                    odds_home = o["price"]
                elif o["name"] == away:
                    odds_away = o["price"]

            if odds_home and odds_away:
                # Guardar el primer libro preferido como odds de referencia
                if best["h2h_home"] is None:
                    best["h2h_home"] = odds_home
                    best["h2h_away"] = odds_away
                    best["bookmaker"] = book["title"]

                # Calcular implied prob sin vig para este libro y acumular
                raw_h = abs(odds_home) / (abs(odds_home) + 100) if odds_home < 0 else 100 / (odds_home + 100)
                raw_a = abs(odds_away) / (abs(odds_away) + 100) if odds_away < 0 else 100 / (odds_away + 100)
                total = raw_h + raw_a
                implied_home_list.append(raw_h / total)
                implied_away_list.append(raw_a / total)

        if "spreads" in markets and best["spread_home"] is None:
            for o in markets["spreads"]:
                if o["name"] == home:
                    best["spread_home"]     = o["price"]
                    best["spread_home_pts"] = o["point"]
                elif o["name"] == away:
                    best["spread_away"]     = o["price"]
                    best["spread_away_pts"] = o["point"]

        if "totals" in markets and best["total_line"] is None:
            for o in markets["totals"]:
                if o["name"] == "Over":
                    best["total_over"] = o["price"]
                    best["total_line"] = o["point"]
                elif o["name"] == "Under":
                    best["total_under"] = o["price"]

    if best["h2h_home"] is None:
        return None

    # Consenso: promedio de implied probs sin vig de todos los libros
    if implied_home_list:
        best["consensus_impl_home"] = round(sum(implied_home_list) / len(implied_home_list), 6)
        best["consensus_impl_away"] = round(sum(implied_away_list) / len(implied_away_list), 6)
        best["consensus_books"]     = len(implied_home_list)
        best["impl_home_by_book"]   = implied_home_list
        best["impl_away_by_book"]   = implied_away_list
    else:
        best["consensus_impl_home"] = None
        best["consensus_impl_away"] = None
        best["consensus_books"]     = 0
        best["impl_home_by_book"]   = []
        best["impl_away_by_book"]   = []

    return best


def get_team_stats(team_name: str) -> dict | None:
    """
    Retorna stats mezcladas de regular season + playoffs, incluyendo
    splits de home (net_rating_home) y away (net_rating_away).
    """
    global _regular_team_cache, _playoff_team_cache
    global _regular_home_cache, _regular_away_cache
    global _playoff_home_cache, _playoff_away_cache

    # ── Stats globales ────────────────────────────────────────────────────────
    if not _regular_team_cache:
        print("  [NBA API] Stats equipos — Regular Season...", end=" ", flush=True)
        _regular_team_cache = _fetch_team_stats_for_season_type("Regular Season")
        print("OK")

    if not _playoff_team_cache:
        print("  [NBA API] Stats equipos — Playoffs...", end=" ", flush=True)
        try:
            _playoff_team_cache = _fetch_team_stats_for_season_type("Playoffs")
            print(f"OK ({len(_playoff_team_cache)} equipos)")
        except Exception as e:
            print(f"Sin datos ({e})")
            _playoff_team_cache = {}

    # ── Splits home/away ──────────────────────────────────────────────────────
    if not _regular_home_cache:
        print("  [NBA API] Splits LOCAL — Regular Season...", end=" ", flush=True)
        _regular_home_cache = _fetch_team_splits("Regular Season", "Home")
        print("OK")

    if not _regular_away_cache:
        print("  [NBA API] Splits VISITANTE — Regular Season...", end=" ", flush=True)
        _regular_away_cache = _fetch_team_splits("Regular Season", "Road")
        print("OK")

    if not _playoff_home_cache:
        print("  [NBA API] Splits LOCAL — Playoffs...", end=" ", flush=True)
        try:
            _playoff_home_cache = _fetch_team_splits("Playoffs", "Home")
            print(f"OK ({len(_playoff_home_cache)} equipos)")
        except Exception as e:
            print(f"Sin datos ({e})")
            _playoff_home_cache = {}

    if not _playoff_away_cache:
        print("  [NBA API] Splits VISITANTE — Playoffs...", end=" ", flush=True)
        try:
            _playoff_away_cache = _fetch_team_splits("Playoffs", "Road")
            print(f"OK ({len(_playoff_away_cache)} equipos)")
        except Exception as e:
            print(f"Sin datos ({e})")
            _playoff_away_cache = {}

    # ── Blend global ──────────────────────────────────────────────────────────
    abbr    = config.TEAM_MAP.get(team_name)
    regular = _regular_team_cache.get(abbr)
    playoff = _playoff_team_cache.get(abbr)

    if not regular:
        return None

    playoff_gp = playoff.get("wins", 0) + playoff.get("losses", 0) if playoff else 0
    blended = _blend(regular, playoff, playoff_gp)

    # ── Inyectar splits home/away (con blend RS + playoffs) ───────────────────
    def _blend_split(reg_split: dict | None, po_split: dict | None, po_gp: int) -> float | None:
        """Blend del NRtg del split, usando misma proporción que el blend global."""
        if not reg_split:
            return None
        reg_nr = reg_split.get("net_rating", 0)
        if not po_split or po_gp == 0:
            return reg_nr
        po_nr = po_split.get("net_rating", 0)
        w_p   = min(0.65, po_gp / 15)
        return round(w_p * po_nr + (1 - w_p) * reg_nr, 2)

    blended["net_rating_home"] = _blend_split(
        _regular_home_cache.get(abbr),
        _playoff_home_cache.get(abbr),
        playoff_gp,
    )
    blended["net_rating_away"] = _blend_split(
        _regular_away_cache.get(abbr),
        _playoff_away_cache.get(abbr),
        playoff_gp,
    )

    return blended


def get_all_player_season_stats() -> dict:
    """
    Retorna stats mezcladas de regular season + playoffs por jugador.
    """
    global _regular_player_cache, _playoff_player_cache

    if not _regular_player_cache:
        print("  [NBA API] Stats jugadores — Regular Season...", end=" ", flush=True)
        _regular_player_cache = _fetch_player_stats_for_season_type("Regular Season")
        print("OK")

    if not _playoff_player_cache:
        print("  [NBA API] Stats jugadores — Playoffs...", end=" ", flush=True)
        try:
            _playoff_player_cache = _fetch_player_stats_for_season_type("Playoffs")
            print(f"OK ({len(_playoff_player_cache)} jugadores con datos)")
        except Exception as e:
            print(f"Sin datos ({e})")
            _playoff_player_cache = {}

    # Mezclar por jugador
    all_names = set(_regular_player_cache) | set(_playoff_player_cache)
    blended = {}

    for name in all_names:
        regular = _regular_player_cache.get(name)
        playoff = _playoff_player_cache.get(name)

        if not regular:
            blended[name] = playoff
            continue

        playoff_gp = playoff.get("GP", 0) if playoff else 0
        blended[name] = _blend(regular, playoff, playoff_gp)

    return blended


def is_back_to_back(team_name: str) -> bool:
    """Revisa si el equipo jugó ayer (busca en log de playoffs primero, luego regular)."""
    global _gamelog_cache

    abbr = config.TEAM_MAP.get(team_name)
    if not abbr:
        return False

    if abbr not in _gamelog_cache:
        all_teams = nba_teams_static.get_teams()
        team_info = next((t for t in all_teams if t["abbreviation"] == abbr), None)
        if not team_info:
            return False

        time.sleep(0.8)

        # Intentar playoffs primero (más reciente en mayo)
        try:
            log = TeamGameLog(
                team_id=team_info["id"],
                season=config.NBA_SEASON,
                season_type_all_star="Playoffs",
            )
            df = log.get_data_frames()[0]
        except Exception:
            df = None

        # Si no tiene juegos de playoffs, usar regular season
        if df is None or df.empty:
            time.sleep(0.8)
            log = TeamGameLog(
                team_id=team_info["id"],
                season=config.NBA_SEASON,
                season_type_all_star="Regular Season",
            )
            df = log.get_data_frames()[0]

        _gamelog_cache[abbr] = df

    df = _gamelog_cache[abbr]
    if df.empty:
        return False

    yesterday = date.today() - timedelta(days=1)
    last_game_str = df.iloc[0]["GAME_DATE"]

    try:
        last_game = datetime.strptime(last_game_str, "%b %d, %Y").date()
        return last_game == yesterday
    except ValueError:
        return False


def get_rest_days(team_name: str) -> int:
    abbr = config.TEAM_MAP.get(team_name)
    if not abbr or abbr not in _gamelog_cache:
        return 2

    df = _gamelog_cache[abbr]
    if df.empty:
        return 2

    last_game_str = df.iloc[0]["GAME_DATE"]
    try:
        last_game = datetime.strptime(last_game_str, "%b %d, %Y").date()
        return (date.today() - last_game).days
    except ValueError:
        return 2


def get_consecutive_away_games(team_name: str) -> int:
    """
    Consecutive away games the team has played before today's game.
    Reuses the gamelog cached by is_back_to_back() — no extra API calls.
    Signal: 3+ consecutive road games indicate cumulative travel fatigue.
    """
    abbr = config.TEAM_MAP.get(team_name)
    if not abbr or abbr not in _gamelog_cache:
        return 0
    df = _gamelog_cache[abbr]
    if df.empty or "MATCHUP" not in df.columns:
        return 0
    count = 0
    for _, row in df.iterrows():
        if "@" in str(row.get("MATCHUP", "")):
            count += 1
        else:
            break
    return count


def get_h2h_edge(home_team: str, away_team: str, n: int = 10) -> float:
    """
    Historical edge of home_team vs away_team in direct matchups (last n meetings).
    Reuses the gamelog cached by is_back_to_back() — no extra API calls.
    Returns Net Rating adjustment: +2.0 (home dominates) to -2.0 (home always loses).
    Returns 0.0 if fewer than 3 H2H games are available.
    """
    home_abbr = config.TEAM_MAP.get(home_team, "")
    away_abbr = config.TEAM_MAP.get(away_team, "")
    if not home_abbr or not away_abbr or home_abbr not in _gamelog_cache:
        return 0.0
    df = _gamelog_cache[home_abbr]
    if df.empty or "MATCHUP" not in df.columns or "WL" not in df.columns:
        return 0.0
    h2h = df[df["MATCHUP"].str.contains(away_abbr, na=False)].head(n)
    if len(h2h) < 3:
        return 0.0
    win_rate = h2h["WL"].eq("W").sum() / len(h2h)
    return round((win_rate - 0.5) * 4.0, 2)  # max ±2.0 pts


def get_team_recent_form(team_name: str, n: int = 5) -> dict | None:
    """
    Promedio de PLUS_MINUS de los últimos n partidos del equipo.
    Reutiliza el game log ya cacheado por is_back_to_back() — sin llamadas extra.
    Retorna {"recent_nr": float, "games": int} o None si no hay datos.
    PLUS_MINUS ≈ Net Rating (ambos en escala de puntos por 100 posesiones ≈ puntos por partido).
    """
    abbr = config.TEAM_MAP.get(team_name)
    if not abbr or abbr not in _gamelog_cache:
        return None

    df = _gamelog_cache[abbr]
    if df.empty or "PLUS_MINUS" not in df.columns:
        return None

    recent = df.head(n)
    return {
        "recent_nr": round(float(recent["PLUS_MINUS"].mean()), 2),
        "games":     len(recent),
    }


def get_player_recent_avg(player_name: str, stat_cols: list[str], n_games: int = 5) -> dict | None:
    """
    Promedio de los últimos n_games partidos del jugador.
    Prioriza playoffs (mínimo 3 juegos); si no hay suficientes cae a regular season.
    Retorna {stat: valor, "_source": "Playoffs (5j)", "_games": 5} o None si no encuentra al jugador.
    """
    if player_name in _player_recent_cache:
        return _player_recent_cache[player_name]

    matches = nba_players_static.find_players_by_full_name(player_name)
    if not matches:
        _player_recent_cache[player_name] = None
        return None

    player_id = matches[0]["id"]

    for season_type in ("Playoffs", "Regular Season"):
        try:
            time.sleep(0.8)
            log = PlayerGameLog(
                player_id=player_id,
                season=config.NBA_SEASON,
                season_type_all_star=season_type,
            )
            df = log.get_data_frames()[0]
        except Exception:
            continue

        if df.empty:
            continue

        # Playoffs: exigir mínimo 3 partidos para ser representativo
        if season_type == "Playoffs" and len(df) < 3:
            continue

        recent = df.head(n_games)
        result: dict = {}
        for col in stat_cols:
            if col in recent.columns:
                result[col] = round(float(recent[col].mean()), 2)

        games_used = len(recent)
        label = "PO" if season_type == "Playoffs" else "RS"
        result["_source"] = f"Últ.{games_used}j {label}"
        result["_games"]  = games_used

        _player_recent_cache[player_name] = result
        return result

    _player_recent_cache[player_name] = None
    return None


def get_injury_report() -> dict:
    """
    Descarga el reporte de lesiones de la ESPN API (gratuita, sin key).
    Retorna {nombre_en_minúsculas: {"status": "Out"|"Questionable"|"Doubtful",
             "team_abbr": "LAL", "display_name": "LeBron James"}}
    Retorna {} si falla la conexión o no hay datos.
    """
    url = "http://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
    try:
        resp = http_get(url, timeout=10)
        data = resp.json()
    except Exception:
        return {}

    report = {}
    for team_entry in data.get("injuries", []):
        team_abbr = team_entry.get("team", {}).get("abbreviation", "")
        for injury in team_entry.get("injuries", []):
            status = injury.get("status", "")
            if status not in ("Out", "Questionable", "Doubtful"):
                continue
            name = injury.get("athlete", {}).get("displayName", "")
            if not name:
                continue
            report[name.lower()] = {
                "status":       status,
                "team_abbr":    team_abbr,
                "display_name": name,
            }

    return report


def get_team_injury_impact(team_name: str, injury_report: dict, player_stats: dict) -> dict:
    """
    Estima el ajuste de Net Rating por lesiones de un equipo.

    Fórmula: impacto = (PTS * 0.4 + max(PLUS_MINUS, 0) * 1.5) / 10
    - PTS captura el volumen ofensivo
    - PLUS_MINUS captura el impacto total (defensa, playmaking, espaciado)
    - Solo suma PM positivo: un jugador con PM negativo no "ayuda" al ausentarse
    - Cap de 3.5 NRtg por jugador (incluso una estrella raramente vale más)
    - OUT/Doubtful = 100% del impacto | Questionable = 40% (suelen jugar)
    """
    abbr = config.TEAM_MAP.get(team_name, "")
    out_list: list[str] = []
    questionable_list: list[str] = []
    total_adj = 0.0

    for name_lower, info in injury_report.items():
        if info["team_abbr"] != abbr:
            continue

        pts = pm = 0.0
        for pname, pstats in player_stats.items():
            if pname.lower() == name_lower:
                pts = pstats.get("PTS",        0.0)
                pm  = pstats.get("PLUS_MINUS", 0.0)
                break

        pm_contribution = max(pm, 0) * 1.5          # solo PM positivo cuenta
        impact = min((pts * 0.4 + pm_contribution) / 10, 3.5)

        if info["status"] in ("Out", "Doubtful"):
            out_list.append(info["display_name"])
            total_adj -= impact
        elif info["status"] == "Questionable":
            questionable_list.append(info["display_name"])
            total_adj -= impact * 0.40  # questionable suele jugar → impacto reducido

    return {
        "adjustment":   round(total_adj, 2),
        "out":          out_list,
        "questionable": questionable_list,
    }


def get_player_props(event_id: str) -> list[dict]:
    url = f"https://api.the-odds-api.com/v4/sports/basketball_nba/events/{event_id}/odds"
    params = {
        "apiKey":     config.ODDS_API_KEY,
        "regions":    "us",
        "markets":    "player_points,player_rebounds,player_assists,player_threes",
        "oddsFormat": "american",
    }

    try:
        resp = http_get(url, params=params, timeout=15)
    except Exception as e:
        print(f"  ⚠️  Props no disponibles: {e}")
        return []

    data = resp.json()
    stat_labels = {
        "player_points":   ("Puntos",      "PTS"),
        "player_rebounds": ("Rebotes",     "REB"),
        "player_assists":  ("Asistencias", "AST"),
        "player_threes":   ("Triples",     "FG3M"),
    }

    props = []
    for book in data.get("bookmakers", []):
        if book["key"] not in config.PREFERRED_BOOKS:
            continue

        for market in book["markets"]:
            stat_key = market["key"]
            if stat_key not in stat_labels:
                continue

            label, nba_col = stat_labels[stat_key]
            players_seen = {}

            for outcome in market["outcomes"]:
                player    = outcome["description"]
                direction = outcome["name"]
                line      = outcome["point"]
                price     = outcome["price"]

                if player not in players_seen:
                    players_seen[player] = {
                        "player": player, "stat": stat_key,
                        "label": label, "nba_col": nba_col,
                        "line": line, "bookmaker": book["title"],
                    }
                players_seen[player][direction.lower()] = price

            for p in players_seen.values():
                if "over" in p and "under" in p:
                    props.append(p)
        break

    return props
