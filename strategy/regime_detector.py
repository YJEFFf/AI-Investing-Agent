import enum
import pandas as pd
import pandas_ta as ta


class MarketRegime(str, enum.Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"


def detect_regime(ohlcv_df: pd.DataFrame) -> MarketRegime:
    """개별 종목 일봉 데이터로 레짐 분류"""
    if len(ohlcv_df) < 60:
        return MarketRegime.RANGING

    close = ohlcv_df["close"]
    high = ohlcv_df["high"]
    low = ohlcv_df["low"]

    ema_20 = ta.ema(close, length=20)
    ema_60 = ta.ema(close, length=60)
    adx_df = ta.adx(high, low, close, length=14)
    atr_series = ta.atr(high, low, close, length=14)

    current_price = float(close.iloc[-1])
    current_ema20 = float(ema_20.iloc[-1]) if ema_20 is not None else current_price
    current_ema60 = float(ema_60.iloc[-1]) if ema_60 is not None else current_price
    adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 20.0
    atr_val = float(atr_series.iloc[-1]) if atr_series is not None else 0.0
    atr_pct = atr_val / current_price if current_price > 0 else 0.0

    ret_20d = float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) if len(close) > 21 else 0.0

    # 하락 추세 — EMA 하락배열(EMA20 < EMA60)이면 단기 반등과 무관하게 하락 추세로 판단
    if current_ema20 < current_ema60:
        return MarketRegime.TRENDING_DOWN

    # 고변동장 — ATR% 15% 이상을 진짜 고변동성으로 판단 (거래대금 상위 종목 특성 반영)
    if atr_pct > 0.15:
        return MarketRegime.HIGH_VOLATILITY

    # 상승 추세 (ADX > 25로 추세 강도 확인 — 일시적 반등과 구분)
    if adx_val > 25 and current_price > current_ema60 and ret_20d > 0.03:
        return MarketRegime.TRENDING_UP

    return MarketRegime.RANGING


