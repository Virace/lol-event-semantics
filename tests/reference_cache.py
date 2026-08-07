"""测试共享的真实 reference 加载缓存。

真实 reference/ 合集含上万个 YAML 文件，单次全量加载需数十秒。本模块把
ReferenceIndex 与 TranslationBuildContext 的构建缓存为进程级单例，供各
测试文件复用，避免同一 pytest 进程内重复解析。共享实例按只读约定使用：
测试不得修改返回对象。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

from lol_event_semantics.decomposition_model import EventDecompositionRecord
from lol_event_semantics.decomposition_reference import ReferenceIndex
from lol_event_semantics.normalized_output import (
    ChampionEntityResolver,
    build_translation_context,
    load_champion_entity_resolver,
    load_reference_entity_aliases,
    write_normalized_outputs,
)
from lol_event_semantics.normalized_translation_model import TranslationBuildContext
from lol_event_semantics.pattern_catalog import PatternPolicy, load_pattern_policy

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_ROOT = REPO_ROOT / "reference"
OVERRIDES_ROOT = REPO_ROOT / "overrides"
RULES_ROOT = REPO_ROOT / "rules"
CONTRACT_ASSETS_ROOT = REPO_ROOT / "data"
TEST_DIGEST = "0" * 64
TEST_INPUT_IDENTITY: dict[str, object] = {
    "schema": "lol-event-semantics.input-identity.v1",
    "profile": {
        "profile_id": "tests",
        "corpus_scope": "fixture",
        "base_game_version": "test",
        "raw_unique_count": 0,
        "accepted_exceptions": [],
    },
    "generator_state": {"commit": "tests", "dirty": True},
    "inputs": {
        name: {"digest": TEST_DIGEST}
        for name in (
            "raw",
            "reference",
            "reference_manifest",
            "rules",
            "overrides",
            "contract_assets",
            "generator",
            "sources_manifest",
            "frozen_profile",
        )
    },
}


@lru_cache(maxsize=1)
def reference_index() -> ReferenceIndex:
    """返回进程内共享的真实 reference index。"""

    return ReferenceIndex.from_reference_collection(REFERENCE_ROOT, RULES_ROOT, OVERRIDES_ROOT)


@lru_cache(maxsize=1)
def translation_context() -> TranslationBuildContext:
    """返回进程内共享的真实 reference translation 构建上下文。"""

    return build_translation_context(REFERENCE_ROOT, rules_root=RULES_ROOT, overrides_root=OVERRIDES_ROOT)


@lru_cache(maxsize=1)
def pattern_policy() -> PatternPolicy:
    """返回进程内共享的版本化 pattern policy。"""

    return load_pattern_policy(RULES_ROOT / "translation_patterns.yaml")


@lru_cache(maxsize=1)
def entity_resolver() -> ChampionEntityResolver:
    """返回进程内共享的 champion 实体归一解析器。"""

    return load_champion_entity_resolver(REFERENCE_ROOT, OVERRIDES_ROOT)


@lru_cache(maxsize=1)
def entity_aliases() -> dict[tuple[str, str], str]:
    """返回进程内共享的实体归一 alias 映射。"""

    return load_reference_entity_aliases(REFERENCE_ROOT)


def write_outputs(
    records: Iterable[EventDecompositionRecord],
    output_root: Path,
    *,
    reference_version: str,
) -> None:
    """以共享缓存依赖调用 write_normalized_outputs，行为与生产默认路径一致。"""

    write_normalized_outputs(
        records,
        output_root,
        reference_version=reference_version,
        reference_root=REFERENCE_ROOT,
        rules_root=RULES_ROOT,
        overrides_root=OVERRIDES_ROOT,
        input_identity=TEST_INPUT_IDENTITY,
        reference_aliases=entity_aliases(),
        resolver=entity_resolver(),
        translation_context=translation_context(),
        pattern_policy=pattern_policy(),
        contract_assets_root=CONTRACT_ASSETS_ROOT,
    )
