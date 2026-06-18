import asyncio
import json
import logging
import time
from pathlib import Path
import httpx

logger = logging.getLogger(__name__)

from config.settings import settings

_TOKEN_CACHE_FILE = Path(__file__).parent.parent / "data_store" / "token_cache.json"
_TOKEN_EXPIRE_BUFFER = 300  # 만료 5분 전 갱신


class KISAuth:
    def __init__(self):
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    async def get_token(self) -> str:
        if self._is_token_valid():
            return self._access_token

        cached = self._load_cache()
        if cached and cached["expires_at"] - time.time() > _TOKEN_EXPIRE_BUFFER:
            self._access_token = cached["access_token"]
            self._token_expires_at = cached["expires_at"]
            return self._access_token

        await self._issue_token()
        return self._access_token

    def _is_token_valid(self) -> bool:
        return (
            self._access_token is not None
            and time.time() < self._token_expires_at - _TOKEN_EXPIRE_BUFFER
        )

    async def _issue_token(self) -> None:
        url = f"{settings.kis_base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()

        self._access_token = data["access_token"]
        # KIS 토큰 유효기간은 1일 (86400초)
        self._token_expires_at = time.time() + int(data.get("expires_in", 86400))
        self._save_cache()

    async def get_hashkey(self, body: dict) -> str:
        token = await self.get_token()
        url = f"{settings.kis_base_url}/uapi/hashkey"
        headers = {
            "content-type": "application/json",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "Authorization": f"Bearer {token}",
        }
        # 장마감 전후(15:20 등) KIS 서버 부하로 hashkey 엔드포인트가 ReadTimeout을 반환하는 경우가 있어
        # 최대 3회 재시도 (5초 간격). 이 retry가 핵심 fix — rest_client._request 단계가 아님.
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(url, json=body, headers=headers, timeout=30.0)
                    resp.raise_for_status()
                    return resp.json()["HASH"]
            except Exception as e:
                last_exc = e
                if attempt < 2:
                    logger.warning(f"hashkey 획득 실패 (시도 {attempt + 1}/3): {repr(e)} — 5초 후 재시도")
                    await asyncio.sleep(5)
        raise last_exc

    def _load_cache(self) -> dict | None:
        if not _TOKEN_CACHE_FILE.exists():
            return None
        try:
            return json.loads(_TOKEN_CACHE_FILE.read_text())
        except Exception:
            return None

    def _save_cache(self) -> None:
        _TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_CACHE_FILE.write_text(
            json.dumps({
                "access_token": self._access_token,
                "expires_at": self._token_expires_at,
            })
        )


kis_auth = KISAuth()
