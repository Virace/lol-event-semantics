"""生成本地事件语义审核静态站。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


Record = Mapping[str, Any]

BATCH_LABELS = {
    "vo_primary": "VO",
    "sfx": "SFX",
    "other": "Other",
}

BATCH_ORDER = {
    "vo_primary": 0,
    "sfx": 1,
    "other": 2,
}

ISSUE_ORDER = {
    "unknown": 0,
    "low_confidence": 1,
    "manual_contextual_alias": 2,
    "candidate": 3,
    "normalized": 4,
}


def read_jsonl_records(paths: Iterable[Path]) -> list[dict[str, Any]]:
    """读取一个或多个 JSONL 拆解产物，并按原始事件名保序去重。"""

    records: list[dict[str, Any]] = []
    seen_raw_event_names: set[str] = set()

    for path in paths:
        with path.open(encoding="utf-8") as file:
            for line in file:
                stripped = line.strip()

                if not stripped:
                    continue

                record = json.loads(stripped)
                raw_event_name = str(record.get("raw_event_name") or "")

                if raw_event_name:
                    if raw_event_name in seen_raw_event_names:
                        continue

                    seen_raw_event_names.add(raw_event_name)

                records.append(record)

    return records


def build_review_groups(records: Iterable[Record], max_examples: int = 8) -> list[dict[str, Any]]:
    """把逐条事件聚合成适合人工审核的分组。"""

    grouped: dict[tuple[str, ...], dict[str, Any]] = {}

    for record in records:
        signature = record_signature(record)
        key = group_key(signature)
        group = grouped.setdefault(
            key,
            {
                "id": stable_group_id(key),
                "batch": signature["batch"],
                "batch_label": BATCH_LABELS.get(signature["batch"], signature["batch"]),
                "issue_type": issue_type(record),
                "count": 0,
                "signature": signature,
                "examples": [],
                "candidate_summary": candidate_summary(record),
                "unknown_summary": unknown_summary(record),
                "search_text": "",
            },
        )
        group["count"] = int(group["count"]) + 1
        group["issue_type"] = min_issue_type(str(group["issue_type"]), issue_type(record))

        if not group["candidate_summary"]:
            group["candidate_summary"] = candidate_summary(record)

        if not group["unknown_summary"]:
            group["unknown_summary"] = unknown_summary(record)

        examples = group["examples"]

        if isinstance(examples, list) and len(examples) < max_examples:
            examples.append(example_payload(record))

    for group in grouped.values():
        group["review_focus"] = review_focus(group)
        group["search_text"] = build_search_text(group)

    groups = list(grouped.values())
    groups.sort(
        key=lambda group: (
            BATCH_ORDER.get(str(group["batch"]), 99),
            ISSUE_ORDER.get(str(group["issue_type"]), 99),
            -int(group["count"]),
            str(group["signature"].get("owner") or ""),
            str(group["signature"].get("event_key") or ""),
        )
    )
    return groups


def write_review_site(records: Iterable[Record], output_path: Path, max_examples: int = 8) -> dict[str, Any]:
    """把审核分组写成轻量 HTML 和外部数据文件。"""

    groups = build_review_groups(records, max_examples=max_examples)
    payload = {
        "summary": build_summary(groups),
        "groups": groups,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data_path = output_path.with_name("review-data.json")
    data_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    output_path.write_text(render_review_html(data_path.name), encoding="utf-8")
    return payload


def record_signature(record: Record) -> dict[str, Any]:
    """提取分组签名字段。"""

    shell = mapping(record.get("shell"))
    owner = mapping(record.get("owner"))
    profile = mapping(record.get("profile"))
    slots = mapping(record.get("semantic_slots"))
    asset_kind = text(shell.get("asset_kind"))
    batch = record_batch(record)
    event_key = event_signature_key(slots)

    return {
        "batch": batch,
        "asset_kind": asset_kind,
        "confidence": text(record.get("confidence")),
        "scope": text(record.get("scope")),
        "owner": text(owner.get("alias") or owner.get("raw_owner_token")),
        "owner_kind": text(owner.get("kind")),
        "semantic_profile": text(profile.get("semantic_profile")),
        "primary_family": text(profile.get("primary_family")),
        "trigger_family": text(slots.get("trigger_family")),
        "event_key": event_key,
        "spell_key": text(slots.get("spell_key")),
        "phase": text(slots.get("phase")),
        "dimension_token": text(slots.get("dimension_token")),
        "contextual_component": contextual_component(slots),
        "contextual_source": contextual_source(slots),
    }


def group_key(signature: Mapping[str, Any]) -> tuple[str, ...]:
    """返回稳定分组 key。"""

    return tuple(
        text(signature.get(field))
        for field in [
            "batch",
            "asset_kind",
            "confidence",
            "owner",
            "semantic_profile",
            "primary_family",
            "trigger_family",
            "event_key",
            "spell_key",
            "phase",
            "contextual_component",
        ]
    )


def stable_group_id(key: tuple[str, ...]) -> str:
    """根据分组 key 生成稳定 ID。"""

    raw = "__".join(key)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return slug[:120] or "group"


def record_batch(record: Record) -> str:
    """返回人工审核批次。"""

    shell = mapping(record.get("shell"))
    asset_kind = text(shell.get("asset_kind"))

    if asset_kind == "vo":
        return "vo_primary"

    if asset_kind == "sfx":
        return "sfx"

    return "other"


def event_signature_key(slots: Mapping[str, Any]) -> str:
    """返回最能代表该组语义的字段。"""

    for key in ["alias_base", "alias_raw", "effect_base", "effect_head", "outcome", "spell_name", "spell_key"]:
        value = slots.get(key)

        if value:
            return text(value)

    condition = mapping(slots.get("condition"))

    if condition:
        return text(condition.get("key") or condition.get("raw"))

    contextual_alias = mapping(slots.get("contextual_alias"))

    if contextual_alias:
        return text(contextual_alias.get("component") or contextual_alias.get("raw"))

    counterpart = mapping(slots.get("counterpart"))

    if counterpart:
        return text(counterpart.get("raw") or counterpart.get("alias"))

    return text(slots.get("trigger_family") or "event")


def issue_type(record: Record) -> str:
    """判断审核重点类型。"""

    if record.get("unknowns"):
        return "unknown"

    if text(record.get("confidence")) == "low":
        return "low_confidence"

    slots = mapping(record.get("semantic_slots"))

    if contextual_source(slots) == "manual_contextual_alias":
        return "manual_contextual_alias"

    if record.get("candidates"):
        return "candidate"

    return "normalized"


def min_issue_type(left: str, right: str) -> str:
    """返回优先级更高的审核类型。"""

    return left if ISSUE_ORDER.get(left, 99) <= ISSUE_ORDER.get(right, 99) else right


def candidate_summary(record: Record) -> list[dict[str, str]]:
    """提取候选解释摘要。"""

    rows: list[dict[str, str]] = []

    for candidate in list(record.get("candidates") or [])[:3]:
        if not isinstance(candidate, Mapping):
            continue

        rows.append(
            {
                "kind": text(candidate.get("kind")),
                "raw": text(candidate.get("raw")),
                "meaning": text(candidate.get("meaning")),
                "confidence": text(candidate.get("confidence")),
                "score": text(candidate.get("score")),
                "evidence": text(candidate.get("evidence")),
            }
        )

    return rows


def unknown_summary(record: Record) -> list[dict[str, str]]:
    """提取未知项摘要。"""

    rows: list[dict[str, str]] = []

    for unknown in list(record.get("unknowns") or [])[:3]:
        if not isinstance(unknown, Mapping):
            continue

        rows.append(
            {
                "kind": text(unknown.get("kind")),
                "raw": text(unknown.get("raw")),
                "reason": text(unknown.get("blocking_reason")),
            }
        )

    return rows


def review_focus(group: Record) -> dict[str, Any]:
    """构造页面上直接展示的审核重点。"""

    issue = text(group.get("issue_type"))
    signature = mapping(group.get("signature"))
    unknown_rows = [row for row in list(group.get("unknown_summary") or []) if isinstance(row, Mapping)]
    candidate_rows = [row for row in list(group.get("candidate_summary") or []) if isinstance(row, Mapping)]

    if issue == "unknown":
        terms = unique_text(row.get("raw") for row in unknown_rows)
        details = [
            f"{text(row.get('raw'))}: {text(row.get('reason'))}"
            for row in unknown_rows
            if text(row.get("raw"))
        ]
        return {
            "kind": issue,
            "summary": "程序未能规格化这些片段，优先确认它们在完整事件名里的含义。",
            "terms": terms or signature_terms(signature),
            "details": details,
        }

    if issue == "candidate":
        terms = unique_text(row.get("raw") for row in candidate_rows)
        details = [
            f"{text(row.get('raw'))} -> {text(row.get('meaning'))} ({text(row.get('confidence')) or text(row.get('score'))})"
            for row in candidate_rows
            if text(row.get("raw")) or text(row.get("meaning"))
        ]
        return {
            "kind": issue,
            "summary": "程序给出了候选解释，但还没有作为确定规则收录。",
            "terms": terms or signature_terms(signature),
            "details": details,
        }

    if issue == "manual_contextual_alias":
        terms = unique_text([signature.get("contextual_component"), signature.get("event_key")])
        return {
            "kind": issue,
            "summary": "该组依赖人工上下文字典；需要确认上下文规则是否适用于这些事件。",
            "terms": terms,
            "details": [f"source: {text(signature.get('contextual_source'))}"] if signature.get("contextual_source") else [],
        }

    if issue == "low_confidence":
        return {
            "kind": issue,
            "summary": "整组低置信：标题是分组键，不表示只有标题后半段这个词有问题。",
            "terms": signature_terms(signature),
            "details": ["重点核对 raw_event_name、semantic_slots 与分组键是否匹配。"],
        }

    return {
        "kind": issue or "normalized",
        "summary": "已规格化分组；标题用于聚合同类事件，不是问题定位。",
        "terms": signature_terms(signature),
        "details": [],
    }


def signature_terms(signature: Mapping[str, Any]) -> list[str]:
    """从分组签名中提取适合人工核对的片段。"""

    return unique_text(
        [
            signature.get("event_key"),
            signature.get("trigger_family"),
            signature.get("spell_key"),
            signature.get("phase"),
            signature.get("dimension_token"),
            signature.get("contextual_component"),
        ]
    )


def unique_text(values: Iterable[Any]) -> list[str]:
    """按出现顺序返回去重后的非空字符串。"""

    result: list[str] = []
    seen: set[str] = set()

    for value in values:
        item = text(value)

        if not item or item in seen:
            continue

        seen.add(item)
        result.append(item)

    return result


def example_payload(record: Record) -> dict[str, Any]:
    """返回页面展示用样本记录。"""

    return {
        "raw_event_name": text(record.get("raw_event_name")),
        "confidence": text(record.get("confidence")),
        "semantic_slots": record.get("semantic_slots") or {},
        "source_paths": list(record.get("source_paths") or [])[:2],
    }


def build_search_text(group: Mapping[str, Any]) -> str:
    """构造前端搜索文本。"""

    parts: list[str] = []
    signature = mapping(group.get("signature"))
    parts.extend(text(value) for value in signature.values())
    focus = mapping(group.get("review_focus"))
    parts.append(text(focus.get("summary")))
    parts.extend(text(term) for term in list(focus.get("terms") or []))
    parts.extend(text(detail) for detail in list(focus.get("details") or []))

    for example in list(group.get("examples") or []):
        if isinstance(example, Mapping):
            parts.append(text(example.get("raw_event_name")))

    return " ".join(part for part in parts if part).lower()


def build_summary(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """构造页面摘要。"""

    by_batch: dict[str, int] = {key: 0 for key in BATCH_ORDER}
    by_issue: dict[str, int] = {key: 0 for key in ISSUE_ORDER}
    event_total = 0

    for group in groups:
        batch = text(group.get("batch"))
        issue = text(group.get("issue_type"))
        count = int(group.get("count") or 0)
        by_batch[batch] = by_batch.get(batch, 0) + count
        by_issue[issue] = by_issue.get(issue, 0) + count
        event_total += count

    return {
        "group_total": len(groups),
        "event_total": event_total,
        "by_batch": by_batch,
        "by_issue": by_issue,
    }


def contextual_component(slots: Mapping[str, Any]) -> str:
    """返回上下文别名组件。"""

    alias = mapping(slots.get("contextual_alias"))
    return text(alias.get("component"))


def contextual_source(slots: Mapping[str, Any]) -> str:
    """返回上下文别名来源。"""

    alias = mapping(slots.get("contextual_alias"))
    return text(alias.get("source"))


def mapping(value: Any) -> Mapping[str, Any]:
    """把未知值安全转成 mapping。"""

    return value if isinstance(value, Mapping) else {}


def text(value: Any) -> str:
    """把页面字段规范为字符串。"""

    if value is None:
        return ""

    return str(value)


def render_review_html(data_filename: str) -> str:
    """渲染轻量审核入口 HTML。"""

    data_url = json.dumps(data_filename, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>事件语义审核站</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --panel-soft: #eef4f8;
      --text: #172026;
      --muted: #64717c;
      --line: #d8e0e6;
      --accent: #0f766e;
      --warn: #a16207;
      --bad: #b91c1c;
      --ok: #15803d;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 14px;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      background: rgba(255, 255, 255, 0.96);
      border-bottom: 1px solid var(--line);
      padding: 14px 22px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 20px;
      font-weight: 700;
      letter-spacing: 0;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(220px, 1fr) repeat(3, minmax(130px, 170px)) auto;
      gap: 10px;
      align-items: center;
    }}
    input, select, textarea, button {{
      font: inherit;
    }}
    input, select, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
      padding: 8px 10px;
    }}
    button {{
      border: 1px solid var(--line);
      border-radius: 6px;
      background: white;
      color: var(--text);
      padding: 8px 10px;
      cursor: pointer;
    }}
    button.active, button:hover {{
      border-color: var(--accent);
      color: var(--accent);
    }}
    main {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      gap: 18px;
      padding: 18px 22px;
    }}
    aside {{
      position: sticky;
      top: 86px;
      align-self: start;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .stat-grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin-bottom: 14px;
    }}
    .stat {{
      background: var(--panel-soft);
      border-radius: 6px;
      padding: 10px;
    }}
    .stat b {{
      display: block;
      font-size: 20px;
      margin-bottom: 2px;
    }}
    .muted {{ color: var(--muted); }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 10px 0 14px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 5px 9px;
      background: white;
      white-space: nowrap;
    }}
    .section-head {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      margin-bottom: 10px;
    }}
    .pager {{
      display: flex;
      gap: 8px;
      align-items: center;
    }}
    button:disabled {{
      cursor: default;
      opacity: 0.45;
    }}
    .list {{
      display: grid;
      gap: 10px;
    }}
    .group {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }}
    .group.review-ok {{ border-left: 5px solid var(--ok); }}
    .group.review-bad {{ border-left: 5px solid var(--bad); }}
    .group.review-unsure {{ border-left: 5px solid var(--warn); }}
    .group-head {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: start;
    }}
    .title {{
      font-size: 15px;
      font-weight: 700;
      margin-bottom: 6px;
      overflow-wrap: anywhere;
    }}
    .title-label {{
      display: inline-block;
      color: var(--muted);
      font-size: 12px;
      font-weight: 600;
      margin-right: 6px;
    }}
    .badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .badge {{
      border-radius: 999px;
      padding: 3px 7px;
      background: #edf2f7;
      color: #35434f;
      font-size: 12px;
    }}
    .badge.low_confidence, .badge.unknown {{ background: #fee2e2; color: var(--bad); }}
    .badge.manual_contextual_alias {{ background: #fef3c7; color: var(--warn); }}
    .badge.candidate {{ background: #dbeafe; color: #1d4ed8; }}
    .review-focus {{
      margin-top: 10px;
      border-top: 1px solid #e8eef2;
      padding-top: 9px;
      color: #35434f;
    }}
    .focus-title {{
      font-weight: 700;
      margin-bottom: 5px;
    }}
    .focus-summary {{
      margin-bottom: 7px;
      line-height: 1.45;
    }}
    .focus-terms {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 5px;
    }}
    .term {{
      border: 1px solid #c8d5de;
      border-radius: 5px;
      background: #f8fafc;
      padding: 3px 6px;
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12px;
    }}
    .focus-details {{
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
    }}
    .examples {{
      margin-top: 10px;
      display: grid;
      gap: 8px;
    }}
    .example {{
      background: #fbfcfd;
      border: 1px solid #e8eef2;
      border-radius: 6px;
      padding: 8px;
    }}
    code {{
      font-family: Consolas, "Cascadia Mono", monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }}
    details {{
      margin-top: 7px;
    }}
    summary {{
      cursor: pointer;
      color: var(--accent);
    }}
    pre {{
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: #111827;
      color: #e5e7eb;
      border-radius: 6px;
      padding: 10px;
      margin: 7px 0 0;
      max-height: 360px;
      overflow: auto;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      gap: 6px;
    }}
    .actions button {{
      min-width: 58px;
    }}
    textarea {{
      margin-top: 8px;
      min-height: 54px;
      resize: vertical;
    }}
    .empty {{
      padding: 40px;
      text-align: center;
      color: var(--muted);
    }}
    @media (max-width: 900px) {{
      .toolbar {{ grid-template-columns: 1fr 1fr; }}
      main {{ grid-template-columns: 1fr; }}
      aside {{ position: static; }}
      .group-head {{ grid-template-columns: 1fr; }}
      .actions {{ justify-content: flex-start; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>事件语义审核站</h1>
    <div class="toolbar">
      <input id="search" type="search" placeholder="搜索 raw、owner、alias、profile">
      <select id="batchFilter"></select>
      <select id="issueFilter"></select>
      <select id="confidenceFilter"></select>
      <button id="exportBtn">导出审核结果</button>
    </div>
  </header>
  <main>
    <aside>
      <div class="stat-grid">
        <div class="stat"><b id="visibleGroups">0</b><span class="muted">当前分组</span></div>
        <div class="stat"><b id="visibleEvents">0</b><span class="muted">当前事件</span></div>
        <div class="stat"><b id="totalGroups">0</b><span class="muted">全部分组</span></div>
        <div class="stat"><b id="totalEvents">0</b><span class="muted">全部事件</span></div>
      </div>
      <div class="muted">批次</div>
      <div id="batchStats" class="chips"></div>
      <div class="muted">审核状态</div>
      <div id="reviewStats" class="chips"></div>
    </aside>
    <section>
      <div class="section-head">
        <div id="pageSummary" class="muted">正在加载数据</div>
        <div class="pager">
          <button id="prevPage" type="button">上一页</button>
          <button id="nextPage" type="button">下一页</button>
        </div>
      </div>
      <div id="groupList" class="list"></div>
    </section>
  </main>
  <script>
    const dataUrl = {data_url};
    const pageSize = 100;
    let payload = {{
      summary: {{
        group_total: 0,
        event_total: 0,
        by_batch: {{ vo_primary: 0, sfx: 0, other: 0 }},
        by_issue: {{ unknown: 0, low_confidence: 0, manual_contextual_alias: 0, candidate: 0, normalized: 0 }}
      }},
      groups: []
    }};
    let groups = [];
    let currentPage = 1;
    let renderTimer = 0;
    const storageKey = 'lol-event-semantics-review-v1';
    let decisions = loadDecisions();

    const batchLabels = {{
      all: '全部批次',
      vo_primary: 'VO',
      sfx: 'SFX',
      other: 'Other'
    }};
    const issueLabels = {{
      all: '全部类型',
      unknown: '未知项',
      low_confidence: '低置信',
      manual_contextual_alias: '人工上下文',
      candidate: '候选解释',
      normalized: '已规格化'
    }};
    const confidenceLabels = {{
      all: '全部置信',
      low: 'low',
      medium: 'medium',
      high: 'high',
      failed: 'failed'
    }};

    function loadDecisions() {{
      try {{
        if (!window.localStorage) return {{}};
        return JSON.parse(localStorage.getItem(storageKey) || '{{}}');
      }} catch {{
        return {{}};
      }}
    }}

    function saveDecisions() {{
      try {{
        if (window.localStorage) localStorage.setItem(storageKey, JSON.stringify(decisions));
      }} catch {{
        // 本地隐私设置可能禁用 localStorage，审核仍允许临时操作。
      }}
    }}

    function fillSelect(id, labels) {{
      const select = document.getElementById(id);
      Object.entries(labels).forEach(([value, label]) => {{
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        select.appendChild(option);
      }});
    }}

    fillSelect('batchFilter', batchLabels);
    fillSelect('issueFilter', issueLabels);
    fillSelect('confidenceFilter', confidenceLabels);

    ['search', 'batchFilter', 'issueFilter', 'confidenceFilter'].forEach(id => {{
      document.getElementById(id).addEventListener('input', requestRender);
      document.getElementById(id).addEventListener('change', requestRender);
    }});
    document.getElementById('exportBtn').addEventListener('click', exportDecisions);
    document.getElementById('prevPage').addEventListener('click', () => {{
      if (currentPage > 1) {{
        currentPage--;
        render();
      }}
    }});
    document.getElementById('nextPage').addEventListener('click', () => {{
      currentPage++;
      render();
    }});

    function requestRender() {{
      currentPage = 1;
      clearTimeout(renderTimer);
      renderTimer = setTimeout(render, 120);
    }}

    async function loadData() {{
      renderMessage('正在加载 review-data.json');

      try {{
        const response = await fetch(dataUrl);
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);

        payload = await response.json();
        groups = payload.groups || [];
        currentPage = 1;
        render();
      }} catch (error) {{
        renderMessage(`数据加载失败：${{error.message}}。请确认 review-data.json 与 index.html 在同一目录，并通过本地 HTTP 服务打开。`);
      }}
    }}

    function renderMessage(message) {{
      const list = document.getElementById('groupList');
      list.innerHTML = '';
      const empty = document.createElement('div');
      empty.className = 'empty';
      empty.textContent = message;
      list.appendChild(empty);
      document.getElementById('pageSummary').textContent = message;
      document.getElementById('prevPage').disabled = true;
      document.getElementById('nextPage').disabled = true;
      updateStats([]);
    }}

    function currentFilters() {{
      return {{
        search: document.getElementById('search').value.trim().toLowerCase(),
        batch: document.getElementById('batchFilter').value,
        issue: document.getElementById('issueFilter').value,
        confidence: document.getElementById('confidenceFilter').value
      }};
    }}

    function filteredGroups() {{
      const filters = currentFilters();
      return groups.filter(group => {{
        if (filters.batch !== 'all' && group.batch !== filters.batch) return false;
        if (filters.issue !== 'all' && group.issue_type !== filters.issue) return false;
        if (filters.confidence !== 'all' && group.signature.confidence !== filters.confidence) return false;
        if (filters.search && !group.search_text.includes(filters.search)) return false;
        return true;
      }});
    }}

    function render() {{
      const list = document.getElementById('groupList');
      const visible = filteredGroups();
      const pageCount = Math.max(1, Math.ceil(visible.length / pageSize));
      currentPage = Math.min(Math.max(1, currentPage), pageCount);
      const start = (currentPage - 1) * pageSize;
      const end = start + pageSize;
      const pageGroups = visible.slice(start, end);
      list.innerHTML = '';

      if (visible.length === 0) {{
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = '没有匹配的分组';
        list.appendChild(empty);
      }} else {{
        const fragment = document.createDocumentFragment();
        pageGroups.forEach(group => fragment.appendChild(renderGroup(group)));
        list.appendChild(fragment);
      }}

      updateStats(visible);
      updatePager(visible.length, pageGroups.length, start, pageCount);
    }}

    function renderGroup(group) {{
      const decision = decisions[group.id] || {{}};
      const article = document.createElement('article');
      article.className = `group ${{decision.status ? 'review-' + decision.status : ''}}`;

      const signature = group.signature;
      const title = `${{signature.owner || '(no owner)'}} / ${{signature.event_key || signature.trigger_family || '(event)'}}`;
      article.innerHTML = `
        <div class="group-head">
          <div>
            <div class="title"><span class="title-label">分组键</span>${{escapeHtml(title)}}</div>
            <div class="badges">
              ${{badge(group.batch_label)}}
              ${{badge(issueLabels[group.issue_type] || group.issue_type, group.issue_type)}}
              ${{badge(signature.confidence || 'unknown')}}
              ${{badge(signature.semantic_profile || 'unknown')}}
              ${{badge(signature.primary_family || 'unknown')}}
              ${{signature.spell_key ? badge('spell ' + signature.spell_key) : ''}}
              ${{signature.contextual_source ? badge(signature.contextual_source, 'manual_contextual_alias') : ''}}
              ${{badge(group.count + ' 条')}}
            </div>
            ${{renderReviewFocus(group.review_focus)}}
          </div>
          <div class="actions">
            <button onclick="markGroup('${{group.id}}', 'ok')">通过</button>
            <button onclick="markGroup('${{group.id}}', 'bad')">有问题</button>
            <button onclick="markGroup('${{group.id}}', 'unsure')">不确定</button>
            <button onclick="clearGroup('${{group.id}}')">清除</button>
          </div>
        </div>
        <textarea placeholder="备注" oninput="noteGroup('${{group.id}}', this.value)">${{escapeHtml(decision.note || '')}}</textarea>
      `;

      const examples = document.createElement('div');
      examples.className = 'examples';
      group.examples.forEach(example => examples.appendChild(renderExample(example)));
      article.appendChild(examples);
      return article;
    }}

    function renderReviewFocus(focus) {{
      if (!focus) return '';

      const terms = Array.isArray(focus.terms) ? focus.terms.filter(Boolean) : [];
      const details = Array.isArray(focus.details) ? focus.details.filter(Boolean) : [];
      const termsHtml = terms.length
        ? `<div class="focus-terms">${{terms.map(term => `<span class="term">${{escapeHtml(term)}}</span>`).join('')}}</div>`
        : '';
      const detailsHtml = details.length
        ? `<ul class="focus-details">${{details.map(detail => `<li>${{escapeHtml(detail)}}</li>`).join('')}}</ul>`
        : '';

      return `
        <div class="review-focus">
          <div class="focus-title">审核重点</div>
          <div class="focus-summary">${{escapeHtml(focus.summary || '')}}</div>
          ${{termsHtml}}
          ${{detailsHtml}}
        </div>
      `;
    }}

    function renderExample(example) {{
      const div = document.createElement('div');
      div.className = 'example';
      div.innerHTML = `
        <code>${{escapeHtml(example.raw_event_name)}}</code>
        <details>
          <summary>semantic_slots</summary>
          <pre>${{escapeHtml(JSON.stringify(example.semantic_slots, null, 2))}}</pre>
        </details>
        ${{example.candidates && example.candidates.length ? `<details><summary>candidates</summary><pre>${{escapeHtml(JSON.stringify(example.candidates, null, 2))}}</pre></details>` : ''}}
        ${{example.unknowns && example.unknowns.length ? `<details><summary>unknowns</summary><pre>${{escapeHtml(JSON.stringify(example.unknowns, null, 2))}}</pre></details>` : ''}}
      `;
      return div;
    }}

    function updateStats(visible) {{
      const visibleEvents = visible.reduce((sum, group) => sum + group.count, 0);
      document.getElementById('visibleGroups').textContent = visible.length;
      document.getElementById('visibleEvents').textContent = visibleEvents;
      document.getElementById('totalGroups').textContent = payload.summary.group_total;
      document.getElementById('totalEvents').textContent = payload.summary.event_total;

      renderChips('batchStats', payload.summary.by_batch, batchLabels);
      renderReviewStats();
    }}

    function updatePager(visibleCount, pageLength, start, pageCount) {{
      const summary = document.getElementById('pageSummary');
      const prev = document.getElementById('prevPage');
      const next = document.getElementById('nextPage');

      if (visibleCount === 0) {{
        summary.textContent = '没有匹配的分组';
      }} else {{
        summary.textContent = `第 ${{start + 1}}-${{start + pageLength}} 组 / 共 ${{visibleCount}} 组，页 ${{currentPage}}/${{pageCount}}`;
      }}

      prev.disabled = currentPage <= 1;
      next.disabled = currentPage >= pageCount || visibleCount === 0;
    }}

    function renderChips(id, values, labels) {{
      const root = document.getElementById(id);
      root.innerHTML = '';
      Object.entries(values).forEach(([key, value]) => {{
        if (!value) return;
        const span = document.createElement('span');
        span.className = 'chip';
        span.textContent = `${{labels[key] || key}} ${{value}}`;
        root.appendChild(span);
      }});
    }}

    function renderReviewStats() {{
      const counts = {{ ok: 0, bad: 0, unsure: 0 }};
      Object.values(decisions).forEach(decision => {{
        if (decision.status && counts[decision.status] !== undefined) counts[decision.status]++;
      }});
      const root = document.getElementById('reviewStats');
      root.innerHTML = '';
      Object.entries({{ ok: '通过', bad: '有问题', unsure: '不确定' }}).forEach(([key, label]) => {{
        const span = document.createElement('span');
        span.className = 'chip';
        span.textContent = `${{label}} ${{counts[key]}}`;
        root.appendChild(span);
      }});
    }}

    window.markGroup = function(id, status) {{
      decisions[id] = {{ ...(decisions[id] || {{}}), status, updated_at: new Date().toISOString() }};
      saveDecisions();
      render();
    }};

    window.clearGroup = function(id) {{
      delete decisions[id];
      saveDecisions();
      render();
    }};

    window.noteGroup = function(id, note) {{
      decisions[id] = {{ ...(decisions[id] || {{}}), note, updated_at: new Date().toISOString() }};
      saveDecisions();
      renderReviewStats();
    }};

    function exportDecisions() {{
      const blob = new Blob([JSON.stringify({{ exported_at: new Date().toISOString(), decisions }}, null, 2)], {{
        type: 'application/json'
      }});
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'review-decisions.json';
      link.click();
      URL.revokeObjectURL(url);
    }}

    function badge(text, klass = '') {{
      return `<span class="badge ${{klass}}">${{escapeHtml(text)}}</span>`;
    }}

    function escapeHtml(value) {{
      return String(value ?? '').replace(/[&<>"']/g, char => ({{
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;'
      }}[char]));
    }}

    loadData();
  </script>
</body>
</html>
"""
