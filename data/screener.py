import asyncio
import logging
from dataclasses import dataclass

from data.fetcher import fetcher
from kis.market_data import market_data
from strategy.ta_engine import TAEngine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import MarketRegime

logger = logging.getLogger(__name__)

_CANDIDATE_COUNT = 200
_FINAL_COUNT = 40
_MIN_OHLCV_BARS = 60


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
        거래대금 상위 종목을 TA 점수로 필터링해 워치리스트 반환.
        반환 형식: [{"code": "005930", "name": "삼성전자"}, ...]
        """
        candidates = await market_data.get_volume_rank(_CANDIDATE_COUNT)
        if not candidates:
            logger.warning("거래대금 순위 조회 실패 — 스크리닝 건너뜀")
            return []

        # ETF·ETN·리츠 제외: 코드가 순수 6자리 숫자인 종목만 허용
        # ETN은 Q/E 등 문자 포함, ETF 중 일부도 특수문자 포함
        # 이름에 ETF·ETN·TIGER·KODEX 등 포함 시 추가 제외
        _ETF_KEYWORDS = ("ETF", "ETN", "TIGER", "KODEX", "KBSTAR", "HANARO",
                         "ARIRANG", "ACE", "KOSEF", "SOL ", "TIMEFOLIO", "리츠")
        candidates = [
            c for c in candidates
            if c["code"].isdigit() and len(c["code"]) == 6
            and not any(kw in c["name"] for kw in _ETF_KEYWORDS)
        ]

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
