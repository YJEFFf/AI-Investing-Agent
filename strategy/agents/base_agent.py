import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentOpinion:
    agent_name: str
    verdict: str          # buy | neutral | sell
    confidence: float     # 0.0 ~ 1.0
    reasoning: str
    risk_flags: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def parse_llm_json(text: str) -> dict:
    """LLM 응답에서 JSON 추출 (마크다운 코드블록 대응)"""
    text = text.strip()
    # ```json ... ``` 또는 ``` ... ``` 제거
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text.strip())


class BaseAgent(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def analyze(self, context: dict) -> AgentOpinion:
        """context를 받아 AgentOpinion 반환"""
        ...
