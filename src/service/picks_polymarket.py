from ..core.polymarket_config import (
    MIN_EDGE, MIN_VOLUME, MIN_LIQUIDITY, MAX_DAYS_TO_RESOLUTION, STAKE_FRACTION,
)
from ..polymarket import collector, analyzer
from ..polymarket.db_engine import setup as pm_setup, get_conn as pm_get_conn

import json


def _load_saved_strategy(strategy_id: int) -> dict | None:
    with pm_get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM strategy_params WHERE id = ?", (strategy_id,)
        ).fetchone()
    if not row:
        return None
    return dict(row)


def execute(params: dict, ctx) -> dict:
    pm_setup()

    tag           = params.get("tag", "")
    limit         = int(params.get("limit", 50))
    min_volume    = float(params.get("min_volume", MIN_VOLUME))
    min_liquidity = float(params.get("min_liquidity", MIN_LIQUIDITY))
    min_edge      = float(params.get("min_edge", MIN_EDGE))
    max_days      = float(params.get("max_days_to_resolution", MAX_DAYS_TO_RESOLUTION))
    strategy      = params.get("strategy", "meta_consensus")
    fetch_books   = params.get("fetch_orderbooks", True)
    strategy_name = None

    strategy_id = params.get("strategy_id")
    if strategy_id is not None:
        saved = _load_saved_strategy(int(strategy_id))
        if saved:
            strategy = saved["strategy_type"]
            strategy_name = saved["name"]
            saved_params = json.loads(saved.get("params_json", "{}"))
            ctx.log_line(f"Cargando estrategia guardada: {strategy_name} ({strategy})")
        else:
            ctx.log_line(f"Estrategia #{strategy_id} no encontrada, usando default")
            saved_params = {}
    else:
        saved_params = {}

    strategy_params = {
        "weight_momentum":      float(params.get("weight_momentum") or
                                     saved_params.get("weight_momentum", 0.25)),
        "weight_imbalance":     float(params.get("weight_imbalance") or
                                     saved_params.get("weight_imbalance", 0.25)),
        "weight_fundamental":   float(params.get("weight_fundamental") or
                                     saved_params.get("weight_fundamental", 0.25)),
        "weight_sentiment":     float(params.get("weight_sentiment") or
                                     saved_params.get("weight_sentiment", 0.10)),
        "weight_time_penalty":  float(params.get("weight_time_penalty") or
                                     saved_params.get("weight_time_penalty", 0.075)),
        "weight_spread_penalty": float(params.get("weight_spread_penalty") or
                                       saved_params.get("weight_spread_penalty", 0.075)),
        "user_prompt_customization": params.get("user_prompt_customization") or
                                     saved_params.get("user_prompt_customization", ""),
        "fixed_data_block":     params.get("fixed_data_block") or
                                saved_params.get("fixed_data_block", ""),
        "probs":                params.get("manual_probs", {}),
        "model_url":            params.get("external_model_url", ""),
    }

    ctx.log_line(f"Scanner iniciado: tag={tag or 'all'}, strategy={strategy}, limit={limit}")
    ctx.set_progress(0.0)

    # ── Fetch events ──
    ctx.log_line("Fetching events from Polymarket Gamma API...")
    markets = collector.fetch_events(tag=tag, limit=limit, min_volume=min_volume)
    ctx.log_line(f"Retrieved {len(markets)} markets")
    ctx.set_progress(0.05)

    # ── Fetch orderbooks ──
    if fetch_books:
        ctx.log_line("Fetching orderbooks from CLOB API...")
        total = len(markets)
        for i, m in enumerate(markets):
            if m.get("token_id"):
                m["_orderbook"] = collector.fetch_orderbook(m["token_id"])
            ctx.set_progress(0.05 + 0.25 * (i + 1) / max(total, 1))
    else:
        ctx.set_progress(0.30)

    # ── Analyze ──
    ctx.log_line(f"Running {strategy} analysis...")
    results = analyzer.compute_probs(markets, strategy=strategy,
                                     strategy_params=strategy_params)
    ctx.log_line(f"Generated {len(results)} probability estimates")
    ctx.set_progress(0.65)

    # ── Filter opportunities ──
    opportunities = []
    for r in results:
        if r["edge"] < min_edge:
            continue
        market = next((m for m in markets if m["market_slug"] == r["market_slug"]), {})
        if market.get("liquidity", 0) < min_liquidity:
            continue
        days = analyzer._days_until(market.get("end_date", ""))
        if days > max_days:
            continue

        opportunities.append({
            **r,
            "question": market.get("question", ""),
            "token_id": market.get("token_id", ""),
            "volume_24h": market.get("volume_24h", 0),
            "liquidity": market.get("liquidity", 0),
            "end_date": market.get("end_date", ""),
            "days_left": days,
            "tags": market.get("tags", []),
            "direction": "UP" if r["edge"] > 0 else "DOWN",
        })

    opportunities.sort(key=lambda x: x["edge"], reverse=True)
    ctx.log_line(f"Found {len(opportunities)} opportunities above edge={min_edge}")
    ctx.set_progress(1.0)

    return {
        "opportunities": opportunities,
        "total_markets_scanned": len(markets),
        "strategy": strategy,
        "strategy_name": strategy_name if strategy_id else None,
        "tag": tag or "all",
    }
