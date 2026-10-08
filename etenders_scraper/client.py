from __future__ import annotations

import ssl
import time
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import create_urllib3_context

DEFAULT_BASE_URL = "https://www.etenders.gov.ie/epps/"
# Back-compat alias
BASE_URL = DEFAULT_BASE_URL
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IE,en;q=0.9",
}

CIPHERS = (
    'ECDHE+AESGCM:ECDHE+CHACHA20:DHE+AESGCM:DHE+CHACHA20:ECDHE+AES+SHA256:'
    'ECDHE+AES+SHA:DHE+AES+SHA256:DHE+AES+SHA:AESGCM-HMAC-SHA256:AES-GCM:'
    'AES-CBC:HIGH:!aNULL:!eNULL:!EXPORT:!DES:!3DES:!MD5:!PSK'
)


class SSLAdapter(HTTPAdapter):
    """An HTTPAdapter that sets browser-like TLS configurations and ciphers to bypass WAFs and fix SSL EOF/reset errors."""
    def init_poolmanager(self, *args, **kwargs):
        context = create_urllib3_context(ciphers=CIPHERS)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)


class EtendersClient:
    """HTTP session for eTenders with polite rate limiting and SSL hardening."""

    def __init__(
        self,
        delay_seconds: float = 1.0,
        timeout: int = 60,
        *,
        base_url: str | None = None,
    ) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/") + "/"
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.session.mount("https://", SSLAdapter())
        self.delay_seconds = delay_seconds
        self.timeout = timeout
        self._last_request_at = 0.0

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> str:
        return self._request_raw("GET", path, params=params).text

    def post(self, path: str, *, data: dict[str, Any]) -> str:
        return self._request_raw("POST", path, data=data).text

    def get_bytes(self, path: str, *, params: dict[str, Any] | None = None) -> bytes:
        return self._request_raw("GET", path, params=params).content

    def _request_raw(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> requests.Response:
        url = path if path.startswith("http") else urljoin(self.base_url, path)
        max_retries = 3
        backoff = 1.5
        
        for attempt in range(max_retries):
            self._throttle()
            try:
                response = self.session.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                return response
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                if attempt == max_retries - 1:
                    raise
                print(f"Warning: Request to {url} failed (attempt {attempt + 1}/{max_retries}): {e}. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff *= 2
                
        raise RuntimeError("Unreachable")

    def _throttle(self) -> None:
        if self.delay_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request_at = time.monotonic()

