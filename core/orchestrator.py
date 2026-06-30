import asyncio
import logging
import time
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
    get_open_positions_by_market, get_position, get_today_realized_pnl_by_market, save_signal,
    get_today_trade_count_by_market, get_today_signal_count_by_market, get_today_sell_count_by_market,
    get_pending_signals, clear_pending_signals, expire_old_pending_signals,
)
from risk.portfolio_guard import portfolio_guard
from risk.position_sizer import calc_position_size
from risk.stop_loss import should_stop, update_trailing_stop
from strategy.multi_agent_strategy import multi_agent_strategy
from execution.order_manager import order_manager
from data.screener import stock_screener
from strategy.agents.decision_agent import TradeSignal
from core.notifier import notify_buy, notify_buy_fail, notify_sell, notify_sell_fail, notify_daily_summary, notify_pre_market_summary, notify_gap_skip
from core.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = "config/watchlist.yaml"


class Orchestrator:
    def __init__(self):
        self._trading_active = False
        self._ohlcv_buffer: dict[str, pd.DataFrame] = {}       # 일봉 (장전 로드)
        self._pending_signals: dict[str, TradeSignal] = {}     # 장전 분석 결과
        self._pending_names: dict[str, str] = {}               # 종목코드 → 종목명
        self._pending_prices: dict[str, int] = {}              # 종목코드 → 분석 시점 종가 (갭 필터용)
        self._watchlist: list[dict] = []
        self._cached_balance: dict = {"available_cash": 0, "total_eval": 0, "unrealized_pnl": 0}
        self._sell_fail_time: dict[str, float] = {}             # 최초 매도 실패 시각 (monotonic) — 5분 후 재시도
        self._sell_perm_lock: set[str] = set()                 # 재시도 후에도 실패 → 장 마감까지 영구 차단

    def _load_watchlist_config(self) -> dict:
        with open(_WATCHLIST_PATH) as f:
            return yaml.safe_load(f)

    async def pre_market_setup(self, is_retry: bool = False) -> None:
        """15:40 장 마감 후 분석 — 일봉 데이터 로드 + 차트 이미지 분석 → 다음날 09:00 매수 실행"""
        if not is_trading_day():
            logger.info("휴장일 — 장 마감 후 분석 스킵")
            return
        logger.info("장전 준비 시작")
        self._pending_signals.clear()
        self._pending_names.clear()
        self._pending_prices.clear()
        async with AsyncSessionLocal() as session:
            await clear_pending_signals(session)

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

        config = self._load_watchlist_config()
        if config.get("mode") == "auto":
            screened = await stock_screener.run()
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

        # 실제 포지션/손익 조회
        async with AsyncSessionLocal() as session:
            open_positions = await get_open_positions_by_market(session, "KR")
            open_pos_count = len(open_positions)
            already_held = {pos.stock_code for pos in open_positions}
            today_pnl = await get_today_realized_pnl_by_market(session, "KR")
        daily_pnl_pct = today_pnl / balance["total_eval"] if balance["total_eval"] > 0 else 0.0

        # 차트 이미지 분석 (순차 처리 — 동시 호출 시 Anthropic 분당 토큰 한도 초과)
        sem = asyncio.Semaphore(1)

        async def _analyze_stock(stock: dict) -> None:
            code = stock["code"]
            name = stock.get("name", code)
            if code in already_held:
                logger.debug(f"{code} 이미 보유 중 — 분석 스킵")
                return
            df = self._ohlcv_buffer.get(code)
            if df is None or df.empty or len(df) < 60:
                return
            async with sem:
                try:
                    signal = await multi_agent_strategy.evaluate(
                        stock_code=code,
                        stock_name=name,
                        ohlcv_df=df,
                        open_positions=open_pos_count,
                        daily_pnl_pct=daily_pnl_pct,
                        drawdown_pct=drawdown_pct,
                    )
                    if signal.action == "buy":
                        self._pending_signals[code] = signal
                        self._pending_names[code] = name
                        self._pending_prices[code] = int(df["close"].iloc[-1])
                        logger.info(f"[{code}] 스윙 매수 신호 → 대기열 등록 ({signal.reasoning[:60]})")
                    if signal.llm_called:
                        async with AsyncSessionLocal() as sig_session:
                            sig_record = Signal(
                                stock_code=code,
                                stock_name=name,
                                ta_score=signal.ta_score,
                                chart_verdict=signal.chart_verdict,
                                chart_confidence=signal.chart_confidence,
                                risk_level=signal.risk_level,
                                final_action=signal.action,
                                position_size_pct=signal.position_ratio,
                                stop_pct=signal.stop_pct,
                                target_pct=signal.target_pct,
                                reasoning=signal.reasoning,
                                pending=(signal.action == "buy"),
                                rsi=signal.rsi,
                                macd_hist=signal.macd_hist,
                                bb_pct=signal.bb_pct,
                                ema_ratio=signal.ema_ratio,
                                volume_ratio=signal.volume_ratio,
                                atr=signal.atr,
                                regime=signal.regime,
                                current_price=int(signal.current_price),
                            )
                            await save_signal(sig_session, sig_record)
                            # buy 신호면 signal_db_id 보관 — market_open 중복 저장 방지
                            if signal.action == "buy" and code in self._pending_signals:
                                self._pending_signals[code].signal_db_id = sig_record.id
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
                "regime": sig.regime or "",
            }
            for code, sig in self._pending_signals.items()
        ]
        await notify_pre_market_summary(len(self._watchlist), signal_list, is_retry=is_retry)

    async def market_open(self) -> None:
        """09:00 장 시작 — 전일 분석 결과 매수 실행"""
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

        # 장전 매수 신호 실행 — 메모리가 비어있으면 DB에서 복원
        if not self._pending_signals:
            await self._restore_pending_signals()
        if self._pending_signals:
            logger.info(f"장전 매수 신호 실행: {len(self._pending_signals)}개")
            await self._execute_pending_signals()

    async def _execute_pending_signals(self) -> None:
        """장전에 수집된 매수 신호 일괄 집행"""
        from kis.market_data import market_data as md

        async with AsyncSessionLocal() as session:
            balance = self._cached_balance
            open_positions = await get_open_positions_by_market(session, "KR")
            open_pos_count = len(open_positions)
            already_held = {pos.stock_code for pos in open_positions}

            today_pnl = await get_today_realized_pnl_by_market(session, "KR")
            daily_pnl_pct = today_pnl / balance["total_eval"] if balance["total_eval"] > 0 else 0.0

            sorted_signals = sorted(
                self._pending_signals.items(),
                key=lambda x: x[1].chart_confidence,
                reverse=True,
            )
            consecutive_insufficient = 0
            for code, signal in sorted_signals:
                if code in already_held:
                    logger.debug(f"{code} 이미 보유 중 — 스킵")
                    continue

                allowed, reason = portfolio_guard.allows_new_entry(
                    balance["total_eval"], daily_pnl_pct
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

                # 갭다운 필터: 시초가가 분석 기준가 대비 -2% 이상 하락 시 스킵
                analysis_price = self._pending_prices.get(code)
                if analysis_price and analysis_price > 0:
                    gap_pct = (current_price - analysis_price) / analysis_price
                    if gap_pct < -0.02:
                        stock_name = self._pending_names.get(code, code)
                        logger.info(f"{code} 갭다운 스킵: 기준가 {analysis_price:,} → 시초가 {current_price:,} ({gap_pct:.1%})")
                        await notify_gap_skip(code, stock_name, analysis_price, current_price, gap_pct)
                        continue

                # 2,000,000 × confidence 기준 수량 계산
                qty = calc_position_size(
                    available_cash=balance["available_cash"],
                    current_price=current_price,
                    confidence=signal.chart_confidence,
                )

                if qty <= 0:
                    logger.info(f"매수 수량 0 → 스킵: {code} (현재가 {current_price:,}원, confidence={signal.chart_confidence:.2f})")
                    continue


                stock_name = self._pending_names.get(code) or next((s["name"] for s in self._watchlist if s["code"] == code), code)
                if signal.signal_db_id:
                    # 장전 분析 때 이미 저장된 signal — 중복 저장 없이 id 재사용
                    signal_id = signal.signal_db_id
                else:
                    # 복원 경로 fallback: signal_db_id가 없는 경우에만 신규 저장
                    signal_record = Signal(
                        stock_code=code,
                        ta_score=signal.ta_score,
                        chart_verdict=signal.chart_verdict,
                        chart_confidence=signal.chart_confidence,
                        risk_level=signal.risk_level,
                        final_action=signal.action,
                        position_size_pct=signal.position_ratio,
                        reasoning=signal.reasoning,
                        rsi=signal.rsi or None,
                        macd_hist=signal.macd_hist or None,
                        bb_pct=signal.bb_pct or None,
                        ema_ratio=signal.ema_ratio or None,
                        volume_ratio=signal.volume_ratio or None,
                        atr=signal.atr or None,
                        regime=signal.regime or None,
                        current_price=int(signal.current_price) if signal.current_price else None,
                    )
                    await save_signal(session, signal_record)
                    signal_id = signal_record.id
                logger.debug(f"signal id={signal_id}, code={code}")
                result, fill_price = await order_manager.execute_buy(
                    session, code, stock_name, qty, signal, current_price,
                    signal_id=signal_id,
                )
                if not result.success:
                    logger.warning(f"매수 실패: {code} — {result.message}")
                    if "40250000" in result.message:
                        consecutive_insufficient += 1
                        await notify_buy_fail(code, stock_name, "잔고 부족")
                        if consecutive_insufficient >= 2:
                            logger.info("잔고 부족 연속 2회 — 이후 신호 실행 중단")
                            break
                        logger.info(f"잔고 부족 1회 ({code}) — 다음 신호 계속")
                    else:
                        await notify_buy_fail(code, stock_name, result.message)
                    continue
                notify_stop = round(fill_price * (1 - signal.stop_pct))
                notify_target = round(fill_price * (1 + signal.target_pct))
                await notify_buy(
                    code, stock_name, qty, fill_price,
                    notify_target, notify_stop, signal.chart_confidence,
                )
                consecutive_insufficient = 0
                open_pos_count += 1
                if not await self._refresh_balance():
                    # 잔고 갱신 실패 — 체결 비용 수동 차감해 다음 종목 예산 과다 방지
                    self._cached_balance["available_cash"] = max(
                        0, self._cached_balance["available_cash"] - fill_price * qty
                    )
                balance = self._cached_balance

        self._pending_signals.clear()
        async with AsyncSessionLocal() as session:
            await clear_pending_signals(session)

    async def market_close(self) -> None:
        """15:30 장 종료"""
        if not is_trading_day():
            logger.info("휴장일 — 장 종료 스킵")
            return
        logger.info("장 종료 처리")
        self._trading_active = False
        await kis_ws.stop()
        self._pending_signals.clear()
        self._pending_prices.clear()
        self._sell_fail_time.clear()
        self._sell_perm_lock.clear()
        async with AsyncSessionLocal() as session:
            await clear_pending_signals(session)


    async def post_market(self) -> None:
        """15:35 장 후 정산"""
        if not is_trading_day():
            logger.info("휴장일 — 장 후 정산 스킵")
            return
        logger.info("장 후 정산 중")
        async with AsyncSessionLocal() as session:
            expired = await expire_old_pending_signals(session)
            if expired:
                logger.info(f"만료 pending 신호 {expired}개 정리")
            pnl = await get_today_realized_pnl_by_market(session, "KR")
            logger.info(f"오늘 실현 손익: {pnl:+,}원")
            signal_count = await get_today_signal_count_by_market(session, "KR")
            trade_count = await get_today_trade_count_by_market(session, "KR")
            sell_count = await get_today_sell_count_by_market(session, "KR")
            await notify_daily_summary(
                signals=signal_count,
                trades=trade_count,
                sells=sell_count,
                pnl=int(pnl),
            )


    async def retry_analysis_if_needed(self) -> None:
        """16:00~00:00 매시 정각 — pending 신호 3개 미만이면 재분석"""
        if not is_trading_day():
            return
        async with AsyncSessionLocal() as session:
            pending = await get_pending_signals(session)
        count = len(pending)
        if count >= 3:
            logger.info(f"재분석 스킵 — pending 신호 {count}개 (3개 이상 충족)")
            # 메모리가 비어있으면 DB에서 복원 (재시작 후 skip된 경우 대비)
            if not self._pending_signals:
                await self._restore_pending_signals()
            return
        logger.info(f"pending 신호 {count}개 → 재분석 시작")
        await self.pre_market_setup(is_retry=True)

    async def _restore_pending_signals(self) -> None:
        """DB에서 오늘 미실행 pending 신호 복원 — DB가 단일 진실의 소스"""
        try:
            async with AsyncSessionLocal() as session:
                pending = await get_pending_signals(session)
            # 기존 메모리를 DB 상태로 교체 (수동 스크립트 등 외부 저장 반영)
            self._pending_signals.clear()
            self._pending_names.clear()
            self._pending_prices.clear()
            if not pending:
                return
            for sig in pending:
                if not sig.stop_pct or not sig.target_pct:
                    logger.warning(f"stop_pct/target_pct 누락 신호 복원 스킵: {sig.stock_code} (구버전 저장 데이터)")
                    continue
                self._pending_signals[sig.stock_code] = TradeSignal(
                    action="buy",
                    position_ratio=sig.position_size_pct or 0.0,
                    stop_price=0,
                    stop_type="trailing",
                    reasoning=sig.reasoning or "",
                    ta_score=sig.ta_score or 0.0,
                    risk_level=sig.risk_level or "low",
                    chart_verdict=sig.chart_verdict or "buy",
                    chart_confidence=sig.chart_confidence or 0.0,
                    stop_pct=sig.stop_pct,
                    target_pct=sig.target_pct,
                    rsi=sig.rsi or 0.0,
                    macd_hist=sig.macd_hist or 0.0,
                    bb_pct=sig.bb_pct or 0.0,
                    ema_ratio=sig.ema_ratio or 0.0,
                    volume_ratio=sig.volume_ratio or 0.0,
                    atr=sig.atr or 0.0,
                    regime=sig.regime or "",
                    current_price=float(sig.current_price) if sig.current_price else 0.0,
                    llm_called=True,
                    signal_db_id=sig.id,
                )
                self._pending_names[sig.stock_code] = sig.stock_name or sig.stock_code
                if sig.current_price:
                    self._pending_prices[sig.stock_code] = int(sig.current_price)
            logger.info(f"DB에서 pending 신호 {len(pending)}개 복원: {[s.stock_code for s in pending]}")
        except Exception as e:
            logger.warning(f"pending 신호 복원 실패: {e}")

    async def _reconcile_positions(self, db_codes: set) -> None:
        """KIS 실제 잔고와 DB open 포지션 비교 — 수동 매도 자동 closed 처리"""
        try:
            kis_positions = await kis_account.get_positions()
            kis_codes = {p["code"] for p in kis_positions}
            manually_sold = db_codes - kis_codes
            if not manually_sold:
                return
            from kis.market_data import market_data as md
            for code in manually_sold:
                async with AsyncSessionLocal() as session:
                    pos = await get_position(session, code)
                    if pos is None:
                        continue
                    try:
                        info = await md.get_current_price(code)
                        exit_price = info["price"]
                    except Exception:
                        exit_price = int(pos.avg_price)
                    pnl = (exit_price - int(pos.avg_price)) * int(pos.qty)
                    pnl_pct = (exit_price / int(pos.avg_price) - 1) if pos.avg_price else 0.0
                    pos.status = "closed"
                    pos.exit_at = datetime.now()
                    pos.exit_price = exit_price
                    pos.profit_loss = pnl
                    pos.profit_loss_pct = pnl_pct
                    pos.exit_reason = "manual_sell"
                    await session.commit()
                    logger.info(f"수동 매도 감지 → DB 정리: {code} @ {exit_price:,}원 (추정), 손익 {pnl:+,}원")
        except Exception as e:
            logger.debug(f"포지션 대사 실패: {e}")

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
                held_positions = await get_open_positions_by_market(session, "KR")
            held_codes = {pos.stock_code for pos in held_positions}

            # KIS 실제 잔고와 DB 비교 — 수동 매도 감지
            await self._reconcile_positions(held_codes)

            tasks = [_check_with_sem(code) for code in held_codes]
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _refresh_balance(self) -> bool:
        try:
            self._cached_balance = await kis_account.get_balance()
            return True
        except Exception as e:
            logger.warning(f"잔고 갱신 실패 — 캐시 유지: {e}")
            return False

    async def _check_exit_conditions(self, code: str, current_price: int) -> None:
        """보유 포지션 익절/손절 체크 및 트레일링 스톱 업데이트"""
        if code in self._sell_perm_lock:
            return

        _is_retry = False
        if code in self._sell_fail_time:
            elapsed = time.monotonic() - self._sell_fail_time[code]
            if elapsed < 300:
                return  # 5분 미만 — 서버 회복 대기
            del self._sell_fail_time[code]
            _is_retry = True
            logger.info(f"매도 재시도 (5분 경과): {code}")

        async with AsyncSessionLocal() as session:
            pos = await get_position(session, code)
            if pos is None:
                return
            if int(pos.qty) <= 0:
                return

            # 익절 체크
            if pos.target_price and current_price >= pos.target_price:
                qty = int(pos.qty)
                avg_price = int(pos.avg_price)
                stock_name = pos.stock_name
                is_high_vol = (pos.regime == "high_volatility")
                half_qty = qty // 2

                if is_high_vol or half_qty == 0 or qty <= 1:
                    # HIGH_VOLATILITY: 전량 익절 (목표가 도달 시 즉시 전부 매도)
                    logger.info(f"전량 익절: {code} x{qty} @ {current_price} (목표가: {pos.target_price}, 레짐: {pos.regime})")
                    result, fill_price = await order_manager.execute_sell(
                        session, code, qty, current_price, "take_profit"
                    )
                    if not result.success:
                        if _is_retry:
                            self._sell_perm_lock.add(code)
                            logger.warning(f"익절 재시도 실패 — 장 마감까지 영구 차단: {code}")
                        else:
                            self._sell_fail_time[code] = time.monotonic()
                            logger.warning(f"익절 주문 실패 — 5분 후 재시도 예약: {code}")
                        await notify_sell_fail(code, stock_name, "take_profit", not _is_retry)
                        return
                    pnl = (fill_price - avg_price) * qty
                    await notify_sell(code, stock_name, qty, fill_price, "take_profit", int(pnl))
                else:
                    # 일반 레짐: 절반 익절 후 나머지 trailing stop 유지
                    logger.info(f"절반 익절: {code} x{half_qty} @ {current_price} (목표가: {pos.target_price})")
                    result, fill_price = await order_manager.execute_partial_sell(
                        session, code, half_qty, current_price, "partial_take_profit"
                    )
                    if not result.success:
                        if _is_retry:
                            self._sell_perm_lock.add(code)
                            logger.warning(f"절반 익절 재시도 실패 — 장 마감까지 영구 차단: {code}")
                        else:
                            self._sell_fail_time[code] = time.monotonic()
                            logger.warning(f"절반 익절 주문 실패 — 5분 후 재시도 예약: {code}")
                        await notify_sell_fail(code, stock_name, "partial_take_profit", not _is_retry)
                        return
                    pnl = (fill_price - avg_price) * half_qty
                    await notify_sell(code, stock_name, half_qty, fill_price, "partial_take_profit", int(pnl))
                    pos.target_price = None
                    await session.commit()
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
                qty = int(pos.qty)
                avg_price = int(pos.avg_price)
                stock_name = pos.stock_name
                logger.info(f"손절 발동: {code} @ {current_price} (손절가: {pos.stop_price})")
                result, fill_price = await order_manager.execute_sell(
                    session, code, qty, current_price, "stop_loss"
                )
                if not result.success:
                    if _is_retry:
                        self._sell_perm_lock.add(code)
                        logger.warning(f"손절 재시도 실패 — 장 마감까지 영구 차단: {code}")
                    else:
                        self._sell_fail_time[code] = time.monotonic()
                        logger.warning(f"손절 주문 실패 — 5분 후 재시도 예약: {code}")
                    await notify_sell_fail(code, stock_name, "stop_loss", not _is_retry)
                    return
                pnl = (fill_price - avg_price) * qty
                await notify_sell(code, stock_name, qty, fill_price, "stop_loss", int(pnl))
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
        t_1800 = now.replace(hour=18, minute=0, second=0, microsecond=0)
        t_1530 = now.replace(hour=15, minute=30, second=0, microsecond=0)
        if now >= t_1800:
            # 18:00 이후 시작: 당일 분석 즉시 실행
            logger.info("18:00 이후 시작 — 즉시 분석 실행")
            await self.retry_analysis_if_needed()
        elif t_1530 <= now < t_1800:
            # 15:30~18:00 사이: 스케줄러가 18:00에 분석 실행
            logger.info("15:30~18:00 사이 시작 — 스케줄러 대기")
        else:
            # 장중(09:00~15:30) 또는 장전: 재분석 스킵, 포지션 관리만
            logger.info("장중/장전 시작 — 재분석 스킵, 폴링으로 포지션 관리만 진행")
            await self._restore_pending_signals()
            # 장중 재시작 시 미실행 신호 즉시 집행
            t_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
            t_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
            if t_open <= now < t_close and self._pending_signals:
                logger.info(f"장중 재시작 — pending {len(self._pending_signals)}개 즉시 실행")
                self._trading_active = True
                try:
                    balance = await kis_account.get_balance()
                    self._cached_balance = balance
                except Exception:
                    pass
                if settings.is_paper:
                    asyncio.create_task(self._polling_loop())
                await self._execute_pending_signals()

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
