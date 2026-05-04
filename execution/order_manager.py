import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from kis.order import kis_order, OrderResult
from repository.models import Trade, Position
from repository.queries import save_trade, save_position, delete_position, get_position
from strategy.agents.decision_agent import TradeSignal

logger = logging.getLogger(__name__)


class OrderManager:
    async def execute_buy(
        self,
        session: AsyncSession,
        stock_code: str,
        stock_name: str,
        qty: int,
        signal: TradeSignal,
        current_price: int,
    ) -> tuple[OrderResult, int]:
        """매수 실행. (OrderResult, 실제_체결가) 반환."""
        result = await kis_order.market_buy(stock_code, qty)
        if not result.success:
            logger.error(f"Buy order failed: {stock_code} x{qty}")
            return result, current_price

        fill_price = result.fill_price or current_price
        if result.fill_price and result.fill_price != current_price:
            logger.info(f"체결가 반영: {stock_code} 추정가 {current_price:,} → 실제 {fill_price:,}원")

        # 실제 체결가 기준으로 stop/target 재계산
        stop_pct = (current_price - signal.stop_price) / current_price if current_price > 0 else 0.05
        actual_stop = round(fill_price * (1 - stop_pct))
        actual_target = round(fill_price * (signal.target_price / current_price)) if current_price > 0 else signal.target_price
        trail_pct = stop_pct

        position = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            qty=qty,
            avg_price=fill_price,
            stop_price=actual_stop,
            stop_type=signal.stop_type,
            target_price=actual_target if actual_target > 0 else None,
            trail_pct=round(trail_pct, 4),
            highest_price=fill_price,
            trade_type="swing",
            opened_at=datetime.utcnow(),
        )
        await save_position(session, position)

        trade = Trade(
            stock_code=stock_code,
            stock_name=stock_name,
            status="open",
            entry_at=datetime.utcnow(),
            entry_price=fill_price,
            entry_qty=qty,
            stop_price=actual_stop,
            stop_type=signal.stop_type,
        )
        await save_trade(session, trade)
        logger.info(f"Buy executed: {stock_code} x{qty} @ {fill_price:,}, stop={actual_stop:,}, target={actual_target:,}")
        return result, fill_price

    async def execute_sell(
        self,
        session: AsyncSession,
        stock_code: str,
        qty: int,
        current_price: int,
        exit_reason: str,
    ) -> tuple[OrderResult, int]:
        """매도 실행. (OrderResult, 실제_체결가) 반환."""
        result = await kis_order.market_sell(stock_code, qty)
        if not result.success:
            logger.error(f"Sell order failed: {stock_code} x{qty}")
            return result, current_price

        fill_price = result.fill_price or current_price
        if result.fill_price and result.fill_price != current_price:
            logger.info(f"체결가 반영: {stock_code} 추정가 {current_price:,} → 실제 {fill_price:,}원")

        position = await get_position(session, stock_code)
        if position:
            pnl = (fill_price - int(position.avg_price)) * qty
            pnl_pct = (fill_price - position.avg_price) / position.avg_price
            trade = Trade(
                stock_code=stock_code,
                stock_name=position.stock_name,
                status="closed",
                entry_at=position.opened_at,
                entry_price=int(position.avg_price),
                entry_qty=qty,
                exit_at=datetime.utcnow(),
                exit_price=fill_price,
                profit_loss=pnl,
                profit_loss_pct=pnl_pct,
                exit_reason=exit_reason,
            )
            await save_trade(session, trade)
            await delete_position(session, stock_code)

        logger.info(f"Sell executed: {stock_code} x{qty} @ {fill_price:,} ({exit_reason})")
        return result, fill_price


order_manager = OrderManager()
