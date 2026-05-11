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
