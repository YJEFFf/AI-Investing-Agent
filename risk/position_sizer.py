from config.settings import settings


def calc_position_size(
    available_cash: int,
    current_price: int,
    position_ratio: float,
    win_rate: float = 0.5,
    avg_win_pct: float = 0.02,
    avg_loss_pct: float = 0.02,
) -> int:
    """매수 가능 수량 계산 (half-Kelly + 최대 비율 캡)"""
    if current_price <= 0 or available_cash <= 0:
        return 0

    # half-Kelly
    if avg_loss_pct > 0:
        kelly = (win_rate / avg_loss_pct) - ((1 - win_rate) / avg_win_pct)
        half_kelly = max(0.0, kelly / 2)
    else:
        half_kelly = settings.max_position_pct

    # 최대 비율 캡 적용
    fraction = min(half_kelly, settings.max_position_pct) * position_ratio
    budget = int(available_cash * fraction)

    # 잔고 기반 가격 필터: 주당 가격 ≤ 잔고 × 0.95
    max_price = int(available_cash * 0.95)
    if current_price > max_price:
        return 0

    qty = budget // current_price
    return max(0, qty)
