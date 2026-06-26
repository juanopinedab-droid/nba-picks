from .db_engine import setup as _setup_db
from .db_engine import BankrollDB
from src.core.polymarket_config import BANKROLL_USD


def migrate():
    _setup_db()
    BankrollDB.init_bankroll(BANKROLL_USD)
