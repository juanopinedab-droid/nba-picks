"""
picks_mlb.py — Pipeline principal MLB

Uso:
    python picks_mlb.py                      # picks del día
    python picks_mlb.py --partido Yankees    # filtrar por equipo
    python picks_mlb.py --resolver           # resolver pendientes via MLB Stats API
    python picks_mlb.py --resultado 7 WIN    # marcar resultado manual
    python picks_mlb.py --pendientes         # ver picks pendientes
    python picks_mlb.py --historial          # ver historial + ROI
"""

import sys
import io
import json
import argparse
from datetime import date, timedelta, datetime, timezone
from typing import Optional

# Solo reemplazar stdout cuando se ejecuta directamente (no al importar desde daily_report)
if __name__ == "__main__" or getattr(sys.stdout, "encoding", "utf-8").lower() != "utf-8":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except AttributeError:
        pass  # ya es un StringIO buffer (ej. _capture_print)

import collector_mlb
import analyzer_mlb
import bankroll
import database
import config
import group_manager

# ── Estilo ────────────────────────────────────────────────────────────────────
BOLD  = "\033[1m"
RESET = "\033[0m"
_SEP  = "━" * 64
_SEP2 = "─" * 64
_SPORT = "mlb"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _game_date(game: dict) -> date:
    """Fecha local del partido extraída del commence_iso."""
    iso = game.get("commence_iso", "")
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return date.today()


_CONF_MULT = {"ALTA": 1.00, "MEDIA": 0.75, "BAJA": 0.50}

def _kelly_stake(prob: float, odds: int, confianza: str = "ALTA") -> float:
    """Kelly completo × multiplicador de confianza, capado al 10%."""
    if odds > 0:
        dec = odds / 100.0 + 1.0
    else:
        dec = 100.0 / abs(odds) + 1.0
    b = dec - 1.0
    if b <= 0:
        return 0.0
    kelly = (prob * dec - 1.0) / b
    mult = _CONF_MULT.get(confianza, 0.75)
    return max(0.0, min(kelly * mult, 0.10))


def _pick_to_db_format(pick: dict) -> dict:
    """
    Convierte un pick de analyzer_mlb al formato que espera database.save_pick().
    Soporta bet_type TOTAL y ML.
    """
    home      = pick["home_team"]
    away      = pick["away_team"]
    direction = pick["direction"]
    bet_type  = pick.get("bet_type", "TOTAL")
    game_str  = f"{away} @ {home}"
    conf_map  = {"ALTA": "ALTA", "MEDIA": "MEDIA", "BAJA": "BAJA", "FLACA": "FLACA", "SOSPECHA": "SOSPECHA"}

    if bet_type == "ML":
        team_name = pick.get("team", home if direction == "HOME" else away)
        selection = f"ML {team_name}"
        odds_val  = int(pick.get("odds", -110) or -110)
        return {
            "game":         game_str,
            "bet_type":     "MONEYLINE",
            "selection":    selection,
            "odds":         odds_val,
            "our_prob":     pick["our_prob"],
            "implied_prob": pick["fair_market"],
            "edge":         pick["edge"],
            "confidence":   conf_map.get(pick["confianza"], pick["confianza"]),
            "kelly_stake":  _kelly_stake(pick["our_prob"], odds_val),
            "sport":        _SPORT,
            "commence_time": pick.get("commence_iso", pick.get("game_time", "")),
            "context_flags": {
                "game_pk":      pick.get("game_pk"),
                "direction":    direction,
                "team":         team_name,
                "home_pitcher": pick.get("home_pitcher"),
                "away_pitcher": pick.get("away_pitcher"),
            },
        }

    if bet_type == "RL":
        team_name  = pick.get("team", home if direction == "HOME" else away)
        rl_point   = pick.get("rl_point", -1.5)
        spread_str = f"{rl_point:+.1f}"
        selection  = f"RL {team_name} {spread_str}"
        odds_val   = int(pick.get("odds", -110) or -110)
        return {
            "game":         game_str,
            "bet_type":     "RUNLINE",
            "selection":    selection,
            "odds":         odds_val,
            "our_prob":     pick["our_prob"],
            "implied_prob": pick["fair_market"],
            "edge":         pick["edge"],
            "confidence":   conf_map.get(pick["confianza"], pick["confianza"]),
            "kelly_stake":  _kelly_stake(pick["our_prob"], odds_val),
            "sport":        _SPORT,
            "commence_time": pick.get("commence_iso", pick.get("game_time", "")),
            "context_flags": {
                "game_pk":      pick.get("game_pk"),
                "direction":    direction,
                "team":         team_name,
                "rl_point":     rl_point,
                "home_pitcher": pick.get("home_pitcher"),
                "away_pitcher": pick.get("away_pitcher"),
            },
        }

    # F5 (primeros 5 innings — segundo mercado del nicho)
    if bet_type == "F5":
        line      = pick["line"]
        odds_val  = int(pick.get("odds", -110) or -110)
        return {
            "game":         game_str,
            "bet_type":     "F5",
            "selection":    f"{direction} {line}",   # mismo formato que TOTAL (el resolver lo parsea igual)
            "odds":         odds_val,
            "our_prob":     pick["our_prob"],
            "implied_prob": pick["fair_market"],
            "edge":         pick["edge"],
            "confidence":   conf_map.get(pick["confianza"], pick["confianza"]),
            "kelly_stake":  _kelly_stake(pick["our_prob"], odds_val),
            "sport":        _SPORT,
            "commence_time": pick.get("commence_iso", pick.get("game_time", "")),
            "context_flags": {
                "game_pk":      pick.get("game_pk"),
                "line":         line,
                "direction":    direction,
                "f5":           True,
                "home_pitcher": pick.get("home_pitcher"),
                "away_pitcher": pick.get("away_pitcher"),
            },
        }

    # TOTAL
    line      = pick["line"]
    selection = f"{direction} {line}"
    odds_val  = int(pick.get("odds", -110) or -110)
    return {
        "game":         game_str,
        "bet_type":     "TOTAL",
        "selection":    selection,
        "odds":         odds_val,
        "our_prob":     pick["our_prob"],
        "implied_prob": pick["fair_market"],
        "edge":         pick["edge"],
        "confidence":   conf_map.get(pick["confianza"], pick["confianza"]),
        "kelly_stake":  _kelly_stake(pick["our_prob"], odds_val),
        "sport":        _SPORT,
        "commence_time": pick.get("commence_iso", pick.get("game_time", "")),
        "context_flags": {
            "game_pk":      pick.get("game_pk"),
            "line":         line,
            "direction":    direction,
            "home_pitcher": pick.get("home_pitcher"),
            "away_pitcher": pick.get("away_pitcher"),
        },
    }



# ── Display ───────────────────────────────────────────────────────────────────

def _fecha_es(d: date) -> str:
    dias_es  = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
    meses_es = ["Ene","Feb","Mar","Abr","May","Jun","Jul","Ago","Sep","Oct","Nov","Dic"]
    return f"{dias_es[d.weekday()]} {d.day} {meses_es[d.month-1]}, {d.year}"


def _fair_american(prob: float) -> str:
    """Probabilidad → cuota decimal justa (sin vig). Ej: 0.65 → '1.54'."""
    if not prob or prob <= 0 or prob >= 1:
        return "N/D"
    return f"{1.0 / prob:.2f}"


def _display_header(n_games: int, n_picks: int, target_date: date | None = None) -> None:
    d = target_date or date.today()
    suffix = " (MAÑANA)" if d > date.today() else ""

    print()
    print(_SEP)
    print(f"  {BOLD}⚾  MLB PICKS — Totals (Over/Under carreras) — {_fecha_es(d)}{suffix}{RESET}")
    print(_SEP)
    print(f"  Partidos: {n_games}  |  Picks generados: {n_picks}")
    print(_SEP2)


def _display_pick(pick: dict, idx: int) -> None:
    home      = pick["home_team"]
    away      = pick["away_team"]
    direction = pick["direction"]
    line      = pick["line"]
    odds      = pick.get("odds", -110) or -110
    odds_str  = f"+{int(odds)}" if int(odds) > 0 else str(int(odds))
    edge      = pick["edge"]
    prob      = pick["our_prob"]
    fair      = pick["fair_market"]
    conf      = pick["confianza"]
    mu        = pick["mu_total"]
    exp_h     = pick["exp_home"]
    exp_a     = pick["exp_away"]

    conf_emoji = {"ALTA": "🔥", "MEDIA": "✅", "FLACA": "🔹", "SOSPECHA": "⚠️", "BAJA": "🔸"}.get(conf, "▸")

    print()
    print(f"  {conf_emoji} {BOLD}PICK #{idx} — MLB Totals{RESET}")
    print(f"     {away}  @  {home}")
    print(f"     ► {direction} {line}  ({odds_str})")
    print(f"     Confianza:   {conf}")
    print(f"     Edge:        {edge:.1%}  (nuestra {prob:.1%} vs mercado {fair:.1%})")
    print(f"     Cuota justa: {_fair_american(prob)}  (apostar solo si el mercado ofrece mejor)")
    print(f"     mu esperado: {mu:.1f} carreras  ({exp_a:.1f} visit. + {exp_h:.1f} local)")
    game_date = _game_date(pick) if "commence_iso" in pick else None
    fecha_str = f" ({_fecha_es(game_date)})" if game_date and game_date != date.today() else ""
    print(f"     Hora:        {pick.get('game_time', 'N/D')}{fecha_str}")

    # Análisis técnico
    print()
    print(f"     Analisis:")
    for reason in pick.get("reasons", []):
        print(f"       • {reason}")

    # Narrativa en lenguaje humano
    narrative = pick.get("narrative", "")
    if narrative:
        print()
        print(f"     {BOLD}Por que este pick:{RESET}")
        # Partir en líneas de ~80 chars para legibilidad en terminal
        words = narrative.split()
        line_buf: list[str] = []
        char_count = 0
        for word in words:
            if char_count + len(word) + 1 > 76 and line_buf:
                print(f"       {' '.join(line_buf)}")
                line_buf = [word]
                char_count = len(word)
            else:
                line_buf.append(word)
                char_count += len(word) + 1
        if line_buf:
            print(f"       {' '.join(line_buf)}")


def _display_f5_pick(pick: dict, idx: int) -> None:
    direction = pick["direction"]
    line      = pick["line"]
    odds      = pick.get("odds", -110) or -110
    odds_str  = f"+{int(odds)}" if int(odds) > 0 else str(int(odds))
    edge      = pick["edge"]
    prob      = pick["our_prob"]
    fair      = pick["fair_market"]
    conf      = pick["confianza"]
    mu        = pick["mu_total"]
    conf_emoji = {"ALTA": "🔥", "MEDIA": "✅", "FLACA": "🔹", "SOSPECHA": "⚠️", "BAJA": "🔸"}.get(conf, "▸")

    print()
    print(f"  {conf_emoji} {BOLD}F5 PICK #{idx} — Primeros 5 Innings{RESET}")
    print(f"     {pick['away_team']}  @  {pick['home_team']}")
    print(f"     ► F5 {direction} {line}  ({odds_str})")
    print(f"     Confianza:   {conf}")
    print(f"     Edge:        {edge:.1%}  (nuestra {prob:.1%} vs mercado {fair:.1%})")
    print(f"     Cuota justa: {_fair_american(prob)}  (apostar solo si el mercado ofrece mejor)")
    if direction == "OVER":
        print(f"     🎯 En vivo:   esperar pausa tras top 1er inning sin anotar → cuota sube")
        print(f"                  Ref. pre-game: {odds_str} | buscar mejor en live")
    print(f"     mu F5:       {mu:.1f} carr.  ({pick['exp_away']:.1f} visit. + {pick['exp_home']:.1f} local)")
    print(f"     Abridores:   {pick['away_pitcher']} xFIP {pick.get('away_fip_f5', '?')}  |  "
          f"{pick['home_pitcher']} xFIP {pick.get('home_fip_f5', '?')}")
    print()
    print(f"     Analisis (sin bullpen):")
    for reason in pick.get("reasons", []):
        print(f"       • {reason}")


def _display_ml_pick(pick: dict, idx: int) -> None:
    direction = pick["direction"]
    team      = pick.get("team", pick["home_team"] if direction == "HOME" else pick["away_team"])
    away      = pick["away_team"]
    home      = pick["home_team"]
    odds      = pick.get("odds", -110) or -110
    odds_str  = f"+{int(odds)}" if int(odds) > 0 else str(int(odds))
    edge      = pick["edge"]
    prob      = pick["our_prob"]
    fair      = pick["fair_market"]
    conf      = pick["confianza"]
    exp_h     = pick["exp_home"]
    exp_a     = pick["exp_away"]
    role_str  = "local" if direction == "HOME" else "visita"
    conf_emoji = {"ALTA": "🔥", "MEDIA": "✅", "FLACA": "🔹", "SOSPECHA": "⚠️", "BAJA": "🔸"}.get(conf, "▸")

    print()
    print(f"  {conf_emoji} {BOLD}ML PICK #{idx} — Moneyline{RESET}")
    print(f"     {away}  @  {home}")
    print(f"     ► {team.upper()} ({role_str})  ({odds_str})")
    print(f"     Confianza:   {conf}")
    print(f"     Edge:        {edge:.1%}  (nuestra {prob:.1%} vs mercado {fair:.1%})")
    print(f"     Cuota justa: {_fair_american(prob)}  (apostar solo si el mercado ofrece mejor)")
    print(f"     Proyección:  {exp_a:.1f} visit. + {exp_h:.1f} local = {exp_a + exp_h:.1f} total")
    game_date = _game_date(pick) if "commence_iso" in pick else None
    fecha_str = f" ({_fecha_es(game_date)})" if game_date and game_date != date.today() else ""
    print(f"     Hora:        {pick.get('game_time', 'N/D')}{fecha_str}")
    print()
    print(f"     Analisis:")
    for reason in pick.get("reasons", []):
        print(f"       • {reason}")


def _display_rl_pick(pick: dict, idx: int) -> None:
    direction  = pick["direction"]
    team       = pick.get("team", pick["home_team"] if direction == "HOME" else pick["away_team"])
    away       = pick["away_team"]
    home       = pick["home_team"]
    rl_point   = pick.get("rl_point", -1.5)
    spread_str = f"{rl_point:+.1f}"
    odds       = pick.get("odds", -110) or -110
    odds_str   = f"+{int(odds)}" if int(odds) > 0 else str(int(odds))
    edge       = pick["edge"]
    prob       = pick["our_prob"]
    fair       = pick["fair_market"]
    conf       = pick["confianza"]
    exp_h      = pick["exp_home"]
    exp_a      = pick["exp_away"]
    role_str   = "local" if direction == "HOME" else "visita"
    cover_desc = "ganar por 2+" if rl_point < 0 else "ganar o perder por 1"
    conf_emoji = {"ALTA": "🔥", "MEDIA": "✅", "FLACA": "🔹", "SOSPECHA": "⚠️", "BAJA": "🔸"}.get(conf, "▸")

    print()
    print(f"  {conf_emoji} {BOLD}RL PICK #{idx} — Run Line{RESET}")
    print(f"     {away}  @  {home}")
    print(f"     ► {team.upper()} {spread_str} ({role_str}) — necesita {cover_desc}  ({odds_str})")
    print(f"     Confianza:   {conf}")
    print(f"     Edge:        {edge:.1%}  (nuestra {prob:.1%} vs mercado {fair:.1%})")
    print(f"     Cuota justa: {_fair_american(prob)}  (apostar solo si el mercado ofrece mejor)")
    print(f"     Proyeccion:  {exp_a:.1f} visit. + {exp_h:.1f} local = {exp_a + exp_h:.1f} total")
    game_date = _game_date(pick) if "commence_iso" in pick else None
    fecha_str = f" ({_fecha_es(game_date)})" if game_date and game_date != date.today() else ""
    print(f"     Hora:        {pick.get('game_time', 'N/D')}{fecha_str}")
    print()
    print(f"     Analisis:")
    for reason in pick.get("reasons", []):
        print(f"       • {reason}")




# ── Pipeline principal ────────────────────────────────────────────────────────

def _display_group_verdict(agent_results: dict, games: list[dict]) -> None:
    """
    Muestra el veredicto del grupo por partido: quién apuesta qué y por qué.
    Incluye el nivel de consenso como señal de confianza adicional.
    """
    from group_manager import MEMBERS, AGENT_CONFIGS_MLB

    consensus = agent_results.get("consensus", {})

    # Construir mapa de picks por game_key y agente
    game_keys_with_picks: dict = {}
    for agent_key in MEMBERS:
        for p in agent_results[agent_key]["picks"]:
            gk = f"{p.get('away_team','')}@{p.get('home_team','')}|{p.get('direction','')}|{p.get('line','')}"
            if gk not in game_keys_with_picks:
                game_keys_with_picks[gk] = {"pick": p, "agents": []}
            game_keys_with_picks[gk]["agents"].append(agent_key)

    if not game_keys_with_picks:
        return

    print()
    print(_SEP)
    print(f"  {BOLD}🎯  VEREDICTO DEL GRUPO{RESET}")
    print(_SEP)

    for gk, info in game_keys_with_picks.items():
        p      = info["pick"]
        agents = consensus.get(gk, [])
        n      = len(agents)
        away   = p.get("away_team", "?")
        home   = p.get("home_team", "?")
        direct = p.get("direction", "")
        line   = p.get("line", "")

        # Nivel de consenso
        if n == 3:
            consensus_label = f"CONSENSO TOTAL 3/3 — {BOLD}señal muy fuerte{RESET}"
            consensus_color = "\033[92m"   # verde
        elif n == 2:
            consensus_label = f"MAYORIA 2/3 — señal moderada"
            consensus_color = "\033[93m"   # amarillo
        else:
            consensus_label = f"SOLO 1/3 — señal débil"
            consensus_color = "\033[91m"   # rojo

        print()
        print(f"  {away.split()[-1]} @ {home.split()[-1]}  |  "
              f"{direct} {line}  |  "
              f"{consensus_color}{consensus_label}{RESET}")

        for agent_key, m in MEMBERS.items():
            agent_picks = [p2 for p2 in agent_results[agent_key]["picks"]
                           if gk == f"{p2.get('away_team','')}@{p2.get('home_team','')}|{p2.get('direction','')}|{p2.get('line','')}"]
            cfg         = AGENT_CONFIGS_MLB.get(agent_key, {})
            bank        = group_manager.get_member_bankroll(agent_key)

            if agent_picks:
                pp    = agent_picks[0]
                stake = group_manager.get_member_stake(agent_key, bank, pp.get("confianza","BAJA"))
                pct   = stake / bank * 100 if bank > 0 else 0
                # Razón específica del agente
                reason_key = f"{agent_key}_reason"
                extra = pp.get(reason_key, "")
                print(f"     {m['emoji']} {m['display']:<6} [{m['posture']:<20}]  "
                      f"\033[92m✅ APUESTA  ${stake:,.0f} ({pct:.1f}%){RESET}"
                      + (f"\n            └─ {extra}" if extra else ""))
            else:
                # Mostrar por qué no apuesta
                skip_key    = f"{agent_key}_skip"
                all_skipped = agent_results[agent_key]["skipped"]
                skip_reason = ""
                for ps in all_skipped:
                    if ps.get(skip_key):
                        skip_reason = ps[skip_key]
                        break
                print(f"     {m['emoji']} {m['display']:<6} [{m['posture']:<20}]  "
                      f"\033[90m❌ NO APUESTA{RESET}"
                      + (f"\n            └─ {skip_reason}" if skip_reason else ""))

    print()
    print(_SEP)


def run_mlb_picks(partido: Optional[str] = None, solo_manana: bool = False,
                  paper: bool = False) -> None:
    if paper:
        print("\n  🧪 MODO PAPER — picks registrados con stake $0 para validación")
        print("     Filtros relajados: líneas hasta 10.5, UNDER habilitado, ancla al mercado desactivada")
        print("     Red amplia: K-props y F5 sin regla de oro ni ancla de mercado\n")
    print("\n  Obteniendo datos MLB...")

    # 1. Recolectar
    games = collector_mlb.get_todays_mlb_games()

    if not games:
        print("\n  No hay partidos MLB disponibles hoy (o todos ya comenzaron).\n")
        return

    # Filtro de fecha — get_todays_mlb_games devuelve hoy (no empezados) + mañana
    _today    = date.today()
    _tomorrow = _today + timedelta(days=1)
    if solo_manana:
        games = [g for g in games if _game_date(g) == _tomorrow]
        if not games:
            print("\n  No hay partidos MLB con odds disponibles para mañana.\n")
            return
    else:
        _today_games = [g for g in games if _game_date(g) == _today]
        # Auto-avance: si hoy ya no quedan juegos (noche o día de descanso), mañana
        games = _today_games if _today_games else [g for g in games if _game_date(g) == _tomorrow]
        if not games:
            print("\n  No hay partidos MLB próximos con odds disponibles.\n")
            return

    if partido:
        p_lower = partido.lower()
        games = [g for g in games if
                 p_lower in g.get("home_team", "").lower() or
                 p_lower in g.get("away_team", "").lower()]
        if not games:
            print(f"\n  No se encontro el partido con '{partido}'.\n")
            return

    # 2. Analizar con los 3 agentes en paralelo
    print(f"  Analizando {len(games)} partidos con 3 agentes...")
    agent_results = analyzer_mlb.analyze_all_games_per_agent(games)

    # Picks del modelo base (estándar) para el header y display
    raw_picks = analyzer_mlb.analyze_all_games(games)

    # Calcular todos los mercados antes del header para el conteo total
    # En paper mode: red amplia (paper=True desactiva ancla, regla de oro y gates)
    # En paper mode totales: relajar temporalmente la línea máxima y habilitar UNDER
    if paper:
        _orig_max_line    = analyzer_mlb._MAX_TOTAL_LINE
        _orig_allow_under = analyzer_mlb.ALLOW_UNDER
        _orig_mu_margin   = analyzer_mlb._OVER_MIN_MU_MARGIN
        analyzer_mlb._MAX_TOTAL_LINE    = 10.5   # ver picks en líneas altas
        analyzer_mlb.ALLOW_UNDER        = True    # red amplia
        analyzer_mlb._OVER_MIN_MU_MARGIN = 0.5   # margen más permisivo

    f5_picks = analyzer_mlb.analyze_all_f5_games(games) if not paper else [
        p for g in games for p in analyzer_mlb.analyze_f5_game(g, paper=True)
        if p.get("direction") in ("OVER", "UNDER")
    ]
    ml_picks = analyzer_mlb.analyze_all_games_ml(games)
    rl_picks = analyzer_mlb.analyze_all_games_rl(games)
    tt_picks = sorted(
        [p for g in games for p in analyzer_mlb.analyze_team_totals(g, paper=paper)],
        key=lambda p: p["edge"], reverse=True
    )

    if paper:
        analyzer_mlb._MAX_TOTAL_LINE     = _orig_max_line
        analyzer_mlb.ALLOW_UNDER         = _orig_allow_under
        analyzer_mlb._OVER_MIN_MU_MARGIN = _orig_mu_margin

    _total_picks = len(raw_picks) + len(f5_picks) + len(ml_picks) + len(rl_picks)

    # 3. Encabezado
    target_date = date.today() + timedelta(days=1) if solo_manana else date.today()
    _display_header(n_games=len(games), n_picks=_total_picks, target_date=target_date)

    # 4. Mostrar picks con análisis técnico + narrativa
    if not raw_picks:
        if _total_picks == 0:
            print()
            print(f"  Sin picks hoy en ningún mercado.")
            print()
        _display_upcoming(games)
    else:
        for i, raw in enumerate(raw_picks, 1):
            _display_pick(raw, i)
            print(_SEP2)

    # 5. Veredicto del grupo por partido
    _display_group_verdict(agent_results, games)

    # 6. Guardar picks de cada agente en la DB con su stake propio
    for agent_key in group_manager.MEMBERS:
        agent_picks = agent_results[agent_key]["picks"]
        cfg         = group_manager.AGENT_CONFIGS_MLB.get(agent_key, {})
        bank        = group_manager.get_member_bankroll(agent_key)

        for p in agent_picks:
            db_p  = _pick_to_db_format(p)
            conf  = p.get("confianza", "BAJA")
            stake = 0 if paper else group_manager.get_member_stake(agent_key, bank, conf)
            db_p["member"] = agent_key
            database.save_pick(db_p, stake_cop=stake)

    # 7. Otros partidos sin ningún pick
    all_agent_pks = set()
    for agent_key in group_manager.MEMBERS:
        for p in agent_results[agent_key]["picks"]:
            pk = p.get("game_pk")
            if pk is not None:
                all_agent_pks.add(pk)

    # También excluir partidos que aparecen en el veredicto del grupo (consensus)
    games_in_verdict = set()
    for ck in agent_results.get("consensus", {}):
        # key format: "AwayTeam@HomeTeam|direction|line"
        teams_part = ck.split("|")[0] if "|" in ck else ""
        games_in_verdict.add(teams_part)

    no_pick_games = [
        g for g in games
        if (g.get("game_pk") not in all_agent_pks or g.get("game_pk") is None)
        and f"{g.get('away_team','')}@{g.get('home_team','')}" not in games_in_verdict
    ]
    if no_pick_games:
        print()
        print("  Partidos sin pick de ningún agente:")
        for g in no_pick_games:
            home = g.get("home_team", "?")
            away = g.get("away_team", "?")
            line = g.get("total_line", "N/D")
            hp   = (g.get("home_pitcher") or {}).get("name", "TBD")
            ap   = (g.get("away_pitcher") or {}).get("name", "TBD")
            hf   = (g.get("home_pitcher") or {}).get("xfip_blended") or (g.get("home_pitcher") or {}).get("fip")
            af   = (g.get("away_pitcher") or {}).get("xfip_blended") or (g.get("away_pitcher") or {}).get("fip")
            hf_s = f"xFIP={hf:.2f}" if hf else "xFIP=N/D"
            af_s = f"xFIP={af:.2f}" if af else "xFIP=N/D"
            print(f"    {away} @ {home}  |  Total {line}  |  {ap} ({af_s}) vs {hp} ({hf_s})")

    # 8. F5 Picks (Primeros 5 Innings) — segundo mercado del nicho
    # F5 BAJA fuera del reporte apostable — solo ALTA y MEDIA tienen nicho demostrado
    f5_baja = [p for p in f5_picks if p.get("confianza") == "BAJA"]
    f5_picks = [p for p in f5_picks if p.get("confianza") != "BAJA"]

    if f5_picks:
        print()
        print(_SEP)
        label = "🧪 F5 PAPER" if paper else "⚾  F5"
        print(f"  {BOLD}{label} — PRIMEROS 5 INNINGS  (solo abridores, sin bullpen){RESET}")
        print(_SEP)
        for idx, f5p in enumerate(f5_picks, 1):
            _display_f5_pick(f5p, idx)
        bankroll_val = database.get_current_bankroll(config.BANKROLL)
        for f5p in f5_picks:
            db_p = _pick_to_db_format(f5p)
            db_p["member"] = "base"
            stake = 0 if paper else int(_kelly_stake(f5p["our_prob"], int(f5p.get("odds", -110) or -110), f5p.get("confianza", "ALTA")) * bankroll_val)
            database.save_pick(db_p, stake_cop=stake)

    if f5_baja:
        print()
        print(_SEP)
        print(f"  {BOLD}📋 F5 BAJA CONFIANZA — Revisar cuota antes de entrar{RESET}")
        print(_SEP)
        for idx, _b in enumerate(f5_baja, 1):
            _display_f5_pick(_b, idx)
            print(_SEP2)
    else:
        print(f"\n  ℹ️  Sin picks F5 con edge suficiente hoy.")

    # 9. Moneyline Picks
    if ml_picks:
        print()
        print(_SEP)
        label = "🧪 ML PAPER" if paper else "⚾  MONEYLINE"
        print(f"  {BOLD}{label} — Ganador del partido{RESET}")
        print(_SEP)
        for idx, mlp in enumerate(ml_picks, 1):
            _display_ml_pick(mlp, idx)
            print(_SEP2)
        bankroll_val = database.get_current_bankroll(config.BANKROLL)
        for mlp in ml_picks:
            db_p = _pick_to_db_format(mlp)
            db_p["member"] = "base"
            stake = 0 if paper else int(_kelly_stake(mlp["our_prob"], int(mlp.get("odds", -110) or -110), mlp.get("confianza", "ALTA")) * bankroll_val)
            database.save_pick(db_p, stake_cop=stake)
    else:
        print(f"\n  ℹ️  Sin picks Moneyline con edge suficiente hoy.")

    # 10. Run Line Picks
    if rl_picks:
        print()
        print(_SEP)
        label = "🧪 RL PAPER" if paper else "⚾  RUN LINE"
        print(f"  {BOLD}{label} — Spread ±1.5 carreras{RESET}")
        print(_SEP)
        for idx, rlp in enumerate(rl_picks, 1):
            _display_rl_pick(rlp, idx)
            print(_SEP2)
        bankroll_val = database.get_current_bankroll(config.BANKROLL)
        for rlp in rl_picks:
            db_p = _pick_to_db_format(rlp)
            db_p["member"] = "base"
            stake = 0 if paper else int(_kelly_stake(rlp["our_prob"], int(rlp.get("odds", -110) or -110), rlp.get("confianza", "ALTA")) * bankroll_val)
            database.save_pick(db_p, stake_cop=stake)
    else:
        print(f"\n  ℹ️  Sin picks Run Line con edge suficiente hoy.")

    # 11. Team Run Totals — tracking only
    if tt_picks:
        print(f"\n{_SEP}")
        print(f"  \033[1m📊 TEAM RUN TOTALS SEGUIMIENTO (no apostar) — Carreras por equipo\033[0m")
        print(_SEP)
        for idx, tt in enumerate(tt_picks, 1):
            conf_icon = {"ALTA": "🔥", "MEDIA": "✅", "BAJA": "🔸"}.get(tt["confianza"], "•")
            print(f"\n  {conf_icon} \033[1mTEAM TOTAL #{idx}\033[0m")
            print(f"     {tt['away_team']}  @  {tt['home_team']}")
            print(f"     ► {tt['direction']} {tt['line']} carreras — {tt['team']}  ({tt['odds']:+d})")
            print(f"     Confianza:   {tt['confianza']}")
            print(f"     Edge:        {tt['edge']:.1%}  (nuestra {tt['our_prob']:.1%} vs mercado {tt['fair_prob']:.1%})")
            print(f"     Exp. runs:   {tt['exp_runs']:.1f}  vs línea {tt['line']}")
            print(f"     Pitcher opp: {tt['opp_pitcher']}")
            print(f"     Hora:        {tt.get('game_time','?')}")
            print(_SEP2)
            db_p = _pick_to_db_format(tt)
            db_p["member"] = "base"
            database.save_pick(db_p, stake_cop=0)  # siempre tracking, stake=0

    # 13. Footer
    with database.get_conn() as _conn:
        _wins   = (_conn.execute("SELECT COUNT(*) FROM picks WHERE sport=? AND result='WIN'",  (_SPORT,)).fetchone() or [0])[0]
        _losses = (_conn.execute("SELECT COUNT(*) FROM picks WHERE sport=? AND result='LOSS'", (_SPORT,)).fetchone() or [0])[0]
    _total  = _wins + _losses
    roi_str = f"  |  MLB: {_wins}W-{_losses}L ({_wins/_total:.0%} WR)" if _total > 0 else ""

    # Resumen compacto de picks por agente
    zeus_n  = len(agent_results["zeus"]["picks"])
    atena_n = len(agent_results["atena"]["picks"])
    hades_n = len(agent_results["hades"]["picks"])

    print(f"\n{_SEP}")
    print(f"  Picks: Zeus {zeus_n} | Atena {atena_n} | Hades {hades_n}{roi_str}")
    print(f"  Leaderboard: python picks_mlb.py --grupo")
    print(f"  Resolver:    python picks_mlb.py --resolver")
    print(f"{_SEP}\n")


def _display_upcoming(games: list[dict]) -> None:
    print("  Partidos de hoy:")
    for g in games:
        home = g.get("home_team", "?")
        away = g.get("away_team", "?")
        line = g.get("total_line", "N/D")
        hp   = (g.get("home_pitcher") or {}).get("name", "TBD")
        ap   = (g.get("away_pitcher") or {}).get("name", "TBD")
        hf   = (g.get("home_pitcher") or {}).get("fip")
        af   = (g.get("away_pitcher") or {}).get("fip")
        hf_s = f"FIP={hf:.2f}" if hf else "FIP=N/D"
        af_s = f"FIP={af:.2f}" if af else "FIP=N/D"
        print(f"    {away} @ {home}  |  Total {line}  |  {ap} ({af_s}) vs {hp} ({hf_s})")
    print()


# ── Resolver pendientes ───────────────────────────────────────────────────────

def _game_is_final(game_pk: int) -> bool:
    """True si el partido ya terminó oficialmente."""
    import requests
    try:
        r = requests.get("https://statsapi.mlb.com/api/v1/schedule",
                         params={"gamePk": game_pk}, timeout=15)
        dates = r.json().get("dates", [])
        if not dates:
            return False
        status = dates[0]["games"][0].get("status", {}).get("detailedState", "")
        return "Final" in status
    except Exception:
        return False


def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _name_match(api_name: str, search_name: str) -> bool:
    """Compara nombres ignorando acentos, mayúsculas y sufijos (Jr., III)."""
    def _clean(n: str) -> str:
        return _strip_accents(n).lower().replace(".", "").replace(",", "")
    a, b = _clean(api_name), _clean(search_name)
    if a == b:
        return True
    # Coincidencia por apellido (última palabra no-sufijo)
    _SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
    def _last_name(n: str) -> str:
        parts = [p for p in n.split() if p not in _SUFFIXES]
        return parts[-1] if parts else n
    return _last_name(a) == _last_name(b) and len(b.split()) >= 2


_PROP_VOID = "VOID"   # jugador en el roster pero no disputó → apuesta anulada (PUSH)


def _get_prop_stats(game_pk: int, player_name: str, prop_type: str) -> float | str | None:
    """
    Retorna las estadísticas reales de un jugador en el partido:
      prop_type="TB"  → totalBases (bateador)
      prop_type="K"   → strikeOuts (pitcher)

    Tres retornos posibles:
      float        → el valor real (resolver normal)
      _PROP_VOID   → jugador listado pero NO disputó (scratched) → la casa anula
                     la apuesta (PUSH). Distinto de "pendiente": no esperar más.
      None         → partido no final o jugador no encontrado (sigue pendiente)
    """
    import requests
    if not _game_is_final(game_pk):
        return None
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1/game/{game_pk}/boxscore",
                         timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return None

    stat_key = "batting" if prop_type == "TB" else "pitching"
    field    = "totalBases" if prop_type == "TB" else "strikeOuts"

    for side in ("home", "away"):
        for p in data["teams"][side].get("players", {}).values():
            api_name = p.get("person", {}).get("fullName", "")
            if not _name_match(api_name, player_name):
                continue
            sub = p.get("stats", {}).get(stat_key, {})
            # Línea vacía = en el roster pero no jugó (scratched / no entró) → VOID.
            if not sub:
                return _PROP_VOID
            val = sub.get(field)
            return float(val) if val is not None else _PROP_VOID

    return None  # jugador no encontrado en el boxscore


def _get_game_score(game_pk: int) -> tuple[int, int] | None:
    """Retorna (away_runs, home_runs) para un game_pk finalizado, o None."""
    import requests
    BASE = "https://statsapi.mlb.com/api/v1"
    try:
        r = requests.get(f"{BASE}/schedule", params={"gamePk": game_pk}, timeout=15)
        dates = r.json().get("dates", [])
        if not dates:
            return None
        status = dates[0]["games"][0].get("status", {}).get("detailedState", "")
        if "Final" not in status:
            return None
        r2 = requests.get(f"{BASE}/game/{game_pk}/linescore", timeout=15)
        ls = r2.json().get("teams", {})
        away = ls.get("away", {}).get("runs")
        home = ls.get("home", {}).get("runs")
        if away is None or home is None:
            return None
        return int(away), int(home)
    except Exception:
        return None


def _get_f5_score(game_pk: int) -> tuple[float, float] | None:
    """
    Retorna (away_runs_f5, home_runs_f5) sumando innings 1-5 del linescore.
    Requiere que el partido haya terminado (mínimo 5 innings completos).
    """
    import requests
    BASE = "https://statsapi.mlb.com/api/v1"
    try:
        r = requests.get(f"{BASE}/schedule", params={"gamePk": game_pk}, timeout=15)
        dates = r.json().get("dates", [])
        if not dates:
            return None
        status = dates[0]["games"][0].get("status", {}).get("detailedState", "")
        if "Final" not in status:
            return None
        r2 = requests.get(f"{BASE}/game/{game_pk}/linescore", timeout=15)
        data = r2.json()
        innings = data.get("innings", [])
        if len(innings) < 5:
            return None  # partido incompleto / suspendido antes del inning 5
        away_f5 = sum(inn.get("away", {}).get("runs", 0) or 0 for inn in innings[:5])
        home_f5 = sum(inn.get("home", {}).get("runs", 0) or 0 for inn in innings[:5])
        return float(away_f5), float(home_f5)
    except Exception:
        return None


def _find_game_pk_by_teams(away_team: str, home_team: str, game_date: str) -> int | None:
    """
    Busca el game_pk en la MLB Stats API por nombre de equipos y fecha.
    Fallback cuando context_flags no tiene game_pk (picks guardados antes de v2).
    """
    import requests
    BASE = "https://statsapi.mlb.com/api/v1"
    try:
        r = requests.get(f"{BASE}/schedule", params={
            "sportId": 1, "date": game_date,
            "hydrate": "team",
        }, timeout=15)
        for d in r.json().get("dates", []):
            for g in d.get("games", []):
                a = g["teams"]["away"]["team"]["name"].lower()
                h = g["teams"]["home"]["team"]["name"].lower()
                # Match parcial (ej. "Astros" en "Houston Astros")
                away_key = away_team.split()[-1].lower()  # última palabra del nombre
                home_key = home_team.split()[-1].lower()
                if away_key in a and home_key in h:
                    return g.get("gamePk")
    except Exception:
        pass
    return None


def _resolver_mlb() -> None:
    """
    Resuelve picks MLB pendientes consultando la MLB Stats API.

    Flujo para cada pick pendiente:
    1. Si tiene game_pk en context_flags → fetch directo del marcador
    2. Si no → busca por nombre de equipo + fecha (picks legacy sin context_flags)
    3. Calcula OVER/UNDER vs línea → marca WIN/LOSS/PUSH en la DB
    """
    import json

    pending = database.get_pending_with_details()
    mlb_pending = [p for p in pending if p.get("sport") == _SPORT]

    if not mlb_pending:
        print("  No hay picks MLB pendientes.")
        return

    print(f"  Revisando {len(mlb_pending)} picks pendientes...\n")
    resolved = skipped = 0

    for pick in mlb_pending:
        pick_id  = pick["id"]
        game_str = pick.get("game", "?")      # "Houston Astros @ Texas Rangers"
        selection = pick.get("selection", "") # "OVER 7.5"
        pick_date = pick.get("date", "")

        # — Parsear dirección y línea desde selection o context_flags —
        ctx = {}
        try:
            ctx_raw = pick.get("context_flags")
            ctx = json.loads(ctx_raw) if isinstance(ctx_raw, str) else (ctx_raw or {})
            if isinstance(ctx, str):   # doble-serializado: volver a parsear
                ctx = json.loads(ctx)
            if not isinstance(ctx, dict):
                ctx = {}
        except Exception:
            ctx = {}

        bet_type  = pick.get("bet_type", "TOTAL")
        is_ml     = bet_type == "MONEYLINE"
        is_rl     = bet_type == "RUNLINE"
        is_prop   = bet_type in ("PROP", "K_PROP", "TB_PROP")   # PROP = histórico pre-nicho

        # ── PROP: TB o K ─────────────────────────────────────────────────────
        if is_prop:
            import re as _re
            m = _re.match(
                r'(OVER|UNDER)\s+([\d.]+)\s+(TB|Ks?)\s*[—\-]+\s*(.+)',
                selection, _re.IGNORECASE
            )
            if not m:
                print(f"  ⚠️  Pick #{pick_id}: no se pudo parsear prop '{selection}'")
                skipped += 1
                continue

            prop_dir    = m.group(1).upper()          # OVER / UNDER
            prop_line   = float(m.group(2))
            prop_type   = "TB" if "TB" in m.group(3).upper() else "K"
            player_name = m.group(4).strip()

            # Obtener game_pk (igual que el flujo general)
            game_pk = ctx.get("game_pk")
            if not game_pk and "@" in game_str:
                parts_game = game_str.split("@")
                away_name  = parts_game[0].strip()
                home_name  = parts_game[1].strip()
                commence   = pick.get("commence_time", "")
                try:
                    actual_date = str(datetime.fromisoformat(
                        commence.replace("Z", "+00:00")).astimezone().date())
                except Exception:
                    actual_date = None
                for search_date in filter(None, [actual_date, pick_date,
                                                 str(date.today()),
                                                 str(date.today() + timedelta(days=1))]):
                    game_pk = _find_game_pk_by_teams(away_name, home_name, search_date)
                    if game_pk:
                        break

            if not game_pk:
                print(f"  ⚠️  Pick #{pick_id} ({game_str}): no se encontró game_pk")
                skipped += 1
                continue

            actual_val = _get_prop_stats(game_pk, player_name, prop_type)
            if actual_val is None:
                print(f"  ⏳ Pick #{pick_id}: sin datos para {player_name} ({prop_type})")
                skipped += 1
                continue

            if actual_val == _PROP_VOID:
                database.mark_result(pick_id, "PUSH")
                print(f"  ↩️  Pick #{pick_id}: {player_name} no disputó el partido "
                      f"(scratched) → anulado (PUSH, stake devuelto)")
                resolved += 1
                continue

            if actual_val == prop_line:
                outcome = "PUSH"
            elif prop_dir == "OVER":
                outcome = "WIN" if actual_val > prop_line else "LOSS"
            else:
                outcome = "WIN" if actual_val < prop_line else "LOSS"

            database.mark_result(pick_id, outcome)
            icon = "✅" if outcome == "WIN" else ("↩️" if outcome == "PUSH" else "❌")
            prop_label = "TB" if prop_type == "TB" else "Ks"
            print(f"  {icon} Pick #{pick_id}: {game_str}")
            print(f"     {prop_dir} {prop_line} {prop_label} — {player_name}"
                  f"  |  Real: {actual_val:.0f} {prop_label}  →  {outcome}")
            print()
            resolved += 1
            continue

        # Dirección y línea: primero de context_flags, después parsear selection
        direction = ctx.get("direction", "")
        line      = ctx.get("line")

        if is_ml or is_rl:
            # ML/RL picks: no hay línea de totales
            if not direction:
                direction = ctx.get("direction", "")
            line_val = None
        else:
            if not direction or line is None:
                parts = selection.upper().split()
                if len(parts) == 2 and parts[0] in ("OVER", "UNDER"):
                    direction, line = parts[0], parts[1]
                elif len(parts) == 3 and parts[0] == "F5" and parts[1] in ("OVER", "UNDER"):
                    direction, line = parts[1], parts[2]
                else:
                    print(f"  ⚠️  Pick #{pick_id}: no se pudo parsear '{selection}'")
                    skipped += 1
                    continue
            try:
                line_val = float(line)
            except (ValueError, TypeError):
                skipped += 1
                continue

        # — Obtener game_pk —
        game_pk = ctx.get("game_pk")
        if not game_pk and "@" in game_str:
            # Fallback: buscar por nombres de equipo
            # Intentar con la fecha del pick y también el día siguiente
            # (picks de partidos de mañana se guardan con fecha de hoy)
            parts_game = game_str.split("@")
            away_name  = parts_game[0].strip()
            home_name  = parts_game[1].strip()
            # Extraer fecha real desde commence_time (ISO) si está guardado
            commence = pick.get("commence_time", "")
            try:
                actual_date = str(datetime.fromisoformat(
                    commence.replace("Z", "+00:00")).astimezone().date())
            except Exception:
                actual_date = None
            for search_date in filter(None, [actual_date, pick_date,
                                             str(date.today()),
                                             str(date.today() + timedelta(days=1))]):
                game_pk = _find_game_pk_by_teams(away_name, home_name, search_date)
                if game_pk:
                    break

        if not game_pk:
            print(f"  ⚠️  Pick #{pick_id} ({game_str}): no se encontró game_pk")
            skipped += 1
            continue

        # — Obtener marcador (F5 o juego completo) —
        is_f5 = ctx.get("f5", False) or bet_type == "F5"
        if is_f5:
            score = _get_f5_score(game_pk)
        else:
            score = _get_game_score(game_pk)
        if score is None:
            print(f"  ⏳ Pick #{pick_id} ({game_str}): partido aún no terminó o sin datos")
            skipped += 1
            continue

        away_runs, home_runs = score
        total_runs = away_runs + home_runs

        # — Evaluar resultado —
        if is_ml:
            home_won = home_runs > away_runs
            if direction == "HOME":
                outcome = "WIN" if home_won else "LOSS"
            else:  # AWAY
                outcome = "WIN" if not home_won else "LOSS"
            database.mark_result(pick_id, outcome)
            icon = "✅" if outcome == "WIN" else "❌"
            team_picked = ctx.get("team", direction)
            print(f"  {icon} Pick #{pick_id}: {game_str}")
            print(f"     ML {team_picked}  |  Real: {away_runs}-{home_runs}  ->  {outcome}")
        elif is_rl:
            rl_point = float(ctx.get("rl_point", -1.5) or -1.5)
            team_picked = ctx.get("team", direction)
            # run_diff_picked = runs del equipo apostado menos runs del rival
            run_diff_picked = (home_runs - away_runs) if direction == "HOME" else (away_runs - home_runs)
            if rl_point < 0:
                # apostando -1.5: necesita ganar por 2+
                outcome = "WIN" if run_diff_picked >= 2 else "LOSS"
            else:
                # apostando +1.5: cubre si gana o pierde por 1
                outcome = "WIN" if run_diff_picked >= -1 else "LOSS"
            database.mark_result(pick_id, outcome)
            icon = "✅" if outcome == "WIN" else "❌"
            spread_str = f"{rl_point:+.1f}"
            print(f"  {icon} Pick #{pick_id}: {game_str}")
            print(f"     RL {team_picked} {spread_str}  |  Real: {away_runs}-{home_runs}  ->  {outcome}")
        else:
            if total_runs == line_val:
                outcome = "PUSH"
            elif direction == "OVER":
                outcome = "WIN" if total_runs > line_val else "LOSS"
            else:  # UNDER
                outcome = "WIN" if total_runs < line_val else "LOSS"
            database.mark_result(pick_id, outcome)
            icon = "✅" if outcome == "WIN" else ("↩️" if outcome == "PUSH" else "❌")
            print(f"  {icon} Pick #{pick_id}: {game_str}")
            print(f"     {direction} {line_val}  |  Real: {away_runs}+{home_runs}={total_runs}  →  {outcome}")
        print()
        resolved += 1

    print(f"  Resueltos: {resolved}  |  Saltados/pendientes: {skipped}")


# ── Historial ─────────────────────────────────────────────────────────────────

def _display_historial() -> None:
    """Historial y ROI filtrado solo para picks MLB."""
    with database.get_conn() as conn:
        rows = conn.execute("""
            SELECT id, date, game, selection, odds, stake_cop,
                   result, profit_cop, bet_type
            FROM picks
            WHERE sport = ?
            ORDER BY date DESC
        """, (_SPORT,)).fetchall()

    if not rows:
        print(f"\n  Sin historial MLB aun.\n")
        return

    keys = ["id","date","game","selection","odds","stake_cop",
            "result","profit_cop","bet_type"]
    picks = [dict(zip(keys, r)) for r in rows]

    # Separar los tres mercados
    totals_picks  = [p for p in picks if p.get("bet_type") == "TOTAL"]
    ml_picks_hist = [p for p in picks if p.get("bet_type") == "MONEYLINE"]
    rl_picks_hist = [p for p in picks if p.get("bet_type") == "RUNLINE"]

    def _stats_block(subset: list[dict]) -> tuple[int,int,int,float,float,float]:
        resolved_s = [p for p in subset if p["result"] in ("WIN","LOSS","PUSH")]
        w = sum(1 for p in resolved_s if p["result"] == "WIN")
        l = sum(1 for p in resolved_s if p["result"] == "LOSS")
        n = w + l
        pf = sum(p.get("profit_cop", 0) or 0 for p in resolved_s)
        st = sum(p.get("stake_cop",  0) or 0 for p in resolved_s)
        roi = (pf / st * 100) if st > 0 else 0.0
        wr  = (w / n * 100) if n > 0 else 0.0
        return w, l, n, pf, wr, roi

    # ── Totales (OVER/UNDER) ──────────────────────────────────────────────────
    print()
    print(_SEP)
    print(f"  {BOLD}MLB — Historial Totales (OVER/UNDER){RESET}")
    print(_SEP)
    w, l, n, pf, wr, roi = _stats_block(totals_picks)
    if n > 0:
        print(f"  Resueltos: {n}  |  W: {w}  L: {l}  |  WR: {wr:.1f}%  |  ROI: {roi:+.1f}%")
        print(_SEP2)
        for direction in ("OVER", "UNDER"):
            subset = [p for p in totals_picks if direction in (p.get("selection") or "")]
            sw, sl, sn, spf, swr, sroi = _stats_block(subset)
            if sn > 0:
                print(f"  {direction:6s}: {sn:3d} picks | WR {swr:.0f}% | ROI {sroi:+.1f}%")
        print()
        print("  Últimos 10 picks totales:")
        for p in totals_picks[:10]:
            ri = "✅" if p["result"] == "WIN" else "❌" if p["result"] == "LOSS" else "?"
            fecha = (p.get("date") or "")[:10]
            profit_s = f"{p.get('profit_cop',0) or 0:+,.0f}"
            print(f"    {ri} [{fecha}] {p['game']:<30}  {p['selection']:<12}  {profit_s} COP")
    else:
        print(f"  Sin picks de totales resueltos aún.")

    # ── Moneyline ─────────────────────────────────────────────────────────────
    print()
    print(_SEP)
    print(f"  {BOLD}MLB — Historial Moneyline{RESET}")
    print(_SEP)
    w, l, n, pf, wr, roi = _stats_block(ml_picks_hist)
    if n > 0:
        print(f"  Resueltos: {n}  |  W: {w}  L: {l}  |  WR: {wr:.1f}%  |  ROI: {roi:+.1f}%")
        print(_SEP2)
        print("  Últimos picks ML:")
        for p in ml_picks_hist[:15]:
            ri = "✅" if p["result"] == "WIN" else "❌" if p["result"] == "LOSS" else "⏳"
            fecha = (p.get("date") or "")[:10]
            sel   = p.get("selection", "?")
            odds  = p.get("odds", 0) or 0
            odds_s = f"+{odds}" if odds > 0 else str(odds)
            profit_s = f"{p.get('profit_cop',0) or 0:+,.0f}" if p["result"] in ("WIN","LOSS") else "—"
            print(f"    {ri} [{fecha}] {p['game']:<28}  {sel:<22} ({odds_s})  {profit_s} COP")
    else:
        print(f"  Sin picks ML resueltos aún — acumulando muestra.")
        pending_ml = [p for p in ml_picks_hist if p["result"] not in ("WIN","LOSS","PUSH")]
        if pending_ml:
            print(f"  Pendientes: {len(pending_ml)}")

    # ── Run Line ──────────────────────────────────────────────────────────────
    print()
    print(_SEP)
    print(f"  {BOLD}MLB — Historial Run Line (±1.5){RESET}")
    print(_SEP)
    w, l, n, pf, wr, roi = _stats_block(rl_picks_hist)
    if n > 0:
        print(f"  Resueltos: {n}  |  W: {w}  L: {l}  |  WR: {wr:.1f}%  |  ROI: {roi:+.1f}%")
        print(_SEP2)
        # Desglose por favorito (-1.5) vs underdog (+1.5)
        fav_rl  = [p for p in rl_picks_hist if "-1.5" in (p.get("selection") or "")]
        dog_rl  = [p for p in rl_picks_hist if "+1.5" in (p.get("selection") or "")]
        fw, fl, fn, _, fwr, froi = _stats_block(fav_rl)
        dw, dl, dn, _, dwr, droi = _stats_block(dog_rl)
        if fn > 0:
            print(f"  Fav (-1.5): {fn:3d} picks | WR {fwr:.0f}% | ROI {froi:+.1f}%")
        if dn > 0:
            print(f"  Dog (+1.5): {dn:3d} picks | WR {dwr:.0f}% | ROI {droi:+.1f}%")
        print()
        print("  Últimos picks RL:")
        for p in rl_picks_hist[:15]:
            ri = "✅" if p["result"] == "WIN" else "❌" if p["result"] == "LOSS" else "⏳"
            fecha = (p.get("date") or "")[:10]
            sel   = p.get("selection", "?")
            odds  = p.get("odds", 0) or 0
            odds_s = f"+{odds}" if odds > 0 else str(odds)
            profit_s = f"{p.get('profit_cop',0) or 0:+,.0f}" if p["result"] in ("WIN","LOSS") else "—"
            print(f"    {ri} [{fecha}] {p['game']:<28}  {sel:<22} ({odds_s})  {profit_s} COP")
    else:
        print(f"  Sin picks RL resueltos aún — acumulando muestra.")
        pending_rl = [p for p in rl_picks_hist if p["result"] not in ("WIN","LOSS","PUSH")]
        if pending_rl:
            print(f"  Pendientes: {len(pending_rl)}")
    print()


# ── Pendientes ────────────────────────────────────────────────────────────────

def _display_pendientes() -> None:
    pending = database.get_pending_with_details()
    mlb_pending = [p for p in pending if p.get("sport") == _SPORT]

    if not mlb_pending:
        print("\n  No hay picks MLB pendientes.\n")
        return

    print()
    print(_SEP)
    print(f"  {BOLD}MLB — Picks pendientes ({len(mlb_pending)}){RESET}")
    print(_SEP)
    for p in mlb_pending:
        game    = p.get("game", "?")
        sel     = p.get("selection", "?")
        stake   = p.get("stake_cop", 0) or 0
        fecha   = (p.get("date") or "")[:10]
        pick_id = p.get("id", "?")
        odds    = p.get("odds", 0) or 0
        odds_s  = f"+{odds}" if odds > 0 else str(odds)
        print(f"    #{pick_id} [{fecha}] {game:<30}  {sel:<12}  ({odds_s})  ${stake:,.0f} COP")
    print()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_args():
    parser = argparse.ArgumentParser(description="MLB Picks Bot")
    parser.add_argument("--partido",    type=str, help="Filtrar por equipo")
    parser.add_argument("--manana",     action="store_true", help="Ver picks de mañana")
    parser.add_argument("--resolver",   action="store_true", help="Resolver pendientes")
    parser.add_argument("--resultado",  nargs=2, metavar=("ID", "RESULT"),
                        help="Marcar resultado manual: --resultado 7 WIN")
    parser.add_argument("--atribuir",   nargs="+", metavar="ARG",
                        help="Atribuir suerte/analisis: --atribuir ID LUCK_PCT nota")
    parser.add_argument("--pendientes", action="store_true", help="Ver picks pendientes")
    parser.add_argument("--historial",  action="store_true", help="Ver historial + ROI")
    parser.add_argument("--grupo",      action="store_true", help="Ver leaderboard del grupo")
    parser.add_argument("--paper",      action="store_true",
                        help="Modo paper: filtros relajados, stake $0, valida sin apostar")
    return parser.parse_args()


def main():
    args = _parse_args()
    database.setup()

    if args.grupo:
        group_manager.display_leaderboard(sport=_SPORT)
        return

    if args.historial:
        _display_historial()
        return

    if args.pendientes:
        _display_pendientes()
        return

    if args.atribuir:
        if len(args.atribuir) < 3:
            print("  ❌ Uso: --atribuir ID LUCK_PCT 'nota explicando qué pasó'")
            return
        try:
            pick_id  = int(args.atribuir[0])
            luck_pct = int(args.atribuir[1])
            nota     = " ".join(args.atribuir[2:])
        except ValueError:
            print("  ❌ ID y LUCK_PCT deben ser números enteros")
            return
        print(database.set_atribucion(pick_id, luck_pct, nota))
        return

    if args.resultado:
        pick_id_str, result = args.resultado
        result = result.upper()
        if result not in ("WIN", "LOSS", "PUSH"):
            print(f"  Resultado invalido: {result}. Usa WIN, LOSS o PUSH.")
            return
        database.mark_result(int(pick_id_str), result)
        return

    if args.resolver:
        print("\n  Resolviendo picks MLB pendientes...\n")
        _resolver_mlb()
        return

    run_mlb_picks(partido=args.partido, solo_manana=args.manana, paper=args.paper)


if __name__ == "__main__":
    main()
