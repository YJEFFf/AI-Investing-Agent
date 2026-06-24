def calc_position_size(
    available_cash: int,
    current_price: int,
    confidence: float,
) -> int:
    """매수 수량 계산 — 데이터 수집 모드: 무조건 1주"""
    if current_price <= 0 or available_cash <= 0:
        return 0
    if current_price > available_cash:
        return 0
    return 1
