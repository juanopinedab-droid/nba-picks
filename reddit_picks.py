"""
reddit_picks.py — Picks NBA de Reddit (r/sportsbook, r/nbabetting)
Usa la API JSON pública de Reddit — sin cuenta, sin key, gratis.

Uso:
    python reddit_picks.py              # picks de hoy
    python reddit_picks.py --ayer       # posts de ayer
    python reddit_picks.py --top 15     # más picks (default 10)
    python reddit_picks.py --raw        # ver posts crudos
"""

import re
import time
import argparse
import requests
from collections import defaultdict
from datetime import date, timedelta, timezone, datetime
import math

BOLD   = "\033[1m"
RESET  = "\033[0m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
GRAY   = "\033[90m"
YELLOW = "\033[93m"
RED    = "\033[91m"

HEADERS    = {"User-Agent": "python:nba-picks-reader:v1.0 (personal research)"}
SUBREDDITS = ["sportsbook", "nbabetting"]

# ─── LIMPIEZA DE TEXTO ───────────────────────────────────────────────────────

# Patrones de basura a eliminar
_TRASH = [
    re.compile(r"\^\([^)]*\)"),                          # ^(Call) ^(CO,) Reddit superscript
    re.compile(r"1-800-GAMBLER|gambling (help|hotline)", re.I),
    re.compile(r"https?://\S+"),                         # URLs
    re.compile(r"\[([^\]]+)\]\([^)]+\)"),               # [text](url) markdown links → text
    re.compile(r"[*_~`]{1,2}"),                          # markdown bold/italic
    re.compile(r"&amp;|&lt;|&gt;|&quot;"),               # HTML entities
]

def clean(text: str) -> str:
    text = _TRASH[3].sub(r"\1", text)   # primero los links, preservar texto
    for pat in _TRASH:
        text = pat.sub(" ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


# ─── ALIAS DE EQUIPOS ────────────────────────────────────────────────────────

TEAM_ALIASES: dict[str, str] = {
    "hawks": "Atlanta Hawks",           "atl": "Atlanta Hawks",
    "celtics": "Boston Celtics",        "celts": "Boston Celtics",        "bos": "Boston Celtics",
    "nets": "Brooklyn Nets",            "bkn": "Brooklyn Nets",
    "hornets": "Charlotte Hornets",     "cha": "Charlotte Hornets",
    "bulls": "Chicago Bulls",           "chi": "Chicago Bulls",
    "cavaliers": "Cleveland Cavaliers", "cavs": "Cleveland Cavaliers",    "cle": "Cleveland Cavaliers",
    "mavericks": "Dallas Mavericks",    "mavs": "Dallas Mavericks",       "dal": "Dallas Mavericks",
    "nuggets": "Denver Nuggets",        "nuggs": "Denver Nuggets",        "den": "Denver Nuggets",
    "pistons": "Detroit Pistons",       "det": "Detroit Pistons",
    "warriors": "Golden State Warriors","gsw": "Golden State Warriors",   "dubs": "Golden State Warriors", "gs": "Golden State Warriors",
    "rockets": "Houston Rockets",       "hou": "Houston Rockets",
    "pacers": "Indiana Pacers",         "ind": "Indiana Pacers",
    "clippers": "Los Angeles Clippers", "clips": "Los Angeles Clippers",  "lac": "Los Angeles Clippers",
    "lakers": "Los Angeles Lakers",     "lal": "Los Angeles Lakers",
    "grizzlies": "Memphis Grizzlies",   "grizz": "Memphis Grizzlies",     "mem": "Memphis Grizzlies",
    "heat": "Miami Heat",               "mia": "Miami Heat",
    "bucks": "Milwaukee Bucks",         "mil": "Milwaukee Bucks",
    "timberwolves": "Minnesota Timberwolves", "twolves": "Minnesota Timberwolves", "wolves": "Minnesota Timberwolves", "min": "Minnesota Timberwolves",
    "pelicans": "New Orleans Pelicans", "pels": "New Orleans Pelicans",   "nop": "New Orleans Pelicans",
    "knicks": "New York Knicks",        "nyk": "New York Knicks",
    "thunder": "Oklahoma City Thunder", "okc": "Oklahoma City Thunder",
    "magic": "Orlando Magic",           "orl": "Orlando Magic",
    "76ers": "Philadelphia 76ers",      "sixers": "Philadelphia 76ers",   "phi": "Philadelphia 76ers",
    "suns": "Phoenix Suns",             "phx": "Phoenix Suns",
    "blazers": "Portland Trail Blazers","por": "Portland Trail Blazers",
    "kings": "Sacramento Kings",        "sac": "Sacramento Kings",
    "spurs": "San Antonio Spurs",       "sas": "San Antonio Spurs",
    "raptors": "Toronto Raptors",       "raps": "Toronto Raptors",        "tor": "Toronto Raptors",
    "jazz": "Utah Jazz",                "uta": "Utah Jazz",
    "wizards": "Washington Wizards",    "wiz": "Washington Wizards",      "was": "Washington Wizards",
}

# ─── PATRONES DE PICKS ───────────────────────────────────────────────────────

# Props de jugadores: "LeBron James o25.5 pts" / "SGA (OKC) u22 points"
_PROP_RE = re.compile(
    r"([A-Z][a-z]+(?:[\s\-][A-Z][a-z'\-]+)+)"   # Nombre (2+ palabras capitalizadas)
    r"(?:\s*\([A-Z]{2,3}\))?"                     # Opcional: (ABR)
    r"\s+(o|u|over|under)\s*"                     # Dirección
    r"(\d+(?:\.\d+)?)"                            # Línea numérica
    r"(?:\s*(pts?|points?|reb(?:ounds?)?|ast(?:ists?)?|3s?|threes?|blk|stl))?",
    re.IGNORECASE,
)

# Totales de partido: "over 221" / "u215.5"
_TOTAL_RE = re.compile(
    r"\b(over|under|o|u)\s*(\d{3}(?:\.\d)?)\b",
    re.IGNORECASE,
)

# Spread: equipo + línea numérica con signo
_SPREAD_RE = re.compile(r"([+-]\d+(?:\.\d+)?)")

# Términos que validan que hay una apuesta cerca
_BET_CTX = re.compile(
    r"\b(ml|moneyline|money line|spread|cover|over|under|parlay|lock|play|fade|[+-]\d)\b",
    re.IGNORECASE,
)

_STAT_LABELS = {
    "pt": "PTS", "pts": "PTS", "point": "PTS", "points": "PTS",
    "reb": "REB", "rebound": "REB", "rebounds": "REB",
    "ast": "AST", "assist": "AST", "assists": "AST",
    "3": "3PM", "3s": "3PM", "three": "3PM", "threes": "3PM",
    "blk": "BLK", "stl": "STL",
}


def _normalize_stat(raw: str | None) -> str:
    if not raw:
        return "PTS"
    return _STAT_LABELS.get(raw.lower().rstrip("s"), raw.upper())


def _find_teams(text: str) -> list[str]:
    found, lower = [], text.lower()
    for alias, canonical in sorted(TEAM_ALIASES.items(), key=lambda x: -len(x[0])):
        if re.search(rf"\b{re.escape(alias)}\b", lower) and canonical not in found:
            found.append(canonical)
    return found


# ─── PARSERS ─────────────────────────────────────────────────────────────────

def parse_team_picks(text: str, upvotes: int) -> list[dict]:
    picks = []
    for sentence in re.split(r"[\n.!?;|]", text):
        s = clean(sentence)
        if not s or not _BET_CTX.search(s):
            continue

        teams = _find_teams(s)
        if not teams:
            continue

        # Saltar si parece pick de jugador (nombre propio + o/u)
        if _PROP_RE.search(s):
            continue

        # Total de partido
        total = _TOTAL_RE.search(s)
        if total:
            direction = "OVER" if total.group(1).lower() in ("over", "o") else "UNDER"
            line = total.group(2)
            for team in teams:
                picks.append({"team": team, "direction": f"{direction} {line}",
                              "upvotes": upvotes, "snippet": s[:90]})
            continue

        # Spread
        spread = _SPREAD_RE.search(s)
        direction = f"SPREAD {spread.group(1)}" if spread else "ML"

        # ML explícito
        if re.search(r"\b(ml|moneyline|money line)\b", s, re.I):
            direction = "ML"
        if re.search(r"\b(fade|against)\b", s, re.I):
            direction = "FADE"

        for team in teams:
            picks.append({"team": team, "direction": direction,
                          "upvotes": upvotes, "snippet": s[:90]})
    return picks


def parse_player_props(text: str, upvotes: int) -> list[dict]:
    props = []
    cleaned = clean(text)
    for m in _PROP_RE.finditer(cleaned):
        name      = m.group(1).strip()
        direction = "OVER" if m.group(2).lower() in ("o", "over") else "UNDER"
        line      = m.group(3)
        stat      = _normalize_stat(m.group(4))

        # Filtrar nombres genéricos que no son jugadores
        if len(name.split()) < 2 or name.lower() in TEAM_ALIASES:
            continue
        # Filtrar números confundidos como nombres
        if re.search(r"\d", name):
            continue

        props.append({
            "player":    name,
            "direction": direction,
            "line":      line,
            "stat":      stat,
            "upvotes":   upvotes,
            "snippet":   cleaned[max(0, m.start()-20):m.end()+30].strip()[:90],
        })
    return props


# ─── REDDIT API ──────────────────────────────────────────────────────────────

def _get(url: str, params: dict | None = None) -> dict | None:
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  ⚠️  {e}")
        return None


def fetch_posts(subreddit: str, dia: date) -> list[dict]:
    found: dict[str, dict] = {}
    for q in ["NBA picks", "NBA tonight", "NBA"]:
        data = _get(f"https://www.reddit.com/r/{subreddit}/search.json",
                    params={"q": q, "sort": "new", "t": "day",
                            "limit": 100, "restrict_sr": 1})
        if not data:
            continue
        for post in data.get("data", {}).get("children", []):
            p = post["data"]
            created = datetime.fromtimestamp(p["created_utc"], tz=timezone.utc).date()
            if created >= dia:
                found[p["id"]] = p
        time.sleep(0.4)
    return list(found.values())


def fetch_comments(post_id: str, subreddit: str) -> list[str]:
    data = _get(f"https://www.reddit.com/r/{subreddit}/comments/{post_id}.json")
    if not data or not isinstance(data, list) or len(data) < 2:
        return []
    texts = []
    for child in data[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body", "")
        if body and body not in ("[deleted]", "[removed]"):
            texts.append(body)
    return texts


# ─── AGREGACIÓN ──────────────────────────────────────────────────────────────

def _score(mentions: int, upvotes: float) -> float:
    return round(mentions * 2 + math.log(upvotes + 1), 1)


def aggregate_team_picks(picks: list[dict]) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "upvotes": 0.0, "snippets": []})
    for p in picks:
        key = f"{p['team']}||{p['direction']}"
        groups[key]["mentions"] += 1
        groups[key]["upvotes"]  += p["upvotes"]
        if len(groups[key]["snippets"]) < 2:
            groups[key]["snippets"].append(p["snippet"])
    result = []
    for key, d in groups.items():
        team, direction = key.split("||")
        result.append({"team": team, "direction": direction,
                       "mentions": d["mentions"],
                       "score": _score(d["mentions"], d["upvotes"]),
                       "snippets": d["snippets"]})
    return sorted(result, key=lambda x: x["score"], reverse=True)


def aggregate_props(props: list[dict]) -> list[dict]:
    groups: dict[str, dict] = defaultdict(lambda: {"mentions": 0, "upvotes": 0.0,
                                                    "lines": [], "snippets": []})
    for p in props:
        key = f"{p['player']}||{p['stat']}||{p['direction']}"
        groups[key]["mentions"] += 1
        groups[key]["upvotes"]  += p["upvotes"]
        groups[key]["lines"].append(float(p["line"]))
        if len(groups[key]["snippets"]) < 2:
            groups[key]["snippets"].append(p["snippet"])
    result = []
    for key, d in groups.items():
        player, stat, direction = key.split("||")
        avg_line = round(sum(d["lines"]) / len(d["lines"]), 1)
        result.append({"player": player, "stat": stat, "direction": direction,
                       "line": avg_line, "mentions": d["mentions"],
                       "score": _score(d["mentions"], d["upvotes"]),
                       "snippets": d["snippets"]})
    return sorted(result, key=lambda x: x["score"], reverse=True)


# ─── OUTPUT ──────────────────────────────────────────────────────────────────

def _bar(mentions: int) -> str:
    return "█" * min(mentions, 8)


def print_team_picks(ranked: list[dict], top_n: int):
    print(f"\n{BOLD}  PICKS DE PARTIDO  (ML / Spread / Total){RESET}")
    print(f"  {'━'*54}")
    if not ranked:
        print(f"  {GRAY}Sin picks de partido encontrados.{RESET}")
        return
    for i, p in enumerate(ranked[:top_n], 1):
        dir_color = CYAN if p["direction"] == "ML" else (GREEN if "OVER" in p["direction"] else RED if "UNDER" in p["direction"] or "FADE" in p["direction"] else YELLOW)
        print(f"  {i:>2}. {BOLD}{p['team']:<28}{RESET} "
              f"{dir_color}{p['direction']:<14}{RESET} "
              f"×{p['mentions']}  {GRAY}{_bar(p['mentions'])}{RESET}")
        for snip in p["snippets"]:
            print(f"      {GRAY}↳ {snip}{RESET}")


def print_player_props(ranked: list[dict], top_n: int):
    print(f"\n{BOLD}  PROPS DE JUGADORES  (Over / Under){RESET}")
    print(f"  {'━'*54}")
    if not ranked:
        print(f"  {GRAY}Sin props de jugadores encontrados.{RESET}")
        return
    for i, p in enumerate(ranked[:top_n], 1):
        dir_color = GREEN if p["direction"] == "OVER" else RED
        line_str  = f"{p['direction']} {p['line']} {p['stat']}"
        print(f"  {i:>2}. {BOLD}{p['player']:<28}{RESET} "
              f"{dir_color}{line_str:<18}{RESET} "
              f"×{p['mentions']}  {GRAY}{_bar(p['mentions'])}{RESET}")
        for snip in p["snippets"]:
            print(f"      {GRAY}↳ {snip}{RESET}")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Reddit NBA picks reader")
    parser.add_argument("--ayer", action="store_true")
    parser.add_argument("--top",  type=int, default=10, metavar="N")
    parser.add_argument("--raw",  action="store_true")
    args = parser.parse_args()

    dia = date.today() - timedelta(days=1) if args.ayer else date.today()

    print(f"\n{BOLD}  📡  REDDIT NBA PICKS — {dia.strftime('%d/%m/%Y')}{RESET}\n")

    all_posts: list[dict] = []
    for sub in SUBREDDITS:
        print(f"  r/{sub}", end="  ", flush=True)
        posts = fetch_posts(sub, dia)
        for p in posts:
            p["subreddit"] = sub
        all_posts.extend(posts)
        print(f"{len(posts)} posts")
        time.sleep(0.4)

    if not all_posts:
        print("  Sin posts NBA encontrados.\n")
        return

    if args.raw:
        print(f"\n{'━'*58}")
        for p in sorted(all_posts, key=lambda x: x.get("score", 0), reverse=True)[:20]:
            print(f"  [{p.get('score',0):>5}↑] r/{p['subreddit']:12} {p['title'][:70]}")
        print()
        return

    print(f"\n  Analizando {len(all_posts)} posts + comentarios...\n")

    team_picks:  list[dict] = []
    player_props: list[dict] = []

    for post in all_posts:
        upvotes = max(post.get("score", 1), 1)
        for text in [post.get("title", ""), post.get("selftext", "")]:
            team_picks.extend(parse_team_picks(text, upvotes))
            player_props.extend(parse_player_props(text, upvotes))

        if upvotes >= 3 or "pick" in post.get("title", "").lower() or "prop" in post.get("title", "").lower():
            for comment in fetch_comments(post["id"], post["subreddit"])[:40]:
                team_picks.extend(parse_team_picks(comment, 1))
                player_props.extend(parse_player_props(comment, 1))
            time.sleep(0.3)

    ranked_teams = aggregate_team_picks(team_picks)
    ranked_props  = aggregate_props(player_props)

    print_team_picks(ranked_teams,  args.top)
    print_player_props(ranked_props, args.top)

    print(f"\n  {GRAY}Fuentes: r/sportsbook, r/nbabetting  |  Usar como referencia.{RESET}\n")


if __name__ == "__main__":
    main()
