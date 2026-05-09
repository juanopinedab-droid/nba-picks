"""
polymarket.py — Referencia: wallets con mejores picks NBA en Polymarket

Uso:
    python polymarket.py               # top wallets + sus posiciones abiertas
    python polymarket.py --mercados    # solo listar mercados NBA activos
    python polymarket.py --top 20      # mostrar más wallets (default: 10)
"""

import argparse
import time
import requests
from collections import defaultdict

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API  = "https://clob.polymarket.com"

# Keywords que identifican un mercado como NBA (se buscan en el título)
_NBA_KEYWORDS = (
    "nba", "nba ", "basketball", "celtics", "lakers", "warriors", "nuggets",
    "heat", "bucks", "nets", "sixers", "suns", "clippers", "knicks", "bulls",
    "cavaliers", "hawks", "hornets", "pistons", "pacers", "raptors", "magic",
    "wizards", "thunder", "trail blazers", "jazz", "timberwolves", "pelicans",
    "spurs", "rockets", "mavericks", "grizzlies", "kings", "wolves",
    "playoff", "finals", "nba champion", "win the series", "advance",
)

# Máximo de mercados a ANALIZAR con el CLOB (trades = 1 req/mercado)
# Puedes subir este número; tarda ~0.3s por mercado adicional
MAX_MARKETS_ANALYZE = 60

BOLD  = "\033[1m"
RESET = "\033[0m"
GREEN = "\033[92m"
RED   = "\033[91m"
GRAY  = "\033[90m"
CYAN  = "\033[96m"


# ─── FETCH ───────────────────────────────────────────────────────────────────

def _is_nba(market: dict) -> bool:
    """Determina si un mercado es de NBA por su título o tags."""
    question = market.get("question", "").lower()
    tags = [t.get("slug", "") for t in market.get("tags", [])]
    if any(k in question for k in _NBA_KEYWORDS):
        return True
    if any("nba" in t or "basketball" in t for t in tags):
        return True
    return False


def get_nba_markets(solo_activos: bool = False) -> list[dict]:
    """
    Pagina el Gamma API hasta obtener todos los mercados y filtra los de NBA
    por keyword en el título (más confiable que tag_slug solo).
    """
    nba_markets = []
    offset = 0
    page   = 500  # máximo que suele aceptar la API por página

    base_params: dict = {"limit": page}
    if solo_activos:
        base_params["active"] = "true"
        base_params["closed"] = "false"

    print("  [Polymarket] Buscando mercados NBA", end="", flush=True)

    while True:
        try:
            resp = requests.get(
                f"{GAMMA_API}/markets",
                params={**base_params, "offset": offset},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data if isinstance(data, list) else data.get("markets", [])
        except Exception as e:
            print(f"\n  ❌  Error paginando mercados: {e}")
            break

        if not batch:
            break

        found = [m for m in batch if _is_nba(m)]
        nba_markets.extend(found)
        print(".", end="", flush=True)

        if len(batch) < page:
            break  # última página
        offset += page
        time.sleep(0.3)

    print(f" {len(nba_markets)} encontrados")
    return nba_markets


def get_trades(condition_id: str) -> list[dict]:
    """Retorna todos los trades de un mercado paginando el CLOB API."""
    trades = []
    offset = 0
    limit  = 500

    while True:
        try:
            resp = requests.get(
                f"{CLOB_API}/trades",
                params={"market": condition_id, "limit": limit, "offset": offset},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data if isinstance(data, list) else data.get("data", [])
        except Exception:
            break

        trades.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
        time.sleep(0.3)

    return trades


# ─── ANÁLISIS ────────────────────────────────────────────────────────────────

def _winner_outcome(market: dict) -> str | None:
    """
    Intenta determinar el outcome ganador de un mercado resuelto.
    Retorna el asset_id del outcome ganador, o None si no se puede determinar.
    """
    tokens = market.get("tokens", [])
    for token in tokens:
        if float(token.get("price", 0)) >= 0.99:
            return token.get("token_id") or token.get("outcome_id")
    return None


def build_wallet_stats(markets: list[dict]) -> dict:
    """
    Analiza trades de cada mercado y agrupa por wallet.
    Retorna {address: {volume, wins, losses, profit_est, open: [...]}}
    """
    stats = defaultdict(lambda: {
        "volume":      0.0,
        "wins":        0,
        "losses":      0,
        "profit_est":  0.0,  # estimado: ganancia en USDC
        "open":        [],   # posiciones abiertas actuales
    })

    # Priorizar mercados con más volumen para no gastar tiempo en mercados vacíos
    markets_sorted = sorted(
        markets,
        key=lambda m: float(m.get("volume", 0) or 0),
        reverse=True,
    )[:MAX_MARKETS_ANALYZE]

    total = len(markets_sorted)
    for i, market in enumerate(markets_sorted):
        cid      = market.get("conditionId") or market.get("condition_id", "")
        question = market.get("question", "Sin descripción")
        resuelto = market.get("closed", False) or market.get("resolved", False)

        print(f"  [{i+1}/{total}] {question[:55]:<55}", end="\r", flush=True)

        if not cid:
            continue

        winner_token = _winner_outcome(market) if resuelto else None
        trades = get_trades(cid)
        time.sleep(0.25)

        for trade in trades:
            for role in ("maker_address", "taker_address"):
                addr = trade.get(role, "")
                if not addr:
                    continue

                price     = float(trade.get("price", 0) or 0)
                size      = float(trade.get("size",  0) or 0)
                side      = str(trade.get("side", "")).upper()
                asset_id  = str(trade.get("asset_id") or trade.get("outcome_id") or "")
                cost      = price * size

                stats[addr]["volume"] += cost

                if resuelto:
                    if side == "BUY":
                        won = winner_token and asset_id == str(winner_token)
                        if won:
                            stats[addr]["wins"]       += 1
                            stats[addr]["profit_est"] += size - cost   # payout $1/share
                        else:
                            stats[addr]["losses"]     += 1
                            stats[addr]["profit_est"] -= cost
                else:
                    if side == "BUY":
                        stats[addr]["open"].append({
                            "mercado": question,
                            "precio":  price,
                            "shares":  size,
                        })

    print()  # limpia la línea de progreso
    return stats


def rank_wallets(stats: dict, min_volume: float = 5.0) -> list[dict]:
    """Filtra y ordena wallets por profit estimado."""
    ranked = []
    for addr, s in stats.items():
        total_bets = s["wins"] + s["losses"]
        if s["volume"] < min_volume or total_bets == 0:
            continue
        win_rate = s["wins"] / total_bets if total_bets else 0
        ranked.append({
            "address":    addr,
            "volume":     round(s["volume"], 2),
            "wins":       s["wins"],
            "losses":     s["losses"],
            "win_rate":   win_rate,
            "profit_est": round(s["profit_est"], 2),
            "open":       s["open"],
        })

    ranked.sort(key=lambda x: x["profit_est"], reverse=True)
    return ranked


# ─── OUTPUT ──────────────────────────────────────────────────────────────────

def print_markets(markets: list[dict]):
    print(f"\n{BOLD}  MERCADOS NBA ACTIVOS EN POLYMARKET{RESET}")
    print(f"{'━'*58}")
    for m in markets:
        status  = "🟢" if not m.get("closed") else "🔴"
        vol     = float(m.get("volume", 0) or 0)
        q       = m.get("question", "")
        tokens  = m.get("tokens", [])
        odds    = "  |  ".join(
            f"{t.get('outcome','?')}: {float(t.get('price',0)):.0%}"
            for t in tokens
        )
        print(f"\n  {status} {BOLD}{q}{RESET}")
        print(f"     Volumen: ${vol:,.0f} USDC  |  {odds}")
    print()


def print_wallet_table(ranked: list[dict], top_n: int):
    print(f"\n{BOLD}  TOP {top_n} WALLETS NBA — POLYMARKET{RESET}")
    print(f"{'━'*58}")
    print(f"  {'#':<3} {'WALLET':<12} {'W':>4} {'L':>4} {'WIN%':>6} "
          f"{'VOLUMEN':>10} {'PROFIT EST':>12}")
    print(f"  {'─'*3} {'─'*12} {'─'*4} {'─'*4} {'─'*6} {'─'*10} {'─'*12}")

    for i, w in enumerate(ranked[:top_n], 1):
        addr_short = w["address"][:6] + "…" + w["address"][-4:]
        profit_str = f"{'+'if w['profit_est']>=0 else ''}{w['profit_est']:,.1f}"
        color      = GREEN if w["profit_est"] >= 0 else RED

        print(f"  {i:<3} {addr_short:<12} {w['wins']:>4} {w['losses']:>4} "
              f"{w['win_rate']:>6.0%} {w['volume']:>10,.1f} "
              f"{color}{profit_str:>12}{RESET}")

        if w["open"]:
            for pos in w["open"][:3]:  # máximo 3 posiciones abiertas por wallet
                mercado_corto = pos["mercado"][:48]
                print(f"      {CYAN}↳ {mercado_corto:<48} "
                      f"@ {pos['precio']:.0%}  ({pos['shares']:.1f} shares){RESET}")
            if len(w["open"]) > 3:
                print(f"      {GRAY}  … y {len(w['open'])-3} posiciones más{RESET}")

    print(f"\n  {GRAY}Profit estimado = payout en mercados resueltos − costo total de compras.{RESET}")
    print(f"  {GRAY}Wallets con volumen < $5 USDC excluidas.{RESET}\n")


# ─── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Polymarket NBA wallet tracker")
    parser.add_argument("--mercados", action="store_true",
                        help="Solo listar mercados NBA activos")
    parser.add_argument("--top", type=int, default=10, metavar="N",
                        help="Cuántas wallets mostrar (default: 10)")
    parser.add_argument("--min-vol", type=float, default=5.0, metavar="USDC",
                        help="Volumen mínimo para incluir wallet (default: 5 USDC)")
    args = parser.parse_args()

    print(f"\n{BOLD}  🏀  POLYMARKET NBA TRACKER{RESET}\n")

    markets = get_nba_markets(solo_activos=args.mercados)
    if not markets:
        print("  Sin mercados NBA disponibles.\n")
        return

    print(f"  Mercados encontrados: {len(markets)}\n")

    if args.mercados:
        print_markets(markets)
        return

    print(f"  Analizando trades (puede tardar 1-2 min)...\n")
    wallet_stats = build_wallet_stats(markets)
    ranked       = rank_wallets(wallet_stats, min_volume=args.min_vol)

    if not ranked:
        print("  Sin wallets con actividad suficiente en mercados NBA.\n")
        return

    print_wallet_table(ranked, top_n=args.top)


if __name__ == "__main__":
    main()
