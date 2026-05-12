import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

# 미국 정규장 시간 — America/New_York 기준으로 설정 (DST 자동 처리)
# EDT(섬머타임, 3~11월): 09:30 NY = 22:30 KST
# EST(겨울,   11~3월):  09:30 NY = 23:30 KST
# → KST 하드코딩 시 섬머타임 전환 때마다 틀어지므로 NY 타임존 사용


def create_us_scheduler(orchestrator) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="America/New_York")

    # 매주 월요일 NASDAQ 100 구성 종목 갱신
    scheduler.add_job(
        orchestrator.update_watchlist,
        CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="America/New_York"),
        id="us_watchlist_update",
    )
    # 장전 분석 — 월~금 09:00 NY (장 시작 30분 전)
    scheduler.add_job(
        orchestrator.pre_market_setup,
        CronTrigger(hour=9, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_pre_market_setup",
    )
    # 장 시작 — 월~금 09:30 NY
    scheduler.add_job(
        orchestrator.market_open,
        CronTrigger(hour=9, minute=30, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_market_open",
    )
    # 장 종료 — 월~금 16:00 NY
    scheduler.add_job(
        orchestrator.market_close,
        CronTrigger(hour=16, minute=0, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_market_close",
    )
    # 장후 정산 — 월~금 16:10 NY
    scheduler.add_job(
        orchestrator.post_market,
        CronTrigger(hour=16, minute=10, day_of_week="mon-fri", timezone="America/New_York"),
        id="us_post_market",
    )

    return scheduler
