import base64
import logging

import anthropic
import pandas as pd

from config.settings import settings
from strategy.agents.base_agent import BaseAgent, AgentOpinion, parse_llm_json
from strategy.regime_detector import MarketRegime

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """당신은 국내 주식 스윙 매매(2~10일 보유) 전문 트레이더입니다.
첨부된 일봉 차트 이미지(최근 60일)를 분석하여 다음 영업일 시가 기준 매수 적합성을 판단하세요.

차트 구성: 캔들스틱 + EMA20(노란)/EMA60(주황) + 볼린저밴드(파란 점선) + 거래량 + RSI(14)

반드시 아래 JSON 형식으로만 응답하세요:
{
  "verdict": "buy" | "sell" | "skip",
  "confidence": 0.0 ~ 1.0,
  "target_pct": 0.0 ~ 0.15,
  "stop_pct": 0.02 ~ 0.08,
  "reasoning": "한 줄 근거 (차트 패턴 중심)"
}

스윙 매수 기준 (1개 이상 충족 시 buy):
- EMA20이 EMA60 위에 있거나 골든크로스 임박
- 볼린저밴드 하단~중단선 구간에서 반등 징조
- RSI 30~65 구간 (과매도 해소 또는 중립 유지)
- 거래량 동반 양봉 출현
- 캔들 패턴: 망치형, 역망치형, 장악형 양봉

스윙 스킵 기준 (아래 조건에 해당할 때만 skip):
- EMA 명확한 하락 배열 + RSI 70 이상 동시 충족
- 거래량 없는 음봉 3개 이상 연속
- 지지선 명확한 이탈

target_pct: 예상 목표 수익률 (5~12% 권장)
stop_pct: 손절 비율 (3~6% 권장)
confidence 기준:
- 0.60 이상: 신호 존재 → buy
- 0.60 미만: 신호 없음 → skip

중요 — 갭 고려:
실제 매수는 다음 영업일 시초가에 이루어집니다. 전날 종가 대비 ±5% 갭이 발생할 수 있습니다.
연휴 직후, 대형 이슈 직후에는 갭이 더 클 수 있습니다.
stop_pct와 target_pct는 이 갭 변동성을 감안하여 보수적으로 설정하세요
(갭 가능성이 클수록 stop_pct를 넉넉히, target_pct를 현실적으로).

중요: reasoning은 오직 차트에서 보이는 것만 근거로 삼으세요.
뉴스, 매크로, 외부 이슈, 시장 환경 등 차트 외 요소는 언급하지 마세요."""


class ChartAnalystAgent(BaseAgent):
    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

    @property
    def name(self) -> str:
        return "차트 분석가"

    async def analyze(self, context: dict) -> AgentOpinion:
        from strategy.chart_renderer import render_daily_chart

        ohlcv_df: pd.DataFrame = context["ohlcv_df"]
        ta_score: float = context.get("ta_score", 0.0)
        current_price: float = context.get("current_price", 0.0)
        regime: MarketRegime = context.get("regime", MarketRegime.RANGING)
        stock_name: str = context.get("stock_name", "")
        stock_code: str = context.get("stock_code", "")

        try:
            png_bytes = render_daily_chart(ohlcv_df, stock_name, stock_code)
            b64_data = base64.standard_b64encode(png_bytes).decode("utf-8")

            text_ctx = (
                f"종목: {stock_name} ({stock_code})\n"
                f"현재가: {current_price:,.0f}원\n"
                f"시장 레짐: {regime.value}\n"
                f"TA 종합 점수: {ta_score:.1f}/100\n\n"
                f"위 일봉 차트를 보고 스윙 매매 관점에서 판단해 주세요."
            )

            response = await self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=400,
                system=[{
                    "type": "text",
                    "text": _SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64_data,
                            },
                        },
                        {
                            "type": "text",
                            "text": text_ctx,
                        },
                    ],
                }],
            )
            raw = response.content[0].text
            data = parse_llm_json(raw)
            return AgentOpinion(
                agent_name=self.name,
                verdict=data.get("verdict", "skip"),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=data.get("reasoning", ""),
                metadata={
                    "target_pct": float(data.get("target_pct", 0.07)),
                    "stop_pct": float(data.get("stop_pct", 0.05)),
                },
            )

        except Exception as e:
            logger.warning(f"Chart agent LLM 호출 실패: {e}")
            if ta_score >= 65:
                return AgentOpinion(
                    agent_name=self.name, verdict="buy", confidence=0.55,
                    reasoning=f"LLM 실패 폴백 — TA 점수 {ta_score:.1f}",
                    metadata={"target_pct": 0.07, "stop_pct": 0.05},
                )
            return AgentOpinion(
                agent_name=self.name, verdict="skip", confidence=0.3,
                reasoning=f"LLM 실패: {e}",
                metadata={"target_pct": 0.0, "stop_pct": 0.0},
            )


chart_agent = ChartAnalystAgent()
