"""
collector_mlb.py — Datos para el módulo de MLB.

Fuentes:
  - MLB Stats API (statsapi.mlb.com) — oficial, sin key, sin rate limit conocido.
      Provee: schedule con pitchers probables, stats de temporada por pitcher y equipo.
  - The Odds API — cuotas de totales (Over/Under carreras).

Modelo Opción A:
  - FIP del pitcher (calculado de ERA/K/BB/HR/IP de la API oficial)
  - OPS del equipo ofensivo (proxy de wOBA, disponible directamente)
  - Park factors (tabla fija, actualizada 2025-26)
"""

import json
import math
import time
import requests
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import config
import line_tracker

# ─── CONSTANTES DEL MODELO ────────────────────────────────────────────────────

# Promedios de liga 2025-26 MLB (actualizables al inicio de cada temporada)
# Calibrados con backtest_mlb.py sobre temporada 2025 (2307 partidos):
#   runs/equipo=4.446  OPS=0.719  FIP=4.292
LEAGUE_AVG_RUNS    = 4.45    # carreras por equipo por partido
LEAGUE_AVG_OPS     = 0.718   # OPS ofensivo de liga
LEAGUE_AVG_FIP     = 4.29    # FIP promedio de liga (actualizado desde backtest 2025)
FIP_CONSTANT       = 3.15    # calibración: liga_ERA - (13*HR+3*BB-2*K)/IP promedio

# xFIP — igual que FIP pero normaliza HRs al promedio de liga (más estable en muestras pequeñas)
# Expected_HR = (airOuts + HR) × LEAGUE_HR_PER_FB   [airOuts = fly ball outs; HR = fly balls que no son outs]
# Total fly balls = airOuts + HR (ambos son contactos aéreos)
# Fuente HR/FB: Baseball Savant 2024-25 liga promedio ~10.5%
LEAGUE_HR_PER_FB   = 0.105   # tasa HR/fly-ball de liga; actualizar cada temporada
XFIP_CONSTANT      = FIP_CONSTANT   # ≈ FIP_CONSTANT (mismo ERA de referencia, sólo HR normalizado)

# Desviación estándar del TOTAL de carreras en un partido MLB.
# Backtest 2025 (2307 partidos): RMSE=4.56, std_actual=4.59.
# Usamos el valor del backtest directamente (4.59). El valor original de 4.20 subestimaba
# la varianza real y producía overconfidence de ~22% (modelo predecía 63.8% avg, ganaba 41.7%).
# Un σ más alto → probabilidades más conservadoras → menos picks pero más calibrados.
RUN_TOTAL_SIGMA    = 4.59

# ─── PARK FACTORS (2025-26) ───────────────────────────────────────────────────
# Factor > 1.0 = parque favorece bateadores (más carreras)
# Factor < 1.0 = parque favorece pitchers (menos carreras)
# Fuente: Fangraphs / ESPN park factors históricos ajustados al estadio actual.

PARK_FACTORS: dict[str, float] = {
    "Colorado Rockies":       1.28,   # Coors Field — máximo hitter park
    "Boston Red Sox":         1.10,   # Fenway Park — Monster Verde
    "Cincinnati Reds":        1.08,   # Great American Ball Park
    "Texas Rangers":          1.07,   # Globe Life Field
    "Philadelphia Phillies":  1.06,   # Citizens Bank Park
    "Baltimore Orioles":      1.05,   # Camden Yards
    "Toronto Blue Jays":      1.04,   # Rogers Centre (techo → humedad)
    "Minnesota Twins":        1.04,   # Target Field
    "Chicago Cubs":           1.03,   # Wrigley Field
    "Milwaukee Brewers":      1.02,   # American Family Field
    "Kansas City Royals":     1.01,   # Kauffman Stadium
    "Atlanta Braves":         1.01,   # Truist Park
    "Tampa Bay Rays":         1.00,   # Tropicana Field
    "Cleveland Guardians":    1.00,   # Progressive Field
    "Detroit Tigers":         1.00,   # Comerica Park
    "Washington Nationals":   0.99,   # Nationals Park
    "Pittsburgh Pirates":     0.99,   # PNC Park
    "Arizona Diamondbacks":   0.99,   # Chase Field
    "Chicago White Sox":      0.98,   # Guaranteed Rate Field
    "Miami Marlins":          0.97,   # loanDepot park
    "St. Louis Cardinals":    0.97,   # Busch Stadium
    "New York Yankees":       0.97,   # Yankee Stadium
    "New York Mets":          0.97,   # Citi Field
    "Houston Astros":         0.96,   # Minute Maid Park
    "Los Angeles Dodgers":    0.96,   # Dodger Stadium
    "Los Angeles Angels":     0.95,   # Angel Stadium
    "Seattle Mariners":       0.94,   # T-Mobile Park
    "Athletics":              0.94,   # Sutter Health Park (Sacramento, desde 2025)
    "San Francisco Giants":   0.93,   # Oracle Park
    "San Diego Padres":       0.92,   # Petco Park — máximo pitcher park
}

# Alias alternativos para matching de nombres
_PARK_ALIASES: dict[str, str] = {
    "Oakland Athletics": "Athletics",
    "Oakland A's":       "Athletics",
    "A's":               "Athletics",
    "Los Angeles Angels of Anaheim": "Los Angeles Angels",
}

# ─── MAPPINGS DE EQUIPOS ──────────────────────────────────────────────────────

# Team name → MLB Stats API team ID (estable — sólo cambia si equipo se muda/renombra)
TEAM_ID_MAP: dict[str, int] = {
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

# The Odds API → abreviatura MLB estándar
TEAM_MAP: dict[str, str] = {
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

# ─── CACHE EN DISCO ───────────────────────────────────────────────────────────

_CACHE_FILE    = Path(__file__).parent / "mlb_cache.json"
_memory_cache: dict | None = None

MLB_API = "https://statsapi.mlb.com/api/v1"


def _load_daily_cache() -> dict:
    global _memory_cache
    if _memory_cache is not None:
        return _memory_cache
    try:
        with open(_CACHE_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("date") == str(date.today()):
            _memory_cache = data
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save_daily_cache(data: dict):
    global _memory_cache
    data["date"] = str(date.today())
    _memory_cache = data
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


# ─── NORMALIZACIÓN DE NOMBRES ─────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.lower().strip().replace("  ", " ")


def _fuzzy_match(name: str, candidates: dict) -> str | None:
    n = _normalize(name)
    # Exacto
    if n in candidates:
        return n
    # Contención parcial
    for k in candidates:
        if n in k or k in n:
            return k
    # Palabras clave (≥5 chars)
    words = n.split()
    for k in candidates:
        if any(w in k for w in words if len(w) >= 5):
            return k
    return None


def get_park_factor(team_name: str) -> float:
    """Park factor del equipo local (1.0 si desconocido)."""
    name = _PARK_ALIASES.get(team_name, team_name)
    return PARK_FACTORS.get(name, 1.00)


# ─── MLB STATS API: SCHEDULE CON PITCHERS ────────────────────────────────────

def get_mlb_schedule(target_date: date | None = None) -> list[dict]:
    """
    Partidos del día con pitchers probables desde la MLB Stats API.
    Retorna lista de dicts con gamePk, away_team, home_team, away_pitcher, home_pitcher,
    away_pitcher_id, home_pitcher_id, status, commence_iso, hp_umpire,
    home_lineup_ids, away_lineup_ids.

    Lineups: disponibles ~2h antes del juego. Si no están, se retornan listas vacías.
    """
    cache = _load_daily_cache()
    # Cache cada 4 horas para que el run de las 11 AM siempre tenga árbitros frescos
    # (los asignaciones de árbitros suelen aparecer en la API horas antes del primer juego)
    hour_bucket = datetime.now().hour // 4
    cache_key = f"schedule_{target_date or date.today()}_{hour_bucket}"
    if cache_key in cache:
        return cache[cache_key]

    d = target_date or date.today()
    date_str = d.strftime("%Y-%m-%d")

    try:
        r = requests.get(f"{MLB_API}/schedule", params={
            "sportId":  1,
            "date":     date_str,
            "hydrate":  "probablePitcher(note),linescore,team,officials,lineups",
        }, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  ⚠️  MLB schedule error: {e}")
        return []

    result = []
    for date_entry in raw.get("dates", []):
        for g in date_entry.get("games", []):
            away_info = g["teams"]["away"]
            home_info = g["teams"]["home"]

            away_name = away_info["team"]["name"]
            home_name = home_info["team"]["name"]

            away_p     = away_info.get("probablePitcher", {})
            home_p     = home_info.get("probablePitcher", {})

            status_detail = g.get("status", {}).get("detailedState", "")
            commence      = g.get("gameDate", "")

            # Árbitro del home plate (disponible si el partido ya tiene asignaciones)
            officials = g.get("officials", [])
            hp_umpire = next(
                (o["official"]["fullName"] for o in officials
                 if o.get("officialType") == "Home Plate"),
                None,
            )

            # Pitcher handedness — incluido en probablePitcher sin hydration extra
            away_pitcher_hand = away_p.get("pitchHand", {}).get("code")  # "L" o "R"
            home_pitcher_hand = home_p.get("pitchHand", {}).get("code")

            # Lineup del día (disponible ~2h antes del juego)
            # battingOrder: 100=1ro, 200=2do, ... 900=9no
            lineups_raw = g.get("lineups", {})

            def _parse_lineup(players: list) -> tuple[list[int], float | None]:
                # El hydrate 'lineups' actual NO incluye battingOrder — la lista ya
                # viene en orden de bateo. Si battingOrder existe (formato viejo),
                # se usa; si no, se toman los primeros 9 tal cual.
                if any(p.get("battingOrder") for p in players):
                    ordered  = sorted(players, key=lambda p: p.get("battingOrder", 9999))
                    starters = [p for p in ordered
                                if p.get("id") and p.get("battingOrder", 9999) <= 900]
                else:
                    starters = [p for p in players if p.get("id")][:9]
                ids = [p["id"] for p in starters]
                # Fracción del lineup que batea zurdo (S=switch cuenta como 0.5)
                hands = [p.get("batSide", {}).get("code") for p in starters]
                hands = [h for h in hands if h in ("L", "R", "S")]
                if len(hands) >= 5:
                    left = sum(1.0 if h == "L" else 0.5 if h == "S" else 0.0 for h in hands)
                    pct_l = round(left / len(hands), 3)
                else:
                    pct_l = None
                return ids, pct_l

            home_lineup_ids, home_lineup_pct_l = _parse_lineup(lineups_raw.get("homePlayers", []))
            away_lineup_ids, away_lineup_pct_l = _parse_lineup(lineups_raw.get("awayPlayers", []))

            result.append({
                "game_pk":            g.get("gamePk"),
                "away_team":          away_name,
                "home_team":          home_name,
                "away_team_id":       away_info["team"].get("id"),
                "home_team_id":       home_info["team"].get("id"),
                "away_pitcher":       away_p.get("fullName", "TBD"),
                "home_pitcher":       home_p.get("fullName", "TBD"),
                "away_pitcher_id":    away_p.get("id"),
                "home_pitcher_id":    home_p.get("id"),
                "away_pitcher_hand":  away_pitcher_hand,
                "home_pitcher_hand":  home_pitcher_hand,
                "status":             status_detail,
                "commence_iso":       commence,
                "hp_umpire":          hp_umpire,
                "home_lineup_ids":    home_lineup_ids,
                "away_lineup_ids":    away_lineup_ids,
                "home_lineup_pct_l":  home_lineup_pct_l,
                "away_lineup_pct_l":  away_lineup_pct_l,
            })

    # No cachear si algún juego próximo (< 3h) tiene lineups vacíos — se re-fetching al correr de nuevo
    now_utc = datetime.now(timezone.utc)
    has_upcoming_empty = any(
        (not g["home_lineup_ids"] or not g["away_lineup_ids"])
        and g.get("commence_iso")
        and (datetime.fromisoformat(g["commence_iso"].replace("Z", "+00:00")) - now_utc).total_seconds() < 10800
        for g in result
    )
    if not has_upcoming_empty:
        cache[cache_key] = result
        _save_daily_cache(cache)
    return result


# ─── MLB STATS API: STATS DE PITCHER ─────────────────────────────────────────

def get_pitcher_stats(pitcher_id: int, season: int | None = None) -> dict | None:
    """
    Stats de temporada para un pitcher. Calcula FIP desde raw stats.
    Retorna dict con era, fip, k9, bb9, whip, ip, k, bb, hr, wins, losses o None.
    """
    if not pitcher_id:
        return None

    yr = season or (date.today().year)
    cache = _load_daily_cache()
    cache_key = f"pitcher_{pitcher_id}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":  "season",
            "season": yr,
            "group":  "pitching",
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0]["stat"]
    except Exception:
        return None

    try:
        ip_str = str(s.get("inningsPitched", "0"))
        # IP en formato "62.2" → convertir a decimal innings (62 + 2/3)
        parts = ip_str.split(".")
        ip = float(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)

        k        = int(s.get("strikeOuts",  0) or 0)
        bb       = int(s.get("baseOnBalls", 0) or 0)
        hbp      = int(s.get("hitByPitch",  0) or 0)
        hr       = int(s.get("homeRuns",    0) or 0)
        air_outs = int(s.get("airOuts",     0) or 0)   # fly-ball outs (sin HRs)
        era      = float(s.get("era",  "4.50") or "4.50")
        whip     = float(s.get("whip", "1.30") or "1.30")

        # FIP = (13×HR + 3×(BB+HBP) - 2×K) / IP + constante
        fip = ((13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONSTANT) if ip >= 5 else era

        # xFIP — normaliza HRs al promedio de liga para reducir varianza de muestra pequeña
        # Total fly balls = air_outs (outs en el aire) + HR (fly balls que salieron del parque)
        fly_balls   = air_outs + hr
        expected_hr = fly_balls * LEAGUE_HR_PER_FB if fly_balls > 0 else hr
        xfip = ((13 * expected_hr + 3 * (bb + hbp) - 2 * k) / ip + XFIP_CONSTANT) if ip >= 5 else era

        gs = int(s.get("gamesStarted", 0) or 0)
        ip_per_start = round(ip / max(gs, 1), 1) if gs > 0 else round(ip, 1)

        k9   = round(k  / ip * 9, 1) if ip >= 5 else 0.0
        bb9  = round(bb / ip * 9, 1) if ip >= 5 else 0.0
        # K-BB/9 = strikeouts menos walks por 9 innings — mide "calidad de arsenal" independiente de HRs
        # > 7.0 élite | 5-7 above avg | 3-5 avg | < 3 debil
        kbb9 = round(k9 - bb9, 1)

        result = {
            "era":          round(era,  2),
            "fip":          round(fip,  2),
            "xfip":         round(xfip, 2),   # xFIP: más estable que FIP en muestras <50 IP
            "kbb9":         kbb9,              # K-BB/9: calidad de arsenal del pitcher
            "whip":         round(whip, 2),
            "k9":           k9,
            "bb9":          bb9,
            "hr9":          round(hr / ip * 9, 1) if ip >= 5 else 0.0,
            "hr_fb_pct":    round(hr / max(fly_balls, 1), 3),  # HR/FB real (compara con liga 10.5%)
            "fly_balls":    fly_balls,
            "ip":           round(ip, 1),
            "k":            k, "bb": bb, "hr": hr,
            "wins":         int(s.get("wins",   0) or 0),
            "losses":       int(s.get("losses", 0) or 0),
            "games_started":gs,
            "ip_per_start": ip_per_start,
        }
    except (ValueError, ZeroDivisionError):
        return None

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── MLB STATS API: SPLITS HOME/AWAY DEL PITCHER ────────────────────────────

def get_pitcher_home_away_splits(pitcher_id: int, season: int | None = None) -> dict | None:
    """
    xFIP y FIP del pitcher divididos por home vs away (splits de situación).

    Algunos pitchers tienen diferencias dramáticas: mejor en casa (mound familiar,
    apoyo del público) o mejor de visitante (línea de bateo diferente, sin presión).

    Usa el endpoint sitCodes=H,R del MLB Stats API:
      GET /people/{id}/stats?stats=statSplits&group=pitching&sitCodes=H,R&season={yr}

    Retorna dict con:
        xfip_home, xfip_away   — xFIP en casa vs fuera
        fip_home,  fip_away    — FIP crudo para referencia
        ip_home,   ip_away     — innings (muestra)
        split_diff             — xfip_away - xfip_home (positivo = mejor en casa)
    o None si IP insuficiente en alguno de los dos splits (< 10 IP).
    """
    if not pitcher_id:
        return None

    yr    = season or date.today().year
    cache = _load_daily_cache()
    cache_key = f"pitcher_splits_{pitcher_id}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":    "statSplits",
            "group":    "pitching",
            "sitCodes": "H,R",        # H = home, R = road (away)
            "season":   yr,
        }, timeout=15)
        r.raise_for_status()
        raw = r.json()
    except Exception:
        cache[cache_key] = None
        _save_daily_cache(cache)
        return None

    # La API retorna un bloque con splits etiquetados por sitCode
    splits_by_code: dict[str, dict] = {}
    for block in raw.get("stats", []):
        for sp in block.get("splits", []):
            code = sp.get("split", {}).get("code", "")
            if code in ("H", "R"):
                splits_by_code[code] = sp.get("stat", {})

    if "H" not in splits_by_code or "R" not in splits_by_code:
        cache[cache_key] = None
        _save_daily_cache(cache)
        return None

    def _parse_split(st: dict) -> dict | None:
        """Calcula xFIP y FIP de un split individual."""
        try:
            ip_str = str(st.get("inningsPitched", "0"))
            parts  = ip_str.split(".")
            ip = float(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)
            if ip < 10:      # muestra insuficiente para este split
                return None

            k        = int(st.get("strikeOuts",  0) or 0)
            bb       = int(st.get("baseOnBalls", 0) or 0)
            hbp      = int(st.get("hitByPitch",  0) or 0)
            hr       = int(st.get("homeRuns",    0) or 0)
            air_outs = int(st.get("airOuts",     0) or 0)
            era      = float(st.get("era", "4.50") or "4.50")

            fip  = (13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONSTANT if ip >= 5 else era
            fly_balls   = air_outs + hr
            expected_hr = fly_balls * LEAGUE_HR_PER_FB if fly_balls > 0 else hr
            xfip = (13 * expected_hr + 3 * (bb + hbp) - 2 * k) / ip + XFIP_CONSTANT if ip >= 5 else era

            return {"ip": round(ip, 1), "fip": round(fip, 2), "xfip": round(xfip, 2)}
        except (ValueError, ZeroDivisionError):
            return None

    home_data = _parse_split(splits_by_code["H"])
    away_data = _parse_split(splits_by_code["R"])

    if home_data is None or away_data is None:
        cache[cache_key] = None
        _save_daily_cache(cache)
        return None

    result = {
        "xfip_home":  home_data["xfip"],
        "xfip_away":  away_data["xfip"],
        "fip_home":   home_data["fip"],
        "fip_away":   away_data["fip"],
        "ip_home":    home_data["ip"],
        "ip_away":    away_data["ip"],
        # split_diff > 0 → pitcher más débil de visitante (favorece OVER en partidos away)
        # split_diff < 0 → pitcher más fuerte de visitante (desfavorece OVER en partidos away)
        "split_diff": round(away_data["xfip"] - home_data["xfip"], 2),
    }

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── MLB STATS API: FORMA RECIENTE DEL PITCHER (L5 SALIDAS) ──────────────────

def _parse_ip(ip_str: str) -> float:
    """Convierte '6.2' → 6.667 (IP en formato MLB → innings decimales)."""
    parts = str(ip_str).split(".")
    return float(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)


def get_batter_recent_tb(player_id: int, n_games: int = 7) -> dict | None:
    """
    Promedio de Total Bases reales en las últimas n_games salidas del bateador.

    TB = H + D + 2×T + 3×HR (singles×1 + doubles×2 + triples×3 + HR×4).
    Captura momentum del bateador: si lleva 8 días sin hit, tb_avg_recent ≈ 0
    y el modelo lo refleja en la lambda. Cacheado diariamente.
    """
    if not player_id:
        return None

    yr    = date.today().year
    cache = _load_daily_cache()
    cache_key = f"batter_tb_recent_{player_id}_{n_games}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.2)
        r = requests.get(f"{MLB_API}/people/{player_id}/stats", params={
            "stats":  "gameLog",
            "group":  "hitting",
            "season": yr,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
    except Exception:
        return None

    if not splits:
        return None

    recent = splits[-n_games:]
    if len(recent) < 3:
        return None

    total_tb = 0
    for s in recent:
        st = s.get("stat", {})
        try:
            h  = int(st.get("hits",       0) or 0)
            d  = int(st.get("doubles",    0) or 0)
            t  = int(st.get("triples",    0) or 0)
            hr = int(st.get("homeRuns",   0) or 0)
            total_tb += h + d + 2 * t + 3 * hr
        except (ValueError, TypeError):
            continue

    result = {
        "tb_avg_recent": round(total_tb / len(recent), 3),
        "n_games":       len(recent),
    }
    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


def get_pitcher_recent_fip(pitcher_id: int, n_starts: int = 5) -> dict | None:
    """
    FIP y ERA de las últimas n_starts salidas del pitcher.
    Usa el game log de la API oficial (una llamada por pitcher, cacheada).

    Retorna dict con:
        fip_recent, era_recent, ip_recent, n_starts_recent
    o None si hay menos de 3 salidas disponibles.
    """
    if not pitcher_id:
        return None

    yr    = date.today().year
    cache = _load_daily_cache()
    # v2: incluye days_rest y last_start_date en el resultado
    cache_key = f"pitcher_recent_v2_{pitcher_id}_{n_starts}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":  "gameLog",
            "group":  "pitching",
            "season": yr,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
    except Exception:
        return None

    # Filtrar solo salidas como abridor
    starts = [
        s for s in splits
        if int(s.get("stat", {}).get("gamesStarted", 0) or 0) == 1
    ]
    if len(starts) < 3:
        return None   # Muy pocas salidas para ser representativo

    recent = starts[-n_starts:]   # Las últimas n_starts

    # Último inicio: la salida más reciente en la lista (último elemento)
    # El game log retorna splits ordenados cronológicamente (más antiguo primero)
    last_start_str  = starts[-1].get("date")   # "YYYY-MM-DD"
    days_rest: int | None = None
    if last_start_str:
        try:
            last_start_dt = date.fromisoformat(last_start_str)
            days_rest     = (date.today() - last_start_dt).days
        except (ValueError, TypeError):
            pass

    # Acumular stats de las últimas n salidas
    total_ip       = 0.0
    total_k        = total_bb = total_hbp = total_hr = total_er = total_air_outs = 0

    for s in recent:
        st = s.get("stat", {})
        try:
            total_ip       += _parse_ip(st.get("inningsPitched", "0"))
            total_k        += int(st.get("strikeOuts",  0) or 0)
            total_bb       += int(st.get("baseOnBalls", 0) or 0)
            total_hbp      += int(st.get("hitBatsmen",  0) or 0)
            total_hr       += int(st.get("homeRuns",    0) or 0)
            total_er       += int(st.get("earnedRuns",  0) or 0)
            total_air_outs += int(st.get("airOuts",     0) or 0)
        except (ValueError, TypeError):
            continue

    if total_ip < 5:
        return None

    fip_recent = (13 * total_hr + 3 * (total_bb + total_hbp) - 2 * total_k) / total_ip + FIP_CONSTANT
    era_recent = (total_er / total_ip) * 9

    # xFIP reciente: normaliza HRs reales a HRs esperados por tasa de liga
    total_fly_balls    = total_air_outs + total_hr
    expected_hr_recent = total_fly_balls * LEAGUE_HR_PER_FB if total_fly_balls > 0 else total_hr
    xfip_recent = (13 * expected_hr_recent + 3 * (total_bb + total_hbp) - 2 * total_k) / total_ip + XFIP_CONSTANT

    k9_recent  = round(total_k  / total_ip * 9, 1)
    bb9_recent = round(total_bb / total_ip * 9, 1)
    # Promedio de Ks reales por salida (sin normalizar por IP) — señal de momentum.
    # Captura directamente "¿cuántos Ks está sacando en la práctica?" sin que una
    # salida larga/corta distorsione la tasa. Se usa como anchor de momentum en
    # el lambda de K-props, en paralelo al k9_blended de temporada.
    k_avg_recent = round(total_k / len(recent), 2)

    result = {
        "fip_recent":      round(fip_recent,  2),
        "xfip_recent":     round(xfip_recent, 2),  # xFIP de las últimas 5 salidas
        "era_recent":      round(era_recent,  2),
        "k9_recent":       k9_recent,
        "bb9_recent":      bb9_recent,
        "kbb9_recent":     round(k9_recent - bb9_recent, 1),
        "k_avg_recent":    k_avg_recent,
        "ip_recent":       round(total_ip, 1),
        "n_starts_recent": len(recent),
        # Días de descanso desde el último inicio hasta hoy
        "last_start_date": last_start_str,
        "days_rest":       days_rest,
    }
    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── MLB STATS API: STATS DE EQUIPO (OFENSIVA) ───────────────────────────────

def get_all_team_offense_stats(season: int | None = None) -> dict[str, dict]:
    """
    OPS, AVG, OBP, SLG, runs/partido de todos los equipos para la temporada.
    Retorna dict keyed por nombre normalizado.
    """
    yr = season or date.today().year
    cache = _load_daily_cache()
    cache_key = f"team_offense_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        r = requests.get(f"{MLB_API}/teams/stats", params={
            "stats":   "season",
            "group":   "hitting",
            "season":  yr,
            "sportId": 1,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
    except Exception as e:
        print(f"  ⚠️  MLB team offense error: {e}")
        return {}

    result: dict[str, dict] = {}
    for entry in splits:
        team_name = entry.get("team", {}).get("name", "")
        st = entry.get("stat", {})
        games = int(st.get("gamesPlayed", 1) or 1)
        runs  = int(st.get("runs", 0) or 0)

        try:
            result[_normalize(team_name)] = {
                "ops":          float(st.get("ops",  "0.700") or "0.700"),
                "obp":          float(st.get("obp",  "0.320") or "0.320"),
                "slg":          float(st.get("slg",  "0.400") or "0.400"),
                "avg":          float(st.get("avg",  "0.250") or "0.250"),
                "runs_per_game":round(runs / games, 2),
                "hr":           int(st.get("homeRuns", 0) or 0),
                "k_pct":        round(int(st.get("strikeOuts", 0) or 0)
                                      / max(int(st.get("atBats", 1) or 1), 1), 3),
                "bb_pct":       round(int(st.get("baseOnBalls", 0) or 0)
                                      / max(int(st.get("plateAppearances", 1) or 1), 1), 3),
                "display_name": team_name,
                "games_played": games,
            }
        except (ValueError, TypeError):
            continue

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


def get_team_offense_stats(team_name: str, season: int | None = None) -> dict | None:
    """Stats ofensivas de un equipo por nombre."""
    all_stats = get_all_team_offense_stats(season)
    key = _fuzzy_match(team_name, all_stats)
    return all_stats[key] if key else None


# ─── MLB STATS API: STATS DE EQUIPO (PITCHEO) ────────────────────────────────

def get_all_team_pitching_stats(season: int | None = None) -> dict[str, dict]:
    """
    ERA, WHIP y FIP agregados de cada equipo para la temporada.
    Se usa como proxy de calidad del bullpen cuando el abridor no completa el partido.

    Una llamada para los 30 equipos (group=pitching).
    Retorna dict keyed por nombre normalizado.
    """
    yr = season or date.today().year
    cache = _load_daily_cache()
    cache_key = f"team_pitching_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        r = requests.get(f"{MLB_API}/teams/stats", params={
            "stats":   "season",
            "group":   "pitching",
            "season":  yr,
            "sportId": 1,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
    except Exception as e:
        print(f"  ⚠️  MLB team pitching error: {e}")
        return {}

    result: dict[str, dict] = {}
    for entry in splits:
        team_name = entry.get("team", {}).get("name", "")
        st = entry.get("stat", {})
        games = int(st.get("gamesPlayed", 1) or 1)

        try:
            ip_str = str(st.get("inningsPitched", "0"))
            parts  = ip_str.split(".")
            ip     = float(parts[0]) + (int(parts[1]) / 3 if len(parts) > 1 and parts[1] else 0)

            k   = int(st.get("strikeOuts",  0) or 0)
            bb  = int(st.get("baseOnBalls", 0) or 0)
            hbp = int(st.get("hitByPitch",  0) or 0)
            hr  = int(st.get("homeRuns",    0) or 0)
            era = float(st.get("era",  "4.50") or "4.50")
            whip= float(st.get("whip", "1.30") or "1.30")

            team_fip = ((13 * hr + 3 * (bb + hbp) - 2 * k) / ip + FIP_CONSTANT) if ip >= 1 else era

            result[_normalize(team_name)] = {
                "era":          round(era, 2),
                "whip":         round(whip, 2),
                "team_fip":     round(team_fip, 2),
                "games_played": games,
                "display_name": team_name,
            }
        except (ValueError, ZeroDivisionError, TypeError):
            continue

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


def get_team_pitching_stats(team_name: str, season: int | None = None) -> dict | None:
    """Stats de pitcheo de un equipo por nombre."""
    all_stats = get_all_team_pitching_stats(season)
    key = _fuzzy_match(team_name, all_stats)
    return all_stats[key] if key else None


# ─── MLB STATS API: PITCHEO RECIENTE (L4 DÍAS) ───────────────────────────────

def get_team_recent_pitching(team_name: str, n_days: int = 4) -> dict | None:
    """
    ERA y WHIP del equipo en los últimos n_days días.

    Proxy de fatiga del bullpen:
    - Si recent ERA > season ERA → bullpen sobreexigido (más carreras esperadas)
    - Si recent ERA < season ERA → bullpen descansado (menos carreras esperadas)

    Misma estructura que get_team_recent_ops() pero group=pitching.
    Retorna dict con 'era_recent', 'games_recent' o None si falla / < 2 juegos.
    """
    yr    = date.today().year
    cache = _load_daily_cache()
    cache_key = f"team_recent_pitching_{_normalize(team_name)}_{n_days}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    team_id = TEAM_ID_MAP.get(team_name)
    if not team_id:
        for k, v in TEAM_ID_MAP.items():
            if _normalize(team_name) in _normalize(k) or _normalize(k) in _normalize(team_name):
                team_id = v
                break
    if not team_id:
        return None

    end_date   = date.today()
    start_date = end_date - timedelta(days=n_days)

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/teams/{team_id}/stats", params={
            "stats":     "byDateRange",
            "group":     "pitching",
            "season":    yr,
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate":   end_date.strftime("%Y-%m-%d"),
            "sportId":   1,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0]["stat"]
    except Exception:
        return None

    try:
        games = int(s.get("gamesPlayed", 0) or 0)
        if games < 2:   # Muy pocos partidos → no confiable
            return None

        era_str  = s.get("era",  "4.50") or "4.50"
        whip_str = s.get("whip", "1.30") or "1.30"
        result = {
            "era_recent":   round(float(era_str),  2),
            "whip_recent":  round(float(whip_str), 2),
            "games_recent": games,
            "display_name": team_name,
        }
    except (ValueError, TypeError):
        return None

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── MLB STATS API: FORMA RECIENTE (L30 DAYS) ────────────────────────────────

def get_team_recent_ops(team_name: str, n_days: int = 28) -> dict | None:
    """
    OPS ofensivo del equipo en los últimos n_days días (proxy de últimos ~20 partidos).
    Usa byDateRange del MLB Stats API con el team ID.

    Retorna dict con 'ops_recent', 'games_recent', 'display_name' o None si falla.
    Se cachea en el cache diario para evitar múltiples llamadas.
    """
    yr    = date.today().year
    cache = _load_daily_cache()
    cache_key = f"team_recent_ops_{_normalize(team_name)}_{n_days}_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    # Buscar team_id
    team_id = TEAM_ID_MAP.get(team_name)
    if not team_id:
        # Intento fuzzy
        for k, v in TEAM_ID_MAP.items():
            if _normalize(team_name) in _normalize(k) or _normalize(k) in _normalize(team_name):
                team_id = v
                break
    if not team_id:
        return None

    end_date   = date.today()
    start_date = end_date - timedelta(days=n_days)

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/teams/{team_id}/stats", params={
            "stats":     "byDateRange",
            "group":     "hitting",
            "season":    yr,
            "startDate": start_date.strftime("%Y-%m-%d"),
            "endDate":   end_date.strftime("%Y-%m-%d"),
            "sportId":   1,
        }, timeout=15)
        r.raise_for_status()
        splits = r.json().get("stats", [{}])[0].get("splits", [])
        if not splits:
            return None
        s = splits[0]["stat"]
    except Exception:
        return None

    try:
        games = int(s.get("gamesPlayed", 0) or 0)
        if games < 3:   # Muy pocos partidos → no confiable
            return None
        runs  = int(s.get("runs", 0) or 0)
        rg    = round(runs / games, 2) if games > 0 else None
        result = {
            "ops_recent":    float(s.get("ops",  "0.700") or "0.700"),
            "obp_recent":    float(s.get("obp",  "0.320") or "0.320"),
            "slg_recent":    float(s.get("slg",  "0.400") or "0.400"),
            "rg_recent":     rg,          # carreras reales / partido (últimos n_days días)
            "runs_recent":   runs,
            "games_recent":  games,
            "display_name":  team_name,
        }
    except (ValueError, TypeError):
        return None

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


def blended_ops(
    season_ops: float,
    team_name: str,
    w_season: float = 0.70,
    n_days: int = 28,
) -> tuple[float, bool]:
    """
    OPS blend: w_season × season + (1-w_season) × reciente.
    Retorna (ops_blended, usó_forma_reciente).

    Si no hay forma reciente disponible, retorna el OPS de temporada sin blend.
    """
    recent = get_team_recent_ops(team_name, n_days)
    if recent is None:
        return season_ops, False
    ops_r = recent["ops_recent"]
    blended = w_season * season_ops + (1.0 - w_season) * ops_r
    return round(blended, 3), True


# ─── THE ODDS API: TOTALES MLB ────────────────────────────────────────────────

def get_mlb_odds() -> list[dict]:
    """
    Cuotas de totales MLB desde The Odds API (hoy y mañana).
    Retorna lista de dicts con home_team, away_team, total_line, over_odds, under_odds,
    commence_iso, bookmaker, game_id, consensus_impl_over, consensus_impl_under.
    """
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey":     config.ODDS_API_KEY,
        "regions":    "us",
        "markets":    "totals,h2h,spreads",
        "oddsFormat": "american",
        "dateFormat": "iso",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        remaining = r.headers.get("x-requests-remaining", "?")
        print(f"  [API] Requests restantes: {remaining}")
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  ⚠️  The Odds API MLB error: {e}")
        return []

    games = []
    today    = date.today()
    tomorrow = today + timedelta(days=1)

    # Procesar TODOS los juegos próximos (hoy no empezados + mañana). Antes se
    # fijaba a un solo día y de noche —con los juegos de hoy ya en curso— el
    # resultado quedaba vacío, rompiendo `--manana`. Ahora se devuelven ambos
    # días y el caller filtra por fecha (run_mlb_picks con solo_manana).
    today_games_raw    = []
    tomorrow_games_raw = []
    for g in raw:
        try:
            utc_dt   = datetime.fromisoformat(g["commence_time"].replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone()
            if utc_dt < datetime.now(timezone.utc) - timedelta(minutes=5):
                continue
            if local_dt.date() == today:
                today_games_raw.append(g)
            elif local_dt.date() == tomorrow:
                tomorrow_games_raw.append(g)
        except Exception:
            pass

    raw_to_process = today_games_raw + tomorrow_games_raw

    for g in raw_to_process:
        home = g["home_team"]
        away = g["away_team"]

        # Extraer la mejor línea de totales (juego completo) y moneyline h2h
        best_over = best_under = None
        best_line = None
        book_name = ""
        over_list  = []
        under_list = []

        # Moneyline (h2h) y Run Line (spreads)
        home_ml_list = []
        away_ml_list = []
        home_rl_list   = []
        away_rl_list   = []
        home_rl_points = []   # spread del equipo local (−1.5 si favoreado, +1.5 si underdog)

        for book in g.get("bookmakers", []):
            for mkt in book.get("markets", []):
                if mkt["key"] == "totals":
                    over_price  = next((o["price"] for o in mkt["outcomes"] if o["name"] == "Over"),  None)
                    under_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == "Under"), None)
                    line_val    = next((o.get("point") for o in mkt["outcomes"] if o["name"] == "Over"), None)

                    if over_price and under_price and line_val is not None:
                        if best_over is None:
                            best_over, best_under = over_price, under_price
                            best_line = line_val
                            book_name = book.get("title", "")
                        over_list.append(over_price)
                        under_list.append(under_price)

                elif mkt["key"] == "h2h":
                    home_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == home), None)
                    away_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == away), None)
                    if home_price and away_price:
                        home_ml_list.append(home_price)
                        away_ml_list.append(away_price)

                elif mkt["key"] == "spreads":
                    home_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == home), None)
                    away_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == away), None)
                    home_point = next((o.get("point") for o in mkt["outcomes"] if o["name"] == home), None)
                    if home_price and away_price and home_point is not None:
                        home_rl_list.append(home_price)
                        away_rl_list.append(away_price)
                        home_rl_points.append(home_point)

        if best_over is None:
            continue

        # Probabilidades implícitas sin vig — power devigging
        from odds_utils import power_devig, american_to_raw_prob

        def _to_prob(odds: int) -> float:
            return american_to_raw_prob(odds)

        raw_over_list  = [_to_prob(o) for o in over_list]
        raw_under_list = [_to_prob(u) for u in under_list]

        if raw_over_list and raw_under_list:
            avg_raw_over  = sum(raw_over_list)  / len(raw_over_list)
            avg_raw_under = sum(raw_under_list) / len(raw_under_list)
            impl_over, impl_under = power_devig(avg_raw_over, avg_raw_under)
            n_books = len(over_list)
        else:
            ro = _to_prob(best_over)
            ru = _to_prob(best_under)
            impl_over, impl_under = power_devig(ro, ru)
            n_books = 1

        # Moneyline: probabilidades implícitas sin vig
        best_home_ml = home_ml_list[0] if home_ml_list else None
        best_away_ml = away_ml_list[0] if away_ml_list else None
        impl_home_ml = impl_away_ml = None
        n_books_ml = 0
        if home_ml_list and away_ml_list:
            avg_rh_ml = sum(_to_prob(o) for o in home_ml_list) / len(home_ml_list)
            avg_ra_ml = sum(_to_prob(o) for o in away_ml_list) / len(away_ml_list)
            impl_home_ml, impl_away_ml = (round(v, 4) for v in power_devig(avg_rh_ml, avg_ra_ml))
            n_books_ml = len(home_ml_list)

        # Run Line (spreads ±1.5): probabilidades implícitas sin vig
        best_home_rl = home_rl_list[0] if home_rl_list else None
        best_away_rl = away_rl_list[0] if away_rl_list else None
        home_rl_point = home_rl_points[0] if home_rl_points else -1.5
        impl_home_rl = impl_away_rl = None
        if home_rl_list and away_rl_list:
            avg_rh_rl = sum(_to_prob(o) for o in home_rl_list) / len(home_rl_list)
            avg_ra_rl = sum(_to_prob(o) for o in away_rl_list) / len(away_rl_list)
            impl_home_rl, impl_away_rl = (round(v, 4) for v in power_devig(avg_rh_rl, avg_ra_rl))

        games.append({
            "game_id":           g["id"],
            "home_team":         home,
            "away_team":         away,
            "commence_iso":      g["commence_time"],
            "total_line":        best_line,
            "over_odds":         best_over,
            "under_odds":        best_under,
            "bookmaker":         book_name,
            "n_books":           n_books,
            "consensus_impl_over":  round(impl_over,  4),
            "consensus_impl_under": round(impl_under, 4),
            # Moneyline h2h
            "home_ml_odds":         best_home_ml,
            "away_ml_odds":         best_away_ml,
            "consensus_impl_home_ml": impl_home_ml,
            "consensus_impl_away_ml": impl_away_ml,
            "n_books_ml":           n_books_ml,
            # Run Line spreads (±1.5)
            "home_rl_odds":           best_home_rl,
            "away_rl_odds":           best_away_rl,
            "home_rl_point":          home_rl_point,   # −1.5 si local favorito, +1.5 si underdog
            "consensus_impl_home_rl": impl_home_rl,
            "consensus_impl_away_rl": impl_away_rl,
        })

    return games


# ─── THE ODDS API: F5 (PRIMEROS 5 INNINGS) ───────────────────────────────────

def get_mlb_f5_odds() -> dict[str, dict]:
    """
    Cuotas de totales F5 (primeros 5 innings) desde The Odds API.

    Mercado correcto: `totals_1st_5_innings` (béisbol NO tiene "mitades"; el
    código viejo pedía `totals_h1`, de básquet/fútbol, por eso F5 NUNCA funcionó).
    Este mercado solo está en el endpoint POR EVENTO, no en el bulk /odds, así
    que se lista eventos (gratis) y se consulta F5 por cada juego PRÓXIMO.

    Cache por bucket de 4h (no diario): el mercado F5 se publica progresivamente
    durante el día, así que un sentinel diario lo dejaba pegado en "sin mercado".

    Retorna dict keyed por (away|home) normalizado con f5_line/odds/impl.
    """
    cache = _load_daily_cache()
    hour_bucket = datetime.now().hour // 4
    cache_key = f"f5_odds_{date.today()}_{hour_bucket}"
    if cache_key in cache:
        stored = cache[cache_key]
        return {} if stored.get("__no_f5__") else stored

    from odds_utils import power_devig

    def _to_prob(odds: int) -> float:
        return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)

    # 1. Listar eventos (gratis, no descuenta quota)
    try:
        ev = requests.get(
            "https://api.the-odds-api.com/v4/sports/baseball_mlb/events",
            params={"apiKey": config.ODDS_API_KEY, "dateFormat": "iso"}, timeout=15,
        ).json()
    except Exception:
        cache[cache_key] = {"__no_f5__": True}; _save_daily_cache(cache); return {}

    now = datetime.now(timezone.utc)
    result: dict[str, dict] = {}
    for e in ev:
        try:
            if datetime.fromisoformat(e["commence_time"].replace("Z", "+00:00")) < now:
                continue   # juego ya empezó
        except (ValueError, TypeError, KeyError):
            continue
        # 2. F5 por evento (1 request c/u; solo juegos próximos)
        try:
            r = requests.get(
                f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{e['id']}/odds",
                params={"apiKey": config.ODDS_API_KEY, "regions": "us",
                        "markets": "totals_1st_5_innings", "oddsFormat": "american"},
                timeout=15,
            )
            if r.status_code != 200:
                continue
            data = r.json()
        except Exception:
            continue

        home, away = e.get("home_team", ""), e.get("away_team", "")
        f5_over = f5_under = f5_line = None
        over_list: list[int] = []
        under_list: list[int] = []
        for book in data.get("bookmakers", []):
            for mkt in book.get("markets", []):
                if mkt["key"] != "totals_1st_5_innings":
                    continue
                o_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == "Over"), None)
                u_price = next((o["price"] for o in mkt["outcomes"] if o["name"] == "Under"), None)
                line_val = next((o.get("point") for o in mkt["outcomes"] if o["name"] == "Over"), None)
                if o_price and u_price and line_val is not None:
                    if f5_over is None:
                        f5_over, f5_under, f5_line = o_price, u_price, line_val
                    over_list.append(o_price)
                    under_list.append(u_price)
        if f5_over is None or f5_line is None:
            continue

        avg_o = sum(_to_prob(o) for o in over_list) / len(over_list)
        avg_u = sum(_to_prob(u) for u in under_list) / len(under_list)
        fo, fu = power_devig(avg_o, avg_u)
        result[f"{_normalize(away)}|{_normalize(home)}"] = {
            "f5_line":       f5_line,
            "f5_over_odds":  f5_over,
            "f5_under_odds": f5_under,
            "f5_impl_over":  round(fo, 4),
            "f5_impl_under": round(fu, 4),
        }

    cache[cache_key] = result if result else {"__no_f5__": True}
    _save_daily_cache(cache)
    return result


# ─── THE ODDS API: PITCHER STRIKEOUT PROPS ────────────────────────────────────

def _match_k_prop(props: dict, pitcher_name: str) -> dict | None:
    """Empareja nombre de pitcher (MLB Stats API) con clave de prop (Odds API)."""
    if not pitcher_name or not props:
        return None
    if pitcher_name in props:
        return props[pitcher_name]
    # Fallback: comparar apellido en minúsculas
    last = pitcher_name.split()[-1].lower()
    for prop_name, data in props.items():
        if prop_name.split()[-1].lower() == last:
            return data
    return None


def get_pitcher_strikeout_props(game_ids: list[str]) -> dict[str, dict]:
    """
    Cuotas de strikeouts del pitcher desde The Odds API (mercado pitcher_strikeouts).
    Una request por partido — cacheado diariamente para conservar quota API.

    Retorna:
      {game_id: {pitcher_name: {"line": float, "over_odds": int, "under_odds": int}}}
    """
    cache     = _load_daily_cache()
    result: dict[str, dict] = {}
    to_fetch  = [gid for gid in game_ids
                 if f"k_props_{gid}" not in cache or f"team_totals_{gid}" not in cache]

    for gid in to_fetch:
        url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{gid}/odds"
        try:
            r = requests.get(url, params={
                "apiKey":     config.ODDS_API_KEY,
                "regions":    "us",
                "markets":    "pitcher_strikeouts,team_totals",
                "oddsFormat": "american",
            }, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠️  K props {gid[:8]}: {e}")
            cache[f"k_props_{gid}"]    = {}
            cache[f"team_totals_{gid}"] = {}
            continue

        pitchers: dict[str, dict] = {}
        teams: dict[str, dict]    = {}
        for bk in data.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                if mkt["key"] == "pitcher_strikeouts" and not pitchers:
                    for outcome in mkt.get("outcomes", []):
                        name = outcome.get("description", "")
                        if not name:
                            continue
                        if name not in pitchers:
                            pitchers[name] = {"line": outcome.get("point")}
                        if outcome["name"].lower() == "over":
                            pitchers[name]["over_odds"] = outcome.get("price")
                        else:
                            pitchers[name]["under_odds"] = outcome.get("price")

                elif mkt["key"] == "team_totals" and not teams:
                    for outcome in mkt.get("outcomes", []):
                        team = outcome.get("description", "")
                        if not team:
                            continue
                        if team not in teams:
                            teams[team] = {"line": outcome.get("point")}
                        if outcome["name"].lower() == "over":
                            teams[team]["over_odds"] = outcome.get("price")
                        else:
                            teams[team]["under_odds"] = outcome.get("price")

        cache[f"k_props_{gid}"]    = pitchers
        cache[f"team_totals_{gid}"] = teams

    _save_daily_cache(cache)

    for gid in game_ids:
        ck = f"k_props_{gid}"
        if ck in cache:
            result[gid] = cache[ck]

    return result


def get_team_total_odds(game_ids: list[str]) -> dict[str, dict]:
    """
    Cuotas de totales por equipo desde cache (populado junto con K-props).
    Retorna: {game_id: {team_name: {"line": float, "over_odds": int, "under_odds": int}}}
    """
    cache  = _load_daily_cache()
    result = {}
    for gid in game_ids:
        ck = f"team_totals_{gid}"
        if ck in cache:
            result[gid] = cache[ck]
    return result


# ─── CLIMA: DATOS DE ESTADIOS ─────────────────────────────────────────────────
#
# open_air : True = clima afecta el juego (estadio abierto o techo retráctil que suele abrir)
#            False = estadio cerrado/domo — ignorar clima
# cf_bearing: ángulo en grados desde home plate hacia center field.
#             Viento soplando en esa dirección = viento a favor de bateadores.
#             Wrigley (CF hacia el E, ~95°) con viento del W es el ejemplo clásico.

STADIUM_INFO: dict[str, dict] = {
    "Arizona Diamondbacks":  {"lat": 33.4453, "lon": -112.0667, "open_air": False, "cf_bearing": 350},  # Chase Field (retráctil, suele cerrar en verano)
    "Atlanta Braves":        {"lat": 33.8907, "lon":  -84.4677, "open_air": True,  "cf_bearing":  10},  # Truist Park
    "Baltimore Orioles":     {"lat": 39.2838, "lon":  -76.6215, "open_air": True,  "cf_bearing":  18},  # Camden Yards
    "Boston Red Sox":        {"lat": 42.3467, "lon":  -71.0972, "open_air": True,  "cf_bearing":  92},  # Fenway Park (CF hacia el E)
    "Chicago Cubs":          {"lat": 41.9484, "lon":  -87.6553, "open_air": True,  "cf_bearing":  95},  # Wrigley Field (CF hacia el E — el más sensible al viento)
    "Chicago White Sox":     {"lat": 41.8299, "lon":  -87.6338, "open_air": True,  "cf_bearing": 355},  # Guaranteed Rate Field
    "Cincinnati Reds":       {"lat": 39.0979, "lon":  -84.5082, "open_air": True,  "cf_bearing":   5},  # Great American Ball Park
    "Cleveland Guardians":   {"lat": 41.4962, "lon":  -81.6852, "open_air": True,  "cf_bearing": 350},  # Progressive Field
    "Colorado Rockies":      {"lat": 39.7559, "lon": -104.9942, "open_air": True,  "cf_bearing":   5},  # Coors Field
    "Detroit Tigers":        {"lat": 42.3390, "lon":  -83.0485, "open_air": True,  "cf_bearing": 350},  # Comerica Park
    "Houston Astros":        {"lat": 29.7573, "lon":  -95.3555, "open_air": False, "cf_bearing": 350},  # Minute Maid Park (retráctil, suele cerrar)
    "Kansas City Royals":    {"lat": 39.0517, "lon":  -94.4803, "open_air": True,  "cf_bearing":   5},  # Kauffman Stadium
    "Los Angeles Angels":    {"lat": 33.8003, "lon": -117.8827, "open_air": True,  "cf_bearing": 350},  # Angel Stadium
    "Los Angeles Dodgers":   {"lat": 34.0739, "lon": -118.2400, "open_air": True,  "cf_bearing":  20},  # Dodger Stadium
    "Miami Marlins":         {"lat": 25.7781, "lon":  -80.2198, "open_air": False, "cf_bearing": 350},  # loanDepot park (retráctil, suele cerrar)
    "Milwaukee Brewers":     {"lat": 43.0280, "lon":  -87.9712, "open_air": False, "cf_bearing": 350},  # American Family Field (retráctil, fría en may/sep)
    "Minnesota Twins":       {"lat": 44.9817, "lon":  -93.2776, "open_air": True,  "cf_bearing":  10},  # Target Field
    "New York Mets":         {"lat": 40.7571, "lon":  -73.8458, "open_air": True,  "cf_bearing": 355},  # Citi Field
    "New York Yankees":      {"lat": 40.8296, "lon":  -73.9262, "open_air": True,  "cf_bearing": 350},  # Yankee Stadium
    "Athletics":             {"lat": 38.5803, "lon": -121.4992, "open_air": True,  "cf_bearing":   5},  # Sutter Health Park, Sacramento
    "Philadelphia Phillies": {"lat": 39.9061, "lon":  -75.1665, "open_air": True,  "cf_bearing": 340},  # Citizens Bank Park
    "Pittsburgh Pirates":    {"lat": 40.4469, "lon":  -80.0057, "open_air": True,  "cf_bearing": 350},  # PNC Park
    "San Diego Padres":      {"lat": 32.7076, "lon": -117.1570, "open_air": True,  "cf_bearing":  10},  # Petco Park
    "San Francisco Giants":  {"lat": 37.7786, "lon": -122.3893, "open_air": True,  "cf_bearing":  15},  # Oracle Park (viento del Pacífico)
    "Seattle Mariners":      {"lat": 47.5914, "lon": -122.3325, "open_air": False, "cf_bearing": 350},  # T-Mobile Park (retráctil)
    "St. Louis Cardinals":   {"lat": 38.6226, "lon":  -90.1928, "open_air": True,  "cf_bearing": 355},  # Busch Stadium
    "Tampa Bay Rays":        {"lat": 27.7682, "lon":  -82.6534, "open_air": False, "cf_bearing": 350},  # Tropicana Field (domo)
    "Texas Rangers":         {"lat": 32.7473, "lon":  -97.0832, "open_air": False, "cf_bearing": 350},  # Globe Life Field (cerrado)
    "Toronto Blue Jays":     {"lat": 43.6414, "lon":  -79.3894, "open_air": False, "cf_bearing": 350},  # Rogers Centre (retráctil, suele cerrar)
    "Washington Nationals":  {"lat": 38.8730, "lon":  -77.0074, "open_air": True,  "cf_bearing": 350},  # Nationals Park
}


def get_weather_for_game(home_team: str, commence_iso: str | None = None) -> dict | None:
    """
    Clima en el estadio del equipo local — PRONÓSTICO a la hora del juego.

    Con commence_iso usa el endpoint /forecast de OpenWeatherMap (bloques de
    3h, gratis con la misma key) y toma el bloque más cercano al primer pitch:
    un juego de 6:41 PM evaluado a las 11 AM se ajusta con el clima de las
    6-9 PM, no con el de las 11 AM. El forecast además trae `pop`
    (probabilidad de precipitación 0-1) — señal de riesgo de delay/posposición
    que los props del nicho usan como gate.

    Fallback al current weather si no hay commence_iso, el juego es inminente
    (<90 min) o el forecast falla.

    Retorna dict con: temp_f, wind_speed_mph, wind_deg, description,
    cf_bearing, pop (None si viene del current), is_forecast.
    """
    api_key = config.OPENWEATHER_API_KEY
    if not api_key:
        return None

    info = STADIUM_INFO.get(home_team)
    if not info or not info.get("open_air"):
        return None

    cache = _load_daily_cache()
    cache_key = f"weather_{home_team}_{date.today()}_{datetime.now().hour}"
    if cache_key in cache:
        return cache[cache_key]

    # ── Pronóstico a la hora del juego (preferido) ────────────────────────────
    game_dt = None
    if commence_iso:
        try:
            from datetime import timezone as _tz
            game_dt = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
            mins_out = (game_dt - datetime.now(_tz.utc)).total_seconds() / 60
            if mins_out < 90:
                game_dt = None   # inminente → el clima actual es el del juego
        except (ValueError, TypeError):
            game_dt = None

    result = None
    if game_dt is not None:
        try:
            r = requests.get("https://api.openweathermap.org/data/2.5/forecast", params={
                "lat":   info["lat"],
                "lon":   info["lon"],
                "appid": api_key,
                "units": "imperial",
                "cnt":   16,   # 48h de bloques de 3h — suficiente para hoy/mañana
            }, timeout=10)
            r.raise_for_status()
            blocks = r.json().get("list", [])
            if blocks:
                target_ts = game_dt.timestamp()
                best = min(blocks, key=lambda b: abs(b["dt"] - target_ts))
                if abs(best["dt"] - target_ts) <= 3 * 3600:   # bloque a ≤3h del juego
                    result = {
                        "temp_f":         round(best["main"]["temp"], 1),
                        "feels_like_f":   round(best["main"].get("feels_like", best["main"]["temp"]), 1),
                        "wind_speed_mph": round(best.get("wind", {}).get("speed", 0.0), 1),
                        "wind_deg":       best.get("wind", {}).get("deg", 0),
                        "description":    best["weather"][0]["description"],
                        "pop":            round(float(best.get("pop", 0.0)), 2),
                        "cf_bearing":     info["cf_bearing"],
                        "open_air":       True,
                        "is_forecast":    True,
                    }
        except Exception as e:
            print(f"  ⚠️  Forecast error ({home_team}): {e} — fallback a clima actual")

    # ── Fallback: clima actual ────────────────────────────────────────────────
    if result is None:
        try:
            r = requests.get("https://api.openweathermap.org/data/2.5/weather", params={
                "lat":   info["lat"],
                "lon":   info["lon"],
                "appid": api_key,
                "units": "imperial",
            }, timeout=10)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  ⚠️  Clima error ({home_team}): {e}")
            return None
        result = {
            "temp_f":         round(data["main"]["temp"], 1),
            "feels_like_f":   round(data["main"].get("feels_like", data["main"]["temp"]), 1),
            "wind_speed_mph": round(data["wind"].get("speed", 0.0), 1),
            "wind_deg":       data["wind"].get("deg", 0),
            "description":    data["weather"][0]["description"],
            "pop":            None,   # current weather no trae prob. de lluvia
            "cf_bearing":     info["cf_bearing"],
            "open_air":       True,
            "is_forecast":    False,
        }

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── ÁRBITROS: TENDENCIAS DE ZONA ────────────────────────────────────────────
#
# Fuente: umpscorecards.com (API pública, sin key)
# run_impact: carreras sobre/bajo el promedio que genera el árbitro por partido.
#   Positivo = zona estrecha → más BB, más carreras para bateadores
#   Negativo = zona amplia   → más Ks, menos carreras para pitchers
# Cap: ±0.50 carreras (estudios de Baseball Savant: efecto típico ±0.3-0.5 runs).

_UMP_RUN_ADJ_CAP = 0.50   # máximo ajuste por árbitro en un sentido
_UMP_GAMES_API  = "https://umpscorecards.com/api/games/"  # datos por partido


def get_umpire_tendencies(season: int | None = None) -> dict[str, float]:
    """
    Calcula el impacto de carrera neto de cada árbitro del home plate.

    Fuente: umpscorecards.com/api/games/ — API pública JSON, sin key.
    Metodología: Para cada partido arbitrado, calcula:
        net_batter_impact = home_batter_impact + away_batter_impact
    Positivo = árbitro tendió a agregar carreras al total (zona estrecha, más BB)
    Negativo = árbitro tendió a quitar carreras (zona amplia, más Ks)

    Cap ±0.50 carreras. Mínimo 5 partidos para incluir árbitro.
    Cacheado diariamente. Retorna {} si la API falla.

    Returns:
        {umpire_name: run_adj_per_game}  — float, cap ±0.50
    """
    yr = season or date.today().year
    cache = _load_daily_cache()
    cache_key = f"umpire_tendencies_{yr}"
    if cache_key in cache:
        return cache[cache_key]

    tendencies: dict[str, float] = {}
    try:
        r = requests.get(
            _UMP_GAMES_API,
            params={"season": yr},
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json().get("rows", [])

        # Agrupar por árbitro: sumar net_batter_impact y contar partidos
        ump_totals: dict[str, list[float]] = {}
        for row in rows:
            name = (row.get("umpire") or "").strip()
            if not name:
                continue
            # Filtrar partidos inválidos o incompletos
            if row.get("failed") or not row.get("fully_valid"):
                continue
            hbi = row.get("home_batter_impact")
            abi = row.get("away_batter_impact")
            if hbi is None or abi is None:
                continue
            net = float(hbi) + float(abi)
            ump_totals.setdefault(name, []).append(net)

        for name, impacts in ump_totals.items():
            if len(impacts) < 5:    # mínimo 5 partidos para confiar en el promedio
                continue
            avg = sum(impacts) / len(impacts)
            # Cap ±0.50 carreras
            avg = max(-_UMP_RUN_ADJ_CAP, min(_UMP_RUN_ADJ_CAP, avg))
            tendencies[name] = round(avg, 3)

        # Solo cacheamos si obtuvimos datos reales — evita envenenar el cache con {}
        # ante un fallo temporal de la API
        if tendencies:
            cache[cache_key] = tendencies
            _save_daily_cache(cache)

    except Exception as e:
        print(f"  ⚠️  Umpire tendencies error: {e}")

    return tendencies


def get_umpire_run_adj(umpire_name: str | None, tendencies: dict[str, float]) -> float:
    """
    Ajuste de carreras por árbitro. Usa fuzzy match para tolerancia en nombres.

    Returns: float en [-0.50, +0.50]. 0.0 si árbitro no encontrado.
    """
    if not umpire_name or not tendencies:
        return 0.0

    # Búsqueda exacta
    if umpire_name in tendencies:
        return tendencies[umpire_name]

    # Búsqueda por apellido — solo si el apellido es ÚNICO en el dict
    # (evita devolver el árbitro equivocado cuando dos comparten apellido, e.g., dos "Jones")
    last_name = umpire_name.strip().split()[-1].lower()
    matches = [(k, v) for k, v in tendencies.items()
               if k.strip().split()[-1].lower() == last_name]
    if len(matches) == 1:
        return matches[0][1]

    return 0.0


# ─── H2H: HISTORIAL DEL PITCHER VS EQUIPO ESPECÍFICO ────────────────────────
#
# La MLB Stats API provee stats de carrera de un pitcher vs cada equipo.
# Se usa el OPS histórico del equipo vs ese pitcher (no ERA — la API no la retorna en vsTeam).
# Ajuste conservador: ±OPS_diff × 4.0 runs, cap ±0.30 carreras por equipo.
# Mínimo 30 PA para confiar en la muestra (≈ 3-4 salidas del pitcher vs ese equipo).

_H2H_ADJ_CAP    = 0.30   # cap máximo del ajuste H2H en carreras (por equipo)
_H2H_MIN_PA     = 30     # mínimo plate appearances del equipo vs este pitcher para aplicar ajuste
                          # ≈ 8 PA/juego × 4 salidas del pitcher = 32 PA — muestra mínima razonable
_H2H_ADJ_SCALE  = 4.0    # factor de conversión: 1 OPS unit diff → X carreras ajuste/partido
                          # LEAGUE_AVG_RUNS / LEAGUE_AVG_OPS ≈ 4.45 / 0.718 ≈ 6.2; usamos 4.0
                          # (conservador — H2H tiene peso menor que el modelo base)


def get_pitcher_vs_team_ops(pitcher_id: int, opp_team_id: int) -> dict | None:
    """
    OPS histórico que el equipo oponente produce vs este pitcher (carrera completa).

    Fuente: MLB Stats API /people/{id}/stats?stats=vsTeam&group=pitching&opposingTeamId={id}
    La API retorna las stats de BATEO del equipo oponente vs el pitcher — incluye OPS, avg, HR, K.
    Sin filtro de temporada — usa toda la carrera para mayor muestra.

    Por qué OPS en vez de ERA:
    - La API no retorna IP ni ERA en el endpoint vsTeam.
    - El OPS-allowed vs este equipo es directamente comparable al team_season_ops.
    - Si el equipo produce OPS .900 vs este pitcher pero .740 en general → +.160 ventaja ofensiva.

    Args:
        pitcher_id:   ID del pitcher en la MLB Stats API.
        opp_team_id:  ID del equipo oponente (de TEAM_ID_MAP).

    Returns:
        Dict con {"ops_vs", "pa_vs", "hr_vs", "k_vs", "avg_vs"} o None si PA < _H2H_MIN_PA.
    """
    if not pitcher_id or not opp_team_id:
        return None

    cache = _load_daily_cache()
    cache_key = f"h2h_{pitcher_id}_vs_{opp_team_id}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":           "vsTeam",
            "group":           "pitching",
            "opposingTeamId":  opp_team_id,
        }, timeout=15)
        r.raise_for_status()
        raw_stats = r.json().get("stats", [])
    except Exception as e:
        print(f"  [WARN] H2H pitcher stats error: {e}")
        return None

    # La API retorna 2 bloques: vsTeamTotal (carrera) y vsTeam (por temporada).
    # Buscamos vsTeamTotal para mayor muestra histórica.
    splits = []
    for block in raw_stats:
        if block.get("type", {}).get("displayName") in ("vsTeamTotal", "vsTeam"):
            splits = block.get("splits", [])
            if splits:
                break

    if not splits:
        cache[cache_key] = None
        _save_daily_cache(cache)
        return None

    s = splits[0].get("stat", {})
    try:
        pa_vs = int(s.get("plateAppearances", 0) or 0)

        if pa_vs < _H2H_MIN_PA:   # muestra insuficiente (ver constante arriba)
            cache[cache_key] = None
            _save_daily_cache(cache)
            return None

        result = {
            "ops_vs": round(float(s.get("ops",  "0.700") or "0.700"), 3),
            "obp_vs": round(float(s.get("obp",  "0.320") or "0.320"), 3),
            "slg_vs": round(float(s.get("slg",  "0.400") or "0.400"), 3),
            "avg_vs": round(float(s.get("avg",  "0.250") or "0.250"), 3),
            "pa_vs":  pa_vs,
            "hr_vs":  int(s.get("homeRuns",  0) or 0),
            "k_vs":   int(s.get("strikeOuts", 0) or 0),
        }
    except (ValueError, TypeError):
        cache[cache_key] = None
        _save_daily_cache(cache)
        return None

    cache[cache_key] = result
    _save_daily_cache(cache)
    return result


# ─── IL (INJURED LIST) ────────────────────────────────────────────────────────

def get_team_il_ids(team_id: int) -> set[int]:
    """
    Retorna set de player_ids actualmente en la IL del equipo
    (10-day, 15-day, 60-day — cualquier tipo de IL).
    Cache diaria por equipo.
    """
    if not team_id:
        return set()

    cache = _load_daily_cache()
    cache_key = f"il_{team_id}_{date.today().isoformat()}"
    if cache_key in cache:
        return set(cache[cache_key])

    try:
        time.sleep(0.2)
        r = requests.get(
            f"{MLB_API}/teams/{team_id}/roster",
            params={"rosterType": "injuries", "season": date.today().year},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        ids = [p["person"]["id"] for p in data.get("roster", []) if "person" in p]
    except Exception as e:
        print(f"  ⚠️  IL fetch error team {team_id}: {e}")
        ids = []

    cache[cache_key] = ids
    _save_daily_cache(cache)
    return set(ids)


# ─── LINEUP DEL DÍA: OPS POR TITULAR CONFIRMADO ──────────────────────────────

def get_team_active_roster(team_name: str) -> list[int]:
    """
    Retorna lista de player_ids del roster activo del equipo.
    Usado como fallback cuando el lineup confirmado no está disponible aún.
    Cache diaria.
    """
    team_id = TEAM_ID_MAP.get(team_name)
    if not team_id:
        for k, v in TEAM_ID_MAP.items():
            if _normalize(team_name) in _normalize(k) or _normalize(k) in _normalize(team_name):
                team_id = v
                break
    if not team_id:
        return []

    cache_key = f"roster_{team_id}"
    cache     = _load_daily_cache()
    if cache_key in cache:
        return cache[cache_key]

    try:
        time.sleep(0.3)
        r = requests.get(
            f"{MLB_API}/teams/{team_id}/roster/active",
            params={"season": date.today().year},
            timeout=10,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        ids  = [p["person"]["id"] for p in data.get("roster", []) if "person" in p]
        cache[cache_key] = ids
        _save_daily_cache(cache)
        return ids
    except Exception:
        return []


def get_batch_player_hitting_stats(
    player_ids: list[int],
    season: int | None = None,
) -> dict[int, dict]:
    """
    Fetch hitting stats para múltiples jugadores en una sola llamada batch.

    Usa el endpoint /people?personIds=...&hydrate=stats(group=[hitting],type=[season],...).
    Cachea cada jugador individualmente en el cache diario.

    Args:
        player_ids: Lista de player IDs (MLBam IDs del batting order).
        season: Temporada (default: año actual).

    Returns:
        {player_id: {"ops", "obp", "slg", "avg", "pa"}} para jugadores con datos.
        Jugadores con < 10 PA o sin datos no se incluyen.
    """
    if not player_ids:
        return {}

    yr    = season or date.today().year
    cache = _load_daily_cache()

    # Separar jugadores ya cacheados de los que necesitan fetch
    result:   dict[int, dict] = {}
    uncached: list[int]       = []
    for pid in player_ids:
        ck = f"player_hit_{pid}_{yr}"
        if ck in cache:
            if cache[ck]:  # None significa que ya lo intentamos y no tiene datos
                result[pid] = cache[ck]
        else:
            uncached.append(pid)

    if not uncached:
        return result

    # Batch call — hasta 50 IDs por request (límite de la API)
    BATCH_SIZE = 50
    for i in range(0, len(uncached), BATCH_SIZE):
        batch = uncached[i:i + BATCH_SIZE]
        ids_str = ",".join(str(p) for p in batch)
        try:
            time.sleep(0.5)
            r = requests.get(f"{MLB_API}/people", params={
                "personIds": ids_str,
                "hydrate":   f"stats(group=[hitting],type=[season],season=[{yr}])",
            }, timeout=20)
            r.raise_for_status()
            people = r.json().get("people", [])
        except Exception as e:
            print(f"  [WARN] Batch player stats error: {e}")
            continue  # Devuelve lo que ya tenemos

        for person in people:
            pid = person.get("id")
            if not pid:
                continue
            # batSide está en el objeto base — cachear siempre, independiente de stats
            bat_side = person.get("batSide", {})
            bat_side_code = bat_side.get("code") if isinstance(bat_side, dict) else None
            cache[f"bat_side_{pid}"] = bat_side_code

            stats_list = person.get("stats", [])
            splits = stats_list[0].get("splits", []) if stats_list else []
            s      = splits[0].get("stat", {}) if splits else {}
            try:
                pa = int(s.get("plateAppearances", 0) or 0)
                if pa < 10:
                    cache[f"player_hit_{pid}_{yr}"] = None  # marca "sin datos"
                    continue
                games = int(s.get("gamesPlayed", 0) or 0)
                ab    = int(s.get("atBats",      0) or 0)
                so    = int(s.get("strikeOuts",  0) or 0)
                player_data = {
                    "ops":  round(float(s.get("ops",  "0.700") or "0.700"), 3),
                    "obp":  round(float(s.get("obp",  "0.320") or "0.320"), 3),
                    "slg":  round(float(s.get("slg",  "0.400") or "0.400"), 3),
                    "avg":  round(float(s.get("avg",  "0.250") or "0.250"), 3),
                    "pa":   pa,
                    "ab":   ab,
                    "k_pct": round(so / ab, 3) if ab > 0 else None,   # K/AB del bateador
                    "games": games,
                    "ab_per_game": round(ab / games, 2) if games > 0 else 3.66,
                    "name": person.get("fullName", ""),
                }
                result[pid] = player_data
                cache[f"player_hit_{pid}_{yr}"] = player_data
            except (ValueError, TypeError):
                cache[f"player_hit_{pid}_{yr}"] = None

        # Marcar jugadores sin respuesta (no estaban en la API para este año)
        returned_ids = {p.get("id") for p in people if p.get("id")}
        for pid in batch:
            if pid not in returned_ids:
                cache[f"player_hit_{pid}_{yr}"] = None

    _save_daily_cache(cache)
    return result


def get_savant_whiff(season: int | None = None) -> dict[int, float]:
    """
    Whiff% (swings & misses / swings) por pitcher desde el custom leaderboard
    de Baseball Savant. UNA request por día — cachea diariamente.

    El whiff% es el mejor predictor de Ks junto al K% mismo (r≈0.78 año-a-año)
    y estabiliza MÁS RÁPIDO que el K/9 — detecta breakouts/declives de stuff
    antes de que el K/9 converja. Usado como corrector del K/9 en K-props.

    Retorna {player_id: whiff_pct} (porcentaje, ej. 26.4).
    """
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"savant_whiff_{yr}"
    if ck in cache:
        return {int(k): v for k, v in cache[ck].items()}

    try:
        r = requests.get("https://baseballsavant.mlb.com/leaderboard/custom", params={
            "year": yr, "type": "pitcher", "min": "10",
            "selections": "whiff_percent", "csv": "true",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Savant whiff fetch error: {e}")
        return {}

    import csv as _csv
    result: dict[int, float] = {}
    try:
        text   = r.content.decode("utf-8-sig")
        reader = _csv.DictReader(text.strip().splitlines())
        for row in reader:
            try:
                pid   = int(row.get("player_id", "").strip())
                whiff = float(row.get("whiff_percent", "").strip())
                result[pid] = round(whiff, 1)
            except (ValueError, TypeError):
                continue
    except Exception as e:
        print(f"  [WARN] Savant whiff parse error: {e}")
        return {}

    cache[ck] = {str(k): v for k, v in result.items()}
    _save_daily_cache(cache)
    return result


def get_pitcher_k_splits(pitcher_id: int, season: int | None = None) -> dict | None:
    """
    Splits K% del pitcher vs zurdos y vs diestros (MLB Stats API, gratis).

    El platoon de Ks: un pitcher slider-heavy poncha mucho más al lado que
    abre hacia su slider. Cruzado con la fracción zurda del lineup confirmado
    (que ya calculamos) da el factor de matchup real del día.

    Retorna {"k_pct_vs_l", "k_pct_vs_r", "bf_l", "bf_r"} (K/BF por lado)
    o None sin datos. Cache diario por pitcher.
    """
    if not pitcher_id:
        return None
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"k_splits_{pitcher_id}_{yr}"
    if ck in cache:
        return cache[ck]

    try:
        time.sleep(0.4)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":    "statSplits",
            "group":    "pitching",
            "sitCodes": "vl,vr",
            "season":   yr,
        }, timeout=15)
        r.raise_for_status()
        stats = r.json().get("stats", [])
    except Exception:
        cache[ck] = None
        _save_daily_cache(cache)
        return None

    out: dict = {}
    for block in stats:
        for split in block.get("splits", []):
            code = (split.get("split") or {}).get("code", "")
            s    = split.get("stat", {})
            try:
                so = int(s.get("strikeOuts", 0) or 0)
                bf = int(s.get("battersFaced", 0) or 0)
            except (ValueError, TypeError):
                continue
            if bf <= 0:
                continue
            if code == "vl":
                out["k_pct_vs_l"], out["bf_l"] = round(so / bf, 4), bf
            elif code == "vr":
                out["k_pct_vs_r"], out["bf_r"] = round(so / bf, 4), bf

    result = out if ("k_pct_vs_l" in out and "k_pct_vs_r" in out) else None
    cache[ck] = result
    _save_daily_cache(cache)
    return result


def get_batter_platoon_k_splits(player_id: int, season: int | None = None) -> dict | None:
    """
    K% del BATEADOR vs zurdos (LHP) y vs diestros (RHP) — espejo del split del pitcher.

    Cada bateador tiene un perfil platoon distinto: algunos se debaten más vs zurdos
    (p.ej. bateador diestro vs LHP), otros son más balanceados. Cruzar esto con la
    mano del abridor de hoy da el `opp_k_pct` más preciso posible para la lambda.

    Retorna {"k_pct_vs_l", "pa_vs_l", "k_pct_vs_r", "pa_vs_r"} o None sin datos.
    Cache diario por bateador.
    """
    if not player_id:
        return None
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"batter_platoon_{player_id}_{yr}"
    if ck in cache:
        return cache[ck]

    try:
        time.sleep(0.15)
        r = requests.get(f"{MLB_API}/people/{player_id}/stats", params={
            "stats":    "statSplits",
            "group":    "hitting",
            "sitCodes": "vl,vr",
            "season":   yr,
        }, timeout=15)
        r.raise_for_status()
        stats = r.json().get("stats", [])
    except Exception:
        cache[ck] = None
        _save_daily_cache(cache)
        return None

    out: dict = {}
    for block in stats:
        for split in block.get("splits", []):
            code = (split.get("split") or {}).get("code", "")
            s    = split.get("stat", {})
            try:
                so = int(s.get("strikeOuts",       0) or 0)
                pa = int(s.get("plateAppearances", 0) or 0)
            except (ValueError, TypeError):
                continue
            if pa <= 0:
                continue
            if code == "vl":
                out["k_pct_vs_l"] = round(so / pa, 4)
                out["pa_vs_l"]    = pa
            elif code == "vr":
                out["k_pct_vs_r"] = round(so / pa, 4)
                out["pa_vs_r"]    = pa

    result = out if out else None
    cache[ck] = result
    _save_daily_cache(cache)
    return result


def get_savant_xera(season: int | None = None) -> dict[int, dict]:
    """
    Descarga el leaderboard de expected statistics de Baseball Savant para pitchers.
    UNA request por temporada — cachea diariamente.

    Retorna {player_id: {"xera": float, "era": float, "xwoba": float, "pa": int}}.
    Incluye a todos los pitchers con >= 10 PA (mínimo de la API).
    """
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"savant_xera_{yr}"
    if ck in cache:
        # El cache guarda {str_id: dict}; convertimos keys a int
        return {int(k): v for k, v in cache[ck].items()}

    url = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
    try:
        r = requests.get(url, params={
            "type": "pitcher", "year": yr,
            "position": "", "team": "", "min": "10", "csv": "true",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Savant xERA fetch error: {e}")
        return {}

    import csv as _csv
    result: dict[int, dict] = {}
    try:
        # utf-8-sig decodifica el BOM inicial (﻿) que precede al primer campo
        # del CSV de Savant — sin esto la primera columna se divide incorrectamente
        text  = r.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        reader = _csv.DictReader(lines)
        for row in reader:
            pid_str = row.get("player_id", "").strip()
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
            except ValueError:
                continue
            def _f(key: str) -> float | None:
                v = row.get(key, "").strip()
                try:
                    return round(float(v), 3) if v else None
                except ValueError:
                    return None
            xera = _f("xera")
            if xera is None:
                continue
            result[pid] = {
                "xera":  xera,
                "era":   _f("era"),
                "xwoba": _f("est_woba"),
                "woba":  _f("woba"),
                "pa":    int(row.get("pa", 0) or 0),
            }
    except Exception as e:
        print(f"  [WARN] Savant xERA parse error: {e}")
        return {}

    if result:
        cache[ck] = {str(k): v for k, v in result.items()}
        _save_daily_cache(cache)
    return result


def get_savant_pitcher_contact(season: int | None = None) -> dict[int, dict]:
    """
    Descarga estadísticas de contacto de Baseball Savant para pitchers.
    UNA request bulk por temporada — cachea diariamente.

    Retorna {player_id: {"brl_pct": float, "hard_hit_pct": float, "avg_ev": float}}.
        brl_pct        → barrel% permitido (batted balls con ángulo+velocidad de HR)
        hard_hit_pct   → hard hit% permitido (exit velocity ≥ 95 mph)
        avg_ev         → exit velocity promedio permitida (mph)
    Estas métricas son más estables que ERA y complementan xFIP al medir calidad de contacto.
    """
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"savant_contact_{yr}"
    if ck in cache:
        return {int(k): v for k, v in cache[ck].items()}

    url = "https://baseballsavant.mlb.com/leaderboard/statcast"
    try:
        r = requests.get(url, params={
            "type": "pitcher", "year": yr,
            "position": "", "team": "", "min": "10", "csv": "true",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Savant contact fetch error: {e}")
        return {}

    import csv as _csv
    result: dict[int, dict] = {}
    try:
        text  = r.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        reader = _csv.DictReader(lines)
        for row in reader:
            pid_str = row.get("player_id", "").strip()
            if not pid_str:
                continue
            try:
                pid = int(pid_str)
            except ValueError:
                continue

            def _f(key: str) -> float | None:
                v = row.get(key, "").strip()
                try:
                    return round(float(v), 2) if v else None
                except ValueError:
                    return None

            brl = _f("brl_percent")
            hh  = _f("ev95percent")
            ev  = _f("avg_hit_speed")
            if brl is None and hh is None:
                continue
            result[pid] = {
                "brl_pct":      brl,
                "hard_hit_pct": hh,
                "avg_ev":       ev,
            }
    except Exception as e:
        print(f"  [WARN] Savant contact parse error: {e}")
        return {}

    if result:
        cache[ck] = {str(k): v for k, v in result.items()}
        _save_daily_cache(cache)
    return result


def get_savant_team_batting_xwoba(season: int | None = None) -> dict[str, dict]:
    """
    xwOBA ofensivo por equipo desde Baseball Savant expected statistics.
    UNA request para los 30 equipos — cachea diariamente.

    Retorna {team_abbr: {"xwoba": float, "woba": float, "pa": int}} donde
    team_abbr es la abreviatura estándar MLB (NYY, LAD, etc.).

    xwOBA (expected wOBA) elimina la suerte en BABIP usando la velocidad de salida
    y el ángulo de contacto — más predictivo que wOBA real para proyecciones futuras.
    Liga avg xwOBA = 0.316 (2025, 30 equipos con temporada completa).
    """
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"savant_team_xwoba_{yr}"
    if ck in cache:
        return cache[ck]

    url = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
    try:
        r = requests.get(url, params={
            "type": "batter-team", "year": yr,
            "position": "", "team": "", "min": "q", "csv": "true",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  [WARN] Savant team xwOBA fetch error: {e}")
        return {}

    import csv as _csv
    # Savant usa algunas abreviaturas distintas a nuestro TEAM_MAP
    _SAVANT_ABBR_FIX = {"AZ": "ARI"}   # Savant "AZ" → nuestro "ARI"

    result: dict[str, dict] = {}
    try:
        text  = r.content.decode("utf-8-sig")
        lines = text.strip().splitlines()
        reader = _csv.DictReader(lines)
        for row in reader:
            abbr_raw = row.get("team_id", "").strip()
            if not abbr_raw:
                continue
            abbr = _SAVANT_ABBR_FIX.get(abbr_raw, abbr_raw)

            def _f(key: str) -> float | None:
                v = row.get(key, "").strip()
                try:
                    return round(float(v), 4) if v else None
                except ValueError:
                    return None

            xwoba = _f("est_woba")
            woba  = _f("woba")
            if xwoba is None:
                continue
            result[abbr] = {
                "xwoba": xwoba,
                "woba":  woba,
                "pa":    int(row.get("pa", 0) or 0),
            }
    except Exception as e:
        print(f"  [WARN] Savant team xwOBA parse error: {e}")
        return {}

    if result:
        cache[ck] = result
        _save_daily_cache(cache)
    return result


def get_pitcher_arsenal(pitcher_id: int, season: int | None = None) -> dict:
    """
    Fetch pitch arsenal stats del pitcher: velocidad promedio por tipo + uso%.
    Usa el endpoint /people/{id}/stats?stats=pitchArsenal del MLB Stats API.
    Cached diariamente.

    Retorna dict con:
        "fastball_velo":  mph promedio del fastball principal (FF/FT/SI)
        "fastball_type":  código del pitch (FF, FT, SI...)
        "total_pitches":  total de pitches en la temporada
    """
    if not pitcher_id:
        return {}

    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"pitcher_arsenal_{pitcher_id}_{yr}"
    if ck in cache:
        return cache[ck] or {}

    try:
        time.sleep(0.3)
        r = requests.get(f"{MLB_API}/people/{pitcher_id}/stats", params={
            "stats":  "pitchArsenal",
            "group":  "pitching",
            "season": yr,
        }, timeout=15)
        r.raise_for_status()
        stats_list = r.json().get("stats") or []
        splits = stats_list[0].get("splits", []) if stats_list else []
    except Exception as e:
        print(f"  [WARN] pitcher_arsenal({pitcher_id}) error: {e}")
        cache[ck] = None
        _save_daily_cache(cache)
        return {}

    # Identificar el fastball principal (FF > FT > SI > FC en ese orden)
    _FASTBALL_PRIORITY = ("FF", "FT", "SI", "FC")
    pitches: list[dict] = []
    for split in splits:
        s = split.get("stat", {})
        code  = (s.get("type") or {}).get("code", "")
        velo  = s.get("averageSpeed")
        usage = s.get("percentage", 0.0)
        total = s.get("totalPitches", 0)
        if code and velo is not None:
            pitches.append({"code": code, "velo": round(velo, 1), "usage": round(usage, 3), "total": total})

    result: dict = {}
    if pitches:
        result["total_pitches"] = pitches[0]["total"] if pitches else 0
        # Fastball principal por prioridad
        fb = next(
            (p for fb_code in _FASTBALL_PRIORITY for p in pitches if p["code"] == fb_code),
            None
        )
        if fb:
            result["fastball_velo"] = fb["velo"]
            result["fastball_type"] = fb["code"]
            result["fastball_usage"] = fb["usage"]

    cache[ck] = result if result else None
    _save_daily_cache(cache)
    return result


def get_pitcher_hands(pitcher_ids: list[int]) -> dict[int, str | None]:
    """
    Retorna {pitcher_id: pitch_hand_code} para los IDs dados. Cached diariamente.

    pitch_hand_code: "L", "R", o None si no hay datos.
    Hace UNA llamada batch para todos los pitchers del día.
    """
    if not pitcher_ids:
        return {}

    cache   = _load_daily_cache()
    result  = {}
    uncached: list[int] = []
    for pid in pitcher_ids:
        ck = f"pitcher_hand_{pid}"
        if ck in cache:
            result[pid] = cache[ck]
        else:
            uncached.append(pid)

    if uncached:
        ids_str = ",".join(str(i) for i in uncached)
        try:
            r = requests.get(f"{MLB_API}/people", params={
                "personIds": ids_str,
            }, timeout=15)
            r.raise_for_status()
            returned_ids: set[int] = set()
            for person in r.json().get("people", []):
                pid  = person.get("id")
                hand = person.get("pitchHand", {})
                code = hand.get("code") if isinstance(hand, dict) else None
                if pid:
                    cache[f"pitcher_hand_{pid}"] = code
                    result[pid] = code
                    returned_ids.add(pid)
            for pid in uncached:
                if pid not in returned_ids:
                    cache[f"pitcher_hand_{pid}"] = None
                    result[pid] = None
        except Exception as e:
            print(f"  [WARN] pitcher_hands fetch error: {e}")
            for pid in uncached:
                result.setdefault(pid, None)
        _save_daily_cache(cache)

    return result


def get_lineup_bat_pct_l(lineup_ids: list[int]) -> float | None:
    """
    Fracción del lineup que batea zurdo (incluye switch como 0.5).
    Lee primero del cache (populado por get_batch_player_hitting_stats).
    Si faltan IDs, hace una llamada batch mínima para obtener batSide.

    Retorna None si hay < 5 bateadores con handedness conocido.
    """
    if not lineup_ids:
        return None

    cache    = _load_daily_cache()
    uncached = [pid for pid in lineup_ids if f"bat_side_{pid}" not in cache]

    if uncached:
        ids_str = ",".join(str(i) for i in uncached)
        try:
            time.sleep(0.3)
            r = requests.get(f"{MLB_API}/people", params={
                "personIds": ids_str,
            }, timeout=15)
            r.raise_for_status()
            returned_ids: set[int] = set()
            for person in r.json().get("people", []):
                pid  = person.get("id")
                side = person.get("batSide", {})
                code = side.get("code") if isinstance(side, dict) else None
                if pid:
                    cache[f"bat_side_{pid}"] = code
                    returned_ids.add(pid)
            for pid in uncached:
                if pid not in returned_ids:
                    cache[f"bat_side_{pid}"] = None
        except Exception as e:
            print(f"  [WARN] bat_side fetch error: {e}")
        _save_daily_cache(cache)

    hands = [cache.get(f"bat_side_{pid}") for pid in lineup_ids]
    hands = [h for h in hands if h in ("L", "R", "S")]
    if len(hands) < 5:
        return None
    left = sum(1.0 if h == "L" else 0.5 if h == "S" else 0.0 for h in hands)
    return round(left / len(hands), 3)


def get_lineup_ops(
    lineup_ids: list[int],
    team_fallback_ops: float,
    min_batters: int = 7,
    min_pa: int = 30,
) -> tuple[float, bool, list[str]]:
    """
    OPS ponderado (por PA) del lineup titular confirmado.

    Solo se usa el lineup si al menos `min_batters` jugadores tienen ≥ `min_pa` PA.
    Si no hay suficientes datos, retorna el OPS de equipo como fallback.

    Args:
        lineup_ids:        IDs de los bateadores en orden (de get_mlb_schedule).
        team_fallback_ops: OPS del equipo (blended temporada/reciente) como fallback.
        min_batters:       Mínimo de bateadores con datos para usar el lineup (default 7).
        min_pa:            Mínimo de plate appearances por bateador (default 30).

    Returns:
        (ops_value, lineup_was_used, player_names_with_data)
    """
    _WEAK_OPS_FLOOR = 0.650  # bateador "hueco" en el lineup

    if not lineup_ids:
        return team_fallback_ops, False, [], 0

    player_stats = get_batch_player_hitting_stats(lineup_ids)

    # Filtrar jugadores con muestra suficiente
    valid = [
        (pid, stats)
        for pid, stats in player_stats.items()
        if pid in lineup_ids and stats.get("pa", 0) >= min_pa
    ]

    if len(valid) < min_batters:
        return team_fallback_ops, False, [], 0

    # OPS ponderado por PA (más PA = más peso)
    total_pa      = sum(s["pa"] for _, s in valid)
    weighted_ops  = sum(s["ops"] * s["pa"] for _, s in valid) / total_pa
    player_names  = [s.get("name", "?") for _, s in valid[:5]]  # top 5 para display
    weak_count    = sum(1 for _, s in valid if s.get("ops", 1.0) < _WEAK_OPS_FLOOR)

    return round(weighted_ops, 3), True, player_names, weak_count


def get_lineup_k_pct(
    lineup_ids: list[int],
    team_fallback_k_pct: float,
    pitcher_hand: str | None = None,
    min_batters: int = 7,
    min_pa: int = 30,
    min_platoon_pa: int = 40,
) -> tuple[float, bool, int]:
    """
    K-rate ponderado por PA del lineup CONFIRMADO, ajustado por la mano del pitcher.

    Cuando `pitcher_hand` está disponible ("L" o "R"), cada bateador aporta su
    K%vsLHP o K%vsRHP en vez de su K% global — la señal más específica posible
    antes del primer pitch. El split individual captura el platoon real del día
    (e.g. un diestro que abanicar mucho vs LHP pesa más si el abridor es zurdo).
    Si el bateador tiene < 40 PA vs esa mano, se cae al K% global (muestra chica).

    Returns: (k_pct, lineup_was_used, n_high_k)
    """
    if not lineup_ids:
        return team_fallback_k_pct, False, 0

    player_stats = get_batch_player_hitting_stats(lineup_ids)
    valid = [
        (pid, st) for pid, st in player_stats.items()
        if pid in lineup_ids and st.get("pa", 0) >= min_pa
        and st.get("k_pct") is not None
    ]
    if len(valid) < min_batters:
        return team_fallback_k_pct, False, 0

    # Splits platoon por bateador — 1 llamada cacheada por jugador
    platoon: dict[int, dict] = {}
    if pitcher_hand in ("L", "R"):
        for pid, _ in valid:
            sp = get_batter_platoon_k_splits(pid)
            if sp:
                platoon[pid] = sp

    split_key = "k_pct_vs_l" if pitcher_hand == "L" else "k_pct_vs_r"
    pa_key    = "pa_vs_l"    if pitcher_hand == "L" else "pa_vs_r"

    entries: list[tuple[int, float, float]] = []   # (pid, k_pct_used, weight_pa)
    n_high_k = 0
    for pid, st in valid:
        k_pct_used = st["k_pct"]   # fallback: K% global
        if pitcher_hand in ("L", "R") and pid in platoon:
            sp = platoon[pid]
            if (sp.get(split_key) is not None
                    and sp.get(pa_key, 0) >= min_platoon_pa):
                k_pct_used = sp[split_key]
        entries.append((pid, k_pct_used, st["pa"]))
        if k_pct_used >= 0.28:
            n_high_k += 1

    total_pa = sum(pa for _, _, pa in entries)
    k_pct    = sum(k * pa for _, k, pa in entries) / total_pa
    return round(k_pct, 4), True, n_high_k


# ─── PIPELINE PRINCIPAL ───────────────────────────────────────────────────────

def _pitcher_dict(name: str, stats: dict | None) -> dict | None:
    """Combina nombre del pitcher con sus stats en un solo dict."""
    if name in ("TBD", "", None):
        return None
    base = {"name": name}
    if stats:
        base.update(stats)
    return base


def _add_savant(
    d: dict | None,
    arsenal: dict,
    savant: dict,
    contact: dict | None = None,
) -> dict | None:
    """Agrega datos de Savant (xERA, velo, barrel%, hard_hit%) al dict del pitcher."""
    if d is None:
        return None
    if arsenal.get("fastball_velo") is not None:
        d["fastball_velo"]  = arsenal["fastball_velo"]
        d["fastball_type"]  = arsenal.get("fastball_type", "FF")
        d["fastball_usage"] = arsenal.get("fastball_usage")
    if savant.get("xera") is not None:
        d["xera"]  = savant["xera"]
        d["xwoba"] = savant.get("xwoba")
    # Calidad de contacto permitido: barrel% y hard_hit% (más estables que ERA/FIP)
    if contact:
        if contact.get("brl_pct") is not None:
            d["brl_pct_allowed"]      = contact["brl_pct"]
            d["hard_hit_pct_allowed"] = contact.get("hard_hit_pct")
            d["avg_ev_allowed"]       = contact.get("avg_ev")
    return d


# ─── SAVANT: xSLG POR BATEADOR ────────────────────────────────────────────────

def get_savant_batter_xslg(season: int | None = None) -> dict[int, dict]:
    """
    xBA, xSLG y xwOBA por bateador desde Baseball Savant expected statistics.
    Una request para todos los bateadores calificados — cachea diariamente.

    Retorna {mlbam_player_id: {"xslg": float, "xba": float, "xwoba": float,
                                "slg": float, "pa": int, "name": str}}
    """
    import csv, io as _io
    yr    = season or date.today().year
    cache = _load_daily_cache()
    ck    = f"savant_batter_xslg_{yr}"
    if ck in cache:
        return cache[ck]

    url = "https://baseballsavant.mlb.com/leaderboard/expected_statistics"
    try:
        r = requests.get(url, params={
            "type": "batter", "year": yr, "min": 20, "csv": "true",
        }, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
        r.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  Savant batter xSLG error: {e}")
        return {}

    result: dict[int, dict] = {}
    reader = csv.DictReader(_io.StringIO(r.text.lstrip("﻿")))
    for row in reader:
        try:
            pid  = int(row.get("player_id", 0) or 0)
            pa   = int(row.get("pa", 0) or 0)
            if pid <= 0 or pa < 20:
                continue
            # Nombre: "Last, First" → "First Last"
            raw_name = row.get("last_name, first_name", "")
            parts = raw_name.split(", ", 1)
            name  = f"{parts[1]} {parts[0]}" if len(parts) == 2 else raw_name
            result[pid] = {
                "xba":   round(float(row.get("est_ba",  0) or 0), 3),
                "xslg":  round(float(row.get("est_slg", 0) or 0), 3),
                "xwoba": round(float(row.get("est_woba",0) or 0), 3),
                "slg":   round(float(row.get("slg",     0) or 0), 3),
                "pa":    pa,
                "name":  name,
            }
        except (ValueError, TypeError):
            continue

    cache[ck] = result
    _save_daily_cache(cache)
    print(f"  [SAVANT] xSLG bateadores: {len(result)} jugadores disponibles")
    return result


# ─── THE ODDS API: BATTER TOTAL BASES PROPS ───────────────────────────────────

def _match_tb_prop(props: dict, player_name: str) -> dict | None:
    """Empareja nombre de bateador (MLB Stats API) con clave de prop (Odds API)."""
    if not player_name or not props:
        return None
    if player_name in props:
        return props[player_name]
    last = player_name.split()[-1].lower()
    for prop_name, data in props.items():
        if prop_name.split()[-1].lower() == last:
            return data
    return None


def get_batter_tb_props(game_id: str) -> dict[str, dict]:
    """
    Cuotas de Total Bases por bateador desde The Odds API (mercado batter_total_bases).
    Una request por partido — cacheado diariamente.

    Retorna {player_name: {"line": float, "over_odds": int, "under_odds": int}}
    """
    cache = _load_daily_cache()
    ck    = f"tb_props_{game_id}"
    if ck in cache:
        return cache[ck]

    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{game_id}/odds"
    try:
        r = requests.get(url, params={
            "apiKey":     config.ODDS_API_KEY,
            "regions":    "us,us2",
            "markets":    "batter_total_bases",
            "oddsFormat": "american",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  TB props {game_id[:8]}: {e}")
        cache[ck] = {}
        _save_daily_cache(cache)
        return {}

    players: dict[str, dict] = {}
    for bk in data.get("bookmakers", []):
        for mkt in bk.get("markets", []):
            if mkt["key"] != "batter_total_bases":
                continue
            for outcome in mkt.get("outcomes", []):
                name = outcome.get("description", "")
                if not name:
                    continue
                if name not in players:
                    players[name] = {"line": outcome.get("point")}
                if outcome["name"].lower() == "over":
                    # Usar la mejor cuota disponible (primer libro en orden)
                    if "over_odds" not in players[name]:
                        players[name]["over_odds"] = outcome.get("price")
                else:
                    if "under_odds" not in players[name]:
                        players[name]["under_odds"] = outcome.get("price")
            break  # primer bookmaker es suficiente

    cache[ck] = players
    _save_daily_cache(cache)
    return players


def get_todays_mlb_games() -> list[dict]:
    """
    Combina odds (The Odds API) + pitchers probables (MLB Stats API) + stats.
    Retorna lista de dicts completos para el análisis.
    Solo incluye partidos con pitcher confirmado en al menos un equipo.
    """
    odds_list = get_mlb_odds()
    if not odds_list:
        return []

    # Odds F5 (primeros 5 innings) — puede retornar {} si no hay mercado disponible
    f5_odds_map = get_mlb_f5_odds()
    if f5_odds_map:
        print(f"  [F5] Odds F5 disponibles para {len(f5_odds_map)} partido(s)")

    # Schedule con pitchers — HOY y MAÑANA combinados. De noche, get_mlb_odds()
    # ya devuelve los juegos de mañana (los de hoy ya empezaron y se filtran);
    # sin el schedule de mañana esos juegos no encuentran su pitcher → se caían.
    # Clave por (fecha, par de equipos): en una serie el mismo matchup se repite
    # con pitchers distintos, así que la fecha desambigua.
    today    = date.today()
    tomorrow = today + timedelta(days=1)
    all_schedule = (get_mlb_schedule(today) or []) + (get_mlb_schedule(tomorrow) or [])

    def _sched_key(a: str, h: str) -> str:
        return f"{_normalize(a)}|{_normalize(h)}"

    def _iso_date(iso: str) -> str:
        try:
            return datetime.fromisoformat((iso or "").replace("Z", "+00:00")).date().isoformat()
        except (ValueError, TypeError):
            return ""

    # Lookup con fecha y, como respaldo, sin fecha (último gana si hay colisión)
    schedule_lookup: dict[str, dict] = {}
    schedule_by_date: dict[str, dict] = {}
    for s in all_schedule:
        k = _sched_key(s["away_team"], s["home_team"])
        schedule_lookup[k] = s
        d = _iso_date(s.get("commence_iso", ""))
        if d:
            schedule_by_date[f"{d}|{k}"] = s

    # Stats de equipo (ofensivas y pitcheo) — cargadas una vez
    team_offense  = get_all_team_offense_stats()
    team_pitching = get_all_team_pitching_stats()

    # Handedness de todos los pitchers del día — UN batch call antes del loop de partidos
    all_pitcher_ids = [
        pid
        for s in all_schedule
        for pid in (s.get("away_pitcher_id"), s.get("home_pitcher_id"))
        if pid
    ]
    pitcher_hands = get_pitcher_hands(all_pitcher_ids)
    n_hands = sum(1 for v in pitcher_hands.values() if v is not None)
    print(f"  [PLATOON] Handedness pitchers: {n_hands}/{len(pitcher_hands)} disponibles")

    # Savant xERA — UNA request bulk para todos los pitchers de la temporada
    savant_xera_map = get_savant_xera()
    if savant_xera_map:
        print(f"  [SAVANT] xERA disponible para {len(savant_xera_map)} pitchers")

    # Savant whiff% — UNA request bulk (corrector del K/9 en K-props)
    savant_whiff_map = get_savant_whiff()
    if savant_whiff_map:
        print(f"  [SAVANT] whiff% disponible para {len(savant_whiff_map)} pitchers")

    # Savant contacto — barrel% y hard_hit% permitidos (señal de calidad de contacto)
    savant_contact_map = get_savant_pitcher_contact()
    if savant_contact_map:
        print(f"  [SAVANT] Contacto (barrel/HH%) disponible para {len(savant_contact_map)} pitchers")

    # Savant xwOBA ofensivo — esperada wOBA de cada equipo (elimina suerte en BABIP)
    savant_team_xwoba_map = get_savant_team_batting_xwoba()
    if savant_team_xwoba_map:
        print(f"  [SAVANT] xwOBA ofensivo disponible para {len(savant_team_xwoba_map)} equipos")

    # Tendencias de árbitros — cargadas una vez para todos los partidos del día
    ump_tendencies = get_umpire_tendencies()

    # Deduplicar odds por par de equipos (el Odds API a veces retorna el mismo partido
    # dos veces con game_id diferentes — tomamos la primera aparición).
    seen_matchups: set[str] = set()
    deduped_odds: list[dict] = []
    for o in odds_list:
        key = f"{_normalize(o['away_team'])}|{_normalize(o['home_team'])}"
        if key not in seen_matchups:
            seen_matchups.add(key)
            deduped_odds.append(o)
    odds_list = deduped_odds

    result = []
    for odds in odds_list:
        home = odds["home_team"]
        away = odds["away_team"]

        # Match con schedule MLB — preferir match por fecha (desambigua series),
        # con fallback al match por par de equipos sin fecha.
        _od = _iso_date(odds.get("commence_iso", ""))
        sched = (schedule_by_date.get(f"{_od}|{_sched_key(away, home)}")
                 or schedule_by_date.get(f"{_od}|{_sched_key(home, away)}")
                 or schedule_lookup.get(_sched_key(away, home))
                 or schedule_lookup.get(_sched_key(home, away)))

        away_pitcher    = sched["away_pitcher"]    if sched else "TBD"
        home_pitcher    = sched["home_pitcher"]    if sched else "TBD"
        away_pitcher_id = sched["away_pitcher_id"] if sched else None
        home_pitcher_id = sched["home_pitcher_id"] if sched else None
        status          = sched["status"]           if sched else ""
        hp_umpire         = sched.get("hp_umpire")           if sched else None
        home_lineup_ids   = sched.get("home_lineup_ids",  []) if sched else []
        away_lineup_ids   = sched.get("away_lineup_ids",  []) if sched else []
        # Handedness real del pitcher (del batch call pre-loop, no del schedule que no lo trae)
        home_pitcher_hand = pitcher_hands.get(home_pitcher_id) if home_pitcher_id else None
        away_pitcher_hand = pitcher_hands.get(away_pitcher_id) if away_pitcher_id else None
        # lineup_pct_l se computa después de get_lineup_ops (que popula el cache de bat_side)

        # ── IL check: verificar que el pitcher probable está en el active roster ──
        # Usamos el active roster (26-man) en vez del injury list porque rosterType=injuries
        # devuelve todos los que han estado en IL en la temporada, incluyendo los ya recuperados.
        # Si el pitcher NO aparece en el active roster → no puede abrir hoy.
        home_active = set(get_team_active_roster(home)) if home_pitcher_id else set()
        away_active = set(get_team_active_roster(away)) if away_pitcher_id else set()
        home_pitcher_on_il = bool(home_pitcher_id and home_active and home_pitcher_id not in home_active)
        away_pitcher_on_il = bool(away_pitcher_id and away_active and away_pitcher_id not in away_active)
        # Nombre limpio para los mensajes (pitcher puede ser string o dict)
        _hp_name = home_pitcher.get("name", home_pitcher) if isinstance(home_pitcher, dict) else home_pitcher
        _ap_name = away_pitcher.get("name", away_pitcher) if isinstance(away_pitcher, dict) else away_pitcher
        if home_pitcher_on_il:
            print(f"  🚨 IL: {_hp_name} ({home}) no está en el active roster — {away}@{home} excluido del análisis")
        if away_pitcher_on_il:
            print(f"  🚨 IL: {_ap_name} ({away}) no está en el active roster — {away}@{home} excluido del análisis")

        # Ajuste de carreras por árbitro (0.0 si no hay datos)
        ump_run_adj = get_umpire_run_adj(hp_umpire, ump_tendencies)

        # Stats de temporada + forma reciente (L5 salidas) + splits home/away
        away_p_stats  = get_pitcher_stats(away_pitcher_id) if away_pitcher_id else None
        home_p_stats  = get_pitcher_stats(home_pitcher_id) if home_pitcher_id else None
        away_p_recent = get_pitcher_recent_fip(away_pitcher_id) if away_pitcher_id else None
        home_p_recent = get_pitcher_recent_fip(home_pitcher_id) if home_pitcher_id else None
        # Splits situacionales: home vs away xFIP (cacheados — 1 llamada extra por pitcher)
        away_p_splits = get_pitcher_home_away_splits(away_pitcher_id) if away_pitcher_id else None
        home_p_splits = get_pitcher_home_away_splits(home_pitcher_id) if home_pitcher_id else None

        # FIP blended: 60% temporada + 40% últimas 5 salidas
        def _blend_fip(season_stats: dict | None, recent: dict | None) -> float | None:
            if not season_stats:
                return None
            fip_s = season_stats.get("fip")
            if not fip_s:
                return None
            if recent and recent.get("fip_recent") is not None:
                return round(0.60 * fip_s + 0.40 * recent["fip_recent"], 2)
            return fip_s

        # xFIP blended: mismo peso 60/40 pero usando xFIP (más estable que FIP)
        def _blend_xfip(season_stats: dict | None, recent: dict | None) -> float | None:
            if not season_stats:
                return None
            xfip_s = season_stats.get("xfip")
            if not xfip_s:
                return None
            if recent and recent.get("xfip_recent") is not None:
                return round(0.60 * xfip_s + 0.40 * recent["xfip_recent"], 2)
            return xfip_s

        away_fip_blended  = _blend_fip(away_p_stats, away_p_recent)
        home_fip_blended  = _blend_fip(home_p_stats, home_p_recent)
        away_xfip_blended = _blend_xfip(away_p_stats, away_p_recent)
        home_xfip_blended = _blend_xfip(home_p_stats, home_p_recent)

        # Stats ofensivas via fuzzy match (team_offense ya está cargado)
        away_off_key = _fuzzy_match(away, team_offense)
        home_off_key = _fuzzy_match(home, team_offense)
        away_off = team_offense.get(away_off_key) if away_off_key else None
        home_off = team_offense.get(home_off_key) if home_off_key else None

        # Stats de pitcheo del equipo (bullpen proxy)
        away_pit_key = _fuzzy_match(away, team_pitching)
        home_pit_key = _fuzzy_match(home, team_pitching)
        away_pit = team_pitching.get(away_pit_key) if away_pit_key else None
        home_pit = team_pitching.get(home_pit_key) if home_pit_key else None

        # Fatiga del bullpen: pitcheo reciente (últimos 4 días) vs temporada
        # Si recent ERA >> season ERA → bullpen sobreexigido → más carreras esperadas
        away_recent_pit = get_team_recent_pitching(away)
        home_recent_pit = get_team_recent_pitching(home)

        away_season_era = (away_pit or {}).get("era")
        home_season_era = (home_pit or {}).get("era")
        away_recent_era = (away_recent_pit or {}).get("era_recent")
        home_recent_era = (home_recent_pit or {}).get("era_recent")

        # Blend 60% season + 40% últimos 4 días — mismo patrón que FIP blend
        # Usado solo para DISPLAY y flags de fatiga — el modelo usa ERA de temporada en _effective_fip.
        # Usar is not None (no and) para no descartar ERA=0.0 (improbable pero posible en muestras pequeñas)
        if away_recent_era is not None and away_season_era is not None:
            away_era_blended = round(0.60 * away_season_era + 0.40 * away_recent_era, 2)
        else:
            away_era_blended = away_season_era
        if home_recent_era is not None and home_season_era is not None:
            home_era_blended = round(0.60 * home_season_era + 0.40 * home_recent_era, 2)
        else:
            home_era_blended = home_season_era

        # Flag de fatiga: bullpen fatigado si recent ERA > season ERA + 0.80
        _FATIGUE_THRESHOLD = 0.80
        away_bullpen_fatigued = bool(
            away_recent_era is not None and away_season_era is not None and
            (away_recent_era - away_season_era) >= _FATIGUE_THRESHOLD
        )
        home_bullpen_fatigued = bool(
            home_recent_era is not None and home_season_era is not None and
            (home_recent_era - home_season_era) >= _FATIGUE_THRESHOLD
        )
        # Bullpen descansado: recent ERA < season ERA - 0.80
        away_bullpen_rested = bool(
            away_recent_era is not None and away_season_era is not None and
            (away_season_era - away_recent_era) >= _FATIGUE_THRESHOLD
        )
        home_bullpen_rested = bool(
            home_recent_era is not None and home_season_era is not None and
            (home_season_era - home_recent_era) >= _FATIGUE_THRESHOLD
        )

        # Enriquecer el dict del pitcher con forma reciente + splits home/away
        def _enrich(
            stats: dict | None,
            recent: dict | None,
            fip_blended: float | None,
            xfip_blended: float | None,
            splits: dict | None = None,
        ) -> dict | None:
            if not stats:
                return None
            d = dict(stats)
            if recent:
                d.update(recent)
            if fip_blended is not None:
                d["fip_blended"] = fip_blended
            if xfip_blended is not None:
                d["xfip_blended"] = xfip_blended
            # Splits home/away: xfip_home, xfip_away, split_diff, ip_home, ip_away
            if splits:
                d["xfip_home"]  = splits.get("xfip_home")
                d["xfip_away"]  = splits.get("xfip_away")
                d["fip_home"]   = splits.get("fip_home")
                d["fip_away"]   = splits.get("fip_away")
                d["ip_home"]    = splits.get("ip_home")
                d["ip_away"]    = splits.get("ip_away")
                d["split_diff"] = splits.get("split_diff")   # positivo = más débil away
            return d

        # ── Savant: xERA + arsenal (velo) + contacto (barrel%, hard_hit%) ──────
        away_arsenal  = get_pitcher_arsenal(away_pitcher_id) if away_pitcher_id else {}
        home_arsenal  = get_pitcher_arsenal(home_pitcher_id) if home_pitcher_id else {}
        away_savant   = savant_xera_map.get(away_pitcher_id, {}) if away_pitcher_id else {}
        home_savant   = savant_xera_map.get(home_pitcher_id, {}) if home_pitcher_id else {}
        away_contact  = savant_contact_map.get(away_pitcher_id, {}) if away_pitcher_id else {}
        home_contact  = savant_contact_map.get(home_pitcher_id, {}) if home_pitcher_id else {}

        # ── H2H: historial pitcher vs equipo oponente ────────────────────────
        # El pitcher visitante lanza vs el equipo local (home batting team)
        # El pitcher local lanza vs el equipo visitante (away batting team)
        home_team_id = TEAM_ID_MAP.get(home)
        away_team_id = TEAM_ID_MAP.get(away)

        # away_pitcher lanza vs home_batting_team (home)
        # → OPS del equipo LOCAL vs pitcher visitante (historial carrera)
        away_h2h = get_pitcher_vs_team_ops(away_pitcher_id, home_team_id) if (away_pitcher_id and home_team_id) else None
        # home_pitcher lanza vs away_batting_team (away)
        # → OPS del equipo VISITANTE vs pitcher local (historial carrera)
        home_h2h = get_pitcher_vs_team_ops(home_pitcher_id, away_team_id) if (home_pitcher_id and away_team_id) else None

        # Calcular ajuste de carreras por H2H
        # Comparamos: ops_vs (OPS del equipo vs ESTE pitcher) vs team_season_ops (OPS vs todos)
        # ops_diff > 0 → equipo históricamente mejor vs este pitcher → más carreras esperadas
        def _h2h_run_adj(
            h2h: dict | None,
            team_season_ops: float | None,
        ) -> tuple[float, str]:
            """
            Ajuste de carreras por historial H2H.
            Compara OPS del equipo vs este pitcher específico con su OPS de temporada.
            Retorna (run_adj, description).
            run_adj > 0 = equipo bateador históricamente mejor vs este pitcher.
            """
            if not h2h or not team_season_ops:
                return 0.0, ""
            ops_vs   = h2h["ops_vs"]
            pa_vs    = h2h["pa_vs"]
            ops_diff = ops_vs - team_season_ops   # positivo = equipo hace más daño vs este pitcher
            # Convertir OPS diff a carreras: 1 OPS unit ≈ 4.0 runs/partido (conservador)
            run_adj  = ops_diff * _H2H_ADJ_SCALE
            run_adj  = max(-_H2H_ADJ_CAP, min(_H2H_ADJ_CAP, run_adj))
            if abs(run_adj) < 0.05:   # ignorar diferencias pequeñas (< 0.012 OPS diff)
                return 0.0, ""
            diff_sign = "+" if ops_diff > 0 else ""
            # run_adj < 0: equipo scores LESS → pitcher lo domina
            # run_adj > 0: equipo scores MORE → le pega bien al pitcher
            if run_adj < 0:
                matchup = "pitcher domina a este equipo"
            else:
                matchup = "este equipo le pega bien al pitcher"
            desc = (f"H2H: equipo OPS {ops_vs:.3f} vs pitcher ({diff_sign}{ops_diff:.3f} vs temporada, "
                    f"{pa_vs} PA) - {matchup} ({run_adj:+.2f} carr.)")
            return round(run_adj, 2), desc

        away_season_ops_for_h2h = (away_off or {}).get("ops") or LEAGUE_AVG_OPS
        home_season_ops_for_h2h = (home_off or {}).get("ops") or LEAGUE_AVG_OPS

        # away_h2h: OPS del equipo LOCAL vs pitcher visitante → ajusta exp_home
        home_h2h_adj, home_h2h_desc = _h2h_run_adj(away_h2h, home_season_ops_for_h2h)
        # home_h2h: OPS del equipo VISITANTE vs pitcher local → ajusta exp_away
        away_h2h_adj, away_h2h_desc = _h2h_run_adj(home_h2h, away_season_ops_for_h2h)

        away_p_dict = _add_savant(
            _pitcher_dict(away_pitcher, _enrich(away_p_stats, away_p_recent, away_fip_blended, away_xfip_blended, away_p_splits)),
            away_arsenal, away_savant, away_contact,
        )
        home_p_dict = _add_savant(
            _pitcher_dict(home_pitcher, _enrich(home_p_stats, home_p_recent, home_fip_blended, home_xfip_blended, home_p_splits)),
            home_arsenal, home_savant, home_contact,
        )

        # Señales del nicho K-props: whiff% (Savant bulk) y splits K% L/R (gratis)
        if away_p_dict is not None and away_pitcher_id:
            away_p_dict["whiff_pct"] = savant_whiff_map.get(away_pitcher_id)
            _ks = get_pitcher_k_splits(away_pitcher_id)
            if _ks:
                away_p_dict.update(_ks)
        if home_p_dict is not None and home_pitcher_id:
            home_p_dict["whiff_pct"] = savant_whiff_map.get(home_pitcher_id)
            _ks = get_pitcher_k_splits(home_pitcher_id)
            if _ks:
                home_p_dict.update(_ks)

        # Forma reciente: OPS blend (70% temporada + 30% últimos 28 días)
        away_season_ops  = (away_off or {}).get("ops") or LEAGUE_AVG_OPS
        home_season_ops  = (home_off or {}).get("ops") or LEAGUE_AVG_OPS
        away_recent_data = get_team_recent_ops(away)
        home_recent_data = get_team_recent_ops(home)
        away_recent_ops  = (away_recent_data or {}).get("ops_recent")
        home_recent_ops  = (home_recent_data or {}).get("ops_recent")
        away_rg_recent   = (away_recent_data or {}).get("rg_recent")   # R/G real últimos 28d
        home_rg_recent   = (home_recent_data or {}).get("rg_recent")
        if away_recent_ops:
            away_ops_blended = round(0.70 * away_season_ops + 0.30 * away_recent_ops, 3)
            away_has_recent  = True
        else:
            away_ops_blended = away_season_ops
            away_has_recent  = False
        if home_recent_ops:
            home_ops_blended = round(0.70 * home_season_ops + 0.30 * home_recent_ops, 3)
            home_has_recent  = True
        else:
            home_ops_blended = home_season_ops
            home_has_recent  = False

        # ── Lineup del día (reemplaza team OPS cuando está disponible) ──────────
        # Disponible ~2h antes del juego. Si no está, usa el OPS blended del equipo.
        # Solo se activa si ≥ 7 de los 9 bateadores tienen ≥ 30 PA esta temporada.
        (home_ops_final,
         home_lineup_used,
         home_lineup_names,
         home_lineup_weak) = get_lineup_ops(home_lineup_ids, home_ops_blended)
        (away_ops_final,
         away_lineup_used,
         away_lineup_names,
         away_lineup_weak) = get_lineup_ops(away_lineup_ids, away_ops_blended)

        if home_lineup_used:
            weak_note = f"  ⚠️ {home_lineup_weak} hueco(s)" if home_lineup_weak >= 2 else ""
            print(f"    [LINEUP] {home}: OPS={home_ops_final:.3f} "
                  f"(vs blended {home_ops_blended:.3f}){weak_note}")
        if away_lineup_used:
            weak_note = f"  ⚠️ {away_lineup_weak} hueco(s)" if away_lineup_weak >= 2 else ""
            print(f"    [LINEUP] {away}: OPS={away_ops_final:.3f} "
                  f"(vs blended {away_ops_blended:.3f}){weak_note}")

        # Fracción zurda del lineup — lee del cache de bat_side populado por get_lineup_ops.
        # Si el lineup no está disponible aún, hace un batch call mínimo para obtener batSide.
        home_lineup_pct_l = get_lineup_bat_pct_l(home_lineup_ids) if home_lineup_ids else None
        away_lineup_pct_l = get_lineup_bat_pct_l(away_lineup_ids) if away_lineup_ids else None

        # K-rate del lineup confirmado (nicho K-props): el lineup real difiere
        # del promedio de equipo — señal que la casa incorpora tarde.
        _home_team_k = (home_off or {}).get("k_pct") or 0.226
        _away_team_k = (away_off or {}).get("k_pct") or 0.226
        # Lineup enfrenta al pitcher CONTRARIO — usar su mano para el split platoon
        home_lineup_k_pct, home_lineup_k_used, home_lineup_high_k = get_lineup_k_pct(
            home_lineup_ids, _home_team_k, pitcher_hand=away_pitcher_hand)
        away_lineup_k_pct, away_lineup_k_used, away_lineup_high_k = get_lineup_k_pct(
            away_lineup_ids, _away_team_k, pitcher_hand=home_pitcher_hand)
        if home_lineup_k_used:
            _hand_tag = f" vs {away_pitcher_hand}HP platoon" if away_pitcher_hand else ""
            print(f"    [LINEUP-K] {home}: K%={home_lineup_k_pct:.1%} (equipo {_home_team_k:.1%})"
                  f"  high-K bats: {home_lineup_high_k}{_hand_tag}")
        if away_lineup_k_used:
            _hand_tag = f" vs {home_pitcher_hand}HP platoon" if home_pitcher_hand else ""
            print(f"    [LINEUP-K] {away}: K%={away_lineup_k_pct:.1%} (equipo {_away_team_k:.1%})"
                  f"  high-K bats: {away_lineup_high_k}{_hand_tag}")

        # Formatear hora local legible
        commence_iso = odds.get("commence_iso", "")
        try:
            utc_dt   = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
            local_dt = utc_dt.astimezone()
            game_time_str = local_dt.strftime("%I:%M %p").lstrip("0")
            game_date_str = utc_dt.date().isoformat()
        except Exception:
            game_time_str = ""
            game_date_str = date.today().isoformat()

        # ── Movimiento de línea ──────────────────────────────────────────────
        # Guarda la línea de apertura la primera vez que vemos el partido;
        # en llamadas posteriores devuelve el movimiento acumulado.
        current_total  = odds.get("total_line")
        _line_mov_data = {}
        if current_total is not None:
            _line_mov_data = line_tracker.record_and_get_movement(
                game_date_str    = game_date_str,
                away_team        = away,
                home_team        = home,
                current_line     = current_total,
                current_over_odds  = odds.get("over_odds"),
                current_under_odds = odds.get("under_odds"),
            )

        # F5 odds para este partido
        f5_key  = f"{_normalize(away)}|{_normalize(home)}"
        f5_data = f5_odds_map.get(f5_key, {})

        # Savant xwOBA ofensivo (keyed por abreviatura estándar MLB)
        away_abbr_key = TEAM_MAP.get(away, away[:3].upper())
        home_abbr_key = TEAM_MAP.get(home, home[:3].upper())
        away_xwoba_data = savant_team_xwoba_map.get(away_abbr_key, {})
        home_xwoba_data = savant_team_xwoba_map.get(home_abbr_key, {})

        result.append({
            **odds,
            "game_time":       game_time_str,
            # Pitchers como dicts con 'name' + stats (FIP, IP, ERA…)
            "away_pitcher":    away_p_dict,
            "home_pitcher":    home_p_dict,
            "away_pitcher_id": away_pitcher_id,
            "home_pitcher_id": home_pitcher_id,
            # Stats ofensivas del equipo atacante
            "away_offense":    away_off,
            "home_offense":    home_off,
            # OPS final para el modelo:
            #   Si lineup confirmado (≥7 de 9 con ≥30 PA) → OPS ponderado del lineup
            #   Si no → OPS blended (70% temporada + 30% últimos 28 días)
            "away_ops":        away_ops_final,
            "home_ops":        home_ops_final,
            # Desglose para display y debug
            "away_ops_season": (away_off or {}).get("ops"),
            "home_ops_season": (home_off or {}).get("ops"),
            "away_ops_recent": away_recent_ops,
            "home_ops_recent": home_recent_ops,
            "away_ops_blended":away_ops_blended,   # blend temporada+reciente (antes del lineup)
            "home_ops_blended":home_ops_blended,
            "away_has_recent": away_has_recent,
            "home_has_recent": home_has_recent,
            # Lineup del día
            "home_lineup_ids":   home_lineup_ids,
            "away_lineup_ids":   away_lineup_ids,
            "home_lineup_used":  home_lineup_used,
            "away_lineup_used":  away_lineup_used,
            "home_lineup_names": home_lineup_names,  # top 5 titulares para display
            "away_lineup_names": away_lineup_names,
            "away_runs_pg":    (away_off or {}).get("runs_per_game"),   # temporada
            "home_runs_pg":    (home_off or {}).get("runs_per_game"),   # temporada
            "away_rg_recent":  away_rg_recent,   # R/G reales últimos 28 días (más directo que OPS)
            "home_rg_recent":  home_rg_recent,
            "park_factor":     get_park_factor(home),
            "weather":         (_weather := get_weather_for_game(home, commence_iso)),
            # Riesgo de lluvia a la hora del juego (del forecast 3h) — gate del nicho
            "rain_pop":        (_weather or {}).get("pop"),
            "status":          status,
            # H2H: ajuste por historial pitcher vs equipo oponente
            # home_h2h_adj: ajuste para las carreras del equipo LOCAL (batea vs pitcher visitante)
            # away_h2h_adj: ajuste para las carreras del equipo VISITANTE (batea vs pitcher local)
            "home_h2h_adj":    home_h2h_adj,    # positivo = local espera más carreras
            "away_h2h_adj":    away_h2h_adj,    # positivo = visitante espera más carreras
            "home_h2h_desc":   home_h2h_desc,
            "away_h2h_desc":   away_h2h_desc,
            # Árbitro del home plate y su ajuste de carreras
            "hp_umpire":       hp_umpire,
            "ump_run_adj":     ump_run_adj,
            # ERA del equipo completo (proxy del bullpen)
            # Se usa la ERA blended (60% season + 40% últimos 4 días) para capturar fatiga
            "away_team_era":          away_era_blended,
            "home_team_era":          home_era_blended,
            "away_team_era_season":   away_season_era,      # ERA temporada (referencia)
            "home_team_era_season":   home_season_era,
            "away_team_era_recent":   away_recent_era,      # ERA últimos 4 días
            "home_team_era_recent":   home_recent_era,
            "away_bullpen_fatigued":  away_bullpen_fatigued,  # ERA reciente >> temporada
            "home_bullpen_fatigued":  home_bullpen_fatigued,
            "away_bullpen_rested":    away_bullpen_rested,    # ERA reciente << temporada
            "home_bullpen_rested":    home_bullpen_rested,
            "away_team_fip":          (away_pit or {}).get("team_fip"),
            "home_team_fip":          (home_pit or {}).get("team_fip"),
            # Días de descanso del pitcher desde su último inicio
            # None si no hay historial de salidas; 0 si lanzó ayer (posible abridor en relevo)
            "away_pitcher_days_rest": (away_p_recent or {}).get("days_rest"),
            "home_pitcher_days_rest": (home_p_recent or {}).get("days_rest"),
            # Movimiento de línea — apertura vs actual
            # opening_line:    línea cuando The Odds API publicó el partido por primera vez
            # line_movement:   current_line - opening_line (>0 = subió, <0 = bajó)
            # movement_signal: "neutral" | "steam_over" | "strong_over" | "steam_under" | "strong_under"
            "opening_line":     _line_mov_data.get("opening_line"),
            "line_movement":    _line_mov_data.get("line_movement", 0.0),
            "movement_signal":  _line_mov_data.get("movement_signal", "neutral"),
            "line_first_seen":  _line_mov_data.get("first_seen"),
            # Abreviaturas para display
            "away_abbr":       TEAM_MAP.get(away, away[:3].upper()),
            "home_abbr":       TEAM_MAP.get(home, home[:3].upper()),
            # Savant xwOBA ofensivo del equipo (expected wOBA — elimina suerte en BABIP)
            # xwOBA > wOBA → equipo ha sido "unlucky" → esperar mejora futura
            # xwOBA < wOBA → equipo ha tenido suerte   → esperar regresión
            "away_xwoba":      away_xwoba_data.get("xwoba"),
            "home_xwoba":      home_xwoba_data.get("xwoba"),
            "away_woba":       away_xwoba_data.get("woba"),
            "home_woba":       home_xwoba_data.get("woba"),
            # K rate del equipo (K/AB) — señal de cuánto poncha el lineup rival
            "away_k_pct":      (away_off or {}).get("k_pct"),
            "home_k_pct":      (home_off or {}).get("k_pct"),
            # K rate del LINEUP CONFIRMADO (nicho K-props) — más preciso que el de equipo
            "home_lineup_k_pct":  home_lineup_k_pct,
            "away_lineup_k_pct":  away_lineup_k_pct,
            "home_lineup_k_used": home_lineup_k_used,
            "away_lineup_k_used": away_lineup_k_used,
            # Composición: bateadores high-K (K/AB ≥ 28%) en el lineup confirmado
            "home_lineup_high_k": home_lineup_high_k,
            "away_lineup_high_k": away_lineup_high_k,
            # Bateadores "huecos" (OPS < 0.650) en el lineup — filtra OVERs con lineup inflado
            "home_lineup_weak":   home_lineup_weak,
            "away_lineup_weak":   away_lineup_weak,
            # Props de strikeouts — poblado después del loop por get_pitcher_strikeout_props()
            "away_k_prop":     None,
            "home_k_prop":     None,
            # Platoon splits (handedness)
            "home_pitcher_hand":  home_pitcher_hand,   # "L" o "R" del pitcher local
            "away_pitcher_hand":  away_pitcher_hand,   # "L" o "R" del pitcher visitante
            "home_lineup_pct_l":  home_lineup_pct_l,  # fracción zurda del lineup local (None si no hay lineup)
            "away_lineup_pct_l":  away_lineup_pct_l,  # fracción zurda del lineup visitante
            # F5 (primeros 5 innings) — None si no hay mercado disponible
            "f5_line":         f5_data.get("f5_line"),
            "f5_over_odds":    f5_data.get("f5_over_odds"),
            "f5_under_odds":   f5_data.get("f5_under_odds"),
            "f5_impl_over":    f5_data.get("f5_impl_over"),
            "f5_impl_under":   f5_data.get("f5_impl_under"),
            # IL status del pitcher probable — si True el partido se excluye del análisis
            "home_pitcher_on_il": home_pitcher_on_il,
            "away_pitcher_on_il": away_pitcher_on_il,
        })

    # ── Savant xSLG bateadores (una request para todos, cache diaria) ────────────
    savant_xslg_map = get_savant_batter_xslg()

    # ── Batter Total Bases props + lineup stats (un request por partido) ─────────
    for g in result:
        gid          = g.get("game_id", "")
        home_ids     = g.get("home_lineup_ids") or []
        away_ids     = g.get("away_lineup_ids") or []
        all_ids      = list(set(home_ids + away_ids))

        if not gid:
            g["home_tb_lineup"] = []
            g["away_tb_lineup"] = []
            continue

        # Fallback: si no hay lineup confirmado, usar roster activo del equipo
        if not all_ids:
            home_ids = get_team_active_roster(g.get("home_team", ""))
            away_ids = get_team_active_roster(g.get("away_team", ""))
            all_ids  = list(set(home_ids + away_ids))

        if not all_ids:
            g["home_tb_lineup"] = []
            g["away_tb_lineup"] = []
            continue

        # Fetch stats de los jugadores del lineup (cacheado diariamente)
        player_stats = get_batch_player_hitting_stats(all_ids)

        # Fetch TB props para este partido
        tb_props = get_batter_tb_props(gid)

        def _build_tb_entry(pid: int) -> dict | None:
            stats  = player_stats.get(pid)
            if not stats or stats.get("pa", 0) < 20:
                return None
            name   = stats.get("name", "")
            prop   = _match_tb_prop(tb_props, name)
            xdata  = savant_xslg_map.get(pid, {})
            # Momentum: TB promedio en últimas 7 salidas — solo para jugadores
            # con prop disponible (evita N llamadas innecesarias a la API).
            recent_tb = get_batter_recent_tb(pid) if prop else None
            return {
                "player_id":    pid,
                "name":         name,
                "slg":          stats.get("slg", 0.400),
                "xslg":         xdata.get("xslg") or stats.get("slg", 0.400),
                "xba":          xdata.get("xba"),
                "pa":           stats.get("pa", 0),
                "ab_per_game":  stats.get("ab_per_game", 3.66),
                "prop":         prop,
                "tb_avg_recent": recent_tb.get("tb_avg_recent") if recent_tb else None,
                "tb_n_games":    recent_tb.get("n_games")       if recent_tb else None,
            }

        g["home_tb_lineup"] = [e for pid in home_ids if (e := _build_tb_entry(pid))]
        g["away_tb_lineup"] = [e for pid in away_ids if (e := _build_tb_entry(pid))]

    n_tb = sum(1 for g in result if any(p.get("prop") for p in (g.get("home_tb_lineup") or [])))
    if n_tb:
        print(f"  [PROPS] TB props disponibles para {n_tb} partido(s) (home lineup)")

    # ── Pitcher strikeout props + team totals (mismo request, cache diaria) ─────
    game_ids = [g["game_id"] for g in result if g.get("game_id")]
    if game_ids:
        k_props_map     = get_pitcher_strikeout_props(game_ids)
        team_totals_map = get_team_total_odds(game_ids)
        n_with_props = sum(1 for v in k_props_map.values() if v)
        n_with_tt    = sum(1 for v in team_totals_map.values() if v)
        if n_with_props:
            print(f"  [PROPS] K-props disponibles para {n_with_props} partido(s)")
        if n_with_tt:
            print(f"  [PROPS] Team totals disponibles para {n_with_tt} partido(s)")
        for g in result:
            gid   = g.get("game_id", "")
            props = k_props_map.get(gid, {})
            g["away_k_prop"] = _match_k_prop(props, (g.get("away_pitcher") or {}).get("name", ""))
            g["home_k_prop"] = _match_k_prop(props, (g.get("home_pitcher") or {}).get("name", ""))
            tt = team_totals_map.get(gid, {})
            g["home_team_total"] = tt.get(g.get("home_team", ""))
            g["away_team_total"] = tt.get(g.get("away_team", ""))

    return result


# Alias para uso en picks_mlb.py
def get_pitcher_stats_by_id(pitcher_id: int) -> dict | None:
    return get_pitcher_stats(pitcher_id)


# ─── LIVE GAME DATA ───────────────────────────────────────────────────────────

def get_live_game_state(game_pk: int) -> dict | None:
    """
    Score e inning actual de un partido en curso.
    Retorna None si el partido no ha comenzado o la API no responde.
    Usa MLB Stats API (gratuita, sin quota).
    """
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/linescore"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    inning = data.get("currentInning") or 0
    if not inning:
        return None

    half      = (data.get("inningHalf") or "top").lower()  # "top" | "bottom"
    outs      = int(data.get("outs") or 0)
    teams     = data.get("teams", {})
    runs_home = int(teams.get("home", {}).get("runs") or 0)
    runs_away = int(teams.get("away", {}).get("runs") or 0)

    return {
        "inning":      inning,
        "inning_half": half,
        "outs":        outs,
        "runs_home":   runs_home,
        "runs_away":   runs_away,
    }


def get_live_current_pitchers(game_pk: int) -> dict:
    """
    Compara el pitcher actual con el abridor para detectar cambios tempranos.

    Returns dict con claves:
      {home|away}_starter_id, {home|away}_current_id,
      {home|away}_starter_changed (bool), {home|away}_starter_ip (float)
    """
    url = f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore"
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return {}

    teams  = data.get("teams", {})
    result: dict = {}
    for side in ("home", "away"):
        team     = teams.get(side, {})
        pitchers = team.get("pitchers", [])
        players  = team.get("players", {})

        starter_id = pitchers[0] if pitchers else None
        current_id = pitchers[-1] if pitchers else None

        starter_ip = 0.0
        if starter_id:
            p_data    = players.get(f"ID{starter_id}", {})
            ip_str    = (p_data.get("stats", {})
                                .get("pitching", {})
                                .get("inningsPitched", "0"))
            starter_ip = _parse_ip(ip_str)

        result[f"{side}_starter_id"]      = starter_id
        result[f"{side}_current_id"]      = current_id
        result[f"{side}_starter_changed"] = (starter_id is not None
                                             and starter_id != current_id)
        result[f"{side}_starter_ip"]      = starter_ip

    return result
