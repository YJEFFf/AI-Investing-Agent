import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


def create_scheduler(orchestrator) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    scheduler.add_job(
        orchestrator.market_open,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri"),
        id="market_open",
    )
    scheduler.add_job(
        orchestrator.pre_close,
        CronTrigger(hour=15, minute=20, day_of_week="mon-fri"),
        id="pre_close",
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
    scheduler.add_job(
        orchestrator.pre_market_setup,
        CronTrigger(hour=15, minute=40, day_of_week="mon-fri"),
        id="pre_market_setup",
    )

    return scheduler
