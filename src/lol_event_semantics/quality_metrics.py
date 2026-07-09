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

# 棘轮盯防的 validation issue code：这三类计数不得上升。
RATCHET_WARNING_CODES = (
    "low_confidence_output",
    "suspicious_champion_alias",
    "unknown_entity_output",
)

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
       `IGNORECASE | ASCII`，锁定 ASCII 大小写折叠，不随 Unicode 版本漂移），
       替换为空串，不做分词、不做空白归一化；
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
        stripped = re.sub(re.escape(alias), "", stripped, flags=re.IGNORECASE | re.ASCII)

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

    `issue_counts_by_code` 对全部严重级（error 与 warning）按 code 合并计数，
    不按 severity 拆分。

    Args:
        validation_json: normalized 校验报告 JSON 路径。

    Returns:
        含 error_count、warning_count、issue_counts_by_code 的 dict。

    Raises:
        ValueError: summary 的 error_count 或 warning_count 缺失或非整数。
    """

    report = json.loads(validation_json.read_text(encoding="utf-8"))
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []

    # 失败前移：计数缺失或类型漂移直接抛错，避免 None 混入基线与棘轮比较。
    for name in ("error_count", "warning_count"):
        value = summary.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{validation_json} summary.{name} 缺失或非整数：{value!r}")

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


def compare_quality_metrics(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """按棘轮规则比较当前质量指标与基线。

    棘轮规则：
    1. `validation.error_count` 必须为 0 且不得高于基线；
    2. `fallback_label.ratio` 与 `residual_english.ratio` 不得上升
       （比较比例而非计数，record_count 允许增长）；
    3. `RATCHET_WARNING_CODES` 各 code 的计数不得上升（code 缺失视为 0）；
    4. `record_count` 仅报告（verdict=info），不参与判定；
    5. 受门禁指标数值缺失或非数值（含基线有而当前无的口径漂移）判
       missing，整体失败。

    Args:
        baseline: 基线指标 dict（collect_quality_metrics 输出结构）。
        current: 当前指标 dict（同上）。

    Returns:
        `{"passed": bool, "failed_metrics": [...], "checks": [...]}`；
        每项 check 含 metric/baseline/current/verdict（ok|worse|missing|info）。
    """

    checks = [
        build_ratchet_check(
            "validation.error_count",
            metric_number(baseline, "validation", "error_count"),
            metric_number(current, "validation", "error_count"),
            require_zero=True,
        ),
        build_ratchet_check(
            "fallback_label.ratio",
            metric_number(baseline, "fallback_label", "ratio"),
            metric_number(current, "fallback_label", "ratio"),
        ),
        build_ratchet_check(
            "residual_english.ratio",
            metric_number(baseline, "residual_english", "ratio"),
            metric_number(current, "residual_english", "ratio"),
        ),
    ]
    checks.extend(
        build_ratchet_check(
            f"validation.issue_counts_by_code.{code}",
            warning_code_count(baseline, code),
            warning_code_count(current, code),
        )
        for code in RATCHET_WARNING_CODES
    )
    # record_count 允许任意方向变化（新数据会增长），只报告不判失败。
    checks.append(
        {
            "metric": "record_count",
            "baseline": baseline.get("record_count"),
            "current": current.get("record_count"),
            "verdict": "info",
        }
    )

    failed = [check["metric"] for check in checks if check["verdict"] in ("worse", "missing")]
    return {"passed": not failed, "failed_metrics": failed, "checks": checks}


def build_ratchet_check(
    metric: str,
    baseline_value: int | float | None,
    current_value: int | float | None,
    *,
    require_zero: bool = False,
) -> dict[str, Any]:
    """构造单项棘轮比较结果。

    Args:
        metric: 指标名（点分路径）。
        baseline_value: 基线数值；None 表示缺失或非数值。
        current_value: 当前数值；None 表示缺失或非数值。
        require_zero: 是否额外要求当前值必须为 0。

    Returns:
        含 metric/baseline/current/verdict 的 check 项。
    """

    if baseline_value is None or current_value is None:
        verdict = "missing"
    elif current_value > baseline_value or (require_zero and current_value > 0):
        verdict = "worse"
    else:
        verdict = "ok"

    return {"metric": metric, "baseline": baseline_value, "current": current_value, "verdict": verdict}


def metric_number(metrics: dict[str, Any], *path: str) -> int | float | None:
    """按点分路径读取指标数值。

    Args:
        metrics: 指标 dict。
        path: 逐层 key。

    Returns:
        int/float 数值；路径缺失或值非数值（bool 视为非数值）返回 None。
    """

    value: Any = metrics
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None

    return value


def warning_code_count(metrics: dict[str, Any], code: str) -> int | None:
    """读取指定 code 的 validation issue 计数。

    Args:
        metrics: 指标 dict。
        code: validation issue code。

    Returns:
        计数；`issue_counts_by_code` 中缺该 code 视为 0；
        `issue_counts_by_code` 本身缺失或计数非整数返回 None。
    """

    counts = metrics.get("validation", {}) if isinstance(metrics.get("validation"), dict) else {}
    counts = counts.get("issue_counts_by_code")
    if not isinstance(counts, dict):
        return None

    if code not in counts:
        return 0

    value = counts[code]
    if isinstance(value, bool) or not isinstance(value, int):
        return None

    return value


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
