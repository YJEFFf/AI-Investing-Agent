from pathlib import Path
from backtesting.metrics import BacktestMetrics


def print_report(stock_code: str, metrics: BacktestMetrics) -> None:
    print("\n" + "=" * 50)
    print(f"  백테스트 결과: {stock_code}")
    print("=" * 50)
    print(f"  총 수익률:      {metrics.total_return_pct:+.2f}%")
    print(f"  연환산 수익률:  {metrics.cagr_pct:+.2f}%")
    print(f"  Sharpe 비율:    {metrics.sharpe_ratio:.2f}")
    print(f"  최대 낙폭:      -{metrics.max_drawdown_pct:.2f}%")
    print(f"  승률:           {metrics.win_rate*100:.1f}%")
    print(f"  Profit Factor:  {metrics.profit_factor:.2f}")
    print(f"  총 거래 수:     {metrics.total_trades}회")
    print(f"  평균 보유:      {metrics.avg_hold_days:.1f}일")
    print("=" * 50)

    if metrics.sharpe_ratio >= 1.0 and metrics.max_drawdown_pct <= 15.0:
        print("  ✅ 목표 기준 통과 (Sharpe ≥ 1.0, MDD ≤ 15%)")
    else:
        issues = []
        if metrics.sharpe_ratio < 1.0:
            issues.append(f"Sharpe {metrics.sharpe_ratio:.2f} < 1.0")
        if metrics.max_drawdown_pct > 15.0:
            issues.append(f"MDD {metrics.max_drawdown_pct:.1f}% > 15%")
        print(f"  ❌ 목표 기준 미달: {', '.join(issues)}")
    print()
