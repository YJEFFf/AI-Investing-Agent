import asyncio
import logging
import time
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
    get_today_signal_count_by_market,
)
from risk.portfolio_guard import PortfolioGuard
from risk.stop_loss import should_stop
from strategy.multi_agent_strategy import multi_agent_strategy
from strategy.regime_detector import detect_regime, MarketRegime
from strategy.ta_engine import ta_engine
from strategy.signal_scorer import compute_score
from execution.us_order_manager import us_order_manager
from strategy.agents.decision_agent import TradeSignal
from core.us_notifier import (
    notify_us_buy, notify_us_sell, notify_us_sell_fail, notify_us_pre_market_summary,
    notify_us_daily_summary, notify_us_watchlist_update, notify_us_reenter_no_signal,
)
from core.trading_calendar import is_us_trading_day

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = "config/us_watchlist.yaml"
_TA_TOP_N = 40


class USOrchestrator:
    def __init__(self):
        self._trading_active = False
        self._ohlcv_buffer: dict[str, pd.DataFrame] = {}
        self._pending_signals: dict[str, TradeSignal] = {}
        self._watchlist: list[dict] = []
        self._nasdaq_df: pd.DataFrame | None = None
        self._cached_balance: dict = {"available_usd": 0.0, "total_eval_usd": 0.0}
        self._portfolio_guard = PortfolioGuard()
        self._sell_fail_time: dict[str, float] = {}             # 최초 매도 실패 시각 (monotonic) — 5분 후 재시도
        self._sell_perm_lock: set[str] = set()                 # 재시도 후에도 실패 → 장 마감까지 영구 차단
        self._reenter_lock = asyncio.Lock()                    # 재진입 분석 중복 실행 방지
        self._ta_filtered_watchlist: list[dict] = []           # TA 점수 상위 40 (당일)

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

        balance = await self._get_us_balance()
        self._cached_balance = balance
        self._portfolio_guard.update_peak(balance["total_eval_usd"])

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
        else:
            regime = MarketRegime.RANGING

        # 일봉 OHLCV 로드 + TA 필터링은 잔고 유무와 무관하게 항상 실행
        # → _analyze_and_reenter가 당일 최신 데이터를 사용할 수 있도록 보장
        for stock in self._watchlist:
            ticker = stock["ticker"]
            try:
                df = await overseas_market_data.get_daily_ohlcv(ticker, 120)
                self._ohlcv_buffer[ticker] = df
            except Exception as e:
                logger.warning(f"US 일봉 로드 실패 {ticker}: {e}")

        # TA 점수로 상위 40 필터링 (토큰 미사용 — 순수 pandas 계산)
        scored = []
        for stock in self._watchlist:
            ticker = stock["ticker"]
            df = self._ohlcv_buffer.get(ticker)
            if df is None or len(df) < 60:
                continue
            ta_result = ta_engine.compute(df)
            if ta_result is None:
                continue
            score = compute_score(ta_result, float(df["close"].iloc[-1]), regime)
            scored.append((stock, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        self._ta_filtered_watchlist = [s for s, _ in scored[:_TA_TOP_N]]
        logger.info(
            f"US TA 필터링: {len(self._watchlist)}개 → 상위 {len(self._ta_filtered_watchlist)}개 선정"
            + (f" (TA {scored[len(self._ta_filtered_watchlist)-1][1]:.1f}~{scored[0][1]:.1f}점)" if scored else "")
        )

        # Vision 분석은 가용 잔고가 있을 때만 실행
        if balance.get("available_usd", 0) < 1.0:
            logger.info(f"US 가용 잔고 없음 (${balance.get('available_usd', 0):.2f}) — Vision 분석 스킵 (OHLCV/TA는 갱신 완료)")
            return

        if regime == MarketRegime.TRENDING_DOWN:
            logger.info("US 하락장 레짐 — 스윙 매수 전체 스킵")
            return

        async with AsyncSessionLocal() as session:
            open_positions = await get_open_positions_by_market(session, "US")
            open_pos_count = len(open_positions)
            today_pnl = await get_today_realized_pnl_by_market(session, "US")
        daily_pnl_pct = today_pnl / balance["total_eval_usd"] if balance["total_eval_usd"] > 0 else 0.0
        drawdown_pct = self._portfolio_guard.get_drawdown_pct(balance["total_eval_usd"])

        # 차트 이미지 분석 (최대 3개 동시) — TA 상위 40만 대상
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

        await asyncio.gather(*[_analyze(s) for s in self._ta_filtered_watchlist], return_exceptions=True)
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
            balance = await self._get_us_balance()
            self._cached_balance = balance
            self._portfolio_guard.update_peak(balance["total_eval_usd"])
        except Exception as e:
            logger.warning(f"US 장 시작 잔고 갱신 실패 — 캐시 유지: {e}")

        if self._pending_signals:
            logger.info(f"US 매수 신호 실행: {len(self._pending_signals)}개")
            await self._execute_pending_signals()

    async def _execute_pending_signals(self, skip_guard: bool = False) -> None:
        async with AsyncSessionLocal() as session:
            balance = self._cached_balance
            open_positions = await get_open_positions_by_market(session, "US")
            open_pos_count = len(open_positions)
            already_held = {pos.stock_code for pos in open_positions}

            today_pnl = await get_today_realized_pnl_by_market(session, "US")
            daily_pnl_pct = today_pnl / balance["total_eval_usd"] if balance["total_eval_usd"] > 0 else 0.0

            # 가용 시드가 적으므로 confidence 최고 종목 1개에 전액 집중
            eligible = [
                (t, s) for t, s in self._pending_signals.items()
                if t not in already_held
            ]
            if not eligible:
                logger.info("US 매수 대상 없음 (이미 보유 중이거나 신호 없음)")
                self._pending_signals.clear()
                return

            ticker, signal = max(eligible, key=lambda x: x[1].chart_confidence)
            logger.info(
                f"US 최고 confidence 종목 단일 집중: {ticker} (confidence={signal.chart_confidence:.2f}) "
                f"| 후보 {len(eligible)}개 중 선택"
            )

            if not skip_guard:
                allowed, reason = self._portfolio_guard.allows_new_entry(
                    open_pos_count, balance["total_eval_usd"], daily_pnl_pct
                )
                if not allowed:
                    logger.info(f"US 포트폴리오 한도 도달: {reason}")
                    self._pending_signals.clear()
                    return

            try:
                info = await overseas_market_data.get_current_price(ticker)
                current_price = info["price"]
            except Exception as e:
                logger.warning(f"US 현재가 조회 실패 {ticker}: {e}")
                self._pending_signals.clear()
                return

            # 전액 매수: 개장 직후 변동성 → 7% 버퍼
            qty = round((balance["available_usd"] * 0.93) / current_price, 4)
            if qty <= 0.0001:
                logger.info(f"US 매수 수량 너무 작음 → 스킵: {ticker} (잔고 ${balance['available_usd']:.2f})")
                self._pending_signals.clear()
                return

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

            result, fill_price = await us_order_manager.execute_buy(
                session, ticker, name, qty, signal, current_price
            )
            if not result.success:
                logger.warning(f"US 매수 실패 — 텔레그램 알림 생략: {ticker}")
                self._pending_signals.clear()
                return

            notify_stop = round(fill_price * (1 - signal.stop_pct), 4)
            notify_target = round(fill_price * (1 + signal.target_pct), 4)
            await notify_us_buy(ticker, name, qty, fill_price, notify_target, notify_stop, signal.chart_confidence)

        self._pending_signals.clear()

    async def market_close(self) -> None:
        """06:10 KST — 장 종료."""
        if not is_us_trading_day():
            return
        logger.info("US 장 종료")
        self._trading_active = False
        self._pending_signals.clear()
        self._sell_fail_time.clear()
        self._sell_perm_lock.clear()

    async def post_market(self) -> None:
        """06:15 KST — 일일 결산."""
        if not is_us_trading_day():
            return
        async with AsyncSessionLocal() as session:
            pnl = await get_today_realized_pnl_by_market(session, "US")
            signal_count = await get_today_signal_count_by_market(session, "US")
            trades = await get_today_trade_count_by_market(session, "US")
            sells = await get_today_sell_count_by_market(session, "US")
        logger.info(f"US 오늘 실현 손익: ${pnl:+.2f}")
        await notify_us_daily_summary(
            signals=signal_count,
            trades=trades,
            sells=sells,
            pnl=pnl,
        )

    async def _get_us_balance(self) -> dict:
        """잔고 조회. API 실패 또는 $0 반환 시 seed_usd + DB 포지션으로 보정."""
        try:
            balance = await overseas_account.get_balance()
        except Exception as e:
            logger.warning(f"US 잔고 조회 실패 — 폴백 사용: {e}")
            balance = self._cached_balance.copy()

        if balance.get("available_usd", 0) < 1.0 and settings.us_seed_usd > 0:
            async with AsyncSessionLocal() as session:
                positions = await get_open_positions_by_market(session, "US")
            used = sum(float(p.qty) * float(p.avg_price) for p in positions)
            available = max(0.0, settings.us_seed_usd - used)
            total = settings.us_seed_usd
            balance = {**balance, "available_usd": available, "total_eval_usd": total}
            logger.info(f"US 잔고 폴백: seed=${settings.us_seed_usd:.2f} - 포지션=${used:.2f} → 가용=${available:.2f}")

        return balance

    async def _refresh_balance(self) -> bool:
        try:
            self._cached_balance = await self._get_us_balance()
            return True
        except Exception as e:
            logger.warning(f"US 잔고 갱신 실패 — 캐시 유지: {e}")
            return False

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
        if ticker in self._sell_perm_lock:
            return

        _is_retry = False
        if ticker in self._sell_fail_time:
            elapsed = time.monotonic() - self._sell_fail_time[ticker]
            if elapsed < 300:
                return  # 5분 미만 — 서버 회복 대기
            del self._sell_fail_time[ticker]
            _is_retry = True
            logger.info(f"US 매도 재시도 (5분 경과): {ticker}")

        async with AsyncSessionLocal() as session:
            pos = await get_position(session, ticker)
            if pos is None:
                return
            if float(pos.qty) <= 0.0:
                return

            # 익절 체크
            if pos.target_price and current_price >= float(pos.target_price):
                half_qty = round(float(pos.qty) / 2, 4)
                avg_price = float(pos.avg_price)
                name = pos.stock_name or ticker

                if half_qty > 0.0001 and float(pos.qty) > 0.0002:
                    logger.info(f"US 절반 익절: {ticker} x{half_qty:.4f} @ ${current_price:.2f}")
                    result, fill_price = await us_order_manager.execute_partial_sell(
                        session, ticker, half_qty, current_price, "partial_take_profit"
                    )
                    if not result.success:
                        if _is_retry:
                            self._sell_perm_lock.add(ticker)
                            logger.warning(f"US 절반 익절 재시도 실패 — 장 마감까지 영구 차단: {ticker}")
                        else:
                            self._sell_fail_time[ticker] = time.monotonic()
                            logger.warning(f"US 절반 익절 주문 실패 — 5분 후 재시도 예약: {ticker}")
                        await notify_us_sell_fail(ticker, name, "partial_take_profit", not _is_retry)
                        return
                    pnl = (fill_price - avg_price) * half_qty
                    await notify_us_sell(ticker, name, half_qty, fill_price, "partial_take_profit", pnl)
                    pos.stop_price = avg_price
                    pos.trail_pct = settings.partial_tp_trail_pct
                    pos.target_price = None
                    await session.commit()
                else:
                    qty = float(pos.qty)
                    logger.info(f"US 전량 익절: {ticker} @ ${current_price:.2f}")
                    result, fill_price = await us_order_manager.execute_sell(
                        session, ticker, qty, current_price, "take_profit"
                    )
                    if not result.success:
                        if _is_retry:
                            self._sell_perm_lock.add(ticker)
                            logger.warning(f"US 익절 재시도 실패 — 장 마감까지 영구 차단: {ticker}")
                        else:
                            self._sell_fail_time[ticker] = time.monotonic()
                            logger.warning(f"US 익절 주문 실패 — 5분 후 재시도 예약: {ticker}")
                        await notify_us_sell_fail(ticker, name, "take_profit", not _is_retry)
                        return
                    pnl = (fill_price - avg_price) * qty
                    await notify_us_sell(ticker, name, qty, fill_price, "take_profit", pnl)
                    asyncio.create_task(self._analyze_and_reenter(freed_usd=fill_price * qty))
                await self._refresh_balance()
                return

            # 트레일링 스톱 업데이트 (USD 소수점 유지)
            if pos.stop_type == "trailing":
                pct = float(pos.trail_pct) if pos.trail_pct and pos.trail_pct > 0 else 0.05
                updated_highest = max(float(pos.highest_price or pos.avg_price), current_price)
                new_stop = round(updated_highest * (1 - pct), 4)
                cur_stop = float(pos.stop_price)
                new_stop = max(cur_stop, new_stop)  # 절대 내려가지 않음
                if new_stop != cur_stop or updated_highest != float(pos.highest_price or 0):
                    pos.stop_price = new_stop
                    pos.highest_price = updated_highest
                    await session.commit()

            # 손절 체크
            if should_stop(pos, current_price):
                qty = float(pos.qty)
                avg_price = float(pos.avg_price)
                name = pos.stock_name or ticker
                logger.info(f"US 손절: {ticker} @ ${current_price:.2f} (stop=${float(pos.stop_price):.2f})")
                result, fill_price = await us_order_manager.execute_sell(
                    session, ticker, qty, current_price, "stop_loss"
                )
                if not result.success:
                    if _is_retry:
                        self._sell_perm_lock.add(ticker)
                        logger.warning(f"US 손절 재시도 실패 — 장 마감까지 영구 차단: {ticker}")
                    else:
                        self._sell_fail_time[ticker] = time.monotonic()
                        logger.warning(f"US 손절 주문 실패 — 5분 후 재시도 예약: {ticker}")
                    await notify_us_sell_fail(ticker, name, "stop_loss", not _is_retry)
                    return
                pnl = (fill_price - avg_price) * qty
                await notify_us_sell(ticker, name, qty, fill_price, "stop_loss", pnl)
                asyncio.create_task(self._analyze_and_reenter(freed_usd=fill_price * qty))
                await self._refresh_balance()

    async def _analyze_and_reenter(self, freed_usd: float = 0.0) -> None:
        """전량 청산 후 즉시 재분석 → 최고 confidence 종목 재진입."""
        if self._reenter_lock.locked():
            logger.debug("US 재진입 분석 이미 실행 중 — 중복 스킵")
            return
        async with self._reenter_lock:
            if not self._trading_active:
                return
            if not self._ohlcv_buffer or not self._ta_filtered_watchlist:
                logger.info("US 재진입 스킵: OHLCV 캐시 없음")
                return

            await asyncio.sleep(5)  # 매도 체결 후 잔고 반영 대기
            await self._refresh_balance()
            balance = self._cached_balance

            # T+1 결제 미반영 보정: 매도 직후 API가 아직 proceeds를 반영 안 한 경우
            # freed_usd의 50% 미만으로 보이면 T+1 지연으로 판단하고 freed_usd 기준 사용
            available = balance.get("available_usd", 0.0)
            if freed_usd > 0 and available < freed_usd * 0.5:
                logger.info(
                    f"US T+1 결제 미반영 감지 — API 잔고 ${available:.2f} < 매도대금 ${freed_usd:.2f} × 0.5 "
                    f"→ freed_usd 기준으로 재진입"
                )
                available = freed_usd * 0.93  # 7% 버퍼
                balance = {**balance, "available_usd": available}
                self._cached_balance = balance

            if available < 1.0:
                logger.info(f"US 재진입 스킵: 가용 잔고 없음 (${available:.2f})")
                return

            # max_drawdown_pct만 체크 — daily_loss_cap은 단일종목 올인 전략에 맞지 않아 우회
            drawdown_pct = self._portfolio_guard.get_drawdown_pct(balance["total_eval_usd"])
            if drawdown_pct >= settings.max_drawdown_pct:
                logger.info(f"US 재진입 스킵: 최대 낙폭 도달 ({drawdown_pct:.1%} >= {settings.max_drawdown_pct:.1%})")
                return

            async with AsyncSessionLocal() as session:
                open_positions = await get_open_positions_by_market(session, "US")
                open_pos_count = len(open_positions)
                already_held = {pos.stock_code for pos in open_positions}
                today_pnl = await get_today_realized_pnl_by_market(session, "US")
            daily_pnl_pct = today_pnl / balance["total_eval_usd"] if balance["total_eval_usd"] > 0 else 0.0

            regime = detect_regime(self._nasdaq_df) if self._nasdaq_df is not None else MarketRegime.RANGING
            if regime == MarketRegime.TRENDING_DOWN:
                logger.info("US 재진입 스킵: 하락장 레짐")
                return

            logger.info("US 청산 후 재진입 분석 시작")
            new_signals: dict[str, TradeSignal] = {}
            sem = asyncio.Semaphore(3)

            async def _analyze(stock: dict) -> None:
                ticker = stock["ticker"]
                if ticker in already_held:
                    return
                df = self._ohlcv_buffer.get(ticker)
                if df is None or df.empty or len(df) < 60:
                    return
                async with sem:
                    try:
                        signal = await multi_agent_strategy.evaluate(
                            stock_code=ticker,
                            stock_name=stock.get("name", ticker),
                            ohlcv_df=df,
                            kospi_df=self._nasdaq_df if self._nasdaq_df is not None else df,
                            open_positions=open_pos_count,
                            daily_pnl_pct=daily_pnl_pct,
                            drawdown_pct=drawdown_pct,
                        )
                        if signal.action == "buy":
                            new_signals[ticker] = signal
                    except Exception as e:
                        logger.warning(f"US 재진입 분석 실패 {ticker}: {e}")

            await asyncio.gather(*[_analyze(s) for s in self._ta_filtered_watchlist], return_exceptions=True)
            logger.info(f"US 재진입 분석 완료: {len(new_signals)}개 매수 신호")

            if not new_signals:
                logger.info("US 재진입: 매수 신호 없음")
                await notify_us_reenter_no_signal(len(self._ta_filtered_watchlist))
                return

            self._pending_signals = new_signals
            # skip_guard=True: 재진입은 daily_loss_cap·drawdown 차단 대상 아님
            # (방금 청산한 자금을 재배치하는 것이므로 장전 매수와 다른 상황)
            await self._execute_pending_signals(skip_guard=True)

    async def run(self) -> None:
        from core.us_scheduler import create_us_scheduler
        from repository.database import init_db
        from datetime import time as dtime

        await init_db()
        scheduler = create_us_scheduler(self)
        scheduler.start()
        logger.info("US 트레이딩 에이전트 시작")

        # 장 중 재시작 시 즉시 복구 (NY 기준 08:30~16:00)
        from zoneinfo import ZoneInfo
        now_ny = datetime.now(ZoneInfo("America/New_York"))
        t_ny = now_ny.time()
        if dtime(9, 0) <= t_ny < dtime(16, 0):
            logger.info("NY 09:00~16:00 사이 시작 — pre_market_setup 즉시 실행")
            await self.pre_market_setup()
        if dtime(9, 30) <= t_ny < dtime(16, 0):
            logger.info("NY 09:30~16:00 사이 시작 — market_open 즉시 실행")
            await self.market_open()

        try:
            while True:
                await asyncio.sleep(3600)
        except (KeyboardInterrupt, asyncio.CancelledError):
            scheduler.shutdown()
            logger.info("US 트레이딩 에이전트 종료")


us_orchestrator = USOrchestrator()
