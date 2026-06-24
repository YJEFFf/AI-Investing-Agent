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

    ema_60 = ta.ema(close, length=60)
    adx_df = ta.adx(high, low, close, length=14)
    atr_series = ta.atr(high, low, close, length=14)

    current_price = float(close.iloc[-1])
    current_ema60 = float(ema_60.iloc[-1]) if ema_60 is not None else current_price
    adx_val = float(adx_df["ADX_14"].iloc[-1]) if adx_df is not None else 20.0
    atr_val = float(atr_series.iloc[-1]) if atr_series is not None else 0.0
    atr_pct = atr_val / current_price if current_price > 0 else 0.0

    # 최근 20일 수익률
    ret_20d = float((close.iloc[-1] - close.iloc[-21]) / close.iloc[-21]) if len(close) > 21 else 0.0

    # 하락 추세 — HIGH_VOLATILITY보다 먼저 체크 (ADX 조건 없음, 흘러내리는 장세도 감지)
    if current_price < current_ema60 and ret_20d < -0.02:
        return MarketRegime.TRENDING_DOWN

    # 고변동장 (개별 종목은 지수보다 변동성이 크므로 3% 기준)
    if atr_pct > 0.03:
        return MarketRegime.HIGH_VOLATILITY

    # 상승 추세 (ADX > 25로 추세 강도 확인 — 일시적 반등과 구분)
    if adx_val > 25 and current_price > current_ema60 and ret_20d > 0.03:
        return MarketRegime.TRENDING_UP

    return MarketRegime.RANGING


