# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Personal NBA betting bot for daily picks (moneyline, spread, player props, parlays). Runs manually each morning before games. Output goes to terminal. Data is persisted in local SQLite. All code and output is in Spanish.

## Running the project

```powershell
# Daily picks (main command)
python picks.py

# Mark outcome after games finish
python picks.py --resultado <ID> WIN   # or LOSS / PUSH

# View picks pending a result
python picks.py --pendientes

# View full record with ROI by bet type
python picks.py --historial

# Validate/calibrate the prediction model (run once, takes ~3 min)
python backtest.py

# Download historical data only (without running analysis)
python backtest.py --download

# Use last N seasons for backtest (1 or 2, default 2)
python backtest.py --seasons 1
```

Install dependencies: `pip install -r requirements.txt`

## Architecture

**Data flow:** `collector.py` → `analyzer.py` → `bankroll.py` + `parlays.py` → `database.py` → display in `picks.py`

**collector.py** — All external data fetching. Calls nba_api (team stats, player stats, game logs) and The Odds API (odds + player props). Blends Regular Season and Playoff stats weighted by how many playoff games a team has played (max 65% weight at 10+ playoff games). Caches all API responses in module-level dicts for the session duration (`_regular_team_cache`, `_playoff_team_cache`, `_regular_player_cache`, `_playoff_player_cache`, `_gamelog_cache`).

**analyzer.py** — Prediction model. Uses rolling Net Rating differential + home advantage + back-to-back penalty + rest days → sigmoid → win probability. Compares to implied probability from odds (after vig removal) to calculate edge. Also evaluates player props using normal distribution with per-stat coefficient of variation (`_STAT_CV = {"PTS": 0.38, "REB": 0.45, "AST": 0.50, "FG3M": 0.70}`).

**backtest.py** — Standalone calibration tool. Downloads historical game logs, reconstructs rolling features with no data leakage, grid-searches the sigmoid `k` parameter, and reports Brier Score, calibration curve, and simulated ROI at different edge thresholds.

**bankroll.py** — Four strategies (Conservative 1%, Moderate 2%, Kelly, Aggressive 3%). Minimum bet is 500 COP. `calc_stakes_moderado()` is called by picks.py to persist stake amounts.

**database.py** — SQLite (`picks_history.db`). `setup()` runs migrations so new columns are added safely to existing DBs. `get_current_bankroll(initial)` computes live bankroll by summing all `profit_cop` values.

## Configuration (.env)

| Variable | Purpose |
|---|---|
| `ODDS_API_KEY` | The Odds API key (500 req/month free tier) |
| `NBA_SEASON` | e.g. `2025-26` |
| `MIN_EDGE` | Minimum edge to generate a pick (default `0.04`) |
| `FETCH_PROPS` | Set `false` to skip player props (saves API requests) |
| `BANKROLL` | Initial bankroll in COP — live balance is computed from DB |

## Key constants to tune

- `HOME_ADVANTAGE_POINTS = 3.0` in `config.py` — home court adjustment in Net Rating points
- `B2B_PENALTY_POINTS = 3.5` in `config.py` — back-to-back penalty in Net Rating points
- `k = 0.8` in `analyzer.py:net_rating_to_prob()` — sigmoid steepness; run `backtest.py` to find the calibrated value for the current dataset
- `MIN_EDGE` in `.env` — raise to 0.06–0.08 if backtester shows negative ROI at 0.04

## API rate limits

nba_api requires `time.sleep(0.8–1.0)` between calls or returns HTTP 429. All nba_api calls in `collector.py` include sleeps. The Odds API free tier allows 500 requests/month; player props cost 1 request per game, so `FETCH_PROPS=true` with 8 games/day uses ~240 requests/month.

## SQLite databases

- `picks_history.db` — picks, results, profit/loss (user-facing)
- `backtest_data.db` — historical game logs for calibration (auto-created, safe to delete and re-download)
