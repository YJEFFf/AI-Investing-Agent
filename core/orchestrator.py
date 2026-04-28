import asyncio
import logging
from collections import defaultdict
from datetime import datetime

import yaml

from config.settings import settings
from data.fetcher import fetcher
from data.news_scraper import news_scraper
from data.preprocessor import resample_to_minutes
from kis.account import kis_account
from kis.websocket_client import kis_ws, Tick
from repository.database import AsyncSessionLocal
from repository.queries import (
    get_open_positions, get_today_realized_pnl, get_win_rate_stats
)
from risk.portfolio_guard import portfolio_guard
from risk.position_sizer import calc_position_size
from risk.stop_loss import should_stop, update_trailing_stop
from strategy.multi_agent_strategy import multi_agent_strategy
from strategy.regime_detector import detect_regime
from execution.order_manager import order_manager

logger = logging.getLogger(__name__)

_WATCHLIST_PATH = "config/watchlist.yaml"


class Orchestrator:
    def __init__(self):
        self._trading_active = False
        self._tick_buffer: dict[str, list[dict]] = defaultdict(list)
        self._ohlcv_buffer: dict[str, list] = defaultdict(list)
        self._watchlist: list[dict] = []
        self._kospi_df = None
        self._news_cache: dict[str, list] = {}

    def _load_watchlist(self) -> list[dict]:
        with open(_WATCHLIST_PATH) as f:
            config = yaml.safe_load(f)
        return config.get("stocks", [])

    async def pre_market_setup(self) -> None:
        """08:30 장 시작 전 준비"""
        logger.info("장전 준비 시작")
        self._watchlist = self._load_watchlist()

        # KOSPI 데이터 로드 (레짐 감지용)
        self._kospi_df = await fetcher.fetch_kospi(250)

        # 워치리스트 종목 OHLCV 미리 로드
        for stock in self._watchlist:
            code = stock["code"]
            try:
                df = await fetcher.fetch_daily(code, 120)
                self._ohlcv_buffer[code] = df
                logger.debug(f"OHLCV 로드: {code}")
            except Exception as e:
                logger.warning(f"OHLCV 로드 실패 {code}: {e}")

        # 장전 뉴스 수집
        await self._collect_all_news()
        logger.info(f"장전 준비 완료: {len(self._watchlist)}개 종목")

    async def market_open(self) -> None:
        """09:00 장 시작"""
        logger.info("장 시작 — 매매 활성화")
        self._trading_active = True

        # WebSocket 구독
        codes = [s["code"] for s in self._watchlist]
        kis_ws.subscribe(codes)
        kis_ws.add_handler(self._on_tick)

        # 잔고 초기화
        balance = await kis_account.get_balance()
        portfolio_guard.update_peak(balance["total_eval"])

    async def market_close(self) -> None:
        """15:30 장 종료"""
        logger.info("장 종료 처리")
        self._trading_active = False
        await kis_ws.stop()
        news_scraper.clear_seen()

    async def pre_close(self) -> None:
        """15:20 장마감 전 스윙 포지션 점검"""
        logger.info("장마감 전 스윙 포지션 점검")

    async def post_market(self) -> None:
        """15:35 장 후 정산"""
        logger.info("장 후 정산 중")
        async with AsyncSessionLocal() as session:
            pnl = await get_today_realized_pnl(session)
            logger.info(f"오늘 실현 손익: {pnl:+,}원")

    async def news_poll(self) -> None:
        """2분마다 뉴스 폴링"""
        if not self._trading_active:
            return
        await self._collect_all_news()

    async def _collect_all_news(self) -> None:
        for stock in self._watchlist:
            code = stock["code"]
            try:
                new_items = await news_scraper.fetch_new_stock_news(code, max_items=5)
                if new_items:
                    self._news_cache[code] = new_items
                    logger.debug(f"새 뉴스 {len(new_items)}건: {code}")
            except Exception as e:
                logger.warning(f"뉴스 수집 실패 {code}: {e}")

    async def _on_tick(self, tick: Tick) -> None:
        """WebSocket tick 수신 처리"""
        self._tick_buffer[tick.code].append({
            "datetime": datetime.now(),
            "price": tick.price,
            "volume": tick.volume,
        })

        # 1분봉 완성 여부 확인
        ticks = self._tick_buffer[tick.code]
        if len(ticks) >= 2:
            first_min = ticks[0]["datetime"].minute
            last_min = ticks[-1]["datetime"].minute
            if last_min != first_min:
                bar_ticks = [t for t in ticks if t["datetime"].minute == first_min]
                self._tick_buffer[tick.code] = [t for t in ticks if t["datetime"].minute != first_min]
                await self._on_bar_close(tick.code, bar_ticks)

    async def _on_bar_close(self, code: str, bar_ticks: list[dict]) -> None:
        """1분봉 완성 시 전략 실행"""
        if not self._trading_active:
            return

        current_price = bar_ticks[-1]["price"]

        async with AsyncSessionLocal() as session:
            # 1. 손절 체크 (최우선)
            positions = await get_open_positions(session)
            for pos in positions:
                if pos.stock_code != code:
                    continue

                # 트레일링 스탑 업데이트
                if pos.stop_type == "trailing":
                    new_stop = update_trailing_stop(pos, current_price)
                    if new_stop != int(pos.stop_price):
                        pos.stop_price = new_stop
                        pos.highest_price = max(pos.highest_price or 0, current_price)
                        await session.commit()

                if should_stop(pos, current_price):
                    logger.info(f"손절 발동: {code} @ {current_price} (손절가: {pos.stop_price})")
                    await order_manager.execute_sell(session, code, pos.qty, current_price, "stop_loss")
                    return

            # 2. 포트폴리오 상태 확인
            balance = await kis_account.get_balance()
            open_pos_count = len(await get_open_positions(session))
            today_pnl = await get_today_realized_pnl(session)
            portfolio_guard.update_peak(balance["total_eval"])
            daily_pnl_pct = today_pnl / balance["total_eval"] if balance["total_eval"] > 0 else 0.0
            drawdown_pct = portfolio_guard.get_drawdown_pct(balance["total_eval"])

            allowed, reason = portfolio_guard.allows_new_entry(
                open_pos_count, balance["total_eval"], daily_pnl_pct
            )
            if not allowed:
                return

            # 3. 전략 평가
            ohlcv_df = self._ohlcv_buffer.get(code)
            if ohlcv_df is None or ohlcv_df.empty:
                return

            news_items = self._news_cache.get(code, [])
            stock_name = next((s["name"] for s in self._watchlist if s["code"] == code), code)

            signal = await multi_agent_strategy.evaluate(
                stock_code=code,
                stock_name=stock_name,
                ohlcv_df=ohlcv_df,
                news_items=news_items,
                kospi_df=self._kospi_df,
                open_positions=open_pos_count,
                daily_pnl_pct=daily_pnl_pct,
                drawdown_pct=drawdown_pct,
            )

            if signal.action != "buy":
                return

            # 4. 포지션 크기 계산
            stats = await get_win_rate_stats(session)
            qty = calc_position_size(
                available_cash=balance["available_cash"],
                current_price=current_price,
                position_ratio=signal.position_ratio,
                win_rate=stats["win_rate"],
                avg_win_pct=stats["avg_win_pct"],
                avg_loss_pct=stats["avg_loss_pct"],
            )

            if qty <= 0:
                logger.info(f"매수 수량 0 → 스킵: {code} (잔고 {balance['available_cash']:,}원, 주가 {current_price:,}원)")
                return

            # 5. 주문 실행
            await order_manager.execute_buy(
                session, code, stock_name, qty, signal, current_price
            )

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

        # 시작 시점이 장전 준비 이후면 즉시 실행
        now = datetime.now()
        market_open_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
        pre_market_time = now.replace(hour=8, minute=30, second=0, microsecond=0)
        if now >= pre_market_time:
            logger.info("08:30 이후 시작 — pre_market_setup 즉시 실행")
            await self.pre_market_setup()
        if now >= market_open_time:
            logger.info("09:00 이후 시작 — market_open 즉시 실행")
            await self.market_open()

        # WebSocket 연결 (백그라운드)
        asyncio.create_task(kis_ws.connect_and_run())

        # 메인 루프 유지
        try:
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass
        finally:
            scheduler.shutdown()
            await kis_ws.stop()
            logger.info("Orchestrator 종료")


orchestrator = Orchestrator()
