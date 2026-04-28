from strategy.agents.base_agent import BaseAgent, AgentOpinion
from strategy.ta_engine import TAResult


class TechnicalAnalystAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "기술적 분석가"

    async def analyze(self, context: dict) -> AgentOpinion:
        ta: TAResult = context["ta_result"]
        ta_score: float = context["ta_score"]
        current_price: float = context["current_price"]

        verdict, confidence, reasoning = self._evaluate(ta, ta_score, current_price)
        return AgentOpinion(
            agent_name=self.name,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            metadata={"ta_score": ta_score},
        )

    def _evaluate(self, ta: TAResult, score: float, price: float) -> tuple[str, float, str]:
        reasons = []

        # RSI 판단
        if ta.rsi <= 30:
            reasons.append(f"RSI {ta.rsi:.1f} 과매도 구간")
        elif ta.rsi <= 45:
            reasons.append(f"RSI {ta.rsi:.1f} 저점 구간")
        elif ta.rsi >= 70:
            reasons.append(f"RSI {ta.rsi:.1f} 과매수 → 매도 신호")

        # MACD 판단
        if ta.macd_hist > 0 and ta.macd > ta.macd_signal:
            reasons.append("MACD 골든크로스")
        elif ta.macd_hist < 0 and ta.macd < ta.macd_signal:
            reasons.append("MACD 데드크로스")

        # 볼린저밴드 판단
        if price <= ta.bb_lower * 1.02:
            reasons.append("볼린저밴드 하단 근접")
        elif price >= ta.bb_upper * 0.98:
            reasons.append("볼린저밴드 상단 근접 → 과매수")

        # EMA 판단
        if ta.ema_20 > ta.ema_60:
            reasons.append("EMA 20 > EMA 60 (상승 추세)")
        else:
            reasons.append("EMA 20 < EMA 60 (하락 추세)")

        reasoning = " | ".join(reasons) if reasons else f"TA 점수 {score:.1f}"

        # 점수 기반 최종 verdict
        if score >= 65:
            return "buy", min(0.95, score / 100), reasoning
        elif score >= 45:
            return "neutral", 0.5, reasoning
        else:
            # 매도 신호 강도에 따라 분기
            if ta.rsi >= 70 or (ta.macd_hist < 0 and ta.macd < ta.macd_signal):
                return "sell", min(0.85, (100 - score) / 100), reasoning
            return "neutral", 0.4, reasoning


ta_agent = TechnicalAnalystAgent()
