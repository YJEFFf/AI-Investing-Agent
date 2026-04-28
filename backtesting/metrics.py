import math
from dataclasses import dataclass


@dataclass
class BacktestMetrics:
    total_return_pct: float
    cagr_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    profit_factor: float
    total_trades: int
    avg_hold_days: float


def calc_metrics(equity_curve: list[float], trades: list[dict], days: int) -> BacktestMetrics:
    if not equity_curve or len(equity_curve) < 2:
        return BacktestMetrics(0, 0, 0, 0, 0, 0, 0, 0)

    initial = equity_curve[0]
    final = equity_curve[-1]
    total_return = (final - initial) / initial

    # CAGR
    years = days / 252
    cagr = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0

    # 일간 수익률
    daily_returns = [
        (equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1]
        for i in range(1, len(equity_curve))
    ]
    avg_ret = sum(daily_returns) / len(daily_returns)
    std_ret = math.sqrt(sum((r - avg_ret) ** 2 for r in daily_returns) / len(daily_returns))
    sharpe = (avg_ret / std_ret * math.sqrt(252)) if std_ret > 0 else 0.0

    # MDD
    peak = equity_curve[0]
    max_dd = 0.0
    for val in equity_curve:
        if val > peak:
            peak = val
        dd = (peak - val) / peak
        if dd > max_dd:
            max_dd = dd

    # 거래 통계
    closed = [t for t in trades if t.get("pnl") is not None]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    win_rate = len(wins) / len(closed) if closed else 0.0
    gross_profit = sum(t["pnl"] for t in wins)
    gross_loss = abs(sum(t["pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    avg_hold = (
        sum(t.get("hold_days", 0) for t in closed) / len(closed) if closed else 0.0
    )

    return BacktestMetrics(
        total_return_pct=round(total_return * 100, 2),
        cagr_pct=round(cagr * 100, 2),
        sharpe_ratio=round(sharpe, 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 2),
        total_trades=len(closed),
        avg_hold_days=round(avg_hold, 1),
    )
