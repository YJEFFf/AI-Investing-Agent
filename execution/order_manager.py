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
    ) -> OrderResult:
        result = await kis_order.market_buy(stock_code, qty)
        if not result.success:
            logger.error(f"Buy order failed: {stock_code} x{qty}")
            return result

        trail_pct = (current_price - signal.stop_price) / current_price if current_price > 0 else 0.05

        position = Position(
            stock_code=stock_code,
            stock_name=stock_name,
            qty=qty,
            avg_price=current_price,
            stop_price=int(signal.stop_price),
            stop_type=signal.stop_type,
            target_price=signal.target_price if signal.target_price > 0 else None,
            trail_pct=round(trail_pct, 4),
            highest_price=current_price,
            trade_type="swing",
            opened_at=datetime.utcnow(),
        )
        await save_position(session, position)

        trade = Trade(
            stock_code=stock_code,
            stock_name=stock_name,
            status="open",
            entry_at=datetime.utcnow(),
            entry_price=current_price,
            entry_qty=qty,
            stop_price=int(signal.stop_price),
            stop_type=signal.stop_type,
        )
        await save_trade(session, trade)
        logger.info(f"Buy executed: {stock_code} x{qty} @ {current_price}, stop={signal.stop_price}, target={signal.target_price}")
        return result

    async def execute_sell(
        self,
        session: AsyncSession,
        stock_code: str,
        qty: int,
        current_price: int,
        exit_reason: str,
    ) -> OrderResult:
        result = await kis_order.market_sell(stock_code, qty)
        if not result.success:
            logger.error(f"Sell order failed: {stock_code} x{qty}")
            return result

        position = await get_position(session, stock_code)
        if position:
            pnl = (current_price - int(position.avg_price)) * qty
            pnl_pct = (current_price - position.avg_price) / position.avg_price
            trade = Trade(
                stock_code=stock_code,
                stock_name=position.stock_name,
                status="closed",
                entry_at=position.opened_at,
                entry_price=int(position.avg_price),
                entry_qty=qty,
                exit_at=datetime.utcnow(),
                exit_price=current_price,
                profit_loss=pnl,
                profit_loss_pct=pnl_pct,
                exit_reason=exit_reason,
            )
            await save_trade(session, trade)
            await delete_position(session, stock_code)

        logger.info(f"Sell executed: {stock_code} x{qty} @ {current_price} ({exit_reason})")
        return result


order_manager = OrderManager()
