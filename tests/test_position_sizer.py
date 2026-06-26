from risk.position_sizer import calc_position_size


# ── 0 반환 케이스 ─────────────────────────────────────────────────────────────

def test_zero_available_cash():
    assert calc_position_size(0, 5_000, 0.6) == 0


def test_zero_price():
    assert calc_position_size(10_000_000, 0, 0.6) == 0


def test_price_exceeds_available_cash():
    # current_price > available_cash → 살 수 없음
    assert calc_position_size(5_000, 10_000, 0.6) == 0


# ── 1주 반환 케이스 ───────────────────────────────────────────────────────────

def test_returns_one_unit():
    # 데이터 수집 모드 — 조건 충족 시 항상 1주
    assert calc_position_size(10_000_000, 5_000, 0.6) == 1


def test_confidence_does_not_affect_qty():
    # confidence 와 무관하게 1주
    assert calc_position_size(10_000_000, 5_000, 0.5) == 1
    assert calc_position_size(10_000_000, 5_000, 1.0) == 1


def test_exact_balance_allowed():
    # current_price == available_cash → 구매 가능
    assert calc_position_size(5_000, 5_000, 0.6) == 1
