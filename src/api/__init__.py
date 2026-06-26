from flask import Flask
from flask_cors import CORS

from ..core import database, config, settings
from ..nba import collector

_app = None


def create_app() -> Flask:
    global _app
    if _app is not None:
        return _app

    app = Flask(__name__)
    CORS(app)

    database.setup()
    settings.apply_saved()

    from ..polymarket.db_engine import setup as pm_setup
    pm_setup()

    from .routes.picks import bp as picks_bp
    from .routes.pending import bp as pending_bp
    from .routes.history import bp as history_bp
    from .routes.backtest import bp as backtest_bp
    from .routes.calibrate import bp as calibrate_bp
    from .routes.bankroll import bp as bankroll_bp
    from .routes.resolver import bp as resolver_bp
    from .routes.football import bp as football_bp
    from .routes.mlb import bp as mlb_bp
    from .routes.jobs import bp as jobs_bp

    from ..polymarket.api import pm_bp

    app.register_blueprint(picks_bp, url_prefix="/api")
    app.register_blueprint(pending_bp, url_prefix="/api")
    app.register_blueprint(history_bp, url_prefix="/api")
    app.register_blueprint(backtest_bp, url_prefix="/api")
    app.register_blueprint(calibrate_bp, url_prefix="/api")
    app.register_blueprint(bankroll_bp, url_prefix="/api")
    app.register_blueprint(resolver_bp, url_prefix="/api")
    app.register_blueprint(football_bp, url_prefix="/api")
    app.register_blueprint(mlb_bp, url_prefix="/api")
    app.register_blueprint(jobs_bp, url_prefix="/api")
    app.register_blueprint(pm_bp, url_prefix="/api/pm")

    @app.route("/api/health")
    def health():
        record = database.get_record()
        wins = record.get("WIN", {}).get("count", 0)
        losses = record.get("LOSS", {}).get("count", 0)
        return {
            "status": "ok",
            "record": {"wins": wins, "losses": losses},
            "season": collector.get_active_season(),
            "min_edge": config.get_min_edge(),
            "fetch_props": config.get_fetch_props(),
            "bankroll": database.get_current_bankroll(config.get_bankroll()),
        }

    _app = app
    return app



