"""V1 产品审核 bundle 与本地静态 Review Site。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

from .advisory_lock import AdvisoryLockUnavailableError, advisory_file_lock
from .dataset_identity import review_decision_identity, review_decision_source_path, rules_tree_digest
from .review_decisions import (
    BUNDLE_SCHEMA_VERSION,
    ReviewDecisionError,
    TRANSACTION_LOCK_NAME,
    build_target,
    canonical_digest,
    load_decision_source,
    verify_source_review,
)

__all__ = ["build_review_bundle", "write_review_site"]


def build_review_bundle(
    normalized_root: Path,
    support_diff_path: Path,
    decision_source_path: Path,
    rules_root: Path,
) -> dict[str, Any]:
    """从不可变 candidate 构造精简、可哈希的产品审核 bundle。"""

    manifest = _read_object(normalized_root / "manifest.json")
    try:
        expected_source_path = review_decision_source_path(rules_root)
    except ValueError as exc:
        raise ReviewDecisionError(str(exc)) from exc
    if decision_source_path.resolve() != expected_source_path:
        raise ReviewDecisionError("decision source 必须是 rules/review-decisions.v1.json")
    expected_inputs = _mapping(_mapping(_mapping(manifest.get("content_identity")).get("input_identity")).get("inputs"))
    expected_rules = expected_inputs.get("rules")
    expected_review = expected_inputs.get("review_decisions")
    try:
        with advisory_file_lock(rules_root / TRANSACTION_LOCK_NAME):
            rules_before = rules_tree_digest(rules_root).to_dict()
            source = load_decision_source(decision_source_path, allow_transaction_state=True)
            rules_after = rules_tree_digest(rules_root).to_dict()
    except AdvisoryLockUnavailableError as exc:
        raise ReviewDecisionError("另一 review source transaction 正在运行") from exc
    if rules_before != rules_after or rules_after != expected_rules:
        raise ReviewDecisionError("rules tree 与 candidate input identity 不一致")
    if review_decision_identity(source) != expected_review:
        raise ReviewDecisionError("decision source 与 candidate input identity 不一致")
    manifest_review = _mapping(_mapping(manifest.get("content_identity")).get("review"))
    if (
        manifest_review.get("decision_set_id") != source.get("decision_set_id")
        or manifest_review.get("decision_set_digest") != source.get("decision_set_digest")
        or manifest_review.get("decision_status") != source.get("status")
    ):
        raise ReviewDecisionError("decision source identity 未正确绑定到 manifest")
    catalog = _read_object(normalized_root / "pattern-catalog.json")
    quality = _read_object(normalized_root / "quality-summary.json")
    corpus = _read_object(normalized_root / "examples" / "accepted-corpus.v1.json")
    support_diff = _read_object(support_diff_path)

    dataset_id = _required_text(manifest, "dataset_id", "manifest")
    content_digest = _required_digest(manifest, "content_digest", "manifest")
    stable_patterns = sorted(
        (deepcopy(item) for item in _objects(catalog.get("patterns")) if item.get("support_level") == "stable"),
        key=lambda item: str(item.get("pattern_id") or ""),
    )
    corpus_by_id = {str(item.get("pattern_id")): item for item in _objects(corpus.get("entries"))}
    pattern_changes = {str(item.get("pattern_id")): item for item in _objects(support_diff.get("pattern_changes"))}
    source_targets: list[dict[str, object]] = []
    stable_cards: list[dict[str, Any]] = []
    for pattern in stable_patterns:
        pattern_id = _required_text(pattern, "pattern_id", "stable pattern")
        entry = corpus_by_id.get(pattern_id)
        if entry is None:
            raise ReviewDecisionError(f"Stable pattern 缺少 accepted corpus: {pattern_id}")
        payload = {
            "pattern": _pattern_payload(pattern),
            "accepted_corpus": _corpus_payload(entry),
            "support_change": deepcopy(pattern_changes.get(pattern_id)),
        }
        target = build_target(
            "pattern",
            pattern_id,
            payload,
            actions=("accept", "reject"),
            required=True,
        )
        source_targets.append(target)
        stable_cards.append(
            {
                "pattern_id": pattern_id,
                "target_content_hash": target["target_content_hash"],
                **payload,
            }
        )

    support_payload = {
        "decision_id": quality.get("decision_id"),
        "scope_contract": deepcopy(quality.get("scope_contract")),
        "stable": deepcopy(quality.get("stable")),
        "baseline": support_diff.get("baseline"),
        "current": deepcopy(support_diff.get("current")),
        "transition_counts": deepcopy(support_diff.get("transition_counts")),
        "stable_pattern_downgrades": deepcopy(support_diff.get("stable_pattern_downgrades")),
        "stable_record_downgrades": deepcopy(support_diff.get("stable_record_downgrades")),
    }
    source_targets.append(
        build_target(
            "support",
            f"stable-scope:{quality.get('decision_id')}",
            support_payload,
            actions=("accept", "reject"),
            required=True,
        )
    )

    generator = _mapping(manifest.get("content_identity")).get("input_identity")
    generator_state = _mapping(_mapping(generator).get("generator_state"))
    approval_payload = {
        "dataset_id": dataset_id,
        "content_digest": content_digest,
        "generator_commit": generator_state.get("commit"),
        "generator_dirty": generator_state.get("dirty"),
        "quality_status": quality.get("status"),
        "stable_scope": deepcopy(quality.get("scope_contract")),
        "support_diff_digest": canonical_digest(support_diff),
        "source_decision_set_digest": source["decision_set_digest"],
    }
    approval_targets = [
        build_target(
            "dataset",
            dataset_id,
            approval_payload,
            actions=("accept", "reject"),
            required=True,
        )
    ]

    source_accepted = source.get("status") == "accepted" and corpus.get("review_status") == "accepted"
    phase = "dataset_approval" if source_accepted else "source_review"
    targets = approval_targets if source_accepted else source_targets
    identity = {
        "dataset_id": dataset_id,
        "content_digest": content_digest,
        "source_decision_set_digest": source["decision_set_digest"],
        "source_target_hashes": [target["target_content_hash"] for target in source_targets],
        "approval_target_hashes": [target["target_content_hash"] for target in approval_targets],
        "phase": phase,
    }
    bundle: dict[str, Any] = {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "review_bundle_id": f"review-bundle-v1-{canonical_digest(identity)[:16]}",
        "lifecycle_state": "awaiting_manual_review",
        "phase": phase,
        "dataset_id": dataset_id,
        "content_digest": content_digest,
        "generator_state": deepcopy(generator_state),
        "source_decision_set_id": source["decision_set_id"],
        "source_decision_set_digest": source["decision_set_digest"],
        "source_targets": source_targets,
        "approval_targets": approval_targets,
        "targets": targets,
        "required_target_count": sum(target["required"] is True for target in targets),
        "stable_patterns": stable_cards,
        "stable_summary": {
            "pattern_count": len(stable_patterns),
            "record_count": _mapping(quality.get("stable")).get("record_count"),
            "accepted_corpus_entry_count": len(corpus_by_id),
            "violations": deepcopy(_mapping(quality.get("stable")).get("violation_counts")),
            "support_transition_counts": deepcopy(support_diff.get("transition_counts")),
        },
        "experimental_summary": _experimental_summary(quality),
        "exceptions": _exceptions(quality),
        "manual_checklist": [
            "逐项确认 Stable pattern 的公共含义、角色和中文模板。",
            "确认代表事件的默认与主视图中文文案可用于下游展示。",
            "确认 Stable support diff 没有意外晋级、降级或范围漂移。",
            "查看异常项与 Experimental 摘要；它们不因本次批准自动进入 Stable。",
        ],
    }
    if source_accepted:
        bundle["source_verification"] = verify_source_review(bundle, source, corpus)
    else:
        bundle["source_verification"] = {
            "status": "pending",
            "decision_set_digest": source["decision_set_digest"],
            "accepted_corpus_entry_count": 0,
        }
    return bundle


def write_review_site(bundle: Mapping[str, Any], output_root: Path) -> tuple[Path, Path]:
    """写出 review-bundle.json 与不内嵌大数据的静态审核页。"""

    output_root.mkdir(parents=True, exist_ok=True)
    bundle_path = output_root / "review-bundle.json"
    site_path = output_root / "index.html"
    bundle_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    site_path.write_text(_review_html(), encoding="utf-8")
    return bundle_path, site_path


def _pattern_payload(pattern: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "pattern_id",
        "frame_type",
        "frame_version",
        "asset_kind",
        "record_count",
        "signature",
        "required_roles",
        "optional_roles",
        "zh_CN_template",
        "en_US_template",
        "example_event_names",
        "support_level",
        "warning_counts",
        "provenance_policy",
    )
    return {field: deepcopy(pattern.get(field)) for field in fields}


def _corpus_payload(entry: Mapping[str, Any]) -> dict[str, Any]:
    fields = (
        "pattern_id",
        "frame_type",
        "signature",
        "required_roles",
        "expected_arguments",
        "raw_event_name",
        "zh_cn_default",
        "zh_cn_main",
        "scope_status",
        "decision_id",
    )
    return {field: deepcopy(entry.get(field)) for field in fields}


def _experimental_summary(quality: Mapping[str, Any]) -> dict[str, Any]:
    experimental = _mapping(quality.get("experimental"))
    residual = _mapping(experimental.get("residual_english"))
    top_tokens = _mapping(residual.get("top_tokens_by_category"))
    return {
        "record_count": experimental.get("record_count"),
        "promotion_candidate_pattern_count": experimental.get("promotion_candidate_pattern_count"),
        "promotion_candidate_record_count": experimental.get("promotion_candidate_record_count"),
        "render_status_counts": deepcopy(experimental.get("render_status_counts")),
        "top_unresolved_tokens": deepcopy(_list(top_tokens.get("unresolved"))[:10]),
        "top_warnings": deepcopy(_list(experimental.get("top_warnings"))[:10]),
    }


def _exceptions(quality: Mapping[str, Any]) -> list[dict[str, object]]:
    stable = _mapping(quality.get("stable"))
    corpus = _mapping(quality.get("accepted_corpus"))
    result: list[dict[str, object]] = []
    for name, count in sorted(_mapping(stable.get("violation_counts")).items()):
        result.append({"kind": "stable_violation", "name": name, "count": count})
    for entry in _objects(corpus.get("results")):
        if entry.get("passed") is not True:
            result.append(
                {
                    "kind": "accepted_corpus_failure",
                    "pattern_id": entry.get("pattern_id"),
                    "raw_event_name": entry.get("raw_event_name"),
                    "failures": deepcopy(entry.get("failures")),
                }
            )
    return result


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewDecisionError(f"无法读取产品审核输入: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewDecisionError(f"产品审核输入根必须是 object: {path}")
    return value


def _objects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ReviewDecisionError("产品审核输入必须是 object array")
    return [deepcopy(item) for item in value]


def _list(value: object) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _required_text(document: Mapping[str, Any], field: str, location: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ReviewDecisionError(f"{location}.{field} 必须是非空字符串")
    return value


def _required_digest(document: Mapping[str, Any], field: str, location: str) -> str:
    value = _required_text(document, field, location)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ReviewDecisionError(f"{location}.{field} 必须是小写 SHA-256")
    return value


def _review_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>V1 产品审核</title>
  <style>
    :root { color-scheme: light; font-family: Inter, "Microsoft YaHei", sans-serif; --ink:#172033; --muted:#657087; --line:#d8deea; --brand:#315efb; --ok:#16794b; --bad:#b42318; --panel:#fff; --bg:#f4f6fa; }
    * { box-sizing: border-box; }
    body { margin:0; color:var(--ink); background:var(--bg); }
    header, main { width:min(1180px, calc(100% - 32px)); margin:auto; }
    header { padding:32px 0 18px; }
    h1 { margin:0 0 8px; font-size:28px; }
    h2 { margin:0 0 14px; font-size:19px; }
    p { line-height:1.65; }
    .muted { color:var(--muted); }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:16px; box-shadow:0 6px 24px rgba(26,40,73,.05); }
    .chips { display:flex; flex-wrap:wrap; gap:8px; }
    .chip { background:#edf1f8; border-radius:999px; padding:6px 10px; font-size:13px; }
    .toolbar { display:grid; grid-template-columns:minmax(180px,1fr) 180px auto; gap:10px; align-items:end; }
    label { display:grid; gap:6px; color:var(--muted); font-size:13px; }
    input, select, textarea, button { font:inherit; }
    input, select, textarea { width:100%; border:1px solid var(--line); border-radius:8px; padding:9px 10px; background:#fff; color:var(--ink); }
    textarea { min-height:72px; resize:vertical; }
    button { border:0; border-radius:8px; padding:9px 13px; cursor:pointer; background:#e9edf5; color:var(--ink); }
    a { color:var(--brand); }
    button.primary { background:var(--brand); color:#fff; }
    button.accept.active { background:var(--ok); color:#fff; }
    button.reject.active { background:var(--bad); color:#fff; }
    button:disabled { opacity:.5; cursor:not-allowed; }
    .target { border:1px solid var(--line); border-left:5px solid #9aa6bd; border-radius:12px; padding:16px; margin:12px 0; background:#fff; }
    .target.accepted { border-left-color:var(--ok); }
    .target.rejected { border-left-color:var(--bad); }
    .target.resolved { border-left-color:#6d7a92; background:#f8f9fc; }
    .target-head { display:flex; justify-content:space-between; gap:12px; align-items:start; }
    .target h3 { margin:0; font-size:16px; }
    .meta { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; margin:12px 0; }
    .meta div { background:#f6f8fb; border-radius:8px; padding:10px; overflow-wrap:anywhere; }
    .meta b { display:block; font-size:12px; color:var(--muted); margin-bottom:4px; }
    .actions { display:flex; gap:8px; margin:12px 0; }
    code { overflow-wrap:anywhere; }
    .pager { display:flex; justify-content:space-between; align-items:center; gap:10px; }
    .alert { border-left:4px solid var(--brand); padding:10px 12px; background:#eef3ff; border-radius:8px; }
    .error { border-left-color:var(--bad); background:#fff0ee; }
    @media (max-width:760px) { .toolbar { grid-template-columns:1fr; } header, main { width:min(100% - 20px,1180px); } }
  </style>
</head>
<body>
  <header>
    <h1>V1 产品审核</h1>
    <p class="muted" id="subtitle">正在加载审核数据…</p>
  </header>
  <main>
    <section class="panel" id="summary"></section>
    <section class="panel" id="riskSummary"></section>
    <section class="panel">
      <h2>审核说明</h2>
      <ol id="checklist"></ol>
      <p><a href="review-bundle.json" download>下载候选绑定的精确 Review Bundle JSON</a></p>
      <div class="alert" id="phaseNote"></div>
    </section>
    <section class="panel toolbar">
      <label>筛选模式或事件<input id="search" type="search" placeholder="输入 frame、事件名或模式 ID"></label>
      <label>目标类型<select id="typeFilter"><option value="">全部</option><option value="pattern">Stable pattern</option><option value="support">支持范围</option><option value="dataset">数据集批准</option></select></label>
      <div class="chips" id="progress" aria-live="polite"></div>
    </section>
    <section id="targets"></section>
    <section class="panel pager"><button id="prev">上一页</button><span id="page"></span><button id="next">下一页</button></section>
    <section class="panel">
      <h2>导出审核决定</h2>
      <p class="muted">所有必审项完成后填写审核人。导出的文件只包含决定，不修改候选数据。</p>
      <label>审核人<input id="reviewer" autocomplete="name" placeholder="请输入审核人名称"></label>
      <div class="actions"><button class="primary" id="export" disabled>导出审核决定</button></div>
      <div id="message" aria-live="polite"></div>
    </section>
  </main>
<script>
  const pageSize = 20;
  let bundle;
  let decisions = {};
  let page = 1;

  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const compact = value => esc(JSON.stringify(value ?? null));
  const keyOf = target => `${target.target_type}:${target.target_id}`;
  const storageKey = () => `lol-event-semantics-product-review:${bundle.review_bundle_id}`;

  async function load() {
    try {
      const response = await fetch('review-bundle.json', {cache:'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      bundle = await response.json();
      decisions = JSON.parse(localStorage.getItem(storageKey()) || '{}');
      render();
    } catch (error) {
      document.getElementById('subtitle').textContent = '无法加载审核数据。';
      showMessage('请确认 review-bundle.json 与页面位于同一目录，并通过本地 HTTP 服务访问。', true);
    }
  }

  function render() {
    document.getElementById('subtitle').textContent = `${bundle.dataset_id} · ${bundle.lifecycle_state}`;
    const stable = bundle.stable_summary;
    document.getElementById('summary').innerHTML = `<h2>候选摘要</h2><div class="chips">
      <span class="chip">阶段 ${esc(bundle.phase)}</span><span class="chip">Stable ${esc(stable.pattern_count)} patterns</span>
      <span class="chip">${esc(stable.record_count)} records</span><span class="chip">Corpus ${esc(stable.accepted_corpus_entry_count)}</span>
      <span class="chip">Digest ${esc(bundle.content_digest.slice(0,16))}…</span></div>`;
    const experimental = bundle.experimental_summary;
    const exceptions = bundle.exceptions || [];
    document.getElementById('riskSummary').innerHTML = `<h2>变化与风险摘要</h2>
      <div class="meta"><div><b>Stable support diff</b><code>${compact(stable.support_transition_counts)}</code></div>
      <div><b>Stable violations</b><code>${compact(stable.violations)}</code></div>
      <div><b>Experimental records</b>${esc(experimental.record_count)}</div>
      <div><b>Experimental promotion candidates</b>${esc(experimental.promotion_candidate_pattern_count)} patterns / ${esc(experimental.promotion_candidate_record_count)} records</div>
      <div><b>异常项</b>${esc(exceptions.length)}</div></div>
      <p><b>Experimental 未解析 token：</b> ${experimental.top_unresolved_tokens.length ? experimental.top_unresolved_tokens.map(compact).join('；') : '无'}</p>
      <p><b>Experimental 高频警告：</b> ${experimental.top_warnings.length ? experimental.top_warnings.map(compact).join('；') : '无'}</p>
      <p><b>需人工关注的异常：</b> ${exceptions.length ? exceptions.map(compact).join('；') : '无'}</p>`;
    document.getElementById('checklist').innerHTML = bundle.manual_checklist.map(item => `<li>${esc(item)}</li>`).join('');
    document.getElementById('phaseNote').textContent = bundle.phase === 'source_review'
      ? '本轮确认 Stable 模式、代表文案和支持范围。完成后仍不会批准数据集。'
      : '长期审核决定已应用。本轮决定只批准或拒绝这个最终数据集。';
    renderTargets();
    renderProgress();
  }

  function visibleTargets() {
    const search = document.getElementById('search').value.trim().toLowerCase();
    const type = document.getElementById('typeFilter').value;
    const current = new Set(bundle.targets.map(keyOf));
    const all = bundle.phase === 'source_review' ? bundle.source_targets : [...bundle.source_targets, ...bundle.approval_targets];
    return all.filter(target => {
      if (type && target.target_type !== type) return false;
      const haystack = JSON.stringify(target.payload).toLowerCase() + ' ' + target.target_id.toLowerCase();
      return !search || haystack.includes(search);
    }).map(target => ({...target, current: current.has(keyOf(target))}));
  }

  function renderTargets() {
    const visible = visibleTargets();
    const pages = Math.max(1, Math.ceil(visible.length / pageSize));
    page = Math.min(page, pages);
    const slice = visible.slice((page-1)*pageSize, page*pageSize);
    document.getElementById('targets').innerHTML = slice.map(renderTarget).join('') || '<section class="panel">没有符合筛选条件的审核项。</section>';
    document.getElementById('page').textContent = `第 ${page} / ${pages} 页，共 ${visible.length} 项`;
    document.getElementById('prev').disabled = page <= 1;
    document.getElementById('next').disabled = page >= pages;
  }

  function renderTarget(target) {
    const key = keyOf(target);
    const decision = decisions[key] || {};
    const resolved = !target.current;
    const klass = resolved ? 'resolved' : decision.action === 'accept' ? 'accepted' : decision.action === 'reject' ? 'rejected' : '';
    const payload = target.payload;
    let detail = '';
    if (target.target_type === 'pattern') {
      const pattern = payload.pattern;
      const corpus = payload.accepted_corpus;
      detail = `<div class="meta"><div><b>Frame</b>${esc(pattern.frame_type)}</div><div><b>记录数</b>${esc(pattern.record_count)}</div>
        <div><b>主视图</b>${esc(corpus.zh_cn_main)}</div><div><b>默认视图</b>${esc(corpus.zh_cn_default)}</div>
        <div><b>代表事件</b><code>${esc(corpus.raw_event_name)}</code></div><div><b>模式 ID</b><code>${esc(target.target_id)}</code></div>
        <div><b>Signature</b><code>${compact(pattern.signature)}</code></div><div><b>必需角色</b><code>${compact(pattern.required_roles)}</code></div>
        <div><b>可选角色</b><code>${compact(pattern.optional_roles)}</code></div><div><b>中文模板</b><code>${compact(pattern.zh_CN_template)}</code></div>
        <div><b>证据策略</b><code>${compact(pattern.provenance_policy)}</code></div><div><b>警告计数</b><code>${compact(pattern.warning_counts)}</code></div>
        <div><b>本轮 support change</b><code>${compact(payload.support_change)}</code></div></div>`;
    } else if (target.target_type === 'support') {
      detail = `<div class="meta"><div><b>Stable patterns</b>${esc(payload.stable.pattern_count)}</div><div><b>Stable records</b>${esc(payload.stable.record_count)}</div>
        <div><b>基线</b>${esc(payload.baseline)}</div><div><b>范围匹配</b>${esc(payload.scope_contract.matched)}</div>
        <div><b>Support transitions</b><code>${compact(payload.transition_counts)}</code></div>
        <div><b>Stable 降级</b><code>${compact({patterns:payload.stable_pattern_downgrades,records:payload.stable_record_downgrades})}</code></div></div>`;
    } else {
      detail = `<div class="meta"><div><b>Dataset</b>${esc(payload.dataset_id)}</div><div><b>内容摘要</b><code>${esc(payload.content_digest)}</code></div>
        <div><b>Generator</b><code>${esc(payload.generator_commit)}</code></div><div><b>质量状态</b>${esc(payload.quality_status)}</div></div>`;
    }
    const controls = resolved ? '<p class="muted">已由长期 decision source 接受，本轮只读复核。</p>' : `<div class="actions">
      <button class="accept ${decision.action === 'accept' ? 'active' : ''}" data-review-key="${esc(key)}" data-review-action="accept">接受</button>
      <button class="reject ${decision.action === 'reject' ? 'active' : ''}" data-review-key="${esc(key)}" data-review-action="reject">需要修改</button></div>
      <label>审核备注<textarea data-review-note="${esc(key)}" placeholder="接受时可选；需要修改时请说明问题">${esc(decision.rationale || '')}</textarea></label>`;
    return `<article class="target ${klass}"><div class="target-head"><h3>${esc(target.target_type)} · ${esc(target.target_id)}</h3><span class="chip">${resolved ? '已应用' : target.required ? '必审' : '可选'}</span></div>${detail}${controls}</article>`;
  }

  function setDecision(key, action) {
    const current = decisions[key] || {};
    decisions[key] = {...current, action, reviewed_at:new Date().toISOString()};
    save(); renderTargets(); renderProgress();
  }
  function setNote(key, rationale) { decisions[key] = {...(decisions[key] || {}), rationale}; save(); renderProgress(); }
  function save() { localStorage.setItem(storageKey(), JSON.stringify(decisions)); }

  function renderProgress() {
    const required = bundle.targets.filter(target => target.required);
    const complete = required.filter(target => decisions[keyOf(target)]?.action).length;
    document.getElementById('progress').innerHTML = `<span class="chip">已决定 ${complete} / ${required.length}</span>`;
    document.getElementById('export').disabled = complete !== required.length;
  }

  function canonical(value) {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort(compareOrdinal).map(key => [key, canonical(value[key])]));
    return value;
  }
  function decisionIdentity(decision) {
    return {
      decision_schema_version:decision.decision_schema_version,
      decision_id:decision.decision_id,
      target_type:decision.target_type,
      target_id:decision.target_id,
      target_content_hash:decision.target_content_hash,
      action:decision.action,
      payload:decision.payload
    };
  }
  async function sha256(value) {
    const bytes = new TextEncoder().encode(JSON.stringify(canonical(value)));
    const digest = await crypto.subtle.digest('SHA-256', bytes);
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2,'0')).join('');
  }
  async function exportDecisions() {
    const reviewer = document.getElementById('reviewer').value.trim();
    if (!reviewer) return showMessage('请先填写审核人。', true);
    const required = bundle.targets.filter(target => target.required);
    if (required.some(target => !decisions[keyOf(target)]?.action)) return showMessage('仍有必审项未决定。', true);
    for (const target of required) {
      const state = decisions[keyOf(target)];
      if (state.action === 'reject' && !String(state.rationale || '').trim()) return showMessage('标记“需要修改”的项目必须填写审核备注。', true);
    }
    const exported = [];
    for (const target of bundle.targets) {
      const state = decisions[keyOf(target)];
      if (!state?.action) continue;
      exported.push({
        decision_schema_version:1,
        decision_id:`decision-v1-${target.target_content_hash.slice(0,16)}-${state.action}`,
        target_type:target.target_type,
        target_id:target.target_id,
        target_content_hash:target.target_content_hash,
        action:state.action,
        payload:{},
        rationale:String(state.rationale || (state.action === 'accept' ? '已人工确认。' : '')).trim(),
        reviewer,
        reviewed_at:state.reviewed_at || new Date().toISOString()
      });
    }
    exported.sort((a,b) => compareOrdinal(`${a.target_type}:${a.target_id}:${a.decision_id}`, `${b.target_type}:${b.target_id}:${b.decision_id}`));
    const decisionDocument = {
      schema_version:1,
      review_bundle_id:bundle.review_bundle_id,
      dataset_id:bundle.dataset_id,
      content_digest:bundle.content_digest,
      source_decision_set_digest:bundle.source_decision_set_digest,
      decisions:exported,
      decision_set_digest:await sha256(exported.map(decisionIdentity)),
      exported_at:new Date().toISOString()
    };
    const blob = new Blob([JSON.stringify(decisionDocument,null,2)+'\n'], {type:'application/json'});
    const link = window.document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.href = url; link.download = `${bundle.dataset_id}-review-decisions.json`; link.click();
    URL.revokeObjectURL(url);
    showMessage('审核决定已导出到下载目录。', false);
  }
  function compareOrdinal(left, right) { return left < right ? -1 : left > right ? 1 : 0; }
  function showMessage(message, error) { const node=document.getElementById('message'); node.className=`alert ${error?'error':''}`; node.textContent=message; }
  document.getElementById('targets').addEventListener('click', event => {
    const button = event.target.closest('button[data-review-key]');
    if (button) setDecision(button.dataset.reviewKey, button.dataset.reviewAction);
  });
  document.getElementById('targets').addEventListener('input', event => {
    const note = event.target.closest('textarea[data-review-note]');
    if (note) setNote(note.dataset.reviewNote, note.value);
  });
  document.getElementById('search').addEventListener('input', () => { page=1; renderTargets(); });
  document.getElementById('typeFilter').addEventListener('change', () => { page=1; renderTargets(); });
  document.getElementById('prev').addEventListener('click', () => { page--; renderTargets(); });
  document.getElementById('next').addEventListener('click', () => { page++; renderTargets(); });
  document.getElementById('export').addEventListener('click', exportDecisions);
  load();
</script>
</body>
</html>
"""
