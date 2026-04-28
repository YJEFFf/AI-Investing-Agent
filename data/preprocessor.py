import pandas as pd


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """결측치 제거, 타입 정규화, 정렬"""
    if df.empty:
        return df
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    df = df[df["volume"] > 0]
    for col in ["open", "high", "low", "close"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)
    if "date" in df.columns:
        df = df.sort_values("date").reset_index(drop=True)
    return df


def resample_to_minutes(ticks: list[dict], interval: int = 1) -> pd.DataFrame:
    """실시간 tick 리스트를 분봉으로 변환"""
    if not ticks:
        return pd.DataFrame()

    df = pd.DataFrame(ticks)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()

    rule = f"{interval}T"
    ohlcv = df["price"].resample(rule).ohlc()
    ohlcv["volume"] = df["volume"].resample(rule).sum()
    ohlcv = ohlcv.dropna()
    return ohlcv.reset_index()
