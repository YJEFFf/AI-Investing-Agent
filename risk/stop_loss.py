from config.settings import settings
from repository.models import Position


def calc_initial_stop(entry_price: int, stop_type: str, atr: float = 0.0) -> int:
    """최초 손절가 계산"""
    if stop_type == "atr" and atr > 0:
        return int(entry_price - 2.0 * atr)
    return int(entry_price * (1 - settings.default_stop_pct))


def update_trailing_stop(position: Position, current_price: int, trail_pct: float = 0.03) -> int:
    """트레일링 스탑 업데이트: 고점 대비 trail_pct% 하락 시 손절"""
    pct = position.trail_pct if (position.trail_pct and position.trail_pct > 0) else trail_pct
    highest = max(position.highest_price or position.avg_price, current_price)
    new_stop = int(highest * (1 - pct))
    return max(int(position.stop_price), new_stop)


def should_stop(position: Position, current_price: int) -> bool:
    """현재가가 손절가에 도달했는지 확인"""
    return current_price <= position.stop_price
