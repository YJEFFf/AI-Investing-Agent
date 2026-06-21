from dataclasses import dataclass


@dataclass
class TradeSignal:
    action: str           # buy | sell | skip
    position_ratio: float  # 0.0 ~ 1.0
    stop_price: float
    stop_type: str         # fixed | trailing | atr
    reasoning: str
    ta_score: float
    risk_level: str
    target_price: float = 0.0
    chart_verdict: str = "skip"
    chart_confidence: float = 0.0
    stop_pct: float = 0.05    # LLM이 설정한 손절 비율 (실제 체결가에 적용)
    target_pct: float = 0.08  # LLM이 설정한 목표 비율 (실제 체결가에 적용)
    # ML 학습용 피처
    rsi: float = 0.0
    macd_hist: float = 0.0
    bb_pct: float = 0.0       # (현재가 - BB하단) / (BB상단 - BB하단), 0~1
    ema_ratio: float = 0.0    # EMA20 / EMA60, >1이면 상승 배열
    volume_ratio: float = 0.0
    regime: str = ""
    current_price: float = 0.0
    llm_called: bool = False  # LLM 분석까지 진행됐는지 여부
