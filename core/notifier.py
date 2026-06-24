import logging

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def _send(text: str) -> None:
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return
    try:
        url = _BASE_URL.format(token=settings.telegram_bot_token)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
        if not resp.is_success:
            logger.warning(f"텔레그램 알림 실패: HTTP {resp.status_code} — {resp.text[:200]}")
    except Exception as e:
        logger.warning(f"텔레그램 알림 실패: {repr(e)}")


async def notify_buy(code: str, name: str, qty: int, price: int,
                     target_price: int, stop_price: int, confidence: float) -> None:
    text = (
        f"📈 <b>매수 체결</b>\n"
        f"종목: {name} ({code})\n"
        f"수량: {qty:,}주 @ {price:,}원\n"
        f"총 매수금액: {qty * price:,}원\n"
        f"목표가: {target_price:,}원 | 손절가: {stop_price:,}원\n"
        f"Confidence: {confidence:.2f}"
    )
    await _send(text)


async def notify_sell(code: str, name: str, qty: int, price: int,
                      reason: str, pnl: int) -> None:
    if reason == "stop_loss":
        reason_kr = "트레일링 익절" if pnl >= 0 else "손절"
    else:
        reason_kr = {"take_profit": "익절", "partial_take_profit": "절반 익절"}.get(reason, reason)
    emoji = "✅" if pnl >= 0 else "🔴"
    text = (
        f"{emoji} <b>매도 체결 — {reason_kr}</b>\n"
        f"종목: {name} ({code})\n"
        f"수량: {qty:,}주 @ {price:,}원\n"
        f"손익: {pnl:+,}원"
    )
    await _send(text)


async def notify_pre_market_summary(
    total_scanned: int,
    buy_signals: list[dict],
    is_retry: bool = False,
) -> None:
    label = "장전 분석 완료"
    if buy_signals:
        lines = "\n".join(
            f"  • {s['name']}({s['code']}) | confidence {s['confidence']:.2f} | {s.get('regime', '')}"
            for s in buy_signals
        )
        text = (
            f"🔍 <b>{label}</b>\n"
            f"스캔: {total_scanned}개 종목 → 매수 신호: {len(buy_signals)}개\n\n"
            f"{lines}"
        )
    else:
        text = (
            f"⚠️ <b>{label} — 매수 신호 없음</b>\n"
            f"스캔: {total_scanned}개 종목 중 조건 충족 종목 없음"
        )
    await _send(text)


async def notify_sell_fail(code: str, name: str, reason: str, will_retry: bool) -> None:
    reason_kr = {"take_profit": "전량 익절", "stop_loss": "스탑 청산", "partial_take_profit": "절반 익절"}.get(reason, reason)
    if will_retry:
        status = "5분 후 자동 재시도 예정"
        emoji = "⚠️"
    else:
        status = "재시도도 실패 — 수동 확인 필요"
        emoji = "🚨"
    text = (
        f"{emoji} <b>매도 주문 실패 — {reason_kr}</b>\n"
        f"종목: {name} ({code})\n"
        f"상태: {status}"
    )
    await _send(text)



async def notify_gap_skip(code: str, name: str, analysis_price: int, open_price: int, gap_pct: float) -> None:
    text = (
        f"⏭ <b>갭다운 스킵</b>\n"
        f"종목: {name} ({code})\n"
        f"분석 기준가: {analysis_price:,}원 → 시초가: {open_price:,}원\n"
        f"갭: {gap_pct:.1%}"
    )
    await _send(text)


async def notify_pre_close_exit(code: str, name: str, qty: int, avg_price: int, current_price: int, loss_pct: float) -> None:
    pnl = (current_price - avg_price) * qty
    text = (
        f"🔴 <b>장마감 전 손절</b>\n"
        f"종목: {name} ({code})\n"
        f"매수가: {avg_price:,}원 → 현재가: {current_price:,}원 ({loss_pct:.1%})\n"
        f"수량: {qty}주 | 손익: {pnl:+,}원\n"
        f"사유: 당일 -5% 이상, 이익권 미진입"
    )
    await _send(text)


async def notify_daily_summary(signals: int, trades: int, sells: int, pnl: int) -> None:
    text = (
        f"📊 <b>오늘 결과</b>\n"
        f"매수 신호: {signals}개 | 매수: {trades}건 | 매도: {sells}건\n"
        f"실현 손익: {pnl:+,}원"
    )
    await _send(text)
