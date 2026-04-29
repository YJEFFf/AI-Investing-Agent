from config.settings import settings
from kis.rest_client import kis_client


class KISAccount:
    async def get_balance(self) -> dict:
        """잔고 조회 (inquire-balance) — 총평가금액, 가용현금, 미실현손익"""
        account_no = settings.kis_account_no
        acnt_prdt_cd = account_no.split("-")[1] if "-" in account_no else "01"
        acnt_no = account_no.split("-")[0]

        tr_id = "VTTC8434R" if settings.is_paper else "TTTC8434R"
        data = await kis_client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "N",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        summary = data.get("output2", [{}])
        summary = summary[0] if isinstance(summary, list) and summary else summary
        return {
            "available_cash": int(float(summary.get("dnca_tot_amt", 0))),
            "total_eval": int(float(summary.get("tot_evlu_amt", 0))),
            "unrealized_pnl": int(float(summary.get("evlu_pfls_smtl_amt", 0))),
        }

    async def get_positions(self) -> list[dict]:
        """보유 종목 조회"""
        account_no = settings.kis_account_no
        acnt_prdt_cd = account_no.split("-")[1] if "-" in account_no else "01"
        acnt_no = account_no.split("-")[0]

        tr_id = "VTTC8434R" if settings.is_paper else "TTTC8434R"
        data = await kis_client.get(
            "/uapi/domestic-stock/v1/trading/inquire-balance",
            tr_id=tr_id,
            params={
                "CANO": acnt_no,
                "ACNT_PRDT_CD": acnt_prdt_cd,
                "AFHR_FLPR_YN": "N",
                "OFL_YN": "N",
                "INQR_DVSN": "02",
                "UNPR_DVSN": "01",
                "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N",
                "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        positions = []
        for item in data.get("output1", []):
            qty = int(item.get("hldg_qty", 0))
            if qty <= 0:
                continue
            positions.append({
                "code": item["pdno"],
                "name": item["prdt_name"],
                "qty": qty,
                "avg_price": float(item.get("pchs_avg_pric", 0)),
                "current_price": int(item.get("prpr", 0)),
                "eval_profit_loss": int(item.get("evlu_pfls_amt", 0)),
                "profit_loss_pct": float(item.get("evlu_pfls_rt", 0)),
            })
        return positions


kis_account = KISAccount()
