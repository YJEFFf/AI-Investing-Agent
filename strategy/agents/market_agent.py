import logging

import anthropic

from config.settings import settings
from strategy.agents.base_agent import BaseAgent, AgentOpinion, parse_llm_json
from strategy.regime_detector import MarketRegime

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 국내 주식 시장 거시 환경 분석 전문가입니다.
KOSPI 레짐, 환율, 섹터 동향을 보고 신규 매수가 적합한 시장 환경인지 판단하세요.

반드시 아래 JSON 형식으로만 응답하세요:
{
  "verdict": "buy" | "neutral" | "sell",
  "confidence": 0.0 ~ 1.0,
  "reasoning": "한 줄 근거"
}

판단 기준:
- buy: 상승장, 긍정적 거시 환경
- sell: 하락장, 고위험 환경 (신규 매수 부적합)
- neutral: 횡보장 또는 불확실"""


class MarketAnalystAgent(BaseAgent):
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @property
    def name(self) -> str:
        return "시장 분석가"

    async def analyze(self, context: dict) -> AgentOpinion:
        regime: MarketRegime = context.get("regime", MarketRegime.RANGING)
        kospi_change_pct: float = context.get("kospi_change_pct", 0.0)

        # 하락장이면 LLM 호출 없이 즉시 sell 반환 (비용 절감)
        if regime == MarketRegime.TRENDING_DOWN:
            return AgentOpinion(
                agent_name=self.name,
                verdict="sell",
                confidence=0.95,
                reasoning="하락장 레짐 — 신규 매수 중단",
                risk_flags=["bear_market"],
            )

        user_prompt = (
            f"시장 레짐: {regime.value}\n"
            f"KOSPI 당일 등락: {kospi_change_pct:+.2f}%\n"
            f"고변동장 여부: {'예' if regime == MarketRegime.HIGH_VOLATILITY else '아니오'}"
        )

        try:
            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=200,
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text
            data = parse_llm_json(raw)
            return AgentOpinion(
                agent_name=self.name,
                verdict=data.get("verdict", "neutral"),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                metadata={"regime": regime.value},
            )
        except Exception as e:
            logger.warning(f"Market agent LLM call failed: {e}")
            # LLM 실패 시 레짐으로 폴백
            verdict = "buy" if regime == MarketRegime.TRENDING_UP else "neutral"
            return AgentOpinion(
                agent_name=self.name,
                verdict=verdict,
                confidence=0.4,
                reasoning=f"레짐 기반 폴백: {regime.value}",
            )


market_agent = MarketAnalystAgent()
