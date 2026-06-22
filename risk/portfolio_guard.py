import logging
from config.settings import settings

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
        """신규 진입 가능 여부 및 이유 반환"""
        if daily_pnl_pct <= -settings.daily_loss_cap:
            return False, f"일일 손실 한도 -{settings.daily_loss_cap*100:.0f}% 도달"

        return True, "ok"

    def get_drawdown_pct(self, current_balance: int) -> float:
        if self._peak_balance <= 0:
            return 0.0
        return (self._peak_balance - current_balance) / self._peak_balance


portfolio_guard = PortfolioGuard()
