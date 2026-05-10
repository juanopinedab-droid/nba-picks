"""
feedback.py — Contexto automático post-partido para evaluación del modelo.

Tras la resolución de cada pick, extrae de la ESPN API pública (sin key):
- Titular del partido
- Líderes de PTS, REB y AST
- Noticias recientes de cada equipo (lesiones, lineup changes)

El contexto se guarda en la columna feedback_notes de la DB y se muestra
durante la resolución automática (--resolver).
"""

import time

import config
import database
from utils import http_get

_ESPN_SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
_ESPN_NEWS    = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/news"

# ESPN team IDs (abreviatura NBA → ID interno ESPN)
_ESPN_TEAM_IDS = {
    "ATL": 1,  "BOS": 2,  "BKN": 17, "CHA": 30, "CHI": 4,
    "CLE": 5,  "DAL": 6,  "DEN": 7,  "DET": 8,  "GSW": 9,
    "HOU": 10, "IND": 11, "LAC": 12, "LAL": 13, "MEM": 29,
    "MIA": 14, "MIL": 15, "MIN": 16, "NOP": 3,  "NYK": 18,
    "OKC": 25, "ORL": 19, "PHI": 20, "PHX": 21, "POR": 22,
    "SAC": 23, "SAS": 24, "TOR": 28, "UTA": 26, "WAS": 27,
}


def _fetch_game_leaders(espn_id: str) -> list[str]:
    """Titular del partido + líderes de estadísticas desde el summary ESPN."""
    try:
        r = http_get(_ESPN_SUMMARY, params={"event": espn_id})
        data = r.json()
    except Exception as e:
        print(f"  ⚠️  Feedback leaders error: {e}")
        return []

    lines: list[str] = []

    # Titular del partido (notes del header de la competición)
    for comp in data.get("header", {}).get("competitions", []):
        for note in comp.get("notes", []):
            headline = note.get("headline", "")
            if headline:
                lines.append(headline)
                break
        if lines:
            break

    # Líderes de estadísticas (PTS, REB, AST)
    for cat in data.get("leaders", []):
        short_name   = cat.get("shortDisplayName", cat.get("displayName", ""))
        leaders_list = cat.get("leaders", [])
        if leaders_list:
            top          = leaders_list[0]
            athlete_name = top.get("athlete", {}).get("shortName", "")
            display_val  = top.get("displayValue", "")
            if athlete_name and display_val:
                lines.append(f"{short_name}: {athlete_name} {display_val}")

    return lines


def _fetch_team_news(abbr: str) -> list[str]:
    """Hasta 2 titulares de noticias recientes de un equipo desde ESPN."""
    espn_id = _ESPN_TEAM_IDS.get(abbr)
    if not espn_id:
        return []
    try:
        r = http_get(_ESPN_NEWS, params={"team": espn_id, "limit": 5})
        articles = r.json().get("articles", [])
    except Exception:
        return []

    return [a["headline"] for a in articles[:2] if a.get("headline")]


def get_game_context(espn_id: str, home_team: str, away_team: str, historical: bool = False) -> str:
    """
    Construye el contexto completo del partido.
    historical=True → solo líderes del partido (sin noticias actuales de equipo,
    que no son relevantes para partidos pasados).
    """
    parts: list[str] = []

    parts.extend(_fetch_game_leaders(espn_id))

    if not historical:
        seen      = set(parts)
        home_abbr = config.TEAM_MAP.get(home_team, "")
        away_abbr = config.TEAM_MAP.get(away_team, "")

        for abbr in (home_abbr, away_abbr):
            if not abbr:
                continue
            time.sleep(0.3)
            for headline in _fetch_team_news(abbr):
                if headline not in seen:
                    parts.append(headline)
                    seen.add(headline)

    return " | ".join(parts)


def attach_feedback(espn_id: str, pick_ids: list[int], home_team: str, away_team: str,
                    historical: bool = False):
    """
    Adjunta el contexto ESPN a todos los picks de un mismo partido.
    Un solo fetch por partido → persiste en todos sus picks.
    """
    if not pick_ids:
        return

    context = get_game_context(espn_id, home_team, away_team, historical=historical)
    if not context:
        return

    for pick_id in pick_ids:
        database.save_feedback(pick_id, context)

    preview = context[:90] + ("..." if len(context) > 90 else "")
    print(f"  📰  Contexto: {preview}")


def backfill_feedback_all():
    """
    Aplica feedback a todos los picks resueltos que aún no tienen feedback_notes.
    Agrupa por fecha y partido para minimizar llamadas a ESPN.
    Para picks históricos usa solo líderes del partido (sin noticias actuales de equipo).
    """
    from datetime import date as date_cls
    from resolver import fetch_nba_scores, _match_game

    picks = database.get_resolved_without_feedback()
    if not picks:
        print("  ✅  Todos los picks resueltos ya tienen feedback.")
        return

    print(f"  Aplicando feedback a {len(picks)} pick(s) histórico(s)...\n")

    # Agrupar por fecha
    picks_by_date: dict[str, list] = {}
    for pick in picks:
        picks_by_date.setdefault(pick["date"], []).append(pick)

    total_ok = 0
    total_skip = 0

    for date_str, date_picks in sorted(picks_by_date.items()):
        try:
            game_date = date_cls.fromisoformat(date_str)
        except ValueError:
            continue

        print(f"  📅  {date_str} — {len(date_picks)} pick(s)")

        scores = fetch_nba_scores(game_date)
        if not scores:
            print(f"       ⚠️  Sin datos ESPN para {date_str}")
            total_skip += len(date_picks)
            continue

        # Agrupar picks por partido (espn_id)
        feedback_queue: dict[str, dict] = {}
        for pick in date_picks:
            game_result = _match_game(pick["game"], scores)
            if not game_result:
                print(f"       ⚠️  Partido no encontrado en ESPN: {pick['game']}")
                total_skip += 1
                continue
            eid = game_result["espn_id"]
            if eid not in feedback_queue:
                feedback_queue[eid] = {
                    "pick_ids":  [],
                    "home_team": game_result["home_team"],
                    "away_team": game_result["away_team"],
                }
            feedback_queue[eid]["pick_ids"].append(pick["id"])

        for eid, info in feedback_queue.items():
            time.sleep(0.5)
            attach_feedback(
                eid,
                info["pick_ids"],
                info["home_team"],
                info["away_team"],
                historical=True,
            )
            total_ok += len(info["pick_ids"])

    print(f"\n  ✅  Feedback aplicado: {total_ok} pick(s)  |  Sin datos: {total_skip} pick(s)")
