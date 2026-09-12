"use strict";

const schemaVersion = 3;
const numberFormatter = new Intl.NumberFormat("zh-CN");
const cache = new Map();
const state = { index: null, document: null, status: "all", type: "champion", key: "", query: "", page: 1, pageSize: 100, sequence: 0 };
const reasonLabels = { unresolved_entity: "实体归属待确认", incomplete_render: "中文解释待完善", record_warning: "记录存在警告", validator_issue: "语义校验问题" };
const typeLabels = { vo: "语音", sfx: "音效", control: "控制事件", music: "音乐", unknown: "其他" };
const supportLabels = { stable: "稳定支持", experimental: "试验支持", unsupported: "暂不支持" };
const renderLabels = { rendered: "已生成", fallback: "回退显示", missing: "缺少解释", not_applicable: "不适用" };
const reviewLabels = { accepted: "已接受", pending: "待审阅", rejected: "已拒绝", deferred: "暂缓" };
const datasetLabels = { candidate: "待审候选", approved_local: "本地已批准", checks_failed: "检查未通过" };
const entityLabels = { champion: "英雄", item: "装备", map: "地图", mode_unit: "模式单位", mode_boss: "模式首领", mode_system: "模式实体", system: "公共系统", unknown: "未知实体" };
const confidenceLabels = { high: "高", medium: "中", low: "低", failed: "判定失败", unknown: "未确定", None: "未提供" };
const basisLabels = {
  unresolved: "归属待确认", source_asset: "来源资产", reference_exact: "参考数据精确匹配", reference_item_id: "装备编号参考",
  explicit_map: "显式地图证据", public_system: "已确认的公共系统", reference_alias_suffix: "基础英雄别名对应",
};
const sourceLabels = {
  source_asset: "来源资产", reference_champion_alias: "英雄参考别名", reviewed_entity_rule: "已审阅实体规则",
  raw_owner_numeric_prefix: "原始归属编号", raw_owner_item_prefix: "原始装备编号", reference_item_id: "装备编号参考", reference_map_id: "地图编号参考",
  entity_attribution_override: "人工归属规则", reference_skin_owner: "皮肤所属英雄参考", candidate_owner_affix: "候选归属词缀",
  semantic_namespace: "语义命名空间", reviewed_system_owner: "已审阅系统归属",
};
const attributionReasonLabels = {
  conflicting_champion_sources: "多个英雄来源相互冲突。", source_reference_conflict: "来源资产与参考实体冲突。",
  conflicting_reference_entities: "参考证据指向多个不同实体。", unverified_numeric_owner: "该编号尚无可核验的实体映射。",
  unverified_owner: "实体归属尚未核验。", unverified_owner_affix: "归属词缀只有候选线索，尚未确认。",
  ambiguous_owner_affix: "归属词缀可匹配多个实体。", ambiguous_map_sources: "来源包含多个地图，无法确定归属。",
  insufficient_entity_evidence: "现有证据不足以确定实体归属。",
};
const issueLabels = {
  translation_missing: "缺少中文解释", translation_missing_for_supported_frame: "已支持的语义框架缺少中文解释",
  translation_frame_required_role_missing: "语义框架缺少必需角色", duplicate_raw_event_name: "同一事件原名出现多条记录",
  suspicious_champion_alias: "英雄归属名称待核验", unknown_entity_output: "实体归属尚未确定",
  low_confidence_output: "语义判定可信程度不足", low_trust_rule_in_output: "记录包含低可信规则",
  technical_token_in_display: "简要解释残留技术片段", technical_token_in_detail: "完整解释残留技术片段",
  display_drops_parsed_slot: "完整解释丢失已解析语义", unknown_count_in_output: "记录包含未知片段",
  diagnostics_in_output: "记录仍有诊断问题", translation_locale_label_missing: "解释参数缺少中文名称",
  translation_label_fallback: "解释参数使用回退名称", translation_fallback_reason_missing: "缺少名称回退原因",
  translation_argument_low_confidence: "解释参数可信程度不足", translation_argument_provenance_missing: "解释参数缺少来源证据",
  translation_render_context_too_sparse: "生成中文所需上下文不足", action_phase_translation_missing: "动作阶段缺少中文解释",
  sfx_effect_translation_missing: "音效效果缺少中文解释", spell_component_modifier_translation_missing: "技能部件修饰缺少中文解释",
  spell_phase_translation_missing: "技能阶段缺少中文解释", spell_sequence_translation_missing: "技能序列缺少中文解释",
  spell_stage_not_rendered: "技能阶段未体现于解释", spell_sequence_stage_not_rendered: "技能序列阶段未体现于解释",
};
const elements = Object.fromEntries([
  "appShell", "semanticStatus", "entityType", "statusExplanation", "datasetVersion", "datasetStatus", "datasetSummaryInline", "totalCount", "feedbackCount",
  "browseSearch", "browseHeading", "browseResultCount", "browseList", "loadingState", "entityView", "entityPortrait", "entityName", "entityAlias", "entityDescription",
  "entityEventCount", "entityStableCount", "entityFeedbackCount", "skinPreview", "splashImage", "skinHeading", "skinPreviewSelect",
  "eventFilters", "eventSearch", "assetFilter", "supportFilter", "modeFilter", "mapFilter", "skinFilter", "issueFilter", "resetFilters",
  "resultsSummary", "eventTableBody", "emptyState", "previousPage", "nextPage", "pageStatus", "pageSize", "copyPageLink", "liveRegion",
].map((id) => [id, document.getElementById(id)]));

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined) result.textContent = text;
  if (className) result.className = className;
  return result;
}
function count(value) { return numberFormatter.format(value || 0); }
function term(labels, value, title) { return labels[value] || (value ? `未识别的${title}（原始值：${value}）` : "未提供"); }
function issueLabel(code) { return term(issueLabels, code, "问题类型"); }
function issueMessage(issue) {
  const message = issue.message || "";
  const fixed = {
    "supported frame 缺少 translation。": "已支持的语义框架尚未生成中文解释。",
    "argument 使用 fallback label。": "解释参数采用回退名称，仍需核验。",
    "最终产物仍包含 low_trust_rule。": "记录仍使用低可信规则。",
    "最终产物仍包含 unknown 实体分片。": "该记录的实体归属尚未确定。",
    "记录仍包含 diagnostics。": "记录仍有待处理的诊断问题。",
    "argument 缺少 zh_CN label。": "解释参数缺少中文名称。",
    "fallback argument 缺少 fallback_reason。": "采用回退名称的解释参数缺少回退原因。",
    "argument 缺少 provenance。": "解释参数缺少来源证据。",
  };
  if (fixed[message]) return fixed[message];
  let match = message.match(/^argument confidence=(\w+)。$/);
  if (match) return `解释参数可信程度：${term(confidenceLabels, match[1], "可信程度")}。`;
  match = message.match(/^最终产物仍包含 (\w+) 置信度。$/);
  if (match) return `语义判定可信程度：${term(confidenceLabels, match[1], "可信程度")}。`;
  match = message.match(/^记录仍包含 (\d+) 个 unknown fragment。$/);
  if (match) return `记录仍包含 ${match[1]} 个未知片段。`;
  match = message.match(/^(?:短展示|详细展示)疑似残留技术 token：(.*)$/);
  if (match) return `待核验的解释原文：${match[1]}`;
  match = message.match(/^champion alias 看起来不是最终英雄实体：(.*)。$/);
  if (match) return `待核验的英雄归属原值：${match[1]}。`;
  match = message.match(/^展示 detail 丢弃已解析语义槽位[：。](.*)$/);
  if (match) return match[1] ? `完整解释丢失已解析语义。槽位原始证据：${match[1]}` : "完整解释丢失已解析语义。";
  match = message.match(/^raw_event_name 已在 (.*) 出现。$/);
  if (match) return `同一事件原名还出现在以下记录位置：${match[1]}。`;
  match = message.match(/^zh_CN\.(default|main) 渲染结果为空。$/);
  if (match) return `${match[1] === "default" ? "完整" : "简要"}中文解释的生成结果为空。`;
  return message ? `原始诊断：${message}` : "";
}
function warningLabel(value) {
  if (typeof value !== "string") return `未识别的警告（原始证据：${JSON.stringify(value)}）`;
  if (value.startsWith("renderer_optional_missing_role:")) return `中文生成缺少可选角色（角色原值：${value.split(":").slice(1).join(":")}）`;
  return issueLabel(value);
}
function statusCount(entry) { return state.status === "all" ? entry.stats.events : entry.stats.statuses[state.status] || 0; }
function categoryLabel() { return state.index.categories.find((category) => category.key === state.type)?.label || "实体"; }
function announce(message) { elements.liveRegion.textContent = message; }
function options(select, values, selected = "all") {
  select.replaceChildren(...values.map(([value, label]) => { const option = node("option", label); option.value = value; return option; }));
  select.value = values.some(([value]) => value === selected) ? selected : values[0]?.[0] || "";
}
function setHash() {
  const params = new URLSearchParams({ status: state.status, type: state.type, entity: state.key });
  if (elements.eventSearch.value) params.set("q", elements.eventSearch.value);
  for (const [name, id] of Object.entries({ asset: "assetFilter", support: "supportFilter", mode: "modeFilter", map: "mapFilter", skin: "skinFilter", issue: "issueFilter" })) {
    if (elements[id].value && elements[id].value !== "all") params.set(name, elements[id].value);
  }
  window.history.replaceState(null, "", `#${params}`);
}
async function fetchJson(path, entryPoint = false) {
  const url = new URL(path, window.location.href);
  if (!entryPoint) url.searchParams.set("v", state.index.dataset.demo_id);
  const response = await fetch(url, { cache: entryPoint ? "no-cache" : "default" });
  if (!response.ok) throw new Error(`数据读取失败 (${response.status})`);
  const payload = await response.json();
  if (payload.schema_version !== schemaVersion) throw new Error("站点资源与数据版本不一致，请刷新页面。");
  if (!entryPoint) assertDatasetIdentity(payload);
  return payload;
}
function assertDatasetIdentity(payload) {
  for (const key of ["dataset_id", "content_digest", "demo_id"]) {
    if (payload.dataset?.[key] !== state.index.dataset[key]) throw new Error("实体分片与当前数据集不一致，请刷新页面。");
  }
}
function renderNavigation() {
  elements.browseHeading.textContent = categoryLabel();
  const query = elements.browseSearch.value.trim().toLocaleLowerCase();
  const entries = state.index.entities.filter((entry) => entry.kind === state.type && statusCount(entry) > 0 &&
    [entry.alias, entry.label, ...(entry.search_terms || [])].join(" ").toLocaleLowerCase().includes(query));
  elements.browseResultCount.textContent = count(entries.length);
  elements.browseList.replaceChildren(...entries.map((entry) => {
    const link = node("a", undefined, "champion-link");
    const params = new URLSearchParams({ status: state.status, type: state.type, entity: entry.key });
    link.href = `#${params}`;
    if (entry.key === state.key) link.setAttribute("aria-current", "page");
    const names = node("span", undefined, "champion-list-name");
    names.append(node("strong", entry.label), node("span", entry.alias, "entity-list-alias"));
    link.append(names, node("span", count(statusCount(entry)), "champion-list-count"));
    return link;
  }));
  if (!entries.length) elements.browseList.append(node("p", "当前分类与状态下没有匹配实体。", "status-explanation"));
}
function populateFilters() {
  const events = state.document.events;
  const facet = (getter, label = (value) => value) => [["all", "全部"], ...Array.from(new Set(events.map(getter))).sort().map((value) => [value, label(value)])];
  options(elements.assetFilter, facet((event) => event.asset_kind, (value) => term(typeLabels, value, "事件类型")));
  options(elements.supportFilter, facet((event) => event.support_level, (value) => term(supportLabels, value, "支持级别")));
  options(elements.modeFilter, facet((event) => event.mode.key, (value) => events.find((event) => event.mode.key === value).mode.label));
  options(elements.mapFilter, facet((event) => event.map.key, (value) => events.find((event) => event.map.key === value).map.label));
  options(elements.skinFilter, facet((event) => String(event.skin_number ?? "none"), skinLabel));
  const reasons = Array.from(new Set(events.flatMap((event) => event.feedback_reasons))).sort();
  options(elements.issueFilter, [["all", "全部原因"], ...reasons.map((value) => [value, term(reasonLabels, value, "反馈原因")])]);
}
function skinLabel(number) {
  if (number === "none") return "未指定皮肤";
  return state.document.skins.find((skin) => String(skin.number) === number)?.zh_name || `皮肤 ${number}`;
}
function showSkin() {
  const skin = state.document?.skins.find((value) => String(value.number) === elements.skinPreviewSelect.value);
  if (!skin) return;
  elements.splashImage.src = skin.splash_url;
  elements.splashImage.alt = skin.zh_name;
  elements.skinHeading.textContent = skin.zh_name;
}
function renderHeader() {
  const profile = state.document.entity;
  const stats = state.document.stats;
  elements.entityName.textContent = profile.label;
  elements.entityAlias.textContent = profile.alias;
  elements.entityDescription.textContent = [categoryLabel(), profile.title, profile.en_name].filter(Boolean).join(" · ");
  elements.entityEventCount.textContent = count(stats.events);
  elements.entityStableCount.textContent = count(stats.support.stable);
  elements.entityFeedbackCount.textContent = count(stats.statuses.feedback);
  elements.entityPortrait.hidden = !profile.portrait_url;
  if (profile.portrait_url) elements.entityPortrait.src = profile.portrait_url;
  elements.skinPreview.hidden = !state.document.skins.length;
  elements.skinFilter.closest("label").hidden = profile.kind !== "champion";
  elements.skinPreview.open = false;
  if (state.document.skins.length) options(elements.skinPreviewSelect, state.document.skins.map((skin) => [String(skin.number), skin.zh_name]), "0");
}
function matches(event) {
  if (state.status !== "all" && event.semantic_status !== state.status) return false;
  for (const [id, value] of [["assetFilter", event.asset_kind], ["supportFilter", event.support_level], ["modeFilter", event.mode.key], ["mapFilter", event.map.key], ["skinFilter", String(event.skin_number ?? "none")]]) {
    if (elements[id].value !== "all" && elements[id].value !== value) return false;
  }
  if (elements.issueFilter.value !== "all" && !event.feedback_reasons.includes(elements.issueFilter.value)) return false;
  const query = elements.eventSearch.value.trim().toLocaleLowerCase();
  return !query || [event.raw_event_name, event.label, event.zh_cn_short, event.zh_cn_detail, event.record_id, event.event_id, ...event.issues.map((issue) => `${issue.code} ${issue.message} ${issueLabel(issue.code)} ${issueMessage(issue)}`)].join(" ").toLocaleLowerCase().includes(query);
}
function badge(text, name, value) {
  const element = node("span", text, "badge");
  element.dataset[name] = value;
  return element;
}
function eventDetails(event) {
  const detail = node("details", undefined, "event-evidence");
  detail.append(node("summary", "查看完整解释与依据"));
  if (event.full_label && event.full_label !== event.zh_cn_detail) detail.append(node("p", event.full_label));
  if (event.zh_cn_detail) detail.append(node("p", event.zh_cn_detail));
  const metadata = node("dl", undefined, "review-detail-meta");
  const attribution = event.entity.attribution || {};
  for (const [label, value] of [["来源实体", `${term(entityLabels, event.entity.kind, "实体分类")} · ${event.entity.alias}`], ["归属依据", term(basisLabels, attribution.basis, "归属依据")], ["可信程度", term(confidenceLabels, attribution.confidence, "可信程度")], ["模式审阅", term(reviewLabels, event.review_status, "模式审阅状态")], ["中文生成", term(renderLabels, event.render_status, "中文生成状态")], ["地图上下文", event.map.label], ["事件标识", event.event_id], ["本版本记录标识", event.record_id]]) {
    if (value) { metadata.append(node("dt", label), node("dd", String(value))); }
  }
  detail.append(metadata);
  if (event.entity.candidate) {
    const candidate = event.entity.candidate;
    detail.append(node("p", `候选归属（尚未确认）：${term(entityLabels, candidate.kind, "实体分类")} · ${candidate.id ?? candidate.alias ?? "未提供编号"}`, "event-detail-label"));
  }
  for (const evidence of attribution.evidence || []) {
    const value = evidence.source === "source_asset" ? String(evidence.value).replace(/^champions:/, "英雄编号：").replace(/^maps:/, "地图编号：") : evidence.value;
    detail.append(node("p", `${term(sourceLabels, evidence.source, "证据来源")}：${value}`, "event-detail-label"));
  }
  if (attribution.reason) detail.append(node("p", term(attributionReasonLabels, attribution.reason, "归属原因"), "event-detail-label"));
  for (const warning of event.warnings || []) detail.append(node("p", `记录警告：${warningLabel(warning)}`, "event-detail-label"));
  if (event.issues.length) {
    const list = node("ul", undefined, "review-issue-list");
    for (const issue of event.issues) {
      const item = node("li");
      item.append(node("strong", issueLabel(issue.code)));
      const message = issueMessage(issue);
      if (message) item.append(node("span", message, "event-detail-label"));
      list.append(item);
    }
    detail.append(list);
  }
  return detail;
}
function renderRow(event) {
  const row = node("tr");
  const label = node("td"); label.dataset.label = "事件 / 解释";
  const raw = node("code", event.raw_event_name, "raw-event-name"); raw.setAttribute("translate", "no");
  label.append(raw, node("div", event.label || "中文解释待完善", "event-short-label"));
  if (event.label_basis === "preview") label.append(node("span", "预览解释", "event-detail-label"));
  label.append(eventDetails(event));
  const support = node("td"); support.dataset.label = "类型 / 支持级别";
  support.append(badge(term(typeLabels, event.asset_kind, "事件类型"), "kind", event.asset_kind), node("br"), badge(term(supportLabels, event.support_level, "支持级别"), "support", event.support_level));
  const context = node("td"); context.dataset.label = "上下文";
  context.append(node("span", event.mode.label));
  if (event.skin_number !== null) context.append(node("span", skinLabel(String(event.skin_number)), "event-detail-label"));
  const status = node("td"); status.dataset.label = "语义状态";
  status.append(badge(event.semantic_status === "determined" ? "已确定" : "待反馈", "status", event.semantic_status));
  for (const reason of event.feedback_reasons) status.append(node("span", term(reasonLabels, reason, "反馈原因"), "event-detail-label"));
  row.append(label, support, context, status);
  return row;
}
function renderEvents(updateUrl = true) {
  if (!state.document) return;
  const records = state.document.events.filter(matches);
  const pages = Math.max(1, Math.ceil(records.length / state.pageSize));
  state.page = Math.min(state.page, pages);
  const offset = (state.page - 1) * state.pageSize;
  elements.eventTableBody.replaceChildren(...records.slice(offset, offset + state.pageSize).map(renderRow));
  elements.emptyState.hidden = records.length > 0;
  elements.resultsSummary.textContent = `${count(records.length)} / ${count(state.document.events.length)} 条事件`;
  elements.pageStatus.textContent = `第 ${state.page} / ${pages} 页`;
  elements.previousPage.disabled = state.page <= 1;
  elements.nextPage.disabled = state.page >= pages;
  if (updateUrl) setHash();
}
async function loadRoute() {
  const sequence = ++state.sequence;
  const params = new URLSearchParams(window.location.hash.slice(1));
  const requestedStatus = params.get("status");
  state.status = ["all", "determined", "feedback"].includes(requestedStatus) ? requestedStatus : requestedStatus === "review" ? "feedback" : "all";
  const requestedType = params.get("type");
  state.type = state.index.categories.some((category) => category.key === requestedType) ? requestedType : state.index.categories[0]?.key || "champion";
  elements.semanticStatus.value = state.status;
  elements.entityType.value = state.type;
  elements.statusExplanation.textContent = state.status === "all" ? "支持级别、语义状态、模式审阅与数据集批准分别展示；已生成中文不代表达到稳定支持。" : state.index.status_policy[state.status];
  const candidates = state.index.entities.filter((entry) => entry.kind === state.type && statusCount(entry) > 0);
  const entry = candidates.find((value) => value.key === params.get("entity")) || candidates[0];
  state.key = entry?.key || "";
  renderNavigation();
  elements.entityView.hidden = true;
  elements.loadingState.hidden = false;
  elements.loadingState.textContent = entry ? "正在读取实体事件…" : "当前分类与语义状态下没有事件。";
  elements.appShell.setAttribute("aria-busy", String(Boolean(entry)));
  if (!entry) { state.document = null; return; }
  try {
    let payload = cache.get(entry.key);
    if (!payload) {
      payload = await fetchJson(entry.data_path);
      cache.set(entry.key, payload);
      if (cache.size > 6) cache.delete(cache.keys().next().value);
    }
    if (sequence !== state.sequence) return;
    state.document = payload;
    state.page = 1;
    renderHeader();
    populateFilters();
    elements.eventSearch.value = params.get("q") || "";
    for (const [name, id] of Object.entries({ asset: "assetFilter", support: "supportFilter", mode: "modeFilter", map: "mapFilter", skin: "skinFilter", issue: "issueFilter" })) {
      const value = params.get(name);
      if (value && Array.from(elements[id].options).some((option) => option.value === value)) elements[id].value = value;
    }
    renderEvents();
    elements.loadingState.hidden = true;
    elements.entityView.hidden = false;
    elements.appShell.setAttribute("aria-busy", "false");
    document.title = `${entry.label} · 峡谷事件语义图鉴`;
    announce(`已载入${entry.label}，共 ${count(payload.events.length)} 条事件`);
  } catch (error) {
    if (sequence !== state.sequence) return;
    elements.loadingState.textContent = `实体事件读取失败，请刷新页面后重试。${error instanceof TypeError || error instanceof SyntaxError ? "请检查网络连接及站点数据是否完整。" : error.message}`;
    elements.appShell.setAttribute("aria-busy", "false");
  }
}
function wireInteractions() {
  for (const id of ["semanticStatus", "entityType"]) elements[id].addEventListener("change", () => {
    const params = new URLSearchParams(window.location.hash.slice(1));
    params.set("status", elements.semanticStatus.value); params.set("type", elements.entityType.value);
    window.location.hash = params.toString();
  });
  elements.browseSearch.addEventListener("input", renderNavigation);
  elements.eventFilters.addEventListener("submit", (event) => { event.preventDefault(); state.page = 1; renderEvents(); });
  elements.eventSearch.addEventListener("input", () => { state.page = 1; renderEvents(); });
  for (const id of ["assetFilter", "supportFilter", "modeFilter", "mapFilter", "skinFilter", "issueFilter"]) elements[id].addEventListener("change", () => { state.page = 1; renderEvents(); });
  elements.resetFilters.addEventListener("click", () => { populateFilters(); elements.eventSearch.value = ""; state.page = 1; renderEvents(); });
  elements.previousPage.addEventListener("click", () => { state.page -= 1; renderEvents(); });
  elements.nextPage.addEventListener("click", () => { state.page += 1; renderEvents(); });
  elements.pageSize.addEventListener("change", () => { state.pageSize = Number(elements.pageSize.value); state.page = 1; renderEvents(); });
  elements.skinPreviewSelect.addEventListener("change", showSkin);
  elements.skinPreview.addEventListener("toggle", () => { if (elements.skinPreview.open) showSkin(); });
  elements.entityPortrait.addEventListener("error", () => { elements.entityPortrait.hidden = true; });
  elements.splashImage.addEventListener("error", () => { elements.skinHeading.textContent = "原画暂时无法载入，事件浏览不受影响"; });
  elements.copyPageLink.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(window.location.href); announce("页面链接已复制"); }
    catch { announce("无法自动复制，请复制浏览器地址栏中的链接。"); }
  });
  window.addEventListener("hashchange", loadRoute);
}
async function boot() {
  try {
    state.index = await fetchJson("data/index.json", true);
    options(elements.entityType, state.index.categories.map((category) => [category.key, `${category.label} · ${count(category.event_count)}`]), "champion");
    elements.datasetVersion.textContent = state.index.dataset.base_game_version === "unknown" ? "未提供版本" : state.index.dataset.base_game_version;
    elements.datasetStatus.textContent = term(datasetLabels, state.index.dataset.status, "数据集状态");
    elements.datasetSummaryInline.textContent = `${count(state.index.counts.source_records)} 条`;
    elements.totalCount.textContent = count(state.index.counts.source_records);
    elements.feedbackCount.textContent = count(state.index.counts.feedback_records);
    wireInteractions();
    await loadRoute();
  } catch (error) { elements.loadingState.textContent = `站点数据读取失败，请刷新页面后重试。${error instanceof TypeError || error instanceof SyntaxError ? "请检查网络连接及站点数据是否完整。" : error.message}`; elements.appShell.setAttribute("aria-busy", "false"); }
}
boot();
