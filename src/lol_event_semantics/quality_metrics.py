"""normalized 输出质量指标统计。"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .normalized_lookup import iter_normalized_rows

# 纯兜底短文案模板：alias 后缀固定为这三种之一时视为无语义增量。
FALLBACK_LABEL_SUFFIXES = ("事件", "音效事件", "语音事件")

LATIN_LETTER_PATTERN = re.compile(r"[A-Za-z]")


def collect_quality_metrics(normalized_root: Path, validation_json: Path) -> dict[str, Any]:
    """统计 normalized 输出与 validation 报告的质量指标。

    Args:
        normalized_root: normalized 输出根目录（含 manifest.json 与 by-entity/）。
        validation_json: normalized 校验报告 JSON 路径。

    Returns:
        可 JSON 序列化的指标 dict，含记录数交叉核对、confidence 分布、
        translation 覆盖、纯兜底文案、残留英文与 validation 计数。
    """

    record_count = 0
    confidence_counts: Counter[str] = Counter()
    translation_count = 0
    translation_by_kind: Counter[str] = Counter()
    fallback_count = 0
    residual_english_count = 0

    for _shard_path, _line_number, row in iter_normalized_rows(normalized_root):
        record_count += 1

        entity = row.get("entity") if isinstance(row.get("entity"), dict) else {}
        alias = entity.get("alias") if isinstance(entity.get("alias"), str) else ""
        kind = entity.get("kind") if isinstance(entity.get("kind"), str) else "unknown"

        semantic = row.get("semantic") if isinstance(row.get("semantic"), dict) else {}
        confidence = semantic.get("confidence")
        confidence_counts[confidence if isinstance(confidence, str) and confidence else "unknown"] += 1

        if isinstance(row.get("translation"), dict):
            translation_count += 1
            translation_by_kind[kind] += 1

        display = row.get("display") if isinstance(row.get("display"), dict) else {}
        short = display.get("zh_cn_short") if isinstance(display.get("zh_cn_short"), str) else ""

        if is_fallback_label(short, alias):
            fallback_count += 1
        if has_residual_english(short, alias):
            residual_english_count += 1

    return {
        "record_count": record_count,
        "manifest_record_count": read_manifest_record_count(normalized_root),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "translation": {
            "record_count": translation_count,
            "by_entity_kind": dict(sorted(translation_by_kind.items())),
        },
        "fallback_label": {
            "record_count": fallback_count,
            "ratio": safe_ratio(fallback_count, record_count),
        },
        "residual_english": {
            "record_count": residual_english_count,
            "ratio": safe_ratio(residual_english_count, record_count),
        },
        "validation": read_validation_metrics(validation_json),
    }


def is_fallback_label(zh_cn_short: str, alias: str) -> bool:
    """判断短文案是否为纯兜底模板。

    口径：`zh_cn_short` 精确等于（大小写敏感）`{alias}事件`、
    `{alias}音效事件`、`{alias}语音事件` 三种之一；alias 为空时不判定。

    Args:
        zh_cn_short: 记录的短展示文案。
        alias: 记录的实体别名。

    Returns:
        是否命中纯兜底模板。
    """

    if not alias:
        return False

    return any(zh_cn_short == f"{alias}{suffix}" for suffix in FALLBACK_LABEL_SUFFIXES)


def has_residual_english(zh_cn_short: str, alias: str) -> bool:
    """判断短文案在移除实体别名后是否仍残留英文。

    棘轮口径（必须确定性，改动即改变基线含义）：
    1. 仅移除 `entity.alias` 的字面出现，大小写不敏感（`re.escape(alias)` +
       `IGNORECASE`），替换为空串，不做分词、不做空白归一化；
    2. alias 为空串时不移除任何内容；
    3. 移除后若仍匹配 `[A-Za-z]`（任一 ASCII 拉丁字母）即计为残留英文。

    Args:
        zh_cn_short: 记录的短展示文案。
        alias: 记录的实体别名。

    Returns:
        是否残留英文字母。
    """

    stripped = zh_cn_short
    if alias:
        stripped = re.sub(re.escape(alias), "", stripped, flags=re.IGNORECASE)

    return bool(LATIN_LETTER_PATTERN.search(stripped))


def read_manifest_record_count(normalized_root: Path) -> int | None:
    """读取 manifest.json 中的 record_count。

    Args:
        normalized_root: normalized 输出根目录。

    Returns:
        manifest 声明的记录数；manifest 缺失或字段非整数时返回 None。
    """

    manifest_path = normalized_root / "manifest.json"
    if not manifest_path.exists():
        return None

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        return None

    value = manifest.get("record_count")
    return value if isinstance(value, int) else None


def read_validation_metrics(validation_json: Path) -> dict[str, Any]:
    """提取 validation 报告的 error/warning 与按 code 分组计数。

    Args:
        validation_json: normalized 校验报告 JSON 路径。

    Returns:
        含 error_count、warning_count、issue_counts_by_code 的 dict。
    """

    report = json.loads(validation_json.read_text(encoding="utf-8"))
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []

    counts_by_code: Counter[str] = Counter()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        code = issue.get("code")
        counts_by_code[code if isinstance(code, str) and code else "unknown"] += 1

    return {
        "error_count": summary.get("error_count"),
        "warning_count": summary.get("warning_count"),
        "issue_counts_by_code": dict(sorted(counts_by_code.items())),
    }


def safe_ratio(numerator: int, denominator: int) -> float:
    """计算比例，分母为 0 时返回 0.0。

    Args:
        numerator: 分子。
        denominator: 分母。

    Returns:
        保留 6 位小数的比例值。
    """

    if denominator == 0:
        return 0.0

    return round(numerator / denominator, 6)
