from dataclasses import dataclass
from strategy.agents.base_agent import BaseAgent, AgentOpinion


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


class DecisionAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "최종 결정자"

    async def analyze(self, context: dict) -> AgentOpinion:
        # 이 에이전트는 직접 호출하지 않고 make_decision()을 사용
        raise NotImplementedError

    def make_decision(
        self,
        ta_opinion: AgentOpinion,
        news_opinion: AgentOpinion,
        market_opinion: AgentOpinion,
        risk_opinion: AgentOpinion,
        current_price: float,
        atr: float,
        ta_score: float,
    ) -> TradeSignal:
        risk_meta = risk_opinion.metadata
        position_ratio = risk_meta.get("position_ratio", 1.0)
        risk_level = risk_meta.get("risk_level", "low")

        # 리스크 관리자가 진입 불가로 판단하면 즉시 스킵
        if position_ratio == 0.0:
            return TradeSignal(
                action="skip",
                position_ratio=0.0,
                stop_price=0.0,
                stop_type="none",
                reasoning=f"리스크 관리자 차단: {risk_opinion.reasoning}",
                ta_score=ta_score,
                risk_level=risk_level,
            )

        # TA 하드 룰: 45점 미만이면 무조건 스킵
        if ta_score < 45:
            return TradeSignal(
                action="skip",
                position_ratio=0.0,
                stop_price=0.0,
                stop_type="none",
                reasoning=f"TA 점수 미달 ({ta_score:.1f} < 45)",
                ta_score=ta_score,
                risk_level=risk_level,
            )

        # 신뢰도 가중 합산
        buy_score = sum(
            op.confidence for op in [ta_opinion, news_opinion, market_opinion]
            if op.verdict == "buy"
        )
        sell_score = sum(
            op.confidence for op in [ta_opinion, news_opinion, market_opinion]
            if op.verdict == "sell"
        )
        buy_count = sum(1 for op in [ta_opinion, news_opinion, market_opinion] if op.verdict == "buy")

        # 매도 신호 우선: 2개 이상 sell이면 청산
        sell_count = sum(1 for op in [ta_opinion, news_opinion, market_opinion] if op.verdict == "sell")
        if sell_count >= 2:
            return TradeSignal(
                action="sell",
                position_ratio=1.0,
                stop_price=0.0,
                stop_type="signal",
                reasoning=f"매도 신호 {sell_count}/3: {ta_opinion.reasoning}",
                ta_score=ta_score,
                risk_level=risk_level,
            )

        # 매수 결정
        if buy_count >= 3 or (buy_count == 2 and buy_score >= 1.4):
            final_ratio = position_ratio  # 리스크 관리자 조정값 반영
        elif buy_count >= 2:
            final_ratio = position_ratio * 0.5
        else:
            return TradeSignal(
                action="skip",
                position_ratio=0.0,
                stop_price=0.0,
                stop_type="none",
                reasoning=f"매수 합의 미달 ({buy_count}/3)",
                ta_score=ta_score,
                risk_level=risk_level,
            )

        # 손절가 계산
        stop_price, stop_type = self._calc_stop(current_price, atr, risk_level)

        reasons = " | ".join([
            f"TA:{ta_opinion.verdict}({ta_opinion.confidence:.2f})",
            f"뉴스:{news_opinion.verdict}({news_opinion.confidence:.2f})",
            f"시장:{market_opinion.verdict}({market_opinion.confidence:.2f})",
        ])

        return TradeSignal(
            action="buy",
            position_ratio=final_ratio,
            stop_price=stop_price,
            stop_type=stop_type,
            reasoning=reasons,
            ta_score=ta_score,
            risk_level=risk_level,
        )

    def _calc_stop(self, price: float, atr: float, risk_level: str) -> tuple[float, str]:
        from config.settings import settings
        if atr > 0 and atr / price > 0.015:
            # 고변동 시 ATR 기반 손절
            stop = price - (2.0 * atr)
            return round(stop), "atr"
        stop_pct = settings.default_stop_pct
        return round(price * (1 - stop_pct)), "fixed"


decision_agent = DecisionAgent()
