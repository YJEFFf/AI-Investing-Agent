from strategy.ta_engine import TAResult
from strategy.regime_detector import MarketRegime


# 레짐별 지표 가중치
_WEIGHTS: dict[MarketRegime, dict] = {
    MarketRegime.TRENDING_UP: {
        "rsi": 0.20, "macd": 0.30, "bb": 0.15, "ema": 0.25, "volume": 0.10
    },
    MarketRegime.RANGING: {
        "rsi": 0.25, "macd": 0.20, "bb": 0.30, "ema": 0.15, "volume": 0.10
    },
    MarketRegime.HIGH_VOLATILITY: {
        "rsi": 0.30, "macd": 0.20, "bb": 0.20, "ema": 0.15, "volume": 0.15
    },
    MarketRegime.TRENDING_DOWN: {
        "rsi": 0.25, "macd": 0.25, "bb": 0.20, "ema": 0.20, "volume": 0.10
    },
}

_DEFAULT_WEIGHTS = {"rsi": 0.25, "macd": 0.25, "bb": 0.20, "ema": 0.20, "volume": 0.10}


def compute_score(ta: TAResult, current_price: float, regime: MarketRegime) -> float:
    """TA 지표를 0~100점 복합 점수로 변환.

    두 가지 스윙 패턴을 모두 평가:
    - 저점반등형: RSI 30~45 + BB 하단 → 전통적 역추세 매수
    - 모멘텀돌파형: RSI 50~65 + EMA 우상향 + 거래량 급증 → 추세 추종 매수
    """
    w = _WEIGHTS.get(regime, _DEFAULT_WEIGHTS)
    scores = {}

    # RSI: 저점반등(30~45 최고), 모멘텀(50~65)도 긍정 평가
    if ta.rsi <= 30:
        scores["rsi"] = 90
    elif ta.rsi <= 45:
        scores["rsi"] = 78
    elif ta.rsi <= 55:
        scores["rsi"] = 60
    elif ta.rsi <= 65:
        scores["rsi"] = 55   # 모멘텀 구간 — 이전 25점에서 상향
    elif ta.rsi <= 75:
        scores["rsi"] = 30
    else:
        scores["rsi"] = 10

    # MACD: 히스토그램 방향 + 크로스오버
    if ta.macd_hist > 0 and ta.macd > ta.macd_signal:
        scores["macd"] = 85 if ta.macd_hist > abs(ta.macd) * 0.1 else 65
    elif ta.macd_hist > 0:
        scores["macd"] = 55
    elif ta.macd_hist < 0 and ta.macd < ta.macd_signal:
        scores["macd"] = 15
    else:
        scores["macd"] = 35

    # 볼린저밴드: 하단반등(최고) + 상단돌파시도(거래량 동반 시 긍정)
    bb_range = ta.bb_upper - ta.bb_lower
    if bb_range > 0:
        position = (current_price - ta.bb_lower) / bb_range
        if position <= 0.15:
            scores["bb"] = 85   # 하단 근처 — 저점반등 신호
        elif position <= 0.35:
            scores["bb"] = 70
        elif position <= 0.65:
            scores["bb"] = 50   # 중간
        elif position <= 0.85:
            scores["bb"] = 35
        else:
            # 상단 근처: 거래량 동반 시 돌파 시도로 긍정 평가
            scores["bb"] = 55 if ta.volume_ratio >= 1.5 else 15
    else:
        scores["bb"] = 50

    # EMA 크로스: 20 > 60이면 상승 추세
    if ta.ema_20 > ta.ema_60:
        gap_pct = (ta.ema_20 - ta.ema_60) / ta.ema_60
        scores["ema"] = min(85, 60 + gap_pct * 500)
    else:
        gap_pct = (ta.ema_60 - ta.ema_20) / ta.ema_60
        scores["ema"] = max(15, 40 - gap_pct * 300)

    # 거래량 비율: 평균 대비 높을수록 확신도 ↑
    if ta.volume_ratio >= 2.0:
        scores["volume"] = 90
    elif ta.volume_ratio >= 1.5:
        scores["volume"] = 75
    elif ta.volume_ratio >= 1.0:
        scores["volume"] = 55
    elif ta.volume_ratio >= 0.7:
        scores["volume"] = 40
    else:
        scores["volume"] = 20

    total = sum(scores[k] * w[k] for k in scores)

    # 모멘텀 돌파형 보너스: EMA 우상향 + RSI 50~70 + 거래량 급증 + MACD 양전
    momentum_breakout = (
        ta.ema_20 > ta.ema_60
        and 50 <= ta.rsi <= 70
        and ta.volume_ratio >= 1.5
        and ta.macd_hist > 0
    )
    if momentum_breakout:
        total = min(100.0, total + 12)

    return round(total, 1)
