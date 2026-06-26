from dataclasses import dataclass, field


@dataclass
class OrderBook:
    token_id: str
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    spread: float
    imbalance: float = 0.0
    timestamp: str = ""


@dataclass
class Market:
    slug: str
    event_slug: str
    question: str
    outcomes: list[str]
    outcome_prices: list[float]
    token_id: str
    last_trade_price: float
    best_bid: float
    best_ask: float
    spread: float
    volume_24h: float
    liquidity: float
    competitive: float
    end_date: str
    tags: list[str]
    one_hour_change: float
    one_day_change: float
    one_week_change: float
    orderbook: OrderBook | None = None
    event_context: str = ""


@dataclass
class AnalysisResult:
    market_slug: str
    event_slug: str = ""
    our_prob: float = 0.0
    market_price: float = 0.0
    real_price: float = 0.0
    edge: float = 0.0
    confidence: str = "LOW"
    rationale: dict = field(default_factory=dict)
    strategy: str = ""
    llm_reasoning: str = ""
    news_used: str = ""
    fundamental_shift: float = 0.0


@dataclass
class Position:
    id: int | None = None
    opened_at: str = ""
    closed_at: str = ""
    event_slug: str = ""
    market_slug: str = ""
    question: str = ""
    token_id: str = ""
    side: str = "BUY"
    shares: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    exit_price: float = 0.0
    cost_usd: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0
    status: str = "open"
    our_prob: float = 0.0
    edge_at_entry: float = 0.0
    strategy: str = ""
    rationale_json: str = ""
    job_id: str = ""
    closed_reason: str = ""
    notes: str = ""


@dataclass
class StrategyParams:
    name: str
    strategy_type: str = "meta_consensus"
    params: dict = field(default_factory=dict)
    is_active: bool = False
    user_prompt_customization: str = ""
    fixed_data_block: str = ""
