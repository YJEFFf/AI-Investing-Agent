"""실전매매 실행 (KIS_ENV=prod)"""
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

if settings.is_paper:
    print("오류: 실전매매를 실행하려면 .env에서 KIS_ENV=prod 로 변경하세요.")
    sys.exit(1)

confirm = input("실전매매를 시작합니다. 실제 돈이 사용됩니다. 계속하려면 'YES'를 입력하세요: ")
if confirm != "YES":
    print("취소됨.")
    sys.exit(0)

from core.orchestrator import orchestrator

if __name__ == "__main__":
    asyncio.run(orchestrator.run())
