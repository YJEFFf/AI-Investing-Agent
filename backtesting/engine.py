import logging

import pandas as pd

from strategy.ta_engine import ta_engine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import detect_regime, MarketRegime
from backtesting.metrics import calc_metrics, BacktestMetrics

logger = logging.getLogger(__name__)

_SLIPPAGE_BUY  = 0.0005
_SLIPPAGE_SELL = 0.0003
_FEE_BUY       = 0.00015
_FEE_SELL      = 0.003    # 수수료 + 증권거래세

# 스윙 전략 고정 파라미터 (LLM 판단 근사값)
_TA_MIN_SCORE  = 35
_TARGET_PCT    = 0.07   # 익절 +7%
_STOP_PCT      = 0.05   # 손절 -5%
_TRAIL_PCT     = 0.05   # 트레일링 스탑 비율
_POSITION_PCT  = 0.10   # 포지션당 잔고 비율


class BacktestEngine:
    """스윙 매매 전략 bar-by-bar 백테스트 (look-ahead bias 방지)

    LLM vision 판단은 TA 규칙으로 근사:
    - 진입: TA ≥ 35점 + 레짐 TRENDING_DOWN 제외
    - 익절: +7% (LLM 평균 target_pct)
    - 손절: -5% trailing stop (LLM 평균 stop_pct)
    """

    def __init__(self, initial_cash: int = 10_000_000):
        self._initial_cash = initial_cash

    def run(self, stock_code: str, df: pd.DataFrame, kospi_df: pd.DataFrame) -> BacktestMetrics:
        cash = self._initial_cash
        position = None   # dict: entry_price, qty, stop_price, target_price, highest, entry_date
        trades = []
        equity_curve = [float(cash)]

        for i in range(60, len(df)):
            bar      = df.iloc[i]
            hist     = df.iloc[:i].copy()
            price    = float(bar["close"])
            date     = bar["date"]

            # ── 보유 중: 익절 / 트레일링 스탑 / 손절 체크 ──────────────
            if position:
                # 최고가 갱신
                if price > position["highest"]:
                    position["highest"] = price

                # 트레일링 스탑 업데이트
                new_stop = position["highest"] * (1 - _TRAIL_PCT)
                if new_stop > position["stop_price"]:
                    position["stop_price"] = new_stop

                exit_price = None
                exit_reason = None

                if price >= position["target_price"]:
                    exit_price  = price
                    exit_reason = "take_profit"
                elif price <= position["stop_price"]:
                    exit_price  = price
                    exit_reason = "stop_loss"

                if exit_price is not None:
                    sell = int(exit_price * (1 - _SLIPPAGE_SELL))
                    fee  = int(sell * position["qty"] * _FEE_SELL)
                    proceeds = sell * position["qty"] - fee
                    pnl = proceeds - position["cost"]
                    cash += proceeds
                    hold_days = (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days or 1
                    trades.append({
                        "pnl":        pnl,
                        "hold_days":  hold_days,
                        "exit_reason": exit_reason,
                    })
                    position = None

            # ── 미보유: 진입 신호 평가 ──────────────────────────────────
            if position is None:
                kospi_hist = kospi_df[kospi_df["date"] <= date].copy()
                if len(kospi_hist) < 60:
                    equity_curve.append(float(cash))
                    continue

                regime = detect_regime(kospi_hist)
                if regime == MarketRegime.TRENDING_DOWN:
                    equity_curve.append(float(cash))
                    continue

                ta_result = ta_engine.compute(hist)
                if ta_result is None:
                    equity_curve.append(float(cash))
                    continue

                score = compute_score(ta_result, price, regime)
                if score >= _TA_MIN_SCORE:
                    buy   = int(price * (1 + _SLIPPAGE_BUY))
                    fee   = int(buy * _FEE_BUY)
                    budget = int(cash * _POSITION_PCT)
                    qty   = max(0, (budget - fee) // buy)

                    if qty > 0 and buy * qty + fee <= cash:
                        cost  = buy * qty + fee
                        cash -= cost
                        position = {
                            "entry_price":  buy,
                            "qty":          qty,
                            "cost":         cost,
                            "stop_price":   buy * (1 - _STOP_PCT),
                            "target_price": buy * (1 + _TARGET_PCT),
                            "highest":      float(buy),
                            "entry_date":   date,
                        }

            pos_value = position["qty"] * price if position else 0
            equity_curve.append(float(cash + pos_value))

        # 마지막 포지션 강제 청산
        if position:
            last_price = float(df["close"].iloc[-1])
            sell  = int(last_price * (1 - _SLIPPAGE_SELL))
            fee   = int(sell * position["qty"] * _FEE_SELL)
            pnl   = sell * position["qty"] - fee - position["cost"]
            trades.append({"pnl": pnl, "hold_days": 1, "exit_reason": "forced"})

        return calc_metrics(equity_curve, trades, len(df) - 60)
