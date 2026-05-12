import asyncio
import logging
from datetime import datetime

import pandas as pd
import yaml

from config.settings import settings
from data.fetcher import fetcher
from kis.account import kis_account
from kis.websocket_client import kis_ws, Tick
from repository.database import AsyncSessionLocal
from repository.models import Signal
from repository.queries import (
    get_open_positions, get_position, get_today_realized_pnl, save_signal,
    get_today_trade_count, get_today_signal_count, get_today_sell_count,
)
from risk.portfolio_guard import portfolio_guard
from risk.stop_loss import should_stop, update_trailing_stop
from strategy.multi_agent_strategy import multi_agent_strategy
from strategy.regime_detector import detect_regime, MarketRegime
from execution.order_manager import order_manager
from data.screener import stock_screener
from strategy.agents.decision_agent import TradeSignal
from core.notifier import notify_buy, notify_sell, notify_daily_summary, notify_pre_market_summary
from core.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = "config/watchlist.yaml"


class Orchestrator:
    def __init__(self):
        self._trading_active = False
        self._ohlcv_buffer: dict[str, pd.DataFrame] = {}       # 일봉 (장전 로드)
        self._pending_signals: dict[str, TradeSignal] = {}     # 장전 분석 결과
        self._watchlist: list[dict] = []
        self._kospi_df = None
        self._cached_balance: dict = {"available_cash": 0, "total_eval": 0, "unrealized_pnl": 0}

    def _load_watchlist_config(self) -> dict:
        with open(_WATCHLIST_PATH) as f:
            return yaml.safe_load(f)

    async def pre_market_setup(self) -> None:
        """08:30 장 시작 전 준비 — 일봉 데이터 로드 + 차트 이미지 분석"""
        if not is_trading_day():
            logger.info("휴장일 — 장전 준비 스킵")
            return
        logger.info("장전 준비 시작")
        self._pending_signals.clear()

        try:
            balance = await kis_account.get_balance()
            self._cached_balance = balance
            portfolio_guard.update_peak(balance["total_eval"])
        except Exception as e:
            logger.warning(f"장전 잔고 조회 실패 — 캐시 사용: {e}")
            balance = self._cached_balance

        if balance.get("available_cash", 0) < 100_000:
            logger.info(f"가용 잔고 부족 ({balance.get('available_cash', 0):,}원) — 분석 스킵")
            return

        drawdown_pct = portfolio_guard.get_drawdown_pct(balance["total_eval"])

        self._kospi_df = await fetcher.fetch_kospi(250)

        regime = detect_regime(self._kospi_df)

        config = self._load_watchlist_config()
        if config.get("mode") == "auto":
            screened = await stock_screener.run(regime=regime)
            self._watchlist = screened if screened else config.get("stocks", [])
            logger.info(f"자동 스크리닝 완료: {len(self._watchlist)}개 종목 선정")
        else:
            self._watchlist = config.get("stocks", [])
            logger.info(f"수동 워치리스트: {len(self._watchlist)}개 종목")

        # 일봉 OHLCV 로드
        for stock in self._watchlist:
            code = stock["code"]
            try:
                df = await fetcher.fetch_daily(code, 120)
                self._ohlcv_buffer[code] = df
                logger.debug(f"일봉 로드: {code}")
            except Exception as e:
                logger.warning(f"일봉 로드 실패 {code}: {e}")

        # 하락장이면 분석 전체 스킵
        if regime == MarketRegime.TRENDING_DOWN:
            logger.info("하락장 레짐 — 스윙 매수 분석 전체 스킵")
            return

        # 실제 포지션/손익 조회
        async with AsyncSessionLocal() as session:
            open_pos_count = len(await get_open_positions(session))
            today_pnl = await get_today_realized_pnl(session)
        daily_pnl_pct = today_pnl / balance["total_eval"] if balance["total_eval"] > 0 else 0.0

        # 차트 이미지 분석 (최대 3개 동시)
        sem = asyncio.Semaphore(3)

        async def _analyze_stock(stock: dict) -> None:
            code = stock["code"]
            name = stock.get("name", code)
            df = self._ohlcv_buffer.get(code)
            if df is None or df.empty or len(df) < 60:
                return
            async with sem:
                try:
                    signal = await multi_agent_strategy.evaluate(
                        stock_code=code,
                        stock_name=name,
                        ohlcv_df=df,
                        kospi_df=self._kospi_df,
                        open_positions=open_pos_count,
                        daily_pnl_pct=daily_pnl_pct,
                        drawdown_pct=drawdown_pct,
                    )
                    if signal.action == "buy":
                        self._pending_signals[code] = signal
                        logger.info(f"[{code}] 스윙 매수 신호 → 대기열 등록 ({signal.reasoning[:60]})")
                except Exception as e:
                    logger.warning(f"장전 분석 실패 {code}: {e}")

        tasks = [_analyze_stock(s) for s in self._watchlist]
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"장전 분석 완료: {len(self._pending_signals)}개 매수 신호")

        signal_list = [
            {
                "code": code,
                "name": next((s["name"] for s in self._watchlist if s["code"] == code), code),
                "confidence": sig.chart_confidence or 0.0,
                "reasoning": sig.reasoning[:60],
            }
            for code, sig in self._pending_signals.items()
        ]
        await notify_pre_market_summary(len(self._watchlist), signal_list)

    async def market_open(self) -> None:
        """09:00 장 시작"""
        if not is_trading_day():
            logger.info("휴장일 — 장 시작 스킵")
            return
        logger.info("장 시작 — 매매 활성화")
        self._trading_active = True

        if not settings.is_paper:
            codes = [s["code"] for s in self._watchlist]
            kis_ws.subscribe(codes)
            kis_ws.add_handler(self._on_tick)
        else:
            logger.info("모의매매 모드 — 손절 폴링 시작")
            asyncio.create_task(self._polling_loop())

        try:
            balance = await kis_account.get_balance()
            self._cached_balance = balance
            portfolio_guard.update_peak(balance["total_eval"])
        except Exception as e:
            logger.warning(f"장 시작 잔고 조회 실패 — 캐시 사용: {e}")
            balance = self._cached_balance

        # 장전 매수 신호 실행
        if self._pending_signals:
            logger.info(f"장전 매수 신호 실행: {len(self._pending_signals)}개")
            await self._execute_pending_signals()

    async def _execute_pending_signals(self) -> None:
        """장전에 수집된 매수 신호 일괄 집행"""
        from kis.market_data import market_data as md

        async with AsyncSessionLocal() as session:
            balance = self._cached_balance
            open_positions = await get_open_positions(session)
            open_pos_count = len(open_positions)
            already_held = {pos.stock_code for pos in open_positions}

            today_pnl = await get_today_realized_pnl(session)
            daily_pnl_pct = today_pnl / balance["total_eval"] if balance["total_eval"] > 0 else 0.0

            # 잔고를 매수 대상 종목 수로 균등 분할 (이미 보유 중인 종목 제외)
            eligible_codes = [c for c in self._pending_signals if c not in already_held]
            n = max(1, len(eligible_codes))
            per_stock_budget = int(balance["available_cash"] / n)
            logger.info(f"시드 균등 분할: {balance['available_cash']:,}원 ÷ {n}종목 = {per_stock_budget:,}원/종목")

            for code, signal in list(self._pending_signals.items()):
                if code in already_held:
                    logger.debug(f"{code} 이미 보유 중 — 스킵")
                    continue

                allowed, reason = portfolio_guard.allows_new_entry(
                    open_pos_count, balance["total_eval"], daily_pnl_pct
                )
                if not allowed:
                    logger.info(f"포트폴리오 한도 도달 — 이후 신호 중단: {reason}")
                    break

                try:
                    info = await md.get_current_price(code)
                    current_price = info["price"]
                except Exception as e:
                    logger.warning(f"현재가 조회 실패 {code}: {e}")
                    continue

                # 균등 예산과 실제 가용 잔고 중 작은 값으로 수량 계산 (이전 체결 후 잔고 감소 반영)
                effective_budget = min(per_stock_budget, balance["available_cash"])
                qty = int(effective_budget * 0.95) // current_price

                if qty <= 0:
                    logger.info(f"매수 수량 0 → 스킵: {code} (예산 {per_stock_budget:,}원, 현재가 {current_price:,}원)")
                    continue

                stock_name = next((s["name"] for s in self._watchlist if s["code"] == code), code)
                signal_record = Signal(
                    stock_code=code,
                    ta_score=signal.ta_score,
                    chart_verdict=signal.chart_verdict,
                    chart_confidence=signal.chart_confidence,
                    risk_level=signal.risk_level,
                    final_action=signal.action,
                    position_size_pct=signal.position_ratio,
                    reasoning=signal.reasoning,
                )
                await save_signal(session, signal_record)
                result, fill_price = await order_manager.execute_buy(
                    session, code, stock_name, qty, signal, current_price
                )
                if not result.success:
                    logger.warning(f"매수 실패 — 텔레그램 알림 생략: {code}")
                    continue
                notify_stop = round(fill_price * (1 - signal.stop_pct))
                notify_target = round(fill_price * (1 + signal.target_pct))
                await notify_buy(
                    code, stock_name, qty, fill_price,
                    notify_target, notify_stop, signal.chart_confidence,
                )
                open_pos_count += 1
                await self._refresh_balance()
                balance = self._cached_balance

        self._pending_signals.clear()

    async def market_close(self) -> None:
        """15:30 장 종료"""
        if not is_trading_day():
            logger.info("휴장일 — 장 종료 스킵")
            return
        logger.info("장 종료 처리")
        self._trading_active = False
        await kis_ws.stop()
        self._pending_signals.clear()

    async def pre_close(self) -> None:
        """15:20 장마감 전 스윙 포지션 점검"""
        if not is_trading_day():
            logger.info("휴장일 — 장마감 전 점검 스킵")
            return
        logger.info("장마감 전 스윙 포지션 점검")

    async def post_market(self) -> None:
        """15:35 장 후 정산"""
        if not is_trading_day():
            logger.info("휴장일 — 장 후 정산 스킵")
            return
        logger.info("장 후 정산 중")
        async with AsyncSessionLocal() as session:
            pnl = await get_today_realized_pnl(session)
            logger.info(f"오늘 실현 손익: {pnl:+,}원")
            signal_count = await get_today_signal_count(session)
            trade_count = await get_today_trade_count(session)
            sell_count = await get_today_sell_count(session)
            await notify_daily_summary(
                signals=signal_count,
                trades=trade_count,
                sells=sell_count,
                pnl=pnl,
            )

    async def _polling_loop(self) -> None:
        """1분마다 보유 종목 현재가 조회 → 익절/손절 체크 (스윙 모드)"""
        from kis.market_data import market_data as md
        sem = asyncio.Semaphore(5)

        async def _check_with_sem(code: str) -> None:
            async with sem:
                try:
                    info = await md.get_current_price(code)
                    await self._check_exit_conditions(code, info["price"])
                except Exception as e:
                    logger.debug(f"현재가 폴링 실패 {code}: {e}")

        while self._trading_active:
            await asyncio.sleep(60)
            if not self._trading_active:
                break

            async with AsyncSessionLocal() as session:
                held_positions = await get_open_positions(session)
            held_codes = {pos.stock_code for pos in held_positions}

            tasks = [_check_with_sem(code) for code in held_codes]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_balance(self) -> None:
        try:
            self._cached_balance = await kis_account.get_balance()
        except Exception as e:
            logger.warning(f"잔고 갱신 실패 — 캐시 유지: {e}")

    async def _check_exit_conditions(self, code: str, current_price: int) -> None:
        """보유 포지션 익절/손절 체크 및 트레일링 스톱 업데이트"""
        async with AsyncSessionLocal() as session:
            pos = await get_position(session, code)
            if pos is None:
                return

            # 익절 체크 — 절반 먼저 매도, 나머지는 손익분기 trailing stop으로 유지
            if pos.target_price and current_price >= pos.target_price:
                half_qty = pos.qty // 2
                if half_qty > 0 and pos.qty > 1:
                    # execute_partial_sell이 session.commit()을 호출하면 pos 속성이 expire됨
                    # async 컨텍스트에서 재로드 불가하므로 commit 전에 미리 저장
                    avg_price = int(pos.avg_price)
                    stock_name = pos.stock_name
                    logger.info(f"절반 익절: {code} x{half_qty} @ {current_price} (목표가: {pos.target_price})")
                    _, fill_price = await order_manager.execute_partial_sell(
                        session, code, half_qty, current_price, "partial_take_profit"
                    )
                    pnl = (fill_price - avg_price) * half_qty
                    await notify_sell(code, stock_name, half_qty, fill_price, "partial_take_profit", pnl)
                    # 나머지 절반: 손익분기(avg_price)로 stop 이동, trailing 비율 확대, target 제거
                    pos.stop_price = avg_price
                    pos.trail_pct = settings.partial_tp_trail_pct
                    pos.target_price = None
                    await session.commit()
                else:
                    # 1주만 보유 시 전량 익절
                    qty = pos.qty
                    avg_price = int(pos.avg_price)
                    stock_name = pos.stock_name
                    logger.info(f"익절 발동: {code} @ {current_price} (목표가: {pos.target_price})")
                    _, fill_price = await order_manager.execute_sell(
                        session, code, qty, current_price, "take_profit"
                    )
                    pnl = (fill_price - avg_price) * qty
                    await notify_sell(code, stock_name, qty, fill_price, "take_profit", pnl)
                await self._refresh_balance()
                return

            # 트레일링 스톱 업데이트
            if pos.stop_type == "trailing":
                new_stop = update_trailing_stop(pos, current_price)
                updated_highest = max(pos.highest_price or 0, current_price)
                # stop이 바뀌거나 신고점 갱신 시 모두 커밋 (highest_price 항상 최신 유지)
                if new_stop != int(pos.stop_price) or updated_highest != (pos.highest_price or 0):
                    pos.stop_price = new_stop
                    pos.highest_price = updated_highest
                    await session.commit()

            # 손절 체크
            if should_stop(pos, current_price):
                qty = pos.qty
                avg_price = int(pos.avg_price)
                stock_name = pos.stock_name
                logger.info(f"손절 발동: {code} @ {current_price} (손절가: {pos.stop_price})")
                _, fill_price = await order_manager.execute_sell(
                    session, code, qty, current_price, "stop_loss"
                )
                pnl = (fill_price - avg_price) * qty
                await notify_sell(code, stock_name, qty, fill_price, "stop_loss", pnl)
                await self._refresh_balance()
                return

    async def _on_tick(self, tick: Tick) -> None:
        """WebSocket tick 수신 처리 (실전) — 익절/손절 체크"""
        await self._check_exit_conditions(tick.code, tick.price)

    async def run(self) -> None:
        """메인 실행 루프"""
        from core.scheduler import create_scheduler
        from repository.database import init_db
        from execution.paper_trader import log_paper_mode

        log_paper_mode()
        await init_db()

        scheduler = create_scheduler(self)
        scheduler.start()
        logger.info("스케줄러 시작됨")

        now = datetime.now()
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now < market_close:
            if now >= now.replace(hour=8, minute=30, second=0, microsecond=0):
                logger.info("08:30 이후 시작 — pre_market_setup 즉시 실행")
                await self.pre_market_setup()
            if now >= now.replace(hour=9, minute=0, second=0, microsecond=0):
                logger.info("09:00 이후 시작 — market_open 즉시 실행")
                await self.market_open()

        if not settings.is_paper:
            asyncio.create_task(kis_ws.connect_and_run())

        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass
        finally:
            scheduler.shutdown()
            await kis_ws.stop()
            from kis.rest_client import kis_client
            await kis_client.close()
            logger.info("Orchestrator 종료")


orchestrator = Orchestrator()
