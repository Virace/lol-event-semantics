"use strict";

const schemaVersion = 3;
const numberFormatter = new Intl.NumberFormat("zh-CN");
const cache = new Map();
const state = { index: null, document: null, status: "all", type: "champion", key: "", query: "", page: 1, pageSize: 100, sequence: 0 };
const reasonLabels = { unresolved_entity: "实体归属待确认", incomplete_render: "中文解释待完善", record_warning: "记录存在警告", validator_issue: "语义校验问题" };
const typeLabels = { vo: "语音", sfx: "音效", control: "控制事件", music: "音乐", unknown: "其他" };
const elements = Object.fromEntries([
  "appShell", "semanticStatus", "entityType", "statusExplanation", "datasetVersion", "datasetSummaryInline", "totalCount", "feedbackCount",
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
  options(elements.assetFilter, facet((event) => event.asset_kind, (value) => typeLabels[value] || value));
  options(elements.supportFilter, facet((event) => event.support_level));
  options(elements.modeFilter, facet((event) => event.mode.key, (value) => events.find((event) => event.mode.key === value).mode.label));
  options(elements.mapFilter, facet((event) => event.map.key, (value) => events.find((event) => event.map.key === value).map.label));
  options(elements.skinFilter, facet((event) => String(event.skin_number ?? "none"), skinLabel));
  const reasons = Array.from(new Set(events.flatMap((event) => event.feedback_reasons))).sort();
  options(elements.issueFilter, [["all", "全部原因"], ...reasons.map((value) => [value, reasonLabels[value] || value])]);
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
  return !query || [event.raw_event_name, event.label, event.zh_cn_short, event.zh_cn_detail, event.record_id, event.event_id, ...event.issues.map((issue) => `${issue.code} ${issue.message}`)].join(" ").toLocaleLowerCase().includes(query);
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
  for (const [label, value] of [["来源实体", `${event.entity.kind} · ${event.entity.alias}`], ["归属依据", attribution.basis], ["可信程度", attribution.confidence], ["人工审核", event.review_status], ["渲染状态", event.render_status], ["地图上下文", event.map.label], ["事件标识", event.event_id], ["本版本记录标识", event.record_id]]) {
    if (value) { metadata.append(node("dt", label), node("dd", String(value))); }
  }
  detail.append(metadata);
  for (const evidence of attribution.evidence || []) detail.append(node("p", `${evidence.source}: ${evidence.value}`, "event-detail-label"));
  if (attribution.reason) detail.append(node("p", attribution.reason, "event-detail-label"));
  for (const warning of event.warnings || []) detail.append(node("p", `记录警告: ${typeof warning === "string" ? warning : JSON.stringify(warning)}`, "event-detail-label"));
  if (event.issues.length) {
    const list = node("ul", undefined, "review-issue-list");
    for (const issue of event.issues) list.append(node("li", `${issue.code}: ${issue.message}`));
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
  const support = node("td"); support.dataset.label = "类型 / 支持度";
  support.append(badge(typeLabels[event.asset_kind] || event.asset_kind, "kind", event.asset_kind), node("br"), badge(event.support_level, "support", event.support_level));
  const context = node("td"); context.dataset.label = "上下文";
  context.append(node("span", event.mode.label));
  if (event.skin_number !== null) context.append(node("span", skinLabel(String(event.skin_number)), "event-detail-label"));
  const status = node("td"); status.dataset.label = "语义状态";
  status.append(badge(event.semantic_status === "determined" ? "已确定" : "待反馈", "status", event.semantic_status));
  for (const reason of event.feedback_reasons) status.append(node("span", reasonLabels[reason] || reason, "event-detail-label"));
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
  elements.statusExplanation.textContent = state.status === "all" ? "支持度、语义状态与人工审核分别展示；已生成中文不等于 Stable。" : state.index.status_policy[state.status];
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
    elements.loadingState.textContent = error.message;
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
    elements.datasetVersion.textContent = state.index.dataset.base_game_version;
    elements.datasetSummaryInline.textContent = `${count(state.index.counts.source_records)} 条`;
    elements.totalCount.textContent = count(state.index.counts.source_records);
    elements.feedbackCount.textContent = count(state.index.counts.feedback_records);
    wireInteractions();
    await loadRoute();
  } catch (error) { elements.loadingState.textContent = error.message; elements.appShell.setAttribute("aria-busy", "false"); }
}
boot();
