import logging
from datetime import datetime

import pandas as pd

from strategy.ta_engine import ta_engine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import detect_regime, get_entry_threshold
from strategy.agents.ta_agent import TechnicalAnalystAgent
from strategy.agents.base_agent import AgentOpinion
from risk.stop_loss import calc_initial_stop
from backtesting.metrics import calc_metrics, BacktestMetrics

logger = logging.getLogger(__name__)

_SLIPPAGE_BUY = 0.0005
_SLIPPAGE_SELL = 0.0003
_FEE_BUY = 0.00015
_FEE_SELL = 0.003   # 수수료 + 증권거래세


class BacktestEngine:
    """이벤트 기반 bar-by-bar 백테스트 (look-ahead bias 방지)"""

    def __init__(self, initial_cash: int = 1_000_000):
        self._initial_cash = initial_cash

    def run(self, stock_code: str, df: pd.DataFrame, kospi_df: pd.DataFrame) -> BacktestMetrics:
        cash = self._initial_cash
        position = None
        trades = []
        equity_curve = [float(cash)]

        _ta_agent = TechnicalAnalystAgent()

        for i in range(60, len(df)):
            bar = df.iloc[i]
            hist = df.iloc[:i].copy()
            current_price = float(bar["close"])
            date = bar["date"]

            # 손절 체크
            if position:
                if current_price <= position["stop_price"]:
                    sell_price = int(current_price * (1 - _SLIPPAGE_SELL))
                    pnl = (sell_price - position["entry_price"]) * position["qty"]
                    pnl -= int(sell_price * position["qty"] * _FEE_SELL)
                    cash += sell_price * position["qty"] - int(sell_price * position["qty"] * _FEE_SELL)
                    hold_days = (date - position["entry_date"]).days if hasattr(date - position["entry_date"], "days") else 1
                    trades.append({"pnl": pnl, "hold_days": hold_days})
                    position = None

            # 레짐 감지 (KOSPI 데이터 사용)
            kospi_hist = kospi_df[kospi_df["date"] <= date].copy()
            regime = detect_regime(kospi_hist) if len(kospi_hist) >= 60 else None

            if regime is None:
                equity_curve.append(float(cash + (position["qty"] * current_price if position else 0)))
                continue

            threshold = get_entry_threshold(regime)

            # 매수 신호 평가
            if position is None:
                ta_result = ta_engine.compute(hist)
                if ta_result is None:
                    equity_curve.append(float(cash))
                    continue

                score = compute_score(ta_result, current_price, regime)
                # 새 전략 기준: TA 45점 이상 (LLM이 추가 판단하는 구간까지 포함)
                if score >= 45 and score >= threshold * 0.7:
                    buy_price = int(current_price * (1 + _SLIPPAGE_BUY))
                    fee = int(buy_price * _FEE_BUY)
                    budget = int(cash * 0.10)  # 10% 포지션
                    qty = (budget - fee) // buy_price
                    if qty > 0 and buy_price <= cash:
                        cost = buy_price * qty + fee
                        cash -= cost
                        stop = calc_initial_stop(buy_price, "fixed")
                        position = {
                            "entry_price": buy_price,
                            "qty": qty,
                            "stop_price": stop,
                            "entry_date": date,
                        }

            # 자산 평가
            pos_value = position["qty"] * current_price if position else 0
            equity_curve.append(float(cash + pos_value))

        # 마지막 포지션 강제 청산
        if position:
            last_price = int(df["close"].iloc[-1])
            pnl = (last_price - position["entry_price"]) * position["qty"]
            trades.append({"pnl": pnl, "hold_days": 1})

        return calc_metrics(equity_curve, trades, len(df) - 60)
