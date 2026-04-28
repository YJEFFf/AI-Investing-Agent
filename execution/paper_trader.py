"""
모의매매 실행기.
KIS_ENV=vps 설정 시 kis_order가 자동으로 모의 TR ID를 사용하므로
실제 코드 변경 없이 동작함. 이 파일은 모의매매 전용 로깅/검증 레이어.
"""
import logging
from config.settings import settings

logger = logging.getLogger(__name__)


def log_paper_mode() -> None:
    if settings.is_paper:
        logger.info("=" * 50)
        logger.info("  모의매매 모드 (KIS_ENV=vps)")
        logger.info("  실제 돈이 사용되지 않습니다.")
        logger.info("=" * 50)
    else:
        logger.warning("=" * 50)
        logger.warning("  실전매매 모드 (KIS_ENV=prod)")
        logger.warning("  실제 돈이 사용됩니다!")
        logger.warning("=" * 50)
