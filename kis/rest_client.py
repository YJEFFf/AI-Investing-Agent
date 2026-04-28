import asyncio
import logging
from typing import Any

import httpx
from aiolimiter import AsyncLimiter

from config.settings import settings
from kis.auth import kis_auth

logger = logging.getLogger(__name__)

# 15 req/s (KIS 한도 20 req/s에서 여유 확보)
_rate_limiter = AsyncLimiter(15, 1)
_MAX_RETRIES = 3
_RETRY_DELAY = 1.0


class KISRestClient:
    def __init__(self):
        self._client = httpx.AsyncClient(timeout=30.0)

    async def get(self, path: str, tr_id: str, params: dict | None = None) -> dict:
        return await self._request("GET", path, tr_id, params=params)

    async def post(self, path: str, tr_id: str, body: dict) -> dict:
        hashkey = await kis_auth.get_hashkey(body)
        return await self._request("POST", path, tr_id, body=body, hashkey=hashkey)

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict | None = None,
        body: dict | None = None,
        hashkey: str | None = None,
    ) -> dict:
        token = await kis_auth.get_token()
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": tr_id,
        }
        if hashkey:
            headers["hashkey"] = hashkey

        url = f"{settings.kis_base_url}{path}"

        for attempt in range(_MAX_RETRIES):
            async with _rate_limiter:
                try:
                    resp = await self._client.request(
                        method, url, headers=headers, params=params, json=body
                    )
                    resp.raise_for_status()
                    data = resp.json()

                    # KIS 에러 코드 처리
                    rt_cd = data.get("rt_cd", "0")
                    if rt_cd != "0":
                        msg = data.get("msg1", "Unknown KIS error")
                        # EGW00201: 초당 거래건수 초과 → 재시도
                        if "EGW00201" in msg and attempt < _MAX_RETRIES - 1:
                            await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                            continue
                        raise RuntimeError(f"KIS API error [{rt_cd}]: {msg}")

                    return data

                except httpx.HTTPStatusError as e:
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(_RETRY_DELAY * (attempt + 1))
                        continue
                    raise RuntimeError(f"HTTP error: {e}") from e

        raise RuntimeError(f"KIS API request failed after {_MAX_RETRIES} retries")

    async def close(self):
        await self._client.aclose()


kis_client = KISRestClient()
