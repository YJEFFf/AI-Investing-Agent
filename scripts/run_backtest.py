"""백테스트 실행"""
import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

from data.fetcher import fetcher
from backtesting.engine import BacktestEngine
from backtesting.reporter import print_report


async def main():
    # 백테스트 대상 종목 목록
    import yaml
    with open("config/watchlist.yaml") as f:
        config = yaml.safe_load(f)
    stocks = config.get("stocks", [])

    print(f"\n백테스트 시작: {len(stocks)}개 종목")

    # KOSPI 데이터 로드
    kospi_df = await fetcher.fetch_kospi(500)

    engine = BacktestEngine(initial_cash=1_000_000)

    for stock in stocks:
        code = stock["code"]
        name = stock["name"]
        try:
            df = await fetcher.fetch_daily(code, 500)
            if df.empty or len(df) < 120:
                print(f"  [{code}] 데이터 부족 — 스킵")
                continue
            metrics = engine.run(code, df, kospi_df)
            print_report(f"{name}({code})", metrics)
        except Exception as e:
            print(f"  [{code}] 오류: {e}")


if __name__ == "__main__":
    asyncio.run(main())
