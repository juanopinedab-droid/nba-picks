import json
import time
import requests
from .db_engine import MarketCache

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"
CACHE_TTL  = 300
MAX_RETRIES = 3
RETRY_BACKOFF = 2.0


def _rate_limited_get(url: str, params: dict | None = None,
                      timeout: int = 15, retries: int = MAX_RETRIES) -> requests.Response:
    last_exc = None
    for attempt in range(retries):
        try:
            resp = requests.get(url, params=params, timeout=timeout)
            if resp.status_code == 429:
                wait = RETRY_BACKOFF * (2 ** attempt)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
    raise last_exc


def fetch_tags() -> list[dict]:
    """GET /tags → lista de categorias disponibles."""
    cached = MarketCache.get("tags")
    if cached:
        return cached
    resp = _rate_limited_get(f"{GAMMA_BASE}/tags", timeout=15)
    tags = resp.json()
    MarketCache.set("tags", tags, ttl_seconds=86400)
    return tags


def fetch_events(tag: str = "", limit: int = 50,
                 active_only: bool = True,
                 min_volume: float = 0) -> list[dict]:
    """
    GET /events?tag=...&limit=...&active=true&closed=false
    Retorna eventos con sus mercados anidados.
    """
    cache_key = f"gamma:events:tag={tag}:limit={limit}:minvol={int(min_volume)}"
    cached = MarketCache.get(cache_key)
    if cached:
        return cached

    params = {"limit": limit}
    if tag:
        params["tag"] = tag
    if active_only:
        params["active"] = "true"
        params["closed"] = "false"

    resp = _rate_limited_get(f"{GAMMA_BASE}/events", params=params, timeout=20)
    resp.raise_for_status()
    events = resp.json()

    markets = _flatten_markets(events, min_volume)

    MarketCache.set(cache_key, markets, ttl_seconds=CACHE_TTL)
    return markets


def fetch_orderbook(token_id: str) -> dict | None:
    """
    GET /book?token_id=... → order book completo.
    Calcula bestBid, bestAsk, imbalance.
    """
    cache_key = f"clob:book:{token_id}"
    cached = MarketCache.get(cache_key)
    if cached:
        return cached

    try:
        resp = _rate_limited_get(f"{CLOB_BASE}/book",
                                params={"token_id": token_id}, timeout=10)
        resp.raise_for_status()
        book = resp.json()

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        total_bids = sum(float(b.get("size", 0)) for b in bids)
        total_asks = sum(float(a.get("size", 0)) for a in asks)

        result = {
            "token_id": token_id,
            "best_bid": float(bids[0]["price"]) if bids else 0,
            "best_ask": float(asks[0]["price"]) if asks else 1,
            "bid_size": total_bids,
            "ask_size": total_asks,
            "spread": (float(asks[0]["price"]) - float(bids[0]["price"]))
                       if bids and asks else 1,
            "imbalance": ((total_bids - total_asks) / (total_bids + total_asks))
                         if (total_bids + total_asks) > 0 else 0,
        }
        MarketCache.set(cache_key, result, ttl_seconds=60)
        return result
    except Exception:
        return None


def fetch_price_history(token_id: str, days: int = 7) -> list[dict]:
    """
    GET /prices-history?token_id=...&interval=max&fidelity=60
    Retorna serie temporal de precios.
    """
    try:
        resp = _rate_limited_get(
            f"{CLOB_BASE}/prices-history",
            params={"market": token_id, "interval": "max",
                   "fidelity": 60 * 24},
            timeout=15
        )
        resp.raise_for_status()
        history = resp.json().get("history", [])
        return [
            {"t": h["t"], "price": h["p"]}
            for h in history[-days:] if h.get("p")
        ]
    except Exception:
        return []


def _flatten_markets(events: list[dict], min_volume: float) -> list[dict]:
    result = []
    for event in events:
        event_slug = event.get("slug", "")
        event_tags = [t.get("label", t.get("slug", ""))
                     for t in event.get("tags", [])]
        context = (event.get("eventMetadata", {})
                   .get("context_description", ""))

        for market in event.get("markets", []):
            if min_volume > 0 and market.get("volume24hr", 0) < min_volume:
                continue
            if market.get("closed", False):
                continue
            result.append({
                "event_slug": event_slug,
                "market_slug": market.get("slug", market.get("id", "")),
                "question": market.get("question", ""),
                "outcomes": market.get("outcomes", "[]"),
                "outcome_prices": market.get("outcomePrices", "[]"),
                "token_id": _extract_token_id(market),
                "last_trade_price": float(market.get("lastTradePrice", 0)),
                "best_bid": float(market.get("bestBid", 0)),
                "best_ask": float(market.get("bestAsk", 1)),
                "spread": float(market.get("spread", 1)),
                "volume_24h": float(market.get("volume24hr", 0)),
                "liquidity": float(market.get("liquidity", 0)),
                "competitive": float(market.get("competitive", 0)),
                "end_date": market.get("endDateIso",
                          market.get("endDate", "")),
                "tags": event_tags,
                "one_hour_change": float(market.get("oneHourPriceChange", 0)),
                "one_day_change": float(market.get("oneDayPriceChange", 0)),
                "one_week_change": float(market.get("oneWeekPriceChange", 0)),
                "event_context": context,
            })
    return result


def _extract_token_id(market: dict) -> str:
    tokens = market.get("clobTokenIds", "[]")
    try:
        parsed = json.loads(tokens) if isinstance(tokens, str) else tokens
        return str(parsed[0]) if parsed else ""
    except (json.JSONDecodeError, IndexError, TypeError):
        return ""
