_BASE_AMOUNT = 2_000_000


def calc_position_size(
    available_cash: int,
    current_price: int,
    confidence: float,
) -> int:
    """매수 가능 수량 계산 (2,000,000 × confidence, 가용 잔고 90% 캡)"""
    if current_price <= 0 or available_cash <= 0:
        return 0

    budget = int(_BASE_AMOUNT * confidence)
    budget = min(budget, int(available_cash * 0.9))

    if current_price > budget:
        return 0

    return budget // current_price
