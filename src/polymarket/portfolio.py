import json
from .db_engine import PositionDB, BankrollDB


def open_position(market: dict, analysis: dict,
                  bankroll_usd: float,
                  fraction: float = 0.25) -> dict:
    """
    Abre una posicion usando Kelly fraccional para opciones binarias.

    f* = (p - c) / (1 - c) * fraction
    donde p = our_prob, c = best_ask (precio real de compra).
    """
    our_prob = analysis["our_prob"]
    real_price = analysis["real_price"]

    if real_price >= 1.0 or real_price <= 0:
        return {"error": "Precio de compra invalido"}

    if not market.get("token_id"):
        return {"error": "Mercado sin token_id: solo analisis, no se puede tradear"}

    edge = our_prob - real_price
    if edge <= 0:
        return {"error": "Sin edge positivo"}

    kelly_fraction = edge / (1 - real_price)
    stake_pct = kelly_fraction * fraction
    stake_pct = min(stake_pct, 0.05)

    cost_usd = bankroll_usd * stake_pct
    cost_usd = max(cost_usd, 1.0)

    shares = cost_usd / real_price

    position = {
        "event_slug": market["event_slug"],
        "market_slug": market["market_slug"],
        "question": market["question"],
        "token_id": market["token_id"],
        "side": "BUY",
        "shares": shares,
        "entry_price": real_price,
        "current_price": real_price,
        "cost_usd": cost_usd,
        "pnl_usd": 0,
        "pnl_pct": 0,
        "status": "open",
        "our_prob": our_prob,
        "edge_at_entry": edge,
        "strategy": market.get("_strategy", "meta_consensus"),
        "rationale_json": json.dumps(analysis.get("rationale", {}))
    }

    pos_id = PositionDB.open_position(position)

    BankrollDB.withdraw(cost_usd,
                        note=f"Open: {market['question'][:60]}")

    position["id"] = pos_id
    return position


def close_position(position_id: int, reason: str = "manual") -> dict:
    """
    Cierra una posicion al bestBid actual (precio de venta).
    Calcula PnL realizado y registra ingreso en bankroll.
    """
    pos = PositionDB.get_position(position_id)
    if not pos or pos.get("status") != "open":
        return {"error": "Posicion no encontrada o ya cerrada"}

    from . import collector
    if pos["token_id"]:
        ob = collector.fetch_orderbook(pos["token_id"])
        exit_price = ob["best_bid"] if ob else pos["current_price"]
    else:
        exit_price = pos["current_price"]

    result = PositionDB.close_position(position_id, exit_price, reason)

    BankrollDB.deposit(pos["cost_usd"],
                       note=f"Close #{position_id}: {pos['question'][:40]}")

    return result


def mark_to_market(positions: list[dict]) -> list[dict]:
    """
    Actualiza current_price y PnL no realizado para posiciones abiertas.
    Usa bestBid como precio de venta (mark-to-market conservador).
    """
    from . import collector

    for pos in positions:
        if not pos.get("token_id"):
            continue
        ob = collector.fetch_orderbook(pos["token_id"])
        if ob:
            pos["current_price"] = ob["best_bid"]
            pos["pnl_usd"] = (ob["best_bid"] - pos["entry_price"]) * pos["shares"]
            pos["pnl_pct"] = (pos["pnl_usd"] / pos["cost_usd"]) * 100 if pos["cost_usd"] else 0

    PositionDB.update_market_prices(positions)
    return positions


def get_portfolio_summary() -> dict:
    """Resumen del portafolio: posiciones abiertas, PnL, exposicion."""
    open_positions = PositionDB.get_positions(status="open")
    positions = mark_to_market(open_positions)

    total_cost = sum(p["cost_usd"] for p in positions)
    total_pnl = sum(p["pnl_usd"] for p in positions)
    current_value = total_cost + total_pnl

    return {
        "positions": positions,
        "open_count": len(positions),
        "total_cost_usd": total_cost,
        "current_value_usd": current_value,
        "unrealized_pnl_usd": total_pnl,
        "unrealized_pnl_pct": (total_pnl / total_cost * 100) if total_cost > 0 else 0,
    }
