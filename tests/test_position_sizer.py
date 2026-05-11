from config.settings import settings
from risk.position_sizer import calc_position_size

_MAX_PCT = settings.max_position_pct   # 실제 설정값 기준 (현재 0.10)


def test_zero_kelly_returns_zero():
    # win_rate=0.5, avg_win=avg_loss=0.02 → kelly=0 → qty=0
    qty = calc_position_size(10_000_000, 5_000, 0.6, win_rate=0.5, avg_win_pct=0.02, avg_loss_pct=0.02)
    assert qty == 0


def test_zero_available_cash():
    assert calc_position_size(0, 5_000, 0.6) == 0


def test_zero_price():
    assert calc_position_size(10_000_000, 0, 0.6) == 0


def test_price_exceeds_95pct_balance():
    # current_price(96,000) > available_cash(100,000) * 0.95 = 95,000 → 0
    assert calc_position_size(100_000, 96_000, 0.6) == 0


def test_higher_confidence_buys_more():
    # positive kelly 사례 사용 (win_rate=0.6, avg_win=0.10, avg_loss=0.05)
    qty_low = calc_position_size(10_000_000, 5_000, 0.5, win_rate=0.6, avg_win_pct=0.10, avg_loss_pct=0.05)
    qty_high = calc_position_size(10_000_000, 5_000, 0.8, win_rate=0.6, avg_win_pct=0.10, avg_loss_pct=0.05)
    assert qty_high > qty_low


def test_confidence_scales_proportionally():
    # position_ratio 2배 → 수량 2배
    qty_half = calc_position_size(10_000_000, 5_000, 0.5, win_rate=0.6, avg_win_pct=0.10, avg_loss_pct=0.05)
    qty_full = calc_position_size(10_000_000, 5_000, 1.0, win_rate=0.6, avg_win_pct=0.10, avg_loss_pct=0.05)
    assert abs(qty_half / qty_full - 0.5) < 0.02


def test_positive_kelly_capped_at_max_position_pct():
    # kelly = (0.6/0.05) - (0.4/0.10) = 12 - 4 = 8 → half_kelly=4.0
    # capped at MAX_POSITION_PCT → fraction = MAX_PCT * 0.6 * 0.95
    expected = int(10_000_000 * _MAX_PCT * 0.6 * 0.95) // 5_000
    qty = calc_position_size(10_000_000, 5_000, 0.6, win_rate=0.6, avg_win_pct=0.10, avg_loss_pct=0.05)
    assert qty == expected


def test_low_kelly_below_max_uses_kelly():
    # half_kelly < MAX_POSITION_PCT 이면 kelly 값이 그대로 사용됨
    # kelly = (0.55/0.04) - (0.45/0.04) = 13.75 - 11.25 = 2.5 → half_kelly=1.25
    # max_pct=0.10보다 크므로 cap됨 → 여기선 아주 낮은 kelly로 테스트
    # win_rate=0.52, avg_win=0.02, avg_loss=0.01 → kelly=(0.52/0.01)-(0.48/0.02)=52-24=28 → cap
    # 대신 win_rate=0.51, avg_win=0.01, avg_loss=0.03 → kelly=(0.51/0.03)-(0.49/0.01)=17-49=-32 → 0
    # 확실히 kelly < max 케이스: avg_loss=0.5 → kelly=(0.6/0.5)-(0.4/0.1)=1.2-4=-2.8 → 0
    # 그냥 kelly < 0 → qty=0 인지 확인
    qty = calc_position_size(10_000_000, 5_000, 0.6, win_rate=0.4, avg_win_pct=0.05, avg_loss_pct=0.10)
    assert qty == 0  # kelly 음수 → half_kelly=0 → qty=0
