import logging

import pandas as pd

from strategy.ta_engine import ta_engine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import MarketRegime, detect_regime
from strategy.agents.chart_agent import chart_agent
from strategy.agents.risk_agent import risk_agent
from strategy.agents.decision_agent import TradeSignal

logger = logging.getLogger(__name__)


class MultiAgentStrategy:
    async def evaluate(
        self,
        stock_code: str,
        stock_name: str,
        ohlcv_df: pd.DataFrame,
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

        # 2. 시장 레짐
        regime = detect_regime(kospi_df)

        # 3. TA 복합 점수
        ta_score = compute_score(ta_result, current_price, regime)

        # ML 피처 사전 계산
        bb_range = ta_result.bb_upper - ta_result.bb_lower
        bb_pct = (current_price - ta_result.bb_lower) / bb_range if bb_range > 0 else 0.5
        ema_ratio = ta_result.ema_20 / ta_result.ema_60 if ta_result.ema_60 > 0 else 1.0
        _ta_extras = dict(
            rsi=round(ta_result.rsi, 2),
            macd_hist=round(ta_result.macd_hist, 4),
            bb_pct=round(bb_pct, 4),
            ema_ratio=round(ema_ratio, 4),
            volume_ratio=round(ta_result.volume_ratio, 2),
            regime=regime.value,
            current_price=current_price,
        )

        # 4. TA 최소 필터 — 25점 미만이면 스킵 (이미지가 주 판단)
        if ta_score < 25:
            logger.debug(f"{stock_code} TA 점수 미달 ({ta_score:.1f}) → 스킵")
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning=f"TA 점수 미달 ({ta_score:.1f})",
                ta_score=ta_score, risk_level="low",
            )

        # 5. 하락장이면 스킵
        if regime == MarketRegime.TRENDING_DOWN:
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning="하락장 — 신규 매수 중단",
                ta_score=ta_score, risk_level="high",
            )

        # 6. 차트 이미지 분석 (LLM vision)
        chart_op = await chart_agent.analyze({
            "ohlcv_df": ohlcv_df,
            "ta_score": ta_score,
            "current_price": current_price,
            "regime": regime,
            "stock_name": stock_name,
            "stock_code": stock_code,
        })

        logger.info(
            f"[{stock_code}] 차트:{chart_op.verdict}({chart_op.confidence:.2f}) | {chart_op.reasoning}"
        )

        # 7. 리스크 관리자 (룰 기반)
        risk_op = await risk_agent.analyze({
            "opinions": [chart_op],
            "open_positions": open_positions,
            "daily_pnl_pct": daily_pnl_pct,
            "drawdown_pct": drawdown_pct,
        })

        risk_meta = risk_op.metadata
        position_ratio = risk_meta.get("position_ratio", 1.0)
        risk_level = risk_meta.get("risk_level", "low")

        # 8. 최종 결정
        if position_ratio == 0.0:
            return TradeSignal(
                action="skip", position_ratio=0.0, stop_price=0.0,
                stop_type="none", reasoning=f"리스크 차단: {risk_op.reasoning}",
                ta_score=ta_score, risk_level=risk_level,
                chart_verdict=chart_op.verdict, chart_confidence=chart_op.confidence,
                llm_called=True, **_ta_extras,
            )

        if chart_op.verdict == "buy" and chart_op.confidence >= 0.60:
            # LLM 반환값 그대로 사용, 물리적 범위만 클램핑 (0~100% 벗어나는 경우만 방어)
            stop_pct = max(0.02, min(float(chart_op.metadata.get("stop_pct", 0.05)), 0.12))
            target_pct = max(0.03, min(float(chart_op.metadata.get("target_pct", 0.05)), 0.15))
            # HIGH_VOLATILITY: ATR 기반 최소 스탑 보정 (노이즈 손절 방지)
            if regime == MarketRegime.HIGH_VOLATILITY:
                atr_pct = ta_result.atr / current_price
                atr_stop = min(round(atr_pct * 1.5, 4), 0.10)
                if stop_pct < atr_stop:
                    logger.info(f"[{stock_code}] HIGH_VOL 스탑 확대: {stop_pct:.3f} → {atr_stop:.3f} (ATR%={atr_pct:.3%})")
                    stop_pct = atr_stop
            # R:R 필터: target이 stop의 1.2배 미만이면 진입 불리 → 스킵
            # HIGH_VOLATILITY는 ATR로 stop이 강제 확대되므로 R:R 역전이 구조적 — 필터 면제
            if regime != MarketRegime.HIGH_VOLATILITY and target_pct < stop_pct * 1.2:
                logger.info(
                    f"[{stock_code}] R:R 불량 스킵: stop={stop_pct:.1%} / target={target_pct:.1%} "
                    f"(비율={target_pct/stop_pct:.2f} < 1.2)"
                )
                return TradeSignal(
                    action="skip", position_ratio=0.0, stop_price=0.0,
                    stop_type="none",
                    reasoning=f"R:R 불량 (stop={stop_pct:.1%} / target={target_pct:.1%})",
                    ta_score=ta_score, risk_level=risk_level,
                    chart_verdict=chart_op.verdict, chart_confidence=chart_op.confidence,
                    llm_called=True, **_ta_extras,
                )
            # stop/target은 전날 종가(current_price) 기준 잠정 계산 — 실제 체결 시 order_manager가 entry 기준으로 재계산
            stop_price = round(current_price * (1 - stop_pct))
            target_price = round(current_price * (1 + target_pct))
            # confidence를 포지션 비율에 반영: 확신도 높을수록 더 크게 투자
            # (confidence 0.50 → 50%, 0.70 → 70%, 1.0 → 100% of 리스크 한도)
            conf_position_ratio = round(position_ratio * chart_op.confidence, 3)
            reasoning = f"차트:{chart_op.verdict}({chart_op.confidence:.2f}) | {chart_op.reasoning}"
            logger.info(f"[{stock_code}] 최종 결정: buy | ratio={conf_position_ratio:.2f} | {reasoning}")
            return TradeSignal(
                action="buy",
                position_ratio=conf_position_ratio,
                stop_price=stop_price,
                stop_type="trailing",
                reasoning=reasoning,
                ta_score=ta_score,
                risk_level=risk_level,
                target_price=target_price,
                chart_verdict=chart_op.verdict,
                chart_confidence=chart_op.confidence,
                stop_pct=stop_pct,
                target_pct=target_pct,
                llm_called=True, **_ta_extras,
            )

        logger.info(
            f"[{stock_code}] 최종 결정: skip | "
            f"매수 신호 미달 ({chart_op.verdict}, confidence={chart_op.confidence:.2f})"
        )
        return TradeSignal(
            action="skip", position_ratio=0.0, stop_price=0.0,
            stop_type="none",
            reasoning=f"매수 신호 미달 ({chart_op.verdict}, confidence={chart_op.confidence:.2f})",
            ta_score=ta_score, risk_level=risk_level,
            chart_verdict=chart_op.verdict, chart_confidence=chart_op.confidence,
            llm_called=True, **_ta_extras,
        )


multi_agent_strategy = MultiAgentStrategy()
