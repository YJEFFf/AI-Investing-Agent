import asyncio
import logging

import pandas as pd

from data.news_scraper import NewsItem
from strategy.ta_engine import ta_engine, TAResult
from strategy.signal_scorer import compute_score
from strategy.regime_detector import MarketRegime, detect_regime
from strategy.agents.ta_agent import ta_agent
from strategy.agents.news_agent import news_agent
from strategy.agents.market_agent import market_agent
from strategy.agents.risk_agent import risk_agent
from strategy.agents.decision_agent import decision_agent, TradeSignal

logger = logging.getLogger(__name__)


class MultiAgentStrategy:
    async def evaluate(
        self,
        stock_code: str,
        stock_name: str,
        ohlcv_df: pd.DataFrame,
        news_items: list[NewsItem],
        kospi_df: pd.DataFrame,
        open_positions: int,
        daily_pnl_pct: float,
        drawdown_pct: float,
    ) -> TradeSignal:
        # 1. TA 계산
        ta_result = ta_engine.compute(ohlcv_df)
        if ta_result is None:
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning="데이터 부족 (< 60봉)",
                ta_score=0.0, risk_level="high",
            )

        current_price = float(ohlcv_df["close"].iloc[-1])

        # 2. 시장 레짐 감지
        regime = detect_regime(kospi_df)

        # 3. TA 복합 점수
        ta_score = compute_score(ta_result, current_price, regime)

        # 4. TA 하드 룰 — 45점 미만이면 에이전트 토론 생략
        if ta_score < 45:
            logger.debug(f"{stock_code} TA 점수 미달 ({ta_score:.1f}) → 스킵")
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning=f"TA 점수 미달 ({ta_score:.1f})",
                ta_score=ta_score, risk_level="low",
            )

        # 5. 하락장이면 토론 생략
        if regime == MarketRegime.TRENDING_DOWN:
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning="하락장 — 신규 매수 중단",
                ta_score=ta_score, risk_level="high",
            )

        # 6. 기술적/뉴스/시장 에이전트 병렬 실행
        kospi_change_pct = float(
            (kospi_df["close"].iloc[-1] - kospi_df["close"].iloc[-2])
            / kospi_df["close"].iloc[-2] * 100
        ) if len(kospi_df) >= 2 else 0.0

        ta_ctx = {"ta_result": ta_result, "ta_score": ta_score, "current_price": current_price}
        news_ctx = {"stock_code": stock_code, "stock_name": stock_name, "news_items": news_items}
        market_ctx = {"regime": regime, "kospi_change_pct": kospi_change_pct}

        ta_op, news_op, market_op = await asyncio.gather(
            ta_agent.analyze(ta_ctx),
            news_agent.analyze(news_ctx),
            market_agent.analyze(market_ctx),
        )

        logger.info(
            f"[{stock_code}] TA:{ta_op.verdict}({ta_op.confidence:.2f}) "
            f"뉴스:{news_op.verdict}({news_op.confidence:.2f}) "
            f"시장:{market_op.verdict}({market_op.confidence:.2f})"
        )

        # 7. 리스크 관리자 평가
        risk_ctx = {
            "opinions": [ta_op, news_op, market_op],
            "open_positions": open_positions,
            "daily_pnl_pct": daily_pnl_pct,
            "drawdown_pct": drawdown_pct,
        }
        risk_op = await risk_agent.analyze(risk_ctx)

        # 8. 최종 결정
        signal = decision_agent.make_decision(
            ta_op, news_op, market_op, risk_op,
            current_price, ta_result.atr, ta_score,
        )

        logger.info(f"[{stock_code}] 최종 결정: {signal.action} | {signal.reasoning}")
        return signal


multi_agent_strategy = MultiAgentStrategy()
