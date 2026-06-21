import os
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY       = os.getenv("ODDS_API_KEY", "")
NBA_SEASON         = os.getenv("NBA_SEASON", "2025-26")
MIN_EDGE           = float(os.getenv("MIN_EDGE", "0.04"))
FETCH_PROPS        = os.getenv("FETCH_PROPS", "true").lower() == "true"
BANKROLL           = float(os.getenv("BANKROLL", "10000"))
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

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
