import os
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
NBA_SEASON   = os.getenv("NBA_SEASON", "2025-26")
MIN_EDGE     = float(os.getenv("MIN_EDGE", "0.04"))
FETCH_PROPS  = os.getenv("FETCH_PROPS", "true").lower() == "true"
BANKROLL     = float(os.getenv("BANKROLL", "10000"))

_active_min_edge    = MIN_EDGE
_active_fetch_props = FETCH_PROPS
_active_bankroll    = BANKROLL


def get_min_edge() -> float:
    return _active_min_edge


def set_min_edge(v: float):
    _set_min_edge_raw(v)
    _auto_save()


def _set_min_edge_raw(v: float):
    global _active_min_edge
    _active_min_edge = float(v)


def get_fetch_props() -> bool:
    return _active_fetch_props


def set_fetch_props(v: bool):
    _set_fetch_props_raw(v)
    _auto_save()


def _set_fetch_props_raw(v: bool):
    global _active_fetch_props
    _active_fetch_props = bool(v)


def get_bankroll() -> float:
    return _active_bankroll


def set_bankroll(v: float):
    _set_bankroll_raw(v)
    _auto_save()


def _set_bankroll_raw(v: float):
    global _active_bankroll
    _active_bankroll = float(v)


def _auto_save():
    import importlib
    settings = importlib.import_module("src.core.settings")
    settings.save_setting("bankroll", _active_bankroll)
    settings.save_setting("min_edge", _active_min_edge)
    settings.save_setting("fetch_props", _active_fetch_props)

# Preferimos DraftKings o FanDuel. El sistema usa el mejor precio disponible.
PREFERRED_BOOKS = ["draftkings", "fanduel", "betmgm", "bovada", "williamhill_us"]

# NBA: ventaja de local vale ~3 puntos de Net Rating
HOME_ADVANTAGE_POINTS = 3.0

# Penalización por back-to-back (jugar dos noches seguidas)
B2B_PENALTY_POINTS = 3.5

# Mappings: nombre en The Odds API → abreviatura NBA
TEAM_MAP = {
    "Atlanta Hawks":          "ATL",
    "Boston Celtics":         "BOS",
    "Brooklyn Nets":          "BKN",
    "Charlotte Hornets":      "CHA",
    "Chicago Bulls":          "CHI",
    "Cleveland Cavaliers":    "CLE",
    "Dallas Mavericks":       "DAL",
    "Denver Nuggets":         "DEN",
    "Detroit Pistons":        "DET",
    "Golden State Warriors":  "GSW",
    "Houston Rockets":        "HOU",
    "Indiana Pacers":         "IND",
    "Los Angeles Clippers":   "LAC",
    "Los Angeles Lakers":     "LAL",
    "Memphis Grizzlies":      "MEM",
    "Miami Heat":             "MIA",
    "Milwaukee Bucks":        "MIL",
    "Minnesota Timberwolves": "MIN",
    "New Orleans Pelicans":   "NOP",
    "New York Knicks":        "NYK",
    "Oklahoma City Thunder":  "OKC",
    "Orlando Magic":          "ORL",
    "Philadelphia 76ers":     "PHI",
    "Phoenix Suns":           "PHX",
    "Portland Trail Blazers": "POR",
    "Sacramento Kings":       "SAC",
    "San Antonio Spurs":      "SAS",
    "Toronto Raptors":        "TOR",
    "Utah Jazz":              "UTA",
    "Washington Wizards":     "WAS",
}

# ── FOOTBALL ──────────────────────────────────────────────────────────────────
FOOTBALL_MIN_EDGE = float(os.getenv("FOOTBALL_MIN_EDGE", "0.04"))
FOOTBALL_BOOKS    = ["draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "bet365"]


# ── MLB ───────────────────────────────────────────────────────────────────────
MLB_MIN_EDGE       = float(os.getenv("MLB_MIN_EDGE", "0.07"))
MLB_ALLOW_OVER     = os.getenv("MLB_ALLOW_OVER", "true").lower() == "true"
MLB_ALLOW_UNDER    = os.getenv("MLB_ALLOW_UNDER", "false").lower() == "true"
MLB_MAX_PICKS      = int(os.getenv("MLB_MAX_PICKS", "3"))
MLB_MAX_TOTAL_LINE = float(os.getenv("MLB_MAX_TOTAL_LINE", "8.5"))
MLB_BANKROLL       = float(os.getenv("MLB_BANKROLL", "10000"))
MLB_MIN_CONFIDENCE = os.getenv("MLB_MIN_CONFIDENCE", "BAJA")
MLB_ALLOW_RUNLINE  = os.getenv("MLB_ALLOW_RUNLINE", "true").lower() == "true"
MLB_ALLOW_MONEYLINE= os.getenv("MLB_ALLOW_MONEYLINE", "true").lower() == "true"
MLB_ALLOW_F5       = os.getenv("MLB_ALLOW_F5", "false").lower() == "true"
MLB_SEASON         = os.getenv("MLB_SEASON", "2026")
OPENWEATHER_API_KEY= os.getenv("OPENWEATHER_API_KEY", "")

_active_mlb_min_edge        = MLB_MIN_EDGE
_active_mlb_allow_over      = MLB_ALLOW_OVER
_active_mlb_allow_under     = MLB_ALLOW_UNDER
_active_mlb_max_picks       = MLB_MAX_PICKS
_active_mlb_max_total_line  = MLB_MAX_TOTAL_LINE
_active_mlb_bankroll        = MLB_BANKROLL
_active_mlb_min_confidence  = MLB_MIN_CONFIDENCE
_active_mlb_allow_runline   = MLB_ALLOW_RUNLINE
_active_mlb_allow_moneyline = MLB_ALLOW_MONEYLINE
_active_mlb_allow_f5        = MLB_ALLOW_F5


def get_mlb_min_edge() -> float:               return _active_mlb_min_edge
def set_mlb_min_edge(v: float):                global _active_mlb_min_edge; _active_mlb_min_edge = float(v)
def get_mlb_allow_over() -> bool:              return _active_mlb_allow_over
def set_mlb_allow_over(v: bool):               global _active_mlb_allow_over; _active_mlb_allow_over = bool(v)
def get_mlb_allow_under() -> bool:             return _active_mlb_allow_under
def set_mlb_allow_under(v: bool):              global _active_mlb_allow_under; _active_mlb_allow_under = bool(v)
def get_mlb_max_picks() -> int:                return _active_mlb_max_picks
def set_mlb_max_picks(v: int):                 global _active_mlb_max_picks; _active_mlb_max_picks = int(v)
def get_mlb_max_total_line() -> float:         return _active_mlb_max_total_line
def set_mlb_max_total_line(v: float):          global _active_mlb_max_total_line; _active_mlb_max_total_line = float(v)
def get_mlb_bankroll() -> float:               return _active_mlb_bankroll
def set_mlb_bankroll(v: float):                global _active_mlb_bankroll; _active_mlb_bankroll = float(v)
def get_mlb_min_confidence() -> str:           return _active_mlb_min_confidence
def set_mlb_min_confidence(v: str):            global _active_mlb_min_confidence; _active_mlb_min_confidence = str(v)
def get_mlb_allow_runline() -> bool:           return _active_mlb_allow_runline
def set_mlb_allow_runline(v: bool):            global _active_mlb_allow_runline; _active_mlb_allow_runline = bool(v)
def get_mlb_allow_moneyline() -> bool:         return _active_mlb_allow_moneyline
def set_mlb_allow_moneyline(v: bool):          global _active_mlb_allow_moneyline; _active_mlb_allow_moneyline = bool(v)
def get_mlb_allow_f5() -> bool:                return _active_mlb_allow_f5
def set_mlb_allow_f5(v: bool):                 global _active_mlb_allow_f5; _active_mlb_allow_f5 = bool(v)


# ── MLB: League averages (calibrated from backtest) ────────────────────────────
MLB_LEAGUE_AVG_RUNS = 4.45
MLB_LEAGUE_AVG_OPS  = 0.718
MLB_LEAGUE_AVG_FIP  = 4.29
MLB_FIP_CONSTANT    = 3.15
MLB_LEAGUE_HR_PER_FB = 0.105
MLB_RUN_TOTAL_SIGMA = 4.59

# ── MLB: Team → The Odds API abbreviation ─────────────────────────────────────
MLB_TEAM_MAP: dict[str, str] = {
    "Arizona Diamondbacks":   "ARI",
    "Atlanta Braves":         "ATL",
    "Baltimore Orioles":      "BAL",
    "Boston Red Sox":         "BOS",
    "Chicago Cubs":           "CHC",
    "Chicago White Sox":      "CWS",
    "Cincinnati Reds":        "CIN",
    "Cleveland Guardians":    "CLE",
    "Colorado Rockies":       "COL",
    "Detroit Tigers":         "DET",
    "Houston Astros":         "HOU",
    "Kansas City Royals":     "KC",
    "Los Angeles Angels":     "LAA",
    "Los Angeles Dodgers":    "LAD",
    "Miami Marlins":          "MIA",
    "Milwaukee Brewers":      "MIL",
    "Minnesota Twins":        "MIN",
    "New York Mets":          "NYM",
    "New York Yankees":       "NYY",
    "Athletics":              "ATH",
    "Oakland Athletics":      "ATH",
    "Philadelphia Phillies":  "PHI",
    "Pittsburgh Pirates":     "PIT",
    "San Diego Padres":       "SD",
    "San Francisco Giants":   "SF",
    "Seattle Mariners":       "SEA",
    "St. Louis Cardinals":    "STL",
    "Tampa Bay Rays":         "TB",
    "Texas Rangers":          "TEX",
    "Toronto Blue Jays":      "TOR",
    "Washington Nationals":   "WSH",
}

# ── MLB: Team → MLB Stats API team ID ─────────────────────────────────────────
MLB_TEAM_ID_MAP: dict[str, int] = {
    "Arizona Diamondbacks":   109,
    "Atlanta Braves":         144,
    "Baltimore Orioles":      110,
    "Boston Red Sox":         111,
    "Chicago Cubs":           112,
    "Chicago White Sox":      145,
    "Cincinnati Reds":        113,
    "Cleveland Guardians":    114,
    "Colorado Rockies":       115,
    "Detroit Tigers":         116,
    "Houston Astros":         117,
    "Kansas City Royals":     118,
    "Los Angeles Angels":     108,
    "Los Angeles Dodgers":    119,
    "Miami Marlins":          146,
    "Milwaukee Brewers":      158,
    "Minnesota Twins":        142,
    "New York Mets":          121,
    "New York Yankees":       147,
    "Athletics":              133,
    "Oakland Athletics":      133,
    "Philadelphia Phillies":  143,
    "Pittsburgh Pirates":     134,
    "San Diego Padres":       135,
    "San Francisco Giants":   137,
    "Seattle Mariners":       136,
    "St. Louis Cardinals":    138,
    "Tampa Bay Rays":         139,
    "Texas Rangers":          140,
    "Toronto Blue Jays":      141,
    "Washington Nationals":   120,
}

# ── MLB: Park factors (2025-26) ────────────────────────────────────────────────
MLB_PARK_FACTORS: dict[str, float] = {
    "Colorado Rockies":       1.28,
    "Boston Red Sox":         1.10,
    "Cincinnati Reds":        1.08,
    "Texas Rangers":          1.07,
    "Philadelphia Phillies":  1.06,
    "Baltimore Orioles":      1.05,
    "Toronto Blue Jays":      1.04,
    "Minnesota Twins":        1.04,
    "Chicago Cubs":           1.03,
    "Milwaukee Brewers":      1.02,
    "Kansas City Royals":     1.01,
    "Atlanta Braves":         1.01,
    "Tampa Bay Rays":         1.00,
    "Cleveland Guardians":    1.00,
    "Detroit Tigers":         1.00,
    "Washington Nationals":   0.99,
    "Pittsburgh Pirates":     0.99,
    "Arizona Diamondbacks":   0.99,
    "Chicago White Sox":      0.98,
    "Miami Marlins":          0.97,
    "St. Louis Cardinals":    0.97,
    "New York Yankees":       0.97,
    "New York Mets":          0.97,
    "Houston Astros":         0.96,
    "Los Angeles Dodgers":    0.96,
    "Los Angeles Angels":     0.95,
    "Seattle Mariners":       0.94,
    "Athletics":              0.94,
    "San Francisco Giants":   0.93,
    "San Diego Padres":       0.92,
}

MLB_PARK_ALIASES: dict[str, str] = {
    "Oakland Athletics": "Athletics",
    "Oakland A's":       "Athletics",
    "A's":               "Athletics",
    "Los Angeles Angels of Anaheim": "Los Angeles Angels",
}

# ── MLB: Stadium info (lat/lon, open air, center field bearing) ───────────────
MLB_STADIUM_INFO: dict[str, dict] = {
    "Arizona Diamondbacks":  {"lat": 33.4453, "lon": -112.0667, "open_air": False, "cf_bearing": 350},
    "Atlanta Braves":        {"lat": 33.8907, "lon":  -84.4677, "open_air": True,  "cf_bearing":  10},
    "Baltimore Orioles":     {"lat": 39.2838, "lon":  -76.6215, "open_air": True,  "cf_bearing":  18},
    "Boston Red Sox":        {"lat": 42.3467, "lon":  -71.0972, "open_air": True,  "cf_bearing":  92},
    "Chicago Cubs":          {"lat": 41.9484, "lon":  -87.6553, "open_air": True,  "cf_bearing":  95},
    "Chicago White Sox":     {"lat": 41.8299, "lon":  -87.6338, "open_air": True,  "cf_bearing": 355},
    "Cincinnati Reds":       {"lat": 39.0979, "lon":  -84.5082, "open_air": True,  "cf_bearing":   5},
    "Cleveland Guardians":   {"lat": 41.4962, "lon":  -81.6852, "open_air": True,  "cf_bearing": 350},
    "Colorado Rockies":      {"lat": 39.7559, "lon": -104.9942, "open_air": True,  "cf_bearing":   5},
    "Detroit Tigers":        {"lat": 42.3390, "lon":  -83.0485, "open_air": True,  "cf_bearing": 350},
    "Houston Astros":        {"lat": 29.7573, "lon":  -95.3555, "open_air": False, "cf_bearing": 350},
    "Kansas City Royals":    {"lat": 39.0517, "lon":  -94.4803, "open_air": True,  "cf_bearing":   5},
    "Los Angeles Angels":    {"lat": 33.8003, "lon": -117.8827, "open_air": True,  "cf_bearing": 350},
    "Los Angeles Dodgers":   {"lat": 34.0739, "lon": -118.2400, "open_air": True,  "cf_bearing":  20},
    "Miami Marlins":         {"lat": 25.7781, "lon":  -80.2198, "open_air": False, "cf_bearing": 350},
    "Milwaukee Brewers":     {"lat": 43.0280, "lon":  -87.9712, "open_air": False, "cf_bearing": 350},
    "Minnesota Twins":       {"lat": 44.9817, "lon":  -93.2776, "open_air": True,  "cf_bearing":  10},
    "New York Mets":         {"lat": 40.7571, "lon":  -73.8458, "open_air": True,  "cf_bearing": 355},
    "New York Yankees":      {"lat": 40.8296, "lon":  -73.9262, "open_air": True,  "cf_bearing": 350},
    "Athletics":             {"lat": 38.5803, "lon": -121.4992, "open_air": True,  "cf_bearing":   5},
    "Philadelphia Phillies": {"lat": 39.9061, "lon":  -75.1665, "open_air": True,  "cf_bearing": 340},
    "Pittsburgh Pirates":    {"lat": 40.4469, "lon":  -80.0057, "open_air": True,  "cf_bearing": 350},
    "San Diego Padres":      {"lat": 32.7076, "lon": -117.1570, "open_air": True,  "cf_bearing":  10},
    "San Francisco Giants":  {"lat": 37.7786, "lon": -122.3893, "open_air": True,  "cf_bearing":  15},
    "Seattle Mariners":      {"lat": 47.5914, "lon": -122.3325, "open_air": False, "cf_bearing": 350},
    "St. Louis Cardinals":   {"lat": 38.6226, "lon":  -90.1928, "open_air": True,  "cf_bearing": 355},
    "Tampa Bay Rays":        {"lat": 27.7682, "lon":  -82.6534, "open_air": False, "cf_bearing": 350},
    "Texas Rangers":         {"lat": 32.7473, "lon":  -97.0832, "open_air": False, "cf_bearing": 350},
    "Toronto Blue Jays":     {"lat": 43.6414, "lon":  -79.3894, "open_air": False, "cf_bearing": 350},
    "Washington Nationals":  {"lat": 38.8730, "lon":  -77.0074, "open_air": True,  "cf_bearing": 350},
}
