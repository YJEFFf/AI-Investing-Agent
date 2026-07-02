import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date

from data.fetcher import fetcher
from strategy.ta_engine import TAEngine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import MarketRegime

logger = logging.getLogger(__name__)

_FINAL_COUNT = 40
_MIN_OHLCV_BARS = 60
_CANDIDATE_COUNT = 200  # KRX 거래대금 상위 N개를 TA 평가 대상으로 사용

_EXCLUDE_KEYWORDS = (
    "ETF", "ETN", "TIGER", "KODEX", "KBSTAR", "HANARO",
    "ARIRANG", "ACE", "KOSEF", "SOL ", "TIMEFOLIO", "RISE ",
    "PLUS ", "KIWOOM ", "리츠", "스팩", "SPAC", "인버스", "채권",
    "선물", "TR ", "레버리지",
)


def _fetch_krx_volume_rank(top_n: int = _CANDIDATE_COUNT) -> list[dict]:
    """KRX 거래대금 상위 종목을 pykrx로 조회한다 (장전 분析용)."""
    from pykrx import stock as krx

    today = date.today().strftime("%Y%m%d")

    # ETF 제외 목록
    try:
        etf_set = set(krx.get_etf_ticker_list(today))
    except Exception:
        etf_set = set()

    # 전체 종목 거래대금 조회
    try:
        df = krx.get_market_ohlcv_by_ticker(today, market="ALL")
    except Exception as e:
        logger.warning(f"KRX 거래대금 조회 실패: {e}")
        return []

    df = df[df["거래대금"] > 0]
    df = df[~df.index.isin(etf_set)]
    df = df.sort_values("거래대금", ascending=False)

    result: list[dict] = []
    for ticker in df.index:
        if len(result) >= top_n:
            break
        try:
            name = krx.get_market_ticker_name(ticker)
        except Exception:
            continue
        if any(kw in name for kw in _EXCLUDE_KEYWORDS):
            continue
        result.append({"code": ticker, "name": name})

    logger.info(f"KRX 거래대금 상위 {len(result)}개 종목 수집 완료")
    return result


def _set_krx_env() -> None:
    """pykrx 로그인용 환경변수를 settings에서 주입한다."""
    from config.settings import settings
    if settings.krx_id:
        os.environ.setdefault("KRX_ID", settings.krx_id)
    if settings.krx_pw:
        os.environ.setdefault("KRX_PW", settings.krx_pw)


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
        KRX 거래대금 상위 종목을 수집하고 TA 점수로 필터링해 워치리스트를 반환한다.
        반환 형식: [{"code": "005930", "name": "삼성전자"}, ...]
        """
        _set_krx_env()

        loop = asyncio.get_event_loop()
        candidates = await loop.run_in_executor(None, _fetch_krx_volume_rank)
        if not candidates:
            logger.warning("KRX 거래대금 순위 조회 실패 — 스크리닝 건너뜀")
            return []

        logger.info(f"스크리너: {len(candidates)}개 후보 TA 점수 계산 중...")

        # 1차: 세마포어 5로 동시 요청 제한 (KIS API 500 에러 방지)
        sem = asyncio.Semaphore(5)

        async def _score_with_sem(c: dict) -> "_ScoredStock | None":
            async with sem:
                return await self._score(c, regime)

        results = await asyncio.gather(
            *[_score_with_sem(c) for c in candidates], return_exceptions=True
        )

        scored = [r for r in results if isinstance(r, _ScoredStock)]
        failed = [
            candidates[i] for i, r in enumerate(results)
            if not isinstance(r, _ScoredStock)
        ]

        # 2차: 1차 실패 종목 재시도 (2초 대기 후 세마포어 3)
        if failed:
            logger.info(f"스크리너: 1차 실패 {len(failed)}개 → 2초 후 재시도")
            await asyncio.sleep(2)
            sem2 = asyncio.Semaphore(3)

            async def _retry_with_sem(c: dict) -> "_ScoredStock | None":
                async with sem2:
                    return await self._score(c, regime)

            retry_results = await asyncio.gather(
                *[_retry_with_sem(c) for c in failed], return_exceptions=True
            )
            retry_scored = [r for r in retry_results if isinstance(r, _ScoredStock)]
            scored.extend(retry_scored)
            logger.info(f"스크리너: 재시도 {len(retry_scored)}개 추가 성공")

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
