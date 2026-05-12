"""미국 주식 백테스트 실행 (대표 종목 5개)"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

import pandas as pd
from strategy.ta_engine import ta_engine
from strategy.signal_scorer import compute_score
from strategy.regime_detector import detect_regime, MarketRegime
from backtesting.metrics import calc_metrics
from backtesting.reporter import print_report
from kis.overseas_market_data import overseas_market_data

_SLIPPAGE_BUY  = 0.0005
_SLIPPAGE_SELL = 0.0003
_FEE_BUY       = 0.0
_FEE_SELL      = 0.0025
_TA_MIN_SCORE  = 35
_TARGET_PCT    = 0.12   # US: 목표 +12% (stop 8% 대비 1.5:1 비율)
_STOP_PCT      = 0.08   # US: 일간 변동폭 감안 KR(5%)보다 넉넉히
_TRAIL_PCT     = 0.08   # US: trailing stop도 동일하게 확대
_POSITION_PCT  = 0.10

_US_STOCKS = [
    {"ticker": "AAPL", "name": "Apple"},
    {"ticker": "MSFT", "name": "Microsoft"},
    {"ticker": "NVDA", "name": "NVIDIA"},
    {"ticker": "AMZN", "name": "Amazon"},
    {"ticker": "META", "name": "Meta"},
]


def run_us_backtest(ticker: str, df: pd.DataFrame, nasdaq_df: pd.DataFrame):
    cash = 10_000.0  # USD
    position = None
    trades = []
    equity_curve = [cash]

    for i in range(60, len(df)):
        bar   = df.iloc[i]
        hist  = df.iloc[:i].copy()
        price = float(bar["close"])
        date  = bar["date"]

        if position:
            if price > position["highest"]:
                position["highest"] = price

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
                sell     = exit_price * (1 - _SLIPPAGE_SELL)
                fee      = sell * position["qty"] * _FEE_SELL
                proceeds = sell * position["qty"] - fee
                pnl      = proceeds - position["cost"]
                cash    += proceeds
                hold_days = (pd.Timestamp(date) - pd.Timestamp(position["entry_date"])).days or 1
                trades.append({"pnl": pnl, "hold_days": hold_days, "exit_reason": exit_reason})
                position = None

        if position is None:
            nasdaq_hist = nasdaq_df[nasdaq_df["date"] <= date].copy()
            if len(nasdaq_hist) < 60:
                equity_curve.append(float(cash))
                continue

            regime = detect_regime(nasdaq_hist)
            if regime == MarketRegime.TRENDING_DOWN:
                equity_curve.append(float(cash))
                continue

            ta_result = ta_engine.compute(hist)
            if ta_result is None:
                equity_curve.append(float(cash))
                continue

            score = compute_score(ta_result, price, regime)
            if score >= _TA_MIN_SCORE:
                buy    = price * (1 + _SLIPPAGE_BUY)
                fee    = buy * _FEE_BUY
                budget = cash * _POSITION_PCT
                qty    = (budget - fee) / buy
                if qty > 0:
                    cost  = buy * qty + fee
                    cash -= cost
                    position = {
                        "qty":          qty,
                        "cost":         cost,
                        "stop_price":   buy * (1 - _STOP_PCT),
                        "target_price": buy * (1 + _TARGET_PCT),
                        "highest":      buy,
                        "entry_date":   date,
                    }

        pos_value = position["qty"] * price if position else 0
        equity_curve.append(float(cash + pos_value))

    if position:
        last_price = float(df["close"].iloc[-1])
        sell  = last_price * (1 - _SLIPPAGE_SELL)
        fee   = sell * position["qty"] * _FEE_SELL
        pnl   = sell * position["qty"] - fee - position["cost"]
        trades.append({"pnl": pnl, "hold_days": 1, "exit_reason": "forced"})

    return calc_metrics(equity_curve, trades, len(df) - 60)


async def main():
    print(f"\n미국 주식 백테스트 시작: {len(_US_STOCKS)}개 종목")

    # NASDAQ 지수 대용: AAPL 데이터로 레짐 감지 (레짐만 사용하므로 근사 가능)
    print("NASDAQ 기준 데이터 로드 중...")
    nasdaq_df = await overseas_market_data.get_daily_ohlcv("QQQ", 500)
    if nasdaq_df.empty:
        # QQQ 실패 시 AAPL 대용
        nasdaq_df = await overseas_market_data.get_daily_ohlcv("AAPL", 500)

    for stock in _US_STOCKS:
        ticker = stock["ticker"]
        name   = stock["name"]
        try:
            df = await overseas_market_data.get_daily_ohlcv(ticker, 500)
            if df.empty or len(df) < 120:
                print(f"  [{ticker}] 데이터 부족 — 스킵")
                continue
            metrics = run_us_backtest(ticker, df, nasdaq_df)
            print_report(f"{name}({ticker})", metrics)
        except Exception as e:
            print(f"  [{ticker}] 오류: {e}")


if __name__ == "__main__":
    asyncio.run(main())
