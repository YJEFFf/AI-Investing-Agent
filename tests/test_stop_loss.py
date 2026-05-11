from unittest.mock import MagicMock
from risk.stop_loss import should_stop, update_trailing_stop


def make_pos(stop_price, avg_price=10_000, highest_price=None, trail_pct=0.05):
    pos = MagicMock()
    pos.stop_price = stop_price
    pos.avg_price = avg_price
    pos.highest_price = highest_price if highest_price is not None else avg_price
    pos.trail_pct = trail_pct
    return pos


# ── should_stop ──────────────────────────────────────────────────────────────

def test_triggers_at_stop_price():
    assert should_stop(make_pos(9_500), 9_500) is True


def test_triggers_below_stop_price():
    assert should_stop(make_pos(9_500), 9_000) is True


def test_no_trigger_above_stop_price():
    assert should_stop(make_pos(9_500), 9_501) is False


def test_no_trigger_at_breakeven_plus_one():
    # partial TP 후 stop = avg_price(10,000), 현재가 10,001 → 손절 안 함
    assert should_stop(make_pos(10_000), 10_001) is False


# ── update_trailing_stop ─────────────────────────────────────────────────────

def test_trailing_rises_on_new_high():
    # 고점 10,000 → 현재가 11,000으로 신고점 갱신
    # new_stop = int(11,000 * 0.95) = 10,450
    pos = make_pos(stop_price=9_500, highest_price=10_000, trail_pct=0.05)
    assert update_trailing_stop(pos, 11_000) == int(11_000 * 0.95)


def test_trailing_never_decreases():
    # 고점 11,000에서 현재가 9,000으로 하락 → stop은 내려가지 않음
    # highest = max(11,000, 9,000) = 11,000 → calc = 10,450 > stop(10,000) → 10,450
    pos = make_pos(stop_price=10_000, highest_price=11_000, trail_pct=0.05)
    assert update_trailing_stop(pos, 9_000) == int(11_000 * 0.95)


def test_tight_trail_stops_higher_than_wide():
    # trail 3% vs 8%: tight(3%)이 더 높은 손절가를 가짐
    pos_tight = make_pos(stop_price=9_000, highest_price=10_000, trail_pct=0.03)
    pos_wide = make_pos(stop_price=9_000, highest_price=10_000, trail_pct=0.08)
    assert update_trailing_stop(pos_tight, 10_000) > update_trailing_stop(pos_wide, 10_000)


def test_partial_tp_wider_trail_gives_more_room():
    # partial TP 전(5%) vs 후(8%): 8%가 더 낮은 손절가 → 나머지 포지션에 더 많은 여유
    pos_before = make_pos(stop_price=9_500, highest_price=10_000, trail_pct=0.05)
    pos_after = make_pos(stop_price=10_000, highest_price=10_000, trail_pct=0.08)
    # 신고점 11,000 도달 후
    stop_before = update_trailing_stop(pos_before, 11_000)  # int(11,000 * 0.95) = 10,450
    stop_after = update_trailing_stop(pos_after, 11_000)    # int(11,000 * 0.92) = 10,120
    assert stop_before > stop_after
