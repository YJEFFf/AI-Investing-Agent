import asyncio
import logging
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

from data.fetcher import fetcher
from strategy.ta_engine import TAEngine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import MarketRegime

logger = logging.getLogger(__name__)

_FINAL_COUNT = 40
_MIN_OHLCV_BARS = 60

_EXCLUDE_KEYWORDS = (
    "ETF", "ETN", "TIGER", "KODEX", "KBSTAR", "HANARO",
    "ARIRANG", "ACE", "KOSEF", "SOL ", "TIMEFOLIO", "RISE ",
    "PLUS ", "KIWOOM ", "리츠", "스팩", "SPAC", "인버스", "채권",
    "선물", "TR ", "레버리지",
)

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _fetch_naver_volume_rank(pages: int = 5) -> list[dict]:
    """네이버 금융 거래대금 순위에서 KOSPI/KOSDAQ 개별주를 수집한다."""
    stocks: list[tuple] = []
    for sosok, market in [("0", "KOSPI"), ("1", "KOSDAQ")]:
        for page in range(1, pages + 1):
            try:
                url = (
                    f"https://finance.naver.com/sise/sise_quant.naver"
                    f"?sosok={sosok}&page={page}"
                )
                resp = requests.get(url, headers=_HEADERS, timeout=5)
                soup = BeautifulSoup(resp.text, "html.parser")
                for row in soup.select("table.type_2 tr"):
                    cols = row.select("td")
                    if len(cols) < 7:
                        continue
                    a = cols[1].select_one("a")
                    if not a or "code=" not in a.get("href", ""):
                        continue
                    code = a["href"].split("code=")[-1].strip()
                    name = a.text.strip()
                    if not code.isdigit() or len(code) != 6:
                        continue
                    if any(kw in name for kw in _EXCLUDE_KEYWORDS):
                        continue
                    try:
                        vol = int(cols[6].text.strip().replace(",", ""))
                    except ValueError:
                        vol = 0
                    stocks.append((code, name, vol))
            except Exception as e:
                logger.warning(f"네이버 거래대금 순위 조회 실패 (sosok={sosok}, page={page}): {e}")

    stocks.sort(key=lambda x: x[2], reverse=True)
    seen: set[str] = set()
    result: list[dict] = []
    for code, name, _ in stocks:
        if code not in seen:
            seen.add(code)
            result.append({"code": code, "name": name})
    return result


@dataclass
class _ScoredStock:
    code: str
    name: str
    ta_score: float


class StockScreener:
    def __init__(self):
        self._ta_engine = TAEngine()

    async def run(
        self,
        regime: MarketRegime = MarketRegime.RANGING,
        top_n: int = _FINAL_COUNT,
    ) -> list[dict]:
        """
        네이버 금융 거래대금 순위에서 후보 종목을 수집하고
        TA 점수로 필터링해 워치리스트를 반환한다.
        반환 형식: [{"code": "005930", "name": "삼성전자"}, ...]
        """
        loop = asyncio.get_event_loop()
        candidates = await loop.run_in_executor(None, _fetch_naver_volume_rank)
        if not candidates:
            logger.warning("네이버 거래대금 순위 조회 실패 — 스크리닝 건너뜀")
            return []

        logger.info(f"스크리너: {len(candidates)}개 후보 TA 점수 계산 중...")
        sem = asyncio.Semaphore(10)

        async def _score_with_sem(c: dict) -> "_ScoredStock | None":
            async with sem:
                return await self._score(c, regime)

        tasks = [_score_with_sem(c) for c in candidates]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored = [r for r in results if isinstance(r, _ScoredStock)]
        scored.sort(key=lambda x: x.ta_score, reverse=True)
        selected = scored[:top_n]

        if selected:
            logger.info(
                f"스크리너: {len(scored)}개 평가 완료 → 상위 {len(selected)}개 선정 "
                f"(TA {selected[-1].ta_score:.1f}~{selected[0].ta_score:.1f}점)"
            )
        return [{"code": s.code, "name": s.name} for s in selected]

    async def _score(self, stock: dict, regime: MarketRegime) -> "_ScoredStock | None":
        code = stock["code"]
        try:
            df = await fetcher.fetch_daily(code, 120)
            if df is None or len(df) < _MIN_OHLCV_BARS:
                return None

            ta_result = self._ta_engine.compute(df)
            if ta_result is None:
                return None

            current_price = float(df["close"].iloc[-1])
            score = compute_score(ta_result, current_price, regime)
            return _ScoredStock(code=code, name=stock["name"], ta_score=score)
        except Exception as e:
            logger.debug(f"스크리닝 실패 {code}: {e}")
            return None


stock_screener = StockScreener()
