"""验证无项目源码消费、按需加载和完整性失败的消费者边界。"""

from __future__ import annotations

import base64
import hashlib
import json
import runpy
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from lol_event_semantics import consumption

ASSETS = Path(consumption.__file__).with_name("consumer_assets")
CLIENT = ASSETS / "client.mjs"
NODE = shutil.which("node")


@pytest.fixture
def packages(tmp_path: Path) -> dict[str, Path]:
    """构造两个实体同名事件及一个缺解释记录，隔离上游语义规则。"""

    records = [
        {
            "raw_event_name": "Play_Common",
            "entity": {"kind": "champion", "alias": "Ashe"},
            "label": "移动时触发",
            "flags": 2,
            "skin": None,
            "state": ["vo", "stable", "rendered", "not_required", "translation"],
            "source_index": 1,
        },
        {
            "raw_event_name": "Play_Common",
            "entity": {"kind": "champion", "alias": "Lux"},
            "label": "攻击时触发",
            "flags": 3,
            "skin": 7,
            "state": ["vo", "experimental", "rendered", "pending", "translation"],
            "source_index": 2,
        },
        {
            "raw_event_name": "Unknown_Skin42",
            "entity": {"kind": "champion", "alias": "Lux"},
            "label": None,
            "flags": 17,
            "skin": None,
            "state": ["sfx", "experimental", "missing", "pending", "missing"],
            "source_index": 3,
        },
    ]
    metadata = {
        "schema_version": 1,
        "locale": "zh_CN",
        "source": {"dataset_id": "local-v1-" + "a" * 16, "content_digest": "a" * 64, "record_count": 3},
        "record_count": 3,
        "filters": {"support_levels": [], "asset_kinds": [], "entities": []},
        "projection_digest": consumption.projection_digest(records),
        "flags": consumption.FLAGS,
        "state_columns": list(consumption.STATE_COLUMNS),
        "text_contract": "trigger-explanation.zh_CN.v1",
        "audit_locator": "source.content_digest + normalized-events.jsonl one-based source_index",
    }
    roots = {}
    for profile, writer in [
        ("web", consumption.write_web),
        ("software", consumption.write_software),
        ("lookup", consumption.write_lookup),
    ]:
        root = tmp_path / profile
        root.mkdir()
        details = writer(records, root)
        shutil.copyfile(ASSETS / "consumer.py", root / "consumer.py")
        shutil.copyfile(ASSETS / "schema.json", root / "schema.json")
        consumption.seal_package(root, {**metadata, "profile": profile, **details})
        roots[profile] = root
    return roots


def load_consumer() -> dict[str, Any]:
    """执行独立消费者定义，避免向待校验目录写入 pycache。"""

    return runpy.run_path(str(ASSETS / "consumer.py"))


@pytest.mark.parametrize("profile", consumption.PROFILES)
def test_standalone_profiles_preserve_candidates_and_missing_labels(packages: dict[str, Path], profile: str) -> None:
    """三种布局保留候选、空解释和定位，且独立进程不需要源码路径。"""

    root = packages[profile]
    result = subprocess.run(
        [sys.executable, "-I", str(root / "consumer.py"), str(root), "Play_Common"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    data = json.loads(result.stdout)
    assert data["status"] == "FOUND"
    assert {row["label"] for row in data["records"]} == {"移动时触发", "攻击时触发"}
    assert {row["source"]["source_index"] for row in data["records"]} == {1, 2}
    consumer = load_consumer()
    unknown = consumer["query"](root, "Unknown_Skin42")
    assert unknown["records"][0]["label"] is None
    assert unknown["records"][0]["flags"] == 17
    assert consumer["query"](root, "absent") == {"status": "MISS", "records": []}


@pytest.mark.parametrize("damage", ["payload", "closure", "identity", "projection", "source", "path"])
def test_standalone_rejects_corrupt_packages(packages: dict[str, Path], damage: str) -> None:
    """损坏数据、投影及来源身份都不能伪装成查询未命中。"""

    root = packages["web"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if damage == "payload":
        path = root / manifest["shards"][0]["path"]
        path.write_bytes(path.read_bytes() + b" ")
    elif damage == "closure":
        (root / "unexpected.txt").write_text("unexpected")
    else:
        if damage == "identity":
            manifest["package_id"] = "consumption-v1-" + "0" * 64
        elif damage == "projection":
            manifest["projection_digest"] = "0" * 64
        elif damage == "source":
            manifest["source"]["dataset_id"] = "local-v1-" + "b" * 16
        else:
            manifest["files"][0]["path"] = "../outside.json"
        if damage != "identity":
            unsigned = {key: value for key, value in manifest.items() if key != "package_id"}
            manifest["package_id"] = (
                "consumption-v1-" + hashlib.sha256(consumption.canonical_bytes(unsigned)).hexdigest()
            )
        consumption.write_json(root / "manifest.json", manifest)
    with pytest.raises(ValueError):
        load_consumer()["query"](root, "absent")


def run_node(root: Path, code: str) -> dict[str, Any]:
    """用内存 HTTP 边界运行真实 ESM 客户端，记录其实际请求。"""

    if NODE is None:
        pytest.skip("Node.js is not available")
    files = {
        path.relative_to(root).as_posix(): base64.b64encode(path.read_bytes()).decode("ascii")
        for path in root.rglob("*")
        if path.is_file()
    }
    prelude = """
import {createClient, decodeShard, IntegrityError, NetworkError} from CLIENT_URI;
let input = '';
for await (const chunk of process.stdin) input += chunk;
const files = JSON.parse(input);
const requests = [];
const fetcher = async url => {
  const name = new URL(url).pathname.slice('/package/'.length);
  requests.push(name);
  return files[name] === undefined ? new Response('', {status: 404}) : new Response(Buffer.from(files[name], 'base64'));
};
const base = 'https://example.test/package/';
""".replace("CLIENT_URI", json.dumps(CLIENT.as_uri()))
    result = subprocess.run(
        [NODE, "--input-type=module", "-e", prelude + code],
        input=json.dumps(files),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


def test_web_initialization_and_entity_lookup_are_lazy(packages: dict[str, Path]) -> None:
    """初始化不下载实体；已知实体请求不下载原名路由或其他实体。"""

    result = run_node(
        packages["web"],
        """
const client = await createClient(base, {fetch: fetcher});
const initial = [...requests];
const [a, b] = await Promise.all([client.lookup('Play_Common', {entity: 'champion:Ashe'}), client.loadEntity({kind:'champion',alias:'Ashe'})]);
console.log(JSON.stringify({initial, requests, a, b}));
""",
    )
    assert result["initial"] == ["manifest.json"]
    assert len(result["requests"]) == 2
    assert result["requests"][1].startswith("entities/")
    assert result["a"]["records"][0]["label"] == "移动时触发"
    assert result["b"][0]["source"]["path"] == "normalized-events.jsonl"


def test_web_routes_keep_all_candidates_and_bound_cache(packages: dict[str, Path]) -> None:
    """未知实体只请求一个路由桶和候选实体，并按缓存容量淘汰。"""

    result = run_node(
        packages["web"],
        """
const client = await createClient(base, {fetch: fetcher, cacheSize: 1});
const found = await client.lookup('Play_Common');
const first = [...requests];
const unknown = await client.lookup('Unknown_Skin42', {entity:'champion:Lux'});
await client.loadEntity('champion:Ashe');
await client.loadEntity('champion:Lux');
const miss = await client.lookup('absent', {entity:'champion:Lux'});
console.log(JSON.stringify({found, first, requests, unknown, miss}));
""",
    )
    assert result["found"]["status"] == "FOUND"
    assert len(result["found"]["records"]) == 2
    assert len(result["first"]) == 4
    assert sum(path.startswith("routes/") for path in result["first"]) == 1
    assert result["unknown"]["records"][0]["label"] is None
    assert result["unknown"]["records"][0]["skin"] is None
    assert len(result["requests"]) >= 6
    assert result["miss"] == {"status": "MISS", "records": []}


@pytest.mark.parametrize(
    "damage,expected",
    [
        ("hash", "IntegrityError"),
        ("network", "NetworkError"),
        ("source", "IntegrityError"),
        ("redirect", "IntegrityError"),
    ],
)
def test_web_errors_are_not_misses(packages: dict[str, Path], damage: str, expected: str) -> None:
    """网络、摘要、跳转和跨来源错误必须向调用者暴露。"""

    code = """
const damage = DAMAGE;
const custom = async url => {
  if (url.endsWith('manifest.json')) return fetcher(url);
  if (damage === 'network') throw new Error('offline');
  const response = await fetcher(url);
  if (damage === 'hash') return new Response((await response.text()) + ' ');
  if (damage === 'redirect') Object.defineProperty(response, 'url', {value:'https://other.test/foreign.json'});
  return response;
};
let error = null;
try {
  const client = await createClient(base, {fetch:custom, ...(damage === 'source' ? {source:{content_digest:'b'.repeat(64)}} : {})});
  await client.lookup('Play_Common');
} catch (cause) {error = cause.name;}
console.log(JSON.stringify({error}));
""".replace("DAMAGE", json.dumps(damage))
    assert run_node(packages["web"], code)["error"] == expected


def test_web_rejects_manifest_path_escape_before_data_request(packages: dict[str, Path]) -> None:
    """即使包摘要重算正确，也拒绝跨目录 payload 路径。"""

    root = packages["web"]
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../outside.json"
    unsigned = {key: value for key, value in manifest.items() if key != "package_id"}
    manifest["package_id"] = "consumption-v1-" + hashlib.sha256(consumption.canonical_bytes(unsigned)).hexdigest()
    consumption.write_json(root / "manifest.json", manifest)
    result = run_node(
        root,
        """
let error = null;
try {await createClient(base, {fetch:fetcher});} catch (cause) {error = cause.name;}
console.log(JSON.stringify({error, requests}));
""",
    )
    assert result == {"error": "IntegrityError", "requests": ["manifest.json"]}
