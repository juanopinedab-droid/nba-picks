def get_direction(game_id: str, over_under: str, line: float) -> str:
    return "steady"


def get_movement(game_id: str, over_under: str) -> float:
    return 0.0


def record_and_get_movement(
    game_date_str: str = "",
    away_team: str = "",
    home_team: str = "",
    current_line: float = 0.0,
    current_over_odds: int = 0,
    current_under_odds: int = 0,
) -> dict:
    return {
        "opening_line": current_line,
        "line_movement": 0.0,
        "movement_signal": "neutral",
        "first_seen": True,
    }
