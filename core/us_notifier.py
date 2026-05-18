import logging

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


async def _send(text: str) -> None:
    if not settings.telegram_us_bot_token or not settings.telegram_us_chat_id:
        return
    try:
        url = _BASE_URL.format(token=settings.telegram_us_bot_token)
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={
                "chat_id": settings.telegram_us_chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
    except Exception as e:
        logger.warning(f"US 텔레그램 알림 실패: {e}")


async def notify_us_buy(
    ticker: str, name: str, qty: float, price: float,
    target_price: float, stop_price: float, confidence: float,
) -> None:
    total = qty * price
    text = (
        f"📈 <b>[US] 매수 체결</b>\n"
        f"종목: {name} ({ticker})\n"
        f"수량: {qty:.4f}주 @ ${price:.2f}\n"
        f"총 매수금액: ${total:.2f}\n"
        f"목표가: ${target_price:.2f} | 손절가: ${stop_price:.2f}\n"
        f"Confidence: {confidence:.2f}"
    )
    await _send(text)


async def notify_us_sell(
    ticker: str, name: str, qty: float, price: float,
    reason: str, pnl: float,
) -> None:
    emoji = "✅" if pnl >= 0 else "🔴"
    reason_kr = {
        "take_profit": "익절",
        "stop_loss": "손절",
        "partial_take_profit": "절반 익절",
    }.get(reason, reason)
    text = (
        f"{emoji} <b>[US] 매도 — {reason_kr}</b>\n"
        f"종목: {name} ({ticker})\n"
        f"수량: {qty:.4f}주 @ ${price:.2f}\n"
        f"손익: ${pnl:+.2f}"
    )
    await _send(text)


async def notify_us_pre_market_summary(
    total_scanned: int, buy_signals: list[dict],
) -> None:
    if buy_signals:
        lines = "\n".join(
            f"  • {s['name']}({s['ticker']}) | confidence {s['confidence']:.2f}"
            for s in buy_signals
        )
        text = (
            f"🔍 <b>[US] 장전 분석 완료</b>\n"
            f"스캔: {total_scanned}개 → 매수 신호: {len(buy_signals)}개\n\n"
            f"{lines}"
        )
    else:
        text = (
            f"🔍 <b>[US] 장전 분석 완료</b>\n"
            f"스캔: {total_scanned}개 → 매수 신호 없음"
        )
    await _send(text)


async def notify_us_sell_fail(ticker: str, name: str, reason: str, will_retry: bool) -> None:
    reason_kr = {"take_profit": "전량 익절", "stop_loss": "손절", "partial_take_profit": "절반 익절"}.get(reason, reason)
    if will_retry:
        status = "5분 후 자동 재시도 예정"
        emoji = "⚠️"
    else:
        status = "재시도도 실패 — 수동 확인 필요"
        emoji = "🚨"
    text = (
        f"{emoji} <b>[US] 매도 주문 실패 — {reason_kr}</b>\n"
        f"종목: {name} ({ticker})\n"
        f"상태: {status}"
    )
    await _send(text)


async def notify_us_daily_summary(
    signals: int, trades: int, sells: int, pnl: float,
) -> None:
    text = (
        f"📊 <b>[US] 오늘 결과</b>\n"
        f"매수 신호: {signals}개 | 매수: {trades}건 | 매도: {sells}건\n"
        f"실현 손익: ${pnl:+.2f}"
    )
    await _send(text)


async def notify_us_reenter_no_signal(scanned: int) -> None:
    text = (
        f"♻️ <b>[US] 재진입 분석 완료</b>\n"
        f"스캔: {scanned}개 → 매수 신호 없음\n"
        f"현금 보유 상태로 대기"
    )
    await _send(text)


async def notify_us_watchlist_update(added: list[str], removed: list[str]) -> None:
    if not added and not removed:
        return
    lines = []
    if added:
        lines.append(f"추가: {', '.join(added)}")
    if removed:
        lines.append(f"제거: {', '.join(removed)}")
    text = f"🔄 <b>[US] NASDAQ 100 갱신</b>\n" + "\n".join(lines)
    await _send(text)
