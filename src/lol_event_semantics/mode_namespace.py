"""游戏模式前缀的稳定领域定义。"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModeNamespace:
    """事件前缀命名空间及其面向展示的语义。

    历史类名为兼容现有调用保留；``kind`` 明确区分游戏模式与玩法机制，
    防止 OdysseyAugments 之类机制前缀被当成英雄或模式实体。
    """

    prefix: str
    key: str
    label: str
    kind: str = "mode"
    actor_role: str | None = None
    resolve_actor: bool = True
    owner_only: bool = False


MODE_NAMESPACES = (
    # Arena 地图来源及 Riot 的 Guests of Honor 机制说明互相印证；
    # 嘉宾包含 NPC 与角色组件，仅保留 token，不按英雄前缀推断演员。
    # https://www.leagueoflegends.com/en-gb/news/dev/dev-leveling-up-arena/
    ModeNamespace(
        prefix="GoH",
        key="guests_of_honor",
        label="Guests of Honor",
        kind="mechanism",
        resolve_actor=False,
        owner_only=True,
    ),
    ModeNamespace(prefix="Jade", key="jade", label="经典模式", actor_role="champion"),
    ModeNamespace(prefix="Ruby", key="ruby", label="末日人机", actor_role="apc"),
    ModeNamespace(prefix="Strawberry", key="strawberry", label="PVE 模式", actor_role="boss"),
    ModeNamespace(
        prefix="OdysseyAugments",
        key="odyssey_augments",
        label="奥德赛增幅芯片",
        kind="mechanism",
        actor_role="augmented_champion",
    ),
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
