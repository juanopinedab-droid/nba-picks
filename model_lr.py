# Auto-generado por backtest.py — no editar manualmente
# Entrenado sobre 2540 partidos de 2023-24, 2024-25
import math

_COEF      = [0.7958570477049712, -0.1636492724330463, 0.11410898792307364, -0.001255242156925948]
_INTERCEPT = 0.2097478179314213
_MEANS     = [2.786265760837569, 0.16141732283464566, 0.17165354330708663, 0.015828113468487073]
_SCALES    = [10.02404136992753, 0.3679154396373506, 0.3770790426120459, 0.35946058753834415]


def lr_prob(features: list) -> float:
    """
    Probabilidad de victoria del equipo local.
    features = [net_diff_base, home_b2b (0.0/1.0), away_b2b (0.0/1.0), rest_tanh]
    net_diff_base: diferencial de Net Rating sin ajustes B2B/rest
    rest_tanh:     tanh((home_rest - away_rest) / 2)
    """
    scaled = [(f - m) / s for f, m, s in zip(features, _MEANS, _SCALES)]
    z = _INTERCEPT + sum(c * x for c, x in zip(_COEF, scaled))
    return max(0.05, min(0.93, 1 / (1 + math.exp(-z))))
