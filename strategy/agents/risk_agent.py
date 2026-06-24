from strategy.agents.base_agent import BaseAgent, AgentOpinion


class RiskManagerAgent(BaseAgent):
    @property
    def name(self) -> str:
        return "리스크 관리자"

    async def analyze(self, context: dict) -> AgentOpinion:
        return AgentOpinion(
            agent_name=self.name,
            verdict="buy",
            confidence=0.8,
            reasoning="포트폴리오 상태 정상",
            risk_flags=[],
            metadata={"position_ratio": 1.0, "risk_level": "low"},
        )


risk_agent = RiskManagerAgent()
