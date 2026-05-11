"""
_check_exit_conditions 통합 테스트.

AsyncSessionLocal을 in-memory SQLite 세션으로 교체하고,
KIS API / 알림 함수는 모두 AsyncMock으로 대체한다.
"""
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from core.orchestrator import Orchestrator
from repository.models import Position
from repository.queries import get_position


# ── 헬퍼 ────────────────────────────────────────────────────────────────────

async def seed_position(
    session,
    code="005930",
    qty=10,
    avg_price=10_000,
    stop_price=9_500,
    target_price=None,
    stop_type="trailing",
    trail_pct=0.05,
    highest_price=None,
):
    pos = Position(
        stock_code=code,
        stock_name="삼성전자",
        qty=qty,
        avg_price=float(avg_price),
        stop_price=float(stop_price),
        stop_type=stop_type,
        target_price=float(target_price) if target_price else None,
        trail_pct=trail_pct,
        highest_price=float(highest_price or avg_price),
        trade_type="swing",
        opened_at=datetime.utcnow(),
    )
    session.add(pos)
    await session.commit()


class _FakeSessionCM:
    """AsyncSessionLocal() → async with 패턴을 흉내 내는 context manager."""

    def __init__(self, session):
        self._s = session

    async def __aenter__(self):
        return self._s

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def orch():
    return Orchestrator()


def session_factory(session):
    """patch("core.orchestrator.AsyncSessionLocal").return_value 에 넣을 값."""
    return _FakeSessionCM(session)


# ── 트레일링 스톱 업데이트 ────────────────────────────────────────────────────

async def test_trailing_stop_rises_on_new_high(db_session, orch):
    # 고점 10,000 → 현재가 11,000 → new_stop = int(11,000 * 0.95) = 10,450
    await seed_position(db_session, stop_price=9_500, highest_price=10_000, trail_pct=0.05)

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_ka.get_balance = AsyncMock(return_value={"available_cash": 0, "total_eval": 0, "unrealized_pnl": 0})
        await orch._check_exit_conditions("005930", 11_000)

    pos = await get_position(db_session, "005930")
    assert int(pos.stop_price) == int(11_000 * 0.95)  # 10,450
    assert pos.highest_price == 11_000


async def test_trailing_stop_never_decreases(db_session, orch):
    # 고점 11,000 → 현재가 10,600으로 하락 (stop 10,450보다는 높음 → 손절 안 함)
    # 트레일링 재계산: int(11,000 * 0.95) = 10,450 → 기존값과 동일 → 낮아지지 않음
    await seed_position(
        db_session, stop_price=10_450, highest_price=11_000, trail_pct=0.05
    )

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 10_600)

    pos = await get_position(db_session, "005930")
    assert int(pos.stop_price) == 10_450   # 낮아지지 않음


# ── 손절 발동 ────────────────────────────────────────────────────────────────

async def test_stop_loss_fires_below_stop_price(db_session, orch):
    await seed_position(db_session, avg_price=10_000, stop_price=9_500)

    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 9_400))
    notify_mock = AsyncMock()

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", notify_mock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 9_400)

    sell_mock.assert_called_once()
    # 매도 reason 확인
    assert sell_mock.call_args.args[4] == "stop_loss"
    notify_mock.assert_called_once()


async def test_stop_loss_does_not_fire_above_stop(db_session, orch):
    await seed_position(db_session, avg_price=10_000, stop_price=9_500)

    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 9_600))

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 9_600)

    sell_mock.assert_not_called()


# ── 절반 익절 ────────────────────────────────────────────────────────────────

async def test_partial_tp_sells_half_qty(db_session, orch):
    await seed_position(
        db_session, qty=10, avg_price=10_000, stop_price=9_500, target_price=11_000
    )

    partial_mock = AsyncMock(return_value=(MagicMock(success=True), 11_000))
    notify_mock = AsyncMock()

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", notify_mock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_partial_sell = partial_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 11_000)

    partial_mock.assert_called_once()
    assert partial_mock.call_args.args[2] == 5   # half_qty = 10 // 2


async def test_partial_tp_sets_breakeven_stop(db_session, orch):
    # partial TP 후 stop_price == avg_price(10,000), target_price == None
    await seed_position(
        db_session, qty=10, avg_price=10_000, stop_price=9_500, target_price=11_000
    )

    partial_mock = AsyncMock(return_value=(MagicMock(success=True), 11_000))

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_partial_sell = partial_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 11_000)

    pos = await get_position(db_session, "005930")
    assert pos is not None              # 포지션 유지
    assert int(pos.stop_price) == 10_000  # 손익분기 이동
    assert pos.target_price is None       # target 제거


async def test_partial_tp_widens_trail_pct(db_session, orch):
    # partial TP 후 trail_pct가 partial_tp_trail_pct(0.08)로 확대됨
    from config.settings import settings

    await seed_position(
        db_session, qty=10, avg_price=10_000, stop_price=9_500,
        target_price=11_000, trail_pct=0.05,
    )

    partial_mock = AsyncMock(return_value=(MagicMock(success=True), 11_000))

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_partial_sell = partial_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 11_000)

    pos = await get_position(db_session, "005930")
    assert pos.trail_pct == settings.partial_tp_trail_pct  # 0.08


# ── 전량 익절 (1주) ──────────────────────────────────────────────────────────

async def test_full_tp_on_single_share(db_session, orch):
    # qty=1 → half_qty=0 → 전량 매도
    await seed_position(
        db_session, qty=1, avg_price=10_000, stop_price=9_500, target_price=11_000
    )

    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 11_000))
    notify_mock = AsyncMock()

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", notify_mock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("005930", 11_000)

    sell_mock.assert_called_once()
    assert sell_mock.call_args.args[4] == "take_profit"
    notify_mock.assert_called_once()


# ── 포지션 없을 때 ────────────────────────────────────────────────────────────

async def test_no_position_exits_early(db_session, orch):
    # DB에 포지션 없음 → 아무것도 하지 않음
    sell_mock = AsyncMock()

    with patch("core.orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.orchestrator.order_manager") as mock_om, \
         patch("core.orchestrator.notify_sell", new_callable=AsyncMock), \
         patch("core.orchestrator.kis_account") as mock_ka:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_ka.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("999999", 10_000)

    sell_mock.assert_not_called()
