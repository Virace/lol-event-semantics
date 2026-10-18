"""为 riotmanifest 的同步 HTTP 边界补充既有环境代理路由。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from threading import RLock
from typing import Any
from urllib.parse import urlsplit
from urllib.request import getproxies_environment, proxy_bypass_environment  # type: ignore[attr-defined]

_CONFIGURE_LOCK = RLock()


class _EnvironmentProxyPool:
    """按环境快照选择直连或有界代理池，不改变上游请求及 TLS 参数。"""

    def __init__(self, direct_pool: Any, proxies: Mapping[str, str], proxy_factory: Callable[..., Any]) -> None:
        """复用原有连接配置，每个不同的 HTTP(S) 代理只建立一个池。"""

        self._direct_pool = direct_pool
        self._proxies = {key: value for key, value in proxies.items() if key in ("http", "https", "no")}
        proxy_urls = dict.fromkeys(proxies[scheme] for scheme in ("http", "https") if proxies.get(scheme))
        self._proxy_pools = {
            url: proxy_factory(
                url,
                num_pools=direct_pool.pools._maxsize,
                headers=direct_pool.headers,
                **direct_pool.connection_pool_kw,
            )
            for url in proxy_urls
        }

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """遵循 NO_PROXY 选择传输，并原样转发 Range、超时与其他请求参数。"""

        target = urlsplit(url)
        proxy_url = self._proxies.get(target.scheme)
        pool = self._direct_pool
        if proxy_url and not proxy_bypass_environment(target.netloc, self._proxies):
            pool = self._proxy_pools[proxy_url]
        return pool.request(method, url, **kwargs)


def configure_environment_proxy() -> None:
    """幂等安装 riotmanifest 2.4.3 默认同步客户端的环境代理适配。

    首次安装读取 HTTP_PROXY、HTTPS_PROXY 与 NO_PROXY（含小写形式），
    没有 HTTP(S) 代理时保持原样。只替换默认客户端的连接池，保留已被
    manifest、版本解析和 WAD Range 下载导入的函数及其响应校验、异常语义。
    不修改第三方安装文件、异步下载器或系统网络配置。
    """

    proxies = getproxies_environment()
    if not any(proxies.get(scheme) for scheme in ("http", "https")):
        return
    with _CONFIGURE_LOCK:
        http_client = import_module("riotmanifest.utils.http_client")
        client = http_client._DEFAULT_HTTP_CLIENT
        if isinstance(client._pool, _EnvironmentProxyPool):
            return
        urllib3 = import_module("urllib3")
        client._pool = _EnvironmentProxyPool(client._pool, proxies, urllib3.ProxyManager)
