"""Small shared HTTP GET boundary for Paper Trans integrations."""

from __future__ import annotations

import time

import requests

from .env_config import http_proxies


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    max_retries: int = 4,
    timeout: int | float = 30,
    use_proxy: bool = True,
    log_prefix: str = "http",
) -> str:
    """Fetch text with one shared proxy/direct fallback and bounded backoff."""
    request_headers = dict(DEFAULT_HEADERS)
    if headers:
        request_headers.update(headers)

    proxy_enabled = bool(use_proxy)
    last_exc = None
    for attempt in range(max_retries):
        proxies = http_proxies(proxy_enabled)
        try:
            response = requests.get(
                url,
                headers=request_headers,
                proxies=proxies,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.text
        except requests.exceptions.ProxyError as exc:
            last_exc = exc
            if proxy_enabled:
                print(f"[{log_prefix}] 代理失败，切换直连...", flush=True)
                proxy_enabled = False
        except (
            requests.exceptions.SSLError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as exc:
            last_exc = exc
            wait = 2**attempt
            print(
                f"[{log_prefix}] 连接错误 (尝试 {attempt + 1}/{max_retries}): "
                f"{type(exc).__name__}",
                flush=True,
            )
            if attempt < max_retries - 1:
                if proxy_enabled:
                    print(f"[{log_prefix}] 切换直连重试...", flush=True)
                    proxy_enabled = False
                else:
                    print(f"[{log_prefix}] 等待 {wait}s 后重试...", flush=True)
                    time.sleep(wait)
        except Exception as exc:
            last_exc = exc
            wait = 2**attempt
            print(f"[{log_prefix}] 请求失败 (尝试 {attempt + 1}/{max_retries}): {exc}", flush=True)
            if attempt < max_retries - 1:
                print(f"[{log_prefix}] 等待 {wait}s 后重试...", flush=True)
                time.sleep(wait)

    raise last_exc or Exception("请求失败")
