from pathlib import Path
import pandas as pd

_CACHE_DIR = Path(__file__).parent.parent / "data_store" / "ohlcv_cache"


def load_ohlcv(stock_code: str) -> pd.DataFrame:
    path = _CACHE_DIR / f"{stock_code}_daily.parquet"
    if not path.exists():
        raise FileNotFoundError(f"캐시 없음: {path} — run_backtest 전에 OHLCV를 먼저 수집하세요.")
    df = pd.read_parquet(path)
    df = df.sort_values("date").reset_index(drop=True)
    return df
