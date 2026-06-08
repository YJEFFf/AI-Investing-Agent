from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from repository.models import Trade, Position, DailyPnL, Signal
from core.trading_calendar import last_analysis_cutoff_utc


async def get_open_positions(session: AsyncSession) -> list[Position]:
    result = await session.execute(select(Position))
    return result.scalars().all()


async def get_position(session: AsyncSession, stock_code: str, market: str | None = None) -> Position | None:
    query = select(Position).where(Position.stock_code == stock_code)
    if market:
        query = query.where(Position.market == market)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def save_position(session: AsyncSession, position: Position) -> None:
    session.add(position)
    await session.commit()


async def delete_position(session: AsyncSession, stock_code: str, market: str | None = None) -> None:
    position = await get_position(session, stock_code, market=market)
    if position:
        await session.delete(position)
        await session.commit()


async def get_open_trade(session: AsyncSession, stock_code: str, market: str | None = None) -> Trade | None:
    query = (
        select(Trade)
        .where(Trade.stock_code == stock_code)
        .where(Trade.status == "open")
        .order_by(Trade.entry_at.desc())
        .limit(1)
    )
    if market:
        query = query.where(Trade.market == market)
    result = await session.execute(query)
    return result.scalar_one_or_none()


async def get_recent_trades(session: AsyncSession, limit: int = 50) -> list[Trade]:
    result = await session.execute(
        select(Trade)
        .where(Trade.status == "closed")
        .order_by(Trade.exit_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def get_today_realized_pnl(session: AsyncSession) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.sum(Trade.profit_loss))
        .where(Trade.status == "closed")
        .where(func.date(Trade.exit_at) == today)
    )
    return result.scalar() or 0


async def save_signal(session: AsyncSession, signal: Signal) -> Signal:
    session.add(signal)
    await session.commit()
    await session.refresh(signal)
    return signal


async def save_trade(session: AsyncSession, trade: Trade) -> Trade:
    session.add(trade)
    await session.commit()
    await session.refresh(trade)
    return trade


async def get_today_trade_count(session: AsyncSession) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Trade.id))
        .where(func.date(Trade.entry_at) == today)
    )
    return result.scalar() or 0


async def get_today_signal_count(session: AsyncSession) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Signal.id))
        .where(func.date(Signal.created_at) == today)
    )
    return result.scalar() or 0


async def get_today_sell_count(session: AsyncSession) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Trade.id))
        .where(Trade.status == "closed")
        .where(func.date(Trade.exit_at) == today)
    )
    return result.scalar() or 0


async def get_today_signal_count_by_market(session: AsyncSession, market: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Signal.id))
        .where(Signal.market == market)
        .where(func.date(Signal.created_at) == today)
    )
    return result.scalar() or 0


async def get_open_positions_by_market(session: AsyncSession, market: str) -> list[Position]:
    result = await session.execute(select(Position).where(Position.market == market))
    return result.scalars().all()


async def get_today_realized_pnl_by_market(session: AsyncSession, market: str) -> float:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.sum(Trade.profit_loss))
        .where(Trade.market == market)
        .where(Trade.status == "closed")
        .where(func.date(Trade.exit_at) == today)
    )
    return float(result.scalar() or 0)


async def get_today_trade_count_by_market(session: AsyncSession, market: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Trade.id))
        .where(Trade.market == market)
        .where(func.date(Trade.entry_at) == today)
    )
    return result.scalar() or 0


async def get_today_sell_count_by_market(session: AsyncSession, market: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await session.execute(
        select(func.count(Trade.id))
        .where(Trade.market == market)
        .where(Trade.status == "closed")
        .where(func.date(Trade.exit_at) == today)
    )
    return result.scalar() or 0


async def get_pending_signals(session: AsyncSession, market: str = "KR") -> list[Signal]:
    """미실행 pending 신호 조회 — 마지막 거래일 16:00 KST 이후 생성된 것만

    24시간 윈도우 대신 last_analysis_cutoff_utc()를 사용하여
    주말/공휴일 후 금요일 신호가 만료되어 재분석되는 문제를 방지한다.
    """
    cutoff = last_analysis_cutoff_utc()
    result = await session.execute(
        select(Signal)
        .where(Signal.market == market)
        .where(Signal.pending == True)
        .where(Signal.created_at >= cutoff)
        .order_by(Signal.chart_confidence.desc())
    )
    return result.scalars().all()


async def clear_pending_signals(session: AsyncSession, market: str = "KR") -> None:
    """pending 신호 전체 실행 완료(만료) 처리"""
    cutoff = last_analysis_cutoff_utc()
    await session.execute(
        update(Signal)
        .where(Signal.market == market)
        .where(Signal.pending == True)
        .where(Signal.created_at >= cutoff)
        .values(pending=False)
    )
    await session.commit()


async def expire_old_pending_signals(session: AsyncSession, market: str = "KR") -> int:
    """마지막 거래일 분석 시작 이전 pending 신호 만료 처리 — 매일 post_market에서 호출"""
    cutoff = last_analysis_cutoff_utc()
    result = await session.execute(
        update(Signal)
        .where(Signal.market == market)
        .where(Signal.pending == True)
        .where(Signal.created_at < cutoff)
        .values(pending=False)
    )
    await session.commit()
    return result.rowcount


async def get_win_rate_stats(session: AsyncSession, limit: int = 50) -> dict:
    """최근 N거래 승률 및 평균 수익/손실 (포지션 사이징용)"""
    trades = await get_recent_trades(session, limit)
    if not trades:
        return {"win_rate": 0.5, "avg_win_pct": 0.07, "avg_loss_pct": 0.05}

    wins = [t for t in trades if (t.profit_loss or 0) > 0]
    losses = [t for t in trades if (t.profit_loss or 0) <= 0]
    win_rate = len(wins) / len(trades)
    avg_win_pct = sum(t.profit_loss_pct or 0 for t in wins) / len(wins) if wins else 0.02
    avg_loss_pct = abs(sum(t.profit_loss_pct or 0 for t in losses) / len(losses)) if losses else 0.02
    return {
        "win_rate": win_rate,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
    }
