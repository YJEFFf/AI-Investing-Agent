from config.settings import settings
from strategy.agents.base_agent import BaseAgent, AgentOpinion


class RiskManagerAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "리스크 관리자"

    async def analyze(self, context: dict) -> AgentOpinion:
        opinions: list[AgentOpinion] = context["opinions"]
        open_positions: int = context.get("open_positions", 0)
        daily_pnl_pct: float = context.get("daily_pnl_pct", 0.0)
        drawdown_pct: float = context.get("drawdown_pct", 0.0)

        risk_flags = []

        # 포트폴리오 상태 체크
        if open_positions >= settings.max_open_positions:
            risk_flags.append("max_positions_reached")
        if daily_pnl_pct <= -settings.daily_loss_cap:
            risk_flags.append("daily_loss_cap_hit")
        if drawdown_pct >= settings.max_drawdown_pct:
            risk_flags.append("max_drawdown_hit")

        # 포지션 크기 권장
        if risk_flags:
            position_ratio = 0.0  # 모든 risk_flag는 진입 불가 조건
        else:
            position_ratio = 1.0  # 정상

        # 위험도 레벨
        if position_ratio == 0.0:
            risk_level = "high"
            verdict = "sell"
            confidence = 0.9
            reasoning = f"진입 불가: {', '.join(risk_flags)}"
        else:
            risk_level = "low"
            verdict = "buy"
            confidence = 0.8
            reasoning = "포트폴리오 상태 정상"

        return AgentOpinion(
            agent_name=self.name,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            risk_flags=risk_flags,
            metadata={"position_ratio": position_ratio, "risk_level": risk_level},
        )


risk_agent = RiskManagerAgent()
