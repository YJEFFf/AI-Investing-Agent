"""US 주문 관리자 테스트 — 소수점 매수/매도/부분매도."""
from datetime import datetime
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from execution.us_order_manager import USOrderManager
from repository.models import Position, Trade
from repository.queries import get_position, get_open_trade
from strategy.agents.decision_agent import TradeSignal


def make_signal(stop_pct=0.05, target_pct=0.08):
    return TradeSignal(
        action="buy", position_ratio=0.5, stop_price=0.0,
        stop_type="trailing", reasoning="test", ta_score=70.0,
        risk_level="medium", stop_pct=stop_pct, target_pct=target_pct,
    )


async def seed_us_position(
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


# ── 매수 ──────────────────────────────────────────────────────────────────────

async def test_buy_creates_position(db_session):
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=0.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_buy = AsyncMock(return_value=mock_result)
        with patch("kis.overseas_account.overseas_account.get_fill_price", new_callable=AsyncMock, return_value=182.5):
            mock_result.fill_price = 182.5
            await mgr.execute_buy(db_session, "AAPL", "Apple Inc.", 1.5, make_signal(), 180.0)

    pos = await get_position(db_session, "AAPL")
    assert pos is not None
    assert pos.market == "US"
    assert pos.qty == 1.5
    assert pos.avg_price == 182.5


async def test_buy_calculates_stop_from_fill_price(db_session):
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=200.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_buy = AsyncMock(return_value=mock_result)
        await mgr.execute_buy(db_session, "NVDA", "NVIDIA", 0.5, make_signal(stop_pct=0.05), 200.0)

    pos = await get_position(db_session, "NVDA")
    assert round(pos.stop_price, 4) == round(200.0 * 0.95, 4)
    assert round(pos.target_price, 4) == round(200.0 * 1.08, 4)


async def test_buy_fallback_to_current_price_on_fill_zero(db_session):
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=0.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_buy = AsyncMock(return_value=mock_result)
        _, fill = await mgr.execute_buy(db_session, "TSLA", "Tesla", 1.0, make_signal(), 250.0)

    assert fill == 250.0


async def test_buy_failed_order_returns_early(db_session):
    mgr = USOrderManager()
    mock_result = MagicMock(success=False, fill_price=0.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_buy = AsyncMock(return_value=mock_result)
        result, fill = await mgr.execute_buy(db_session, "AAPL", "Apple", 1.0, make_signal(), 180.0)

    assert not result.success
    pos = await get_position(db_session, "AAPL")
    assert pos is None


# ── 전량 매도 ─────────────────────────────────────────────────────────────────

async def test_sell_closes_position(db_session):
    await seed_us_position(db_session, ticker="AAPL", qty=1.5, avg_price=180.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=195.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_sell(db_session, "AAPL", 1.5, 195.0, "take_profit")

    pos = await get_position(db_session, "AAPL")
    assert pos is None


async def test_sell_records_correct_pnl(db_session):
    await seed_us_position(db_session, ticker="MSFT", qty=2.0, avg_price=400.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=420.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_sell(db_session, "MSFT", 2.0, 420.0, "take_profit")

    trade = await get_open_trade(db_session, "MSFT")
    assert trade is None  # closed
    from sqlalchemy import select
    from repository.models import Trade as TradeModel
    result = await db_session.execute(
        select(TradeModel).where(TradeModel.stock_code == "MSFT").where(TradeModel.status == "closed")
    )
    closed = result.scalar_one_or_none()
    assert closed is not None
    assert closed.market == "US"
    assert abs(closed.profit_loss - (420.0 - 400.0) * 2.0) < 0.001


async def test_sell_stop_loss_negative_pnl(db_session):
    await seed_us_position(db_session, ticker="AMD", qty=3.0, avg_price=150.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=142.5)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_sell(db_session, "AMD", 3.0, 142.5, "stop_loss")

    from sqlalchemy import select
    from repository.models import Trade as TradeModel
    result = await db_session.execute(
        select(TradeModel).where(TradeModel.stock_code == "AMD").where(TradeModel.status == "closed")
    )
    closed = result.scalar_one_or_none()
    assert closed.profit_loss < 0
    assert closed.exit_reason == "stop_loss"


# ── 부분 매도 ─────────────────────────────────────────────────────────────────

async def test_partial_sell_reduces_qty(db_session):
    await seed_us_position(db_session, ticker="GOOGL", qty=2.0, avg_price=170.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=185.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_partial_sell(db_session, "GOOGL", 1.0, 185.0, "partial_take_profit")

    pos = await get_position(db_session, "GOOGL")
    assert pos is not None
    assert abs(pos.qty - 1.0) < 0.0001


async def test_partial_sell_keeps_position(db_session):
    await seed_us_position(db_session, ticker="META", qty=3.0, avg_price=500.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=520.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_partial_sell(db_session, "META", 1.5, 520.0, "partial_take_profit")

    pos = await get_position(db_session, "META")
    assert pos is not None


async def test_partial_sell_creates_closed_trade(db_session):
    await seed_us_position(db_session, ticker="AMZN", qty=1.0, avg_price=200.0)
    mgr = USOrderManager()
    mock_result = MagicMock(success=True, fill_price=220.0)

    with patch("execution.us_order_manager.overseas_order") as mock_ord:
        mock_ord.fractional_sell = AsyncMock(return_value=mock_result)
        await mgr.execute_partial_sell(db_session, "AMZN", 0.5, 220.0, "partial_take_profit")

    from sqlalchemy import select
    from repository.models import Trade as TradeModel
    result = await db_session.execute(
        select(TradeModel).where(TradeModel.stock_code == "AMZN").where(TradeModel.status == "closed")
    )
    closed = result.scalar_one_or_none()
    assert closed is not None
    assert closed.market == "US"
    assert abs(closed.profit_loss - (220.0 - 200.0) * 0.5) < 0.001
