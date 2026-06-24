import logging

logger = logging.getLogger(__name__)


class PortfolioGuard:
    def __init__(self):
        self._peak_balance: int = 0

    def update_peak(self, current_balance: int) -> None:
        if current_balance > self._peak_balance:
            self._peak_balance = current_balance

    def allows_new_entry(
        self,
        current_balance: int,
        daily_pnl_pct: float,
    ) -> tuple[bool, str]:
        return True, "ok"

    def get_drawdown_pct(self, current_balance: int) -> float:
        if self._peak_balance <= 0:
            return 0.0
        return (self._peak_balance - current_balance) / self._peak_balance


portfolio_guard = PortfolioGuard()
