import time
import requests
from . import config


def http_get(url: str, params: dict = None, timeout: int = 15, max_retries: int = 3) -> requests.Response:
    """
    GET con retry exponencial (1s → 2s → 4s).
    Reintenta solo errores transitorios: 5xx, Timeout, ConnectionError.
    No reintenta 4xx (clave inválida, not found, rate limit).
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except requests.HTTPError as e:
            last_exc = e
            if e.response.status_code < 500:
                raise  # 4xx → sin reintentos
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  ⚠️  HTTP {e.response.status_code} — reintento {attempt + 1}/{max_retries - 1} en {wait}s...")
                time.sleep(wait)
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < max_retries - 1:
                wait = 2 ** attempt
                print(f"  ⚠️  Error de red — reintento {attempt + 1}/{max_retries - 1} en {wait}s...")
                time.sleep(wait)
    raise last_exc


def extract_best_odds(bookmakers: list, home: str, away: str) -> dict | None:
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
                if best["h2h_home"] is None:
                    best["h2h_home"] = odds_home
                    best["h2h_away"] = odds_away
                    best["bookmaker"] = book["title"]

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
