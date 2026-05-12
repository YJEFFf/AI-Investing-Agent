"""NASDAQ 100 구성 종목을 Wikipedia에서 가져와 us_watchlist.yaml을 갱신한다.
매주 월요일 21:00 KST 스케줄러에 의해 호출된다."""
import logging
from pathlib import Path

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_YAML_PATH = Path(__file__).parent.parent / "config" / "us_watchlist.yaml"
_WIKI_URL = "https://en.wikipedia.org/wiki/Nasdaq-100"


def _fetch_nasdaq100() -> list[dict]:
    """Wikipedia에서 NASDAQ 100 구성 종목 파싱. {ticker, name} 리스트 반환."""
    tables = pd.read_html(_WIKI_URL)
    # "Ticker" 컬럼을 포함한 첫 번째 테이블 사용
    df = next((t for t in tables if "Ticker" in t.columns), None)
    if df is None:
        raise ValueError("NASDAQ 100 테이블을 찾지 못했습니다 — Wikipedia 구조 변경 확인 필요")

    name_col = next((c for c in df.columns if "Company" in c or "Name" in c), None)
    tickers = df["Ticker"].dropna().str.strip().tolist()
    names = df[name_col].dropna().str.strip().tolist() if name_col else tickers

    return [
        {"ticker": t, "name": n}
        for t, n in zip(tickers, names)
        if t and len(t) <= 6  # 이상한 값 필터
    ]


def _load_current() -> list[dict]:
    if not _YAML_PATH.exists():
        return []
    with open(_YAML_PATH) as f:
        data = yaml.safe_load(f) or {}
    return data.get("stocks", [])


def _save(stocks: list[dict]) -> None:
    with open(_YAML_PATH, "w") as f:
        yaml.dump({"stocks": stocks}, f, allow_unicode=True, default_flow_style=False)


async def update_nasdaq100_watchlist() -> dict:
    """
    NASDAQ 100을 갱신하고 변경 내역을 반환한다.
    Returns: {"added": [...], "removed": [...], "total": int}
    """
    try:
        new_stocks = _fetch_nasdaq100()
    except Exception as e:
        logger.error(f"NASDAQ 100 갱신 실패: {e}")
        return {"added": [], "removed": [], "total": 0, "error": str(e)}

    current_stocks = _load_current()
    current_tickers = {s["ticker"] for s in current_stocks}
    new_tickers = {s["ticker"] for s in new_stocks}

    added = sorted(new_tickers - current_tickers)
    removed = sorted(current_tickers - new_tickers)

    if added or removed:
        _save(new_stocks)
        logger.info(f"NASDAQ 100 갱신: +{len(added)}개 추가, -{len(removed)}개 제거 → 총 {len(new_stocks)}개")
        if added:
            logger.info(f"추가: {added}")
        if removed:
            logger.info(f"제거: {removed}")
    else:
        logger.info(f"NASDAQ 100 변동 없음 ({len(new_stocks)}개 유지)")

    return {"added": added, "removed": removed, "total": len(new_stocks)}
