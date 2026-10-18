"""riotmanifest 同步传输遵循环境代理的离线回归。"""

from __future__ import annotations

import os
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from lol_event_semantics.riotmanifest_transport import configure_environment_proxy
from lol_event_semantics.wad_source import _riotmanifest


@pytest.fixture
def transport():
    """注入最小第三方传输边界，默认测试不需要 download 可选依赖。"""

    direct = Mock(headers={"User-Agent": "upstream"}, connection_pool_kw={"maxsize": 64, "retries": False})
    direct.pools._maxsize = 16
    client = SimpleNamespace(_pool=direct)
    http_client = ModuleType("riotmanifest.utils.http_client")
    http_client._DEFAULT_HTTP_CLIENT = client
    urllib3 = ModuleType("urllib3")
    managers = {}

    def proxy_factory(url, **kwargs):
        """记录每个地址对应的传输桩。"""

        managers[url] = Mock()
        return managers[url]

    urllib3.ProxyManager = Mock(side_effect=proxy_factory)
    with patch.dict(sys.modules, {http_client.__name__: http_client, "urllib3": urllib3}):
        yield SimpleNamespace(direct=direct, client=client, proxy_factory=urllib3.ProxyManager, managers=managers)


@pytest.mark.parametrize("scheme", ["http", "https"])
def test_routes_matching_environment_proxy_and_preserves_request(transport, scheme):
    """HTTP/HTTPS 分别走既有代理，Range、超时与内容预载参数原样保留。"""

    proxies = {"HTTP_PROXY": "http://http-proxy.test:8080", "HTTPS_PROXY": "http://https-proxy.test:8080"}
    with patch.dict(os.environ, proxies, clear=True):
        configure_environment_proxy()
    request = {"headers": {"Range": "bytes=12-25"}, "timeout": object(), "preload_content": True}
    url = f"{scheme}://cdn.test/bundle"
    response = transport.client._pool.request("GET", url, **request)
    manager = transport.managers[proxies[f"{scheme.upper()}_PROXY"]]
    manager.request.assert_called_once_with("GET", url, **request)
    assert response is manager.request.return_value
    transport.direct.request.assert_not_called()
    for call in transport.proxy_factory.call_args_list:
        assert call.kwargs == {
            "num_pools": 16,
            "headers": {"User-Agent": "upstream"},
            "maxsize": 64,
            "retries": False,
        }


@pytest.mark.parametrize("no_proxy", ["cdn.test", ".cdn.test", "*", "bundle.cdn.test:8443"])
def test_no_proxy_hosts_keep_direct_transport(transport, no_proxy):
    """绕过规则覆盖主机、域后缀、端口与通配符，保留原有直连池。"""

    with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.test:8080", "NO_PROXY": no_proxy}, clear=True):
        configure_environment_proxy()
    transport.client._pool.request("GET", "https://bundle.cdn.test:8443/bundle", timeout=12)
    transport.direct.request.assert_called_once_with("GET", "https://bundle.cdn.test:8443/bundle", timeout=12)
    for manager in transport.managers.values():
        manager.request.assert_not_called()


def test_no_proxy_suffix_does_not_match_unrelated_domain(transport):
    """绕过域后缀必须匹配标签边界，不能误放行同尾字符串。"""

    with patch.dict(os.environ, {"https_proxy": "http://proxy.test:8080", "no_proxy": "cdn.test"}, clear=True):
        configure_environment_proxy()
    transport.client._pool.request("GET", "https://unrelatedcdn.test/bundle")
    transport.direct.request.assert_not_called()


def test_missing_scheme_proxy_keeps_direct_transport(transport):
    """只有 HTTPS 代理时，HTTP 请求仍使用上游直连池。"""

    with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.test:8080"}, clear=True):
        configure_environment_proxy()
    transport.client._pool.request("GET", "http://cdn.test/bundle")
    transport.direct.request.assert_called_once_with("GET", "http://cdn.test/bundle")


def test_no_environment_proxy_leaves_upstream_unchanged(transport):
    """未配置 HTTP(S) 代理时不改写第三方客户端。"""

    with patch.dict(os.environ, {}, clear=True):
        configure_environment_proxy()
    assert transport.client._pool is transport.direct
    transport.proxy_factory.assert_not_called()


def test_repeated_configuration_is_idempotent_and_reuses_proxy_pool(transport):
    """重复导入不叠加路由器，相同代理地址只建立一个有界池。"""

    with patch.dict(
        os.environ, {"HTTP_PROXY": "http://proxy.test:8080", "HTTPS_PROXY": "http://proxy.test:8080"}, clear=True
    ):
        configure_environment_proxy()
        installed = transport.client._pool
        configure_environment_proxy()
    assert transport.client._pool is installed
    assert transport.proxy_factory.call_count == 1


def test_lowercase_proxy_overrides_uppercase(transport):
    """遵循 Python 环境代理规则，小写变量覆盖同名大写变量。"""

    with patch.dict(
        os.environ, {"HTTPS_PROXY": "http://upper.test:8080", "https_proxy": "http://lower.test:8080"}, clear=True
    ):
        configure_environment_proxy()
    assert transport.proxy_factory.call_args.args[0] == "http://lower.test:8080"


def test_cgi_context_ignores_uppercase_http_proxy(transport):
    """沿用标准库的 CGI 安全规则，拒绝请求头映射出的 HTTP_PROXY。"""

    with patch.dict(os.environ, {"HTTP_PROXY": "http://proxy.test:8080", "REQUEST_METHOD": "GET"}, clear=True):
        configure_environment_proxy()
    assert transport.client._pool is transport.direct
    transport.proxy_factory.assert_not_called()


def test_preserves_upstream_tls_configuration_and_network_exception(transport):
    """保留上游证书设置与原始网络异常，不降级 TLS 或静默改为直连。"""

    tls_context = object()
    transport.direct.connection_pool_kw["ssl_context"] = tls_context
    with patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.test:8080"}, clear=True):
        configure_environment_proxy()
    assert transport.proxy_factory.call_args.kwargs["ssl_context"] is tls_context
    error = RuntimeError("network unavailable")
    transport.managers["http://proxy.test:8080"].request.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        transport.client._pool.request("GET", "https://cdn.test/bundle")
    assert caught.value is error
    transport.direct.request.assert_not_called()


def test_existing_riotmanifest_byte_helpers_use_installed_pool():
    """可选真实依赖中的已导入 helper 仍走新池，并保留状态校验。"""

    http_client = pytest.importorskip("riotmanifest.utils.http_client")
    manifest = pytest.importorskip("riotmanifest.manifest")
    extractor = pytest.importorskip("riotmanifest.extractor.wad_extractor")
    client = http_client.HttpClient()
    response = SimpleNamespace(status=206, data=b"chunk", headers={"Content-Length": "5"})
    manager = Mock()
    manager.request.return_value = response
    with (
        patch.dict(os.environ, {"HTTPS_PROXY": "http://proxy.test:8080"}, clear=True),
        patch.object(http_client, "_DEFAULT_HTTP_CLIENT", client),
        patch("urllib3.ProxyManager", return_value=manager),
    ):
        configure_environment_proxy()
        for helper in (manifest.http_get_bytes, extractor.http_get_bytes):
            assert helper("https://cdn.test/bundle", headers={"Range": "bytes=0-4"}) == b"chunk"
        assert manager.request.call_args.kwargs["timeout"] is client.timeout
        assert manager.request.call_args.kwargs["headers"] == {"Range": "bytes=0-4"}
        response.status = 503
        with pytest.raises(http_client.HttpClientError, match="503"):
            manifest.http_get_bytes("https://cdn.test/bundle")


def test_riotmanifest_import_configures_transport():
    """所有 WAD 远程入口经惰性导入统一安装环境传输适配。"""

    backend = ModuleType("riotmanifest")
    with (
        patch.dict(sys.modules, {"riotmanifest": backend}),
        patch("lol_event_semantics.wad_source.configure_environment_proxy") as configure,
    ):
        assert _riotmanifest() is backend
    configure.assert_called_once_with()
