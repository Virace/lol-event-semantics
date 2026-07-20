"""共享 HTTP 下载：带退避重试的 bytes 获取。

官方 CDN（riotcdn/ddragon）与 GitHub raw 存在偶发不可达/SSL 握手超时，
各在线 source 的 fetch_bytes 统一委托本模块，不再各自维护无重试实现。
"""

from __future__ import annotations

import time
import urllib.request


def fetch_bytes(url: str, *, attempts: int = 4, backoff: float = 2.0, timeout: int = 60) -> bytes:
    """下载 URL 的原始字节，失败按线性退避重试。

    Args:
        url: 目标地址。
        attempts: 总尝试次数。
        backoff: 基础退避秒数（第 n 次失败后等待 backoff*n 秒）。
        timeout: 单次请求超时秒数。

    Returns:
        响应字节。

    Raises:
        RuntimeError: 全部尝试失败（携带最后一次异常）。
    """

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # noqa: BLE001 - 网络异常统一重试后上抛
            last = exc
            if attempt < attempts - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"下载失败（重试 {attempts} 次）：{url}") from last
