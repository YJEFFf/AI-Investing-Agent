import logging

from config.settings import settings
from kis.rest_client import kis_client

logger = logging.getLogger(__name__)


class KISOverseasAccount:
    def _account_parts(self) -> tuple[str, str]:
        no = settings.kis_account_no
        if "-" in no:
            acnt, prdt = no.split("-", 1)
        else:
            acnt, prdt = no, "01"
        return acnt, prdt

    async def get_balance(self) -> dict:
        """해외주식 잔고 조회 — USD 가용현금·총평가금액 반환."""
        acnt, prdt = self._account_parts()
        data = await kis_client.get(
            "/uapi/overseas-stock/v1/account/inquire-present-balance",
            tr_id="CTRP6504R",
            params={
                "CANO": acnt,
                "ACNT_PRDT_CD": prdt,
                "WCRC_FRCR_DVSN_CD": "02",   # 02=USD
                "NATN_CD": "840",             # 840=미국
                "TR_MKET_CD": "00",
                "INQR_DVSN_CD": "00",
            },
        )
        # output3: 통화별 외화 잔고 요약
        summary_list = data.get("output3") or [{}]
        summary = summary_list[0] if isinstance(summary_list, list) else summary_list
        available_usd = float(summary.get("frcr_dncl_amt_2", 0) or 0)
        total_usd = float(summary.get("tot_evlu_pfls_amt", available_usd) or available_usd)

        # output2: 보유 종목별 평가 (포지션 체결가 확인용)
        positions_raw = data.get("output2") or []
        positions = []
        for item in positions_raw:
            qty = float(item.get("cblc_qty13", 0) or 0)
            if qty <= 0:
                continue
            positions.append({
                "ticker": item.get("pdno", ""),
                "name": item.get("prdt_name", ""),
                "qty": qty,
                "avg_price": float(item.get("pchs_avg_pric", 0) or 0),
                "current_price": float(item.get("ovrs_now_pric1", 0) or 0),
            })

        return {
            "available_usd": available_usd,
            "total_eval_usd": total_usd,
            "positions": positions,
        }

    async def get_fill_price(self, ticker: str) -> float:
        """매수 직후 잔고에서 체결가 확인 (3회 재시도)."""
        import asyncio
        for attempt in range(3):
            await asyncio.sleep(3)
            try:
                balance = await self.get_balance()
                for p in balance["positions"]:
                    if p["ticker"] == ticker and p["avg_price"] > 0:
                        logger.info(f"해외 체결가 확인: {ticker} → ${p['avg_price']:.4f} (시도 {attempt + 1})")
                        return p["avg_price"]
            except Exception as e:
                logger.debug(f"해외 체결가 조회 실패 (시도 {attempt + 1}): {e}")
        logger.warning(f"해외 체결가 확인 실패 — 추정가 사용: {ticker}")
        return 0.0


overseas_account = KISOverseasAccount()
