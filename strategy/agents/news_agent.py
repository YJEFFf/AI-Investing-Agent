import logging

import anthropic

from config.settings import settings
from strategy.agents.base_agent import BaseAgent, AgentOpinion, parse_llm_json

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 국내 주식 뉴스 분석 전문가입니다.
주어진 뉴스 헤드라인을 분석하여 해당 종목의 단기 주가에 미치는 영향을 판단하세요.

반드시 아래 JSON 형식으로만 응답하세요:
{
  "verdict": "buy" | "neutral" | "sell",
  "confidence": 0.0 ~ 1.0,
  "risk_flags": [],
  "reasoning": "한 줄 근거"
}

risk_flags 가능한 값: earnings_tomorrow, sector_selloff, regulatory_risk, major_shareholder_sell, accounting_issue, delisting_risk

판단 기준:
- buy: 명확한 호재 (신규 계약, 실적 개선, 제품 출시 등)
- sell: 명확한 악재 (실적 쇼크, 규제, 사고, 소송 등)
- neutral: 중립적이거나 정보 불충분"""


class NewsAnalystAgent(BaseAgent):
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @property
    def name(self) -> str:
        return "뉴스 분석가"

    async def analyze(self, context: dict) -> AgentOpinion:
        news_items: list = context.get("news_items", [])
        stock_code: str = context.get("stock_code", "")
        stock_name: str = context.get("stock_name", stock_code)

        if not news_items:
            return AgentOpinion(
                agent_name=self.name,
                verdict="neutral",
                confidence=0.3,
                reasoning="수집된 뉴스 없음",
            )

        headlines = "\n".join(
            f"- {n.title} ({n.published_at.strftime('%H:%M')})"
            for n in news_items[:5]
        )
        user_prompt = f"종목: {stock_name} ({stock_code})\n\n최근 뉴스:\n{headlines}"

        try:
            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=300,
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
                risk_flags=data.get("risk_flags", []),
            )
        except Exception as e:
            logger.warning(f"News agent LLM call failed: {e}")
            return AgentOpinion(
                agent_name=self.name,
                verdict="neutral",
                confidence=0.3,
                reasoning=f"분석 실패: {e}",
            )


news_agent = NewsAnalystAgent()
