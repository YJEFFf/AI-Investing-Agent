"""모의매매 실행 (KIS_ENV=vps)"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

from config.settings import settings

if not settings.is_paper:
    print("오류: KIS_ENV=vps 로 설정해야 모의매매가 실행됩니다.")
    sys.exit(1)

from core.orchestrator import orchestrator

if __name__ == "__main__":
    asyncio.run(orchestrator.run())
