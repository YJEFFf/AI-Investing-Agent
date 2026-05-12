import logging
from dataclasses import dataclass

from config.settings import settings
from kis.rest_client import kis_client

logger = logging.getLogger(__name__)

_EXCD = "NAS"  # NASDAQ


@dataclass
class OverseasOrderResult:
    order_no: str
    ticker: str
    order_type: str   # buy | sell
    qty: float
    price: float
    success: bool
    message: str = ""
    fill_price: float = 0.0


class KISOverseasOrder:
    def _account_parts(self) -> tuple[str, str]:
        no = settings.kis_account_no
        if "-" in no:
            acnt, prdt = no.split("-", 1)
        else:
            acnt, prdt = no, "01"
        return acnt, prdt

    async def fractional_buy(self, ticker: str, qty: float) -> OverseasOrderResult:
        """소수점 해외주식 매수. qty는 소수점 가능 (e.g. 1.5234)."""
        result = await self._submit_fractional(ticker, qty, "buy")
        if result.success:
            from kis.overseas_account import overseas_account
            fill = await overseas_account.get_fill_price(ticker)
            result.fill_price = fill
        return result

    async def fractional_sell(self, ticker: str, qty: float) -> OverseasOrderResult:
        """소수점 해외주식 매도."""
        return await self._submit_fractional(ticker, qty, "sell")

    async def _submit_fractional(
        self, ticker: str, qty: float, order_type: str
    ) -> OverseasOrderResult:
        acnt, prdt = self._account_parts()
        tr_id = "TTTS0308U" if order_type == "buy" else "TTTS0309U"
        body = {
            "CANO": acnt,
            "ACNT_PRDT_CD": prdt,
            "OVRS_EXCG_CD": _EXCD,
            "PDNO": ticker,
            "ORD_QTY": f"{qty:.4f}",
            "OVRS_ORD_UNPR": "0",      # 시장가
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }
        try:
            data = await kis_client.post(
                "/uapi/overseas-stock/v1/trading/order",
                tr_id=tr_id,
                body=body,
            )
            output = data.get("output", {})
            order_no = output.get("ODNO", "")
            logger.info(f"US order: {order_type} {ticker} x{qty:.4f} → {order_no}")
            return OverseasOrderResult(
                order_no=order_no, ticker=ticker, order_type=order_type,
                qty=qty, price=0.0, success=True,
            )
        except Exception as e:
            logger.error(f"US order failed: {order_type} {ticker} x{qty:.4f} → {e}")
            return OverseasOrderResult(
                order_no="", ticker=ticker, order_type=order_type,
                qty=qty, price=0.0, success=False, message=str(e),
            )


overseas_order = KISOverseasOrder()
