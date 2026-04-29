import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Callable, Awaitable

import websockets
from websockets.exceptions import ConnectionClosedError

from config.settings import settings
from kis.auth import kis_auth

logger = logging.getLogger(__name__)

_RECONNECT_DELAY = 30
_MAX_SUBSCRIPTIONS = 41


@dataclass
class Tick:
    code: str
    price: int
    volume: int
    change_sign: str  # 1=상한, 2=상승, 3=보합, 4=하한, 5=하락


class KISWebSocketClient:
    def __init__(self):
        self._subscriptions: set[str] = set()
        self._handlers: list[Callable[[Tick], Awaitable[None]]] = []
        self._running = False
        self._ws = None

    def add_handler(self, handler: Callable[[Tick], Awaitable[None]]) -> None:
        self._handlers.append(handler)

    def subscribe(self, codes: list[str]) -> None:
        for code in codes:
            if len(self._subscriptions) >= _MAX_SUBSCRIPTIONS:
                logger.warning(f"WebSocket 구독 한도({_MAX_SUBSCRIPTIONS}) 초과")
                break
            self._subscriptions.add(code)

    def unsubscribe(self, codes: list[str]) -> None:
        for code in codes:
            self._subscriptions.discard(code)

    async def connect_and_run(self) -> None:
        self._running = True
        delay = _RECONNECT_DELAY
        while self._running:
            try:
                await self._run_session()
                delay = _RECONNECT_DELAY  # 정상 종료 시 딜레이 초기화
            except Exception as e:
                logger.error(f"WebSocket 연결 오류: {e}, {delay}초 후 재연결")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 300)  # 지수 백오프, 최대 5분

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _run_session(self) -> None:
        approval_key = await self._get_approval_key()
        async with websockets.connect(
            settings.kis_ws_url,
            ping_interval=None,
            ping_timeout=None,
            close_timeout=10,
        ) as ws:
            self._ws = ws
            logger.info("WebSocket 연결됨")

            # 서버 준비 대기 후 구독 전송
            await asyncio.sleep(1.0)
            for code in list(self._subscriptions):
                await self._send_subscribe(ws, approval_key, code)
                await asyncio.sleep(0.1)

            async for message in ws:
                if not self._running:
                    break
                await self._handle_message(message)

    async def _handle_message(self, message: str) -> None:
        try:
            # KIS WebSocket: 데이터는 '|' 구분자로 전달
            if message.startswith("{"):
                # JSON 형식: 연결 확인, 에러 메시지 등
                data = json.loads(message)
                if data.get("header", {}).get("tr_id") == "PINGPONG":
                    if self._ws:
                        await self._ws.send(message)  # PINGPONG 응답
                return

            parts = message.split("|")
            if len(parts) < 4:
                return

            tr_id = parts[1]
            if tr_id != "H0STCNT0":  # 실시간 체결가
                return

            # 체결 데이터 파싱 (^구분자)
            fields = parts[3].split("^")
            if len(fields) < 13:
                return

            tick = Tick(
                code=fields[0],
                price=int(fields[2]),
                volume=int(fields[12]),
                change_sign=fields[3],
            )
            for handler in self._handlers:
                await handler(tick)

        except Exception as e:
            logger.debug(f"WebSocket 메시지 파싱 오류: {e}")

    async def _send_subscribe(self, ws, approval_key: str, code: str) -> None:
        msg = json.dumps({
            "header": {
                "approval_key": approval_key,
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",
                    "tr_key": code,
                }
            },
        })
        await ws.send(msg)

    async def _get_approval_key(self) -> str:
        import httpx
        url = f"{settings.kis_base_url}/oauth2/Approval"
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "secretkey": settings.kis_app_secret,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10.0)
            resp.raise_for_status()
            return resp.json()["approval_key"]


kis_ws = KISWebSocketClient()
