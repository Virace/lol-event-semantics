"""游戏模式前缀的稳定领域定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModeNamespace:
    """模式前缀及其面向展示的语义。"""

    prefix: str
    key: str
    label: str


MODE_NAMESPACES = (
    ModeNamespace(prefix="Jade", key="jade", label="经典模式"),
    ModeNamespace(prefix="Ruby", key="ruby", label="末日人机"),
    ModeNamespace(prefix="Strawberry", key="strawberry", label="PVE 模式"),
)

_MODE_NAMESPACES_BY_TOKEN = {
    re.sub(r"[^a-z0-9]+", "", namespace.prefix.lower()): namespace for namespace in MODE_NAMESPACES
}


def mode_namespace_for_token(token: str) -> ModeNamespace | None:
    """按完整 token 解析已确认的模式命名空间。"""

    key = re.sub(r"[^a-z0-9]+", "", token.lower())
    return _MODE_NAMESPACES_BY_TOKEN.get(key)


def split_mode_entity_alias(alias: str) -> tuple[ModeNamespace, str] | None:
    """拆分已确认的 ``Mode_Entity`` 规范别名。"""

    for namespace in MODE_NAMESPACES:
        marker = f"{namespace.prefix}_"

        if alias.lower().startswith(marker.lower()) and len(alias) > len(marker):
            return namespace, alias[len(marker) :]

    return None
