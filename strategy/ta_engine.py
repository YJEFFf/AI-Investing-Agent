from dataclasses import dataclass
import pandas as pd
import pandas_ta as ta


@dataclass
class TAResult:
    rsi: float
    macd: float
    macd_signal: float
    macd_hist: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    ema_20: float
    ema_60: float
    atr: float
    volume_ratio: float  # 현재 거래량 / 20일 평균 거래량


class TAEngine:
    def compute(self, df: pd.DataFrame) -> TAResult | None:
        """OHLCV DataFrame으로 모든 지표 계산"""
        if len(df) < 60:
            return None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        volume = df["volume"]

        rsi_series = ta.rsi(close, length=14)
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        bb_df = ta.bbands(close, length=20, std=2)
        ema_20 = ta.ema(close, length=20)
        ema_60 = ta.ema(close, length=60)
        atr_series = ta.atr(high, low, close, length=14)

        vol_ma20 = volume.rolling(20).mean()

        return TAResult(
            rsi=float(rsi_series.iloc[-1]),
            macd=float(macd_df["MACD_12_26_9"].iloc[-1]),
            macd_signal=float(macd_df["MACDs_12_26_9"].iloc[-1]),
            macd_hist=float(macd_df["MACDh_12_26_9"].iloc[-1]),
            bb_upper=float(bb_df[[c for c in bb_df.columns if c.startswith("BBU_")][0]].iloc[-1]),
            bb_mid=float(bb_df[[c for c in bb_df.columns if c.startswith("BBM_")][0]].iloc[-1]),
            bb_lower=float(bb_df[[c for c in bb_df.columns if c.startswith("BBL_")][0]].iloc[-1]),
            ema_20=float(ema_20.iloc[-1]),
            ema_60=float(ema_60.iloc[-1]),
            atr=float(atr_series.iloc[-1]),
            volume_ratio=float(volume.iloc[-1] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 1.0,
        )


ta_engine = TAEngine()
