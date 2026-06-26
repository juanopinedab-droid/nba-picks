def american_to_raw_prob(odds: int) -> float:
    """American odds → raw implied probability (with vig)."""
    if odds > 0:
        return 100 / (odds + 100)
    return abs(odds) / (abs(odds) + 100)


def power_devig(p1: float, p2: float) -> tuple[float, float]:
    """Remove vig via power method: normalize raw probs to sum=1.0."""
    total = p1 + p2
    if total <= 0:
        return 0.5, 0.5
    return p1 / total, p2 / total
