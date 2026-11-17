"""Polite HTTP client: identifies itself, retries transient errors and spaces requests per host."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import httpx

from . import config

log = logging.getLogger(__name__)


class Client:
    def __init__(self, delay: float | None = None, timeout: float = 60.0):
        self.delay = config.REQUEST_DELAY if delay is None else delay
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()
        self._http = httpx.Client(
            headers={"User-Agent": config.USER_AGENT, "Accept-Language": "pt-PT,pt;q=0.9"},
            timeout=timeout, follow_redirects=True,
        )

    def _wait(self, url: str) -> None:
        host = urlparse(url).netloc
        with self._lock:
            wait = self._last.get(host, 0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def request(self, method: str, url: str, retries: int = 3, timeout: float | None = None, **kw) -> httpx.Response:
        error: Exception | None = None
        for attempt in range(retries):
            self._wait(url)
            try:
                kwargs = dict(kw)
                if timeout is not None:
                    kwargs["timeout"] = timeout
                r = self._http.request(method, url, **kwargs)
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                r.raise_for_status()
                return r
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                error = e
                status = getattr(getattr(e, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    break  # 404 and friends: retrying will not help
                time.sleep(min(60, 5 * 2 ** attempt))
        assert error is not None
        raise error

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> httpx.Response:
        return self.request("POST", url, **kw)

    def close(self) -> None:
        self._http.close()
