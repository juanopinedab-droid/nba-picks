from dataclasses import dataclass, field


@dataclass
class OddsData:
    h2h_home: int | None = None
    h2h_away: int | None = None
    spread_home: int | None = None
    spread_home_pts: float | None = None
    spread_away: int | None = None
    spread_away_pts: float | None = None
    total_line: float | None = None
    total_over: int | None = None
    total_under: int | None = None
    bookmaker: str | None = None
    consensus_impl_home: float | None = None
    consensus_impl_away: float | None = None
    consensus_books: int = 0
    impl_home_by_book: list[float] = field(default_factory=list)
    impl_away_by_book: list[float] = field(default_factory=list)


@dataclass
class Game:
    game_id: str
    home_team: str
    away_team: str
    commence: str
    h2h_home: int | None = None
    h2h_away: int | None = None
    spread_home: int | None = None
    spread_home_pts: float | None = None
    spread_away: int | None = None
    spread_away_pts: float | None = None
    total_line: float | None = None
    total_over: int | None = None
    total_under: int | None = None
    bookmaker: str | None = None
    consensus_impl_home: float | None = None
    consensus_impl_away: float | None = None
    consensus_books: int = 0
    impl_home_by_book: list[float] = field(default_factory=list)
    impl_away_by_book: list[float] = field(default_factory=list)


@dataclass
class Pick:
    game: str
    bet_type: str
    selection: str
    odds: int
    our_prob: float
    implied_prob: float
    edge: float
    confidence: str
    sport: str = 'nba'
    commence_time: str = ''
    kelly_stake: float = 0.0
    reasons: list[str] = field(default_factory=list)
    bookmaker: str = ''


@dataclass
class JobContext:
    job_id: str
    progress: float = 0.0
    log: list[str] = field(default_factory=list)

    def set_progress(self, pct: float):
        self.progress = max(0.0, min(1.0, pct))

    def log_line(self, msg: str):
        self.log.append(msg)
        self._flush()

    def _flush(self):
        from ..core import database
        try:
            with database.get_conn() as conn:
                conn.execute(
                    "UPDATE jobs SET progress = ?, log = ? WHERE id = ?",
                    (self.progress, "\n".join(self.log[-200:]), self.job_id)
                )
                conn.commit()
        except Exception:
            pass


@dataclass
class JobResult:
    job_id: str
    type: str
    status: str
    progress: float = 0.0
    result: dict | None = None
    error: str | None = None
