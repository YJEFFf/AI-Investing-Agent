import asyncio
import logging
from dataclasses import dataclass, field

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
        if result.success:
            result.fill_price = await self._fetch_fill_price_from_balance(stock_code)
        return result

    async def market_sell(self, stock_code: str, qty: int) -> OrderResult:
        # 매도 체결가는 order_manager의 current_price 폴백으로 처리
        return await self._submit_order(stock_code, qty, 0, "sell", "01")

    async def limit_buy(self, stock_code: str, qty: int, price: int) -> OrderResult:
        return await self._submit_order(stock_code, qty, price, "buy", "00")

    async def limit_sell(self, stock_code: str, qty: int, price: int) -> OrderResult:
        return await self._submit_order(stock_code, qty, price, "sell", "00")

    async def _fetch_fill_price_from_balance(self, stock_code: str) -> int:
        """매수 직후 잔고 조회에서 매입평균단가를 체결가로 반환.
        inquire-daily-ccld는 모의투자에서 데이터를 반환하지 않아 사용 불가."""
        await asyncio.sleep(3)
        try:
            from kis.account import kis_account
            positions = await kis_account.get_positions()
            for p in positions:
                if p["code"] == stock_code:
                    price = int(p["avg_price"])
                    if price > 0:
                        logger.info(f"잔고 기준 체결가: {stock_code} → {price:,}원")
                        return price
        except Exception as e:
            logger.debug(f"잔고 기준 체결가 조회 실패: {e}")
        logger.warning(f"잔고 조회 체결가 실패 — 추정가 사용: {stock_code}")
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
