import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from kis.overseas_order import overseas_order, OverseasOrderResult
from repository.models import Trade, Position
from repository.queries import save_trade, save_position, delete_position, get_position, get_open_trade
from strategy.agents.decision_agent import TradeSignal

logger = logging.getLogger(__name__)


class USOrderManager:
    async def execute_buy(
        self,
        session: AsyncSession,
        ticker: str,
        name: str,
        qty: float,
        signal: TradeSignal,
        current_price: float,
    ) -> tuple[OverseasOrderResult, float]:
        """소수점 매수 실행. (OrderResult, 실제_체결가_USD) 반환."""
        result = await overseas_order.fractional_buy(ticker, qty)
        if not result.success:
            logger.error(f"US Buy failed: {ticker} x{qty:.4f}")
            return result, current_price

        fill_price = result.fill_price or current_price
        if result.fill_price and abs(result.fill_price - current_price) / current_price > 0.001:
            logger.info(f"US 체결가 반영: {ticker} 추정가 ${current_price:.2f} → 실제 ${fill_price:.2f}")

        stop_pct = signal.stop_pct if signal.stop_pct > 0 else settings.us_swing_stop_pct
        target_pct = signal.target_pct if signal.target_pct > 0 else 0.08
        actual_stop = round(fill_price * (1 - stop_pct), 4)
        actual_target = round(fill_price * (1 + target_pct), 4)

        position = Position(
            market="US",
            stock_code=ticker,
            stock_name=name,
            qty=qty,
            avg_price=fill_price,
            stop_price=actual_stop,
            stop_type="trailing",
            target_price=actual_target,
            trail_pct=round(stop_pct, 4),
            highest_price=fill_price,
            trade_type="swing",
            opened_at=datetime.utcnow(),
        )
        await save_position(session, position)

        trade = Trade(
            market="US",
            stock_code=ticker,
            stock_name=name,
            status="open",
            entry_at=datetime.utcnow(),
            entry_price=fill_price,
            entry_qty=qty,
            stop_price=actual_stop,
            stop_type="trailing",
        )
        await save_trade(session, trade)
        logger.info(f"US Buy: {ticker} x{qty:.4f} @ ${fill_price:.2f}, stop=${actual_stop:.2f}, target=${actual_target:.2f}")
        return result, fill_price

    async def execute_sell(
        self,
        session: AsyncSession,
        ticker: str,
        qty: float,
        current_price: float,
        exit_reason: str,
    ) -> tuple[OverseasOrderResult, float]:
        """전량 매도. (OrderResult, 실제_체결가_USD) 반환."""
        result = await overseas_order.fractional_sell(ticker, qty)
        if not result.success:
            logger.error(f"US Sell failed: {ticker} x{qty:.4f}")
            return result, current_price

        fill_price = result.fill_price or current_price

        position = await get_position(session, ticker, market="US")
        if position is None:
            logger.warning(f"US 포지션 없음 — 매도 기록 누락: {ticker}")
        if position:
            pnl = (fill_price - float(position.avg_price)) * qty
            pnl_pct = (fill_price - float(position.avg_price)) / float(position.avg_price)
            trade = await get_open_trade(session, ticker, market="US")
            if trade:
                trade.status = "closed"
                trade.exit_at = datetime.utcnow()
                trade.exit_price = fill_price
                trade.profit_loss = pnl
                trade.profit_loss_pct = pnl_pct
                trade.exit_reason = exit_reason
                await session.commit()
            else:
                await save_trade(session, Trade(
                    market="US",
                    stock_code=ticker,
                    stock_name=position.stock_name,
                    status="closed",
                    entry_at=position.opened_at,
                    entry_price=float(position.avg_price),
                    entry_qty=qty,
                    exit_at=datetime.utcnow(),
                    exit_price=fill_price,
                    profit_loss=pnl,
                    profit_loss_pct=pnl_pct,
                    exit_reason=exit_reason,
                ))
            await delete_position(session, ticker, market="US")

        logger.info(f"US Sell: {ticker} x{qty:.4f} @ ${fill_price:.2f} ({exit_reason})")
        return result, fill_price

    async def execute_partial_sell(
        self,
        session: AsyncSession,
        ticker: str,
        qty: float,
        current_price: float,
        exit_reason: str,
    ) -> tuple[OverseasOrderResult, float]:
        """부분 매도 — 포지션 유지하면서 qty만 매도."""
        result = await overseas_order.fractional_sell(ticker, qty)
        if not result.success:
            logger.error(f"US Partial sell failed: {ticker} x{qty:.4f}")
            return result, current_price

        fill_price = result.fill_price or current_price

        position = await get_position(session, ticker, market="US")
        if position is None:
            logger.warning(f"US 포지션 없음 — 부분매도 기록 누락: {ticker}")
        if position:
            pnl = (fill_price - float(position.avg_price)) * qty
            pnl_pct = (fill_price - float(position.avg_price)) / float(position.avg_price)
            await save_trade(session, Trade(
                market="US",
                stock_code=ticker,
                stock_name=position.stock_name,
                status="closed",
                entry_at=position.opened_at,
                entry_price=float(position.avg_price),
                entry_qty=qty,
                exit_at=datetime.utcnow(),
                exit_price=fill_price,
                profit_loss=pnl,
                profit_loss_pct=pnl_pct,
                exit_reason=exit_reason,
            ))
            new_qty = round(float(position.qty) - qty, 4)
            if new_qty < 0:
                logger.warning(f"US 부분매도 수량({qty}) > 포지션 수량({position.qty}) — 0으로 클램핑: {ticker}")
            position.qty = max(0.0, new_qty)
            await session.commit()

        logger.info(f"US Partial sell: {ticker} x{qty:.4f} @ ${fill_price:.2f} ({exit_reason})")
        return result, fill_price


us_order_manager = USOrderManager()
