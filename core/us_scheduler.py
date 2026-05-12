import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# 미국 정규장 시간 (KST 기준)
# NYSE/NASDAQ 09:30~16:00 EST = KST 23:30~06:00 (다음날)
# 장전 분석: 22:30 KST (= 08:30 EST)
# 장 시작:  23:30 KST (= 09:30 EST)
# 장 종료:  06:00 KST (= 16:00 EST, 다음날)
# 장후 정산: 06:10 KST


def create_us_scheduler(orchestrator) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="Asia/Seoul")

    # 매주 월요일 NASDAQ 100 구성 종목 갱신 (장 시작 전 여유 있게)
    scheduler.add_job(
        orchestrator.update_watchlist,
        CronTrigger(day_of_week="mon", hour=21, minute=0),
        id="us_watchlist_update",
    )
    # 장전 분석 — 월~금 22:30 KST
    scheduler.add_job(
        orchestrator.pre_market_setup,
        CronTrigger(hour=22, minute=30, day_of_week="mon-fri"),
        id="us_pre_market_setup",
    )
    # 장 시작 — 월~금 23:30 KST
    scheduler.add_job(
        orchestrator.market_open,
        CronTrigger(hour=23, minute=30, day_of_week="mon-fri"),
        id="us_market_open",
    )
    # 장 종료 — 화~토 06:00 KST (월~금 EST 16:00 = 다음날 KST)
    scheduler.add_job(
        orchestrator.market_close,
        CronTrigger(hour=6, minute=0, day_of_week="tue-sat"),
        id="us_market_close",
    )
    # 장후 정산 — 화~토 06:10 KST
    scheduler.add_job(
        orchestrator.post_market,
        CronTrigger(hour=6, minute=10, day_of_week="tue-sat"),
        id="us_post_market",
    )

    return scheduler
