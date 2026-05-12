"""미국 주식 실전매매 실행"""
import asyncio
import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_log_dir, exist_ok=True)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")

_file_handler = TimedRotatingFileHandler(
    os.path.join(_log_dir, "us_live.log"),
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_file_handler.setFormatter(_fmt)

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_file_handler, _console_handler])

from config.settings import settings

if settings.is_paper:
    print("오류: KIS_ENV=prod 로 설정해야 US 실전매매가 실행됩니다.")
    sys.exit(1)

from core.us_orchestrator import us_orchestrator

if __name__ == "__main__":
    asyncio.run(us_orchestrator.run())
