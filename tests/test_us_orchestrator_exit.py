"""USOrchestrator._check_exit_conditions 통합 테스트 (USD / 소수점 수량)."""
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from core.us_orchestrator import USOrchestrator
from repository.models import Position
from repository.queries import get_position


async def seed_us_pos(
    session,
    ticker="AAPL",
    qty=1.5,
    avg_price=180.0,
    stop_price=171.0,
    target_price=None,
    trail_pct=0.05,
    highest_price=None,
):
    pos = Position(
        market="US",
        stock_code=ticker,
        stock_name="Apple Inc.",
        qty=qty,
        avg_price=avg_price,
        stop_price=stop_price,
        stop_type="trailing",
        target_price=float(target_price) if target_price else None,
        trail_pct=trail_pct,
        highest_price=float(highest_price or avg_price),
        trade_type="swing",
        opened_at=datetime.utcnow(),
    )
    session.add(pos)
    await session.commit()


class _FakeSessionCM:
    def __init__(self, s): self._s = s
    async def __aenter__(self): return self._s
    async def __aexit__(self, *a): pass


@pytest.fixture
def orch():
    return USOrchestrator()


def session_factory(session):
    return _FakeSessionCM(session)


# ── 트레일링 스톱 ──────────────────────────────────────────────────────────────

async def test_trailing_stop_rises_on_new_high(db_session, orch):
    await seed_us_pos(db_session, stop_price=171.0, highest_price=180.0, trail_pct=0.05)

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock):
        mock_sl.return_value = session_factory(db_session)
        await orch._check_exit_conditions("AAPL", 190.0)

    pos = await get_position(db_session, "AAPL")
    assert abs(pos.stop_price - round(190.0 * 0.95, 4)) < 0.001
    assert pos.highest_price == 190.0


async def test_trailing_stop_never_decreases(db_session, orch):
    await seed_us_pos(db_session, stop_price=round(190.0 * 0.95, 4), highest_price=190.0, trail_pct=0.05)

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager"), \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock):
        mock_sl.return_value = session_factory(db_session)
        await orch._check_exit_conditions("AAPL", 185.0)  # 하락했지만 stop 이상

    pos = await get_position(db_session, "AAPL")
    assert abs(pos.stop_price - round(190.0 * 0.95, 4)) < 0.001


# ── 손절 ──────────────────────────────────────────────────────────────────────

async def test_stop_loss_fires_below_stop_price(db_session, orch):
    await seed_us_pos(db_session, avg_price=180.0, stop_price=171.0)
    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 170.0))
    notify_mock = AsyncMock()

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", notify_mock), \
         patch("core.us_orchestrator.overseas_account") as mock_acc:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_acc.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("AAPL", 170.0)

    sell_mock.assert_called_once()
    assert sell_mock.call_args.args[4] == "stop_loss"
    notify_mock.assert_called_once()


async def test_stop_loss_does_not_fire_above_stop(db_session, orch):
    await seed_us_pos(db_session, avg_price=180.0, stop_price=171.0)
    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 175.0))

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock):
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        await orch._check_exit_conditions("AAPL", 175.0)

    sell_mock.assert_not_called()


# ── 절반 익절 ─────────────────────────────────────────────────────────────────

async def test_partial_tp_sells_half_qty(db_session, orch):
    await seed_us_pos(db_session, qty=2.0, avg_price=180.0, stop_price=171.0, target_price=194.4)
    partial_mock = AsyncMock(return_value=(MagicMock(success=True), 194.4))

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock), \
         patch("core.us_orchestrator.overseas_account") as mock_acc:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_partial_sell = partial_mock
        mock_acc.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("AAPL", 194.4)

    partial_mock.assert_called_once()
    assert abs(partial_mock.call_args.args[2] - 1.0) < 0.0001  # half of 2.0


async def test_partial_tp_sets_breakeven_stop(db_session, orch):
    await seed_us_pos(db_session, qty=2.0, avg_price=180.0, stop_price=171.0, target_price=194.4)
    partial_mock = AsyncMock(return_value=(MagicMock(success=True), 194.4))

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock), \
         patch("core.us_orchestrator.overseas_account") as mock_acc:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_partial_sell = partial_mock
        mock_acc.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("AAPL", 194.4)

    pos = await get_position(db_session, "AAPL")
    assert pos is not None
    assert abs(pos.stop_price - 180.0) < 0.01  # 손익분기로 이동
    assert pos.target_price is None


async def test_full_tp_on_tiny_qty(db_session, orch):
    # qty가 너무 작으면 (≤ 0.0002) 전량 익절
    await seed_us_pos(db_session, qty=0.0001, avg_price=180.0, stop_price=171.0, target_price=194.4)
    sell_mock = AsyncMock(return_value=(MagicMock(success=True), 194.4))
    notify_mock = AsyncMock()

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", notify_mock), \
         patch("core.us_orchestrator.overseas_account") as mock_acc:
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        mock_acc.get_balance = AsyncMock(return_value={})
        await orch._check_exit_conditions("AAPL", 194.4)

    sell_mock.assert_called_once()
    assert sell_mock.call_args.args[4] == "take_profit"


async def test_no_position_exits_early(db_session, orch):
    sell_mock = AsyncMock()

    with patch("core.us_orchestrator.AsyncSessionLocal") as mock_sl, \
         patch("core.us_orchestrator.us_order_manager") as mock_om, \
         patch("core.us_orchestrator.notify_us_sell", new_callable=AsyncMock):
        mock_sl.return_value = session_factory(db_session)
        mock_om.execute_sell = sell_mock
        await orch._check_exit_conditions("FAKE", 100.0)

    sell_mock.assert_not_called()
