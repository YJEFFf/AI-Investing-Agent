import asyncio
import logging
from datetime import datetime

import pandas as pd
import yaml

from config.settings import settings
from data.us_watchlist_updater import update_nasdaq100_watchlist
from kis.overseas_account import overseas_account
from kis.overseas_market_data import overseas_market_data
from repository.database import AsyncSessionLocal
from repository.models import Signal
from repository.queries import (
    get_open_positions_by_market, get_position, get_today_realized_pnl_by_market,
    save_signal, get_today_trade_count_by_market, get_today_sell_count_by_market,
)
from risk.portfolio_guard import PortfolioGuard
from risk.stop_loss import should_stop, update_trailing_stop
from strategy.multi_agent_strategy import multi_agent_strategy
from strategy.regime_detector import detect_regime, MarketRegime
from execution.us_order_manager import us_order_manager
from strategy.agents.decision_agent import TradeSignal
from core.us_notifier import (
    notify_us_buy, notify_us_sell, notify_us_pre_market_summary,
    notify_us_daily_summary, notify_us_watchlist_update,
)
from core.trading_calendar import is_us_trading_day

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = "config/us_watchlist.yaml"


class USOrchestrator:
    def __init__(self):
        self._trading_active = False
        self._ohlcv_buffer: dict[str, pd.DataFrame] = {}
        self._pending_signals: dict[str, TradeSignal] = {}
        self._watchlist: list[dict] = []
        self._nasdaq_df: pd.DataFrame | None = None
        self._cached_balance: dict = {"available_usd": 0.0, "total_eval_usd": 0.0}
        self._portfolio_guard = PortfolioGuard()

    def _load_watchlist(self) -> list[dict]:
        with open(_WATCHLIST_PATH) as f:
            data = yaml.safe_load(f) or {}
        return data.get("stocks", [])

    async def update_watchlist(self) -> None:
        """매주 월요일 21:00 NASDAQ 100 갱신."""
        result = await update_nasdaq100_watchlist()
        if result.get("added") or result.get("removed"):
            await notify_us_watchlist_update(result["added"], result["removed"])

    async def pre_market_setup(self) -> None:
        """22:30 KST — 일봉 로드 + Claude Vision 분석."""
        if not is_us_trading_day():
            logger.info("US 휴장일 — 장전 준비 스킵")
            return
        logger.info("US 장전 준비 시작")
        self._pending_signals.clear()

        try:
            balance = await overseas_account.get_balance()
            self._cached_balance = balance
            self._portfolio_guard.update_peak(balance["total_eval_usd"])
        except Exception as e:
            logger.warning(f"US 잔고 조회 실패 — 캐시 사용: {e}")
            balance = self._cached_balance

        self._watchlist = self._load_watchlist()
        logger.info(f"US 워치리스트: {len(self._watchlist)}개 종목")

        # QQQ 일봉으로 나스닥 레짐 감지 (시장 전체 방향 판단)
        try:
            self._nasdaq_df = await overseas_market_data.get_daily_ohlcv("QQQ", 250)
        except Exception as e:
            logger.warning(f"QQQ 데이터 로드 실패: {e}")
            self._nasdaq_df = None

        if self._nasdaq_df is not None:
            regime = detect_regime(self._nasdaq_df)
            if regime == MarketRegime.TRENDING_DOWN:
                logger.info("US 하락장 레짐 — 스윙 매수 전체 스킵")
                return
        else:
            regime = MarketRegime.RANGING

        # 일봉 OHLCV 로드
        for stock in self._watchlist:
            ticker = stock["ticker"]
            try:
                df = await overseas_market_data.get_daily_ohlcv(ticker, 120)
                self._ohlcv_buffer[ticker] = df
            except Exception as e:
                logger.warning(f"US 일봉 로드 실패 {ticker}: {e}")

        async with AsyncSessionLocal() as session:
            open_positions = await get_open_positions_by_market(session, "US")
            open_pos_count = len(open_positions)
            today_pnl = await get_today_realized_pnl_by_market(session, "US")
        daily_pnl_pct = today_pnl / balance["total_eval_usd"] if balance["total_eval_usd"] > 0 else 0.0
        drawdown_pct = self._portfolio_guard.get_drawdown_pct(balance["total_eval_usd"])

        # 차트 이미지 분석 (최대 3개 동시)
        sem = asyncio.Semaphore(3)

        async def _analyze(stock: dict) -> None:
            ticker = stock["ticker"]
            name = stock.get("name", ticker)
            df = self._ohlcv_buffer.get(ticker)
            if df is None or df.empty or len(df) < 60:
                return
            async with sem:
                try:
                    signal = await multi_agent_strategy.evaluate(
                        stock_code=ticker,
                        stock_name=name,
                        ohlcv_df=df,
                        kospi_df=self._nasdaq_df if self._nasdaq_df is not None else df,
                        open_positions=open_pos_count,
                        daily_pnl_pct=daily_pnl_pct,
                        drawdown_pct=drawdown_pct,
                    )
                    if signal.action == "buy":
                        self._pending_signals[ticker] = signal
                        logger.info(f"[US {ticker}] 매수 신호 → 대기열 등록")
                except Exception as e:
                    logger.warning(f"US 분석 실패 {ticker}: {e}")

        await asyncio.gather(*[_analyze(s) for s in self._watchlist], return_exceptions=True)
        logger.info(f"US 장전 분석 완료: {len(self._pending_signals)}개 매수 신호")

        signal_list = [
            {
                "ticker": ticker,
                "name": next((s["name"] for s in self._watchlist if s["ticker"] == ticker), ticker),
                "confidence": sig.chart_confidence or 0.0,
            }
            for ticker, sig in self._pending_signals.items()
        ]
        await notify_us_pre_market_summary(len(self._watchlist), signal_list)

    async def market_open(self) -> None:
        """23:30 KST — 매수 신호 실행 + 손절 폴링 시작."""
        if not is_us_trading_day():
            logger.info("US 휴장일 — 장 시작 스킵")
            return
        logger.info("US 장 시작")
        self._trading_active = True
        asyncio.create_task(self._polling_loop())

        try:
            balance = await overseas_account.get_balance()
            self._cached_balance = balance
            self._portfolio_guard.update_peak(balance["total_eval_usd"])
        except Exception as e:
            logger.warning(f"US 장 시작 잔고 조회 실패 — 캐시 사용: {e}")

        if self._pending_signals:
            logger.info(f"US 매수 신호 실행: {len(self._pending_signals)}개")
            await self._execute_pending_signals()

    async def _execute_pending_signals(self) -> None:
        async with AsyncSessionLocal() as session:
            balance = self._cached_balance
            open_positions = await get_open_positions_by_market(session, "US")
            open_pos_count = len(open_positions)
            already_held = {pos.stock_code for pos in open_positions}

            today_pnl = await get_today_realized_pnl_by_market(session, "US")
            daily_pnl_pct = today_pnl / balance["total_eval_usd"] if balance["total_eval_usd"] > 0 else 0.0

            # 가용 USD를 매수 대상 종목 수로 균등 분할
            eligible = [t for t in self._pending_signals if t not in already_held]
            n = max(1, len(eligible))
            per_stock_usd = balance["available_usd"] / n
            logger.info(f"US 시드 균등 분할: ${balance['available_usd']:.2f} ÷ {n}종목 = ${per_stock_usd:.2f}/종목")

            for ticker, signal in list(self._pending_signals.items()):
                if ticker in already_held:
                    continue

                allowed, reason = self._portfolio_guard.allows_new_entry(
                    open_pos_count, balance["total_eval_usd"], daily_pnl_pct
                )
                if not allowed:
                    logger.info(f"US 포트폴리오 한도 도달: {reason}")
                    break

                try:
                    info = await overseas_market_data.get_current_price(ticker)
                    current_price = info["price"]
                except Exception as e:
                    logger.warning(f"US 현재가 조회 실패 {ticker}: {e}")
                    continue

                # 소수점 수량 계산 (슬리피지 0.5%)
                qty = round((per_stock_usd * 0.995) / current_price, 4)
                if qty <= 0.0001:
                    logger.info(f"US 매수 수량 너무 작음 → 스킵: {ticker}")
                    continue

                name = next((s["name"] for s in self._watchlist if s["ticker"] == ticker), ticker)
                signal_record = Signal(
                    market="US",
                    stock_code=ticker,
                    ta_score=signal.ta_score,
                    chart_verdict=signal.chart_verdict,
                    chart_confidence=signal.chart_confidence,
                    risk_level=signal.risk_level,
                    final_action=signal.action,
                    position_size_pct=signal.position_ratio,
                    reasoning=signal.reasoning,
                )
                await save_signal(session, signal_record)

                _, fill_price = await us_order_manager.execute_buy(
                    session, ticker, name, qty, signal, current_price
                )
                notify_stop = round(fill_price * (1 - signal.stop_pct), 4)
                notify_target = round(fill_price * (1 + signal.target_pct), 4)
                await notify_us_buy(ticker, name, qty, fill_price, notify_target, notify_stop, signal.chart_confidence)
                open_pos_count += 1
                try:
                    self._cached_balance = await overseas_account.get_balance()
                    balance = self._cached_balance
                except Exception:
                    pass

        self._pending_signals.clear()

    async def market_close(self) -> None:
        """06:10 KST — 장 종료."""
        if not is_us_trading_day():
            return
        logger.info("US 장 종료")
        self._trading_active = False
        self._pending_signals.clear()

    async def post_market(self) -> None:
        """06:15 KST — 일일 결산."""
        if not is_us_trading_day():
            return
        async with AsyncSessionLocal() as session:
            pnl = await get_today_realized_pnl_by_market(session, "US")
            trades = await get_today_trade_count_by_market(session, "US")
            sells = await get_today_sell_count_by_market(session, "US")
        logger.info(f"US 오늘 실현 손익: ${pnl:+.2f}")
        await notify_us_daily_summary(
            signals=len(self._pending_signals),
            trades=trades,
            sells=sells,
            pnl=pnl,
        )

    async def _polling_loop(self) -> None:
        """1분마다 보유 종목 손절/익절 체크."""
        sem = asyncio.Semaphore(5)

        async def _check(ticker: str) -> None:
            async with sem:
                try:
                    info = await overseas_market_data.get_current_price(ticker)
                    await self._check_exit_conditions(ticker, info["price"])
                except Exception as e:
                    logger.debug(f"US 현재가 폴링 실패 {ticker}: {e}")

        while self._trading_active:
            await asyncio.sleep(60)
            if not self._trading_active:
                break
            async with AsyncSessionLocal() as session:
                positions = await get_open_positions_by_market(session, "US")
            held = {pos.stock_code for pos in positions}
            await asyncio.gather(*[_check(t) for t in held], return_exceptions=True)

    async def _check_exit_conditions(self, ticker: str, current_price: float) -> None:
        """USD 기준 익절/손절 체크 및 트레일링 스톱 업데이트."""
        async with AsyncSessionLocal() as session:
            pos = await get_position(session, ticker)
            if pos is None:
                return

            # 익절 체크
            if pos.target_price and current_price >= float(pos.target_price):
                half_qty = round(float(pos.qty) / 2, 4)
                avg_price = float(pos.avg_price)
                name = pos.stock_name or ticker

                if half_qty > 0.0001 and float(pos.qty) > 0.0002:
                    logger.info(f"US 절반 익절: {ticker} x{half_qty:.4f} @ ${current_price:.2f}")
                    _, fill_price = await us_order_manager.execute_partial_sell(
                        session, ticker, half_qty, current_price, "partial_take_profit"
                    )
                    pnl = (fill_price - avg_price) * half_qty
                    await notify_us_sell(ticker, name, half_qty, fill_price, "partial_take_profit", pnl)
                    pos.stop_price = avg_price
                    pos.trail_pct = settings.partial_tp_trail_pct
                    pos.target_price = None
                    await session.commit()
                else:
                    qty = float(pos.qty)
                    logger.info(f"US 전량 익절: {ticker} @ ${current_price:.2f}")
                    _, fill_price = await us_order_manager.execute_sell(
                        session, ticker, qty, current_price, "take_profit"
                    )
                    pnl = (fill_price - avg_price) * qty
                    await notify_us_sell(ticker, name, qty, fill_price, "take_profit", pnl)
                try:
                    self._cached_balance = await overseas_account.get_balance()
                except Exception:
                    pass
                return

            # 트레일링 스톱 업데이트
            updated = update_trailing_stop(pos, current_price)
            if updated:
                await session.commit()

            # 손절 체크
            if should_stop(pos, current_price):
                qty = float(pos.qty)
                avg_price = float(pos.avg_price)
                name = pos.stock_name or ticker
                logger.info(f"US 손절: {ticker} @ ${current_price:.2f} (stop=${float(pos.stop_price):.2f})")
                _, fill_price = await us_order_manager.execute_sell(
                    session, ticker, qty, current_price, "stop_loss"
                )
                pnl = (fill_price - avg_price) * qty
                await notify_us_sell(ticker, name, qty, fill_price, "stop_loss", pnl)
                try:
                    self._cached_balance = await overseas_account.get_balance()
                except Exception:
                    pass

    async def run(self) -> None:
        from core.us_scheduler import create_us_scheduler
        scheduler = create_us_scheduler(self)
        scheduler.start()
        logger.info("US 트레이딩 에이전트 시작")
        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            scheduler.shutdown()
            logger.info("US 트레이딩 에이전트 종료")


us_orchestrator = USOrchestrator()
