import enum
import pandas as pd
import pandas_ta as ta


class MarketRegime(str, enum.Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"


def detect_regime(kospi_df: pd.DataFrame) -> MarketRegime:
    """KOSPI 일봉 데이터로 현재 시장 레짐 분류"""
    if len(kospi_df) < 60:
        return MarketRegime.RANGING

    close = kospi_df["close"]
    high = kospi_df["high"]
    low = kospi_df["low"]

    ema_200 = ta.ema(close, length=200)
    adx_df = ta.adx(high, low, close, length=14)
    atr_series = ta.atr(high, low, close, length=14)

    current_price = float(close.iloc[-1])
    current_ema200 = float(ema_200.iloc[-1]) if ema_200 is not None else current_price
    adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 20.0
    atr_val = float(atr_series.iloc[-1]) if atr_series is not None else 0.0
    atr_pct = atr_val / current_price if current_price > 0 else 0.0

    # 최근 20일 수익률
    ret_20d = float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) if len(close) > 21 else 0.0

    # 고변동장: ATR/가격 > 2%
    if atr_pct > 0.02:
        return MarketRegime.HIGH_VOLATILITY

    # 추세장 판단 (ADX > 25 + 방향)
    if adx_val > 25:
        if current_price > current_ema200 and ret_20d > 0.03:
            return MarketRegime.TRENDING_UP
        if current_price < current_ema200 and ret_20d < -0.03:
            return MarketRegime.TRENDING_DOWN

    return MarketRegime.RANGING


