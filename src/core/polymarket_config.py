import os
from dotenv import load_dotenv

load_dotenv()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
POLYMARKET_PROXY = os.getenv("POLYMARKET_PROXY", "")

BANKROLL_USD           = float(os.getenv("PM_BANKROLL", "5000"))
MIN_EDGE               = float(os.getenv("PM_MIN_EDGE", "0.03"))
MIN_VOLUME             = float(os.getenv("PM_MIN_VOLUME", "50000"))
MIN_LIQUIDITY          = float(os.getenv("PM_MIN_LIQUIDITY", "5000"))
MAX_DAYS_TO_RESOLUTION = float(os.getenv("PM_MAX_DAYS", "60"))
STAKE_FRACTION         = float(os.getenv("PM_STAKE_FRACTION", "0.04"))

_active_bankroll        = BANKROLL_USD
_active_min_edge        = MIN_EDGE
_active_min_volume      = MIN_VOLUME
_active_min_liquidity   = MIN_LIQUIDITY
_active_max_days        = MAX_DAYS_TO_RESOLUTION
_active_stake_fraction  = STAKE_FRACTION


def get_bankroll() -> float:
    return _active_bankroll


def set_bankroll(v: float):
    _set_bankroll_raw(v)
    _auto_save()


def _set_bankroll_raw(v: float):
    global _active_bankroll
    _active_bankroll = float(v)


def get_min_edge() -> float:
    return _active_min_edge


def set_min_edge(v: float):
    _set_min_edge_raw(v)
    _auto_save()


def _set_min_edge_raw(v: float):
    global _active_min_edge
    _active_min_edge = float(v)


def get_min_volume() -> float:
    return _active_min_volume


def set_min_volume(v: float):
    _set_min_volume_raw(v)
    _auto_save()


def _set_min_volume_raw(v: float):
    global _active_min_volume
    _active_min_volume = float(v)


def get_min_liquidity() -> float:
    return _active_min_liquidity


def set_min_liquidity(v: float):
    _set_min_liquidity_raw(v)
    _auto_save()


def _set_min_liquidity_raw(v: float):
    global _active_min_liquidity
    _active_min_liquidity = float(v)


def get_max_days_to_resolution() -> float:
    return _active_max_days


def set_max_days_to_resolution(v: float):
    _set_max_days_raw(v)
    _auto_save()


def _set_max_days_raw(v: float):
    global _active_max_days
    _active_max_days = float(v)


def get_stake_fraction() -> float:
    return _active_stake_fraction


def set_stake_fraction(v: float):
    _set_stake_fraction_raw(v)
    _auto_save()


def _set_stake_fraction_raw(v: float):
    global _active_stake_fraction
    _active_stake_fraction = float(v)


def _auto_save():
    from src.polymarket.db_engine import BankrollDB
    BankrollDB.set_setting("bankroll_usd", _active_bankroll)
    BankrollDB.set_setting("min_edge", _active_min_edge)
    BankrollDB.set_setting("min_volume", _active_min_volume)
    BankrollDB.set_setting("min_liquidity", _active_min_liquidity)
    BankrollDB.set_setting("max_days_to_resolution", _active_max_days)
    BankrollDB.set_setting("stake_fraction", _active_stake_fraction)


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE  = "https://clob.polymarket.com"
CACHE_TTL  = 300
