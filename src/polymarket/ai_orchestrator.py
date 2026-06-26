import asyncio
import json
import os
import re
import time
import logging
import threading
from datetime import datetime
from typing import Callable
from openai import AsyncOpenAI
from ddgs import DDGS
import requests

logger = logging.getLogger(__name__)

SEARCH_SEM = threading.Semaphore(1)
SEARCH_DELAY = 0.6
SEARCH_MAX_RETRIES = 2

PM_API_SEM = threading.Semaphore(2)
PM_CACHE_TTL = 60
PM_CACHE: dict[str, tuple[float, str]] = {}

GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"

_CANCEL_EVENTS: dict[int, threading.Event] = {}
_CANCEL_LOCK = threading.Lock()


def register_cancel_event(session_id: int) -> threading.Event:
    with _CANCEL_LOCK:
        evt = threading.Event()
        _CANCEL_EVENTS[session_id] = evt
        return evt


def cancel_session(session_id: int) -> bool:
    with _CANCEL_LOCK:
        evt = _CANCEL_EVENTS.get(session_id)
        if evt is not None:
            evt.set()
            return True
        return False


def _cleanup_cancel_event(session_id: int):
    with _CANCEL_LOCK:
        _CANCEL_EVENTS.pop(session_id, None)


def _now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

MAX_CONCURRENT  = 3
MAX_STEPS_PER_AGENT = 3

client = None


def _extract_json(text: str) -> dict | list | None:
    """Intenta extraer JSON del texto, incluyendo fences markdown y arrays."""
    if not text:
        return None
    cleaned = text.strip()

    fence_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', cleaned, re.DOTALL)
    if fence_match:
        cleaned = fence_match.group(1).strip()

    first_brace = cleaned.find('{')
    first_bracket = cleaned.find('[')

    if first_brace == -1 and first_bracket == -1:
        return None

    pairs = []
    if first_bracket != -1 and first_bracket < (first_brace if first_brace != -1 else float('inf')):
        pairs = [('[', ']'), ('{', '}')]
    else:
        pairs = [('{', '}'), ('[', ']')]

    for start_char, end_char in pairs:
        start = cleaned.find(start_char)
        end = cleaned.rfind(end_char)
        if start != -1 and end != -1 and end > start:
            candidate = cleaned[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                continue

    return None


def _get_client() -> AsyncOpenAI:
    global client
    if client is None:
        key = os.getenv("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DEEPSEEK_API_KEY not configured in .env")
        client = AsyncOpenAI(
            api_key=key,
            base_url="https://api.deepseek.com"
        )
    return client

subagent_tools = [{
    "type": "function",
    "function": {
        "name": "search_internet",
        "description": "Search the internet for current news, statistics, and factual "
                       "information about a specific topic. Use this to gather hard data.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Be specific and use keywords."
                }
            },
            "required": ["query"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "search_polymarket_markets",
        "description": "Search Polymarket for active prediction markets matching a topic. "
                       "Returns markets with current prices, volume, liquidity, and spreads. "
                       "Use this to find what the market is actually pricing for a given event.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query or tag. Examples: 'ethereum', 'crypto', 'elections', 'fed'"
                },
                "limit": {
                    "type": "integer",
                    "description": "Max markets to return (default 5, max 10)"
                }
            },
            "required": ["query"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "get_polymarket_orderbook",
        "description": "Get the full order book (bids and asks with sizes) for a specific "
                       "Polymarket market token. Use this to assess market depth, conviction, "
                       "and potential slippage. Returns best bid/ask, spread, and order book "
                       "imbalance.",
        "parameters": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": "The CLOB token ID of the market — a 77-digit decimal string "
                                   "(NOT hexadecimal). Found in the 'clobTokenIds' field of market "
                                   "results. The first element (index 0) is the YES token."
                }
            },
            "required": ["token_id"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "get_polymarket_price_history",
        "description": "Get historical price data for a specific Polymarket market. "
                       "Use this to analyze price trends, momentum, and volatility over time.",
        "parameters": {
            "type": "object",
            "properties": {
                "token_id": {
                    "type": "string",
                    "description": "The CLOB token ID of the market — a 77-digit decimal string "
                                   "(NOT hexadecimal). Found in 'clobTokenIds' field. "
                                   "Index 0 = YES token, index 1 = NO token."
                },
                "days": {
                    "type": "integer",
                    "description": "Number of days of history (default 7, max 30)"
                }
            },
            "required": ["token_id"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "get_polymarket_token_id",
        "description": "Get the full CLOB token IDs for a Polymarket market by name. "
                       "Use this BEFORE calling get_polymarket_orderbook or "
                       "get_polymarket_price_history — those tools require a "
                       "complete token ID. Returns both YES and NO token IDs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Market question or keywords to search. "
                                   "Example: 'Republican Senate majority 2026'"
                }
            },
            "required": ["query"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "get_financial_data",
        "description": "Fetch real-time and historical financial data from Yahoo Finance. "
                       "Use this to get stock/crypto/commodity/index prices, market cap, "
                       "PE ratios, historical OHLCV data, and recent news. "
                       "CRITICAL: use this INSTEAD of search_internet when you need "
                       "HARD price data, trends, or fundamentals for a specific asset.",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "Yahoo Finance ticker symbol. Examples: 'BTC-USD' (crypto), "
                                   "'AAPL' (stock), '^GSPC' (S&P 500 index), 'GC=F' (gold futures), "
                                   "'EURUSD=X' (forex). Use -USD suffix for crypto."
                },
                "action": {
                    "type": "string",
                    "enum": ["quote", "history", "news", "fundamentals", "compare"],
                    "description": "What data to fetch:\n"
                                   "- 'quote': current price, day change, day range, volume\n"
                                   "- 'history': OHLCV time series for trend analysis\n"
                                   "- 'news': recent headlines related to the asset\n"
                                   "- 'fundamentals': market cap, PE ratio, sector, description\n"
                                   "- 'compare': compare multiple tickers (use 'symbols' field)"
                },
                "range": {
                    "type": "string",
                    "enum": ["5d", "1mo", "3mo", "6mo", "1y", "2y", "5y"],
                    "description": "Time range for 'history' action. Default: '1mo'."
                },
                "symbols": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Multiple tickers for 'compare' action. Max 5 symbols."
                }
            },
            "required": ["symbol", "action"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "scrape_webpage",
        "description": (
            "Scrape the FULL content of a webpage and return it as clean markdown. "
            "Use this AFTER search_internet to read an article that looks promising "
            "based on its title/snippet. This gives you the COMPLETE article text, "
            "not just a 150-character summary. "
            "CRITICAL: only use on URLs returned by a previous search_internet call. "
            "Do NOT scrape random URLs or social media. Limit to 3 scrapes per session."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to scrape. Must come from a previous "
                                   "search_internet result. Example: 'https://www.reuters.com/...'"
                }
            },
            "required": ["url"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "submit_research_report",
        "description": "Submit your final research report. Call this EXACTLY ONCE "
                       "when you have gathered sufficient hard data. "
                       "After calling this, you stop researching.",
        "parameters": {
            "type": "object",
            "properties": {
                "report": {
                    "type": "string",
                    "description": "4-6 sentence factual summary with numbers, dates, sources."
                }
            },
            "required": ["report"]
        }
    }
}]

main_agent_tools = [{
    "type": "function",
    "function": {
        "name": "spawn_sub_agents",
        "description": (
            "Spawn specialized sub-agents to research specific topics. "
            "Use this to gather context or hard data before writing the final report. "
            "Call multiple times across different rounds. "
            "Start with 1-2 exploratory agents if the topic is unfamiliar, "
            "then add more specific agents in later rounds as you learn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "agents": {
                    "type": "array",
                    "description": "Sub-agents to spawn this round",
                    "items": {
                        "type": "object",
                        "properties": {
                            "topic": {
                                "type": "string",
                                "description": "Specific research topic for this sub-agent"
                            },
                            "instructions": {
                                "type": "string",
                                "description": (
                                    "Which specific tools the sub-agent should use and what data to look for. "
                                    "Available: get_financial_data (Yahoo Finance — prices, history, news for assets), "
                                    "search_internet (web), scrape_webpage (read full articles), "
                                    "search_polymarket_markets, get_polymarket_orderbook, "
                                    "get_polymarket_price_history. "
                                    "Example: 'Use get_financial_data for BTC-USD current price and 30-day history. "
                                    "Use search_internet for recent Fed policy statements, then scrape_webpage "
                                    "on the most relevant articles.'"
                                )
                            }
                        },
                        "required": ["topic", "instructions"]
                    }
                }
            },
            "required": ["agents"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "spawn_visualization_agent",
        "description": (
            "Spawn a dedicated chart-generation agent to create data visualizations. "
            "Use AFTER you have gathered sufficient data through sub-agents. "
            "This agent receives a natural language description and returns structured "
            "chart configurations that will be rendered in the final report. "
            "Describe exactly what type of chart you want (line_chart, bar_chart, "
            "donut_chart, depth_chart), what data to plot, and what the axes/labels "
            "should be. Cite specific numbers from sub-agent findings. "
            "The visualization agent does NOT count toward your research agent cap."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "request": {
                    "type": "string",
                    "description": (
                        "Natural language description of the chart to generate. Include: "
                        "chart type, title, labels, data values, and any styling hints. "
                        "Example: 'Create a bar_chart titled Mexico vs Czechia Comparison "
                        "with labels FIFA Ranking, Points, Goals Scored and values 12/6/3 "
                        "for Mexico and 43/1/1 for Czechia.'"
                    )
                }
            },
            "required": ["request"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "spawn_mispricing_agent",
        "description": (
            "Generate a structured mispricing analysis from sub-agent research data. "
            "Returns a list of outcomes with market price vs fair value, edge percentage, "
            "and BUY/SELL/HOLD signals. Call this AFTER you have gathered sufficient "
            "sub-agent reports. This agent does NOT count toward your research agent cap. "
            "You can call this multiple times if you have many outcomes (max per session is configurable)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "context": {
                    "type": "string",
                    "description": (
                        "Structured description of outcomes to analyze. Format:\n"
                        "For each outcome, write EXACTLY:\n"
                        "OUTCOME: <exact outcome name>\n"
                        "MARKET PRICE: $<decimal>\n"
                        "FINDINGS: <2-3 sentences from sub-agents>\n\n"
                        "Example for binary markets:\n"
                        "OUTCOME: YES (Republican holds Senate majority)\n"
                        "MARKET PRICE: $0.56\n"
                        "FINDINGS: Sub-agent #2 found 538 gives Republicans 51% chance. "
                        "Sub-agent #4 found polling averages show tight races in 3 key states.\n\n"
                        "OUTCOME: NO (Democrats win Senate majority)\n"
                        "MARKET PRICE: $0.44\n"
                        "FINDINGS: Sub-agent #2 found Democratic fundraising up 40% YoY. "
                        "Sub-agent #4 found Democrat incumbents lead in PA, WI by 3+ points.\n\n"
                        "Example for multi-outcome:\n"
                        "OUTCOME: Brazil wins 2026 World Cup\n"
                        "MARKET PRICE: $0.22\n"
                        "FINDINGS: Sub-agent #3 found deep squad, 4 consecutive wins, no injuries.\n\n"
                        "IMPORTANT: For binary markets, ALWAYS label outcomes as "
                        "'YES (<what yes means>)' and 'NO (<what no means>)'. "
                        "NEVER use double-negations like 'Democratic NO'."
                    )
                }
            },
            "required": ["context"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "write_report",
        "description": (
            "Write your markdown analysis. Call this to draft your report — "
            "you can call it multiple times to rewrite (last write wins). "
            "This does NOT submit the report — use publish_final_report to submit. "
            "MANDATORY: must be called at least once before publish_final_report."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "markdown_report": {
                    "type": "string",
                    "description": (
                        "Structured analysis in markdown with these REQUIRED sections:\n"
                        "## Key Findings — specific numbers, dates, and facts from sub-agents.\n"
                        "## Evidence Analysis — evaluate the quality and consistency of evidence.\n"
                        "## Market Assessment — compare market price vs true probability.\n"
                        "## Actionable Advice — based on mispricing edge percentages.\n"
                        "## Conclusion — final VERDICT. Take a clear stance.\n"
                        "CRITICAL: PLAIN MARKDOWN ONLY. No <invoke>, <parameter>, <tool_call>."
                    )
                }
            },
            "required": ["markdown_report"]
        }
    }
}, {
    "type": "function",
    "function": {
        "name": "publish_final_report",
        "description": (
            "PUBLISH your final report. SERVER-SIDE VALIDATION enforces that ALL "
            "of these were completed before accepting:\n"
            "1. spawn_visualization_agent called at least once (charts present)\n"
            "2. spawn_mispricing_agent called at least once (mispricing data present)\n"
            "3. write_report called at least once (markdown report written)\n"
            "If ANY is missing, publish is REJECTED with the list of missing items. "
            "Fix them and call publish_final_report again. Ends the session on success."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "conviction_score": {
                    "type": "number",
                    "description": (
                        "Optional directional score from -1.0 to +1.0. "
                        "If omitted, auto-derived from mispricing edge data."
                    )
                },
                "top_reports": {
                    "type": "array",
                    "description": "Optional. 0-based indices of most influential sub-agent reports. Default: first 3.",
                    "items": {"type": "integer"}
                }
            },
            "required": []
        }
    }
}]


async def _search_news_async(query: str) -> tuple[str, list[dict]]:
    """
    Ejecuta DuckDuckGo search via ddgs con rate limiting y retry.
    - threading.Semaphore global para solo 1 search a la vez (cross-event-loop)
    - 1.5s delay entre searches para evitar rate-limit
    - Hasta 2 reintentos si no hay resultados
    Returns: (formatted_text, sources_list)
      formatted_text: Markdown con [Title](url) — body
      sources_list: list of {"title": str, "url": str}
    """
    loop = asyncio.get_running_loop()

    def _search():
        try:
            with DDGS(timeout=30) as ddgs:
                results = list(ddgs.text(
                    query,
                    max_results=4,
                    safesearch='off'
                ))
        except Exception as e:
            logger.debug(f"DDGS search error for '{query[:60]}': {e}")
            return None, []
        if not results:
            return None, []
        formatted = "\n".join(
            f"- [{r.get('title', 'Untitled')}]({r.get('href', '')}) — {r.get('body', '')}"
            for r in results
        )
        sources = []
        for r in results:
            href = r.get('href', '')
            if href:
                sources.append({"title": r.get('title', 'Untitled'), "url": href})
        return formatted, sources

    await loop.run_in_executor(None, SEARCH_SEM.acquire)
    try:
        for attempt in range(SEARCH_MAX_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(SEARCH_DELAY * attempt)
            try:
                formatted, sources = await asyncio.wait_for(
                    loop.run_in_executor(None, _search), timeout=90
                )
            except asyncio.TimeoutError:
                logger.debug(f"Search timed out for: {query[:60]}")
                formatted, sources = None, []
            except Exception as e:
                logger.debug(f"Search error for '{query[:60]}': {e}")
                formatted, sources = None, []
            if formatted is not None:
                return formatted, sources
            logger.debug(f"Search retry {attempt+1}/{SEARCH_MAX_RETRIES} for: {query[:60]}")

        logger.debug(f"Search exhausted retries for: {query[:60]}")
        return "No relevant news found for this query.", []
    finally:
        SEARCH_SEM.release()


def _fallback_summary_from_search_results(messages: list) -> str:
    results = []
    for m in messages:
        if m.get("role") == "tool" and m.get("content"):
            text = m["content"].strip()
            if text and text != "No relevant news found for this query.":
                results.append(text)
    if not results:
        return "No findings: all searches returned empty or were not executed."
    return "Raw search results (no structured summary available):\n\n" + "\n---\n".join(results)


_TOOL_CALL_XML_RE = re.compile(
    r'<\s*/?\s*(?:tool_call|invoke|function_call|parameter|xml_tag)[^>]*/?>|'
    r'<\s*\|?[A-Z_]+\|?\s*>|'
    r'```xml\s*<[^>]+>.*?</[^>]+>\s*```|'
    r'</?\s*function_call\s*>|'
    r'\|\s*(?:TOOL_CALL|END_TOOL_CALL|RESULT|END_RESULT)\s*\|'
    r'(?:\s*<\s*/?\s*(?:invoke|tool_call|parameter)[^>]*/?>)*',
    re.DOTALL | re.IGNORECASE
)

_TOOL_CALL_LINE_RE = re.compile(
    r'^\s*<\s*/?\s*(?:invoke|tool_call|function_call|parameter)\b.*$',
    re.MULTILINE | re.IGNORECASE
)

_TOOL_CALL_BLOCK_RE = re.compile(
    r'<\s*invoke\b[^>]*>.*?</\s*invoke\s*>|'
    r'<\s*tool_call\b[^>]*>.*?</\s*tool_call\s*>|'
    r'<\s*function_call\b[^>]*>.*?</\s*function_call\s*>',
    re.DOTALL | re.IGNORECASE
)

_TOOL_CALL_OPEN_END_RE = re.compile(
    r'<\s*invoke\b[^>]*>(?!.*?</\s*invoke\s*>).*$|'
    r'<\s*tool_call\b[^>]*>(?!.*?</\s*tool_call\s*>).*$|'
    r'<\s*function_call\b[^>]*>(?!.*?</\s*function_call\s*>).*$',
    re.DOTALL | re.IGNORECASE
)

_TOOL_CALL_JSON_RE = re.compile(
    r'\{\s*"name"\s*:\s*"(?:search_internet|search_polymarket|get_polymarket|submit_research|get_financial)'
    r'[^}]*\}(?:\s*\{(?:[^{}]|\{[^{}]*\})*\}\s*)*\}',
    re.DOTALL | re.IGNORECASE
)


_TOOL_CALL_FENCE_RE = re.compile(
    r'```json\s*\n?\{(?:[^{}]|\{[^{}]*\})*"name"\s*:\s*"[^"]*"(?:[^{}]|\{[^{}]*\})*\}\n?```',
    re.DOTALL | re.IGNORECASE
)


def _clean_subagent_report(text: str) -> str:
    if not text:
        return text
    cleaned = _TOOL_CALL_BLOCK_RE.sub('', text)
    cleaned = _TOOL_CALL_OPEN_END_RE.sub('', cleaned)
    cleaned = _TOOL_CALL_XML_RE.sub('', cleaned)
    cleaned = _TOOL_CALL_JSON_RE.sub('', cleaned)
    cleaned = _TOOL_CALL_FENCE_RE.sub('', cleaned)
    cleaned = _TOOL_CALL_LINE_RE.sub('', cleaned)
    cleaned = re.sub(r'<\s*/?\s*[a-zA-Z_]\w*(?:\s+[^>]*?)?\s*/?\s*>', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


def _resolve_top_reports(args: dict, all_subagent_reports: list) -> list:
    indices = args.get("top_reports", [])
    valid_indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(all_subagent_reports)]
    if valid_indices:
        return [all_subagent_reports[i] for i in valid_indices]
    fallback_count = min(3, len(all_subagent_reports))
    return [all_subagent_reports[i] for i in range(fallback_count)] if fallback_count else []


def _validate_visualizations(visualizations_raw: list) -> list:
    if not visualizations_raw:
        return []
    try:
        from .schemas import SynthesisResult
        from pydantic import ValidationError
        partial = {
            "fundamental_shift": 0.0,
            "rationale": "",
            "top_reports": [],
            "visualizations": visualizations_raw
        }
        validated = SynthesisResult.model_validate(partial)
        return [v.model_dump() for v in validated.visualizations]
    except (ValidationError, ImportError):
        return visualizations_raw if isinstance(visualizations_raw, list) else []


def _pm_fetch_sync(url: str, params: dict | None = None) -> dict | None:
    try:
        resp = requests.get(url, params=params, timeout=(30, 120))
        if resp.status_code == 429:
            time.sleep(2)
            resp = requests.get(url, params=params, timeout=(30, 60))
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        logger.debug(f"Polymarket API error: {url[:80]} — {e}")
        return None


async def _pm_fetch(url: str, params: dict | None = None) -> dict | None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, PM_API_SEM.acquire)
    try:
        return await loop.run_in_executor(None, _pm_fetch_sync, url, params)
    finally:
        PM_API_SEM.release()


async def _pm_search_markets_async(query: str, limit: int = 5) -> tuple[str, list[dict]]:
    limit = min(max(1, limit), 10)
    data = await _pm_fetch(f"{GAMMA_BASE}/public-search", {"q": query, "active": "true"})
    if not data:
        return f"No markets found for '{query}' (API returned empty).", []

    markets = data if isinstance(data, list) else data.get("events", [])
    if not markets:
        return f"No active markets found for '{query}'.", []

    source_info = {"title": f"Polymarket Gamma API — /public-search?q={query}", "url": f"https://gamma-api.polymarket.com/public-search?q={query}"}

    lines = [f"Found {len(markets[:limit])} active markets for \"{query}\":"]
    for i, m in enumerate(markets[:limit]):
        title = m.get("title", m.get("question", "Unknown"))
        price = m.get("bestAsk", m.get("price", "?"))
        vol = m.get("volume24hr", m.get("volume", "?"))
        spread = m.get("spread", "?")
        clob_ids = m.get("clobTokenIds", [])
        token = clob_ids[0] if clob_ids else "?"
        token_display = token if isinstance(token, str) and len(token) < 30 else (token[:10] + "..." + token[-8:] if isinstance(token, str) and len(token) >= 30 else str(token))

        price_str = f"${float(price):.2f}" if isinstance(price, (int, float)) else str(price)
        vol_str = f"${float(vol):,.0f}" if isinstance(vol, (int, float)) else str(vol)
        sp_str = f"{float(spread):.3f}" if isinstance(spread, (int, float)) else str(spread)

        lines.append(f"{i+1}. \"{title}\" | YES: {price_str} | Vol 24h: {vol_str} | Spread: {sp_str} | token: {token_display}")

    return "\n".join(lines), [source_info]


async def _pm_get_orderbook_async(token_id: str) -> tuple[str, list[dict]]:
    if not token_id or not token_id.strip():
        return "Error: No token_id provided.", []

    data = await _pm_fetch(f"{CLOB_BASE}/book", {"token_id": token_id.strip()})
    if not data:
        return f"Order book not available for token {token_id[:8]}... (API error or not found).", []

    source_info = {"title": f"Polymarket CLOB API — /book?token_id={token_id[:8]}...", "url": f"https://clob.polymarket.com/book?token_id={token_id}"}

    bids = data.get("bids", [])
    asks = data.get("asks", [])

    best_bid = float(bids[0][0]) if bids else 0.0
    best_ask = float(asks[0][0]) if asks else 0.0
    best_bid_size = float(bids[0][1]) if bids else 0.0
    best_ask_size = float(asks[0][1]) if asks else 0.0

    bid_volume = sum(float(b[1]) for b in bids[:10])
    ask_volume = sum(float(a[1]) for a in asks[:10])
    total = bid_volume + ask_volume
    imbalance = round((bid_volume - ask_volume) / total, 2) if total > 0 else 0.0
    spread_val = round(best_ask - best_bid, 4) if best_ask > 0 and best_bid > 0 else 0.0
    spread_pct = round(spread_val / best_ask * 100, 1) if best_ask > 0 else 0.0

    direction = "buy pressure" if imbalance > 0.05 else ("sell pressure" if imbalance < -0.05 else "neutral")
    sign = "+" if imbalance >= 0 else ""

    top_bids = ", ".join(f"${float(b[0]):.2f} ({float(b[1]):,.0f})" for b in bids[:3])
    top_asks = ", ".join(f"${float(a[0]):.2f} ({float(a[1]):,.0f})" for a in asks[:3])

    lines = [
        f"Order book for token {token_id[:8]}... (YES side):",
        f"Best Bid: ${best_bid:.2f} (size: {best_bid_size:,.0f} shares)",
        f"Best Ask: ${best_ask:.2f} (size: {best_ask_size:,.0f} shares)",
        f"Spread: ${spread_val:.4f} ({spread_pct}%)",
        f"Order book imbalance: {sign}{imbalance} ({direction})",
        f"Top bids: {top_bids}" if top_bids else "Top bids: none",
        f"Top asks: {top_asks}" if top_asks else "Top asks: none",
    ]

    return "\n".join(lines), [source_info]


async def _pm_get_price_history_async(token_id: str, days: int = 7) -> tuple[str, list[dict]]:
    if not token_id or not token_id.strip():
        return "Error: No token_id provided.", []

    data = await _pm_fetch(
        f"{CLOB_BASE}/prices-history",
        {"market": token_id.strip(), "interval": "max", "fidelity": 1440}
    )
    if not data or not isinstance(data, list) or len(data) == 0:
        return f"No price history available for token {token_id[:8]}... (market may not have historical data).", []

    source_info = {"title": f"Polymarket CLOB API — /prices-history?market={token_id[:8]}...", "url": f"https://clob.polymarket.com/prices-history?market={token_id}"}

    history = data.get("history", data) if isinstance(data, dict) else data
    if isinstance(history, dict):
        history = history.get("history", history.get("prices", []))

    if isinstance(history, list):
        entries = history[-min(days, 30):]
    else:
        return f"Price history for token {token_id[:8]}... is in an unexpected format.", [source_info]

    if not entries:
        return f"Price history for token {token_id[:8]}... is empty.", [source_info]

    first_price = float(entries[0].get("p", entries[0].get("price", 0)))
    last_price = float(entries[-1].get("p", entries[-1].get("price", 0)))

    lines = [f"Price history for token {token_id[:8]}... (last {len(entries)} days):"]
    prev = None
    total = len(entries)
    for j, e in enumerate(entries):
        price = float(e.get("p", e.get("price", 0)))
        day_idx = total - j
        if prev is not None and prev > 0:
            chg = round((price - prev) / prev * 100, 1)
            sign = "+" if chg >= 0 else ""
            lines.append(f"  Day -{day_idx}: ${price:.2f} ({sign}{chg}%)")
        else:
            lines.append(f"  Day -{day_idx}: ${price:.2f}")
        prev = price

    if first_price > 0:
        total_chg = round((last_price - first_price) / first_price * 100, 1)
        sign = "+" if total_chg >= 0 else ""
    else:
        total_chg = 0.0
        sign = ""

    volatility = round(sum(
        abs(float(entries[i].get("p", entries[i].get("price", 0))) -
            float(entries[i-1].get("p", entries[i-1].get("price", 0))))
        / float(entries[i-1].get("p", entries[i-1].get("price", 1)))
        for i in range(1, len(entries))
        if float(entries[i-1].get("p", entries[i-1].get("price", 0))) > 0
    ) / max(1, len(entries) - 1) * 100, 1)

    lines.append(f"{len(entries)}-day change: {sign}{total_chg}% | Volatility: +-{volatility}% daily")

    return "\n".join(lines), [source_info]


async def _run_subagent(
    topic: str,
    question: str,
    sem: asyncio.Semaphore,
    call_counter: list,
    on_event: Callable[[str, dict], None] | None = None,
    agent_idx: int = 0,
    max_steps: int = 3,
    subagent_model: str = "deepseek-v4-flash",
    cancel_event: threading.Event | None = None,
    instructions: str = ""
) -> dict:
    """
    Sub-agente investigador con bucle ReAct autonomo.

    - Tiene acceso a la herramienta 'search_internet' via Tool Calling nativo.
    - Decide que buscar, lee resultados, y refina iterativamente.
    - Maximo max_steps pasos (configurable). Si se alcanza, emite el informe con lo acumulado.
    - Si on_event esta presente, emite 'subagent_step' en cada busqueda.
    - System prompt: solo datos duros. Prohibido especular o estimar probabilidades.
    """
    async with sem:
        if cancel_event and cancel_event.is_set():
            return {"topic": topic, "report": "CANCELLED", "steps_used": 0,
                    "sources": [], "cancelled": True}
        now = _now_str()
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a factual research sub-agent. Your ONLY job is to find "
                    "hard data, statistics, official statements, and verifiable facts "
                    "related to your assigned research topic. "
                    "You have access to: search_internet (web search), search_polymarket_markets "
                    "(find prediction markets), get_polymarket_token_id (get CLOB token IDs "
                    "for a market — use BEFORE orderbook/price tools), "
                    "get_polymarket_orderbook (market depth), "
                    "get_polymarket_price_history (price trends), "
                    "get_financial_data (Yahoo Finance: stock/crypto/commodity prices, "
                    "history, fundamentals, news — use for HARD price data instead of web search), "
                    "scrape_webpage (read FULL article content from a URL — use AFTER "
                    "search_internet to read a promising article completely, not just its snippet), "
                    "and submit_research_report (submit your final report). "
                    f"CURRENT DATE: {now}. All your research must be grounded in "
                    "this date. When searching, use this year as reference. "
                    "RULES:\n"
                    "- Do NOT give investment advice or trading recommendations.\n"
                    "- Cite specific numbers, dates, and sources when available.\n"
                    "- You MAY note directional implications when hard data clearly supports them "
                    "(e.g., 'All 5 polls show candidate X ahead by 4+ points'). "
                    "Do not invent narratives — only note patterns visible in the data.\n"
                    "- TOOL SELECTION GUIDE:\n"
                    "  * For stock/crypto/commodity/index PRICES and hard financial data "
                    "(market cap, PE ratio, OHLCV history, recent news about a specific asset): "
                    "use get_financial_data. Do NOT use search_internet for price data.\n"
                    "  * For Polymarket order books, price history, or market listings: "
                    "use get_polymarket_orderbook, get_polymarket_price_history, or "
                    "search_polymarket_markets.\n"
                    "  * After using search_internet, if you find a highly relevant article "
                    "(judging by title/snippet), use scrape_webpage to read the FULL text. "
                    "Limit to 3 scrapes per session. Prioritize authoritative sources.\n"
                    "  * For general news, political events, regulatory updates, or information "
                    "NOT about a specific traded asset: use search_internet.\n"
                    "  * If your topic mentions a specific ticker (BTC-USD, AAPL, GC=F, ^GSPC, EURUSD=X): "
                    "ALWAYS call get_financial_data FIRST to get the hard numbers.\n"
                    + (f"- You may search up to {max_steps} times.\n" if max_steps > 0 else
                    "- You have unlimited search steps. Use them wisely — stop when ready.\n"
                    "  SELF-REGULATION: After each tool call, ask yourself:\n"
                    "  - Can I already answer the main agent's question with specific data?\n"
                    "  - Do I have at least 3 concrete facts (numbers, dates, names)?\n"
                    "  - Would another tool call likely find the SAME information I already have?\n"
                    "  If ANY of these is 'yes', write your report NOW. "
                    "A good 4-step report beats a mediocre 10-step report.\n") +
                    "- Each search should cover NEW ground — do NOT rephrase previous queries.\n"
                    "- When you have enough hard data, call submit_research_report with your "
                    "4-6 sentence factual summary. You can report at any step.\n"
                    "- Do NOT keep searching after you have sufficient facts.\n"
                    "- Prefer quality over quantity: 2 well-researched facts > 10 vague searches.\n"
                    "- If you MUST search more, make it a single, precise query for the specific "
                    "data you are missing. Do NOT search for confirmation.\n"
                    "- Your report is PLAIN TEXT. Do NOT include any XML tags, HTML tags, "
                    "<invoke>, <parameter>, or tool-call syntax of any kind. "
                    "Write only natural language sentences."
                )
            },
            {
                "role": "user",
                "content": (
                    f"CURRENT DATE: {now}\n\n"
                    f"MARKET QUESTION: {question}\n\n"
                    f"YOUR RESEARCH TOPIC: {topic}\n\n"
                    + (f"RESEARCH INSTRUCTIONS: {instructions}\n\n" if instructions else "") +
                    f"Use the most appropriate tool for your research topic. "
                    f"For asset prices, stock data, crypto, or commodities: use "
                    f"get_financial_data. For Polymarket markets: use the Polymarket "
                    f"tools. For general news and political events: use search_internet."
                )
            }
        ]

        collected_sources: list[dict] = []

        step = 0
        while True:
            if max_steps > 0 and step >= max_steps:
                break
            if cancel_event and cancel_event.is_set():
                content = _fallback_summary_from_search_results(messages)
                return {
                    "topic": topic,
                    "report": _clean_subagent_report(f"[CANCELLED] {content or 'No findings.'}"),
                    "steps_used": step,
                    "sources": collected_sources,
                    "cancelled": True
                }
            if on_event:
                on_event("subagent_thinking", {
                    "id": agent_idx,
                    "step": step + 1
                })
            is_last_step = max_steps > 0 and step == max_steps - 1
            try:
                kwargs = dict(
                    model=subagent_model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=200000,
                    extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}}
                )
                if is_last_step:
                    kwargs["tool_choice"] = "none"
                    messages.append({
                        "role": "user",
                        "content": (
                            "FINAL STEP: You must now synthesize everything you've learned "
                            "and write your final report. Based on ALL data gathered so far, "
                            "produce 4-6 dense, factual sentences. Do NOT request more tools."
                        )
                    })
                else:
                    kwargs["tools"] = subagent_tools
                resp = await asyncio.wait_for(
                    _get_client().chat.completions.create(**kwargs),
                    timeout=45
                )
            except asyncio.TimeoutError:
                logger.warning(f"Sub-agent {topic[:40]} timed out at step {step+1}")
                content = _fallback_summary_from_search_results(messages) if messages else ""
                return {
                    "topic": topic,
                    "report": _clean_subagent_report(content or "Research timed out during step — insufficient data gathered."),
                    "steps_used": step + 1,
                    "sources": collected_sources,
                    "forced_summary": True,
                    "timed_out": True
                }
            msg = resp.choices[0].message

            reasoning = (getattr(msg, 'reasoning_content', None) or "").strip()
            if reasoning and on_event:
                on_event("subagent_reasoning", {
                    "id": agent_idx,
                    "step": step + 1,
                    "content": reasoning
                })

            sanitized = {
                "role": msg.role,
                "content": msg.content,
            }
            if msg.tool_calls:
                sanitized["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments
                        }
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(sanitized)

            if msg.tool_calls:
                for tool in msg.tool_calls:
                    if tool.function.name == "search_internet":
                        args = json.loads(tool.function.arguments)
                        query = args.get("query", topic)
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "search_internet",
                                "label": "Web Search",
                                "query": query
                            })
                        results_text, results_sources = await _search_news_async(query)
                        collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "search_internet",
                                "label": "Web Search",
                                "query": query,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "search_polymarket_markets":
                        args = json.loads(tool.function.arguments)
                        query = args.get("query", "")
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "search_polymarket_markets",
                                "label": "Polymarket Search",
                                "query": query
                            })
                        results_text, results_sources = await _pm_search_markets_async(
                            query,
                            args.get("limit", 5)
                        )
                        collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "search_polymarket_markets",
                                "label": "Polymarket Search",
                                "query": query,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "get_polymarket_orderbook":
                        args = json.loads(tool.function.arguments)
                        token_id = args.get("token_id", "")
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_orderbook",
                                "label": "Order Book",
                                "query": token_id[:16] + "..."
                            })
                        results_text, results_sources = await _pm_get_orderbook_async(token_id)
                        collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_orderbook",
                                "label": "Order Book",
                                "query": token_id,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "get_polymarket_price_history":
                        args = json.loads(tool.function.arguments)
                        token_id = args.get("token_id", "")
                        days = args.get("days", 7)
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_price_history",
                                "label": "Price History",
                                "query": f"{days}d"
                            })
                        results_text, results_sources = await _pm_get_price_history_async(token_id, days)
                        collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_price_history",
                                "label": "Price History",
                                "query": token_id,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "get_polymarket_token_id":
                        args = json.loads(tool.function.arguments)
                        query = args.get("query", "")
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_token_id",
                                "label": "Polymarket Token Lookup",
                                "query": query
                            })
                        results_text, results_sources = await _pm_search_markets_async(query, limit=3)
                        if results_sources:
                            collected_sources.extend(results_sources)
                        token_lines = []
                        data = await _pm_fetch(f"{GAMMA_BASE}/public-search", {"q": query, "active": "true"})
                        if data:
                            markets = data if isinstance(data, list) else data.get("events", [])
                            for m in markets[:3]:
                                title = m.get("title", m.get("question", "?"))
                                clob_ids = m.get("clobTokenIds", [])
                                if clob_ids:
                                    token_lines.append(f"Market: \"{title}\"")
                                    token_lines.append(f"  YES token: {clob_ids[0]}")
                                    if len(clob_ids) > 1:
                                        token_lines.append(f"  NO token: {clob_ids[1]}")
                                    token_lines.append("")
                        token_text = "\n".join(token_lines) if token_lines else f"No token IDs found for '{query}'. Try a more specific query."
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": token_text[:60000]
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_polymarket_token_id",
                                "label": "Token Lookup",
                                "query": query,
                                "results": token_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "get_financial_data":
                        args = json.loads(tool.function.arguments)
                        symbol = args.get("symbol", "")
                        action = args.get("action", "quote")
                        range_val = args.get("range", "1mo")
                        symbols_list = args.get("symbols", None)
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_financial_data",
                                "label": "Yahoo Finance",
                                "query": f"{symbol} ({action})"
                            })
                        results_text = await _get_financial_data_async(symbol, action, range_val, symbols_list)
                        results_sources = [{"title": f"Yahoo Finance — {symbol} ({action})", "url": f"https://finance.yahoo.com/quote/{symbol}"}]
                        collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text[:60000]
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "get_financial_data",
                                "label": "Yahoo Finance",
                                "query": symbol,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "scrape_webpage":
                        args = json.loads(tool.function.arguments)
                        url = args.get("url", "")
                        if on_event:
                            on_event("subagent_tool_start", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "scrape_webpage",
                                "label": "Scraped Article",
                                "query": url
                            })
                        if not url or not url.startswith(("http://", "https://")):
                            results_text = f"ERROR: Invalid URL '{url}'. Must be a full URL starting with http:// or https://"
                            results_sources = []
                        else:
                            results_text = await _scrape_webpage_async(url)
                            results_sources = [{"title": url, "url": url}]
                            if results_text and not results_text.startswith("Error") and not results_text.startswith("Failed") and not results_text.startswith("Timeout"):
                                collected_sources.extend(results_sources)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool.id,
                            "content": results_text[:60000]
                        })
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "scrape_webpage",
                                "label": "Scraped Article",
                                "query": url,
                                "results": results_text,
                                "sources": results_sources
                            })
                    elif tool.function.name == "submit_research_report":
                        args = json.loads(tool.function.arguments)
                        report = args.get("report", "")
                        cleaned = _clean_subagent_report(report or "No findings.")
                        if on_event:
                            on_event("subagent_tool_result", {
                                "id": agent_idx,
                                "step": step + 1,
                                "tool": "submit_research_report",
                                "label": "Final Report",
                                "query": "submit",
                                "results": cleaned,
                                "sources": collected_sources
                            })
                        return {
                            "topic": topic,
                            "report": cleaned,
                            "steps_used": step + 1,
                            "sources": collected_sources
                        }
            else:
                content = msg.content
                if not content or len(content.strip()) < 15:
                    content = _fallback_summary_from_search_results(messages)
                return {
                    "topic": topic,
                    "report": _clean_subagent_report(content or "No findings."),
                    "steps_used": step + 1,
                    "sources": collected_sources
                }

            if is_last_step:
                break
            step += 1

        if max_steps > 0:
            messages.append({
            "role": "user",
            "content": (
                "You have reached the maximum number of research steps. "
                "Based on ALL the information gathered so far, produce a "
                "dense factual summary of your findings in 4-6 sentences. "
                "Do NOT request more searches. Produce your final summary NOW."
            )
        })
        try:
            final_resp = await asyncio.wait_for(
                _get_client().chat.completions.create(
                    model=subagent_model,
                    messages=messages,
                    tool_choice="none",
                    temperature=0.1,
                    max_tokens=200000,
                    extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}}
                ),
                timeout=30
            )
            content = final_resp.choices[0].message.content or ""
        except (Exception, asyncio.TimeoutError):
            try:
                final_resp = await asyncio.wait_for(
                    _get_client().chat.completions.create(
                        model=subagent_model,
                        messages=messages,
                        tool_choice="none",
                        temperature=0.0,
                        max_tokens=200000,
                        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}}
                    ),
                    timeout=30
                )
                content = final_resp.choices[0].message.content or ""
            except (Exception, asyncio.TimeoutError):
                content = ""

        if not content or len(content.strip()) < 20:
            logger.debug(
                f"Sub-agent {topic[:40]} forced summary empty or too short "
                f"(len={len(content)}), retrying with temp 0"
            )
            try:
                retry_resp = await asyncio.wait_for(
                    _get_client().chat.completions.create(
                        model=subagent_model,
                        messages=messages,
                        tool_choice="none",
                        temperature=0.0,
                        max_tokens=200000,
                        extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}}
                    ),
                    timeout=30
                )
                content = retry_resp.choices[0].message.content or ""
            except (Exception, asyncio.TimeoutError):
                content = ""
        if not content or len(content.strip()) < 20:
            logger.warning(
                f"Sub-agent {topic[:40]} forced summary still empty, "
                f"using fallback from search results"
            )
            content = _fallback_summary_from_search_results(messages)
        return {
            "topic": topic,
            "report": _clean_subagent_report(content or "No findings."),
            "steps_used": max_steps,
            "forced_summary": True,
            "sources": collected_sources
        }


def _fmt_billions(val: float) -> str:
    if not val:
        return "N/A"
    abs_v = abs(val)
    if abs_v >= 1e12:
        return f"{val/1e12:.2f}T"
    elif abs_v >= 1e9:
        return f"{val/1e9:.2f}B"
    elif abs_v >= 1e6:
        return f"{val/1e6:.2f}M"
    return f"{val:,.0f}"


def _fetch_quote(symbol: str) -> str:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.info
        fast = t.fast_info
        price = fast.get("lastPrice") or info.get("currentPrice") or info.get("regularMarketPrice", 0)
        prev = fast.get("previousClose") or info.get("previousClose") or info.get("regularMarketPreviousClose", price)
        change = price - prev if price and prev else 0
        change_pct = (change / prev * 100) if prev else 0
        day_high = fast.get("dayHigh") or info.get("dayHigh", 0)
        day_low = fast.get("dayLow") or info.get("dayLow", 0)
        volume = fast.get("lastVolume") or info.get("volume") or info.get("regularMarketVolume", 0)
        market_cap = fast.get("marketCap") or info.get("marketCap", 0)
        lines = [
            f"Quote for {symbol}:",
            f"  Current price: ${price:,.2f}" if price else f"  Current price: N/A",
            f"  Previous close: ${prev:,.2f}" if prev else "",
            f"  Day change: {'+' if change > 0 else ''}{change:,.2f} ({'+' if change > 0 else ''}{change_pct:.2f}%)" if prev else "",
            f"  Day range: ${day_low:,.2f} - ${day_high:,.2f}" if day_low and day_high else "",
        ]
        if volume:
            lines.append(f"  Volume: {volume:,.0f}")
        if market_cap:
            lines.append(f"  Market cap: ${_fmt_billions(market_cap)}")
        return "\n".join(l for l in lines if l)
    except Exception as e:
        return f"Error fetching quote for {symbol}: {str(e)}"


def _fetch_history(symbol: str, range_val: str) -> str:
    try:
        import yfinance as yf
        import pandas as pd
        t = yf.Ticker(symbol)
        df = t.history(period=range_val)
        if df.empty:
            return f"No historical data found for {symbol} ({range_val})."
        if len(df) > 20:
            df = df.iloc[::max(1, len(df) // 20)].head(20)
        lines = [f"{symbol} Price History ({range_val}):"]
        lines.append(f"{'Date':<12} {'Open':>10} {'High':>10} {'Low':>10} {'Close':>10} {'Volume':>10}")
        lines.append("-" * 62)
        for idx, row in df.iterrows():
            date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, 'strftime') else str(idx)[:10]
            lines.append(
                f"{date_str:<12} ${row['Open']:>9,.2f} ${row['High']:>9,.2f} "
                f"${row['Low']:>9,.2f} ${row['Close']:>9,.2f} {row['Volume']:>10,.0f}"
            )
        start = df.iloc[0]["Close"]
        end = df.iloc[-1]["Close"]
        change_pct = ((end - start) / start * 100) if start else 0
        high = df["High"].max()
        low = df["Low"].min()
        avg_vol = df["Volume"].mean()
        std_close = df["Close"].std()
        avg_price = df["Close"].mean()
        lines.append("")
        lines.append("Period stats:")
        lines.append(f"  Start: ${start:,.2f} -> End: ${end:,.2f} ({'+' if change_pct > 0 else ''}{change_pct:.1f}%)")
        lines.append(f"  High: ${high:,.2f}  Low: ${low:,.2f}")
        lines.append(f"  Avg daily volume: {avg_vol:,.0f}")
        if avg_price:
            lines.append(f"  Volatility (std dev): ${std_close:,.2f} ({std_close/avg_price*100:.1f}% of avg price)")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching history for {symbol}: {str(e)}"


def _fetch_news(symbol: str) -> str:
    try:
        import yfinance as yf
        from datetime import datetime
        t = yf.Ticker(symbol)
        news = t.news[:8] if t.news else []
        if not news:
            return f"No recent news for {symbol}."
        lines = [f"Recent news for {symbol}:"]
        for i, n in enumerate(news, 1):
            title = n.get("title", n.get("content", {}).get("title", "Untitled"))
            publisher = n.get("publisher", n.get("content", {}).get("provider", {}).get("displayName", ""))
            pub_time = ""
            content = n.get("content", {})
            ts = content.get("providerPublishTime")
            if ts:
                try:
                    pub_time = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                except (TypeError, ValueError, OSError):
                    pass
            lines.append(f"{i}. \"{title}\" ({publisher}, {pub_time})" if pub_time else f"{i}. \"{title}\" ({publisher})")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching news for {symbol}: {str(e)}"


def _fetch_fundamentals(symbol: str) -> str:
    try:
        import yfinance as yf
        t = yf.Ticker(symbol)
        info = t.info
        fast = t.fast_info
        market_cap = fast.get("marketCap") or info.get("marketCap")
        pe = info.get("trailingPE") or info.get("forwardPE")
        sector = info.get("sector", "")
        industry = info.get("industry", "")
        desc = info.get("longBusinessSummary", "")
        lines = [f"Fundamentals for {symbol}:"]
        if sector:
            label = f"{sector}" + (f" - {industry}" if industry else "")
            lines.append(f"  Sector: {label}")
        if market_cap:
            lines.append(f"  Market cap: ${_fmt_billions(market_cap)}")
        if pe:
            lines.append(f"  PE ratio (TTM): {pe:.1f}")
        if info.get("forwardPE"):
            lines.append(f"  Forward PE: {info['forwardPE']:.1f}")
        if info.get("trailingEps"):
            lines.append(f"  EPS (TTM): ${info['trailingEps']:.2f}")
        if info.get("dividendYield"):
            lines.append(f"  Dividend yield: {info['dividendYield'] * 100:.2f}%")
        if info.get("beta"):
            lines.append(f"  Beta: {info['beta']:.2f}")
        if info.get("totalRevenue"):
            lines.append(f"  Revenue (TTM): ${_fmt_billions(info['totalRevenue'])}")
        if info.get("profitMargins"):
            lines.append(f"  Profit margin: {info['profitMargins'] * 100:.1f}%")
        year_low = fast.get("yearLow") or info.get("fiftyTwoWeekLow")
        year_high = fast.get("yearHigh") or info.get("fiftyTwoWeekHigh")
        if year_low and year_high:
            lines.append(f"  52-week range: ${year_low:,.2f} - ${year_high:,.2f}")
        if desc:
            desc_short = desc[:300] + "..." if len(desc) > 300 else desc
            lines.append(f"  Description: {desc_short}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error fetching fundamentals for {symbol}: {str(e)}"


def _fetch_compare(symbols: list[str], range_val: str) -> str:
    try:
        import yfinance as yf
        import pandas as pd
        df = yf.download(symbols, period=range_val, progress=False)
        if df.empty:
            return "No data for comparison."
        closes = df["Close"]
        if isinstance(closes, pd.Series):
            closes = closes.to_frame()
        lines = [f"Price comparison ({range_val} % change):"]
        best_pct = -999
        best_sym = ""
        for sym in symbols:
            if sym in closes.columns:
                series = closes[sym].dropna()
                if len(series) >= 2:
                    start = series.iloc[0]
                    end = series.iloc[-1]
                    change_pct = (end - start) / start * 100
                    lines.append(f"  {sym}: ${start:,.2f} -> ${end:,.2f} ({'+' if change_pct > 0 else ''}{change_pct:.1f}%)")
                    if change_pct > best_pct:
                        best_pct = change_pct
                        best_sym = sym
                else:
                    lines.append(f"  {sym}: insufficient data")
            else:
                lines.append(f"  {sym}: no data in range")
        if best_sym:
            lines.append(f"\nWinner by momentum: {best_sym} ({'+' if best_pct > 0 else ''}{best_pct:.1f}%)")
        return "\n".join(lines)
    except Exception as e:
        return f"Error comparing symbols: {str(e)}"


async def _get_financial_data_async(symbol: str, action: str,
                                     range_val: str = "1mo",
                                     symbols: list[str] | None = None) -> str:
    loop = asyncio.get_running_loop()

    def _fetch():
        try:
            if action == "compare" and symbols and len(symbols) > 1:
                return _fetch_compare(symbols, range_val)
            elif action == "quote":
                return _fetch_quote(symbol)
            elif action == "history":
                return _fetch_history(symbol, range_val)
            elif action == "news":
                return _fetch_news(symbol)
            elif action == "fundamentals":
                return _fetch_fundamentals(symbol)
            else:
                return f"Unknown action: {action}"
        except Exception as e:
            logger.warning(f"yfinance error for {symbol}/{action}: {e}")
            return f"Error fetching data for {symbol}: {str(e)}"

    return await loop.run_in_executor(None, _fetch)


async def _scrape_webpage_async(url: str) -> str:
    """
    Scrape a URL and return clean markdown using crawl4ai.
    Uses headless Chromium for JS rendering, PruningContentFilter to remove
    nav/sidebars/ads, and BrowserConfig for a custom User-Agent.
    """
    try:
        from crawl4ai import AsyncWebCrawler, CacheMode
        from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig
        from crawl4ai.content_filter_strategy import PruningContentFilter
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
    except ImportError as e:
        return f"Error: crawl4ai not available ({e}). Cannot scrape {url}."

    browser_config = BrowserConfig(
        headless=True,
        user_agent="Mozilla/5.0 (compatible; NekoResearch/1.0)",
        verbose=False
    )

    md_generator = DefaultMarkdownGenerator(
        content_filter=PruningContentFilter(threshold=0.48, min_word_threshold=50)
    )

    run_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=15000,
        word_count_threshold=10,
        exclude_external_links=True,
        remove_overlay_elements=True,
        markdown_generator=md_generator,
    )

    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=url, config=run_config),
                timeout=20
            )
    except asyncio.TimeoutError:
        return f"Timeout: {url} took too long to respond (>20s). Try a different URL."
    except Exception as e:
        logger.warning(f"crawl4ai error for {url}: {e}")
        return f"Error scraping {url}: {str(e)[:200]}"

    if not result or not result.success:
        error_msg = getattr(result, 'error_message', 'Unknown error') if result else 'No result'
        return f"Failed to scrape {url}: {error_msg}"

    if result.markdown:
        if isinstance(result.markdown, str):
            content = result.markdown
        else:
            content = (
                getattr(result.markdown, 'fit_markdown', '') or
                getattr(result.markdown, 'raw_markdown', '') or
                str(result.markdown)
            )
    else:
        content = result.text or ""

    if not content.strip():
        return f"Scraped {url} but got empty content. The page may be behind a paywall or require JavaScript."

    max_chars = 8000
    if len(content) > max_chars:
        content = content[:max_chars] + f"\n\n[... truncated at {max_chars} characters, {len(content) - max_chars} more ...]"

    metadata = result.metadata or {}
    title = metadata.get('title', '') or getattr(result, 'title', '') or ''

    lines = [f"Content from: {url}"]
    if title:
        lines.append(f"Title: {title}")
    lines.append(f"\n{content}")

    return "\n".join(lines)


CHART_SCHEMA_PROMPT = (
    "You are a chart generation assistant. Given a natural language request and data, "
    "output a valid JSON array of chart objects.\n\n"
    "AVAILABLE CHART TYPES:\n"
    "1. line_chart: { type: 'line_chart', title: string, xAxisLabel?: string, yAxisLabel?: string, "
    "data: [{name: string, series1: number, series2?: number, ...}] }\n"
    "2. bar_chart: { type: 'bar_chart', title: string, xAxisLabel?: string, "
    "data: [{name: string, series1: number, series2?: number, ...}] }\n"
    "3. donut_chart: { type: 'donut_chart', title: string, "
    "data: [{label: string, value: number}] }\n"
    "4. depth_chart: { type: 'depth_chart', title: string, "
    "data: [{price: number, bid_size: number, ask_size: number}] }\n\n"
    "CRITICAL RULES:\n"
    "- Output ONLY a JSON array. No explanations, no markdown outside the fence.\n"
    "- Wrap the JSON in a ```json code fence.\n"
    "- ALWAYS produce at least 1 chart, even with estimated data. "
    "If the request gives no numbers, invent reasonable example values.\n"
    "- line_chart/bar_chart: 'name' is the x-axis label, remaining keys are numeric series.\n"
    "- donut_chart: each entry has 'label' (string) and 'value' (number > 0).\n"
    "- depth_chart: each entry has 'price', 'bid_size', 'ask_size' (all numbers).\n"
    "- Use descriptive keys: 'Mexico', 'Czechia', 'Probability' — not 'A', 'B'.\n"
    "- If you absolutely cannot create any chart, output: []"
)


async def _run_viz_agent(request: str, model: str = "deepseek-v4-pro") -> list[dict]:
    messages = [
        {"role": "system", "content": CHART_SCHEMA_PROMPT},
        {"role": "user", "content": request},
    ]
    try:
        resp = await asyncio.wait_for(
            _get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=4000,
            ),
            timeout=30
        )
        content = resp.choices[0].message.content or ""
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning(f"Viz agent LLM call failed: {e}")
        return []

    parsed = _extract_json(content)
    if not parsed:
        return []

    charts = parsed if isinstance(parsed, list) else [parsed]
    validated: list[dict] = []
    for ch in charts:
        if not isinstance(ch, dict) or "type" not in ch:
            continue
        try:
            from .schemas import SynthesisResult
            from pydantic import ValidationError
            partial = {
                "fundamental_shift": 0.0, "rationale": "",
                "top_reports": [], "visualizations": [ch]
            }
            result = SynthesisResult.model_validate(partial)
            validated.extend(v.model_dump() for v in result.visualizations)
        except (ValidationError, ImportError):
            continue

    return validated[:4]


MISPRICING_PROMPT = (
    "You are a financial mispricing analyst. Given research data about prediction market "
    "outcomes, output a valid JSON object with this EXACT structure:\n\n"
    "{\n"
    '  "summary": "<1-2 sentence summary of which outcomes are mispriced>",\n'
    '  "picks": [\n'
    '    {\n'
    '      "outcome": "<name of the outcome>",\n'
    '      "market_price": <current market probability as decimal, e.g. 0.22>,\n'
    '      "fair_value": <your estimated true probability as decimal, e.g. 0.30>,\n'
    '      "edge_pct": <(fair_value - market_price) / market_price * 100, rounded to 1 decimal>,\n'
    '      "action": "<BUY if edge_pct > 10, SELL if edge_pct < -10, HOLD otherwise>",\n'
    '      "rationale": "<2-3 sentence justification citing specific research data>"\n'
    '    }\n'
    '  ]\n'
    '}\n\n'
    "RULES:\n"
    "- ONLY include outcomes that are explicitly mentioned in the context.\n"
    "- market_price MUST be exactly as stated in the context. Do NOT invent prices.\n"
    "- fair_value MUST be grounded in the research data provided.\n"
    "- If an outcome has insufficient data, set fair_value equal to market_price and action=HOLD.\n"
    "- Max 8 picks. Order by |edge_pct| descending (largest mispricing first).\n"
    "- For binary markets (YES/NO), include both sides.\n"
    "- edge_pct formula: (fair_value - market_price) / market_price * 100.\n"
    "- Wrap output in ```json fence.\n"
    "- Return PLAIN JSON. No thinking text, no XML tags.\n"
    "- OUTCOME NAMES: Use the EXACT outcome name from the context. "
    "Do NOT reinterpret, shorten, or invert the outcome name. "
    "If the context says 'YES (Republican holds Senate majority)', "
    "your outcome MUST be 'YES (Republican holds Senate majority)' — "
    "not 'Republican YES', not 'GOP win'. "
    "For binary markets, the context labels both sides as 'YES (...)' "
    "and 'NO (...)' — use those exact labels. "
    "Do NOT create double-negations like 'Democratic NO'."
)


async def _run_mispricing_agent(context: str, model: str = "deepseek-v4-pro") -> dict | None:
    messages = [
        {"role": "system", "content": MISPRICING_PROMPT},
        {"role": "user", "content": context},
    ]
    try:
        resp = await asyncio.wait_for(
            _get_client().chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.1,
                max_tokens=8000,
            ),
            timeout=30
        )
        content = resp.choices[0].message.content or ""
    except (Exception, asyncio.TimeoutError) as e:
        logger.warning(f"Mispricing agent LLM call failed: {e}")
        return None

    parsed = _extract_json(content)
    if not parsed or not isinstance(parsed, dict):
        return None

    picks = parsed.get("picks", [])
    if not isinstance(picks, list):
        return None

    valid_picks = []
    for p in picks:
        try:
            market_price = float(p.get("market_price", 0))
            fair_value = float(p.get("fair_value", 0))
            if not (0 < market_price < 1 and 0 < fair_value < 1):
                continue
            edge_pct = round((fair_value - market_price) / market_price * 100, 1)
            valid_picks.append({
                "outcome": str(p.get("outcome", ""))[:200],
                "market_price": market_price,
                "fair_value": fair_value,
                "edge_pct": edge_pct,
                "action": str(p.get("action", "HOLD"))[:10],
                "rationale": str(p.get("rationale", ""))[:500],
            })
        except (ValueError, TypeError):
            continue

    if not valid_picks:
        return None

    return {
        "summary": str(parsed.get("summary", ""))[:300],
        "picks": sorted(valid_picks, key=lambda p: abs(p["edge_pct"]), reverse=True)[:8]
    }


async def run_multi_agent_analysis(
    question: str,
    context: str,
    price: float,
    user_prompt_customization: str = "",
    fixed_data_block: str = "",
    principal_model: str = "deepseek-v4-pro",
    subagent_model: str = "deepseek-v4-flash",
    max_subagents: int = 9
) -> dict:
    """Legacy wrapper — delegates to run_react_orchestrator."""
    result = await run_react_orchestrator(
        question=question,
        context=context,
        price=price,
        user_prompt_customization=user_prompt_customization,
        fixed_data_block=fixed_data_block,
        principal_model=principal_model,
        subagent_model=subagent_model,
        max_subagents=max_subagents,
        max_rounds=2,
    )
    return {
        "fundamental_shift": result.get("conviction_score", 0.0),
        "rationale": result.get("markdown_report", ""),
        "subagent_reports": result.get("subagent_reports", []),
        "topics": [],
        "top_reports": [],
        "visualizations": result.get("visualizations", []),
    }


def _auto_publish(markdown: str, reports: list, charts: list,
                  mispricing: list, rounds: int, spawned: int) -> dict:
    """Assemble a result dict from accumulated state (used on timeout/error)."""
    markdown = _clean_subagent_report(markdown or "")
    combined = list(mispricing)
    misp = {}
    conviction = 0.0
    if combined:
        combined = sorted(combined, key=lambda p: abs(p.get("edge_pct", 0)), reverse=True)[:8]
        misp = {"summary": "", "picks": combined}
        edges = [abs(p.get("edge_pct", 0)) for p in combined]
        if edges:
            max_e = max(edges)
            sign = 1 if any(p.get("edge_pct", 0) > 0 for p in combined) else -1
            if sign == 1 and any(p.get("edge_pct", 0) < 0 for p in combined):
                sign = 1 if max([p.get("edge_pct", 0) for p in combined]) > abs(min([p.get("edge_pct", 0) for p in combined])) else -1
            conviction = round(min(1.0, max_e * 0.02) * sign, 2)
    return {
        "markdown_report": markdown or "No report generated.",
        "conviction_score": conviction,
        "visualizations": _validate_visualizations(list(charts)[:4]),
        "mispricing_report": misp,
        "subagent_reports": reports,
        "top_reports": _resolve_top_reports({}, reports),
        "rounds": rounds,
        "agents_spawned": spawned,
        "cancelled": False
    }


def _error_result(error: str, reports: list, rounds: int, spawned: int,
                  charts: list | None = None, mispricing: list | None = None) -> dict:
    """Result dict for a failed/terminated session."""
    return {
        "markdown_report": f"**Session terminated**\n\n{error}",
        "conviction_score": 0.0,
        "visualizations": _validate_visualizations(list(charts or [])[:4]),
        "mispricing_report": {"picks": list(mispricing or [])[:8]} if mispricing else {},
        "subagent_reports": reports,
        "top_reports": [],
        "rounds": rounds,
        "agents_spawned": spawned,
        "cancelled": False,
        "error": True
    }


async def run_react_orchestrator(
    question: str,
    context: str,
    price: float,
    user_prompt_customization: str = "",
    fixed_data_block: str = "",
    on_event: Callable[[str, dict], None] | None = None,
    max_steps: int = 3,
    principal_model: str = "deepseek-v4-pro",
    subagent_model: str = "deepseek-v4-flash",
    max_subagents: int = 9,
    max_rounds: int = 5,
    min_visualizations: int = 0,
    min_mispricing_calls: int = 0,
    force_top_reports: bool = True,
    cancel_event: threading.Event | None = None,
    session_id: int = 0,
    existing_state: dict | None = None
) -> dict:
    """
    Main Agent ReAct loop — Exploration -> Exploitation.

    El Main Agent decide cuantos sub-agentes lanzar por ronda.
    Puede llamar a spawn_sub_agents multiples veces, refinando
    sus preguntas en cada ronda. Termina con la secuencia de cierre:
    spawn_mispricing_agent → spawn_visualization_agent → write_report → publish_final_report.

    max_subagents: hard cap total de sub-agentes en TODAS las rondas.
    max_rounds: maximo numero de rondas de sub-agentes (spawn_sub_agents).
    min_visualizations: minimas llamadas a spawn_visualization_agent para publish.
    min_mispricing_calls: minimas llamadas a spawn_mispricing_agent para publish.
    force_top_reports: si True, publish requiere top_reports con >= 3 entries.
    existing_state: dict con estado previo para resume.
    """
    if on_event is None:
        on_event = lambda _type, _data: None

    now = _now_str()
    customization = user_prompt_customization or (
        "Focus on concrete evidence: statistics, official statements, "
        "expert consensus, and macroeconomic signals. Avoid speculation."
    )
    fixed_data = (
        f"\n\nFIXED REFERENCE DATA:\n{fixed_data_block}"
        if fixed_data_block else ""
    )


    GENERAL_PROMPT = (
        "You are an autonomous research agent for prediction markets. "
        "Your task is to gather evidence and produce a final analysis report.\n\n"
        "CURRENT DATE: {now}. Ground ALL research in this date.\n"
        "MARKET QUESTION: {question}\n"
        "CONTEXT: {context}\n"
        "{fixed_data}\n\n"
        "POLYMARKET DATA: You MUST send at least one sub-agent to research "
        "current Polymarket data for this market (order book depth, current "
        "prices, volume, bid/ask spread). Do NOT assume a price — discover it "
        "through your agents.\n\n"
        "RESEARCH METHOD — Exploration then Exploitation:\n\n"
        "ROUND 1 (Exploration): If you lack context about the topic, "
        "spawn 2-3 exploratory sub-agents with broad questions covering "
        "different angles of the market. "
        "Example: 'What are the key factors driving this market right now?', "
        "'Find recent official statements about this event.', "
        "'What data do Polymarket markets currently show?'. "
        "DO NOT assume facts — verify before committing to a direction.\n\n"
        "ROUND 2+ (Exploitation): Once you have context from round 1, "
        "refine your research aggressively. Each round must be MORE SPECIFIC. "
        "Spawn 3-5 agents per round with targeted, non-overlapping questions. "
        "Diversity of angles produces better analysis. "
        "Example: after learning a Fed decision is at play, spawn agents "
        "for 'Latest FOMC minutes analysis', 'CME FedWatch probability data', "
        "'Bond market reaction to Fed signals', "
        "'Polymarket order book depth on rate-hike markets'. "
        "Instruct sub-agents to gather hard data using their available tools.\n\n"
        "STOPPING RULE: You have enough evidence when you can check at least 3 of these:\n"
        "- [ ] You have >= 6 sub-agent reports from >= 2 rounds\n"
        "- [ ] You have BOTH web research data AND Polymarket market data\n"
        "- [ ] You can cite at least 3 specific numbers/dates/facts from sub-agents\n"
        "- [ ] The last round's agents found no MAJOR new information vs previous rounds\n"
        "- [ ] You have spent >= 2 rounds researching and have a clear directional signal\n\n"
        "THE 4-STEP CLOSING SEQUENCE — do ALL of these before ending the session:\n"
        "A) spawn_mispricing_agent — MANDATORY, at least once per session\n"
        "B) spawn_visualization_agent — MANDATORY, at least once per session\n"
        "C) write_report — write your final markdown report\n"
        "D) publish_final_report — submit everything. WILL BE REJECTED if A/B/C not done.\n\n"
        "Do NOT spawn more agents 'just to be sure' — diminishing returns apply quickly. "
        "A good analysis with 6 reports beats a perfect analysis with 9.\n"
        "You may spawn up to {max_subagents} agents across all research rounds. "
        "spawn_sub_agents consumes 1 of your {max_rounds} research rounds each call. "
        "spawn_visualization_agent, spawn_mispricing_agent, write_report, and "
        "publish_final_report do NOT consume rounds — use them as needed.\n\n"
        "IMPORTANT RULES:\n"
        "1. NEVER respond with plain text. Always call a tool.\n"
        "2. Start small — 1-2 exploratory agents, not 9 blind guesses.\n"
        "3. Each agent's 'instructions' field must specify WHICH tools to use.\n"
        "   Bad: 'Research the topic'\n"
        "   Good: 'Use get_financial_data for BTC-USD price and history. "
        "Use search_internet for recent ETF flow news, then scrape_webpage on "
        "the most relevant results to read full articles. "
        "Use get_polymarket_orderbook for current market depth.'\n"
        "   CRITICAL: When researching any asset (crypto, stock, commodity, index, forex), "
        "ALWAYS instruct the sub-agent to use get_financial_data — NOT search_internet — "
        "for hard price data, trends, and fundamentals."
        "4. ANTI-HALLUCINATION: Your markdown_report is plain text for humans. "
        "NEVER include tool-call syntax (<invoke>, <parameter>, <tool_call>, etc.) "
        "or XML/JSON fragments in the report."
    )

    REPORT_STRUCTURE_PROMPT = (
        "=== THE 4-STEP CLOSING SEQUENCE ===\n\n"
        "These 4 tools MUST all be called before the session ends. "
        "publish_final_report VALIDATES server-side and REJECTS if any is missing.\n\n"
        "STEP A) spawn_mispricing_agent — MANDATORY, at least once\n"
        "  Generates structured mispricing analysis (edge %, BUY/SELL/HOLD).\n"
        "  Call after you have market prices and fair value estimates for >= 2 outcomes.\n"
        "  Multiple calls ACCUMULATE. Does NOT count toward research agent cap.\n\n"
        "STEP B) spawn_visualization_agent — MANDATORY, at least once\n"
        "  Generates charts. DOES NOT count toward research agent cap.\n"
        "  Even qualitative data can be charted: bar chart for outcome comparison, donut\n"
        "  chart for probability distribution, line chart for trends over time.\n"
        "  Multiple calls ACCUMULATE. Call by round 3 at the latest.\n\n"
        "STEP C) write_report — MANDATORY, at least once\n"
        "  Write your full markdown report. LAST WRITE WINS (no concatenation).\n"
        "  Required sections: ## Key Findings, ## Evidence Analysis, ## Market Assessment,\n"
        "  ## Actionable Advice (reference mispricing edge % directly), ## Conclusion.\n"
        "  PLAIN MARKDOWN ONLY — no <invoke>, <parameter>, <tool_call>, XML, or JSON.\n\n"
        "STEP D) publish_final_report — ends the session\n"
        "  Validates A/B/C were completed. REJECTS with missing items list if not.\n"
        "  On success: emits the final report and ends the session.\n"
        "  Optional fields: conviction_score (auto-derived from mispricing if omitted),\n"
        "  top_reports (0-based indices, defaults to first 3 sub-agents).\n\n"
        "=== CONVICTION RULE ===\n"
        "conviction_score from -1.0 to +1.0. 0.0 FORBIDDEN unless zero evidence.\n"
        "Strong evidence YES: +0.8 to +1.0 | Moderate: +0.3 to +0.7 | Weak: +0.1 to +0.3\n"
        "Contradictory slight NO: -0.1 to -0.3 | Strong NO: -0.7 to -1.0"
    )

    system_prompt = GENERAL_PROMPT.format(
        now=now,
        question=question,
        context=context or 'None provided.',
        fixed_data=fixed_data if fixed_data else "",
        max_subagents=max_subagents,
        max_rounds=max_rounds,
    ) + "\n" + REPORT_STRUCTURE_PROMPT

    user_prompt = (
        f"Research this prediction market question. Start with exploratory "
        f"sub-agents if the topic is unfamiliar, then refine based on findings."
    )

    sem = asyncio.Semaphore(MAX_CONCURRENT)
    call_counter = [0]
    agents_spawned_total = 0
    viz_calls_made = 0
    accumulated_charts: list[dict] = []
    mispricing_calls = 0
    accumulated_mispricing: list[dict] = []
    all_subagent_reports: list[dict] = []
    current_markdown_report: str = ""
    markdown_written: bool = False
    consecutive_publish_rejections: int = 0
    MAX_PUBLISH_REJECTIONS = 3
    round_num: int = 0
    rounds_remaining: int = max_rounds

    if existing_state:
        agents_spawned_total = existing_state.get("agents_spawned", 0)
        viz_calls_made = existing_state.get("viz_calls_made", 0)
        mispricing_calls = existing_state.get("mispricing_calls", 0)
        all_subagent_reports = existing_state.get("subagent_reports", [])
        start_round = existing_state.get("round_number", 0) + 1
        rounds_remaining = max_rounds - (start_round - 1)
        if rounds_remaining < 0:
            rounds_remaining = 0
        messages = existing_state.get("messages", [])
        if not messages:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        on_event("phase", {
            "phase": "thinking",
            "message": f"Resuming research at round {start_round}. "
                       f"{len(all_subagent_reports)} reports from previous rounds."
        })

        # Emit cached events for already-completed agents
        round_groups: dict[int, list[dict]] = {}
        for r in all_subagent_reports:
            rn = r.get("round", 1)
            round_groups.setdefault(rn, []).append(r)

        agent_idx = 0
        for rn in sorted(round_groups):
            agents = round_groups[rn]
            on_event("phase", {
                "phase": "researching",
                "message": f"Round {rn} — {len(agents)} agents (cached)",
                "round": rn
            })
            for rpt in agents:
                on_event("subagent_start", {
                    "id": agent_idx, "topic": rpt.get("topic", ""), "round": rn
                })
                on_event("subagent_complete", {
                    "id": agent_idx, "topic": rpt.get("topic", ""),
                    "report": rpt.get("report", ""),
                    "steps_used": rpt.get("steps_used", 0),
                    "forced_summary": rpt.get("forced_summary", False),
                    "sources": rpt.get("sources", []),
                    "round": rn
                })
                agent_idx += 1
            on_event("agents_spawned", {
                "round": rn,
                "count": len(agents),
                "total": sum(len(g) for g in round_groups.values()),
                "cap": max_subagents
            })
    else:
        start_round = 1
        rounds_remaining = max_rounds
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        on_event("phase", {
            "phase": "thinking",
            "message": "Main Agent analyzing research strategy..."
        })

    round_num = 0

    while True:
        if cancel_event and cancel_event.is_set():
            on_event("phase", {"phase": "cancelled", "message": "Research cancelled by user"})
            if session_id:
                try:
                    from .db_engine import ResearchDB
                    ResearchDB.update(session_id, {
                        "subagent_reports": all_subagent_reports,
                        "round_number": round_num,
                        "agents_spawned": agents_spawned_total,
                        "status": "failed"
                    })
                except Exception:
                    pass
            return {
                "markdown_report": "Research cancelled by user.",
                "conviction_score": 0.0,
                "visualizations": [],
                "subagent_reports": all_subagent_reports,
                "rounds": round_num,
                "agents_spawned": agents_spawned_total,
                "cancelled": True
            }

        try:
            resp = await asyncio.wait_for(
                _get_client().chat.completions.create(
                    model=principal_model,
                    messages=messages,
                    tools=main_agent_tools,
                    temperature=0.3,
                    max_tokens=200000,
                    extra_body={"thinking": {"type": "enabled", "reasoning_effort": "max"}}
                ),
                timeout=60
            )
            msg = resp.choices[0].message
        except asyncio.TimeoutError:
            logger.warning(f"Principal Agent timed out in round {round_num}")
            on_event("cap_warning", {"message": f"Principal Agent timed out. Session terminated."})
            result = _auto_publish(current_markdown_report, all_subagent_reports,
                                   accumulated_charts, accumulated_mispricing,
                                   round_num, agents_spawned_total)
            on_event("result", result)
            return result

        principal_reasoning = (getattr(msg, 'reasoning_content', None) or "").strip()
        if principal_reasoning:
            on_event("agent_reasoning", {
                "content": principal_reasoning,
                "round": round_num
            })

        sanitized = {
            "role": msg.role,
            "content": msg.content,
        }
        if msg.tool_calls:
            sanitized["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments
                    }
                }
                for tc in msg.tool_calls
            ]
        messages.append(sanitized)

        if not msg.tool_calls:
            messages.append({
                "role": "user",
                "content": (
                "You MUST use one of your available tools: "
                "spawn_sub_agents to research, spawn_visualization_agent "
                "for charts, spawn_mispricing_agent for mispricing analysis, "
                "write_report to draft your report, or publish_final_report "
                "to submit. Do not respond with plain text."
                )
            })
            continue

        for tool in msg.tool_calls:
            if tool.function.name == "spawn_sub_agents":
                try:
                    args = json.loads(tool.function.arguments)
                except (json.JSONDecodeError, KeyError):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": "ERROR: Invalid arguments. Provide valid JSON with 'agents' array."
                    })
                    continue

                if rounds_remaining <= 0:
                    cap_msg = (
                        f"No more research rounds (all {max_rounds} used). "
                        f"Complete the closing sequence: spawn_mispricing_agent, "
                        f"spawn_visualization_agent, write_report, then publish_final_report."
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": cap_msg
                    })
                    on_event("cap_warning", {"message": cap_msg})
                    continue

                rounds_remaining -= 1
                round_num += 1

                requested = args.get("agents", [])
                available = max_subagents - agents_spawned_total

                if available <= 0:
                    cap_msg = (
                        f"SYSTEM: Hard cap of {max_subagents} agents reached "
                        f"({agents_spawned_total} already spawned). You cannot "
                        f"spawn more agents. Complete the 4-step closing sequence: "
                        f"spawn_mispricing_agent, spawn_visualization_agent, "
                        f"write_report, then publish_final_report with "
                        f"the information gathered so far."
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": cap_msg
                    })
                    on_event("cap_warning", {"message": cap_msg})
                    continue

                agents_to_run = requested[:available]
                skipped = len(requested) - len(agents_to_run)

                on_event("phase", {
                    "phase": "researching",
                    "message": f"Round {round_num} — dispatching {len(agents_to_run)} sub-agents",
                    "round": round_num
                })

                if session_id:
                    try:
                        from .db_engine import ResearchDB
                        ResearchDB.update(session_id, {
                            "status": "running",
                            "round_number": round_num,
                        })
                    except Exception:
                        pass

                on_event("agents_spawned", {
                    "round": round_num,
                    "count": len(agents_to_run),
                    "total": agents_spawned_total + len(agents_to_run),
                    "cap": max_subagents
                })

                async def _wrapped_agent(topic: str, instructions: str, idx: int) -> dict:
                    on_event("subagent_start", {
                        "id": idx, "topic": topic, "round": round_num
                    })
                    result = await _run_subagent(
                        topic, question, sem, call_counter,
                        on_event=on_event, agent_idx=idx,
                        max_steps=max_steps, subagent_model=subagent_model,
                        cancel_event=cancel_event, instructions=instructions
                    )
                    on_event("subagent_complete", {
                        "id": idx, "topic": topic,
                        "report": result.get("report", ""),
                        "steps_used": result.get("steps_used", 0),
                        "forced_summary": result.get("forced_summary", False),
                        "sources": result.get("sources", []),
                        "round": round_num
                    })
                    return result

                tasks = [
                    _wrapped_agent(a["topic"], a.get("instructions", ""), i)
                    for i, a in enumerate(agents_to_run, agents_spawned_total)
                ]
                round_reports = await asyncio.gather(*tasks)
                for r in round_reports:
                    r["round"] = round_num

                agents_spawned_total += len(agents_to_run)
                all_subagent_reports.extend(round_reports)

                parts: list[str] = []
                for j, r in enumerate(round_reports):
                    report_text = r.get("report", "No findings.") or "No findings."
                    parts.append(
                        f"SUB-AGENT {j+1} — {r['topic']}:\n"
                        f"{report_text}"
                    )
                remaining = max_subagents - agents_spawned_total
                tool_response = (
                    f"Research round {round_num} complete. {len(agents_to_run)} agents reported:\n\n"
                    + "\n\n".join(parts)
                    + f"\n\nStatus: {agents_spawned_total} of {max_subagents} agents used "
                    f"({remaining} remaining). {rounds_remaining} research rounds left."
                )
                if skipped:
                    tool_response += (
                        f"\n\nNOTE: {skipped} requested agent(s) were skipped "
                        f"due to agent cap ({max_subagents})."
                    )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": tool_response
                })

                if session_id:
                    try:
                        from .db_engine import ResearchDB
                        ResearchDB.update(session_id, {
                            "subagent_reports": all_subagent_reports,
                            "status": "running",
                            "round_number": round_num,
                            "conviction_score": 0.0,
                            "viz_calls_made": viz_calls_made,
                        })
                    except Exception:
                        pass

            elif tool.function.name == "spawn_visualization_agent":
                try:
                    args = json.loads(tool.function.arguments)
                except (json.JSONDecodeError, KeyError):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": "ERROR: Invalid arguments. Provide valid JSON with 'request' field."
                    })
                    continue

                request_text = args.get("request", "")
                if not request_text:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": "ERROR: 'request' field is required for spawn_visualization_agent."
                    })
                    continue

                charts = await _run_viz_agent(request_text)
                viz_calls_made += 1

                quota_info = ""
                if min_visualizations > 0:
                    remaining = min_visualizations - viz_calls_made
                    quota_info = f"Quota: {viz_calls_made}/{min_visualizations} "
                    if remaining > 0:
                        quota_info += f"({remaining} more required)"
                    else:
                        quota_info += f"(quota met)"
                else:
                    quota_info = f"Viz calls made: {viz_calls_made} (optional)"

                if charts:
                    accumulated_charts.extend(charts)
                    on_event("viz_agent_complete", {
                        "charts": charts,
                        "count": len(charts),
                        "round": round_num
                    })
                    chart_text = json.dumps(charts, indent=2)
                    tool_content = (
                        f"Visualization agent generated {len(charts)} chart(s):\n\n"
                        f"```json\n{chart_text}\n```\n\n"
                        "Charts are accumulated automatically. "
                        "publish_final_report will include all charts.\n"
                        f"{quota_info}."
                    )
                else:
                    on_event("viz_agent_complete", {
                        "charts": [],
                        "count": 0,
                        "round": round_num
                    })
                    tool_content = (
                        "Visualization agent could not generate charts from your request. "
                        "Either the data was insufficient or the request was unclear. "
                        "You can try again with more specific data. "
                        f"{quota_info}."
                    )

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": tool_content[:60000]
                })

                if session_id:
                    try:
                        from .db_engine import ResearchDB
                        ResearchDB.update(session_id, {
                            "viz_calls_made": viz_calls_made,
                            "visualizations": accumulated_charts,
                            "status": "running",
                            "round_number": round_num,
                        })
                    except Exception:
                        pass

            elif tool.function.name == "spawn_mispricing_agent":

                try:
                    args = json.loads(tool.function.arguments)
                except (json.JSONDecodeError, KeyError):
                    messages.append({
                        "role": "tool", "tool_call_id": tool.id,
                        "content": "ERROR: Invalid arguments for spawn_mispricing_agent."
                    })
                    continue

                context = args.get("context", "")
                if not context:
                    messages.append({
                        "role": "tool", "tool_call_id": tool.id,
                        "content": "ERROR: 'context' field is required."
                    })
                    continue

                mispricing_calls += 1
                on_event("mispricing_agent_start", {
                    "round": round_num, "call": mispricing_calls
                })

                report = await _run_mispricing_agent(context, model=subagent_model)

                quota_info = ""
                if min_mispricing_calls > 0:
                    rem = min_mispricing_calls - mispricing_calls
                    quota_info = f"Quota: {mispricing_calls}/{min_mispricing_calls} "
                    quota_info += f"({'met' if rem <= 0 else f'{rem} more required'})"
                else:
                    quota_info = f"Calls: {mispricing_calls} (optional)"

                if report:
                    accumulated_mispricing.extend(report["picks"])
                    on_event("mispricing_agent_complete", {
                        "picks": report["picks"],
                        "new": len(report["picks"]),
                        "total": len(accumulated_mispricing),
                        "round": round_num
                    })
                    tool_content = (
                        f"Mispricing analysis generated {len(report['picks'])} pick(s) "
                        f"from call {mispricing_calls}. "
                        f"Total accumulated: {len(accumulated_mispricing)} pick(s).\n\n"
                        f"Current picks:\n```json\n{json.dumps(report, indent=2)}\n```\n\n"
                        "Picks are accumulated automatically across all calls. "
                        "publish_final_report will include the full accumulated list.\n"
                        f"{quota_info}."
                    )
                else:
                    on_event("mispricing_agent_complete", {
                        "picks": [], "new": 0,
                        "total": len(accumulated_mispricing),
                        "round": round_num
                    })
                    tool_content = (
                        f"Mispricing agent call {mispricing_calls} could not generate analysis. "
                        f"Either the context was insufficient or data didn't support picks. "
                        f"{quota_info}."
                    )

                if session_id:
                    try:
                        from .db_engine import ResearchDB
                        mispricing_db = {}
                        if accumulated_mispricing:
                            mispricing_db = {
                                "summary": report.get("summary", "") if report else "",
                                "picks": accumulated_mispricing
                            }
                        ResearchDB.update(session_id, {
                            "mispricing_calls": mispricing_calls,
                            "mispricing_report": mispricing_db,
                            "status": "running",
                            "round_number": round_num,
                        })
                    except Exception:
                        pass

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": tool_content[:60000]
                })

            elif tool.function.name == "write_report":
                try:
                    args = json.loads(tool.function.arguments)
                except (json.JSONDecodeError, KeyError):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": "ERROR: Invalid arguments for write_report."
                    })
                    continue

                md = _clean_subagent_report(args.get("markdown_report", ""))
                if not md or len(md.strip()) < 20:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": (
                            "ERROR: write_report requires a markdown_report with at least "
                            "20 characters. Your report was empty or too short."
                        )
                    })
                    continue

                current_markdown_report = md
                markdown_written = True

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool.id,
                    "content": (
                        "Report written successfully. You can rewrite by calling "
                        "write_report again (last write wins). When ready, call "
                        "publish_final_report to submit.\n\n"
                        "CURRENT STATUS:\n"
                        + (f"- Charts: {viz_calls_made}/{min_visualizations} calls "
                           f"({min_visualizations - viz_calls_made} more required)\n" if min_visualizations > 0 else
                           f"- Charts: {viz_calls_made} calls (optional)\n") +
                        (f"- Mispricing: {mispricing_calls}/{min_mispricing_calls} calls "
                           f"({min_mispricing_calls - mispricing_calls} more required)\n" if min_mispricing_calls > 0 else
                           f"- Mispricing: {mispricing_calls} calls (optional)\n") +
                        f"- Report: written ({len(current_markdown_report)} chars)"
                    )
                })

            elif tool.function.name == "publish_final_report":
                try:
                    args = json.loads(tool.function.arguments)
                except (json.JSONDecodeError, KeyError):
                    args = {}

                # ── VALIDATION with min quotas ──
                errors = []
                if min_visualizations > 0 and viz_calls_made < min_visualizations:
                    errors.append(
                        f"CHARTS: {viz_calls_made}/{min_visualizations} calls — "
                        f"need {min_visualizations - viz_calls_made} more spawn_visualization_agent"
                    )
                if min_mispricing_calls > 0 and mispricing_calls < min_mispricing_calls:
                    errors.append(
                        f"MISPRICING: {mispricing_calls}/{min_mispricing_calls} calls — "
                        f"need {min_mispricing_calls - mispricing_calls} more spawn_mispricing_agent"
                    )
                if not markdown_written:
                    errors.append("REPORT: write_report was never called")
                if force_top_reports:
                    top_candidate = args.get("top_reports", [])
                    if not isinstance(top_candidate, list) or len(top_candidate) < 3:
                        if len(all_subagent_reports) >= 3:
                            errors.append(
                                f"TOP REPORTS: must select exactly 3 most influential reports "
                                f"(0-based indices). You have {len(all_subagent_reports)} reports available."
                            )
                        # else: fewer than 3 sub-agents exist, auto-accept

                if errors:
                    consecutive_publish_rejections += 1
                    error_msg = f"PUBLISH REJECTED (attempt {consecutive_publish_rejections}/{MAX_PUBLISH_REJECTIONS}):\n\n" + "\n\n".join(
                        f"\u274c {e}" for e in errors
                    ) + "\n\nFix ALL issues above, then call publish_final_report again."

                    if consecutive_publish_rejections >= MAX_PUBLISH_REJECTIONS:
                        on_event("phase", {
                            "phase": "error",
                            "message": f"Session terminated: failed to meet quotas after {MAX_PUBLISH_REJECTIONS} attempts."
                        })
                        result = _error_result(
                            f"Failed to meet quotas after {MAX_PUBLISH_REJECTIONS} publish attempts. "
                            f"Missing: {', '.join(errors)}",
                            all_subagent_reports, round_num, agents_spawned_total,
                            accumulated_charts, accumulated_mispricing
                        )
                        on_event("result", result)
                        return result

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool.id,
                        "content": error_msg
                    })
                    continue

                # Reset counter on success
                consecutive_publish_rejections = 0

                # ── ALL CLEAR: assemble and publish ──
                conviction_score = float(args.get("conviction_score", 0))
                conviction_score = max(-1.0, min(1.0, conviction_score))

                combined_picks = list(accumulated_mispricing)
                mispricing_report = {}
                if combined_picks:
                    combined_picks = sorted(
                        combined_picks,
                        key=lambda p: abs(p.get("edge_pct", 0)),
                        reverse=True
                    )[:8]
                    mispricing_report = {
                        "summary": "",
                        "picks": combined_picks
                    }
                    if conviction_score == 0.0 or "conviction_score" not in args:
                        edges = [abs(p.get("edge_pct", 0)) for p in combined_picks]
                        if edges:
                            max_edge = max(edges)
                            sign = 1 if any(p.get("edge_pct", 0) > 0 for p in combined_picks) else -1
                            if sign == 1 and any(p.get("edge_pct", 0) < 0 for p in combined_picks):
                                sign = 1 if max([p.get("edge_pct", 0) for p in combined_picks]) > abs(min([p.get("edge_pct", 0) for p in combined_picks])) else -1
                            derived_score = min(1.0, max_edge * 0.02) * sign
                            conviction_score = round(derived_score, 2)

                on_event("phase", {
                    "phase": "finalizing",
                    "message": "Publishing final report..."
                })

                visualizations = _validate_visualizations(list(accumulated_charts)[:4])
                top_reports = _resolve_top_reports(args, all_subagent_reports)

                result = {
                    "markdown_report": current_markdown_report,
                    "conviction_score": conviction_score,
                    "visualizations": visualizations,
                    "mispricing_report": mispricing_report,
                    "subagent_reports": all_subagent_reports,
                    "top_reports": top_reports,
                    "rounds": round_num,
                    "agents_spawned": agents_spawned_total,
                    "cancelled": False
                }
                on_event("result", result)
                return result

        # End of while True loop — should never be reached
        # (publish_final_report always returns or errors out)
