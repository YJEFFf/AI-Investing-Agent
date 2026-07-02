import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def create_scheduler(orchestrator) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 09:00 장 시작 — 폴링 시작 (매수는 15:20에 실행)
    scheduler.add_job(
        orchestrator.market_open,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
        id="market_open",
    )
    # 14:50 장 마감 직전 분析 — 당일 데이터로 종목 선정
    scheduler.add_job(
        orchestrator.pre_market_setup,
        CronTrigger(hour=14, minute=50, day_of_week="mon-fri"),
        id="pre_close_analysis",
    )
    # 15:20 장 마감 직전 매수 실행
    scheduler.add_job(
        orchestrator.execute_pending_buy,
        CronTrigger(hour=15, minute=20, day_of_week="mon-fri"),
        id="close_buy",
    )
    scheduler.add_job(
        orchestrator.market_close,
        CronTrigger(hour=15, minute=30, day_of_week="mon-fri"),
        id="market_close",
    )
    scheduler.add_job(
        orchestrator.post_market,
        CronTrigger(hour=15, minute=35, day_of_week="mon-fri"),
        id="post_market",
    )

    return scheduler
