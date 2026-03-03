from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential_jitter

from .config import HttpConfig

log = logging.getLogger("http")


@dataclass
class _RateLimiter:
    rps: float
    _lock: asyncio.Lock = asyncio.Lock()
    _last: float = 0.0

    async def wait(self) -> None:
        if self.rps <= 0:
            return
        min_interval = 1.0 / self.rps
        async with self._lock:
            now = time.monotonic()
            delta = now - self._last
            if delta < min_interval:
                await asyncio.sleep(min_interval - delta)
            self._last = time.monotonic()


class HttpClient:
    def __init__(self, cfg: HttpConfig):
        self.cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._sem = asyncio.Semaphore(cfg.concurrency)
        self._rl = _RateLimiter(cfg.rate_limit_rps)

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(self.cfg.timeout_s),
            headers={"User-Agent": self.cfg.user_agent},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if not self._client:
            raise RuntimeError("HttpClient non inizializzato")
        return self._client

    def _retry(self):
        return retry(
            stop=stop_after_attempt(self.cfg.max_retries),
            wait=wait_exponential_jitter(initial=self.cfg.backoff_base_s, max=8.0),
            reraise=True,
        )

    async def _bounded(self):
        await self._rl.wait()
        return self._sem

    async def get_text(self, url: str) -> str:
        @self._retry()
        async def _do() -> str:
            async with (await self._bounded()):
                r = await self.client.get(url)
                r.raise_for_status()
                return r.text
        return await _do()

    async def get_bytes(self, url: str, max_bytes: int = 30_000_000) -> bytes:
        @self._retry()
        async def _do() -> bytes:
            async with (await self._bounded()):
                r = await self.client.get(url)
                r.raise_for_status()
                data = r.content
                if len(data) > max_bytes:
                    raise ValueError(f"File troppo grande: {len(data)} bytes > {max_bytes}")
                return data
        return await _do()

    async def head_ok(self, url: str) -> bool:
        @self._retry()
        async def _do() -> bool:
            async with (await self._bounded()):
                r = await self.client.head(url)
                if r.status_code == 405:
                    r = await self.client.get(url, headers={"Range": "bytes=0-0"})
                return 200 <= r.status_code < 300
        return await _do()
