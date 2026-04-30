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
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={
                "chat_id": settings.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
    except Exception as e:
        logger.warning(f"텔레그램 알림 실패: {e}")


async def notify_buy(code: str, name: str, qty: int, price: int,
                     target_price: int, stop_price: int, confidence: float) -> None:
    text = (
        f"📈 <b>매수 체결</b>\n"
        f"종목: {name} ({code})\n"
        f"수량: {qty:,}주 @ {price:,}원\n"
        f"목표가: {target_price:,}원 | 손절가: {stop_price:,}원\n"
        f"Confidence: {confidence:.2f}"
    )
    await _send(text)


async def notify_sell(code: str, name: str, qty: int, price: int,
                      reason: str, pnl: int) -> None:
    emoji = "✅" if pnl >= 0 else "🔴"
    reason_kr = {"take_profit": "익절", "stop_loss": "손절"}.get(reason, reason)
    text = (
        f"{emoji} <b>매도 체결 — {reason_kr}</b>\n"
        f"종목: {name} ({code})\n"
        f"수량: {qty:,}주 @ {price:,}원\n"
        f"손익: {pnl:+,}원"
    )
    await _send(text)


async def notify_daily_summary(signals: int, trades: int, pnl: int) -> None:
    text = (
        f"📊 <b>오늘 결과</b>\n"
        f"매수 신호: {signals}개\n"
        f"체결: {trades}건\n"
        f"실현 손익: {pnl:+,}원"
    )
    await _send(text)
