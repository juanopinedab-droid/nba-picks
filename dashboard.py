# -*- coding: utf-8 -*-
"""
dashboard.py — Panel de control local para NBA Picks Bot
Ejecutar: python dashboard.py
Abrir:    http://localhost:5000
"""

import threading
from datetime import datetime

from flask import Flask, jsonify, render_template_string, request

import database
import collector
import analyzer
import bankroll
import config

app = Flask(__name__)

# ─── Estado global en memoria ──────────────────────────────────────────────────
_state = {
    "generating": False,
    "picks":      [],
    "props":      [],
    "games":      [],
    "timestamp":  None,
    "bankroll":   0,
    "log":        [],
}


# ─── Pipeline (replica picks.py sin prints) ────────────────────────────────────
def _pipeline():
    global _state
    log = []

    def _log(msg):
        log.append(msg)
        _state["log"] = list(log)

    try:
        _log("Conectando a The Odds API...")
        games = collector.get_todays_odds()
        _log(f"{len(games)} juego(s) encontrados")

        _log("Descargando stats de equipos (NBA API)...")
        player_stats = collector.get_all_player_season_stats()
        _log("Stats jugadores: OK")

        _log("Reporte de lesiones (ESPN)...")
        injury_report = collector.get_injury_report()
        _log(f"{len(injury_report)} jugador(es) con limitaciones")

        live_bankroll   = database.get_current_bankroll(config.BANKROLL)
        all_game_picks  = []
        all_prop_picks  = []
        game_results    = []

        for game in games:
            home = game["home_team"]
            away = game["away_team"]

            home_stats = collector.get_team_stats(home)
            away_stats = collector.get_team_stats(away)
            if not home_stats or not away_stats:
                continue

            home_b2b    = collector.is_back_to_back(home)
            away_b2b    = collector.is_back_to_back(away)
            home_rest   = collector.get_rest_days(home)
            away_rest   = collector.get_rest_days(away)
            home_form   = collector.get_team_recent_form(home)
            away_form   = collector.get_team_recent_form(away)
            home_travel = collector.get_consecutive_away_games(home)
            away_travel = collector.get_consecutive_away_games(away)
            h2h_val     = collector.get_h2h_edge(home, away)
            home_impact = collector.get_team_injury_impact(home, injury_report, player_stats)
            away_impact = collector.get_team_injury_impact(away, injury_report, player_stats)

            home_stats_adj = {
                **home_stats,
                "net_rating":     home_stats["net_rating"] + home_impact["adjustment"],
                "recent_nr":      home_form["recent_nr"] if home_form else None,
                "recent_games":   home_form["games"]     if home_form else 0,
                "travel_fatigue": home_travel,
                "h2h_edge":       h2h_val,
            }
            away_stats_adj = {
                **away_stats,
                "net_rating":     away_stats["net_rating"] + away_impact["adjustment"],
                "recent_nr":      away_form["recent_nr"] if away_form else None,
                "recent_games":   away_form["games"]     if away_form else 0,
                "travel_fatigue": away_travel,
            }

            result = analyzer.analyze_game(
                game, home_stats_adj, away_stats_adj,
                home_b2b, away_b2b, home_rest, away_rest
            )
            result["home_injured_out"]          = home_impact["out"]
            result["home_injured_questionable"] = home_impact["questionable"]
            result["away_injured_out"]          = away_impact["out"]
            result["away_injured_questionable"] = away_impact["questionable"]

            commence = game.get("commence_time", "")
            for pick in result["picks"]:
                pick["commence_time"] = commence
                pick["sport"]         = "nba"

            game_results.append(result)
            all_game_picks.extend(result["picks"])

            if config.FETCH_PROPS:
                try:
                    raw_props = collector.get_player_props(game["game_id"])
                    if raw_props:
                        stat_cols   = ["PTS", "REB", "AST", "FG3M"]
                        recent_avgs = {}
                        for prop in raw_props:
                            name = prop["player"]
                            if name not in recent_avgs:
                                recent_avgs[name] = collector.get_player_recent_avg(name, stat_cols)
                        prop_picks = analyzer.analyze_player_props(
                            raw_props, player_stats,
                            home_b2b, away_b2b, home, away,
                            game_total=game.get("total_line"),
                            recent_avgs=recent_avgs,
                            home_stats=home_stats_adj,
                            away_stats=away_stats_adj,
                        )
                        for pick in prop_picks:
                            pick["commence_time"] = commence
                            pick["sport"]         = "nba"
                        all_prop_picks.extend(prop_picks)
                except Exception:
                    pass

            n = len(result["picks"])
            _log(f"{away} @ {home}: {n} pick(s)")

        # Guardar con stakes en DB
        all_picks = all_game_picks + all_prop_picks
        stake_map = bankroll.calc_stakes_moderado(live_bankroll, all_picks)
        for pick in all_picks:
            stake = stake_map.get(pick["selection"], 0)
            pick["stake_cop"] = stake
            database.save_pick(pick, stake_cop=stake)

        _state.update({
            "picks":     all_game_picks,
            "props":     all_prop_picks,
            "games":     game_results,
            "timestamp": datetime.now().strftime("%H:%M:%S"),
            "bankroll":  live_bankroll,
            "log":       log + ["✅ Generación completada"],
        })

    except Exception as e:
        import traceback
        _state["log"] = log + [f"❌ Error: {e}", traceback.format_exc()]
    finally:
        _state["generating"] = False


# ─── Rutas API ─────────────────────────────────────────────────────────────────

@app.route("/api/generate", methods=["POST"])
def api_generate():
    if _state["generating"]:
        return jsonify({"status": "already_running"})
    _state["generating"] = True
    _state["log"]        = ["Iniciando..."]
    threading.Thread(target=_pipeline, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/api/status")
def api_status():
    record = database.get_record()
    wins   = record.get("WIN",  {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    return jsonify({
        "generating": _state["generating"],
        "timestamp":  _state["timestamp"],
        "bankroll":   _state["bankroll"] or database.get_current_bankroll(config.BANKROLL),
        "picks":      _state["picks"],
        "props":      _state["props"],
        "log":        _state["log"],
        "record":     {"wins": wins, "losses": losses},
    })


@app.route("/api/pending")
def api_pending():
    return jsonify(database.get_pending_with_details())


@app.route("/api/history")
def api_history():
    record  = database.get_record()
    summary = database.get_roi_summary()
    with database.get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, bet_type, selection, odds,
                   result, stake_cop, profit_cop, confidence
            FROM picks
            WHERE result != 'PENDING'
            ORDER BY date DESC LIMIT 100
        """).fetchall()

    wins   = record.get("WIN",  {}).get("count", 0)
    losses = record.get("LOSS", {}).get("count", 0)
    pushes = record.get("PUSH", {}).get("count", 0)
    profit = sum(v["profit"] for v in record.values())

    return jsonify({
        "wins": wins, "losses": losses, "pushes": pushes,
        "profit":      profit,
        "bankroll":    database.get_current_bankroll(config.BANKROLL),
        "roi_summary": summary,
        "history": [
            {
                "id": r[0], "date": r[1], "game": r[2], "bet_type": r[3],
                "selection": r[4], "odds": r[5], "result": r[6],
                "stake_cop": r[7] or 0, "profit_cop": r[8] or 0,
                "confidence": r[9] or "",
            }
            for r in rows
        ],
    })


@app.route("/api/result", methods=["POST"])
def api_result():
    data   = request.json
    result = data.get("result", "").upper()
    if result not in ("WIN", "LOSS", "PUSH"):
        return jsonify({"error": "invalid"}), 400
    database.mark_result(int(data["id"]), result)
    return jsonify({"ok": True})


# ─── Frontend ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template_string(
        _HTML,
        min_edge=int(config.MIN_EDGE * 100),
        fetch_props=config.FETCH_PROPS,
    )


_HTML = r"""<!DOCTYPE html>
<html lang="es" data-bs-theme="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NBA Picks Bot</title>
  <link rel="stylesheet"
    href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css">
  <style>
    body { background: #0f172a; font-family: system-ui, sans-serif; }

    /* Nav */
    .top-nav { background: #1e293b; border-bottom: 1px solid #334155; }

    /* Tabs */
    .nav-tabs { border-bottom: 1px solid #334155; }
    .nav-tabs .nav-link { color: #94a3b8; border: none; padding: 10px 18px; }
    .nav-tabs .nav-link.active {
      color: #f8fafc; background: transparent;
      border-bottom: 2px solid #22c55e !important;
    }

    /* Pick cards */
    .pick-card {
      background: #1e293b;
      border: 1px solid #334155;
      border-radius: 12px;
      border-left: 4px solid #475569;
      transition: transform .15s;
    }
    .pick-card:hover { transform: translateY(-2px); }
    .pick-alta  { border-left-color: #22c55e !important; }
    .pick-media { border-left-color: #f59e0b !important; }
    .pick-baja  { border-left-color: #64748b !important; }

    .badge-alta  { background: #14532d !important; color: #86efac; }
    .badge-media { background: #78350f !important; color: #fde68a; }
    .badge-baja  { background: #1e293b !important; color: #94a3b8;
                   border: 1px solid #475569; }

    .odds-tag {
      font-size: 1rem; font-weight: 700;
      background: #0f172a; color: #e2e8f0;
      padding: 3px 10px; border-radius: 6px;
      white-space: nowrap;
    }

    /* Progress bar */
    .edge-track { height: 4px; background: #334155; border-radius: 2px; }
    .edge-fill  { height: 4px; border-radius: 2px;
                  background: linear-gradient(90deg, #16a34a, #22c55e); }

    /* Stat cards (historial) */
    .stat-card {
      background: #1e293b; border: 1px solid #334155; border-radius: 12px;
    }

    /* Log box */
    .log-box {
      background: #0a0f1a; border: 1px solid #334155; border-radius: 8px;
      font-family: monospace; font-size: .78rem; max-height: 140px;
      overflow-y: auto; padding: 10px 14px; color: #94a3b8;
    }

    /* Result colors */
    .r-win  { color: #22c55e; font-weight: 600; }
    .r-loss { color: #ef4444; font-weight: 600; }
    .r-push { color: #94a3b8; font-weight: 600; }

    .reasons-list { font-size: .8rem; color: #94a3b8; padding-left: 1.1rem; }
    .reasons-list li { padding: 1px 0; }

    table { font-size: .85rem; }
    .table > :not(caption) > * > * { border-color: #334155 !important; }
  </style>
</head>
<body>

<!-- Nav bar -->
<nav class="top-nav px-4 py-3 d-flex justify-content-between align-items-center">
  <span class="fw-bold fs-5 text-white">🏀 NBA Picks Bot</span>
  <div class="d-flex gap-4 align-items-center">
    <span class="text-muted small" id="nav-bankroll">—</span>
    <span class="text-muted small" id="nav-record">—</span>
  </div>
</nav>

<div class="container-fluid px-4 py-4" style="max-width:1200px;">

  <!-- Tabs -->
  <ul class="nav nav-tabs mb-4" id="tabs">
    <li class="nav-item">
      <a class="nav-link active" href="#" onclick="switchTab('picks');return false;">
        Picks de Hoy
      </a>
    </li>
    <li class="nav-item">
      <a class="nav-link" href="#" onclick="switchTab('pending');return false;">
        Pendientes&nbsp;<span id="pending-badge"
          class="badge rounded-pill text-dark"
          style="background:#f59e0b;font-size:.7rem;"></span>
      </a>
    </li>
    <li class="nav-item">
      <a class="nav-link" href="#" onclick="switchTab('history');return false;">
        Historial
      </a>
    </li>
  </ul>

  <!-- ─── TAB PICKS ────────────────────────────────────────────────────── -->
  <div id="tab-picks">
    <div class="d-flex align-items-center gap-3 mb-3">
      <button class="btn btn-success px-4" id="gen-btn" onclick="generatePicks()">
        <span id="gen-spin"
          class="spinner-border spinner-border-sm me-1 d-none"></span>
        Generar Picks
      </button>
      <span class="text-muted small" id="gen-info"></span>
    </div>

    <div id="log-wrap" class="mb-3 d-none">
      <div class="log-box" id="log-box"></div>
    </div>

    <div id="picks-empty" class="text-center py-5 text-muted d-none">
      <div style="font-size:2.5rem;">📭</div>
      <p class="mt-2">Sin picks con edge suficiente hoy.<br>
        <small>Mínimo requerido: {{ min_edge }}%</small></p>
    </div>

    <div id="picks-grid" class="row g-3"></div>
  </div>

  <!-- ─── TAB PENDIENTES ───────────────────────────────────────────────── -->
  <div id="tab-pending" class="d-none">
    <div id="pending-body"></div>
  </div>

  <!-- ─── TAB HISTORIAL ───────────────────────────────────────────────── -->
  <div id="tab-history" class="d-none">
    <div id="history-body"></div>
  </div>

</div><!-- /container -->

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js"></script>
<script>
// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────
const fmt  = n => Math.round(n).toLocaleString('es-CO');
const pct  = n => (n * 100).toFixed(1) + '%';
const odds = n => n > 0 ? '+' + n : '' + n;
const cop  = n => n ? '$' + fmt(n) + ' COP' : '—';

// ─────────────────────────────────────────────────────────────────────────────
// Tabs
// ─────────────────────────────────────────────────────────────────────────────
let currentTab = 'picks';
function switchTab(tab) {
  currentTab = tab;
  ['picks','pending','history'].forEach(t => {
    document.getElementById('tab-' + t).classList.toggle('d-none', t !== tab);
  });
  document.querySelectorAll('#tabs .nav-link').forEach((el, i) => {
    el.classList.toggle('active', ['picks','pending','history'][i] === tab);
  });
  if (tab === 'pending') loadPending();
  if (tab === 'history') loadHistory();
}

// ─────────────────────────────────────────────────────────────────────────────
// Generate picks
// ─────────────────────────────────────────────────────────────────────────────
let pollTimer = null;

async function generatePicks() {
  const r = await fetch('/api/generate', { method: 'POST' });
  const d = await r.json();
  if (d.status === 'already_running') return;

  document.getElementById('gen-btn').disabled = true;
  document.getElementById('gen-spin').classList.remove('d-none');
  document.getElementById('gen-info').textContent = 'Generando…';
  document.getElementById('log-wrap').classList.remove('d-none');
  clearInterval(pollTimer);
  pollTimer = setInterval(poll, 2000);
  poll();
}

async function poll() {
  const r    = await fetch('/api/status');
  const data = await r.json();

  // Live log
  const lb = document.getElementById('log-box');
  lb.innerHTML = (data.log || []).map(l => `<div>${escHtml(l)}</div>`).join('');
  lb.scrollTop = lb.scrollHeight;

  updateHeader(data);

  if (!data.generating) {
    clearInterval(pollTimer);
    document.getElementById('gen-btn').disabled = false;
    document.getElementById('gen-spin').classList.add('d-none');
    if (data.timestamp)
      document.getElementById('gen-info').textContent =
        'Última actualización: ' + data.timestamp;
    renderPicks(data.picks || [], data.props || []);
    refreshPendingBadge();
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Render pick cards
// ─────────────────────────────────────────────────────────────────────────────
function renderPicks(gamePicks, propPicks) {
  const grid  = document.getElementById('picks-grid');
  const empty = document.getElementById('picks-empty');
  const all   = [...gamePicks, ...propPicks];
  grid.innerHTML = '';

  if (!all.length) { empty.classList.remove('d-none'); return; }
  empty.classList.add('d-none');

  all.forEach((pick, i) => {
    const conf      = pick.confidence || 'BAJA';
    const cls       = conf === 'ALTA' ? 'alta' : conf === 'MEDIA' ? 'media' : 'baja';
    const emoji     = conf === 'ALTA' ? '🔥' : conf === 'MEDIA' ? '✅' : '⚠️';
    const edgePct   = (pick.edge * 100).toFixed(1);
    const barW      = Math.min(pick.edge * 600, 100).toFixed(0);
    const reasons   = (pick.reasons || []).map(r =>
      `<li>${escHtml(r)}</li>`).join('');
    const stakeStr  = pick.stake_cop ? cop(pick.stake_cop) : '—';

    grid.insertAdjacentHTML('beforeend', `
      <div class="col-md-6 col-xl-4">
        <div class="pick-card pick-${cls} p-4 d-flex flex-column h-100">

          <div class="d-flex justify-content-between align-items-start mb-2">
            <small class="text-muted">${escHtml(pick.game)}</small>
            <span class="badge badge-${cls} px-2 py-1 rounded-pill small">
              ${emoji} ${conf}
            </span>
          </div>

          <div class="text-muted small text-uppercase mb-1" style="font-size:.72rem;letter-spacing:.04em;">
            ${escHtml(pick.bet_type)}
          </div>

          <div class="d-flex align-items-center gap-2 mb-3 flex-wrap">
            <span class="fw-semibold text-white">${escHtml(pick.selection)}</span>
            <span class="odds-tag">${odds(pick.odds)}</span>
          </div>

          <div class="mb-3">
            <div class="d-flex justify-content-between mb-1">
              <small class="text-muted">Edge</small>
              <small class="fw-bold" style="color:#22c55e;">${edgePct}%</small>
            </div>
            <div class="edge-track">
              <div class="edge-fill" style="width:${barW}%"></div>
            </div>
          </div>

          <div class="d-flex justify-content-between mb-3 text-center">
            <div>
              <div style="font-size:.7rem;" class="text-muted mb-1">NUESTRA</div>
              <div class="fw-semibold">${pct(pick.our_prob)}</div>
            </div>
            <div>
              <div style="font-size:.7rem;" class="text-muted mb-1">CASA</div>
              <div class="text-muted">${pct(pick.implied_prob)}</div>
            </div>
            <div>
              <div style="font-size:.7rem;" class="text-muted mb-1">STAKE</div>
              <div class="fw-semibold text-warning small">${stakeStr}</div>
            </div>
          </div>

          <div class="mt-auto">
            <button class="btn btn-sm btn-outline-secondary w-100"
              onclick="toggleReasons('r${i}',this)">Por qué ▼</button>
            <ul class="reasons-list mt-2 d-none" id="r${i}">${reasons}</ul>
          </div>

        </div>
      </div>`);
  });
}

function toggleReasons(id, btn) {
  const hidden = document.getElementById(id).classList.toggle('d-none');
  btn.textContent = hidden ? 'Por qué ▼' : 'Por qué ▲';
}

// ─────────────────────────────────────────────────────────────────────────────
// Header stats
// ─────────────────────────────────────────────────────────────────────────────
function updateHeader(data) {
  const { wins = 0, losses = 0 } = data.record || {};
  const total   = wins + losses;
  const winPct  = total ? ((wins / total) * 100).toFixed(0) + '%' : '—';
  const br      = data.bankroll || 0;

  document.getElementById('nav-bankroll').textContent =
    `💰 $${fmt(br)} COP`;
  document.getElementById('nav-record').textContent =
    `📊 ${wins}W-${losses}L (${winPct})`;
}

// ─────────────────────────────────────────────────────────────────────────────
// Pending picks
// ─────────────────────────────────────────────────────────────────────────────
async function loadPending() {
  const r    = await fetch('/api/pending');
  const data = await r.json();
  refreshPendingBadge(data.length);

  const body = document.getElementById('pending-body');

  if (!data.length) {
    body.innerHTML = '<p class="text-muted text-center py-5">Sin picks pendientes de resultado.</p>';
    return;
  }

  const rows = data.map(p => `
    <tr>
      <td class="text-muted">${p.id}</td>
      <td class="text-muted">${p.date}</td>
      <td>${escHtml(p.game)}</td>
      <td>
        <div class="fw-semibold">${escHtml(p.bet_type)}</div>
        <div class="text-muted" style="font-size:.78rem;">${escHtml(p.selection)}</div>
      </td>
      <td class="fw-bold">${odds(p.odds)}</td>
      <td class="text-warning">${p.stake_cop ? cop(p.stake_cop) : '—'}</td>
      <td>
        <div class="d-flex gap-1">
          <button class="btn btn-sm btn-success px-2 py-0"
            onclick="markResult(${p.id},'WIN')">WIN</button>
          <button class="btn btn-sm btn-danger px-2 py-0"
            onclick="markResult(${p.id},'LOSS')">LOSS</button>
          <button class="btn btn-sm btn-secondary px-2 py-0"
            onclick="markResult(${p.id},'PUSH')">PUSH</button>
        </div>
      </td>
    </tr>`).join('');

  body.innerHTML = `
    <div class="table-responsive">
      <table class="table table-dark table-hover align-middle">
        <thead>
          <tr class="text-muted small">
            <th>#</th><th>Fecha</th><th>Partido</th><th>Apuesta</th>
            <th>Cuota</th><th>Stake</th><th>Resultado</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function markResult(id, result) {
  await fetch('/api/result', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ id, result }),
  });
  loadPending();
  const r = await fetch('/api/status');
  updateHeader(await r.json());
}

async function refreshPendingBadge(n) {
  if (n === undefined) {
    const r = await fetch('/api/pending');
    n = (await r.json()).length;
  }
  const badge = document.getElementById('pending-badge');
  badge.textContent = n || '';
}

// ─────────────────────────────────────────────────────────────────────────────
// Historial
// ─────────────────────────────────────────────────────────────────────────────
async function loadHistory() {
  const r    = await fetch('/api/history');
  const data = await r.json();
  const { wins, losses, pushes, profit, bankroll: br,
          roi_summary, history } = data;

  const total   = wins + losses;
  const winPct  = total ? ((wins / total) * 100).toFixed(1) + '%' : '—';
  const profColor = profit >= 0 ? '#22c55e' : '#ef4444';
  const profStr   = (profit >= 0 ? '+' : '') + '$' + fmt(Math.abs(profit));

  // ROI table
  const roiRows = (roi_summary || []).map(s => {
    const l   = s.total - s.wins;
    const rc  = s.roi >= 0 ? '#22c55e' : '#ef4444';
    const roi = (s.roi >= 0 ? '+' : '') + s.roi.toFixed(1) + '%';
    return `<tr>
      <td>${escHtml(s.tipo)}</td>
      <td>${s.wins}</td><td>${l}</td>
      <td>$${fmt(s.wagered)}</td>
      <td style="color:${s.profit>=0?'#22c55e':'#ef4444'}">
        ${s.profit>=0?'+':''}\$${fmt(Math.abs(s.profit))}</td>
      <td style="color:${rc};font-weight:600">${roi}</td>
    </tr>`;
  }).join('');

  // History rows
  const histRows = (history || []).map(h => {
    const rc = h.result === 'WIN' ? 'r-win'
             : h.result === 'LOSS' ? 'r-loss' : 'r-push';
    const pc = h.profit_cop >= 0 ? '#22c55e' : '#ef4444';
    return `<tr>
      <td class="text-muted">${h.date}</td>
      <td>${escHtml(h.game)}</td>
      <td class="text-muted">${escHtml(h.bet_type)}</td>
      <td>${escHtml(h.selection)}</td>
      <td class="fw-bold">${odds(h.odds)}</td>
      <td class="${rc}">${h.result}</td>
      <td style="color:${pc}">
        ${h.profit_cop>=0?'+':''}\$${fmt(Math.abs(h.profit_cop || 0))}
      </td>
    </tr>`;
  }).join('');

  document.getElementById('history-body').innerHTML = `
    <!-- Summary cards -->
    <div class="row g-3 mb-4">
      <div class="col-6 col-md-3">
        <div class="stat-card p-3 text-center">
          <div class="fs-4 fw-bold">${wins}W – ${losses}L</div>
          <div class="text-muted small">Record</div>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card p-3 text-center">
          <div class="fs-4 fw-bold">${winPct}</div>
          <div class="text-muted small">Win Rate</div>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card p-3 text-center">
          <div class="fs-4 fw-bold" style="color:${profColor}">${profStr}</div>
          <div class="text-muted small">Profit COP</div>
        </div>
      </div>
      <div class="col-6 col-md-3">
        <div class="stat-card p-3 text-center">
          <div class="fs-4 fw-bold">$${fmt(br)}</div>
          <div class="text-muted small">Bankroll</div>
        </div>
      </div>
    </div>

    ${roiRows ? `
    <p class="text-muted small text-uppercase mb-2" style="letter-spacing:.05em;">
      ROI por tipo de apuesta
    </p>
    <div class="table-responsive mb-4">
      <table class="table table-dark table-sm table-hover">
        <thead>
          <tr class="text-muted small">
            <th>Tipo</th><th>W</th><th>L</th>
            <th>Apostado</th><th>Profit</th><th>ROI</th>
          </tr>
        </thead>
        <tbody>${roiRows}</tbody>
      </table>
    </div>` : ''}

    ${histRows ? `
    <p class="text-muted small text-uppercase mb-2" style="letter-spacing:.05em;">
      Últimas 100 apuestas
    </p>
    <div class="table-responsive">
      <table class="table table-dark table-sm table-hover">
        <thead>
          <tr class="text-muted small">
            <th>Fecha</th><th>Partido</th><th>Tipo</th>
            <th>Apuesta</th><th>Cuota</th><th>Resultado</th><th>Profit</th>
          </tr>
        </thead>
        <tbody>${histRows}</tbody>
      </table>
    </div>` :
    '<p class="text-muted text-center py-4">Sin historial todavía.</p>'}`;
}

// ─────────────────────────────────────────────────────────────────────────────
// XSS safety
// ─────────────────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s ?? '')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────
(async () => {
  const r    = await fetch('/api/status');
  const data = await r.json();
  updateHeader(data);

  // Show cached picks if already generated this session
  if (data.timestamp) {
    document.getElementById('gen-info').textContent =
      'Última actualización: ' + data.timestamp;
    renderPicks(data.picks || [], data.props || []);
  }

  refreshPendingBadge();
})();
</script>
</body>
</html>"""


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    database.setup()
    import webbrowser, threading
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:5000")).start()
    print("\n  🏀  NBA Picks Bot — Dashboard")
    print("  Abre tu navegador en: http://localhost:5000")
    print("  Detener: Ctrl+C\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
