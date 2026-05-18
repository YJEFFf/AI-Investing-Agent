import logging
from datetime import datetime, timedelta

import pandas as pd

from kis.real_client import kis_real_client

logger = logging.getLogger(__name__)

_EXCD = "NAS"  # NASDAQ 100 종목은 전부 NASDAQ


class KISOverseasMarketData:
    async def get_current_price(self, ticker: str) -> dict:
        """해외주식 현재가 조회 (USD)."""
        data = await kis_real_client.get(
            "/uapi/overseas-price/v1/quotations/price",
            tr_id="HHDFS00000300",
            params={"AUTH": "", "EXCD": _EXCD, "SYMB": ticker},
        )
        output = data["output"]
        return {
            "ticker": ticker,
            "price": float(output["last"]),
            "change_pct": float(output.get("rate", 0)),
            "volume": int(output.get("tvol", 0)),
            "high": float(output.get("high", 0)),
            "low": float(output.get("low", 0)),
            "open": float(output.get("open", 0)),
        }

    async def get_daily_ohlcv(self, ticker: str, count: int = 120) -> pd.DataFrame:
        """해외주식 일봉 OHLCV 조회 (USD). 최대 100행씩 페이지네이션."""
        all_rows: list[dict] = []
        end_date = datetime.now()
        max_pages = (count // 100) + 2  # 100행씩 페이지네이션, 여유 2페이지

        for _ in range(max_pages):
            if len(all_rows) >= count:
                break
            data = await kis_real_client.get(
                "/uapi/overseas-price/v1/quotations/dailyprice",
                tr_id="HHDFS76240000",
                params={
                    "AUTH": "",
                    "EXCD": _EXCD,
                    "SYMB": ticker,
                    "GUBN": "0",       # 0=일봉
                    "BYMD": end_date.strftime("%Y%m%d"),
                    "MODP": "1",       # 수정주가
                    "KEYB": "",
                },
            )
            rows = data.get("output2") or []
            if not rows:
                break

            existing = {r["xymd"] for r in all_rows}
            new_rows = [r for r in rows if r["xymd"] not in existing and r.get("clos")]
            if not new_rows:
                break

            all_rows.extend(new_rows)
            earliest = min(r["xymd"] for r in new_rows)
            end_date = datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)

        if not all_rows:
            return pd.DataFrame()

        df = pd.DataFrame(all_rows[:count])
        df = df.rename(columns={
            "xymd": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "clos": "close",
            "tvol": "volume",
        })
        df = df[["date", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
        df = df.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
        return df


overseas_market_data = KISOverseasMarketData()
