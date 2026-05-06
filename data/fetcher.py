import logging
from datetime import datetime
from pathlib import Path
import pandas as pd

from kis.market_data import market_data

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(__file__).parent.parent / "data_store" / "ohlcv_cache"
_CACHE_DIR.mkdir(parents=True, exist_ok=True)


class OHLCVFetcher:
    async def fetch_daily(self, stock_code: str, count: int = 120) -> pd.DataFrame:
        cache_path = _CACHE_DIR / f"{stock_code}_daily.parquet"

        cached = self._load_cache(cache_path)
        if cached is not None and len(cached) >= count:
            logger.debug(f"Cache hit: {stock_code} daily ({len(cached)} rows)")
            return cached.tail(count).reset_index(drop=True)

        logger.info(f"Fetching daily OHLCV: {stock_code}")
        df = await market_data.get_daily_ohlcv(stock_code, count)
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df

    async def fetch_kospi(self, count: int = 250) -> pd.DataFrame:
        cache_path = _CACHE_DIR / "KOSPI_daily.parquet"
        cached = self._load_cache(cache_path)
        if cached is not None and len(cached) >= count:
            return cached.tail(count).reset_index(drop=True)

        df = await market_data.get_kospi_ohlcv(count)
        if not df.empty:
            df.to_parquet(cache_path, index=False)
        return df

    def _load_cache(self, path: Path) -> pd.DataFrame | None:
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path)
            if df.empty:
                return None
            # 오늘 갱신된 캐시만 유효 — 어제 이전 캐시는 최신 봉 누락
            cache_date = datetime.fromtimestamp(path.stat().st_mtime).date()
            if cache_date < datetime.now().date():
                logger.debug(f"캐시 만료 재조회: {path.name} (저장일: {cache_date})")
                return None
            return df
        except Exception:
            return None

    def invalidate_cache(self, stock_code: str) -> None:
        path = _CACHE_DIR / f"{stock_code}_daily.parquet"
        if path.exists():
            path.unlink()


fetcher = OHLCVFetcher()
