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

    # 최대 비율 캡 적용 + 5% 슬리피지 버퍼
    # 시장가 주문은 조회 시점보다 높은 가격에 체결될 수 있으므로
    # 예산을 5% 줄여 실제 체결가가 높아도 한도 초과 방지
    fraction = min(half_kelly, settings.max_position_pct) * position_ratio * 0.95
    budget = int(available_cash * fraction)

    # 잔고 기반 가격 필터: 주당 가격 ≤ 잔고 × 0.95
    max_price = int(available_cash * 0.95)
    if current_price > max_price:
        return 0

    qty = budget // current_price
    return max(0, qty)
