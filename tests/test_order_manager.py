from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from execution.order_manager import OrderManager
from repository.models import Position, Trade
from repository.queries import get_position, get_open_trade
from strategy.agents.decision_agent import TradeSignal


def make_signal(stop_pct=0.05, target_pct=0.08, stop_type="trailing"):
    return TradeSignal(
        action="buy",
        position_ratio=0.6,
        stop_price=0,
        stop_type=stop_type,
        reasoning="test",
        ta_score=70.0,
        risk_level="medium",
        stop_pct=stop_pct,
        target_pct=target_pct,
    )


def make_order_result(success=True, fill_price=None):
    r = MagicMock()
    r.success = success
    r.fill_price = fill_price
    return r


async def seed_position(session, code="005930", qty=10, avg_price=10_000, stop_price=9_500):
    pos = Position(
        stock_code=code,
        stock_name="삼성전자",
        qty=qty,
        avg_price=float(avg_price),
        stop_price=float(stop_price),
        stop_type="trailing",
        target_price=float(10_800),
        trail_pct=0.05,
        highest_price=float(avg_price),
        trade_type="swing",
        opened_at=datetime.utcnow(),
    )
    trade = Trade(
        stock_code=code,
        stock_name="삼성전자",
        status="open",
        entry_at=datetime.utcnow(),
        entry_price=avg_price,
        entry_qty=qty,
        stop_price=stop_price,
        stop_type="trailing",
    )
    session.add(pos)
    session.add(trade)
    await session.commit()


@pytest.fixture
def om():
    return OrderManager()


# ── execute_buy ───────────────────────────────────────────────────────────────

async def test_buy_creates_position(db_session, om):
    result = make_order_result(success=True, fill_price=10_000)
    with patch("execution.order_manager.kis_order.market_buy", AsyncMock(return_value=result)):
        _, fill = await om.execute_buy(
            db_session, "005930", "삼성전자", 10,
            make_signal(stop_pct=0.05, target_pct=0.08), 10_100,
        )

    assert fill == 10_000
    pos = await get_position(db_session, "005930")
    assert pos is not None
    assert pos.qty == 10
    assert int(pos.avg_price) == 10_000
    assert int(pos.stop_price) == 9_500    # 10_000 * 0.95
    assert int(pos.target_price) == 10_800  # 10_000 * 1.08


async def test_buy_uses_current_price_when_no_fill(db_session, om):
    result = make_order_result(success=True, fill_price=None)
    with patch("execution.order_manager.kis_order.market_buy", AsyncMock(return_value=result)):
        _, fill = await om.execute_buy(
            db_session, "005930", "삼성전자", 5, make_signal(), 9_800,
        )
    assert fill == 9_800


async def test_buy_failed_order_creates_no_position(db_session, om):
    result = make_order_result(success=False, fill_price=None)
    with patch("execution.order_manager.kis_order.market_buy", AsyncMock(return_value=result)):
        await om.execute_buy(
            db_session, "005930", "삼성전자", 5, make_signal(), 9_800,
        )
    pos = await get_position(db_session, "005930")
    assert pos is None


async def test_buy_stop_target_based_on_fill_not_current(db_session, om):
    # fill_price(9,800) 기준으로 계산, current_price(10,000)가 아님
    result = make_order_result(success=True, fill_price=9_800)
    with patch("execution.order_manager.kis_order.market_buy", AsyncMock(return_value=result)):
        await om.execute_buy(
            db_session, "005930", "삼성전자", 10,
            make_signal(stop_pct=0.05, target_pct=0.10), 10_000,
        )
    pos = await get_position(db_session, "005930")
    assert int(pos.stop_price) == round(9_800 * 0.95)
    assert int(pos.target_price) == round(9_800 * 1.10)


# ── execute_sell ──────────────────────────────────────────────────────────────

async def test_sell_removes_position(db_session, om):
    await seed_position(db_session)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        _, fill = await om.execute_sell(db_session, "005930", 10, 11_100, "take_profit")

    assert fill == 11_000
    pos = await get_position(db_session, "005930")
    assert pos is None


async def test_sell_closes_trade_record(db_session, om):
    await seed_position(db_session)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_sell(db_session, "005930", 10, 11_100, "take_profit")

    open_trade = await get_open_trade(db_session, "005930")
    assert open_trade is None


async def test_sell_records_correct_pnl(db_session, om):
    await seed_position(db_session, avg_price=10_000, qty=10)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_sell(db_session, "005930", 10, 11_100, "take_profit")

    res = await db_session.execute(
        select(Trade)
        .where(Trade.stock_code == "005930")
        .where(Trade.status == "closed")
    )
    trade = res.scalar_one_or_none()
    assert trade is not None
    assert trade.profit_loss == (11_000 - 10_000) * 10   # 10,000원
    assert trade.exit_reason == "take_profit"


async def test_sell_stop_loss_records_negative_pnl(db_session, om):
    await seed_position(db_session, avg_price=10_000, qty=10)
    result = make_order_result(success=True, fill_price=9_300)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_sell(db_session, "005930", 10, 9_400, "stop_loss")

    res = await db_session.execute(
        select(Trade)
        .where(Trade.stock_code == "005930")
        .where(Trade.status == "closed")
    )
    trade = res.scalar_one_or_none()
    assert trade.profit_loss == (9_300 - 10_000) * 10  # -7,000원
    assert trade.profit_loss < 0


# ── execute_partial_sell ──────────────────────────────────────────────────────

async def test_partial_sell_reduces_qty(db_session, om):
    await seed_position(db_session, qty=10)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_partial_sell(db_session, "005930", 5, 11_100, "partial_take_profit")

    pos = await get_position(db_session, "005930")
    assert pos is not None
    assert pos.qty == 5


async def test_partial_sell_keeps_position(db_session, om):
    await seed_position(db_session, qty=10)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_partial_sell(db_session, "005930", 5, 11_100, "partial_take_profit")

    pos = await get_position(db_session, "005930")
    assert pos is not None


async def test_partial_sell_creates_closed_trade(db_session, om):
    await seed_position(db_session, qty=10, avg_price=10_000)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_partial_sell(db_session, "005930", 5, 11_100, "partial_take_profit")

    res = await db_session.execute(
        select(Trade)
        .where(Trade.stock_code == "005930")
        .where(Trade.status == "closed")
    )
    closed = res.scalar_one_or_none()
    assert closed is not None
    assert closed.profit_loss == (11_000 - 10_000) * 5   # 5,000원
    assert closed.exit_reason == "partial_take_profit"


async def test_partial_sell_open_trade_still_exists(db_session, om):
    # 원본 open 레코드는 최종 매도 시까지 남아있어야 함
    await seed_position(db_session, qty=10)
    result = make_order_result(success=True, fill_price=11_000)
    with patch("execution.order_manager.kis_order.market_sell", AsyncMock(return_value=result)):
        await om.execute_partial_sell(db_session, "005930", 5, 11_100, "partial_take_profit")

    open_trade = await get_open_trade(db_session, "005930")
    assert open_trade is not None
