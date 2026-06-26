import math
import json
import requests
from datetime import date as dt_date
from collections import defaultdict


def compute_probs(markets: list[dict],
                  strategy: str = "meta_consensus",
                  strategy_params: dict | None = None) -> list[dict]:
    """
    Calcula our_prob para cada mercado usando la estrategia indicada.
    Retorna lista de resultados con rationale.

    Estrategias disponibles:
      - market_implied  : precio de mercado como probabilidad (baseline)
      - manual          : usuario proporciona probabilidades
      - external        : POST a un servicio externo
      - meta_consensus  : senales cuantitativas (momentum, imbalance, etc.)
    """
    params = strategy_params or {}
    results = []

    for market in markets:
        if strategy == "market_implied":
            result = _market_implied(market)
        elif strategy == "manual":
            result = _manual(market, params)
        elif strategy == "external":
            result = _external(market, params)
        else:
            result = _meta_consensus(market, params)
        results.append(result)

    results = _normalize_by_event(results)

    return results


def _meta_consensus(market: dict, params: dict) -> dict:
    w = {
        "momentum": params.get("weight_momentum", 0.25),
        "imbalance": params.get("weight_imbalance", 0.25),
        "fundamental": params.get("weight_fundamental", 0.25),
        "sentiment": params.get("weight_sentiment", 0.10),
        "time_penalty": params.get("weight_time_penalty", 0.075),
        "spread_penalty": params.get("weight_spread_penalty", 0.075),
    }
    price = market.get("last_trade_price", 0.5)
    signals = []

    mom_1h  = market.get("one_hour_change", 0)
    mom_24h = market.get("one_day_change", 0)
    mom_1w  = market.get("one_week_change", 0)
    momentum = mom_1h * 0.5 + mom_24h * 0.3 + mom_1w * 0.2
    signals.append({"name": "Price Momentum", "impact": momentum,
                    "weight": w["momentum"], "type": "directional"})

    imbalance = 0.0
    ob = market.get("_orderbook")
    if ob:
        imbalance = ob.get("imbalance", 0) * 0.10
    signals.append({"name": "Order Book Imbalance", "impact": imbalance,
                    "weight": w["imbalance"], "type": "directional"})

    fundamental = params.get("_fundamental_shift", 0.0)
    if fundamental != 0.0:
        signals.append({"name": "AI Oracle (Multi-Agent)", "impact": fundamental,
                        "weight": w["fundamental"], "type": "directional"})

    sentiment = _context_sentiment(market.get("event_context", ""))
    signals.append({"name": "Context Sentiment", "impact": sentiment,
                    "weight": w["sentiment"], "type": "directional"})

    spread = market.get("spread", 0)
    competitive = market.get("competitive", 1)

    spread_penalty = min(spread * 2, 0.5)
    inefficiency_penalty = (1 - competitive) * 0.5

    days_left = _days_until(market.get("end_date", ""))
    time_penalty = 1 - math.exp(-0.003 * max(days_left, 0))

    signals.append({"name": "Time Uncertainty",
                    "impact": -time_penalty,
                    "weight": w["time_penalty"], "type": "uncertainty",
                    "detail": f"{days_left}d to resolution"})

    confidence = (
        1.0
        - spread_penalty * (w["spread_penalty"] / 0.10)
        - inefficiency_penalty * 0.5
        - time_penalty * (w["time_penalty"] / 0.10)
    )
    confidence = max(0.05, min(1.0, confidence))

    raw_directional = (
        momentum * w["momentum"] +
        imbalance * w["imbalance"] +
        fundamental * w["fundamental"] +
        sentiment * w["sentiment"]
    )
    adjustment = raw_directional * confidence
    our_prob = max(0.01, min(0.99, price + adjustment))

    real_price = _safe_real_price(market)

    edge = our_prob - real_price

    return {
        "market_slug": market["market_slug"],
        "event_slug": market.get("event_slug", ""),
        "our_prob": our_prob,
        "market_price": price,
        "real_price": real_price,
        "edge": edge,
        "confidence": _score_confidence(edge, competitive, spread),
        "fundamental_shift": fundamental,
        "rationale": {
            "base_price": price,
            "signals": [
                {**s, "color": _signal_color(s)} for s in signals
            ],
            "raw_directional": raw_directional,
            "confidence_factor": confidence,
            "adjustment": adjustment,
            "adjusted_prob": our_prob,
        }
    }


def _score_confidence(edge: float, competitive: float, spread: float) -> str:
    score = abs(edge) * 10 + competitive * 2 - spread * 3
    if score > 0.6:
        return "HIGH"
    elif score > 0.3:
        return "MEDIUM"
    return "LOW"


def _signal_color(signal: dict) -> str:
    if signal["type"] == "uncertainty":
        return "red"
    impact = signal.get("impact", 0)
    if impact > 0.01:
        return "green"
    elif impact < -0.01:
        return "red"
    return "neutral"


def _score_llm_confidence(edge: float, our_prob: float, market_price: float) -> str:
    divergence = abs(our_prob - market_price)
    if edge > 0.05 and divergence > 0.10:
        return "HIGH"
    elif edge > 0.03:
        return "MEDIUM"
    return "LOW"


def _context_sentiment(context: str) -> float:
    if not context:
        return 0.0
    bullish = ["surge", "momentum", "breakout", "rally", "upward",
               "strong", "accelerat", "record", "outperform"]
    bearish = ["decline", "crash", "bearish", "downturn", "weak",
               "risk", "uncertain", "volatile", "correction", "sell-off"]
    ctx_lower = context.lower()
    score = sum(0.005 for w in bullish if w in ctx_lower)
    score -= sum(0.005 for w in bearish if w in ctx_lower)
    return max(-0.03, min(0.03, score))


def _days_until(iso_date: str) -> int:
    try:
        end = dt_date.fromisoformat(iso_date[:10])
        return max(0, (end - dt_date.today()).days)
    except (ValueError, TypeError):
        return 365


def _normalize_by_event(results: list[dict]) -> list[dict]:
    groups = defaultdict(list)
    for r in results:
        key = r.get("event_slug") or r.get("_event_slug", r["market_slug"])
        groups[key].append(r)

    for event_slug, group in groups.items():
        if len(group) < 2:
            continue
        total = sum(r["our_prob"] for r in group)
        if total > 0:
            for r in group:
                r["our_prob"] = r["our_prob"] / total
                r["edge"] = r["our_prob"] - r["real_price"]
    return results


def _safe_real_price(market: dict) -> float:
    price = market.get("last_trade_price", 0.5)
    ask = market.get("best_ask")
    if ask is None or ask <= 0.01 or ask >= 0.99:
        return price
    return ask


def _market_implied(market: dict) -> dict:
    price = market.get("last_trade_price", 0.5)
    return {
        "market_slug": market["market_slug"],
        "event_slug": market.get("event_slug", ""),
        "our_prob": price,
        "market_price": price,
        "real_price": _safe_real_price(market),
        "edge": 0,
        "confidence": "LOW",
        "fundamental_shift": 0.0,
        "rationale": {"base_price": price, "signals": []}
    }


def _manual(market: dict, params: dict) -> dict:
    probs = params.get("probs", {})
    our = probs.get(market["market_slug"], market.get("last_trade_price", 0.5))
    price = market.get("last_trade_price", 0.5)
    real_price = _safe_real_price(market)
    return {
        "market_slug": market["market_slug"],
        "event_slug": market.get("event_slug", ""),
        "our_prob": our,
        "market_price": price,
        "real_price": real_price,
        "edge": our - real_price,
        "confidence": "MEDIUM",
        "fundamental_shift": 0.0,
        "rationale": {"base_price": price, "signals": []}
    }


def _external(market: dict, params: dict) -> dict:
    model_url = params.get("model_url", "")
    if not model_url:
        return _market_implied(market)
    try:
        resp = requests.post(model_url, json={
            "slug": market["market_slug"],
            "question": market.get("question", ""),
            "market_price": market.get("last_trade_price", 0.5),
            "volume_24h": market.get("volume_24h", 0),
            "context": market.get("event_context", ""),
        }, timeout=10)
        our = resp.json().get("our_prob", market.get("last_trade_price", 0.5))
    except Exception:
        our = market.get("last_trade_price", 0.5)

    price = market.get("last_trade_price", 0.5)
    real_price = _safe_real_price(market)
    return {
        "market_slug": market["market_slug"],
        "event_slug": market.get("event_slug", ""),
        "our_prob": our,
        "market_price": price,
        "real_price": real_price,
        "edge": our - real_price,
        "confidence": "MEDIUM",
        "fundamental_shift": 0.0,
        "rationale": {"base_price": price, "signals": []}
    }


def price_to_american(price: float) -> int:
    if price <= 0 or price >= 1:
        raise ValueError
    if price > 0.50:
        return int(round((price / (1 - price)) * -100))
    elif price < 0.50:
        return int(round(((1 - price) / price) * 100))
    return 100


def american_to_price(odds: int) -> float:
    if odds == 0:
        raise ValueError("Las cuotas americanas no pueden ser 0")
    if odds < 0:
        return abs(odds) / (abs(odds) + 100)
    return 100 / (odds + 100)
