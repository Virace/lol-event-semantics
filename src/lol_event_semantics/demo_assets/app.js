"use strict";

const numberFormatter = new Intl.NumberFormat("zh-CN");
const championCache = new Map();
const state = {
  index: null,
  champion: null,
  championQuery: "",
  eventQuery: "",
  assetKind: "all",
  supportLevel: "all",
  modeKey: "all",
  skinNumber: "all",
  page: 1,
  pageSize: 100,
  previewSkinIndex: 0,
  loadSequence: 0,
};

const elements = Object.fromEntries(
  [
    "appShell",
    "loadingBanner",
    "championSearch",
    "datasetVersion",
    "championCount",
    "eventCount",
    "unassignedCount",
    "datasetId",
    "championResultCount",
    "championList",
    "championPortrait",
    "championAlias",
    "championName",
    "championTitle",
    "championEventCount",
    "championStableCount",
    "championSkinCount",
    "championRenderedRatio",
    "modeCards",
    "splashImage",
    "skinToken",
    "skinHeading",
    "skinEnglishName",
    "previousSkin",
    "nextSkin",
    "skinCounter",
    "skinPreviewSelect",
    "eventFilters",
    "eventSearch",
    "assetFilter",
    "supportFilter",
    "modeFilter",
    "skinFilter",
    "resetFilters",
    "resultsSummary",
    "eventTableBody",
    "emptyState",
    "previousPage",
    "nextPage",
    "pageStatus",
    "pageSize",
    "liveRegion",
  ].map((id) => [id, document.getElementById(id)]),
);

function createElement(tagName, options = {}) {
  const element = document.createElement(tagName);
  if (options.className) element.className = options.className;
  if (options.text !== undefined) element.textContent = options.text;
  if (options.type) element.type = options.type;
  return element;
}

function formatCount(value) {
  return numberFormatter.format(Number(value || 0));
}

function announce(message) {
  elements.liveRegion.textContent = "";
  window.setTimeout(() => {
    elements.liveRegion.textContent = message;
  }, 20);
}

function setLoading(message, error = false) {
  elements.loadingBanner.hidden = false;
  elements.loadingBanner.dataset.state = error ? "error" : "loading";
  elements.loadingBanner.textContent = message;
}

function clearLoading() {
  elements.loadingBanner.hidden = true;
  elements.loadingBanner.textContent = "";
  elements.appShell.setAttribute("aria-busy", "false");
}

function championHash(alias) {
  return `#champion=${encodeURIComponent(alias)}`;
}

function aliasFromHash() {
  const raw = window.location.hash.replace(/^#/, "");
  const params = new URLSearchParams(raw);
  return params.get("champion");
}

function updateDatasetSummary() {
  const index = state.index;
  elements.datasetVersion.textContent = index.dataset.base_game_version;
  elements.championCount.textContent = formatCount(index.counts.champions);
  elements.eventCount.textContent = formatCount(index.counts.champion_events);
  elements.unassignedCount.textContent = formatCount(index.counts.unassigned_records);
  elements.datasetId.textContent = index.dataset.dataset_id;
  elements.datasetId.title = index.dataset.content_digest;
}

function matchesChampionSearch(champion, query) {
  if (!query) return true;
  const haystack = champion.search_terms.join(" ").toLocaleLowerCase("zh-CN");
  return haystack.includes(query.toLocaleLowerCase("zh-CN"));
}

function portraitFallback(champion) {
  const fallback = createElement("span", {
    className: "champion-avatar-fallback",
    text: champion.zh_name.slice(0, 1),
  });
  fallback.setAttribute("aria-hidden", "true");
  return fallback;
}

function renderChampionList() {
  const champions = state.index.champions.filter((champion) =>
    matchesChampionSearch(champion, state.championQuery.trim()),
  );
  const fragment = document.createDocumentFragment();
  for (const champion of champions) {
    const button = createElement("button", { className: "champion-link", type: "button" });
    button.dataset.alias = champion.alias;
    button.setAttribute("aria-current", String(state.champion?.champion.alias === champion.alias));

    const image = createElement("img");
    image.src = champion.portrait_url;
    image.alt = "";
    image.width = 34;
    image.height = 34;
    image.loading = "lazy";
    image.addEventListener("error", () => image.replaceWith(portraitFallback(champion)), { once: true });

    const copy = createElement("span", { className: "champion-link-copy" });
    copy.append(
      createElement("strong", { text: champion.zh_name }),
      createElement("span", { text: champion.en_name }),
    );
    const count = createElement("span", {
      className: "champion-link-count",
      text: formatCount(champion.event_count),
    });
    button.append(image, copy, count);
    button.addEventListener("click", () => {
      if (window.location.hash === championHash(champion.alias)) {
        loadChampion(champion.alias);
      } else {
        window.location.hash = championHash(champion.alias);
      }
    });
    fragment.append(button);
  }
  elements.championList.replaceChildren(fragment);
  elements.championResultCount.textContent = `${formatCount(champions.length)} 位`;
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

async function loadChampion(alias) {
  const championIndex = state.index.champions.find((champion) => champion.alias === alias);
  if (!championIndex) return;
  const sequence = ++state.loadSequence;
  setLoading(`正在读取 ${championIndex.zh_name} 的事件…`);
  try {
    let document = championCache.get(alias);
    if (!document) {
      document = await fetchJson(championIndex.data_path);
      championCache.set(alias, document);
    }
    if (sequence !== state.loadSequence) return;
    state.champion = document;
    state.previewSkinIndex = 0;
    resetEventFilters(false);
    renderChampionList();
    renderChampion();
    clearLoading();
    announce(`已载入${document.champion.zh_name}，共${formatCount(document.stats.events)}条事件`);
  } catch (error) {
    if (sequence !== state.loadSequence) return;
    setLoading(`无法读取英雄数据：${error.message}。请使用本地 HTTP 服务打开本站。`, true);
  }
}

function renderChampion() {
  const document = state.champion;
  const champion = document.champion;
  const stats = document.stats;
  elements.championPortrait.hidden = false;
  elements.championPortrait.src = champion.portrait_url;
  elements.championPortrait.alt = `${champion.zh_name}头像`;
  elements.championPortrait.onerror = () => {
    elements.championPortrait.hidden = true;
  };
  elements.championAlias.textContent = champion.alias;
  elements.championName.textContent = champion.zh_name;
  elements.championTitle.textContent = champion.zh_title || champion.en_name;
  elements.championEventCount.textContent = formatCount(stats.events);
  elements.championStableCount.textContent = formatCount(stats.support.stable || 0);
  elements.championSkinCount.textContent = formatCount(document.skins.length);
  const renderedRatio = stats.events ? Math.round((stats.rendered / stats.events) * 100) : 0;
  elements.championRenderedRatio.textContent = `${renderedRatio}%`;

  renderModeCards();
  populateFilterOptions();
  renderSkinPreviewOptions();
  showPreviewSkin(0);
  applyEventFilters();
}

function renderModeCards() {
  const fragment = document.createDocumentFragment();
  const modes = state.champion.modes;
  if (!modes.length) {
    const empty = createElement("p", { text: "当前英雄没有带显式模式上下文的事件。" });
    empty.className = "event-detail-label";
    fragment.append(empty);
  }
  for (const mode of modes) {
    const button = createElement("button", { className: "mode-card", type: "button" });
    button.dataset.mode = mode.key;
    button.setAttribute("aria-pressed", String(state.modeKey === mode.key));
    button.append(
      createElement("strong", { text: mode.label }),
      createElement("span", {
        text: `${mode.prefix || "Base"} · ${formatCount(mode.event_count)} 条事件`,
      }),
    );
    button.addEventListener("click", () => {
      state.modeKey = state.modeKey === mode.key ? "all" : mode.key;
      elements.modeFilter.value = state.modeKey;
      state.page = 1;
      renderModeCards();
      renderEvents();
    });
    fragment.append(button);
  }
  elements.modeCards.replaceChildren(fragment);
}

function replaceSelectOptions(select, options, selected = "all") {
  const fragment = document.createDocumentFragment();
  for (const optionData of options) {
    const option = createElement("option", { text: optionData.label });
    option.value = optionData.value;
    fragment.append(option);
  }
  select.replaceChildren(fragment);
  select.value = selected;
}

function populateFilterOptions() {
  const events = state.champion.events;
  const assetKinds = [...new Set(events.map((event) => event.asset_kind))].sort();
  const supportLevels = [...new Set(events.map((event) => event.support_level))].sort();
  const assetLabels = { vo: "语音 VO", sfx: "音效 SFX", vfx: "视觉 VFX" };
  const supportLabels = { stable: "Stable", experimental: "Experimental", unsupported: "Unsupported" };
  replaceSelectOptions(
    elements.assetFilter,
    [{ value: "all", label: "全部类型" }, ...assetKinds.map((value) => ({ value, label: assetLabels[value] || value }))],
    state.assetKind,
  );
  replaceSelectOptions(
    elements.supportFilter,
    [
      { value: "all", label: "全部支持度" },
      ...supportLevels.map((value) => ({ value, label: supportLabels[value] || value })),
    ],
    state.supportLevel,
  );
  replaceSelectOptions(
    elements.modeFilter,
    [
      { value: "all", label: "全部模式" },
      ...state.champion.modes.map((mode) => ({ value: mode.key, label: mode.label })),
    ],
    state.modeKey,
  );
  replaceSelectOptions(
    elements.skinFilter,
    [
      { value: "all", label: "全部皮肤" },
      ...state.champion.skins.map((skin) => ({ value: String(skin.number), label: skin.zh_name })),
    ],
    state.skinNumber,
  );
}

function renderSkinPreviewOptions() {
  const options = state.champion.skins.map((skin) => ({
    value: String(skin.number),
    label: `${skin.number}. ${skin.zh_name}`,
  }));
  replaceSelectOptions(elements.skinPreviewSelect, options, String(state.champion.skins[0]?.number || 0));
}

function showPreviewSkin(index) {
  const skins = state.champion.skins;
  if (!skins.length) return;
  const normalizedIndex = ((index % skins.length) + skins.length) % skins.length;
  state.previewSkinIndex = normalizedIndex;
  const skin = skins[normalizedIndex];
  const image = elements.splashImage;
  image.classList.remove("image-missing");
  image.dataset.fallbackUrl = skin.fallback_url;
  image.dataset.fallbackUsed = "false";
  image.src = skin.splash_url;
  image.alt = `${state.champion.champion.zh_name} · ${skin.zh_name}原画`;
  elements.skinToken.textContent = skin.token;
  elements.skinHeading.textContent = skin.zh_name;
  elements.skinEnglishName.textContent = skin.en_name;
  elements.skinCounter.textContent = `${normalizedIndex + 1} / ${skins.length}`;
  elements.skinPreviewSelect.value = String(skin.number);
}

function handleSplashError() {
  const image = elements.splashImage;
  const fallback = image.dataset.fallbackUrl;
  if (fallback && image.dataset.fallbackUsed !== "true" && image.src !== fallback) {
    image.dataset.fallbackUsed = "true";
    image.src = fallback;
    announce("当前皮肤没有独立原画，已回退到基础原画");
    return;
  }
  image.classList.add("image-missing");
  image.removeAttribute("src");
}

function resetEventFilters(render = true) {
  state.eventQuery = "";
  state.assetKind = "all";
  state.supportLevel = "all";
  state.modeKey = "all";
  state.skinNumber = "all";
  state.page = 1;
  elements.eventSearch.value = "";
  if (render && state.champion) {
    populateFilterOptions();
    renderModeCards();
    renderEvents();
  }
}

function eventMatches(event) {
  if (state.assetKind !== "all" && event.asset_kind !== state.assetKind) return false;
  if (state.supportLevel !== "all" && event.support_level !== state.supportLevel) return false;
  if (state.modeKey !== "all" && event.mode.key !== state.modeKey) return false;
  if (state.skinNumber !== "all" && String(event.skin_number) !== state.skinNumber) return false;
  const query = state.eventQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  const haystack = [
    event.raw_event_name,
    event.zh_cn_short,
    event.zh_cn_detail,
    event.frame_type,
    event.primary_family,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
  return haystack.includes(query);
}

function applyEventFilters() {
  state.page = 1;
  renderModeCards();
  renderEvents();
}

function badge(text, attribute, value) {
  const element = createElement("span", { className: "badge", text });
  element.dataset[attribute] = value;
  return element;
}

function eventTypeLabel(value) {
  return { vo: "语音 VO", sfx: "音效 SFX", vfx: "视觉 VFX" }[value] || value.toUpperCase();
}

function skinNameForEvent(event) {
  const skin = state.champion.skins.find((item) => item.number === event.skin_number);
  return skin ? skin.zh_name : event.skin_number ? `Skin ${event.skin_number}` : "基础 / 通用";
}

async function copyRawEventName(rawEventName) {
  if (!navigator.clipboard?.writeText) {
    announce("当前浏览器不允许自动复制，请手动选择事件名");
    return;
  }
  try {
    await navigator.clipboard.writeText(rawEventName);
    announce(`已复制：${rawEventName}`);
  } catch {
    announce("复制失败，请手动选择事件名");
  }
}

function renderEventRow(event) {
  const row = createElement("tr");
  const identityCell = createElement("td");
  identityCell.dataset.label = "事件";
  const rawButton = createElement("button", {
    className: "event-name-button",
    text: event.raw_event_name,
    type: "button",
  });
  rawButton.title = "复制原始事件名";
  rawButton.setAttribute("translate", "no");
  rawButton.addEventListener("click", () => copyRawEventName(event.raw_event_name));
  const shortLabel = createElement("span", {
    className: event.zh_cn_short ? "event-short-label" : "event-short-label event-missing-label",
    text: event.zh_cn_short || "尚无中文短标签",
  });
  const detailLabel = createElement("span", {
    className: "event-detail-label",
    text: event.zh_cn_detail || "未生成中文详情",
  });
  identityCell.append(rawButton, shortLabel, detailLabel);

  const typeCell = createElement("td");
  typeCell.dataset.label = "类型";
  typeCell.append(badge(eventTypeLabel(event.asset_kind), "kind", event.asset_kind));
  const supportCell = createElement("td");
  supportCell.dataset.label = "支持度";
  supportCell.append(badge(event.support_level, "support", event.support_level));
  const modeCell = createElement("td");
  modeCell.dataset.label = "模式";
  modeCell.append(badge(event.mode.label, "mode", event.mode.key));
  const skinCell = createElement("td", { text: skinNameForEvent(event) });
  skinCell.dataset.label = "皮肤";
  row.append(identityCell, typeCell, supportCell, modeCell, skinCell);
  return row;
}

function renderEvents() {
  if (!state.champion) return;
  const filtered = state.champion.events.filter(eventMatches);
  const pageCount = Math.max(1, Math.ceil(filtered.length / state.pageSize));
  state.page = Math.min(Math.max(state.page, 1), pageCount);
  const start = (state.page - 1) * state.pageSize;
  const pageEvents = filtered.slice(start, start + state.pageSize);
  const fragment = document.createDocumentFragment();
  for (const event of pageEvents) fragment.append(renderEventRow(event));
  elements.eventTableBody.replaceChildren(fragment);
  elements.emptyState.hidden = filtered.length !== 0;
  elements.resultsSummary.textContent = `${formatCount(filtered.length)} / ${formatCount(state.champion.events.length)} 条`;
  elements.pageStatus.textContent = `第 ${state.page} / ${pageCount} 页`;
  elements.previousPage.disabled = state.page <= 1;
  elements.nextPage.disabled = state.page >= pageCount;
}

function wireInteractions() {
  elements.eventFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    applyEventFilters();
  });
  elements.championSearch.addEventListener("input", (event) => {
    state.championQuery = event.target.value;
    renderChampionList();
  });
  elements.splashImage.addEventListener("error", handleSplashError);
  elements.previousSkin.addEventListener("click", () => showPreviewSkin(state.previewSkinIndex - 1));
  elements.nextSkin.addEventListener("click", () => showPreviewSkin(state.previewSkinIndex + 1));
  elements.skinPreviewSelect.addEventListener("change", (event) => {
    const number = Number(event.target.value);
    const index = state.champion.skins.findIndex((skin) => skin.number === number);
    if (index >= 0) showPreviewSkin(index);
  });
  elements.eventSearch.addEventListener("input", (event) => {
    state.eventQuery = event.target.value;
    applyEventFilters();
  });
  elements.assetFilter.addEventListener("change", (event) => {
    state.assetKind = event.target.value;
    applyEventFilters();
  });
  elements.supportFilter.addEventListener("change", (event) => {
    state.supportLevel = event.target.value;
    applyEventFilters();
  });
  elements.modeFilter.addEventListener("change", (event) => {
    state.modeKey = event.target.value;
    applyEventFilters();
  });
  elements.skinFilter.addEventListener("change", (event) => {
    state.skinNumber = event.target.value;
    applyEventFilters();
  });
  elements.resetFilters.addEventListener("click", () => resetEventFilters(true));
  elements.previousPage.addEventListener("click", () => {
    state.page -= 1;
    renderEvents();
  });
  elements.nextPage.addEventListener("click", () => {
    state.page += 1;
    renderEvents();
  });
  elements.pageSize.addEventListener("change", (event) => {
    state.pageSize = Number(event.target.value);
    state.page = 1;
    renderEvents();
  });
  window.addEventListener("hashchange", () => {
    const alias = aliasFromHash();
    if (alias && alias !== state.champion?.champion.alias) loadChampion(alias);
  });
}

async function initialize() {
  wireInteractions();
  try {
    state.index = await fetchJson("data/index.json");
    updateDatasetSummary();
    renderChampionList();
    const requestedAlias = aliasFromHash();
    const initial =
      state.index.champions.find((champion) => champion.alias === requestedAlias) ||
      state.index.champions.find((champion) => champion.event_count > 0) ||
      state.index.champions[0];
    if (!initial) throw new Error("索引中没有英雄");
    if (window.location.hash !== championHash(initial.alias)) {
      window.history.replaceState(null, "", championHash(initial.alias));
    }
    await loadChampion(initial.alias);
  } catch (error) {
    elements.appShell.setAttribute("aria-busy", "false");
    setLoading(`演示站无法启动：${error.message}。请在站点目录运行本地 HTTP 服务，不要直接双击 HTML。`, true);
  }
}

initialize();
