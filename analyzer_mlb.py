"""
analyzer_mlb.py — Modelo de predicción MLB

Mercados: Totals (Over/Under), Moneyline (ML)
Modelo:   xFIP_blended del abridor + OPS del equipo ofensor + park factor + platoon L/R
          → expected_runs por equipo → suma = mu_total
          → P(Over/Under) via distribución normal (σ=4.20)
          → P(win) via distribución Poisson independiente

Señales cortadas (backtest R²<1%): clima, árbitro, H2H pitcher-equipo,
splits home/away, días de descanso, ERA del bullpen de equipo.
"""

import math
import numpy as np
from typing import Optional

import line_tracker
from collector_mlb import (
    LEAGUE_AVG_RUNS,
    LEAGUE_AVG_OPS,
    LEAGUE_AVG_FIP,
    RUN_TOTAL_SIGMA,
    LEAGUE_HR_PER_FB,
)

# ── Parámetros del modelo ─────────────────────────────────────────────────────
MIN_EDGE        = 0.07    # edge mínimo — subido de 6% a 7%: muestra acumulada, reducir ruido borderline.
                          # Con 5 filtros de confirmación activos el edge % solo es un tercer filtro.
                          # Picks 6-8% = BAJA confianza (solo Hades apuesta mínimo para la muestra).
MIN_IP_STARTER  = 25.0    # innings mínimos para confiar en el FIP (backtest: FIP se estabiliza ~50 IP)
MIN_GS_STARTER  = 3       # arranques mínimos — filtra relevistas/openers con IP alta pero no starters
_IP_FULL_CONFIDENCE = 50.0  # IP para confianza plena en xFIP (sin shrinkage)
                             # Ej: 21 IP → 42% confianza → xFIP encogido 58% hacia liga (4.29)
MAX_PICKS       = 3       # máximo picks OVER/UNDER por día — cap bajado de 5 a 3
                          # Un día con 4+ OVERs = modelo sin discriminar. Top 3 por edge.
_FIP_CAP        = 5.50    # FIP máximo — calibración real: cap anterior (6.50) generaba
                          # proyecciones irreales que inflaban edge artificialmente (47% WR en ≥12%).
                          # Un starter sustentado con FIP > 5.5 prácticamente no existe; bajar a 5.50.
_FIP_FLOOR      = 2.80    # FIP mínimo (evitar datos de muestra pequeña)

# Filtro de línea máxima — calibración real (n=40):
# Líneas 9.0-9.5: 14% WR / líneas 10+: 0% WR. El modelo no tiene edge en partidos de alto punteo.
# Causa probable: xFIP no captura correctamente bullpens en partidos de ofensiva muy alta.
# Reactivar cuando se implementen splits L/R y lineup handedness.
_MAX_TOTAL_LINE = 8.5

# Edge máximo por pick — calibración real:
# Edge 12-15%: 47% WR | Edge 15%+: 33% WR. A mayor edge predicho, peor el resultado.
# Causa: compounding de señales extremas de xFIP + OPS + clima → pseudo-certeza no real.
# Cap conservador mientras se acumula muestra para validar picks de edge alto.
_MAX_PICK_EDGE  = 0.14

# OVER reactivado: filtros simplificados tras corte de ruido (mu > línea+1.0 + pitcher débil)
ALLOW_OVER      = True

# UNDER deshabilitado: 33% WR / -23.6% ROI en 12 picks — modelo sobreestima prob UNDER
# Reactivar solo si backtest muestra mejora sostenida (mínimo 50 picks)
ALLOW_UNDER     = False

# ── Filtros de confirmación OVER (simplificados) ──────────────────────────────
# Filtro 1: margen mínimo de mu sobre la línea
_OVER_MIN_MU_MARGIN  = 1.0
# Filtro 2: al menos un pitcher genuinamente débil (xFIP alto = más carreras)
_OVER_MIN_WEAK_XFIP  = 4.2
# Filtro 3: shrinkage adicional si hay un ace presente (mejor pitcher xFIP ≤ 3.85)
# Ejemplo: SF@CHC — McDonald xFIP=3.81 → mu 9.2×0.90=8.28, margin 0.28 < 1.0 → bloqueado
_ELITE_ACE_XFIP      = 3.85  # xFIP máximo para considerar pitcher como "ace"
_ELITE_ACE_SHRINKAGE = 0.10  # reducción de mu cuando hay un ace en el partido
# Filtro 4: forma reciente mínima (R/G combinado últimos 28d vs línea)
# Patrón CLE@TEX: equipos fríos (combined ~7.0 R/G) pero modelo ve OVER 8.5 → pérdida
# Si R/G reciente combinado < línea × ratio → no hay evidencia real de ofensiva
_OVER_MIN_RECENT_RG_RATIO = 0.87  # ej: línea 8.5 → exige combined ≥ 7.4 R/G reciente

# ── Filtros de confirmación UNDER (simplificados) ─────────────────────────────
# Ambos pitchers élite + mercado espera ofensiva que el modelo no ve
_UNDER_MAX_XFIP      = 3.5   # ambos pitchers con xFIP ≤ 3.5
_UNDER_MIN_LINE      = 8.0   # apostar UNDER solo en líneas altas (≥ 8.0)

# Niveles de confianza — recalibrados con datos reales (n=40 picks):
# Antes: ALTA ≥ 12% → 54% WR real | MEDIA ≥ 10% → 79% WR real (invertido)
# Ahora: ALTA ≥ 10% → picks de 10-14% edge (71% WR) | MEDIA ≥ 8% → picks de 8-10% edge (100% WR)
# Nota: los picks de edge > 14% se rechazan con _MAX_PICK_EDGE para evitar sobreconfianza.
_CONFIDENCE_HIGH = 0.10   # ALTA  — Zeus, Atena y Hades apuestan
_CONFIDENCE_MED  = 0.08   # MEDIA — Atena y Hades apuestan
                           # BAJA  (6-8%) — solo Hades apuesta

# ── Platoon splits (L/R handedness) ─────────────────────────────────────────
# Fundamento empírico (MLB 2024):
#   LHB vs RHP: ~.745 OPS | LHB vs LHP: ~.695 OPS → diferencia ~50 pts
#   RHB vs LHP: ~.760 OPS | RHB vs RHP: ~.710 OPS → diferencia ~50 pts
# Para un lineup con N% de bateadores con ventaja platoon vs el pitcher:
#   adj = (pct_opuesto - 0.50) × _PLATOON_OPS_SCALE × 2
#   Con _PLATOON_OPS_SCALE = 0.025: lineup 100% opuesto → +0.025 OPS adj (25 pts)
#   Lineup 70% opuesto: adj = (0.70 - 0.50) × 0.025 × 2 = +0.010 OPS ≈ +0.15 carr.
_PLATOON_OPS_SCALE = 0.025   # OPS adj máximo por equipo para platoon
_PLATOON_ADJ_MIN   = 0.003   # ignorar ajustes < 3 OPS points (ruido insignificante)

# ── Savant: thresholds para display de xERA y velocidad ──────────────────────
_XERA_DIVERGENCE_THRESHOLD = 0.40  # mínimo diferencia xERA vs xFIP/FIP para mostrar flag
_FB_VELO_ELITE = 97.0              # mph — elite (top ~15% de starters)
_FB_VELO_WEAK  = 91.0              # mph — por debajo del promedio de starters

# ── Moneyline ────────────────────────────────────────────────────────────────
_ML_MIN_EDGE  = 0.03   # edge mínimo para generar un pick ML (bajado: modelo corte ruido)
_ML_MIN_PROB  = 0.60   # probabilidad mínima de victoria — backtest: señal real en 60%+

# ── Run Line ─────────────────────────────────────────────────────────────────
ALLOW_RUNLINE   = True
_RL_MIN_EDGE    = 0.04   # edge mínimo — Run Line requiere mayor convicción que ML
_RL_MIN_PROB    = 0.58   # probabilidad mínima de cubrir el spread
_RL_MAX_PICKS   = 3      # máximo picks RL por día
_ML_MAX_PICKS = 3      # máximo picks ML por día

# ── Parámetros del clima ─────────────────────────────────────────────────────
_TEMP_BASELINE_F  = 72.0    # temperatura neutra MLB (promedio de día de juego ~mayo-agosto)
_TEMP_ADJ_PER_10F = 0.50    # carreras/partido por cada 10°F sobre/bajo el baseline
                             # Fundamento: ball carries ~8% más lejos por cada 10°F, ≈0.5 runs/partido
_WIND_ADJ_PER_MPH = 0.07    # carreras/partido por mph de viento neto a favor de bateadores
                             # Fundamento: estudios Wrigley ~0.7 runs por 10 mph = 0.07/mph
_WEATHER_ADJ_CAP  = 1.8     # cap total del ajuste de clima (en carreras del partido)

# ── Parámetros de días de descanso del pitcher ────────────────────────────────
# Evidencia empírica MLB (Baseball Prospectus / Fangraphs):
#   • 4 días (short rest): rendimiento ~3-5% peor que la línea base → +0.20 xFIP
#   • 5 días (descanso estándar): línea base, sin ajuste
#   • 6 días: ligera regresión por pérdida de ritmo → +0.10 xFIP
#   • 7+ días (extra rest / regreso de IL): regresión mayor → +0.20 xFIP
#   • ≤2 días (abridor de emergencia / bullpen game): poco frecuente → +0.40 xFIP
# El ajuste se aplica al xFIP_for_model ANTES de pasar a _effective_fip.
# Cap: no puede subir el xFIP_for_model por encima de _FIP_CAP.
_REST_ADJ_SHORT    = 0.20   # ≤4 días (short rest)
_REST_ADJ_OPTIMAL  = 0.00   # 5 días (descanso estándar)
_REST_ADJ_EXTENDED = 0.10   # 6 días (perdida de ritmo)
_REST_ADJ_LONG     = 0.20   # 7+ días (extra rest / regreso IL)
_REST_ADJ_EMERGENCY= 0.40   # ≤2 días (bullpen/emergencia)


def _days_rest_xfip_adj(days_rest: int | None) -> tuple[float, str]:
    """
    Penalización al xFIP del pitcher según días de descanso desde su último inicio.

    Retorna (adj_xfip, descripción_para_display).
    adj_xfip > 0 → el pitcher se espera que rinda peor que su xFIP base.
    adj_xfip = 0 → descanso estándar de 5 días, sin ajuste.
    """
    if days_rest is None:
        return 0.0, ""
    if days_rest <= 2:
        return _REST_ADJ_EMERGENCY, f"emergencia ({days_rest}d descanso, +{_REST_ADJ_EMERGENCY:.2f} xFIP)"
    if days_rest <= 4:
        return _REST_ADJ_SHORT,    f"descanso corto ({days_rest}d, +{_REST_ADJ_SHORT:.2f} xFIP)"
    if days_rest == 5:
        return _REST_ADJ_OPTIMAL,  ""   # sin penalización, sin display
    if days_rest == 6:
        return _REST_ADJ_EXTENDED, f"descanso extendido ({days_rest}d, +{_REST_ADJ_EXTENDED:.2f} xFIP)"
    # 7+ días
    return _REST_ADJ_LONG, f"extra rest ({days_rest}d, +{_REST_ADJ_LONG:.2f} xFIP)"


def _platoon_ops_adj(pitcher_hand: str | None, lineup_pct_l: float | None) -> tuple[float, str]:
    """
    Ajuste de OPS por platoon (handedness del pitcher vs composición zurda/diestra del lineup).

    pitcher_hand:  "L" o "R" (mano del lanzador)
    lineup_pct_l:  fracción del lineup que batea zurdo (0.0–1.0). None = sin datos.

    Retorna (delta_ops, descripción).
    delta_ops > 0 → lineup tiene ventaja platoon (más bateadores opuestos al pitcher).
    delta_ops < 0 → lineup tiene desventaja platoon (más bateadores del mismo lado).
    0.0 si no hay datos suficientes.
    """
    if not pitcher_hand or lineup_pct_l is None:
        return 0.0, ""

    # Fracción del lineup con ventaja platoon (opuesto al pitcher)
    if pitcher_hand == "R":
        pct_opposite = lineup_pct_l          # LHB vs RHP tienen ventaja
    else:
        pct_opposite = 1.0 - lineup_pct_l   # RHB vs LHP tienen ventaja

    adj = (pct_opposite - 0.50) * _PLATOON_OPS_SCALE * 2
    adj = round(max(-_PLATOON_OPS_SCALE, min(_PLATOON_OPS_SCALE, adj)), 4)

    if abs(adj) < _PLATOON_ADJ_MIN:
        return 0.0, ""

    hand_str = "zurdo" if pitcher_hand == "L" else "derecho"
    pct_opp  = round(pct_opposite * 100)
    adv_str  = "ventaja" if adj > 0 else "desventaja"
    desc = f"vs {hand_str}: {pct_opp}% opuesto → {adv_str} ({adj:+.3f} OPS)"
    return adj, desc


def _line_movement_str(
    direction: str,
    signal: str,
    movement: float,
    opening_line: float | None,
    current_line: float | None,
) -> str:
    """
    Genera el string de display para el movimiento de línea en reasons.

    - Si la señal confirma nuestra dirección → indicador positivo ✅
    - Si la señal contradice nuestra dirección → advertencia ⚠️
    - Si es neutral → no llamar esta función (no hay nada que mostrar)

    Ejemplo:
        "Mov. línea: +0.5 (8.5→9.0) — steam OVER ↑ [confirma pick]"
        "Mov. línea: -0.5 (9.5→9.0) — steam UNDER ↓ ⚠️ contradice el OVER"
    """
    if opening_line is None or current_line is None:
        return ""

    sign   = "+" if movement > 0 else ""
    arrow  = "↑" if movement > 0 else "↓"

    if signal == "strong_over":
        label = "sharp OVER fuerte"
    elif signal == "steam_over":
        label = "acción OVER"
    elif signal == "strong_under":
        label = "sharp UNDER fuerte"
    elif signal == "steam_under":
        label = "acción UNDER"
    else:
        return ""

    # Determinar si la señal confirma o contradice nuestro pick
    is_over_signal  = signal in ("steam_over",  "strong_over")
    is_under_signal = signal in ("steam_under", "strong_under")

    if direction == "OVER"  and is_over_signal:
        verdict = "✅ confirma pick"
    elif direction == "OVER"  and is_under_signal:
        verdict = "⚠️ contradice el OVER"
    elif direction == "UNDER" and is_under_signal:
        verdict = "✅ confirma pick"
    elif direction == "UNDER" and is_over_signal:
        verdict = "⚠️ contradice el UNDER"
    else:
        verdict = ""

    return (f"Mov. línea: {sign}{movement:.1f} ({opening_line:.1f}→{current_line:.1f})"
            f" — {label} {arrow}"
            + (f" [{verdict}]" if verdict else ""))


def _weather_run_adj(weather: dict | None) -> tuple[float, str]:
    """
    Ajuste de carreras esperadas basado en clima del estadio.

    Temperatura: la pelota viaja más lejos con calor seco.
        adj = (temp_f - 72) / 10 × 0.50 runs
        Ejemplo: 55°F → -0.85 runs; 90°F → +0.90 runs

    Viento: se descompone en componente home-to-CF (bateadores) vs CF-to-home (pitchers).
        wind_factor = cos(ángulo entre dirección del viento y HP→CF)
        adj = wind_speed_mph × wind_factor × 0.07 runs/mph
        Ejemplo: Wrigley con 15 mph viento del W → +1.05 runs

    Returns: (adj_runs, descripción_para_display)
    """
    if not weather:
        return 0.0, ""

    temp_f         = weather.get("temp_f", _TEMP_BASELINE_F)
    wind_speed_mph = weather.get("wind_speed_mph", 0.0)
    wind_deg       = weather.get("wind_deg", 0)
    cf_bearing     = weather.get("cf_bearing", 0)
    description    = weather.get("description", "")

    # — Ajuste por temperatura —
    temp_adj = (temp_f - _TEMP_BASELINE_F) / 10.0 * _TEMP_ADJ_PER_10F

    # — Ajuste por viento —
    # wind_deg: dirección DESDE la que sopla (meteorológica, 0=N, 90=E, 180=S, 270=W)
    # wind_toward: dirección HACIA la que sopla
    wind_toward = (wind_deg + 180) % 360
    angle_diff  = abs(wind_toward - cf_bearing)
    if angle_diff > 180:
        angle_diff = 360 - angle_diff
    # cos(0°) = 1.0 (viento sale hacia CF), cos(180°) = -1.0 (viento entra hacia home)
    wind_component = math.cos(math.radians(angle_diff))
    wind_adj       = wind_speed_mph * wind_component * _WIND_ADJ_PER_MPH

    total_adj = max(-_WEATHER_ADJ_CAP, min(_WEATHER_ADJ_CAP, temp_adj + wind_adj))

    # — Descripción para el display —
    parts = []
    if abs(temp_adj) >= 0.10:
        direction = "caluroso" if temp_adj > 0 else "frío"
        parts.append(f"{temp_f:.0f}°F {direction} ({temp_adj:+.2f} carr.)")
    if abs(wind_adj) >= 0.10:
        dir_str = "a favor" if wind_adj > 0 else "en contra"
        parts.append(f"viento {wind_speed_mph:.0f} mph {dir_str} ({wind_adj:+.2f} carr.)")
    if parts:
        parts.append(description)
    desc = " | ".join(parts) if parts else ""

    return round(total_adj, 2), desc


# ── Parámetros del bullpen ─────────────────────────────────────────────────────
# ERA promedio de bullpen de liga (estimado; el equipo-total FIP sirve de proxy).
# Fórmula: effective_FIP = starter_FIP × frac_starter + bullpen_ERA × frac_bullpen
# donde frac_starter = ip_per_start / 9 (capped a 7 IP = 0.78 del partido).
LEAGUE_AVG_BULLPEN_ERA = 4.10   # ERA típica del bullpen MLB; actualizar cada temporada

# ── Promedios de liga: métricas de contacto Savant (2025 temporada) ──────────
# Fuente: Baseball Savant statcast leaderboard, pitchers con ≥10 IP
# Usados para normalizar el ajuste de contacto en _composite_fip()
LEAGUE_AVG_BARREL_PCT  = 8.9    # barrel% permitido — promedio real 2025 (795 pitchers, min 10 IP)
LEAGUE_AVG_HARD_HIT    = 41.1   # hard hit% permitido — promedio real 2025
LEAGUE_AVG_AVG_EV      = 89.5   # exit velocity promedio permitida (mph) — promedio real 2025
LEAGUE_AVG_XERA        = 4.30   # xERA de liga (expected ERA via expected values Savant)

# ── Promedios de liga: xwOBA ofensivo por equipo (2025 temporada) ─────────────
# Fuente: Baseball Savant expected_statistics, type=batter-team, 30 equipos
# xwOBA = expected wOBA usando velocidad y ángulo de contacto (elimina suerte BABIP)
# Correlación con producción futura: ~0.70 vs OPS ~0.65 — señal complementaria.
# Liga avg 2025: 0.316 (rango 0.292-0.340 entre las 30 franquicias)
LEAGUE_AVG_XWOBA_BATTING = 0.316  # xwOBA promedio de los 30 equipos, 2025

# ── Props: Strikeouts del pitcher — NICHO PRINCIPAL del bot (jun-2026) ───────
# Tesis del nicho: la casa publica líneas de Ks para 15+ pitchers/día con
# modelos genéricos que ajustan tarde tres señales que nosotros sí modelamos:
#   1. K-rate del LINEUP CONFIRMADO (no del equipo promedio)
#   2. Umpire asignado (zona amplia vs estricta ≈ ±0.5-1 K/salida)
#   3. Longitud esperada de la salida (IP/start reciente, no solo de temporada)
# REGLA DE ORO: nunca apostar sin lineup confirmado Y umpire asignado — el
# descuido de la casa existe porque ella tampoco procesó esa información;
# apostar antes de tenerla es renunciar a la única ventaja del nicho.
LEAGUE_AVG_K_RATE_BATTING = 0.226  # K/AB promedio de equipos 2026 (~22.6%)
_MIN_EDGE_K_PROP          = 0.04   # rango rentable del nicho: 4%+
# FILOSOFÍA DEL NICHO (jun-2026, "que el Ferrari corra"): con la regla de oro
# cumplida (lineup rival + umpire confirmados) y las 8 señales activas, el edge
# es GANADO, no sospechoso. La escepticismo "edge=sospecha" es del modelo
# GENÉRICO (sin info extra); aquí tenemos info que el mercado no incorporó aún.
# Por eso: NO se aplica Platt (entrenado en datos pre-niche que suprimen edge
# ganado), el cap solo rechaza errores de datos groseros, y SOSPECHA solo
# aparece en edges absurdos (≥18% = línea stale / lesión no anunciada).
_MIN_PROB_K_OVER          = 0.52   # sobre breakeven de -110 (52.4% ≈ par)
_MIN_PROB_K_UNDER         = 0.54   # UNDER +2pp (históricamente más difícil)
_MAX_EDGE_K_PROP          = 0.12   # >12% anclado = mercado en fuerte desacuerdo → rechazar
_K_EDGE_SUSPECT           = 0.10   # 10-12% = SOSPECHA; <10% = edge creíble
# Ancla al mercado (opción 2, 13-jun): encoge la prob del modelo hacia la justa
# del mercado mientras no tengamos prueba de que el edge crudo es real. Fuerte
# al arranque (0.60); se afloja con datos de paper_validate/CLV, sube si fallan.
_K_MARKET_ANCHOR          = 0.60
_UMP_K_ADJ_PER_RUN        = 1.0    # Ks por carrera de ump_run_adj (zona amplia = -runs = +Ks)
_UMP_K_ADJ_CAP            = 0.5    # cap del ajuste de umpire en Ks

# Whiff% como corrector del K/9: el whiff estabiliza más rápido que el K/9 y
# detecta breakouts/declives de stuff antes. Si whiff alto pero K/9 modesto →
# K/9 esperado al alza (y viceversa). Peso 0.3, cap ±8%.
_LEAGUE_AVG_WHIFF   = 24.6   # whiff% promedio de starters MLB 2025
_WHIFF_CORR_WEIGHT  = 0.30
_WHIFF_CORR_CAP     = 0.08

# Splits K% L/R del pitcher × composición zurda del lineup confirmado.
# Mínimo 40 BF por lado para confiar en el split; factor cap ±8%.
_KSPLIT_MIN_BF      = 40
_KSPLIT_FACTOR_CAP  = 0.08

# Temperatura a la hora del juego (forecast): aire frío = más denso = más
# movimiento de los pitches = más Ks. Efecto chico y documentado: ~1% de
# lambda por cada 10°F bajo/sobre 72°F, cap ±3%. (El viento NO afecta Ks —
# mueve la pelota bateada, no la pitcheada; su lugar son los totales.)
_TEMP_K_PER_10F     = 0.01
_TEMP_K_CAP         = 0.03

# Correa corta: el Poisson asume la IP como exposición FIJA, pero la IP es
# aleatoria con cola izquierda gorda en abridores de poca confianza del
# manager (IP/start bajo). P(salida ≤2 IP) ≈ 10-15% para ese perfil, y en
# esos mundos el OVER muere automáticamente — el modelo lo subestima.
# OVERs requieren IP/start ≥ 5.0; los UNDER no (se benefician del blowup).
# Caso Imai 12-jun: λ=5.38 sobre 4.7 IP/start, sacado a los 38 pitches en el 1ro.
_K_OVER_MIN_IP_START = 5.0

# Gate de lluvia (estadios abiertos): pop = prob. de precipitación a la hora
# del juego según el forecast 3h. Un rain delay saca al abridor temprano
# (mata K-props OVER aunque el juego se complete) y una posposición anula
# la apuesta. Caso Braves@White Sox 11-jun: pospuesto por tormenta que el
# forecast tenía y el modelo no miró.
_RAIN_POP_GATE = 0.50


def _rain_blocked(game: dict, label: str) -> bool:
    """True si el riesgo de lluvia a la hora del juego bloquea props/F5."""
    pop = game.get("rain_pop")
    if pop is not None and pop >= _RAIN_POP_GATE:
        print(f"  🌧️ {label}: prob. de lluvia {pop:.0%} a la hora del juego "
              f"(≥{_RAIN_POP_GATE:.0%}) — riesgo de delay/posposición, sin picks")
        return True
    return False

# Dead-zone de cuotas para todos los props MLB.
# Backtest junio 2026 (n=15 picks en este rango): WR 40% vs breakeven 57% → -35.6% ROI.
# Rango ≤-150: WR 64-67%, ROI +0.2 a +14.1% — calibración aceptable.
# Se saltan picks con cuota entre -149 y -110 (inclusive) para K-props y TB-props.
_PROP_DEAD_ODDS_LO = -149   # límite superior del dead zone (American odds, negativo = favorito)
_PROP_DEAD_ODDS_HI = -110   # límite inferior del dead zone


# ── Distribución normal (sin scipy) ──────────────────────────────────────────

def _prop_confidence(edge: float, *, suspect: float, alta: float, media: float) -> str:
    """
    Etiqueta de confianza DESAMBIGUADA para props (jun-2026).

    El "BAJA" antiguo era ambiguo: significaba cosas opuestas en cada extremo.
    Ahora son etiquetas distintas:
      SOSPECHA — edge ≥ suspect: discrepamos mucho del mercado = winner's curse
                 (probable error del modelo, no descuido de la casa)
      ALTA     — edge en la banda sólida del mercado
      MEDIA    — edge moderado
      FLACA    — edge apenas sobre el mínimo: el mercado casi coincide → seguro
                 pero de valor delgado (el juice se come el EV). Distinto de
                 SOSPECHA: no es riesgo de error, es valor marginal.
    """
    if edge >= suspect:
        return "SOSPECHA"
    if edge >= alta:
        return "ALTA"
    if edge >= media:
        return "MEDIA"
    return "FLACA"


def _erf(x: float) -> float:
    """Approximación de la función error (error < 1.5e-7)."""
    a1, a2, a3, a4, a5 = 0.254829592, -0.284496736, 1.421413741, -1.453152027, 1.061405429
    p = 0.3275911
    sign = 1 if x >= 0 else -1
    x = abs(x)
    t = 1.0 / (1.0 + p * x)
    y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * math.exp(-x * x)
    return sign * y


def _norm_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    """P(X ≤ x) para una distribución normal."""
    return 0.5 * (1.0 + _erf((x - mu) / (sigma * math.sqrt(2))))


def _poisson_cdf(k: int, lam: float) -> float:
    """P(X ≤ k) para X ~ Poisson(lam). Pure Python, sin scipy."""
    if lam <= 0:
        return 1.0 if k >= 0 else 0.0
    total = 0.0
    term  = math.exp(-lam)
    for i in range(int(k) + 1):
        total += term
        term  *= lam / (i + 1)
    return min(total, 1.0)


_MC_RNG = np.random.default_rng(seed=42)

def _mc_k_prob(
    k9_blended: float,
    ip_mean: float,
    opp_factor: float,
    ump_k_adj: float,
    line: float,
    ip_std: float = 1.5,
    k9_std_frac: float = 0.15,
    n_sims: int = 10_000,
) -> tuple[float, float, float, float]:
    """
    Monte Carlo para props de strikeouts. Simula incertidumbre en IP y K-rate.

    Retorna: (p_over, p_under, lam_mean, lam_std)

    Las dos fuentes principales de varianza:
    - IP real del pitcher: Normal(ip_mean, ip_std) recortado [1, 7]
    - K-rate subyacente: Normal(k9/9, k9/9 * k9_std_frac), recortado [0, inf]

    El resultado es una distribución compuesta más ancha que Poisson puro,
    capturando el riesgo de salida corta (OVER muere) y racha caliente (UNDER muere).
    """
    k_per_ip_mean = k9_blended / 9.0

    ip_sims    = _MC_RNG.normal(ip_mean, ip_std, n_sims).clip(1.0, 7.0)
    krate_sims = _MC_RNG.normal(k_per_ip_mean, k_per_ip_mean * k9_std_frac, n_sims).clip(0.0, None)

    lam_sims = krate_sims * ip_sims * opp_factor + ump_k_adj
    lam_sims = lam_sims.clip(0.1, None)

    k_sims = _MC_RNG.poisson(lam_sims)

    floor_line = int(line)
    is_half    = (line % 1 != 0)
    if is_half:
        p_over  = float(np.mean(k_sims >  floor_line))
        p_under = float(np.mean(k_sims <= floor_line))
    else:
        p_over  = float(np.mean(k_sims >  floor_line))
        p_under = float(np.mean(k_sims <  floor_line))

    return p_over, p_under, float(np.mean(lam_sims)), float(np.std(lam_sims))


# ── Score compuesto ofensivo del equipo ──────────────────────────────────────

def _composite_ops(
    ops: float,
    xwoba: float | None = None,
) -> tuple[float, bool]:
    """
    OPS compuesto del equipo — incorpora xwOBA de Savant como señal correctora.

    Estrategia:
      • Si xwOBA disponible → blend OPS(65%) + xwOBA-ajustado(35%).
        xwOBA usa velocidad y ángulo de contacto (elimina suerte BABIP).
        El ajuste es RELATIVO al promedio de liga, preservando la escala del equipo:
            xwoba_factor = xwoba / LEAGUE_AVG_XWOBA_BATTING
            xwoba_adj_ops = ops × xwoba_factor    ← si xwoba = avg → no cambia
            composite = 0.65 × ops + 0.35 × xwoba_adj_ops
        Propiedad clave: si xwOBA = liga avg → composite = ops (sin efecto).
        Si xwOBA > liga avg → composite sube (equipo "unlucky", contacto mejor que resultados).
        Si xwOBA < liga avg → composite baja (equipo "lucky", resultados mejor que contacto).
      • Si xwOBA no disponible → OPS puro (sin cambios).

    Retorna (composite_ops, has_xwoba) — flag para display.

    Cap: [0.550, 0.980] — mismo rango que el modelo usa para OPS.

    Ejemplo real (2025):
      NYM: OPS=0.742, xwOBA=0.339 → factor=1.073 → adj=0.797 → composite=0.762 (+0.020)
      ATH: OPS=0.724, xwOBA=0.309 → factor=0.978 → adj=0.708 → composite=0.718 (−0.006)
      COL: OPS=0.790, xwOBA=0.294 → factor=0.930 → adj=0.735 → composite=0.771 (−0.019, ajuste Coors)
    """
    if xwoba is None:
        return ops, False
    xwoba_factor  = xwoba / LEAGUE_AVG_XWOBA_BATTING  # 1.0 si es promedio de liga
    xwoba_adj_ops = ops * xwoba_factor                 # OPS "justo" según contacto
    composite = 0.65 * ops + 0.35 * xwoba_adj_ops
    return round(max(0.550, min(0.980, composite)), 3), True


# ── Score compuesto del pitcher ───────────────────────────────────────────────

def _composite_fip(
    xfip: float,
    xera: float | None = None,
    brl_pct: float | None = None,
    hard_hit_pct: float | None = None,
) -> float:
    """
    Score compuesto de calidad del pitcher — reemplaza xFIP puro como input al modelo.

    Estrategia:
      • Si xERA disponible → blend xFIP(55%) + xERA(45%).
        xERA ya incorpora calidad de contacto vía expected values, así que NO se añaden
        barrel%/hard_hit% sobre ella (evitar doble conteo).
      • Si xERA NO disponible pero hay métricas de contacto → ajustar xFIP directamente:
          barrel% cada 1% sobre avg → +0.12 (más carreras esperadas)
          hard_hit% cada 1% sobre avg → +0.05
          Ajuste total capado a ±0.60 para evitar dominancia por outliers.
      • Si nada disponible → xFIP puro.

    El resultado se clampea a [_FIP_FLOOR, _FIP_CAP] — mismos límites que el modelo usa.

    Estabilidad year-to-year (research):
      xFIP: ~0.52 | xERA: ~0.50 | barrel%: ~0.45 | whiff%: ~0.58
      Blend xFIP+xERA: ~0.54 — pequeña mejora, sin ruido adicional.
    """
    if xera is not None:
        # xERA ya incorpora contacto → blend limpio, sin ajuste adicional
        score = xfip * 0.55 + xera * 0.45
    else:
        score = xfip
        contact_adj = 0.0
        if brl_pct is not None:
            contact_adj += (brl_pct - LEAGUE_AVG_BARREL_PCT) * 0.12
        if hard_hit_pct is not None:
            contact_adj += (hard_hit_pct - LEAGUE_AVG_HARD_HIT) * 0.05
        # Cap: ±0.60 (≈ diferencia entre pitcher promedio y muy bueno/malo)
        score += max(-0.60, min(0.60, contact_adj))

    return round(max(_FIP_FLOOR, min(_FIP_CAP, score)), 2)


# ── Bullpen adjustment ────────────────────────────────────────────────────────

def _effective_fip(
    starter_fip: float,
    ip_per_start: float,
) -> float:
    """
    FIP efectivo para un partido que pondera abridor + bullpen de liga.

    Modelo simplificado: siempre usa LEAGUE_AVG_BULLPEN_ERA para el bullpen.
    Elimina la dependencia de team_era (ERA de equipo era ruido — backtest R²<2%).

    frac_starter = ip_per_start / 9  (cappado a 7 IP = 0.78 del partido)
    frac_bullpen = 1 - frac_starter
    effective_FIP = starter_FIP × frac_starter + LEAGUE_AVG_BULLPEN_ERA × frac_bullpen
    """
    # Si ip_per_start > 9 → recibimos IP total en vez de IP/salida → usar promedio de liga
    safe_ip_per_start = ip_per_start if ip_per_start <= 9.0 else 5.5
    # Cappar la fracción del abridor a máx 7 IP (ningún abridor completa 9 en la era moderna)
    frac_starter = min(safe_ip_per_start / 9.0, 7.0 / 9.0)
    frac_bullpen = 1.0 - frac_starter
    return starter_fip * frac_starter + LEAGUE_AVG_BULLPEN_ERA * frac_bullpen


# ── Modelo de carreras ────────────────────────────────────────────────────────

# Shrinkage hacia el promedio de liga (regresión a la media).
# Backtest 2025: el modelo sobreestima partidos con mu>11 en +1.6 a +3.0 carreras.
# Causa: la multiplicación de factores (FIP-bajo × OPS-alta × park-factor) se compone
# de forma no lineal produciendo predicciones extremas que no se reflejan en la realidad.
# Un shrinkage del 25% reduce ese error sin afectar predicciones cerca del promedio.
_SHRINKAGE = 0.75   # cuánto del desvío respecto al promedio retenemos


def expected_runs_team(
    team_ops: float,
    pitcher_fip: float,
    park_factor: float,
    is_home: bool,
) -> float:
    """
    Carreras esperadas para un equipo ofensor en un partido.

    Fórmula:
        offense_mult  = team_ops / LEAGUE_AVG_OPS
        pitching_mult = pitcher_fip / LEAGUE_AVG_FIP   (FIP alto = más carreras)
        home_adj      = 1.04 si local, 0.97 si visitante
        raw           = LEAGUE_AVG_RUNS × offense × pitching × park × home_adj
        shrunk        = LEAGUE_AVG_RUNS + (raw - LEAGUE_AVG_RUNS) × _SHRINKAGE

    El shrinkage del 25% corrige la sobre-estimación en partidos extremos
    evidenciada en el backtest 2025 (ver backtest_mlb.py).
    """
    fip_capped    = max(_FIP_FLOOR, min(_FIP_CAP, pitcher_fip))
    offense_mult  = team_ops / LEAGUE_AVG_OPS
    pitching_mult = fip_capped / LEAGUE_AVG_FIP
    home_adj      = 1.04 if is_home else 0.97

    raw = LEAGUE_AVG_RUNS * offense_mult * pitching_mult * park_factor * home_adj
    # Shrink hacia el promedio de liga per-equipo
    return LEAGUE_AVG_RUNS + (raw - LEAGUE_AVG_RUNS) * _SHRINKAGE


def p_over_under(
    mu_total: float,
    line: float,
    sigma: float = RUN_TOTAL_SIGMA,
) -> tuple[float, float]:
    """
    P(Over) y P(Under) para el total del partido.

    Usa distribución normal con continuity correction de ±0.5
    (líneas de totales van a .5, así que la corrección es formal).

    Returns: (p_over, p_under)
    """
    p_over  = 1.0 - _norm_cdf(line, mu_total, sigma)
    p_under = _norm_cdf(line, mu_total, sigma)
    return p_over, p_under


# ── Conversión de cuotas ──────────────────────────────────────────────────────

def american_to_prob(odds: int) -> float:
    """Convierte cuota americana a probabilidad implícita (con vig incluido)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    else:
        return abs(odds) / (abs(odds) + 100.0)


def remove_vig_two_way(impl_over: float, impl_under: float) -> tuple[float, float]:
    """Elimina el vig usando power devigging (corrige sesgo favorito-longshot)."""
    if impl_over <= 0 or impl_under <= 0:
        return 0.5, 0.5
    from odds_utils import power_devig
    return power_devig(impl_over, impl_under)


# ── Análisis de un partido ────────────────────────────────────────────────────

def win_prob_poisson(lambda_home: float, lambda_away: float, max_runs: int = 30) -> tuple[float, float]:
    """
    P(home wins) y P(away wins) usando distribución Poisson independiente.
    Los empates (extrainnings) se reparten 50/50.
    """
    def _pmf(lam: float, k: int) -> float:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    home_pmf = [_pmf(lambda_home, k) for k in range(max_runs + 1)]
    away_pmf = [_pmf(lambda_away, k) for k in range(max_runs + 1)]

    p_home = 0.0
    p_away = 0.0
    p_tie  = 0.0
    for h in range(max_runs + 1):
        for a in range(max_runs + 1):
            p = home_pmf[h] * away_pmf[a]
            if h > a:
                p_home += p
            elif a > h:
                p_away += p
            else:
                p_tie += p

    p_home += 0.5 * p_tie
    p_away += 0.5 * p_tie
    return round(p_home, 4), round(p_away, 4)


def win_prob_runline(
    lambda_home: float,
    lambda_away: float,
    home_rl_point: float = -1.5,
    max_runs: int = 30,
) -> tuple[float, float]:
    """
    P(home covers their run line) y P(away covers) via Poisson independiente.

    home_rl_point = -1.5 → home favorito: debe ganar por 2+ para cubrir
    home_rl_point = +1.5 → home underdog: cubre si gana o pierde por ≤1

    Retorna: (p_home_cover, p_away_cover) donde p_home + p_away = 1.0
    """
    def _pmf(lam: float, k: int) -> float:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)

    home_pmf = [_pmf(lambda_home, k) for k in range(max_runs + 1)]
    away_pmf = [_pmf(lambda_away, k) for k in range(max_runs + 1)]

    p_home_cover = 0.0
    for h in range(max_runs + 1):
        for a in range(max_runs + 1):
            prob = home_pmf[h] * away_pmf[a]
            if home_rl_point < 0:
                # home -1.5: local necesita ganar por 2+ (h - a >= 2)
                if h - a >= 2:
                    p_home_cover += prob
            else:
                # home +1.5: local cubre si gana o pierde por 1 (a - h <= 1)
                if a - h <= 1:
                    p_home_cover += prob

    return round(p_home_cover, 4), round(1.0 - p_home_cover, 4)


def analyze_game(game: dict, _agent_cfg: dict | None = None) -> list[dict]:
    """
    Analiza un partido MLB y genera picks de Totals (Over/Under).

    Args:
        game: dict del collector.
        _agent_cfg: config opcional del agente (group_manager.AGENT_CONFIGS_MLB).
                    Si es None usa los defaults del módulo (comportamiento estándar).

    Returns:
        Lista de picks (0, 1 o 2 picks por partido según edge y dirección).
    """
    # Leer umbrales del agente (con fallback a defaults del módulo)
    _cfg          = _agent_cfg or {}
    _min_edge     = _cfg.get("min_edge",         MIN_EDGE)
    _min_edge_un  = _cfg.get("min_edge_under",   0.08)   # calibración: UNDERs 33% WR vs 63% pred → umbral más alto
    _min_prob_ov  = _cfg.get("min_prob_over",    0.55)
    _min_prob_un  = _cfg.get("min_prob_under",   0.60)   # calibración: subido de 0.58 (UNDERs sobreestimados -30%)
    _min_ip_agent = _cfg.get("min_ip",           10.0)
    picks = []

    # ── Bloqueo IL: pitcher probable en la IL → no podemos modelar el partido ───
    _home_on_il = game.get("home_pitcher_on_il", False)
    _away_on_il = game.get("away_pitcher_on_il", False)
    if _home_on_il or _away_on_il:
        _pitcher_raw = game.get("home_pitcher") if _home_on_il else game.get("away_pitcher")
        _il_name = _pitcher_raw.get("name", "?") if isinstance(_pitcher_raw, dict) else str(_pitcher_raw)
        _away_t  = game.get("away_team", "?")
        _home_t  = game.get("home_team", "?")
        print(f"  ⛔ IL: {_il_name} no está en el active roster — {_away_t}@{_home_t} excluido")
        return []

    # ── Validar datos mínimos ─────────────────────────────────────────────────
    home_p = game.get("home_pitcher") or {}
    away_p = game.get("away_pitcher") or {}

    # Preferir xFIP sobre FIP: más estable en muestras pequeñas (normaliza HR/FB al promedio de liga)
    # Fallback a FIP si xFIP no está disponible (pitchers con muy pocos airOuts registrados)
    home_xfip_blended = home_p.get("xfip_blended") if home_p else None
    away_xfip_blended = away_p.get("xfip_blended") if away_p else None
    home_xfip_season  = home_p.get("xfip")         if home_p else None
    away_xfip_season  = away_p.get("xfip")         if away_p else None
    home_xfip_recent  = home_p.get("xfip_recent")  if home_p else None
    away_xfip_recent  = away_p.get("xfip_recent")  if away_p else None

    # FIP crudo (para display/comparación)
    home_fip_season = home_p.get("fip") if home_p else None
    away_fip_season = away_p.get("fip") if away_p else None
    home_fip_recent = home_p.get("fip_recent") if home_p else None
    away_fip_recent = away_p.get("fip_recent") if away_p else None

    # El modelo usa xFIP_blended cuando está disponible, FIP_blended como fallback
    home_fip        = home_xfip_blended or (home_p.get("fip_blended") or home_p.get("fip")) if home_p else None
    away_fip        = away_xfip_blended or (away_p.get("fip_blended") or away_p.get("fip")) if away_p else None
    home_uses_xfip  = home_xfip_blended is not None
    away_uses_xfip  = away_xfip_blended is not None

    # ── Shrinkage bayesiana por muestra pequeña ──────────────────────────────
    # Con < 50 IP el xFIP es ruidoso. Encogemos hacia la media de liga proporcional
    # al ratio IP/50. Melton 21 IP: conf=0.42 → xFIP 5.07→4.62 (menos castigado).
    # El valor pre-shrinkage se guarda para el display informativo.
    home_fip_preshrink = home_fip
    away_fip_preshrink = away_fip
    if home_fip and home_ip < _IP_FULL_CONFIDENCE:
        _hconf  = home_ip / _IP_FULL_CONFIDENCE
        home_fip = round(_hconf * home_fip + (1.0 - _hconf) * LEAGUE_AVG_FIP, 2)
    if away_fip and away_ip < _IP_FULL_CONFIDENCE:
        _aconf  = away_ip / _IP_FULL_CONFIDENCE
        away_fip = round(_aconf * away_fip + (1.0 - _aconf) * LEAGUE_AVG_FIP, 2)

    # ── Ajuste por días de descanso ─────────────────────────────────────────
    # Función ya definida (_days_rest_xfip_adj); ahora se aplica al modelo.
    home_days_rest = game.get("home_pitcher_days_rest")
    away_days_rest = game.get("away_pitcher_days_rest")
    home_rest_adj, home_rest_desc = _days_rest_xfip_adj(home_days_rest)
    away_rest_adj, away_rest_desc = _days_rest_xfip_adj(away_days_rest)
    if home_fip and home_rest_adj:
        home_fip = min(_FIP_CAP, round(home_fip + home_rest_adj, 2))
    if away_fip and away_rest_adj:
        away_fip = min(_FIP_CAP, round(away_fip + away_rest_adj, 2))

    home_has_fip_recent = home_fip_recent is not None
    away_has_fip_recent = away_fip_recent is not None
    # Splits eliminados del modelo: backtest muestra que home/away xFIP splits
    # añaden ruido (muestra pequeña, alta varianza) sin mejorar predicción.
    # Usar xFIP_blended general directamente.

    # K-BB/9: calidad de arsenal independiente de HRs (ya incluido en xFIP via K y BB)
    # Usado como indicador de confiabilidad — alto K-BB/9 → xFIP más predecible
    home_kbb9 = home_p.get("kbb9") if home_p else None
    away_kbb9 = away_p.get("kbb9") if away_p else None
    home_kbb9_recent = home_p.get("kbb9_recent") if home_p else None
    away_kbb9_recent = away_p.get("kbb9_recent") if away_p else None
    home_hr_fb = home_p.get("hr_fb_pct") if home_p else None  # HR/FB real (vs liga 10.5%)
    away_hr_fb = away_p.get("hr_fb_pct") if away_p else None
    # Savant: xERA y fastball velocity
    home_xera        = home_p.get("xera")         if home_p else None
    away_xera        = away_p.get("xera")         if away_p else None
    home_fb_velo     = home_p.get("fastball_velo") if home_p else None
    away_fb_velo     = away_p.get("fastball_velo") if away_p else None
    home_fb_type     = home_p.get("fastball_type", "FF") if home_p else "FF"
    away_fb_type     = away_p.get("fastball_type", "FF") if away_p else "FF"
    # Savant: calidad de contacto permitida (barrel%, hard_hit%)
    home_brl_pct      = home_p.get("brl_pct_allowed")      if home_p else None
    away_brl_pct      = away_p.get("brl_pct_allowed")      if away_p else None
    home_hard_hit_pct = home_p.get("hard_hit_pct_allowed") if home_p else None
    away_hard_hit_pct = away_p.get("hard_hit_pct_allowed") if away_p else None
    home_avg_ev       = home_p.get("avg_ev_allowed")        if home_p else None
    away_avg_ev       = away_p.get("avg_ev_allowed")        if away_p else None
    # R/G reales recientes (más directo que OPS como señal ofensiva)
    home_rg_recent  = game.get("home_rg_recent")   # carreras/partido últimos 28 días
    away_rg_recent  = game.get("away_rg_recent")
    # Árbitro home plate — ajuste calculado en el collector (umpscorecards.com)
    # Positivo = zona apretada (más BB → más scoring); negativo = zona amplia (más Ks → menos)
    ump_run_adj = float(game.get("ump_run_adj") or 0.0)
    hp_umpire   = game.get("hp_umpire") or ""
    home_ops        = game.get("home_ops") or (game.get("home_offense") or {}).get("ops")
    away_ops        = game.get("away_ops") or (game.get("away_offense") or {}).get("ops")
    home_ops_season = game.get("home_ops_season") or home_ops
    away_ops_season = game.get("away_ops_season") or away_ops
    home_ops_recent = game.get("home_ops_recent")
    away_ops_recent = game.get("away_ops_recent")
    home_ops_blended= game.get("home_ops_blended")  # blend temporada+reciente (antes del lineup)
    away_ops_blended= game.get("away_ops_blended")
    # Savant xwOBA ofensivo del equipo — expected wOBA (elimina suerte en BABIP)
    home_xwoba      = game.get("home_xwoba")
    away_xwoba      = game.get("away_xwoba")
    home_woba       = game.get("home_woba")
    away_woba       = game.get("away_woba")
    home_has_recent = game.get("home_has_recent", False)
    away_has_recent = game.get("away_has_recent", False)
    home_lineup_used  = game.get("home_lineup_used", False)
    away_lineup_used  = game.get("away_lineup_used", False)
    home_lineup_names = game.get("home_lineup_names", [])
    away_lineup_names = game.get("away_lineup_names", [])
    # H2H y clima eliminados del modelo (ruido, R²<1% en backtest)
    total_line = game.get("total_line")
    park_factor = game.get("park_factor", 1.0)

    # Líneas muy altas: WR real 14% en 9.0-9.5 y 0% en 10+ — sin edge en totales.
    # (ML y Run Line se evalúan igual — no dependen del total line)
    _skip_totals = bool(total_line and total_line > _MAX_TOTAL_LINE)

    # Movimiento de línea (apertura vs actual)
    opening_line     = game.get("opening_line")
    line_movement    = float(game.get("line_movement",   0.0) or 0.0)
    movement_signal  = game.get("movement_signal", "neutral")

    # Necesitamos stats de pitchers y bateadores para el modelo
    if not all([home_fip, away_fip, home_ops, away_ops, total_line]):
        return []

    # Filtro: pitchers confirmados con suficientes innings
    home_ip = (home_p.get("ip", 0) or 0) if home_p else 0
    away_ip = (away_p.get("ip", 0) or 0) if away_p else 0
    home_confirmed = home_p is not None and home_p.get("name") not in ("TBD", "", None)
    away_confirmed = away_p is not None and away_p.get("name") not in ("TBD", "", None)

    if not (home_confirmed and away_confirmed):
        return []   # No apostar si hay TBD en algún pitcher

    # Filtro duro de IP mínima: con < 10 IP el FIP/xFIP es estadísticamente inútil.
    # El agente puede exigir más IP (ej: Zeus requiere 40 IP para muestra confiable).
    _MIN_IP_HARD = max(10.0, _min_ip_agent)
    if home_ip < _MIN_IP_HARD or away_ip < _MIN_IP_HARD:
        return []

    # Filtro de GS mínimos: un pitcher con alta IP pero pocos GS es relevista u opener,
    # no starter. Su xFIP es de rol equivocado — no aplica para proyectar 5-6 innings.
    home_gs = (home_p.get("games_started", 0) or 0) if home_p else 0
    away_gs = (away_p.get("games_started", 0) or 0) if away_p else 0
    _MIN_GS_HARD = max(MIN_GS_STARTER, _cfg.get("min_gs", MIN_GS_STARTER))
    if home_gs < _MIN_GS_HARD or away_gs < _MIN_GS_HARD:
        _low_gs = home_p.get("name", "?") if home_gs < _MIN_GS_HARD else away_p.get("name", "?")
        print(f"  ⏭ GS insuficiente: {_low_gs} ({min(home_gs, away_gs)} GS < {_MIN_GS_HARD}) — partido excluido")
        return []

    # ── Score compuesto del pitcher → Effective FIP ──────────────────────────
    # Modelo simplificado: xFIP_blended directo, bullpen = LEAGUE_AVG_BULLPEN_ERA.
    # El score compuesto incorpora xERA de Savant (cuando disponible) y ajuste de contacto
    # (barrel%/hard_hit% permitidos) como señal complementaria al xFIP.
    home_ip_per_start = (home_p.get("ip_per_start") or
                         (home_p.get("ip", 0) / max(home_p.get("games_started", 1) or 1, 1)))
    away_ip_per_start = (away_p.get("ip_per_start") or
                         (away_p.get("ip", 0) / max(away_p.get("games_started", 1) or 1, 1)))

    # Score compuesto: xFIP + xERA (si disponible) + contacto (si xERA no disponible)
    # home_fip / away_fip se mantienen como xFIP puro para display y filtros
    home_fip_composite = _composite_fip(home_fip, home_xera, home_brl_pct, home_hard_hit_pct)
    away_fip_composite = _composite_fip(away_fip, away_xera, away_brl_pct, away_hard_hit_pct)
    _home_has_composite = (home_xera is not None or home_brl_pct is not None)
    _away_has_composite = (away_xera is not None or away_brl_pct is not None)

    # El local batea contra el pitcher visitante + bullpen visitante (liga avg)
    away_eff_fip = _effective_fip(away_fip_composite, away_ip_per_start)
    # El visitante batea contra el pitcher local + bullpen local (liga avg)
    home_eff_fip = _effective_fip(home_fip_composite, home_ip_per_start)

    # ── OPS compuesto (xwOBA correction) ─────────────────────────────────────
    # xwOBA elimina la suerte en BABIP usando velocidad y ángulo de contacto.
    # Un equipo con xwOBA > wOBA ha sido "unlucky" → composite eleva su OPS.
    # Un equipo con xwOBA < wOBA ha tenido "suerte"  → composite baja su OPS.
    home_ops_composite, _home_has_xwoba = _composite_ops(home_ops, home_xwoba)
    away_ops_composite, _away_has_xwoba = _composite_ops(away_ops, away_xwoba)

    # ── Platoon adjustment (L/R handedness) ──────────────────────────────────
    # Local batea contra el pitcher VISITANTE → usar handedness del pitcher visitante
    home_platoon_adj, home_platoon_desc = _platoon_ops_adj(
        game.get("away_pitcher_hand"), game.get("home_lineup_pct_l")
    )
    # Visitante batea contra el pitcher LOCAL → usar handedness del pitcher local
    away_platoon_adj, away_platoon_desc = _platoon_ops_adj(
        game.get("home_pitcher_hand"), game.get("away_lineup_pct_l")
    )
    # Aplicar platoon sobre el OPS compuesto (composite ya incorpora xwOBA)
    home_ops_eff = round(max(0.550, min(home_ops_composite + home_platoon_adj, 0.980)), 3)
    away_ops_eff = round(max(0.550, min(away_ops_composite + away_platoon_adj, 0.980)), 3)

    # ── Modelo de carreras ────────────────────────────────────────────────────
    # H2H eliminado del modelo: señal ruidosa (muestra pequeña, R²<1% en backtest)
    exp_home = round(expected_runs_team(home_ops_eff, away_eff_fip, park_factor, is_home=True), 2)
    exp_away = round(expected_runs_team(away_ops_eff, home_eff_fip, park_factor, is_home=False), 2)
    mu_base  = exp_home + exp_away

    # ── Ajuste climático (reactivado con umbral de viento) ──────────────────
    # Solo se aplica cuando la componente de viento hacia/desde CF ≥ 12 mph.
    # Por debajo de ese umbral el efecto es ruido; la temperatura siempre aplica.
    _weather    = game.get("weather")
    _weather_adj_raw, _weather_desc = _weather_run_adj(_weather) if _weather else (0.0, "")

    # Separar componentes para aplicar umbral selectivo
    if _weather:
        _w_speed   = _weather.get("wind_speed_mph", 0.0)
        _w_deg     = _weather.get("wind_deg", 0)
        _cf_bear   = _weather.get("cf_bearing", 0)
        _w_toward  = (_w_deg + 180) % 360
        _ang_diff  = abs(_w_toward - _cf_bear)
        if _ang_diff > 180:
            _ang_diff = 360 - _ang_diff
        _wind_component = math.cos(math.radians(_ang_diff))
        _wind_cf_mph    = abs(_w_speed * _wind_component)

        _temp_f    = _weather.get("temp_f", _TEMP_BASELINE_F)
        _temp_adj  = (_temp_f - _TEMP_BASELINE_F) / 10.0 * _TEMP_ADJ_PER_10F

        if _wind_cf_mph >= 12.0:
            # Viento significativo → usar ajuste total (temp + viento)
            weather_adj  = _weather_adj_raw
            weather_desc = _weather_desc
        else:
            # Viento débil → solo temperatura (si es relevante)
            weather_adj  = max(-_WEATHER_ADJ_CAP, min(_WEATHER_ADJ_CAP, _temp_adj))
            weather_desc = _weather_desc if abs(_temp_adj) >= 0.10 else ""
    else:
        weather_adj  = 0.0
        weather_desc = ""

    # ── Forma reciente de los equipos (R/G últimos 28 días) ─────────────────
    # Blend 85% modelo (xFIP-driven) + 15% forma reciente (R/G real).
    # Ancla el mu en observaciones recientes para evitar overestimar en equipos fríos.
    # Si no hay dato reciente, usamos solo el modelo sin penalización.
    if home_rg_recent and away_rg_recent:
        _rg_recent_total = home_rg_recent + away_rg_recent
        mu_total = mu_base * 0.85 + _rg_recent_total * 0.15 + weather_adj + ump_run_adj
    else:
        mu_total = mu_base + weather_adj + ump_run_adj

    # Lluvia pronosticada a la hora del juego: delay probable = abridores fuera
    # temprano = más bullpen = más varianza. Con pop ≥ gate los props ya están
    # bloqueados (_rain_blocked); en totales solo se advierte en reasons.
    _rain_pop  = game.get("rain_pop")
    _rain_note = (f"🌧️ Lluvia {_rain_pop:.0%} a la hora del juego — riesgo de delay (bullpen temprano)"
                  if _rain_pop is not None and _rain_pop >= 0.30 else "")

    p_over, p_under = p_over_under(mu_total, total_line)

    # SIN Platt en totales: el modelo de totales va ~78% WR histórico (gana POR
    # ENCIMA de su prob declarada → está sub-confiado). El Platt del pool mlb
    # (b=-0.30) lo SUPRIME aún más, que es exactamente al revés — fue entrenado
    # sobre un pool contaminado con props genéricos malos. Suprimir un modelo
    # bueno con calibración de un pool malo lo estrangulaba. Se recalibrará
    # con datos segmentados cuando haya muestra suficiente por mercado.

    # ── Cuotas del mercado ───────────────────────────────────────────────────
    # consensus_impl_over/under son probabilidades ya sin vig (0.0-1.0)
    raw_over  = game.get("consensus_impl_over")  or game.get("over_odds")
    raw_under = game.get("consensus_impl_under") or game.get("under_odds")

    if not raw_over or not raw_under:
        return []

    # Pueden venir como probabilidad implícita o cuota americana
    # collector_mlb ya las convierte a probabilidad implícita
    if isinstance(raw_over, float) and raw_over < 1.0:
        mkt_impl_over  = raw_over
        mkt_impl_under = raw_under
    else:
        mkt_impl_over  = american_to_prob(int(raw_over))
        mkt_impl_under = american_to_prob(int(raw_under))

    fair_over, fair_under = remove_vig_two_way(mkt_impl_over, mkt_impl_under)

    edge_over  = p_over  - fair_over
    edge_under = p_under - fair_under

    # ── Construir razones del pick ────────────────────────────────────────────
    home_name = game.get("home_team", "?")
    away_name = game.get("away_team", "?")
    home_abbr = game.get("home_abbr", home_name[:3].upper())
    away_abbr = game.get("away_abbr", away_name[:3].upper())

    home_pitcher_name = home_p.get("name", "?")
    away_pitcher_name = away_p.get("name", "?")

    def _ip_warn(ip: float) -> str:
        if ip < MIN_IP_STARTER:
            return f" ⚠️ solo {ip:.0f} IP"
        elif ip < 50:
            return f" ({ip:.0f} IP)"
        return ""

    def _build_pick(direction: str, edge: float, our_prob: float, fair_mkt: float) -> dict:
        odds_key = "odds_over" if direction == "OVER" else "odds_under"
        odds_val = game.get(odds_key, -110)

        if edge >= _CONFIDENCE_HIGH:
            confianza = "ALTA"
        elif edge >= _CONFIDENCE_MED:
            confianza = "MEDIA"
        else:
            confianza = "BAJA"

        def _ops_detail(ops_final, ops_season, ops_recent, has_recent, lineup_used, lineup_blended):
            """
            Describe el OPS usado en el modelo.
            - Si lineup confirmado: muestra OPS del lineup + el blended de referencia
            - Si blend: muestra temporada + reciente
            - Si solo temporada: muestra el valor directo
            """
            if lineup_used and lineup_blended:
                diff = ops_final - lineup_blended
                diff_str = f"{diff:+.3f}" if abs(diff) >= 0.005 else "≈ equipo"
                return f"{ops_final:.3f} [lineup] ({diff_str} vs equipo {lineup_blended:.3f})"
            if has_recent and ops_recent:
                return f"{ops_final:.3f} (temp {ops_season:.3f} | últ28d {ops_recent:.3f})"
            return f"{ops_final:.3f}"

        def _kbb9_label(kbb9: float | None) -> str:
            """Etiqueta de calidad de arsenal basada en K-BB/9."""
            if kbb9 is None:
                return ""
            if kbb9 >= 7.0:
                return f"K-BB/9={kbb9:.1f} [elite]"
            if kbb9 >= 5.0:
                return f"K-BB/9={kbb9:.1f} [above avg]"
            if kbb9 >= 3.0:
                return f"K-BB/9={kbb9:.1f} [avg]"
            if kbb9 >= 1.5:
                return f"K-BB/9={kbb9:.1f} [below avg]"
            return f"K-BB/9={kbb9:.1f} [debil]"

        def _fip_detail(fip_blended, fip_season, fip_recent, has_recent, uses_xfip,
                        xfip_season=None, xfip_recent=None, fip_raw=None):
            """
            Describe el FIP/xFIP usado en el modelo.
            - Si xFIP disponible: muestra xFIP (el valor que usa el modelo) + FIP raw como referencia
            - Si hay forma reciente: muestra temporada | últimas 5 salidas
            """
            prefix = "xFIP" if uses_xfip else "FIP"
            if has_recent and fip_recent is not None:
                recent_val = xfip_recent if (uses_xfip and xfip_recent) else fip_recent
                season_val = xfip_season if (uses_xfip and xfip_season) else fip_season
                detail = f"{fip_blended:.2f} (temp {season_val:.2f} | últ5 {recent_val:.2f})"
            else:
                season_val = xfip_season if (uses_xfip and xfip_season) else fip_season
                detail = f"{fip_blended:.2f} (temp {season_val:.2f})" if season_val else f"{fip_blended:.2f}"

            # Si estamos usando xFIP, mostrar FIP crudo como nota informativa
            raw_note = ""
            if uses_xfip and fip_raw and abs(fip_blended - fip_raw) >= 0.15:
                diff = fip_blended - fip_raw
                raw_note = f" FIPraw={fip_raw:.2f}({diff:+.2f})"
            return f"{prefix}={detail}{raw_note}"

        # Lineup info para display
        home_lineup_str = ""
        away_lineup_str = ""
        if home_lineup_used and home_lineup_names:
            home_lineup_str = f"\n     Titulares: {', '.join(home_lineup_names[:5])}"
        if away_lineup_used and away_lineup_names:
            away_lineup_str = f"\n     Titulares: {', '.join(away_lineup_names[:5])}"

        # K-BB/9 y HR/FB para display
        home_kbb_str = _kbb9_label(home_kbb9)
        away_kbb_str = _kbb9_label(away_kbb9)
        # Alerta HR/FB: si el pitcher tiene HR/FB > 1.5× la liga (ej: >15.8%) → puede regresar a la media
        away_hr_fb_note = ""
        home_hr_fb_note = ""
        if away_hr_fb is not None and away_hr_fb > LEAGUE_HR_PER_FB * 1.4:
            away_hr_fb_note = f" HR/FB={away_hr_fb:.1%} [alto, xFIP < FIP]"
        elif away_hr_fb is not None and away_hr_fb < LEAGUE_HR_PER_FB * 0.6:
            away_hr_fb_note = f" HR/FB={away_hr_fb:.1%} [bajo, xFIP > FIP]"
        if home_hr_fb is not None and home_hr_fb > LEAGUE_HR_PER_FB * 1.4:
            home_hr_fb_note = f" HR/FB={home_hr_fb:.1%} [alto, xFIP < FIP]"
        elif home_hr_fb is not None and home_hr_fb < LEAGUE_HR_PER_FB * 0.6:
            home_hr_fb_note = f" HR/FB={home_hr_fb:.1%} [bajo, xFIP > FIP]"

        # Savant: xERA vs xFIP/FIP — divergencia indica suerte/habilidad no capturada por el modelo
        away_fip_label = "xFIP" if away_uses_xfip else "FIP"
        home_fip_label = "xFIP" if home_uses_xfip else "FIP"
        away_xera_note = ""
        home_xera_note = ""
        if away_xera is not None and away_fip is not None:
            diff = away_xera - away_fip
            if diff < -_XERA_DIVERGENCE_THRESHOLD:
                away_xera_note = f" xERA={away_xera:.2f} [mejor que {away_fip_label}, favorece pitcher]"
            elif diff > _XERA_DIVERGENCE_THRESHOLD:
                away_xera_note = f" xERA={away_xera:.2f} [peor que {away_fip_label}, puede regresar]"
            else:
                away_xera_note = f" xERA={away_xera:.2f}"
        if home_xera is not None and home_fip is not None:
            diff = home_xera - home_fip
            if diff < -_XERA_DIVERGENCE_THRESHOLD:
                home_xera_note = f" xERA={home_xera:.2f} [mejor que {home_fip_label}, favorece pitcher]"
            elif diff > _XERA_DIVERGENCE_THRESHOLD:
                home_xera_note = f" xERA={home_xera:.2f} [peor que {home_fip_label}, puede regresar]"
            else:
                home_xera_note = f" xERA={home_xera:.2f}"

        # Fastball velocity para display
        away_velo_note = ""
        home_velo_note = ""
        if away_fb_velo is not None:
            label = " [elite]" if away_fb_velo >= _FB_VELO_ELITE else (" [bajo]" if away_fb_velo < _FB_VELO_WEAK else "")
            away_velo_note = f" {away_fb_type}={away_fb_velo:.1f}mph{label}"
        if home_fb_velo is not None:
            label = " [elite]" if home_fb_velo >= _FB_VELO_ELITE else (" [bajo]" if home_fb_velo < _FB_VELO_WEAK else "")
            home_velo_note = f" {home_fb_type}={home_fb_velo:.1f}mph{label}"

        # Contacto (barrel%, hard_hit%) para display — solo cuando xERA no disponible
        # (si hay xERA, el composite ya lo incorpora; mostrar contacto explícito es redundante)
        def _contact_note(brl: float | None, hh: float | None, has_xera: bool) -> str:
            if has_xera or (brl is None and hh is None):
                return ""
            parts = []
            if brl is not None:
                diff = brl - LEAGUE_AVG_BARREL_PCT
                tag = " [HIGH]" if diff > 2.5 else (" [bajo]" if diff < -2.0 else "")
                parts.append(f"brl%={brl:.1f}{tag}")
            if hh is not None:
                diff = hh - LEAGUE_AVG_HARD_HIT
                tag = " [HIGH]" if diff > 5.0 else (" [bajo]" if diff < -5.0 else "")
                parts.append(f"HH%={hh:.1f}{tag}")
            return (" | " + " ".join(parts)) if parts else ""

        away_contact_note = _contact_note(away_brl_pct, away_hard_hit_pct, away_xera is not None)
        home_contact_note = _contact_note(home_brl_pct, home_hard_hit_pct, home_xera is not None)

        # Composite FIP vs raw xFIP para display (muestra divergencia cuando >0.15)
        def _composite_note(fip_raw: float, fip_comp: float, has_composite: bool) -> str:
            if not has_composite or abs(fip_comp - fip_raw) < 0.10:
                return ""
            diff = fip_comp - fip_raw
            return f" comp={fip_comp:.2f}({diff:+.2f})"

        away_comp_note = _composite_note(away_fip, away_fip_composite, _away_has_composite)
        home_comp_note = _composite_note(home_fip, home_fip_composite, _home_has_composite)

        # xwOBA ofensivo — muestra cuando diverge del wOBA real (indica suerte/mala suerte)
        def _xwoba_note(xwoba: float | None, woba: float | None, has_xwoba: bool,
                        ops: float, ops_comp: float) -> str:
            if not has_xwoba or xwoba is None:
                return ""
            parts = [f"xwOBA={xwoba:.3f}"]
            if woba is not None and abs(xwoba - woba) >= 0.010:
                diff = xwoba - woba
                tag  = "unlucky↑" if diff > 0 else "suerte↓"
                parts.append(f"{diff:+.3f} vs wOBA [{tag}]")
            if abs(ops_comp - ops) >= 0.008:
                diff_ops = ops_comp - ops
                parts.append(f"OPS adj={diff_ops:+.3f}")
            return f" | {' '.join(parts)}" if parts else ""

        home_xwoba_note = _xwoba_note(home_xwoba, home_woba, _home_has_xwoba,
                                      home_ops, home_ops_composite)
        away_xwoba_note = _xwoba_note(away_xwoba, away_woba, _away_has_xwoba,
                                      away_ops, away_ops_composite)

        reasons = [
            f"mu total esperado: {mu_total:.1f} carreras (linea {total_line})",
            f"Local  {home_abbr}: {exp_home:.1f} carr. esperadas"
            f"  (OPS={_ops_detail(home_ops, home_ops_season, home_ops_recent, home_has_recent, home_lineup_used, home_ops_blended)}"
            + (home_xwoba_note if home_xwoba_note else "")
            + f" vs {away_pitcher_name}"
            f" {_fip_detail(away_fip, away_fip_season, away_fip_recent, away_has_fip_recent, away_uses_xfip, away_xfip_season, away_xfip_recent, away_fip_season)}"
            f"{away_hr_fb_note}{_ip_warn(away_ip)}"
            f", eff.={away_eff_fip:.2f}{away_comp_note}"
            + (f" | {away_kbb_str}" if away_kbb_str else "")
            + (away_velo_note if away_velo_note else "")
            + (away_xera_note if away_xera_note else "")
            + (away_contact_note if away_contact_note else "")
            + f"){home_lineup_str}",
            f"Visita {away_abbr}: {exp_away:.1f} carr. esperadas"
            f"  (OPS={_ops_detail(away_ops, away_ops_season, away_ops_recent, away_has_recent, away_lineup_used, away_ops_blended)}"
            + (away_xwoba_note if away_xwoba_note else "")
            + f" vs {home_pitcher_name}"
            f" {_fip_detail(home_fip, home_fip_season, home_fip_recent, home_has_fip_recent, home_uses_xfip, home_xfip_season, home_xfip_recent, home_fip_season)}"
            f"{home_hr_fb_note}{_ip_warn(home_ip)}"
            f", eff.={home_eff_fip:.2f}{home_comp_note}"
            + (f" | {home_kbb_str}" if home_kbb_str else "")
            + (home_velo_note if home_velo_note else "")
            + (home_xera_note if home_xera_note else "")
            + (home_contact_note if home_contact_note else "")
            + f"){away_lineup_str}",
            f"Park factor: {park_factor:.2f}",
        ] + ([f"Platoon local  {home_abbr}: {home_platoon_desc}"] if home_platoon_desc else []) + (
            [f"Platoon visita {away_abbr}: {away_platoon_desc}"] if away_platoon_desc else []
        ) + (
            # Shrinkage: mostrar cuando se encogió xFIP significativamente (> 0.10)
            [f"Shrinkage {home_abbr}: xFIP {home_fip_preshrink:.2f}→{home_fip:.2f} ({home_ip:.0f} IP, {home_ip/_IP_FULL_CONFIDENCE:.0%} conf.)"]
            if home_fip_preshrink and abs(home_fip_preshrink - home_fip) >= 0.10 else []
        ) + (
            [f"Shrinkage {away_abbr}: xFIP {away_fip_preshrink:.2f}→{away_fip:.2f} ({away_ip:.0f} IP, {away_ip/_IP_FULL_CONFIDENCE:.0%} conf.)"]
            if away_fip_preshrink and abs(away_fip_preshrink - away_fip) >= 0.10 else []
        ) + (
            # Descanso del pitcher: mostrar cuando hay penalización (desc no vacío)
            [f"Descanso {home_abbr}: {home_rest_desc}"] if home_rest_desc else []
        ) + (
            [f"Descanso {away_abbr}: {away_rest_desc}"] if away_rest_desc else []
        ) + (
            # R/G reciente: ahora integrado en mu_total (blend 85%/15%)
            [f"Forma reciente (28d): {home_abbr} {home_rg_recent:.1f} | {away_abbr} {away_rg_recent:.1f} R/G "
             f"→ mu blend {mu_base:.2f}→{mu_total - weather_adj:.2f} "
             f"({(mu_total - weather_adj) - mu_base:+.2f} ajuste)"]
            if home_rg_recent and away_rg_recent else []
        ) + (
            # Movimiento de línea: señal de mercado — confirma o advierte sobre el pick
            [_line_movement_str(direction, movement_signal, line_movement,
                                opening_line, total_line)]
            if movement_signal != "neutral" and opening_line is not None else []
        ) + (
            # Clima: solo cuando el ajuste es significativo (>= 0.20 carr.)
            [f"Clima: {weather_desc} → ajuste {weather_adj:+.2f} carr."]
            if abs(weather_adj) >= 0.20 and weather_desc else (
                [f"Clima: ajuste {weather_adj:+.2f} carr."]
                if abs(weather_adj) >= 0.20 else []
            )
        ) + (
            # Árbitro: solo cuando el ajuste es significativo (>= 0.10 carr.)
            [f"HP Ump: {hp_umpire} → {'apretado' if ump_run_adj > 0 else 'amplio'} ({ump_run_adj:+.2f} carr.)"]
            if hp_umpire and abs(ump_run_adj) >= 0.10 else (
                [f"HP Ump: {hp_umpire}"]
                if hp_umpire else []
            )
        ) + ([_rain_note] if _rain_note else []) + [
            f"Nuestra prob: {our_prob:.1%} vs mercado fair: {fair_mkt:.1%}",
        ]

        return {
            "game_pk":       game.get("game_pk"),
            "home_team":     home_name,
            "away_team":     away_name,
            "home_abbr":     home_abbr,
            "away_abbr":     away_abbr,
            "game_time":     game.get("game_time", ""),
            "commence_iso":  game.get("commence_iso", ""),
            "bet_type":    "TOTAL",
            "direction":   direction,
            "line":        total_line,
            "odds":        odds_val,
            "our_prob":    our_prob,
            "fair_market": fair_mkt,
            "edge":        edge,
            "confianza":   confianza,
            "mu_total":    mu_total,
            "exp_home":    exp_home,
            "exp_away":    exp_away,
            "reasons":     reasons,
            "home_pitcher": home_pitcher_name,
            "away_pitcher": away_pitcher_name,
            "home_fip":           home_fip,           # xFIP blended (raw, para display/filtros)
            "away_fip":           away_fip,
            "home_fip_composite": home_fip_composite, # score compuesto (lo que usa el modelo)
            "away_fip_composite": away_fip_composite,
            "home_xera":          home_xera,           # xERA Savant (si disponible)
            "away_xera":          away_xera,
            "home_brl_pct":       home_brl_pct,        # barrel% permitido
            "away_brl_pct":       away_brl_pct,
            "home_hard_hit_pct":  home_hard_hit_pct,   # hard hit% permitido
            "away_hard_hit_pct":  away_hard_hit_pct,
            "home_xwoba":         home_xwoba,           # xwOBA ofensivo del equipo (Savant)
            "away_xwoba":         away_xwoba,
            "home_ops_composite": home_ops_composite,   # OPS blend + xwOBA correction
            "away_ops_composite": away_ops_composite,
            "home_fip_season":  home_fip_season,   # solo temporada
            "away_fip_season":  away_fip_season,
            "home_fip_recent":  home_fip_recent,   # últimas 5 salidas
            "away_fip_recent":  away_fip_recent,
            "home_eff_fip":     home_eff_fip,
            "away_eff_fip":     away_eff_fip,
            "home_ip_per_start":home_ip_per_start,
            "away_ip_per_start":away_ip_per_start,
            "home_ops":         home_ops,
            "away_ops":         away_ops,
            "home_ops_season":  home_ops_season,
            "away_ops_season":  away_ops_season,
            "home_ops_recent":  home_ops_recent,
            "away_ops_recent":  away_ops_recent,
            "home_ops_blended": home_ops_blended,
            "away_ops_blended": away_ops_blended,
            "home_lineup_used": home_lineup_used,
            "away_lineup_used": away_lineup_used,
            "park_factor":      park_factor,
            "mu_base":          mu_base,
            "weather_adj":      weather_adj,
            "weather_desc":     weather_desc,
            "ump_run_adj":      ump_run_adj,
            "hp_umpire":        hp_umpire,
        }

    # ── Consenso de señales: movimiento de línea ─────────────────────────────
    # Si las casas reciben dinero inteligente en la dirección opuesta → skip.
    # El mercado sabe algo que el modelo no ve. No pelear contra el steam.
    # "neutral" = sin señal → no bloquear. Solo bloquear en señal activa contraria.
    _steam_over  = movement_signal in ("steam_over",  "strong_over")
    _steam_under = movement_signal in ("steam_under", "strong_under")

    # ── Evaluar Over ──────────────────────────────────────────────────────────
    # Cap: edge > 14% indica modelo patológico en ese partido (datos extremos)
    if not _skip_totals and ALLOW_OVER and 0 < edge_over <= _MAX_PICK_EDGE and edge_over >= _min_edge and p_over >= _min_prob_ov:
        # Consenso: línea moviéndose hacia UNDER = mercado contradice el OVER → skip
        _line_consensus_ok = not _steam_under
        # Filtro 3: ace presente → shrinkage adicional del 10% sobre mu
        _ace_present  = min(home_fip, away_fip) <= _ELITE_ACE_XFIP
        _mu_adj       = mu_total * (1.0 - _ELITE_ACE_SHRINKAGE) if _ace_present else mu_total
        # Filtro 1: margen mínimo de mu (ajustado) sobre la línea
        _mu_margin_ok = (_mu_adj - total_line) >= _OVER_MIN_MU_MARGIN
        # Filtro 2: al menos un pitcher genuinamente débil (no apto para duelo de pitchers)
        _weak_pitcher_ok = (home_fip >= _OVER_MIN_WEAK_XFIP or away_fip >= _OVER_MIN_WEAK_XFIP)
        # Filtro 4: forma reciente — equipos fríos no califican para OVER
        # Patrón CLE@TEX: combined R/G reciente bajo el umbral de la línea = no hay evidencia ofensiva
        _recent_form_ok = True
        if home_rg_recent and away_rg_recent:
            _combined_rg_recent = home_rg_recent + away_rg_recent
            _recent_form_ok = _combined_rg_recent >= total_line * _OVER_MIN_RECENT_RG_RATIO

        if _line_consensus_ok and _mu_margin_ok and _weak_pitcher_ok and _recent_form_ok:
            p = _build_pick("OVER", edge_over, p_over, fair_over)
            p["narrative"] = _narrative_mlb(p)
            picks.append(p)

    # ── Evaluar Under ─────────────────────────────────────────────────────────
    # Filtros simplificados: ambos pitchers élite + línea alta
    if not _skip_totals and ALLOW_UNDER and 0 < edge_under <= _MAX_PICK_EDGE and edge_under >= _min_edge_un and p_under >= _min_prob_un:
        # Consenso: línea moviéndose hacia OVER = mercado contradice el UNDER → skip
        _line_consensus_ok_un = not _steam_over
        # Filtro 1: ambos pitchers con xFIP ≤ 3.5 (duelo de ases)
        _elite_pitchers = (home_fip <= _UNDER_MAX_XFIP and away_fip <= _UNDER_MAX_XFIP)
        # Filtro 2: línea alta (≥ 8.0) — apostar UNDER cuando el mercado espera ofensiva
        _high_line = (total_line >= _UNDER_MIN_LINE)

        if _line_consensus_ok_un and _elite_pitchers and _high_line:
            p = _build_pick("UNDER", edge_under, p_under, fair_under)
            p["narrative"] = _narrative_mlb(p)
            picks.append(p)

    # ── Evaluar Moneyline ─────────────────────────────────────────────────────
    impl_home_ml = game.get("consensus_impl_home_ml")
    impl_away_ml = game.get("consensus_impl_away_ml")
    home_ml_odds = game.get("home_ml_odds")
    away_ml_odds = game.get("away_ml_odds")

    if impl_home_ml and impl_away_ml and home_ml_odds and away_ml_odds:
        p_home_win, p_away_win = win_prob_poisson(exp_home, exp_away)

        # Recalibración Platt por lado (el complemento invertiría la corrección)
        from calibration import calibrate
        p_home_win = calibrate(p_home_win, "mlb", "MONEYLINE")
        p_away_win = calibrate(p_away_win, "mlb", "MONEYLINE")

        fair_home_ml, fair_away_ml = remove_vig_two_way(impl_home_ml, impl_away_ml)

        edge_home_ml = p_home_win - fair_home_ml
        edge_away_ml = p_away_win - fair_away_ml

        def _build_ml_pick(direction: str, team_name: str, odds_val: int,
                           our_prob: float, fair_mkt: float, edge: float) -> dict:
            if edge >= _CONFIDENCE_HIGH:
                confianza = "ALTA"
            elif edge >= _CONFIDENCE_MED:
                confianza = "MEDIA"
            else:
                confianza = "BAJA"

            run_diff = exp_home - exp_away
            role_str  = "local" if direction == "HOME" else "visita"
            opp_role  = "visita" if direction == "HOME" else "local"
            our_runs  = exp_home if direction == "HOME" else exp_away
            opp_runs  = exp_away if direction == "HOME" else exp_home
            our_abbr  = home_abbr if direction == "HOME" else away_abbr
            opp_abbr  = away_abbr if direction == "HOME" else home_abbr
            our_fip     = home_fip           if direction == "HOME" else away_fip
            opp_fip     = away_fip           if direction == "HOME" else home_fip
            our_comp    = home_fip_composite if direction == "HOME" else away_fip_composite
            opp_comp    = away_fip_composite if direction == "HOME" else home_fip_composite
            our_xera    = home_xera          if direction == "HOME" else away_xera
            opp_xera    = away_xera          if direction == "HOME" else home_xera
            our_pitcher = home_pitcher_name  if direction == "HOME" else away_pitcher_name
            opp_pitcher = away_pitcher_name  if direction == "HOME" else home_pitcher_name
            our_ops      = home_ops           if direction == "HOME" else away_ops
            opp_ops      = away_ops           if direction == "HOME" else home_ops
            our_ops_comp = home_ops_composite if direction == "HOME" else away_ops_composite
            opp_ops_comp = away_ops_composite if direction == "HOME" else home_ops_composite
            our_xwoba    = home_xwoba         if direction == "HOME" else away_xwoba
            opp_xwoba    = away_xwoba         if direction == "HOME" else home_xwoba

            def _ml_fip_note(fip_raw: float, fip_comp: float, xera: float | None) -> str:
                parts = [f"xFIP={fip_raw:.2f}"]
                if abs(fip_comp - fip_raw) >= 0.10:
                    parts.append(f"comp={fip_comp:.2f}")
                if xera is not None:
                    parts.append(f"xERA={xera:.2f}")
                return " | ".join(parts)

            def _ml_ops_note(ops_raw: float, ops_comp: float, xwoba: float | None) -> str:
                s = f"{ops_raw:.3f}"
                if xwoba is not None and abs(ops_comp - ops_raw) >= 0.008:
                    s += f" xwOBA={xwoba:.3f}(adj{ops_comp-ops_raw:+.3f})"
                return s

            reasons = [
                f"Proyeccion: {our_abbr} {our_runs:.1f} carr. vs {opp_abbr} {opp_runs:.1f} carr."
                f" — ventaja {role_str} {run_diff:+.1f} carr.",
                f"Pitcher {our_pitcher} ({our_abbr}) {_ml_fip_note(our_fip, our_comp, our_xera)}"
                f" | {opp_pitcher} ({opp_abbr}) {_ml_fip_note(opp_fip, opp_comp, opp_xera)}",
                f"OPS {our_abbr}={_ml_ops_note(our_ops, our_ops_comp, our_xwoba)}"
                f" | OPS {opp_abbr}={_ml_ops_note(opp_ops, opp_ops_comp, opp_xwoba)}",
                f"Park factor: {park_factor:.2f}",
                f"Nuestra prob: {our_prob:.1%} vs mercado fair: {fair_mkt:.1%}",
            ]

            return {
                "game_pk":            game.get("game_pk"),
                "home_team":          home_name,
                "away_team":          away_name,
                "home_abbr":          home_abbr,
                "away_abbr":          away_abbr,
                "game_time":          game.get("game_time", ""),
                "commence_iso":       game.get("commence_iso", ""),
                "bet_type":           "ML",
                "direction":          direction,
                "team":               team_name,
                "line":               None,
                "odds":               odds_val,
                "our_prob":           our_prob,
                "fair_market":        fair_mkt,
                "edge":               edge,
                "confianza":          confianza,
                "mu_total":           mu_total,
                "exp_home":           exp_home,
                "exp_away":           exp_away,
                "reasons":            reasons,
                "home_pitcher":       home_pitcher_name,
                "away_pitcher":       away_pitcher_name,
                "home_fip":           home_fip,
                "away_fip":           away_fip,
                "home_fip_composite": home_fip_composite,
                "away_fip_composite": away_fip_composite,
                "home_xera":          home_xera,
                "away_xera":          away_xera,
                "home_xwoba":         home_xwoba,
                "away_xwoba":         away_xwoba,
                "home_ops_composite": home_ops_composite,
                "away_ops_composite": away_ops_composite,
                "park_factor":        park_factor,
            }

        if edge_home_ml <= _MAX_PICK_EDGE and edge_home_ml >= _ML_MIN_EDGE and p_home_win >= _ML_MIN_PROB:
            picks.append(_build_ml_pick("HOME", home_name, home_ml_odds,
                                        p_home_win, fair_home_ml, edge_home_ml))
        if edge_away_ml <= _MAX_PICK_EDGE and edge_away_ml >= _ML_MIN_EDGE and p_away_win >= _ML_MIN_PROB:
            picks.append(_build_ml_pick("AWAY", away_name, away_ml_odds,
                                        p_away_win, fair_away_ml, edge_away_ml))

    # ── Evaluar Run Line ──────────────────────────────────────────────────────
    impl_home_rl  = game.get("consensus_impl_home_rl")
    impl_away_rl  = game.get("consensus_impl_away_rl")
    home_rl_odds_val = game.get("home_rl_odds")
    away_rl_odds_val = game.get("away_rl_odds")
    home_rl_point    = float(game.get("home_rl_point", -1.5) or -1.5)
    away_rl_point    = -home_rl_point   # siempre opuesto

    if ALLOW_RUNLINE and impl_home_rl and impl_away_rl and home_rl_odds_val and away_rl_odds_val:
        p_home_rl, p_away_rl = win_prob_runline(exp_home, exp_away, home_rl_point)
        # Recalibración Platt por lado (el complemento invertiría la corrección)
        from calibration import calibrate
        p_home_rl = calibrate(p_home_rl, "mlb", "RUNLINE")
        p_away_rl = calibrate(p_away_rl, "mlb", "RUNLINE")
        fair_home_rl, fair_away_rl = remove_vig_two_way(impl_home_rl, impl_away_rl)

        edge_home_rl = p_home_rl - fair_home_rl
        edge_away_rl = p_away_rl - fair_away_rl

        def _build_rl_pick(direction: str, team_name: str, odds_val: int,
                           rl_point: float, our_prob: float, fair_mkt: float, edge: float) -> dict:
            if edge >= _CONFIDENCE_HIGH:
                confianza = "ALTA"
            elif edge >= _CONFIDENCE_MED:
                confianza = "MEDIA"
            else:
                confianza = "BAJA"

            run_diff  = exp_home - exp_away
            our_runs  = exp_home if direction == "HOME" else exp_away
            opp_runs  = exp_away if direction == "HOME" else exp_home
            our_abbr  = home_abbr if direction == "HOME" else away_abbr
            opp_abbr  = away_abbr if direction == "HOME" else home_abbr
            our_fip      = home_fip           if direction == "HOME" else away_fip
            opp_fip      = away_fip           if direction == "HOME" else home_fip
            our_comp     = home_fip_composite if direction == "HOME" else away_fip_composite
            opp_comp     = away_fip_composite if direction == "HOME" else home_fip_composite
            our_xera     = home_xera          if direction == "HOME" else away_xera
            opp_xera     = away_xera          if direction == "HOME" else home_xera
            our_pitcher  = home_pitcher_name  if direction == "HOME" else away_pitcher_name
            opp_pitcher  = away_pitcher_name  if direction == "HOME" else home_pitcher_name
            spread_str   = f"{rl_point:+.1f}"
            cover_desc   = "ganar por 2+" if rl_point < 0 else "ganar o perder por 1"

            def _rl_fip_note(fip_raw: float, fip_comp: float, xera: float | None) -> str:
                """Muestra xFIP con composite si difiere, y xERA si disponible."""
                parts = [f"xFIP={fip_raw:.2f}"]
                if abs(fip_comp - fip_raw) >= 0.10:
                    parts.append(f"comp={fip_comp:.2f}")
                if xera is not None:
                    parts.append(f"xERA={xera:.2f}")
                return " | ".join(parts)

            reasons = [
                f"Proyeccion: {our_abbr} {our_runs:.1f} carr. vs {opp_abbr} {opp_runs:.1f} carr."
                f" (diferencia esperada {run_diff:+.1f})",
                f"Run Line {team_name} {spread_str} — necesitamos {cover_desc}",
                f"Pitcher {our_pitcher} ({our_abbr}) {_rl_fip_note(our_fip, our_comp, our_xera)}"
                f" | {opp_pitcher} ({opp_abbr}) {_rl_fip_note(opp_fip, opp_comp, opp_xera)}",
                f"OPS {home_abbr}={home_ops:.3f} | OPS {away_abbr}={away_ops:.3f}",
                f"Park factor: {park_factor:.2f}",
                f"Nuestra prob: {our_prob:.1%} vs mercado fair: {fair_mkt:.1%}",
            ]

            return {
                "game_pk":            game.get("game_pk"),
                "home_team":          home_name,
                "away_team":          away_name,
                "home_abbr":          home_abbr,
                "away_abbr":          away_abbr,
                "game_time":          game.get("game_time", ""),
                "commence_iso":       game.get("commence_iso", ""),
                "bet_type":           "RL",
                "direction":          direction,
                "team":               team_name,
                "rl_point":           rl_point,
                "line":               rl_point,
                "odds":               odds_val,
                "our_prob":           our_prob,
                "fair_market":        fair_mkt,
                "edge":               edge,
                "confianza":          confianza,
                "mu_total":           mu_total,
                "exp_home":           exp_home,
                "exp_away":           exp_away,
                "reasons":            reasons,
                "home_pitcher":       home_pitcher_name,
                "away_pitcher":       away_pitcher_name,
                "home_fip":           home_fip,
                "away_fip":           away_fip,
                "home_fip_composite": home_fip_composite,
                "away_fip_composite": away_fip_composite,
                "home_xera":          home_xera,
                "away_xera":          away_xera,
                "home_xwoba":         home_xwoba,
                "away_xwoba":         away_xwoba,
                "home_ops_composite": home_ops_composite,
                "away_ops_composite": away_ops_composite,
                "park_factor":        park_factor,
            }

        # Solo favoritos: el equipo debe ceder -1.5 (ganar por 2+) y ser el proyectado ganador
        if (0 < edge_home_rl <= _MAX_PICK_EDGE and edge_home_rl >= _RL_MIN_EDGE and p_home_rl >= _RL_MIN_PROB
                and home_rl_point < 0 and exp_home > exp_away):
            picks.append(_build_rl_pick("HOME", home_name, int(home_rl_odds_val),
                                        home_rl_point, p_home_rl, fair_home_rl, edge_home_rl))
        if (0 < edge_away_rl <= _MAX_PICK_EDGE and edge_away_rl >= _RL_MIN_EDGE and p_away_rl >= _RL_MIN_PROB
                and away_rl_point < 0 and exp_away > exp_home):
            picks.append(_build_rl_pick("AWAY", away_name, int(away_rl_odds_val),
                                        away_rl_point, p_away_rl, fair_away_rl, edge_away_rl))

    return picks


# ── Modelo F5 (Primeros 5 Innings) ───────────────────────────────────────────
#
# Ventaja vs. total de juego completo:
#   - Solo depende del abridor (no del bullpen) → señal más limpia
#   - Nuestro xFIP ya captura esto perfectamente
#   - Elimina el mayor factor de ruido del modelo de totales
#
# F5_SCALE: carreras F5 / carreras partido. Empírico MLB 2024-25: ~4.9/8.9 ≈ 0.55.
# RUN_F5_SIGMA: σ para 5 innings. Derivado: σ_game × √(5/9) ≈ 4.59 × 0.745 ≈ 3.42.
#   Usamos 3.0 (conservador) → menos picks pero más calibrados.
F5_SCALE      = 0.55
RUN_F5_SIGMA  = 3.0
MAX_F5_PICKS  = 3   # máximo F5 picks por día (mercado más estrecho)

# Filtros de confirmación para F5 (adaptados a escala de 5 innings)
# F5 es el segundo mercado del nicho: la casa deriva la línea F5 mecánicamente
# del total del partido (que incluye bullpens); nuestro modelo de abridores es
# justamente donde está la información — esquivamos el punto ciego del bullpen.
_F5_MIN_EDGE       = 0.05    # rango del nicho (algo más exigente que K-props: σ mayor)
_F5_MAX_EDGE       = 0.18    # solo rechaza edges absurdos (error de datos); el F5 es niche
_F5_MIN_PROB       = 0.52    # sobre breakeven de -110 (sin Platt — ver nota abajo)
_F5_MARKET_ANCHOR  = 0.35    # F5 track record: 62% WR — confiamos más en el modelo que en K-props
_F5_MIN_MU_MARGIN  = 0.40    # |proyección − línea F5| mínima en carreras
_F5_MIN_TEAM_RUNS  = 1.8     # OVER: cada equipo debe proyectar ≥ 1.8 carreras en F5
_F5_MIN_WEAK_XFIP  = 3.90    # OVER: al menos un pitcher débil
_F5_ELITE_XFIP     = 3.60    # UNDER: al menos un abridor élite (la tesis del F5 inflado)

# Señal 1 — K% del lineup confirmado ajusta expected runs del equipo ofensor.
# Mecanismo: más Ks = menos balls in play = menos carreras.
# Sensibilidad empírica: cada 1pp de K% sobre la media ≈ -0.4% en runs.
# Cap ±8%: no sobrepesar una sola señal.
_F5_LINEUP_K_SENSITIVITY = 0.40   # pass-through de K% → runs (40%)
_F5_LINEUP_K_CAP         = 0.08   # ajuste máximo ±8% sobre expected runs

# Señal 2 — IP reciente: gate binario para OVERs.
# Si el pitcher promedia < 4.5 IP en últimas 5 salidas, hay riesgo real de
# que el bullpen entre en el inning 4-5 → el OVER asume esos innings al pitcher
# pero puede quedar en manos de un relevista que tire fuego. Se bloquea el OVER.
_F5_MIN_IP_RECENT_OVER   = 4.5    # IP/start reciente mínimo para apostar OVER F5


def analyze_f5_game(game: dict, paper: bool = False) -> list[dict]:
    """
    Genera picks de Primeros 5 Innings (F5 Over/Under) para un partido.

    Diferencia clave vs analyze_game():
    - NO usa _effective_fip() → xFIP del abridor directo, sin mezcla de bullpen.
    - Escala las carreras esperadas × F5_SCALE (5 innings, no 9).
    - Usa RUN_F5_SIGMA más tight (menos varianza que el juego completo).
    - Requiere f5_line + f5_impl_over/under en el game dict (del Odds API).

    paper=True: red amplia para validación (registra ambos lados sin gates).
    """
    picks = []

    # Verificar que hay odds F5 disponibles
    f5_line     = game.get("f5_line")
    f5_impl_ov  = game.get("f5_impl_over")
    f5_impl_un  = game.get("f5_impl_under")
    if not all([f5_line, f5_impl_ov, f5_impl_un]):
        return []

    # Gate de lluvia: el F5 vive en los primeros 5 innings — un delay temprano
    # saca al abridor y el modelo (que es 100% abridores) queda inválido
    if _rain_blocked(game, f"F5 {game.get('away_team','?')}@{game.get('home_team','?')}"):
        return []

    # Pitchers (mismo data que analyze_game)
    home_p = game.get("home_pitcher") or {}
    away_p = game.get("away_pitcher") or {}

    home_confirmed = home_p and home_p.get("name") not in ("TBD", "", None)
    away_confirmed = away_p and away_p.get("name") not in ("TBD", "", None)
    if not (home_confirmed and away_confirmed):
        return []

    home_ip = (home_p.get("ip", 0) or 0)
    away_ip = (away_p.get("ip", 0) or 0)
    if home_ip < 10.0 or away_ip < 10.0:
        return []

    # Gate de lineup: sin lineup confirmado de ninguno de los dos equipos, el modelo
    # usa solo OPS promedio de temporada — misma ventana de descuido que otros nichos.
    # Si al menos un equipo tiene lineup confirmado el pick sigue adelante (parcial).
    _home_lineup_ok = bool(game.get("home_lineup_k_used"))
    _away_lineup_ok = bool(game.get("away_lineup_k_used"))
    if not paper and not (_home_lineup_ok or _away_lineup_ok):
        away_t = game.get("away_team", "?")
        home_t = game.get("home_team", "?")
        print(f"  ⏭ F5 {away_t}@{home_t}: sin lineups confirmados — esperar a la ventana del nicho")
        return []

    # xFIP del abridor: usar xFIP_blended directo (splits eliminados — ruido con muestra pequeña)
    home_fip_f5 = home_p.get("xfip_blended") or home_p.get("fip_blended") or home_p.get("fip")
    away_fip_f5 = away_p.get("xfip_blended") or away_p.get("fip_blended") or away_p.get("fip")

    if not (home_fip_f5 and away_fip_f5):
        return []

    # Savant: xERA y calidad de contacto (para composite score)
    home_xera_f5    = home_p.get("xera")                if home_p else None
    away_xera_f5    = away_p.get("xera")                if away_p else None
    home_brl_pct_f5 = home_p.get("brl_pct_allowed")     if home_p else None
    away_brl_pct_f5 = away_p.get("brl_pct_allowed")     if away_p else None
    home_hh_pct_f5  = home_p.get("hard_hit_pct_allowed") if home_p else None
    away_hh_pct_f5  = away_p.get("hard_hit_pct_allowed") if away_p else None

    # Score compuesto: xFIP + xERA + contacto (misma lógica que analyze_game)
    # En F5, el abridor es 100% de la ecuación — composite tiene incluso más impacto
    home_fip_f5_composite = _composite_fip(home_fip_f5, home_xera_f5, home_brl_pct_f5, home_hh_pct_f5)
    away_fip_f5_composite = _composite_fip(away_fip_f5, away_xera_f5, away_brl_pct_f5, away_hh_pct_f5)
    _home_f5_has_composite = (home_xera_f5 is not None or home_brl_pct_f5 is not None)
    _away_f5_has_composite = (away_xera_f5 is not None or away_brl_pct_f5 is not None)

    # Días de descanso eliminados (R²<1% en backtest)

    home_ops    = game.get("home_ops") or (game.get("home_offense") or {}).get("ops")
    away_ops    = game.get("away_ops") or (game.get("away_offense") or {}).get("ops")
    park_factor = game.get("park_factor", 1.0)

    if not (home_ops and away_ops):
        return []

    # xwOBA ofensivo del equipo — misma corrección que en analyze_game()
    home_xwoba_f5 = game.get("home_xwoba")
    away_xwoba_f5 = game.get("away_xwoba")
    home_ops_comp_f5, _home_f5_has_xwoba = _composite_ops(home_ops, home_xwoba_f5)
    away_ops_comp_f5, _away_f5_has_xwoba = _composite_ops(away_ops, away_xwoba_f5)

    # Platoon L/R — en F5 sólo importa el abridor (sin bullpen → máximo impacto)
    home_platoon_adj_f5, home_platoon_desc_f5 = _platoon_ops_adj(
        game.get("away_pitcher_hand"), game.get("home_lineup_pct_l")
    )
    away_platoon_adj_f5, away_platoon_desc_f5 = _platoon_ops_adj(
        game.get("home_pitcher_hand"), game.get("away_lineup_pct_l")
    )
    # Aplicar platoon sobre el OPS compuesto (con corrección xwOBA)
    home_ops_eff_f5 = round(max(0.550, min(home_ops_comp_f5 + home_platoon_adj_f5, 0.980)), 3)
    away_ops_eff_f5 = round(max(0.550, min(away_ops_comp_f5 + away_platoon_adj_f5, 0.980)), 3)

    # Carreras esperadas en F5 — sin bullpen, escaladas a 5 innings
    # El local batea contra el abridor visitante; el visitante contra el abridor local.
    # Usamos el composite FIP (xFIP + xERA/contacto) para mayor precisión.
    exp_f5_home = round(expected_runs_team(home_ops_eff_f5, away_fip_f5_composite, park_factor, is_home=True)  * F5_SCALE, 2)
    exp_f5_away = round(expected_runs_team(away_ops_eff_f5, home_fip_f5_composite, park_factor, is_home=False) * F5_SCALE, 2)

    # Señal 1 — K% del lineup confirmado: ajusta expected runs del equipo ofensor.
    # Lineup con K% alto genera menos balls in play → menos carreras esperadas.
    # Solo aplica si hay lineup confirmado (k_used=True); fallback = sin ajuste.
    home_k_pct = game.get("home_lineup_k_pct")
    away_k_pct = game.get("away_lineup_k_pct")
    home_lineup_k_used = game.get("home_lineup_k_used", False)
    away_lineup_k_used = game.get("away_lineup_k_used", False)

    if home_lineup_k_used and home_k_pct:
        delta = (home_k_pct - LEAGUE_AVG_K_RATE_BATTING) / LEAGUE_AVG_K_RATE_BATTING
        adj   = max(-_F5_LINEUP_K_CAP, min(_F5_LINEUP_K_CAP, -delta * _F5_LINEUP_K_SENSITIVITY))
        exp_f5_home = round(exp_f5_home * (1 + adj), 2)

    if away_lineup_k_used and away_k_pct:
        delta = (away_k_pct - LEAGUE_AVG_K_RATE_BATTING) / LEAGUE_AVG_K_RATE_BATTING
        adj   = max(-_F5_LINEUP_K_CAP, min(_F5_LINEUP_K_CAP, -delta * _F5_LINEUP_K_SENSITIVITY))
        exp_f5_away = round(exp_f5_away * (1 + adj), 2)

    # Señal 2 — Durabilidad IP reciente: gate binario para OVERs.
    # Si un pitcher promedia < 4.5 IP en últimas 5 salidas, el bullpen puede
    # entrar en el inning 4 o 5 y el OVER queda en manos de relevistas.
    home_ip_recent_per = None
    away_ip_recent_per = None
    if home_p.get("ip_recent") and home_p.get("n_starts_recent", 0) >= 3:
        home_ip_recent_per = home_p["ip_recent"] / home_p["n_starts_recent"]
    if away_p.get("ip_recent") and away_p.get("n_starts_recent", 0) >= 3:
        away_ip_recent_per = away_p["ip_recent"] / away_p["n_starts_recent"]

    # Clima y árbitro eliminados del modelo F5 (misma razón que totales: R²<0.5%)
    mu_f5    = round(exp_f5_home + exp_f5_away, 2)

    p_over, p_under = p_over_under(mu_f5, f5_line, sigma=RUN_F5_SIGMA)
    # SIN Platt en el niche F5: el modelo es 100% abridores (esquiva el bullpen)
    # y la recalibración genérica suprime el edge de la tesis del derivado.
    # Se valida con CLV, no con datos del régimen viejo.

    # Eliminar vig del mercado F5
    fair_over, fair_under = remove_vig_two_way(f5_impl_ov, f5_impl_un)

    # Ancla al mercado (opción 2) — igual que K-props; no se aplica en paper
    if not paper:
        p_over  = (1 - _F5_MARKET_ANCHOR) * p_over  + _F5_MARKET_ANCHOR * fair_over
        p_under = (1 - _F5_MARKET_ANCHOR) * p_under + _F5_MARKET_ANCHOR * fair_under

    edge_over  = round(p_over  - fair_over,  4)
    edge_under = round(p_under - fair_under, 4)

    home_name        = game.get("home_team", "?")
    away_name        = game.get("away_team", "?")
    home_pitcher_name = home_p.get("name", "?")
    away_pitcher_name = away_p.get("name", "?")

    def _conf(edge: float) -> str:
        if edge >= _CONFIDENCE_HIGH: return "ALTA"
        if edge >= _CONFIDENCE_MED:  return "MEDIA"
        return "BAJA"

    def _build_f5_pick(direction: str, edge: float, our_prob: float, fair_mkt: float) -> dict:
        odds_val = game.get("f5_over_odds" if direction == "OVER" else "f5_under_odds")
        if not odds_val:
            odds_val = -110

        def _f5_pitcher_line(name: str, fip_raw: float, fip_comp: float,
                             has_comp: bool, xera: float | None, is_home: bool) -> str:
            """Formatea la línea del abridor para reasons de F5."""
            note = "(sin bullpen — F5 puro)" if not is_home else ""
            comp_note = ""
            if has_comp and abs(fip_comp - fip_raw) >= 0.10:
                diff = fip_comp - fip_raw
                comp_note = f" comp={fip_comp:.2f}({diff:+.2f})"
            xera_note = f" xERA={xera:.2f}" if xera is not None else ""
            return f"Abridor {name}: xFIP {fip_raw:.2f}{comp_note}{xera_note}" + (f" {note}" if note else "")

        reasons = [
            _f5_pitcher_line(away_name, away_fip_f5, away_fip_f5_composite, _away_f5_has_composite, away_xera_f5, is_home=False),
            _f5_pitcher_line(home_name, home_fip_f5, home_fip_f5_composite, _home_f5_has_composite, home_xera_f5, is_home=True),
            f"Proyección F5: {exp_f5_away:.1f} + {exp_f5_home:.1f} = {mu_f5:.1f} carreras vs línea {f5_line}",
        ]
        if home_platoon_desc_f5:
            home_abbr_f5 = game.get("home_abbr", home_name[:3].upper())
            reasons.append(f"Platoon local  {home_abbr_f5}: {home_platoon_desc_f5}")
        if away_platoon_desc_f5:
            away_abbr_f5 = game.get("away_abbr", away_name[:3].upper())
            reasons.append(f"Platoon visita {away_abbr_f5}: {away_platoon_desc_f5}")
        return {
            "home_team":  home_name,
            "away_team":  away_name,
            "home_abbr":  game.get("home_abbr", home_name[:3].upper()),
            "away_abbr":  game.get("away_abbr", away_name[:3].upper()),
            "game_time":  game.get("game_time", ""),
            "commence_iso": game.get("commence_iso", ""),
            "game_pk":    game.get("game_pk"),
            "bet_type":   "F5",
            "direction":  direction,
            "line":       f5_line,
            "odds":       int(odds_val),
            "our_prob":   round(our_prob, 4),
            "fair_market": round(fair_mkt, 4),
            "edge":       round(edge, 4),
            "confianza":  _conf(edge),
            "mu_total":   mu_f5,
            "exp_home":   exp_f5_home,
            "exp_away":   exp_f5_away,
            "home_pitcher": home_pitcher_name,
            "away_pitcher": away_pitcher_name,
            "home_fip_f5":         home_fip_f5,           # xFIP raw (display/filtros)
            "away_fip_f5":         away_fip_f5,
            "home_fip_f5_composite": home_fip_f5_composite,  # score compuesto (lo que usa el modelo)
            "away_fip_f5_composite": away_fip_f5_composite,
            "home_xera_f5":        home_xera_f5,           # xERA Savant (si disponible)
            "away_xera_f5":        away_xera_f5,
            "home_brl_pct_f5":     home_brl_pct_f5,        # barrel% permitido
            "away_brl_pct_f5":     away_brl_pct_f5,
            "home_hh_pct_f5":      home_hh_pct_f5,         # hard hit% permitido
            "away_hh_pct_f5":      away_hh_pct_f5,
            "home_xwoba_f5":       home_xwoba_f5,           # xwOBA ofensivo del equipo
            "away_xwoba_f5":       away_xwoba_f5,
            "home_ops_comp_f5":    home_ops_comp_f5,         # OPS compuesto (con xwOBA)
            "away_ops_comp_f5":    away_ops_comp_f5,
            "reasons":    reasons,
        }

    # Evaluar OVER F5 — techo del nicho: edge >12% = error de datos, no oportunidad
    # Modo PAPER: red amplia (umbral 0%) pero solo el lado con edge positivo — guardar
    # ambos lados produce un WIN+LOSS garantizado por prop, sin valor de calibración.
    if paper:
        if edge_over >= 0:
            picks.append(_build_f5_pick("OVER",  edge_over,  p_over,  fair_over))
        elif edge_under >= 0:
            picks.append(_build_f5_pick("UNDER", edge_under, p_under, fair_under))
        return picks

    if _F5_MIN_EDGE <= edge_over <= _F5_MAX_EDGE and p_over >= _F5_MIN_PROB:
        if exp_f5_home >= _F5_MIN_TEAM_RUNS and exp_f5_away >= _F5_MIN_TEAM_RUNS:
            if mu_f5 - f5_line >= _F5_MIN_MU_MARGIN:
                if max(away_fip_f5_composite, home_fip_f5_composite) >= _F5_MIN_WEAK_XFIP:
                    # Señal 2 — Gate IP reciente: bloquea OVER si algún pitcher
                    # raramente llega al inning 5 (riesgo de bullpen en la ventana F5)
                    _home_ip_ok = home_ip_recent_per is None or home_ip_recent_per >= _F5_MIN_IP_RECENT_OVER
                    _away_ip_ok = away_ip_recent_per is None or away_ip_recent_per >= _F5_MIN_IP_RECENT_OVER
                    if _home_ip_ok and _away_ip_ok:
                        p = _build_f5_pick("OVER", edge_over, p_over, fair_over)
                        picks.append(p)
                    else:
                        _short_pitcher = home_p.get("name") if not _home_ip_ok else away_p.get("name")
                        _ip_val = home_ip_recent_per if not _home_ip_ok else away_ip_recent_per
                        print(f"  ⏭ F5 OVER bloqueado — {_short_pitcher} promedia {_ip_val:.1f} IP/start reciente (<{_F5_MIN_IP_RECENT_OVER})")

    # F5 UNDER desactivado: 3W-4L (43% WR) — el OVER tiene 77% WR y es la fortaleza real.
    # Reactivar cuando haya muestra suficiente para recalibrar.
    if False and _F5_MIN_EDGE <= edge_under <= _F5_MAX_EDGE and p_under >= _F5_MIN_PROB:
        if f5_line - mu_f5 >= _F5_MIN_MU_MARGIN:
            if min(away_fip_f5_composite, home_fip_f5_composite) <= _F5_ELITE_XFIP:
                p = _build_f5_pick("UNDER", edge_under, p_under, fair_under)
                picks.append(p)

    return picks


def analyze_all_f5_games(games: list[dict]) -> list[dict]:
    """Analiza todos los partidos en modo F5 y retorna los mejores picks."""
    all_picks: list[dict] = []
    for game in games:
        try:
            all_picks.extend(analyze_f5_game(game))
        except Exception:
            pass
    all_picks.sort(key=lambda p: p["edge"], reverse=True)
    return all_picks[:MAX_F5_PICKS]


# ── Pipeline completo (estándar) ─────────────────────────────────────────────

def analyze_all_games(games: list[dict]) -> list[dict]:
    """Analiza todos los partidos con el modelo base (sin agente específico)."""
    all_picks: list[dict] = []
    for game in games:
        try:
            picks = analyze_game(game)
            all_picks.extend(p for p in picks if p.get("bet_type") not in ("ML", "RL"))
        except Exception:
            pass
    all_picks.sort(key=lambda p: p["edge"], reverse=True)
    return all_picks[:MAX_PICKS]


def analyze_all_games_ml(games: list[dict]) -> list[dict]:
    """Picks de moneyline (ML) para todos los partidos del día."""
    all_picks: list[dict] = []
    for game in games:
        try:
            picks = analyze_game(game)
            all_picks.extend(p for p in picks if p.get("bet_type") == "ML")
        except Exception:
            pass
    all_picks.sort(key=lambda p: p["edge"], reverse=True)
    return all_picks[:_ML_MAX_PICKS]


# ── Análisis en vivo ──────────────────────────────────────────────────────────
_LIVE_MIN_INNING    = 2      # no analizar antes de que termine el 2do inning
_LIVE_MAX_INNING    = 8      # parar cuando queda menos de 1 inning
_LIVE_EARLY_EXIT_IP = 5.0    # abridor "retirado temprano" si salió con < 5 IP
_LIVE_MIN_EDGE      = 0.07   # edge mínimo para emitir un pick en vivo


def analyze_live_game(game: dict, live_state: dict, pitcher_state: dict,
                      live_line: float | None = None,
                      live_over_odds: int = -110,
                      live_under_odds: int = -110) -> dict:
    """
    Proyección de carreras restantes y pick en vivo.

    game:             dict de collector_mlb (datos de temporada)
    live_state:       de get_live_game_state()
    pitcher_state:    de get_live_current_pitchers()
    live_line:        línea total del bookie en vivo (ej. 8.0). Si None, solo proyección.
    live_over_odds:   cuota americana del OVER (default -110)
    live_under_odds:  cuota americana del UNDER (default -110)

    Returns dict con proyección + pick (si se pasó live_line).
    """
    inning    = live_state["inning"]
    half      = live_state["inning_half"]
    runs_home = live_state["runs_home"]
    runs_away = live_state["runs_away"]
    runs_total = runs_home + runs_away

    # Fracción de innings jugados (sin contar outs dentro del half-inning)
    if half == "top":
        innings_done = inning - 1          # visitante aún batea este inning
    else:
        innings_done = inning - 0.5        # local aún batea este inning

    innings_remaining_frac = max(0.0, (9.0 - innings_done) / 9.0)

    # ── Ajuste de FIP por cambio de abridor ──────────────────────────────────
    notes: list[str] = []

    home_p        = game.get("home_pitcher") or {}
    away_p        = game.get("away_pitcher") or {}
    home_fip_live = float(home_p.get("fip") or LEAGUE_AVG_FIP)
    away_fip_live = float(away_p.get("fip") or LEAGUE_AVG_FIP)

    for side in ("home", "away"):
        changed  = pitcher_state.get(f"{side}_starter_changed", False)
        start_ip = pitcher_state.get(f"{side}_starter_ip", 0.0)
        if changed and start_ip < _LIVE_EARLY_EXIT_IP:
            if side == "home":
                home_fip_live = LEAGUE_AVG_BULLPEN_ERA
            else:
                away_fip_live = LEAGUE_AVG_BULLPEN_ERA
            label = "local" if side == "home" else "visitante"
            notes.append(
                f"Abridor {label} retirado ({start_ip:.1f} IP) "
                f"→ bullpen (xFIP ajustado a {LEAGUE_AVG_BULLPEN_ERA:.2f})"
            )

    # ── Proyección de carreras restantes ─────────────────────────────────────
    home_ops    = float(game.get("home_ops") or LEAGUE_AVG_OPS)
    away_ops    = float(game.get("away_ops") or LEAGUE_AVG_OPS)
    park_factor = float(game.get("park_factor") or 1.0)
    ump_adj     = float(game.get("ump_run_adj") or 0.0)

    # expected_runs_team() proyecta para 9 innings completos
    exp_home_9 = expected_runs_team(home_ops, away_fip_live, park_factor, is_home=True)
    exp_away_9 = expected_runs_team(away_ops, home_fip_live, park_factor, is_home=False)
    mu_9       = exp_home_9 + exp_away_9 + ump_adj

    mu_remaining    = mu_9 * innings_remaining_frac
    sigma_remaining = RUN_TOTAL_SIGMA * (innings_remaining_frac ** 0.5)
    projected_final = runs_total + mu_remaining

    # ── Señales cualitativas (cuando no hay línea específica) ────────────────
    over_threshold  = projected_final - 0.5
    under_threshold = projected_final + 0.5
    signals: list[str] = [
        f"Si linea live < {over_threshold:.1f}  →  OVER edge",
        f"Si linea live > {under_threshold:.1f}  →  UNDER edge",
    ]

    # ── Pick cuantitativo (cuando el usuario pasa la línea live) ──────────────
    pick: dict | None = None
    if live_line is not None:
        remaining_needed = live_line - runs_total  # carreras que faltan para cubrir la línea

        # P(total_final > live_line) = P(runs_remaining > remaining_needed)
        p_over  = 1.0 - _norm_cdf(remaining_needed, mu_remaining, sigma_remaining)
        p_under = _norm_cdf(remaining_needed, mu_remaining, sigma_remaining)

        # Implied probability sin vig a partir de las cuotas americanas
        def _imp(odds: int) -> float:
            return (-odds) / (-odds + 100) if odds < 0 else 100 / (odds + 100)

        raw_over  = _imp(live_over_odds)
        raw_under = _imp(live_under_odds)
        vig       = raw_over + raw_under
        fair_over  = raw_over  / vig
        fair_under = raw_under / vig

        edge_over  = p_over  - fair_over
        edge_under = p_under - fair_under

        best_direction = None
        best_edge      = 0.0
        best_p         = 0.0
        best_fair      = 0.0
        best_odds      = 0

        if edge_over >= _LIVE_MIN_EDGE and edge_over >= edge_under:
            best_direction = "OVER"
            best_edge      = edge_over
            best_p         = p_over
            best_fair      = fair_over
            best_odds      = live_over_odds
        elif edge_under >= _LIVE_MIN_EDGE:
            best_direction = "UNDER"
            best_edge      = edge_under
            best_p         = p_under
            best_fair      = fair_under
            best_odds      = live_under_odds

        if best_direction:
            confianza = "ALTA" if best_edge >= 0.12 else ("MEDIA" if best_edge >= 0.09 else "BAJA")
            pick = {
                "direction":  best_direction,
                "line":       live_line,
                "odds":       best_odds,
                "p_model":    round(best_p,    3),
                "fair_mkt":   round(best_fair, 3),
                "edge":       round(best_edge, 3),
                "confianza":  confianza,
            }

    return {
        "game_pk":                game.get("game_pk"),
        "home_team":              game.get("home_team", ""),
        "away_team":              game.get("away_team", ""),
        "home_abbr":              game.get("home_abbr", ""),
        "away_abbr":              game.get("away_abbr", ""),
        "inning":                 inning,
        "inning_half":            half,
        "runs_home":              runs_home,
        "runs_away":              runs_away,
        "runs_total":             runs_total,
        "innings_remaining_frac": innings_remaining_frac,
        "mu_remaining":           round(mu_remaining,    2),
        "sigma_remaining":        round(sigma_remaining, 2),
        "projected_final":        round(projected_final, 1),
        "notes":                  notes,
        "signals":                signals,
        "pick":                   pick,
    }


def analyze_all_games_rl(games: list[dict]) -> list[dict]:
    """Picks de Run Line (spread ±1.5) para todos los partidos del día."""
    all_picks: list[dict] = []
    for game in games:
        try:
            picks = analyze_game(game)
            all_picks.extend(p for p in picks if p.get("bet_type") == "RL")
        except Exception:
            pass
    all_picks.sort(key=lambda p: p["edge"], reverse=True)
    return all_picks[:_RL_MAX_PICKS]


# ── Análisis por agente ───────────────────────────────────────────────────────

def _preprocess_game_for_agent(game: dict, cfg: dict) -> dict:
    """
    Devuelve una copia del game dict con OPS y xFIP re-ponderados
    según la filosofía del agente.

    Zeus   → 100% temporada
    Atena  → 70% reciente / 30% temporada
    Hades  → 60% temporada / 40% reciente (igual al modelo base)
    """
    import copy
    g = copy.deepcopy(game)

    ws  = cfg.get("ops_season_w", 0.60)  # peso temporada para OPS
    wr  = cfg.get("ops_recent_w", 0.40)  # peso reciente  para OPS
    wfs = cfg.get("fip_season_w", 0.60)  # peso temporada para xFIP
    wfr = cfg.get("fip_recent_w", 0.40)  # peso reciente  para xFIP

    for side in ("home", "away"):
        ops_s  = g.get(f"{side}_ops_season")  or g.get(f"{side}_ops") or LEAGUE_AVG_OPS
        ops_r  = g.get(f"{side}_ops_recent")  or ops_s
        has_r  = g.get(f"{side}_has_recent",  False)

        if has_r and wr > 0:
            g[f"{side}_ops"] = round(ws * ops_s + wr * ops_r, 4)
        else:
            g[f"{side}_ops"] = round(ops_s, 4)

        p = g.get(f"{side}_pitcher") or {}
        if not p:
            continue

        xfip_s = p.get("xfip")        or p.get("fip")        or LEAGUE_AVG_FIP
        xfip_r = p.get("xfip_recent") or p.get("fip_recent") or xfip_s
        has_rp = p.get("xfip_recent") is not None

        if has_rp and wfr > 0:
            new_blend = round(wfs * xfip_s + wfr * xfip_r, 4)
        else:
            new_blend = round(xfip_s, 4)

        p["xfip_blended"] = new_blend
        p["fip_blended"]  = new_blend

    return g


def _zeus_extra_filter(picks: list[dict], game: dict, cfg: dict) -> list[dict]:
    """
    Filtros extra de Zeus: ambos pitchers deben apoyar la dirección.
    OVER  → los dos xFIP > league_avg + pitcher_over_threshold  (débiles)
    UNDER → los dos xFIP < league_avg - pitcher_under_threshold (élite)
    """
    if not cfg.get("require_both_pitchers", False):
        return picks

    ov_delta = cfg.get("pitcher_over_threshold",  0.30)
    un_delta = cfg.get("pitcher_under_threshold",  0.50)

    home_p = game.get("home_pitcher") or {}
    away_p = game.get("away_pitcher") or {}
    home_xfip = home_p.get("xfip") or home_p.get("fip") or LEAGUE_AVG_FIP
    away_xfip = away_p.get("xfip") or away_p.get("fip") or LEAGUE_AVG_FIP

    home_weak  = home_xfip > LEAGUE_AVG_FIP + ov_delta
    away_weak  = away_xfip > LEAGUE_AVG_FIP + ov_delta
    home_elite = home_xfip < LEAGUE_AVG_FIP - un_delta
    away_elite = away_xfip < LEAGUE_AVG_FIP - un_delta

    result = []
    for p in picks:
        direction = p.get("direction", "")
        if direction == "OVER":
            # Zeus solo apuesta OVER si AMBOS pitchers son débiles
            if home_weak and away_weak:
                p["zeus_reason"] = (
                    f"Ambos pitchers débiles: local xFIP {home_xfip:.2f}, "
                    f"visitante xFIP {away_xfip:.2f} — ambos > {LEAGUE_AVG_FIP + ov_delta:.2f}"
                )
                result.append(p)
            else:
                p["zeus_skip"] = (
                    f"Zeus requiere AMBOS pitchers débiles (xFIP > {LEAGUE_AVG_FIP + ov_delta:.2f}) "
                    f"— local: {home_xfip:.2f} {'OK' if home_weak else 'NO'}, "
                    f"visitante: {away_xfip:.2f} {'OK' if away_weak else 'NO'}"
                )
        elif direction == "UNDER":
            # Zeus solo apuesta UNDER si AMBOS pitchers son élite
            if home_elite and away_elite:
                p["zeus_reason"] = (
                    f"Pitching de élite: local xFIP {home_xfip:.2f}, "
                    f"visitante xFIP {away_xfip:.2f} — ambos < {LEAGUE_AVG_FIP - un_delta:.2f}"
                )
                result.append(p)
            else:
                p["zeus_skip"] = (
                    f"Zeus requiere AMBOS pitchers élite (xFIP < {LEAGUE_AVG_FIP - un_delta:.2f}) "
                    f"— local: {home_xfip:.2f} {'OK' if home_elite else 'NO'}, "
                    f"visitante: {away_xfip:.2f} {'OK' if away_elite else 'NO'}"
                )
    return result


def _atena_extra_filter(picks: list[dict], game: dict, cfg: dict) -> list[dict]:
    """
    Filtro de Atena: forma reciente y temporada deben apuntar en la misma dirección.
    Si el equipo está en racha opuesta a la apuesta, Atena no apuesta.
    """
    if not cfg.get("require_momentum_alignment", False):
        return picks

    align_thr = cfg.get("momentum_align_threshold", 0.020)
    result = []

    for p in picks:
        direction = p.get("direction", "")
        home_ops_s = game.get("home_ops_season") or game.get("home_ops") or LEAGUE_AVG_OPS
        home_ops_r = game.get("home_ops_recent") or home_ops_s
        away_ops_s = game.get("away_ops_season") or game.get("away_ops") or LEAGUE_AVG_OPS
        away_ops_r = game.get("away_ops_recent") or away_ops_s

        # Para OVER: queremos que al menos un equipo esté en racha ofensiva positiva
        # (recent > season + threshold → equipo mejoró)
        home_hot = (home_ops_r - home_ops_s) >  align_thr
        away_hot = (away_ops_r - away_ops_s) >  align_thr
        home_cold= (home_ops_r - home_ops_s) < -align_thr
        away_cold= (away_ops_r - away_ops_s) < -align_thr

        if direction == "OVER":
            # Al menos un equipo en racha ofensiva Y ninguno en racha fría significativa
            if (home_hot or away_hot) and not (home_cold and away_cold):
                hot_who = []
                if home_hot: hot_who.append(game.get("home_team","Local").split()[-1])
                if away_hot: hot_who.append(game.get("away_team","Visita").split()[-1])
                p["atena_reason"] = (
                    f"Momentum ofensivo positivo: {', '.join(hot_who)} "
                    f"con OPS reciente superior a la temporada"
                )
                result.append(p)
            else:
                reason = "ningún equipo con momentum ofensivo positivo" if not (home_hot or away_hot) \
                         else "ambos equipos en racha ofensiva fría"
                p["atena_skip"] = f"Atena requiere momentum alineado al OVER - {reason}"
        elif direction == "UNDER":
            # Al menos un equipo ofensivamente frío Y ninguno en racha caliente
            if (home_cold or away_cold) and not (home_hot and away_hot):
                cold_who = []
                if home_cold: cold_who.append(game.get("home_team","Local").split()[-1])
                if away_cold: cold_who.append(game.get("away_team","Visita").split()[-1])
                p["atena_reason"] = (
                    f"Momentum ofensivo negativo: {', '.join(cold_who)} "
                    f"con OPS reciente inferior a la temporada"
                )
                result.append(p)
            else:
                p["atena_skip"] = "Atena requiere momentum alineado al UNDER - equipos sin racha fria reciente"
        else:
            result.append(p)

    return result


def analyze_game_for_agent(game: dict, agent_key: str) -> tuple[list[dict], list[dict]]:
    """
    Analiza un partido desde la perspectiva de un agente especifico.

    Retorna (picks_accepted, picks_rejected) donde rejected incluye el motivo
    de rechazo en pick["{agent}_skip"] para mostrar desacuerdo en el display.
    """
    from group_manager import AGENT_CONFIGS_MLB

    cfg      = AGENT_CONFIGS_MLB.get(agent_key, {})
    skip_key = f"{agent_key}_skip"

    # 1. Pre-procesar game dict (re-ponderar OPS y xFIP segun filosofia)
    g = _preprocess_game_for_agent(game, cfg)

    # 2. Analizar con umbrales del agente
    candidates = analyze_game(g, _agent_cfg=cfg)

    # 3. Si no hay candidatos, generar razon de rechazo sintetica para el display
    if not candidates:
        home_p  = g.get("home_pitcher") or {}
        away_p  = g.get("away_pitcher") or {}
        home_ip = home_p.get("ip", 0) or 0
        away_ip = away_p.get("ip", 0) or 0
        min_ip  = cfg.get("min_ip", 10.0)
        min_e   = cfg.get("min_edge", MIN_EDGE)

        if not home_p.get("name") or not away_p.get("name"):
            reason = "pitcher TBD - no apuesta sin abridor confirmado"
        elif home_ip < min_ip or away_ip < min_ip:
            low_side = "local" if home_ip < min_ip else "visitante"
            low_ip   = home_ip if home_ip < min_ip else away_ip
            reason   = (f"IP insuficiente ({low_side}: {low_ip:.0f} IP, "
                        f"requiere >= {min_ip:.0f})")
        else:
            reason = (f"sin edge suficiente (requiere >= {min_e:.0%} - "
                      f"mercado eficiente en este partido)")

        synthetic = {
            skip_key:    reason,
            "away_team": game.get("away_team", ""),
            "home_team": game.get("home_team", ""),
            "game_pk":   game.get("game_pk"),
        }
        return [], [synthetic]

    # 4. Aplicar filtros filosoficos adicionales
    if agent_key == "zeus":
        accepted = _zeus_extra_filter(candidates, g, cfg)
        rejected = [p for p in candidates if p not in accepted]
    elif agent_key == "atena":
        accepted = _atena_extra_filter(candidates, g, cfg)
        rejected = [p for p in candidates if p not in accepted]
    else:
        accepted = candidates
        rejected = []

    # 5. Marcar picks aceptados con agente
    for p in accepted:
        p["agent"]       = agent_key
        p["agent_stake"] = cfg.get("stake_pct", 0.01)

    return accepted, rejected


# ── Props: Strikeouts del pitcher ─────────────────────────────────────────────

def analyze_strikeout_props(game: dict, _agent_cfg: dict | None = None,
                            paper: bool = False) -> list[dict]:
    """
    Analiza props de strikeouts del abridor usando distribución Poisson.

    Modelo:
      k9_blended = 0.60 × K/9_season + 0.40 × K/9_recent  (si ≥3 starts recientes)
      lambda     = (k9_blended / 9) × ip_per_start × opp_k_rate_factor
      opp_k_rate_factor = team_k_pct / LEAGUE_AVG_K_RATE_BATTING

    Probabilidades:
      Línea X.5 (half) → P(OVER) = 1 - Poisson_CDF(floor, lambda)
      Línea X.0 (entera) → P(OVER) = P(K > X) = 1 - CDF(X, lambda); no push simple

    Retorna lista de picks (OVER/UNDER por pitcher).
    """
    _cfg        = _agent_cfg or {}
    min_edge    = _cfg.get("min_edge_k",     _MIN_EDGE_K_PROP)
    min_prob_ov = _cfg.get("min_prob_k_over",  _MIN_PROB_K_OVER)
    min_prob_un = _cfg.get("min_prob_k_under", _MIN_PROB_K_UNDER)
    picks       = []

    if _rain_blocked(game, f"K-props {game.get('away_team','?')}@{game.get('home_team','?')}"):
        return []

    for side in ("away", "home"):
        opp     = "home" if side == "away" else "away"
        pitcher = game.get(f"{side}_pitcher") or {}
        # Nunca apostar K-props sin pitcher confirmado: si el abridor cambia,
        # la lambda es del pitcher equivocado (el mercado lo sabe antes que nosotros).
        if pitcher.get("name") in (None, "", "TBD"):
            continue
        if game.get(f"{side}_pitcher_on_il"):
            continue

        prop = game.get(f"{side}_k_prop")
        if not prop or prop.get("line") is None:
            continue

        # ── REGLA DE ORO del nicho: lineup rival confirmado + umpire asignado ──
        # Sin esas dos señales, nuestra ventana de descuido no existe todavía.
        # En modo PAPER (validación) la saltamos: queremos red amplia para medir
        # calibración, no apostar — usa team K% como fallback si no hay lineup.
        _lineup_ok = bool(game.get(f"{opp}_lineup_k_used"))
        _ump_name  = game.get("hp_umpire") or ""
        if not paper and not (_lineup_ok and _ump_name):
            _falta = []
            if not _lineup_ok:
                _falta.append("lineup rival sin confirmar")
            if not _ump_name:
                _falta.append("umpire sin asignar")
            print(f"  ⏭ K-prop {pitcher.get('name', '?')}: {' + '.join(_falta)} — "
                  f"esperar a la ventana del nicho")
            continue

        line       = float(prop["line"])
        over_odds  = prop.get("over_odds")
        under_odds = prop.get("under_odds")
        if not over_odds or not under_odds:
            continue

        # Señal 1: K-rate del LINEUP CONFIRMADO (ponderado por PA), no del equipo
        opp_k_pct  = (game.get(f"{opp}_lineup_k_pct")
                      or game.get(f"{opp}_k_pct")
                      or LEAGUE_AVG_K_RATE_BATTING)
        opp_factor = opp_k_pct / LEAGUE_AVG_K_RATE_BATTING

        # K/9 blended — base del modelo (talento de temporada + reciente).
        # Peso 70/30 conservador: la recencia en el K/9 sobreinfló el edge Cameron.
        k9s = pitcher.get("k9") or 0.0
        k9r = pitcher.get("k9_recent") or k9s
        n_r = pitcher.get("n_starts_recent") or 0
        k9_blended = (0.70 * k9s + 0.30 * k9r) if (n_r >= 3 and k9r) else k9s
        if k9_blended <= 0:
            continue

        # Corrector de whiff%: regresión del K/9 hacia el stuff subyacente.
        whiff = pitcher.get("whiff_pct")
        whiff_corr = 0.0
        if whiff:
            whiff_corr = _WHIFF_CORR_WEIGHT * (whiff / _LEAGUE_AVG_WHIFF - 1.0)
            whiff_corr = max(-_WHIFF_CORR_CAP, min(_WHIFF_CORR_CAP, whiff_corr))
            k9_blended *= (1.0 + whiff_corr)

        # Longitud esperada de la salida — anclaje 70% temporada / 30% reciente.
        ip_season = pitcher.get("ip_per_start") or 5.5
        ip_recent_total = pitcher.get("ip_recent")
        if ip_recent_total and n_r >= 3:
            ip_recent_per = ip_recent_total / n_r
            ip_start = 0.70 * ip_season + 0.30 * ip_recent_per
        else:
            ip_start = ip_season
        ip_start = min(ip_start, 7.0)  # cap a 7 IP

        # Lambda base: proyección de temporada ajustada por calidad del oponente
        lam_season = (k9_blended / 9.0) * ip_start * opp_factor

        # Señal de momentum — promedio de Ks REALES en las últimas 5 salidas.
        # A diferencia del k9_recent (normalizado por IP), k_avg_recent captura
        # directamente "¿cuántos Ks está sacando este pitcher en la práctica?"
        # sin que salidas largas/cortas distorsionen la tasa. Se usa como anchor
        # de momentum en paralelo al modelo de temporada: si un pitcher viene
        # caliente (Abbott: 7, 8, 6 Ks) o frío (Gallen: 3, 2, 4 Ks), ese patrón
        # pesa al 35% en la lambda final. El opp_factor ajusta por el rival de hoy.
        _K_MOMENTUM_WEIGHT = 0.35
        k_avg_recent = pitcher.get("k_avg_recent")
        if k_avg_recent is not None and n_r >= 3:
            lam_momentum = k_avg_recent * opp_factor
            lam = (1.0 - _K_MOMENTUM_WEIGHT) * lam_season + _K_MOMENTUM_WEIGHT * lam_momentum
        else:
            lam = lam_season

        # Splits K% L/R del pitcher × fracción zurda del lineup confirmado:
        # el platoon de Ks. Factor = mezcla de K%vsL/K%vsR ponderada por la
        # composición real del lineup de hoy, relativa al K% total del pitcher.
        split_factor = 1.0
        k_l, k_r = pitcher.get("k_pct_vs_l"), pitcher.get("k_pct_vs_r")
        bf_l, bf_r = pitcher.get("bf_l", 0), pitcher.get("bf_r", 0)
        pct_l_opp  = game.get(f"{opp}_lineup_pct_l")
        if (k_l and k_r and pct_l_opp is not None
                and bf_l >= _KSPLIT_MIN_BF and bf_r >= _KSPLIT_MIN_BF):
            k_total = (k_l * bf_l + k_r * bf_r) / (bf_l + bf_r)
            if k_total > 0:
                k_today = pct_l_opp * k_l + (1.0 - pct_l_opp) * k_r
                split_factor = max(1.0 - _KSPLIT_FACTOR_CAP,
                                   min(1.0 + _KSPLIT_FACTOR_CAP, k_today / k_total))
                lam *= split_factor

        # Temperatura a la hora del juego: frío = aire denso = más movimiento = más Ks
        temp_factor = 1.0
        _w = game.get("weather") or {}
        _temp = _w.get("temp_f")
        if _temp is not None:
            temp_factor = 1.0 + max(-_TEMP_K_CAP, min(_TEMP_K_CAP,
                          (72.0 - _temp) / 10.0 * _TEMP_K_PER_10F))
            lam *= temp_factor

        # Composición del lineup (no solo la media): bateadores high-K (K/AB≥28%)
        # son más explotables por pitchers de stuff alto de forma NO lineal — la
        # media ponderada del lineup lo subestima en lineups bimodales.
        # Heurística conservadora a validar con CLV: solo aplica con pitcher de
        # K/9 ≥ 9.5, +2.5% de lambda por high-K bat por encima de 3, cap +10%.
        n_high_k = int(game.get(f"{opp}_lineup_high_k") or 0)
        stack_adj = 0.0
        if k9_blended >= 9.5 and n_high_k > 3:
            stack_adj = min(0.10, 0.025 * (n_high_k - 3))
            lam *= (1.0 + stack_adj)

        # Señal 2: umpire asignado — zona amplia (ump_run_adj < 0) = más Ks.
        # Mapeo: -0.5 carreras de zona ≈ +0.5 K para el abridor.
        ump_run_adj = float(game.get("ump_run_adj") or 0.0)
        ump_k_adj   = max(-_UMP_K_ADJ_CAP, min(_UMP_K_ADJ_CAP,
                          -ump_run_adj * _UMP_K_ADJ_PER_RUN))
        # Monte Carlo: lam aquí tiene k9_blended × ip_start × opp_factor
        # y todos los multiplicadores (whiff, momentum, split, temp, stack)
        # pero NO ump_k_adj (aditivo). Se pasan separados al MC.
        p_over, p_under, lam, _lam_std = _mc_k_prob(
            k9_blended = lam / ip_start * 9.0,  # K/9 efectivo con todos los ajustes
            ip_mean    = ip_start,
            opp_factor = 1.0,                   # ya incorporado en k9_blended efectivo
            ump_k_adj  = ump_k_adj,
            line       = line,
        )
        lam = max(lam, 0.1)

        # SIN Platt en el niche: la recalibración se entrenó sobre K-props
        # genéricos (sin las 8 señales) y suprime el edge ganado. Las señales
        # confirmadas son la validación; recalibrar con datos del régimen viejo
        # estrangula el producto. Se re-evaluará con datos del niche vía CLV.

        # Market fair odds
        fair_over, fair_under = remove_vig_two_way(
            american_to_prob(int(over_odds)),
            american_to_prob(int(under_odds)),
        )

        # ── ANCLA AL MERCADO (opción 2: fuerte hasta que el dato valide) ──────
        # Encoge la prob del modelo hacia la justa del mercado. Un edge crudo de
        # 15% (Cameron) con ancla 0.6 baja a ~6% — tamea el optimismo sin
        # estrangular. NO se aplica en paper (ahí estudiamos la prob cruda).
        # El ancla se AFLOJA cuando paper_validate/CLV demuestren que el edge
        # crudo predice resultados; sube cuando no. Por ahora: conservador.
        if not paper:
            p_over  = (1 - _K_MARKET_ANCHOR) * p_over  + _K_MARKET_ANCHOR * fair_over
            p_under = (1 - _K_MARKET_ANCHOR) * p_under + _K_MARKET_ANCHOR * fair_under

        edge_over  = round(p_over  - fair_over,  4)
        edge_under = round(p_under - fair_under, 4)

        def _build(direction: str, edge: float, prob: float, fair: float, odds: int) -> dict:
            # Confianza del nicho: con las 8 señales confirmadas el edge es GANADO.
            # ALTA desde 5%, MEDIA 3-5%, FLACA debajo. SOSPECHA solo en edges
            # absurdos (≥18% = error de datos), no en el edge que el niche produce.
            conf = _prop_confidence(edge, suspect=_K_EDGE_SUSPECT, alta=0.05, media=0.03)
            return {
                "bet_type":      "K_PROP",
                "direction":     direction,
                "pitcher":       pitcher["name"],
                "pitcher_team":  game.get(f"{side}_team", game.get(f"{side}_team", "")),
                "opp_team":      game.get(f"{opp}_team", ""),
                "side":          side,
                "line":          line,
                "odds":          odds,
                "our_prob":      round(prob, 4),
                "fair_market":   round(fair, 4),
                "edge":          round(edge, 4),
                "confianza":     conf,
                "lambda":        round(lam, 2),
                "k9_blended":    round(k9_blended, 1),
                "k9_season":     round(k9s, 1),
                "k9_recent":     round(k9r, 1),
                "ip_per_start":  round(ip_start, 1),
                "opp_k_pct":     round(opp_k_pct, 3),
                "opp_factor":    round(opp_factor, 3),
                # Señales del nicho (jun-2026) — para tracking/validación con CLV
                "whiff_pct":     whiff,
                "whiff_corr":    round(whiff_corr, 3),
                "split_factor":  round(split_factor, 3),
                "temp_factor":   round(temp_factor, 3),
                "stack_adj":     round(stack_adj, 3),
                "n_high_k":      n_high_k,
                "ump_k_adj":     round(ump_k_adj, 2),
                "home_team":     game.get("home_team", ""),
                "away_team":     game.get("away_team", ""),
                "game_time":     game.get("game_time"),
                "commence_iso":  game.get("commence_iso"),
                "game_pk":       game.get("game_pk"),
                "hp_umpire":     game.get("hp_umpire"),
                "k_avg_recent":  pitcher.get("k_avg_recent"),
            }

        # SIN dead-zone de cuotas en el niche: el bucket -149/-110 perdía en el
        # modelo GENÉRICO (props eficientemente pricados). En el niche apostamos
        # la línea virgen antes de que la casa la afine, con señales confirmadas
        # — el juice estándar (-110/-130) es donde vive el grueso de los props y
        # bloquearlo dejaba al niche sin balas. La validación es el CLV.
        # Modo PAPER: red amplia (umbral 0%) pero solo el lado con edge positivo.
        # Guardar ambos lados produce WIN+LOSS garantizado — sin valor de calibración.
        if paper:
            if edge_over >= 0:
                picks.append(_build("OVER",  edge_over,  p_over,  fair_over,  int(over_odds)))
            elif edge_under >= 0:
                picks.append(_build("UNDER", edge_under, p_under, fair_under, int(under_odds)))
            continue

        if (min_edge <= edge_over <= _MAX_EDGE_K_PROP and p_over >= min_prob_ov):
            if ip_start < _K_OVER_MIN_IP_START:
                print(f"  ⏭ K-prop OVER {pitcher.get('name','?')}: correa corta "
                      f"({ip_start:.1f} IP/start < {_K_OVER_MIN_IP_START}) — "
                      f"riesgo de salida corta que el Poisson no ve")
            else:
                picks.append(_build("OVER", edge_over, p_over, fair_over, int(over_odds)))

        if (min_edge <= edge_under <= _MAX_EDGE_K_PROP and p_under >= min_prob_un):
            picks.append(_build("UNDER", edge_under, p_under, fair_under, int(under_odds)))

    return sorted(picks, key=lambda p: p["edge"], reverse=True)


# ── Props: Total Bases del bateador ──────────────────────────────────────────

LEAGUE_AVG_AB_PER_GAME   = 3.66   # AB/G promedio de un bateador MLB
# TB props = modelo genérico (xSLG × AB × pitcher × park), SIN las señales del
# nicho. Rebalanceo jun-2026: el nicho es pitchers (K-props + F5); los TB no
# deben dominar el menú diario. Cap 5→3 y edge mínimo 6%→7%.
_MIN_EDGE_TB_PROP        = 0.08   # subido: TB es modelo genérico de RELLENO, no el niche.
                                  # Solo el TB más fuerte (≥8%) merece aparecer; es secundario.
_MIN_PROB_TB_OVER        = 0.55
_MIN_PROB_TB_UNDER       = 0.58
_MAX_EDGE_TB_PROP        = 0.12   # techo de props (>12% = error de datos, no oportunidad)
_MIN_PA_TB               = 100    # PA mínimos para confiar en xSLG (subido de 30 — muestra chica = ruido)
_MAX_TB_PROPS            = 2      # cap bajo: TB es relleno secundario, no el plato principal


def analyze_tb_props(game: dict, _agent_cfg: dict | None = None,
                     paper: bool = False) -> list[dict]:
    """
    Analiza props de Total Bases por bateador usando distribución Poisson.

    Modelo:
      xslg_blended = 0.65 × xslg_savant + 0.35 × slg_actual
      lambda       = xslg_blended × ab_per_game × pitcher_quality_factor × park_factor
      pitcher_quality_factor = pitcher_xwoba_allowed / LEAGUE_AVG_XWOBA_BATTING
        (clamped 0.85–1.15)

    Retorna lista de picks ordenados por edge desc.
    """
    _cfg        = _agent_cfg or {}
    min_edge    = _cfg.get("min_edge_tb",       _MIN_EDGE_TB_PROP)
    min_prob_ov = _cfg.get("min_prob_tb_over",  _MIN_PROB_TB_OVER)
    min_prob_un = _cfg.get("min_prob_tb_under", _MIN_PROB_TB_UNDER)
    park_factor = game.get("park_factor", 1.0) or 1.0
    picks       = []

    # Bloqueo IL: si algún pitcher probable está en IL, el pit_factor es inválido
    if game.get("home_pitcher_on_il") or game.get("away_pitcher_on_il"):
        return []

    if _rain_blocked(game, f"TB-props {game.get('away_team','?')}@{game.get('home_team','?')}"):
        return []

    for side in ("home", "away"):
        opp     = "away" if side == "home" else "home"

        # Regla de oro (= K-props): solo apostar con el LINEUP CONFIRMADO del
        # bateador. En paper mode se omite para red amplia de validación.
        if not paper and not (game.get(f"{side}_lineup_ids") or []):
            print(f"  ⏭ TB-props {game.get(f'{side}_team','?')}: lineup sin confirmar "
                  f"— esperar a la ventana del nicho")
            continue

        tb_lineup = game.get(f"{side}_tb_lineup") or []
        opp_pitcher = game.get(f"{opp}_pitcher") or {}

        # Factor de calidad del pitcher oponente (xwOBA permitido / liga avg)
        pit_xwoba   = opp_pitcher.get("xwoba") or LEAGUE_AVG_XWOBA_BATTING
        pit_factor  = max(0.85, min(1.15, pit_xwoba / LEAGUE_AVG_XWOBA_BATTING))

        for entry in tb_lineup:
            prop = entry.get("prop")
            if not prop or prop.get("line") is None:
                continue
            if entry.get("pa", 0) < _MIN_PA_TB:
                continue

            over_odds  = prop.get("over_odds")
            under_odds = prop.get("under_odds")
            if not over_odds or not under_odds:
                continue

            line    = float(prop["line"])
            xslg    = entry.get("xslg") or entry.get("slg", 0.400)
            slg_act = entry.get("slg", xslg)
            xslg_blended = round(0.65 * xslg + 0.35 * slg_act, 4)

            ab_per_game = entry.get("ab_per_game") or LEAGUE_AVG_AB_PER_GAME

            # Lambda base: proyección de temporada
            lam_season = xslg_blended * ab_per_game * pit_factor * park_factor

            # Momentum: TB reales promedio en últimas 7 salidas del bateador.
            # Si lleva una racha fría (ej: 0 TB en 8 días), lam_momentum ≈ 0
            # y arrastra la lambda final hacia abajo. Racha caliente → sube.
            # Ajustamos por pit_factor y park porque el momentum crudo fue
            # contra distintos rivales y estadios.
            _TB_MOMENTUM_WEIGHT = 0.35
            tb_avg_recent = entry.get("tb_avg_recent")
            tb_n_games    = entry.get("tb_n_games") or 0
            if tb_avg_recent is not None and tb_n_games >= 3:
                lam_momentum = tb_avg_recent * pit_factor * park_factor
                lam = (1.0 - _TB_MOMENTUM_WEIGHT) * lam_season + _TB_MOMENTUM_WEIGHT * lam_momentum
            else:
                lam = lam_season
            lam = max(lam, 0.05)

            # Probabilidades Poisson
            floor_line = int(line)
            is_half    = (line % 1 != 0)
            if is_half:
                p_over  = 1.0 - _poisson_cdf(floor_line, lam)
                p_under = _poisson_cdf(floor_line, lam)
            else:
                p_over  = 1.0 - _poisson_cdf(floor_line, lam)
                p_under = _poisson_cdf(floor_line - 1, lam)

            # Recalibración Platt por lado (historial propio, pool mlb)
            from calibration import calibrate
            p_over  = calibrate(p_over,  "mlb", "TB_PROP")
            p_under = calibrate(p_under, "mlb", "TB_PROP")

            # Market fair odds
            fair_over, fair_under = remove_vig_two_way(
                american_to_prob(int(over_odds)),
                american_to_prob(int(under_odds)),
            )
            edge_over  = round(p_over  - fair_over,  4)
            edge_under = round(p_under - fair_under, 4)

            def _build(direction: str, edge: float, prob: float, fair: float, odds: int) -> dict:
                # TB = modelo genérico (sin señales de nicho): umbrales más exigentes
                # que K-props. SOSPECHA ≥12%, ALTA ≥10%, MEDIA ≥8.5%, FLACA debajo
                # (el mercado casi coincide → seguro pero valor delgado, caso Busch/Suzuki).
                conf = _prop_confidence(edge, suspect=0.12, alta=0.10, media=0.085)
                return {
                    "bet_type":      "TB_PROP",
                    "direction":     direction,
                    "player":        entry["name"],
                    "player_team":   game.get(f"{side}_team", ""),
                    "opp_team":      game.get(f"{opp}_team", ""),
                    "side":          side,
                    "line":          line,
                    "odds":          odds,
                    "our_prob":      round(prob, 4),
                    "fair_market":   round(fair, 4),
                    "edge":          round(edge, 4),
                    "confianza":     conf,
                    "lambda":        round(lam, 2),
                    "xslg":         round(xslg, 3),
                    "slg_actual":    round(slg_act, 3),
                    "xslg_blended":  round(xslg_blended, 3),
                    "ab_per_game":   round(ab_per_game, 2),
                    "pit_factor":    round(pit_factor, 3),
                    "park_factor":   round(park_factor, 3),
                    "pa":            entry.get("pa", 0),
                    "tb_avg_recent": entry.get("tb_avg_recent"),
                    "tb_n_games":    entry.get("tb_n_games"),
                    "opp_pitcher":   opp_pitcher.get("name", ""),
                    "home_team":     game.get("home_team", ""),
                    "away_team":     game.get("away_team", ""),
                    "game_time":     game.get("game_time"),
                    "commence_iso":  game.get("commence_iso"),
                    "game_pk":       game.get("game_pk"),
                }

            if (min_edge <= edge_over <= _MAX_EDGE_TB_PROP and p_over >= min_prob_ov
                    and not (_PROP_DEAD_ODDS_LO <= int(over_odds) <= _PROP_DEAD_ODDS_HI)):
                picks.append(_build("OVER", edge_over, p_over, fair_over, int(over_odds)))

            if (min_edge <= edge_under <= _MAX_EDGE_TB_PROP and p_under >= min_prob_un
                    and not (_PROP_DEAD_ODDS_LO <= int(under_odds) <= _PROP_DEAD_ODDS_HI)):
                picks.append(_build("UNDER", edge_under, p_under, fair_under, int(under_odds)))

    return sorted(picks, key=lambda p: p["edge"], reverse=True)


_MIN_EDGE_TEAM_TOTAL  = 0.06   # edge mínimo (tracking mode — más permisivo que apuesta)
_MAX_EDGE_TEAM_TOTAL  = 0.12   # techo anti-error-de-datos (>12% casi siempre error de modelo)
_MIN_PROB_TT_OVER     = 0.52
_MIN_PROB_TT_UNDER    = 0.55
_TT_MARKET_ANCHOR     = 0.50   # mercado no probado aún → ancla fuerte al 50/50


def analyze_team_totals(game: dict, paper: bool = False) -> list[dict]:
    """
    Tracking de team run totals (carreras por equipo, juego completo).
    Señal principal: xFIP del pitcher rival + K% del lineup + OPS del equipo.
    Siempre tracking-only (stake=0) hasta validar calibración.
    """
    picks = []

    if _rain_blocked(game, f"Team-total {game.get('away_team','?')}@{game.get('home_team','?')}"):
        return []

    home_p = game.get("home_pitcher") or {}
    away_p = game.get("away_pitcher") or {}
    if not home_p.get("name") or not away_p.get("name"):
        return []

    # Mismo gate que F5: sin lineup confirmado de ningún equipo, los stats son solo promedio
    # de temporada y el edge calculado es ruido. Requiere al menos un lineup real.
    if not paper and not (game.get("home_lineup_k_used") or game.get("away_lineup_k_used")):
        return []

    home_tt = game.get("home_team_total")
    away_tt = game.get("away_team_total")
    if not home_tt and not away_tt:
        return []

    park_factor  = game.get("park_factor", 1.0) or 1.0
    home_ops     = game.get("home_ops") or game.get("home_ops_season") or 0.720
    away_ops     = game.get("away_ops") or game.get("away_ops_season") or 0.720
    home_ip  = home_p.get("ip_per_start") or home_p.get("ip_recent_per") or 5.5
    away_ip  = away_p.get("ip_per_start") or away_p.get("ip_recent_per") or 5.5
    home_fip = _effective_fip(home_p.get("fip_blended") or home_p.get("xfip_season") or 4.20, home_ip)
    away_fip = _effective_fip(away_p.get("fip_blended") or away_p.get("xfip_season") or 4.20, away_ip)

    exp_home = expected_runs_team(home_ops, away_fip, park_factor, is_home=True)
    exp_away = expected_runs_team(away_ops, home_fip, park_factor, is_home=False)

    # Ajuste K% del lineup (mismo mecanismo que F5)
    home_lineup_k_used = game.get("home_lineup_k_used", False)
    away_lineup_k_used = game.get("away_lineup_k_used", False)
    home_k_pct = game.get("home_lineup_k_pct")
    away_k_pct = game.get("away_lineup_k_pct")
    if home_lineup_k_used and home_k_pct:
        delta = (home_k_pct - LEAGUE_AVG_K_RATE_BATTING) / LEAGUE_AVG_K_RATE_BATTING
        adj = max(-0.08, min(0.08, -delta * 0.40))
        exp_home = round(exp_home * (1 + adj), 2)
    if away_lineup_k_used and away_k_pct:
        delta = (away_k_pct - LEAGUE_AVG_K_RATE_BATTING) / LEAGUE_AVG_K_RATE_BATTING
        adj = max(-0.08, min(0.08, -delta * 0.40))
        exp_away = round(exp_away * (1 + adj), 2)

    home_weak      = game.get("home_lineup_weak", 0)  # huecos OPS<0.650 en lineup home
    away_weak      = game.get("away_lineup_weak", 0)  # huecos OPS<0.650 en lineup away
    _MAX_WEAK_OVER = 2                                 # máximo huecos permitidos para OVER

    def _eval_side(tt: dict, exp_runs: float, team: str, opp_pitcher_name: str,
                   lineup_weak: int = 0) -> list[dict]:
        if not tt or tt.get("line") is None:
            return []
        line       = float(tt["line"])
        over_odds  = tt.get("over_odds")
        under_odds = tt.get("under_odds")
        if not over_odds or not under_odds:
            return []

        lam = max(exp_runs, 0.1)
        floor_line = int(line)
        is_half    = (line % 1 != 0)
        if is_half:
            p_over  = 1.0 - _poisson_cdf(floor_line, lam)
            p_under = _poisson_cdf(floor_line, lam)
        else:
            p_over  = 1.0 - _poisson_cdf(floor_line, lam)
            p_under = _poisson_cdf(floor_line - 1, lam)

        from odds_utils import power_devig, american_to_raw_prob
        impl_over  = american_to_raw_prob(over_odds)
        impl_under = american_to_raw_prob(under_odds)
        fair_over, fair_under = power_devig(impl_over, impl_under)

        side_picks = []
        for direction, p_model, p_fair, odds_val, min_prob in [
            ("OVER",  p_over,  fair_over,  over_odds,  _MIN_PROB_TT_OVER),
            ("UNDER", p_under, fair_under, under_odds, _MIN_PROB_TT_UNDER),
        ]:
            # OVER requiere lineup sin demasiados huecos (OPS < 0.650) — evita OPS inflado por top
            if direction == "OVER" and lineup_weak >= _MAX_WEAK_OVER:
                continue
            edge = p_model - p_fair
            if _MIN_EDGE_TEAM_TOTAL <= edge <= _MAX_EDGE_TEAM_TOTAL and p_model >= min_prob:
                confianza = "ALTA" if edge >= 0.09 else ("MEDIA" if edge >= 0.07 else "BAJA")
                side_picks.append({
                    "type":           "TEAM_TOTAL",
                    "team":           team,
                    "opp_pitcher":    opp_pitcher_name,
                    "direction":      direction,
                    "line":           line,
                    "odds":           int(odds_val),
                    "edge":           round(edge, 4),
                    "our_prob":       round(p_model, 4),
                    "fair_prob":      round(p_fair, 4),
                    "fair_market":    round(p_fair, 4),
                    "exp_runs":       round(exp_runs, 2),
                    "confianza":      confianza,
                    "away_team":      game.get("away_team", ""),
                    "home_team":      game.get("home_team", ""),
                    "game_time":      game.get("game_time", ""),
                    "commence_iso":   game.get("commence_iso", ""),
                    "tracking_only":  True,
                })
        return side_picks

    home_pitcher_name = home_p.get("name", "?")
    away_pitcher_name = away_p.get("name", "?")
    picks += _eval_side(home_tt, exp_home, game.get("home_team", ""), away_pitcher_name, home_weak)
    picks += _eval_side(away_tt, exp_away, game.get("away_team", ""), home_pitcher_name, away_weak)
    return picks


def analyze_all_games_per_agent(games: list[dict]) -> dict:
    """
    Corre los 3 agentes sobre todos los partidos del día.

    Retorna dict con:
        "zeus":  {"picks": [...], "skipped_reasons": {game_key: reason}}
        "atena": {...}
        "hades": {...}
        "consensus": lista de (game_key, direction, n_agents_agreeing, picks_by_agent)
    """
    from group_manager import AGENT_CONFIGS_MLB, MEMBERS

    results: dict = {key: {"picks": [], "skipped": []} for key in MEMBERS}

    for game in games:
        for agent_key in MEMBERS:
            cfg = AGENT_CONFIGS_MLB.get(agent_key, {})
            try:
                accepted, rejected = analyze_game_for_agent(game, agent_key)
                # Respetar max_picks_per_day
                max_p = cfg.get("max_picks_per_day", 999)
                already = len(results[agent_key]["picks"])
                room    = max_p - already
                if room > 0:
                    results[agent_key]["picks"].extend(accepted[:room])
                results[agent_key]["skipped"].extend(rejected)
            except Exception:
                pass

    # Ordenar por edge desc dentro de cada agente
    for agent_key in MEMBERS:
        results[agent_key]["picks"].sort(key=lambda p: p["edge"], reverse=True)

    # Calcular consenso: para cada (juego, dirección) cuántos agentes coinciden
    from collections import defaultdict
    consensus_map: dict = defaultdict(list)
    for agent_key in MEMBERS:
        for p in results[agent_key]["picks"]:
            key = f"{p.get('away_team','')}@{p.get('home_team','')}|{p.get('direction','')}|{p.get('line','')}"
            consensus_map[key].append(agent_key)

    results["consensus"] = dict(consensus_map)
    return results


# ── Narrativa humana del pick ─────────────────────────────────────────────────

def _narrative_mlb(pick: dict) -> str:
    """
    Genera un párrafo narrativo en español (2-4 oraciones) explicando
    en lenguaje simple por qué el modelo recomienda este pick.
    Complementa los reasons técnicos, no los reemplaza.
    """
    direction   = pick["direction"]
    line        = pick["line"]
    mu          = pick["mu_total"]
    edge        = pick["edge"]
    diff        = mu - line          # positivo = hacia OVER, negativo = hacia UNDER

    # Nombres cortos de equipos (última palabra, ej. "Angels", "Rockies")
    home_short = pick.get("home_team", "Local").split()[-1]
    away_short = pick.get("away_team", "Visita").split()[-1]

    home_p_name = pick.get("home_pitcher") or "el pitcher local"
    away_p_name = pick.get("away_pitcher") or "el pitcher visitante"

    # Stats de pitchers
    home_fip      = pick.get("home_fip")   or LEAGUE_AVG_FIP
    away_fip      = pick.get("away_fip")   or LEAGUE_AVG_FIP
    home_eff      = pick.get("home_eff_fip") or home_fip
    away_eff      = pick.get("away_eff_fip") or away_fip
    home_bull     = pick.get("home_team_era")
    away_bull     = pick.get("away_team_era")
    home_ips      = pick.get("home_ip_per_start") or 5.5
    away_ips      = pick.get("away_ip_per_start") or 5.5

    # OPS ofensivos
    home_ops      = pick.get("home_ops") or LEAGUE_AVG_OPS
    away_ops      = pick.get("away_ops") or LEAGUE_AVG_OPS

    # Ajustes secundarios
    weather_adj   = pick.get("weather_adj", 0) or 0
    weather_desc  = pick.get("weather_desc", "") or ""
    h2h_home      = pick.get("home_h2h_adj", 0) or 0
    h2h_away      = pick.get("away_h2h_adj", 0) or 0
    h2h_total     = h2h_home + h2h_away
    ump_adj       = pick.get("ump_run_adj", 0) or 0
    ump_name      = pick.get("hp_umpire") or ""
    home_lineup   = pick.get("home_lineup_used", False)
    away_lineup   = pick.get("away_lineup_used", False)
    park_factor   = pick.get("park_factor", 1.0) or 1.0

    # Umbrales para calificar pitchers
    _WEAK   = LEAGUE_AVG_FIP + 0.50   # xFIP "débil" → favorece carreras
    _STRONG = LEAGUE_AVG_FIP - 0.75   # xFIP "élite" → suprime carreras

    home_weak   = home_fip > _WEAK
    away_weak   = away_fip > _WEAK
    home_strong = home_fip < _STRONG
    away_strong = away_fip < _STRONG

    parts: list[str] = []

    # ── Oración 1: Driver principal (pitching vs ofensiva) ────────────────────
    if direction == "OVER":
        if home_weak and away_weak:
            parts.append(
                f"Los dos abridores llegan por encima del promedio de liga en xFIP "
                f"({home_p_name}: {home_fip:.2f}, {away_p_name}: {away_fip:.2f}), "
                f"lo que favorece ambas ofensivas."
            )
        elif home_weak and not away_weak:
            parts.append(
                f"{home_p_name} tiene xFIP de {home_fip:.2f} — por encima del promedio — "
                f"y el visitante {away_short} tiene ventaja clara al bate."
            )
        elif away_weak and not home_weak:
            parts.append(
                f"{away_p_name} llega con xFIP de {away_fip:.2f}, "
                f"por encima del promedio de liga, "
                f"dándole ventaja ofensiva al local {home_short}."
            )
        elif home_eff > LEAGUE_AVG_FIP + 0.2 or away_eff > LEAGUE_AVG_FIP + 0.2:
            # El bullpen inclina la balanza
            bull_side = home_short if (home_eff > away_eff) else away_short
            parts.append(
                f"El bullpen de {bull_side} arrastra el FIP efectivo del partido hacia arriba, "
                f"abriendo la puerta para más carreras de las que los abridores sugieren."
            )
        else:
            parts.append(
                f"El modelo proyecta {mu:.1f} carreras sobre una línea de {line} "
                f"a pesar de pitching competente en ambos lados."
            )
    else:  # UNDER
        if home_strong and away_strong:
            parts.append(
                f"Élite de abridores: {home_p_name} (xFIP {home_fip:.2f}) "
                f"y {away_p_name} (xFIP {away_fip:.2f}) están muy por debajo del promedio de liga, "
                f"proyectando una tarde de pocas carreras."
            )
        elif home_strong and not away_strong:
            parts.append(
                f"{home_p_name} viene en excelente forma (xFIP {home_fip:.2f}) "
                f"y debería suprimir las carreras del visitante {away_short}."
            )
        elif away_strong and not home_strong:
            parts.append(
                f"{away_p_name} muestra un xFIP de {away_fip:.2f} — "
                f"el local {home_short} tendrá difícil producir carreras."
            )
        elif home_ops < LEAGUE_AVG_OPS - 0.020 and away_ops < LEAGUE_AVG_OPS - 0.020:
            parts.append(
                f"Dos de las ofensivas más frías del momento: "
                f"{home_short} (OPS {home_ops:.3f}) y {away_short} (OPS {away_ops:.3f}) "
                f"están por debajo del promedio de liga."
            )
        else:
            parts.append(
                f"El modelo ve {mu:.1f} carreras totales — "
                f"debajo de la línea de {line} — con una combinación favorable de pitching y ofensivas."
            )

    # ── Oración 2: Drivers secundarios (clima, H2H, árbitro, lineup, parque) ──
    secondary: list[str] = []

    if abs(weather_adj) >= 0.40:
        # Simplificar descripción del clima
        desc_short = weather_desc.split("|")[0].strip() if weather_desc else ""
        if not desc_short and weather_adj > 0:
            desc_short = "condiciones cálidas"
        elif not desc_short:
            desc_short = "condiciones frías"
        sign_word = "suma" if weather_adj > 0 else "resta"
        secondary.append(f"el clima {sign_word} {abs(weather_adj):.1f} carreras ({desc_short})")

    if abs(h2h_total) >= 0.10:
        if h2h_total > 0:
            secondary.append(f"el historial pitcher-equipo agrega {h2h_total:+.2f} carreras esperadas")
        else:
            secondary.append(f"el historial pitcher-equipo reduce {abs(h2h_total):.2f} carreras esperadas")

    if abs(ump_adj) >= 0.15 and ump_name:
        if ump_adj < 0:
            secondary.append(f"el árbitro {ump_name} tiende a ser amplio ({ump_adj:+.2f} carr.)")
        else:
            secondary.append(f"el árbitro {ump_name} tiende a ser apretado ({ump_adj:+.2f} carr.)")

    if park_factor >= 1.10:
        secondary.append(f"el estadio favorece el bateo (factor {park_factor:.2f})")
    elif park_factor <= 0.92:
        secondary.append(f"el estadio suprime el bateo (factor {park_factor:.2f})")

    # Bullpen fatigue en narrativa
    home_bp_fatigued = pick.get("home_bp_fatigued", False)
    away_bp_fatigued = pick.get("away_bp_fatigued", False)
    home_bp_rested   = pick.get("home_bp_rested",   False)
    away_bp_rested   = pick.get("away_bp_rested",   False)
    if direction == "OVER":
        if away_bp_fatigued:
            secondary.append(f"el bullpen de {away_short} llega fatigado (ERA alta L4d)")
        if home_bp_fatigued:
            secondary.append(f"el bullpen de {home_short} llega fatigado (ERA alta L4d)")
    elif direction == "UNDER":
        if home_bp_rested:
            secondary.append(f"el bullpen de {home_short} llega descansado")
        if away_bp_rested:
            secondary.append(f"el bullpen de {away_short} llega descansado")

    if home_lineup or away_lineup:
        secondary.append("se usaron lineups confirmados del día")

    if secondary:
        parts.append("Además, " + " y ".join(secondary[:2]) + ".")

    # ── Oración 3: Conclusión ─────────────────────────────────────────────────
    pct_diff = abs(diff / line * 100) if line else 0
    conf     = pick.get("confianza", "")
    conf_es  = {"ALTA": "alta", "MEDIA": "moderada", "BAJA": "baja",
                "FLACA": "valor delgado (mercado coincide)",
                "SOSPECHA": "sospecha (edge alto = posible error del modelo)"}.get(conf, conf.lower())

    if direction == "OVER":
        parts.append(
            f"El modelo ve {mu:.1f} carreras vs línea de {line} "
            f"({diff:+.1f}, {pct_diff:.0f}% más) — "
            f"edge {edge:.0%}, confianza {conf_es}."
        )
    else:
        parts.append(
            f"El modelo ve {mu:.1f} carreras vs línea de {line} "
            f"({diff:+.1f}, {pct_diff:.0f}% menos) — "
            f"edge {edge:.0%}, confianza {conf_es}."
        )

    return " ".join(parts)


# ── Pipeline completo ─────────────────────────────────────────────────────────

# (analyze_all_games definida arriba, en línea ~629)
