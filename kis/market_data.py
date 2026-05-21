import asyncio
import logging
import pandas as pd
from datetime import datetime

from kis.rest_client import kis_client
from config.settings import settings

logger = logging.getLogger(__name__)


class KISMarketData:
    async def get_current_price(self, stock_code: str) -> dict:
        """현재가 조회"""
        data = await kis_client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            tr_id="FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": stock_code},
        )
        output = data["output"]
        return {
            "code": stock_code,
            "price": int(output["stck_prpr"]),
            "change_pct": float(output["prdy_ctrt"]),
            "volume": int(output["acml_vol"]),
            "high": int(output["stck_hgpr"]),
            "low": int(output["stck_lwpr"]),
            "open": int(output["stck_oprc"]),
        }

    async def get_daily_ohlcv(self, stock_code: str, count: int = 120) -> pd.DataFrame:
        """일봉 OHLCV 조회 (inquire-daily-itemchartprice, 100개씩 페이지네이션)"""
        from datetime import timedelta
        all_rows = []
        end_dt = datetime.now()
        # count일치 거래일을 커버하려면 달력일 기준 약 1.5배 필요
        start_dt = end_dt - timedelta(days=int(count * 1.5) + 30)
        max_pages = (count // 100) + 2

        for _ in range(max_pages):
            if len(all_rows) >= count:
                break
            data = await kis_client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                tr_id="FHKST03010100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": stock_code,
                    "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )
            rows = data.get("output2") or data.get("output", [])
            if not rows:
                break

            existing_dates = {r["stck_bsop_date"] for r in all_rows}
            new_rows = [r for r in rows if r["stck_bsop_date"] not in existing_dates]
            if not new_rows:
                break

            all_rows.extend(new_rows)
            # 다음 페이지: 현재 배치의 가장 이른 날짜 하루 전으로 end_dt 이동
            earliest = min(r["stck_bsop_date"] for r in new_rows)
            end_dt = datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)
            start_dt = end_dt - timedelta(days=150)

        df = pd.DataFrame(all_rows[:count])
        if df.empty:
            return df

        df = df.rename(columns={
            "stck_bsop_date": "date",
            "stck_oprc": "open",
            "stck_hgpr": "high",
            "stck_lwpr": "low",
            "stck_clpr": "close",
            "acml_vol": "volume",
        })
        df = df[["date", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df

    async def get_kospi_ohlcv(self, count: int = 250) -> pd.DataFrame:
        """KOSPI 지수 일봉 (레짐 감지용) — inquire-daily-indexchartprice 엔드포인트"""
        from datetime import timedelta
        all_rows = []
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=int(count * 1.5) + 30)
        max_pages = (count // 100) + 2

        for _ in range(max_pages):
            if len(all_rows) >= count:
                break
            data = await kis_client.get(
                "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
                tr_id="FHKUP03500100",
                params={
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": "0001",
                    "FID_INPUT_DATE_1": start_dt.strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": end_dt.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D",
                },
            )
            rows = data.get("output2") or data.get("output", [])
            if not rows or not isinstance(rows, list):
                break

            existing_dates = {r["stck_bsop_date"] for r in all_rows}
            new_rows = [r for r in rows if r["stck_bsop_date"] not in existing_dates]
            if not new_rows:
                break

            all_rows.extend(new_rows)
            earliest = min(r["stck_bsop_date"] for r in new_rows)
            end_dt = datetime.strptime(earliest, "%Y%m%d") - timedelta(days=1)
            start_dt = end_dt - timedelta(days=150)

        df = pd.DataFrame(all_rows[:count])
        if df.empty:
            return df

        df = df.rename(columns={
            "stck_bsop_date": "date",
            "bstp_nmix_oprc": "open",
            "bstp_nmix_hgpr": "high",
            "bstp_nmix_lwpr": "low",
            "bstp_nmix_prpr": "close",
            "acml_vol": "volume",
        })
        df = df[["date", "open", "high", "low", "close", "volume"]]
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col])
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        return df


    async def get_volume_rank(self, count: int = 200) -> list[dict]:
        """거래대금 순위 상위 종목 조회.
        KOSPI/KOSDAQ 각 30개씩 호출 후 acml_tr_pbmn(누적 거래대금) 기준으로 재정렬."""
        async def _fetch(blng_cls: str) -> list[dict]:
            try:
                data = await kis_client.get(
                    "/uapi/domestic-stock/v1/quotations/volume-rank",
                    tr_id="FHPST01710000",
                    params={
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_COND_SCR_DIV_CODE": "20171",
                        "FID_INPUT_ISCD": "0000",
                        "FID_DIV_CLS_CODE": "0",
                        "FID_BLNG_CLS_CODE": blng_cls,  # 1=KOSPI 2=KOSDAQ
                        "FID_TRGT_CLS_CODE": "111111111",
                        "FID_TRGT_EXLS_CLS_CODE": "0000000011",  # ETF/ETN 제외
                        "FID_INPUT_PRICE_1": "1000",
                        "FID_INPUT_PRICE_2": "500000",
                        "FID_VOL_CNT": "0",
                        "FID_INPUT_DATE_1": "",
                    },
                )
                rows = data.get("output", [])
                return [
                    {
                        "code": r["mksc_shrn_iscd"],
                        "name": r["hts_kor_isnm"],
                        "tr_pbmn": int(r.get("acml_tr_pbmn", 0)),
                    }
                    for r in rows
                    if r.get("mksc_shrn_iscd")
                ]
            except Exception as e:
                logger.warning(f"거래대금 순위 조회 실패 (blng={blng_cls}): {e}")
                return []

        kospi, kosdaq = await asyncio.gather(_fetch("1"), _fetch("2"))

        seen: set[str] = set()
        merged: list[dict] = []
        for item in kospi + kosdaq:
            if item["code"] not in seen:
                seen.add(item["code"])
                merged.append(item)

        merged.sort(key=lambda x: x["tr_pbmn"], reverse=True)
        result = [{"code": x["code"], "name": x["name"]} for x in merged]

        logger.info(f"거래대금 순위: {len(result)}개 수집 (KOSPI {len(kospi)} + KOSDAQ {len(kosdaq)}, 거래대금 내림차순 정렬)")
        return result[:count]


market_data = KISMarketData()
