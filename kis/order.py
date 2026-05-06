import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime

from config.settings import settings
from kis.rest_client import kis_client

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    order_no: str
    stock_code: str
    order_type: str  # buy | sell
    qty: int
    price: int
    success: bool
    message: str = ""
    fill_price: int = 0  # 실제 체결가 (0이면 미확인)


class KISOrder:
    def _get_tr_id(self, order_type: str) -> str:
        if settings.is_paper:
            return "VTTC0802U" if order_type == "buy" else "VTTC0801U"
        return "TTTC0802U" if order_type == "buy" else "TTTC0801U"

    def _get_account_parts(self) -> tuple[str, str]:
        account_no = settings.kis_account_no
        if "-" in account_no:
            acnt_no, acnt_prdt_cd = account_no.split("-", 1)
        else:
            acnt_no, acnt_prdt_cd = account_no, "01"
        return acnt_no, acnt_prdt_cd

    async def market_buy(self, stock_code: str, qty: int) -> OrderResult:
        result = await self._submit_order(stock_code, qty, 0, "buy", "01")
        if result.success and result.order_no:
            result.fill_price = await self._fetch_fill_price(result.order_no, stock_code)
        return result

    async def market_sell(self, stock_code: str, qty: int) -> OrderResult:
        result = await self._submit_order(stock_code, qty, 0, "sell", "01")
        if result.success and result.order_no:
            result.fill_price = await self._fetch_fill_price(result.order_no, stock_code)
        return result

    async def limit_buy(self, stock_code: str, qty: int, price: int) -> OrderResult:
        return await self._submit_order(stock_code, qty, price, "buy", "00")

    async def limit_sell(self, stock_code: str, qty: int, price: int) -> OrderResult:
        return await self._submit_order(stock_code, qty, price, "sell", "00")

    async def _fetch_fill_price(self, order_no: str, stock_code: str, retries: int = 5) -> int:
        """주문번호로 실제 체결가 조회. 체결 확인까지 최대 5회 재시도.
        ODNO 매칭 실패 시 종목 기준 당일 최근 체결가로 폴백."""
        acnt_no, acnt_prdt_cd = self._get_account_parts()
        tr_id = "VTTC8001R" if settings.is_paper else "TTTC8001R"
        today = datetime.now().strftime("%Y%m%d")

        # 시장가 주문이 KIS 시스템에 체결로 기록되기까지 대기
        await asyncio.sleep(2)

        async def _query(odno_filter: str) -> int:
            params = {
                "CANO": acnt_no, "ACNT_PRDT_CD": acnt_prdt_cd,
                "INQR_STRT_DT": today, "INQR_END_DT": today,
                "SLL_BUY_DVSN_CD": "00",
                "INQR_DVSN": "00",  # 역순(최신순)
                "PDNO": stock_code, "CCLD_DVSN": "01",  # 체결분만
                "ORD_GNO_BRNO": "", "ODNO": odno_filter,
                "INQR_DVSN_3": "", "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            }
            data = await kis_client.get(
                "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                tr_id=tr_id, params=params,
            )
            records = data.get("output1", [])
            logger.debug(f"체결가 조회 {stock_code}(odno={odno_filter or 'all'}): {len(records)}건")
            for rec in records:
                if odno_filter and rec.get("ODNO", "").strip() != odno_filter.strip():
                    continue
                if int(str(rec.get("CCLD_QTY", "0")).replace(",", "")) <= 0:
                    continue
                price = int(str(rec.get("CCLD_UNPR", "0")).replace(",", ""))
                if price > 0:
                    return price
            return 0

        # 1차: ODNO 기준으로 재시도
        for attempt in range(retries):
            if attempt > 0:
                await asyncio.sleep(2)
            try:
                price = await _query(order_no)
                if price > 0:
                    logger.info(f"실제 체결가 확인: {stock_code} {order_no} → {price:,}원")
                    return price
            except Exception as e:
                logger.debug(f"체결가 조회 실패 (시도 {attempt + 1}): {e}")

        # 2차 폴백: ODNO 없이 종목 기준 당일 최근 체결가
        logger.warning(f"ODNO 매칭 실패 — 종목 기준 폴백: {stock_code} {order_no}")
        try:
            price = await _query("")
            if price > 0:
                logger.info(f"폴백 체결가 확인: {stock_code} → {price:,}원")
                return price
        except Exception as e:
            logger.debug(f"폴백 조회 실패: {e}")

        logger.warning(f"체결가 조회 완전 실패 — 추정가 사용: {stock_code} {order_no}")
        return 0

    async def cancel_order(self, order_no: str, stock_code: str, qty: int) -> OrderResult:
        acnt_no, acnt_prdt_cd = self._get_account_parts()
        tr_id = "VTTC0803U" if settings.is_paper else "TTTC0803U"
        body = {
            "CANO": acnt_no,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "KRX_FWDG_ORD_ORGNO": "",
            "ORGN_ODNO": order_no,
            "ORD_DVSN": "02",
            "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y",
        }
        try:
            data = await kis_client.post(
                "/uapi/domestic-stock/v1/trading/order-rvsecncl", tr_id=tr_id, body=body
            )
            return OrderResult(
                order_no=order_no,
                stock_code=stock_code,
                order_type="cancel",
                qty=qty,
                price=0,
                success=True,
            )
        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            return OrderResult(
                order_no=order_no,
                stock_code=stock_code,
                order_type="cancel",
                qty=qty,
                price=0,
                success=False,
                message=str(e),
            )

    async def _submit_order(
        self, stock_code: str, qty: int, price: int, order_type: str, ord_dvsn: str
    ) -> OrderResult:
        acnt_no, acnt_prdt_cd = self._get_account_parts()
        tr_id = self._get_tr_id(order_type)
        body = {
            "CANO": acnt_no,
            "ACNT_PRDT_CD": acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": ord_dvsn,  # 00=지정가, 01=시장가
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price),
        }
        try:
            data = await kis_client.post(
                "/uapi/domestic-stock/v1/trading/order-cash", tr_id=tr_id, body=body
            )
            output = data.get("output", {})
            order_no = output.get("ODNO", "")
            logger.info(f"Order submitted: {order_type} {stock_code} x{qty} @ {price} → {order_no}")
            return OrderResult(
                order_no=order_no,
                stock_code=stock_code,
                order_type=order_type,
                qty=qty,
                price=price,
                success=True,
            )
        except Exception as e:
            logger.error(f"Order failed: {order_type} {stock_code} x{qty} → {e}")
            return OrderResult(
                order_no="",
                stock_code=stock_code,
                order_type=order_type,
                qty=qty,
                price=price,
                success=False,
                message=str(e),
            )


kis_order = KISOrder()
