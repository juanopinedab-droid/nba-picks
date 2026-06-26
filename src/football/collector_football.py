"""
collector_football.py — Datos para el módulo de fútbol.

Fuentes:
  - ESPN API (gratuita, sin key): stats de temporada EPL (goles a favor/contra)
  - The Odds API: cuotas EPL (h2h 3-way, totals)

Nota: se usan goles reales (ESPN) en vez de xG como proxy para el modelo Poisson.
La correlación entre goles/partido y xG/partido es ~0.85 en una temporada completa.
"""

import time
import requests
from datetime import date, datetime, timedelta

from ..core import config
from ..core import database

# ─── CONSTANTES ───────────────────────────────────────────────────────────────

ESPN_BASE      = "https://site.api.espn.com/apis/v2/sports/soccer/eng.1"
ESPN_SCOREBOARD= "https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard"

# ─── TEAM MAP ─────────────────────────────────────────────────────────────────
# The Odds API name → abreviatura y nombre normalizado para matching con ESPN

TEAM_MAP_FOOTBALL: dict[str, dict] = {
    "Arsenal":                    {"abbr": "ARS"},
    "Aston Villa":                {"abbr": "AVL"},
    "Brentford":                  {"abbr": "BRE"},
    "Brighton":                   {"abbr": "BHA"},
    "Brighton and Hove Albion":   {"abbr": "BHA"},
    "Chelsea":                    {"abbr": "CHE"},
    "Crystal Palace":             {"abbr": "CRY"},
    "Everton":                    {"abbr": "EVE"},
    "Fulham":                     {"abbr": "FUL"},
    "Ipswich":                    {"abbr": "IPS"},
    "Ipswich Town":               {"abbr": "IPS"},
    "Leicester":                  {"abbr": "LEI"},
    "Leicester City":             {"abbr": "LEI"},
    "Liverpool":                  {"abbr": "LIV"},
    "Manchester City":            {"abbr": "MCI"},
    "Manchester United":          {"abbr": "MUN"},
    "Newcastle United":           {"abbr": "NEW"},
    "Nottingham Forest":          {"abbr": "NFO"},
    "Southampton":                {"abbr": "SOU"},
    "Sunderland":                 {"abbr": "SUN"},
    "Tottenham Hotspur":          {"abbr": "TOT"},
    "West Ham":                   {"abbr": "WHU"},
    "West Ham United":            {"abbr": "WHU"},
    "Wolverhampton Wanderers":    {"abbr": "WOL"},
    "Wolves":                     {"abbr": "WOL"},
    "Bournemouth":                {"abbr": "BOU"},
    "Luton":                      {"abbr": "LUT"},
    "Luton Town":                 {"abbr": "LUT"},
    "Burnley":                    {"abbr": "BUR"},
    "Sheffield United":           {"abbr": "SHU"},
    "Leeds United":               {"abbr": "LEE"},
    "Middlesbrough":              {"abbr": "MID"},
    "Coventry City":              {"abbr": "COV"},
}

# ─── CACHE EN DISCO ───────────────────────────────────────────────────────────

_memory_cache: dict = {}


def _load_daily_cache() -> dict:
    global _memory_cache
    if _memory_cache:
        return _memory_cache
    cache_key = f"football_stats:{date.today()}"
    data = database.cache_get(cache_key)
    if data and data.get("date") == str(date.today()):
        _memory_cache = data
        return data
    return {}


def _save_daily_cache(data: dict):
    global _memory_cache
    data["date"] = str(date.today())
    _memory_cache = data
    cache_key = f"football_stats:{date.today()}"
    database.cache_set(cache_key, data, ttl_hours=24)


# ─── NORMALIZACIÓN DE NOMBRES ─────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Normaliza nombre de equipo para matching flexible."""
    return (name.lower()
            .replace("&", "and")
            .replace("  ", " ")
            .replace("wolverhampton wanderers", "wolves")
            .replace("brighton & hove albion", "brighton")
            .replace("brighton and hove albion", "brighton")
            .replace("nottingham forest", "nottm forest")
            .strip())


# ─── ESPN API ─────────────────────────────────────────────────────────────────

def get_all_team_season_stats() -> dict[str, dict]:
    """
    Descarga la tabla de posiciones EPL de ESPN.
    Retorna dict keyed por nombre normalizado con goles/partido como proxy de xG.
    {"liverpool": {"xg_per_match": 2.1, "xga_per_match": 0.9, "matches": 35, "display_name": "Liverpool"}}
    """
    cache = _load_daily_cache()
    if "season_stats" in cache:
        return cache["season_stats"]

    try:
        r = requests.get(f"{ESPN_BASE}/standings", timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  ESPN standings error: {e}")
        return {}

    result: dict[str, dict] = {}

    for group in data.get("children", []):
        for entry in group.get("standings", {}).get("entries", []):
            team_info = entry.get("team", {})
            display   = team_info.get("displayName", "")
            if not display:
                continue

            stats_list = {s["name"]: s.get("value", 0) for s in entry.get("stats", [])}
            games    = int(stats_list.get("gamesPlayed", 0) or 0)
            goals_f  = float(stats_list.get("pointsFor",     0) or 0)
            goals_a  = float(stats_list.get("pointsAgainst", 0) or 0)

            if games == 0:
                continue

            key = _normalize(display)
            result[key] = {
                "xg_per_match":  round(goals_f / games, 3),
                "xga_per_match": round(goals_a / games, 3),
                "matches":       games,
                "display_name":  display,
            }

    if result:
        cache["season_stats"] = result
        _save_daily_cache(cache)

    return result


def get_team_season_stats(team_name: str) -> dict | None:
    """
    Retorna stats de temporada para el equipo dado (nombre de The Odds API).
    """
    all_stats = get_all_team_season_stats()
    key = _normalize(team_name)

    # Búsqueda exacta
    if key in all_stats:
        return all_stats[key]

    # Búsqueda parcial por si el nombre difiere levemente
    for k, v in all_stats.items():
        if key in k or k in key:
            return v

    return None


def get_team_recent_xg(team_name: str, n: int = 5) -> dict | None:
    """
    Placeholder: ESPN no expone logs de partidos por equipo en la API pública.
    Devuelve None → el modelo usará solo stats de temporada completa.
    Implementar cuando se encuentre una fuente de datos de forma reciente.
    """
    return None


# ─── THE ODDS API ─────────────────────────────────────────────────────────────

def _american_to_prob(odds: int) -> float:
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)


def _extract_football_odds(bookmakers: list, home: str, away: str) -> dict | None:
    result = {
        "h2h_home":    None,
        "h2h_draw":    None,
        "h2h_away":    None,
        "total_line":  None,
        "total_over":  None,
        "total_under": None,
        "btts_yes":    None,
        "btts_no":     None,
        "bookmaker":   None,
    }

    preferred = config.PREFERRED_BOOKS
    sorted_books = sorted(
        bookmakers,
        key=lambda b: preferred.index(b["key"]) if b["key"] in preferred else 99
    )

    impl_home_list: list[float] = []
    impl_draw_list: list[float] = []
    impl_away_list: list[float] = []

    for book in sorted_books:
        markets = {m["key"]: m["outcomes"] for m in book["markets"]}

        if "h2h" in markets:
            odds_home = odds_draw = odds_away = None
            for o in markets["h2h"]:
                if o["name"] == home:
                    odds_home = o["price"]
                elif o["name"] == "Draw":
                    odds_draw = o["price"]
                elif o["name"] == away:
                    odds_away = o["price"]

            if odds_home and odds_draw and odds_away:
                if result["h2h_home"] is None:
                    result["h2h_home"]  = odds_home
                    result["h2h_draw"]  = odds_draw
                    result["h2h_away"]  = odds_away
                    result["bookmaker"] = book["title"]

                rh = _american_to_prob(odds_home)
                rd = _american_to_prob(odds_draw)
                ra = _american_to_prob(odds_away)
                total_vig = rh + rd + ra
                impl_home_list.append(rh / total_vig)
                impl_draw_list.append(rd / total_vig)
                impl_away_list.append(ra / total_vig)

        if "totals" in markets and result["total_line"] is None:
            for o in markets["totals"]:
                if o["name"] == "Over":
                    result["total_over"] = o["price"]
                    result["total_line"] = o["point"]
                elif o["name"] == "Under":
                    result["total_under"] = o["price"]

        if "btts" in markets and result["btts_yes"] is None:
            for o in markets["btts"]:
                if o["name"] in ("Yes", "yes"):
                    result["btts_yes"] = o["price"]
                elif o["name"] in ("No", "no"):
                    result["btts_no"]  = o["price"]

    if result["h2h_home"] is None:
        return None

    n_books = len(impl_home_list)
    if n_books:
        result["consensus_impl_home"] = round(sum(impl_home_list) / n_books, 6)
        result["consensus_impl_draw"] = round(sum(impl_draw_list) / n_books, 6)
        result["consensus_impl_away"] = round(sum(impl_away_list) / n_books, 6)
        result["consensus_books"]     = n_books
    else:
        result["consensus_impl_home"] = None
        result["consensus_impl_draw"] = None
        result["consensus_impl_away"] = None
        result["consensus_books"]     = 0

    return result


def get_upcoming_epl_odds() -> list[dict]:
    """Cuotas EPL de hoy y mañana desde The Odds API."""
    url = "https://api.the-odds-api.com/v4/sports/soccer_epl/odds"
    params = {
        "apiKey":     config.ODDS_API_KEY,
        "regions":    "us,uk",
        "markets":    "h2h,totals",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()

    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"  [API] Requests restantes: {remaining}")

    today    = date.today()
    tomorrow = today + timedelta(days=1)
    games    = []

    for g in resp.json():
        home = g["home_team"]
        away = g["away_team"]

        if home not in TEAM_MAP_FOOTBALL and away not in TEAM_MAP_FOOTBALL:
            continue

        try:
            utc_dt   = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone()
            if local_dt.date() not in (today, tomorrow):
                continue
        except Exception:
            pass

        odds = _extract_football_odds(g["bookmakers"], home, away)
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


def get_todays_epl_matches() -> list[dict]:
    """Partidos EPL de hoy y mañana con cuotas de The Odds API."""
    return get_upcoming_epl_odds()
