"use strict";

const supportedDemoSchemaVersion = 2;
const numberFormatter = new Intl.NumberFormat("zh-CN");
const championCache = new Map();
const mapCache = new Map();
const reviewCache = new Map();
const state = {
  index: null,
  champion: null,
  map: null,
  status: "confirmed",
  entityType: "champion",
  browseQuery: "",
  eventQuery: "",
  assetKind: "all",
  supportLevel: "all",
  modeKey: "all",
  mapKey: "all",
  skinNumber: "all",
  page: 1,
  pageSize: 100,
  previewSkinIndex: 0,
  loadSequence: 0,
  mapEventQuery: "",
  mapAssetKind: "all",
  mapSupportLevel: "all",
  mapModeKey: "all",
  mapEntity: "all",
  mapPage: 1,
  mapPageSize: 100,
  mapLoadSequence: 0,
  reviewIndex: null,
  reviewRecords: [],
  reviewQueue: "residual",
  reviewQuery: "",
  reviewLayer: "all",
  reviewCode: "all",
  reviewFamily: "all",
  reviewEntity: "all",
  reviewMap: "all",
  reviewStatus: "all",
  reviewPage: 1,
  reviewPageSize: 50,
  selectedReviewId: null,
  reviewLoadSequence: 0,
};

const elements = Object.fromEntries(
  [
    "appShell",
    "loadingBanner",
    "browseSearch",
    "browseSearchLabel",
    "browseNavigation",
    "browseListHeading",
    "datasetSummaryInline",
    "datasetVersion",
    "championCount",
    "mapCount",
    "eventCount",
    "mapEventCount",
    "unassignedCount",
    "datasetId",
    "confirmedStatusButton",
    "reviewStatusButton",
    "championEntityButton",
    "mapEntityButton",
    "browseResultCount",
    "browseList",
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
    "mapFilter",
    "skinFilter",
    "resetFilters",
    "resultsSummary",
    "eventTableBody",
    "emptyState",
    "previousPage",
    "nextPage",
    "pageStatus",
    "pageSize",
    "championView",
    "mapView",
    "mapIdentifier",
    "mapAlias",
    "mapName",
    "mapIdentity",
    "mapCurrentEventCount",
    "mapStableCount",
    "mapModeCount",
    "mapRenderedRatio",
    "mapEventFilters",
    "mapEventSearch",
    "mapAssetFilter",
    "mapSupportFilter",
    "mapModeFilter",
    "mapEntityFilter",
    "resetMapFilters",
    "mapResultsSummary",
    "mapEventTableBody",
    "mapEmptyState",
    "mapPreviousPage",
    "mapNextPage",
    "mapPageStatus",
    "mapPageSize",
    "reviewView",
    "reviewHeading",
    "reviewDescription",
    "reviewResidualCount",
    "reviewIssueCount",
    "reviewUnassignedCount",
    "reviewLayerCards",
    "reviewFilters",
    "reviewQueue",
    "reviewSearch",
    "reviewLayer",
    "reviewCode",
    "reviewFamily",
    "reviewEntity",
    "reviewMapField",
    "reviewMap",
    "reviewStatus",
    "resetReviewFilters",
    "reviewResultsSummary",
    "reviewTableBody",
    "reviewEmptyState",
    "reviewPreviousPage",
    "reviewNextPage",
    "reviewPageStatus",
    "reviewPageSize",
    "reviewDetail",
    "reviewDetailTitle",
    "reviewDetailLocator",
    "copyReviewLocator",
    "copyPageLink",
    "reviewDetailLabels",
    "reviewDetailMeta",
    "reviewIssueList",
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

function hashParams() {
  return new URLSearchParams(window.location.hash.replace(/^#/, ""));
}

function championHash(alias) {
  const params = new URLSearchParams();
  params.set("status", "confirmed");
  params.set("type", "champion");
  params.set("champion", alias);
  return `#${params.toString()}`;
}

function mapHash(key) {
  const params = new URLSearchParams();
  params.set("status", "confirmed");
  params.set("type", "map");
  params.set("map", key);
  return `#${params.toString()}`;
}

function reviewHash() {
  const params = new URLSearchParams();
  params.set("status", "review");
  params.set("type", state.entityType);
  params.set("queue", state.reviewQueue);
  if (state.reviewQuery) params.set("q", state.reviewQuery);
  if (state.reviewLayer !== "all") params.set("layer", state.reviewLayer);
  if (state.reviewCode !== "all") params.set("code", state.reviewCode);
  if (state.reviewFamily !== "all") params.set("family", state.reviewFamily);
  if (state.reviewEntity !== "all") params.set("entity", state.reviewEntity);
  if (state.reviewMap !== "all") params.set("map", state.reviewMap);
  if (state.reviewStatus !== "all") params.set("status", state.reviewStatus);
  if (state.reviewPage > 1) params.set("page", String(state.reviewPage));
  if (state.selectedReviewId) params.set("record", state.selectedReviewId);
  return `#${params.toString()}`;
}

function replaceReviewHash() {
  window.history.replaceState(null, "", reviewHash());
}

function showView(status, entityType) {
  state.status = status;
  state.entityType = entityType;
  const reviewing = status === "review";
  const championBrowsing = !reviewing && entityType === "champion";
  const mapBrowsing = !reviewing && entityType === "map";
  elements.championView.hidden = !championBrowsing;
  elements.mapView.hidden = !mapBrowsing;
  elements.reviewView.hidden = !reviewing;
  elements.browseNavigation.hidden = reviewing;
  elements.confirmedStatusButton.setAttribute("aria-pressed", String(!reviewing));
  elements.reviewStatusButton.setAttribute("aria-pressed", String(reviewing));
  elements.championEntityButton.setAttribute("aria-pressed", String(entityType === "champion"));
  elements.mapEntityButton.setAttribute("aria-pressed", String(entityType === "map"));
  updateBrowseNavigationCopy();
}

function updateBrowseNavigationCopy() {
  const browsingMaps = state.entityType === "map";
  elements.browseSearchLabel.textContent = browsingMaps ? "查找地图" : "查找英雄";
  elements.browseListHeading.textContent = browsingMaps ? "地图" : "英雄";
  elements.browseSearch.placeholder = browsingMaps ? "名称、别名或 Map ID" : "名称、称号或别名";
  elements.browseList.setAttribute("aria-label", browsingMaps ? "选择地图" : "选择英雄");
  elements.browseSearch.value = state.browseQuery;
}

function updateDatasetSummary() {
  const index = state.index;
  elements.datasetSummaryInline.textContent = `${index.dataset.base_game_version} · ${formatCount(index.counts.champions)} 英雄 · ${formatCount(index.counts.maps)} 地图`;
  elements.datasetVersion.textContent = index.dataset.base_game_version;
  elements.championCount.textContent = formatCount(index.counts.champions);
  elements.mapCount.textContent = formatCount(index.counts.maps);
  elements.eventCount.textContent = formatCount(index.counts.champion_events);
  elements.mapEventCount.textContent = formatCount(index.counts.map_events);
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
    matchesChampionSearch(champion, state.browseQuery.trim()),
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
  elements.browseList.replaceChildren(fragment);
  elements.browseResultCount.textContent = `${formatCount(champions.length)} 位`;
}

function matchesMapSearch(map, query) {
  if (!query) return true;
  const haystack = [map.key, map.alias, map.label, map.en_name, Number.isInteger(map.id) ? `Map ${map.id}` : null]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
  return haystack.includes(query.toLocaleLowerCase("zh-CN"));
}

function renderMapList() {
  const maps = state.index.maps.filter((map) => matchesMapSearch(map, state.browseQuery.trim()));
  const fragment = document.createDocumentFragment();
  for (const map of maps) {
    const button = createElement("button", { className: "champion-link", type: "button" });
    button.dataset.mapKey = map.key;
    button.setAttribute("aria-current", String(state.map?.map.key === map.key));
    button.append(
      createElement("span", {
        className: "map-link-mark",
        text: Number.isInteger(map.id) ? `M${map.id}` : "MAP",
      }),
    );
    const copy = createElement("span", { className: "champion-link-copy" });
    copy.append(
      createElement("strong", { text: map.label }),
      createElement("span", { text: [map.alias, map.en_name].filter(Boolean).join(" · ") || map.key }),
    );
    button.append(
      copy,
      createElement("span", { className: "champion-link-count", text: formatCount(map.event_count) }),
    );
    button.addEventListener("click", () => {
      if (window.location.hash === mapHash(map.key)) {
        loadMap(map.key);
      } else {
        window.location.hash = mapHash(map.key);
      }
    });
    fragment.append(button);
  }
  elements.browseList.replaceChildren(fragment);
  elements.browseResultCount.textContent = `${formatCount(maps.length)} 张`;
}

function renderBrowseList() {
  if (state.entityType === "map") {
    renderMapList();
  } else {
    renderChampionList();
  }
}

function assertDatasetIdentity(payload, path) {
  if (!state.index || path === "data/index.json") return;
  const expected = state.index.dataset.dataset_id;
  const actual = payload.dataset?.dataset_id || payload.dataset_id;
  if (!actual) throw new Error(`数据分片缺少 dataset identity：${path}`);
  if (actual !== expected) throw new Error(`数据分片版本不一致：预期 ${expected}，实际 ${actual}`);
  if (payload.schema_version !== state.index.schema_version) {
    throw new Error(`数据分片 schema 不一致：${path}`);
  }
}

async function fetchJson(path) {
  const isEntryPoint = path === "data/index.json";
  const datasetId = isEntryPoint ? null : state.index?.dataset?.dataset_id;
  const url = new URL(path, document.baseURI);
  if (datasetId) {
    const cacheVersion = `${datasetId}-s${state.index.schema_version}`;
    url.searchParams.set("v", cacheVersion);
  }
  const response = await fetch(url, {
    cache: isEntryPoint ? "no-cache" : "default",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  const payload = await response.json();
  assertDatasetIdentity(payload, path);
  return payload;
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
    setLoading(`无法读取英雄数据：${error.message}。请检查静态站点文件是否完整或稍后重试。`, true);
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
  window.document.title = `${champion.zh_name} · 峡谷事件语义图鉴`;

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

function mapIdentityText(map) {
  if (!map || map.key === "base") return map?.label || "通用 / 未指定";
  const values = [map.label || map.key];
  if (map.alias) values.push(map.alias);
  if (Number.isInteger(map.id)) values.push(`Map ${map.id}`);
  return [...new Set(values)].join(" · ");
}

function mapHeaderIdentityText(map) {
  const values = [map.en_name];
  if (Number.isInteger(map.id)) values.push(`Map ${map.id}`);
  return [...new Set(values.filter(Boolean))].join(" · ") || map.key;
}

function mapFacetOptions(records) {
  const facets = new Map();
  for (const record of records) {
    const map = record.map || { key: "base", label: "通用 / 未指定" };
    const key = String(map.key || "base");
    const current = facets.get(key) || { map, count: 0 };
    current.count += 1;
    facets.set(key, current);
  }
  return [...facets.entries()]
    .sort((left, right) => {
      if (left[0] === "base") return -1;
      if (right[0] === "base") return 1;
      return String(left[1].map.label || left[0]).localeCompare(
        String(right[1].map.label || right[0]),
        "zh-CN",
      );
    })
    .map(([key, facet]) => ({
      value: key,
      label: `${mapIdentityText(facet.map)} · ${formatCount(facet.count)}`,
    }));
}

function populateFilterOptions() {
  const events = state.champion.events;
  const assetKinds = [...new Set(events.map((event) => event.asset_kind))].sort();
  const supportLevels = [...new Set(events.map((event) => event.support_level))].sort();
  const mapOptions = mapFacetOptions(events);
  const hasExplicitMaps = mapOptions.some((option) => option.value !== "base");
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
    elements.mapFilter,
    hasExplicitMaps
      ? [{ value: "all", label: "全部地图" }, ...mapOptions]
      : [{ value: "all", label: "当前英雄无显式地图" }],
    state.mapKey,
  );
  elements.mapFilter.disabled = !hasExplicitMaps;
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
  state.mapKey = "all";
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
  if (state.mapKey !== "all" && (event.map?.key || "base") !== state.mapKey) return false;
  if (state.skinNumber !== "all" && String(event.skin_number) !== state.skinNumber) return false;
  const query = state.eventQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  const haystack = [
    event.raw_event_name,
    event.zh_cn_short,
    event.zh_cn_detail,
    event.frame_type,
    event.primary_family,
    event.map?.label,
    event.map?.en_name,
    event.map?.alias,
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
  modeCell.dataset.label = "模式 / 地图";
  const contextBadges = createElement("div", { className: "context-badges" });
  contextBadges.append(badge(event.mode.label, "mode", event.mode.key));
  if (event.map?.key && event.map.key !== "base") {
    contextBadges.append(badge(`地图 · ${event.map.label}`, "map", event.map.key));
  }
  modeCell.append(contextBadges);
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

async function loadMap(key) {
  const mapIndex = state.index.maps.find((map) => map.key === key);
  if (!mapIndex) return;
  const sequence = ++state.mapLoadSequence;
  setLoading(`正在读取 ${mapIndex.label} 的事件…`);
  try {
    let document = mapCache.get(key);
    if (!document) {
      document = await fetchJson(mapIndex.data_path);
      mapCache.set(key, document);
    }
    if (sequence !== state.mapLoadSequence) return;
    state.map = document;
    resetMapFilters(false);
    renderMapList();
    renderMap();
    clearLoading();
    announce(`已载入${document.map.label}，共${formatCount(document.stats.events)}条事件`);
  } catch (error) {
    if (sequence !== state.mapLoadSequence) return;
    setLoading(`无法读取地图数据：${error.message}。请检查静态站点文件是否完整或稍后重试。`, true);
  }
}

function renderMap() {
  const document = state.map;
  const map = document.map;
  const stats = document.stats;
  elements.mapIdentifier.textContent = Number.isInteger(map.id) ? `M${map.id}` : "MAP";
  elements.mapAlias.textContent = map.alias || map.key;
  elements.mapName.textContent = map.label;
  elements.mapIdentity.textContent = mapHeaderIdentityText(map);
  elements.mapCurrentEventCount.textContent = formatCount(stats.events);
  elements.mapStableCount.textContent = formatCount(stats.support.stable || 0);
  elements.mapModeCount.textContent = formatCount(document.modes.length);
  const renderedRatio = stats.events ? Math.round((stats.rendered / stats.events) * 100) : 0;
  elements.mapRenderedRatio.textContent = `${renderedRatio}%`;
  window.document.title = `${map.label} · 峡谷事件语义图鉴`;
  populateMapFilterOptions();
  renderMapEvents();
}

function populateMapFilterOptions() {
  const events = state.map.events;
  const assetKinds = [...new Set(events.map((event) => event.asset_kind))].sort();
  const supportLevels = [...new Set(events.map((event) => event.support_level))].sort();
  const entities = [...new Set(events.map((event) => event.entity.alias))].sort((left, right) =>
    left.localeCompare(right, "zh-CN"),
  );
  const assetLabels = { vo: "语音 VO", sfx: "音效 SFX", vfx: "视觉 VFX" };
  const supportLabels = { stable: "Stable", experimental: "Experimental", unsupported: "Unsupported" };
  replaceSelectOptions(
    elements.mapAssetFilter,
    [{ value: "all", label: "全部类型" }, ...assetKinds.map((value) => ({ value, label: assetLabels[value] || value }))],
    state.mapAssetKind,
  );
  replaceSelectOptions(
    elements.mapSupportFilter,
    [
      { value: "all", label: "全部支持度" },
      ...supportLevels.map((value) => ({ value, label: supportLabels[value] || value })),
    ],
    state.mapSupportLevel,
  );
  replaceSelectOptions(
    elements.mapModeFilter,
    [
      { value: "all", label: "全部模式" },
      ...state.map.modes.map((mode) => ({ value: mode.key, label: mode.label })),
    ],
    state.mapModeKey,
  );
  replaceSelectOptions(
    elements.mapEntityFilter,
    [{ value: "all", label: "全部实体" }, ...entities.map((value) => ({ value, label: value }))],
    state.mapEntity,
  );
  elements.mapEventSearch.value = state.mapEventQuery;
}

function resetMapFilters(render = true) {
  state.mapEventQuery = "";
  state.mapAssetKind = "all";
  state.mapSupportLevel = "all";
  state.mapModeKey = "all";
  state.mapEntity = "all";
  state.mapPage = 1;
  elements.mapEventSearch.value = "";
  if (render && state.map) {
    populateMapFilterOptions();
    renderMapEvents();
  }
}

function mapEventMatches(event) {
  if (state.mapAssetKind !== "all" && event.asset_kind !== state.mapAssetKind) return false;
  if (state.mapSupportLevel !== "all" && event.support_level !== state.mapSupportLevel) return false;
  if (state.mapModeKey !== "all" && event.mode.key !== state.mapModeKey) return false;
  if (state.mapEntity !== "all" && event.entity.alias !== state.mapEntity) return false;
  const query = state.mapEventQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  const haystack = [
    event.raw_event_name,
    event.zh_cn_short,
    event.zh_cn_detail,
    event.frame_type,
    event.primary_family,
    event.entity.alias,
    event.entity.kind,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
  return haystack.includes(query);
}

function applyMapFilters() {
  state.mapPage = 1;
  renderMapEvents();
}

function renderMapEventRow(event) {
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
  identityCell.append(
    rawButton,
    createElement("span", {
      className: event.zh_cn_short ? "event-short-label" : "event-short-label event-missing-label",
      text: event.zh_cn_short || "尚无中文短标签",
    }),
    createElement("span", { className: "event-detail-label", text: event.zh_cn_detail || "未生成中文详情" }),
  );
  const typeCell = createElement("td");
  typeCell.dataset.label = "类型";
  typeCell.append(badge(eventTypeLabel(event.asset_kind), "kind", event.asset_kind));
  const supportCell = createElement("td");
  supportCell.dataset.label = "支持度";
  supportCell.append(badge(event.support_level, "support", event.support_level));
  const modeCell = createElement("td");
  modeCell.dataset.label = "模式";
  modeCell.append(badge(event.mode.label, "mode", event.mode.key));
  const entityCell = createElement("td");
  entityCell.dataset.label = "实体";
  entityCell.append(
    createElement("strong", { text: event.entity.alias }),
    createElement("span", {
      className: "event-detail-label",
      text: `${event.entity.kind} · ${event.primary_family || "unknown"}`,
    }),
  );
  row.append(identityCell, typeCell, supportCell, modeCell, entityCell);
  return row;
}

function renderMapEvents() {
  if (!state.map) return;
  const filtered = state.map.events.filter(mapEventMatches);
  const pageCount = Math.max(1, Math.ceil(filtered.length / state.mapPageSize));
  state.mapPage = Math.min(Math.max(state.mapPage, 1), pageCount);
  const start = (state.mapPage - 1) * state.mapPageSize;
  const fragment = document.createDocumentFragment();
  for (const event of filtered.slice(start, start + state.mapPageSize)) {
    fragment.append(renderMapEventRow(event));
  }
  elements.mapEventTableBody.replaceChildren(fragment);
  elements.mapEmptyState.hidden = filtered.length !== 0;
  elements.mapResultsSummary.textContent = `${formatCount(filtered.length)} / ${formatCount(state.map.events.length)} 条`;
  elements.mapPageStatus.textContent = `第 ${state.mapPage} / ${pageCount} 页`;
  elements.mapPreviousPage.disabled = state.mapPage <= 1;
  elements.mapNextPage.disabled = state.mapPage >= pageCount;
}

async function ensureReviewIndex() {
  if (state.reviewIndex) return state.reviewIndex;
  const index = await fetchJson("data/review/index.json");
  if (index.dataset?.dataset_id !== state.index.dataset.dataset_id) {
    throw new Error("Review 索引与当前数据集身份不一致");
  }
  state.reviewIndex = index;
  return index;
}

function updateReviewScopeSummary() {
  const scope = state.reviewIndex.scopes.find((item) => item.key === state.entityType);
  const mapReview = state.entityType === "map";
  elements.reviewHeading.textContent = mapReview ? "地图 Review" : "英雄 Review";
  elements.reviewDescription.textContent = mapReview
    ? "同一 candidate 中带显式地图槽位的残差与未归属记录；地图身份已知，待审的是其余语义与归属问题。"
    : "同一 candidate 中无显式地图槽位的残差与未归属记录；主要审阅英雄归属、动作与翻译问题。";
  elements.reviewResidualCount.textContent = formatCount(scope?.residual_records || 0);
  const issueCount = state.reviewIndex.layers.reduce(
    (total, layer) => total + Number(layer.scope_issue_counts?.[state.entityType] || 0),
    0,
  );
  elements.reviewIssueCount.textContent = formatCount(issueCount);
  elements.reviewUnassignedCount.textContent = formatCount(scope?.unassigned_records || 0);
  elements.reviewMapField.hidden = !mapReview;
}

async function loadReviewQueue(queue) {
  if (reviewCache.has(queue.key)) return reviewCache.get(queue.key);
  const payload = await fetchJson(queue.data_path);
  if (payload.dataset_id !== state.index.dataset.dataset_id || !Array.isArray(payload.records)) {
    throw new Error(`Review 队列 ${queue.key} 与当前数据集身份不一致`);
  }
  reviewCache.set(queue.key, payload.records);
  return payload.records;
}

function reviewFacetOptions(records, getter) {
  const counts = new Map();
  for (const record of records) {
    const value = String(getter(record) || "unknown");
    counts.set(value, (counts.get(value) || 0) + 1);
  }
  return [...counts.entries()]
    .sort((left, right) => left[0].localeCompare(right[0], "zh-CN"))
    .map(([value, count]) => ({ value, label: `${value} · ${formatCount(count)}` }));
}

function selectAvailable(select, value, fallback = "all") {
  const available = [...select.options].some((option) => option.value === value);
  select.value = available ? value : fallback;
  return select.value;
}

function populateReviewFilters() {
  const index = state.reviewIndex;
  const records = state.reviewRecords.filter((record) => record.review_scope === state.entityType);
  replaceSelectOptions(
    elements.reviewQueue,
    index.queues.map((queue) => ({
      value: queue.key,
      label: `${queue.label} · ${formatCount(queue.scope_counts?.[state.entityType] || 0)}`,
    })),
    state.reviewQueue,
  );

  const layers = state.reviewQueue === "residual" ? index.layers : [];
  replaceSelectOptions(
    elements.reviewLayer,
    [
      { value: "all", label: "全部层级" },
      ...layers.map((layer) => ({
        value: layer.key,
        label: `${layer.label} · ${formatCount(layer.record_count)}`,
      })),
    ],
    state.reviewLayer,
  );

  const codes = reviewFacetOptions(
    records.flatMap((record) => record.issues || []),
    (issue) => issue.code,
  );
  replaceSelectOptions(elements.reviewCode, [{ value: "all", label: "全部代码" }, ...codes], state.reviewCode);
  replaceSelectOptions(
    elements.reviewFamily,
    [{ value: "all", label: "全部语义族" }, ...reviewFacetOptions(records, (record) => record.family)],
    state.reviewFamily,
  );
  replaceSelectOptions(
    elements.reviewEntity,
    [{ value: "all", label: "全部实体" }, ...reviewFacetOptions(records, (record) => record.entity.alias)],
    state.reviewEntity,
  );
  replaceSelectOptions(
    elements.reviewMap,
    [{ value: "all", label: "全部地图" }, ...mapFacetOptions(records)],
    state.reviewMap,
  );
  replaceSelectOptions(
    elements.reviewStatus,
    [{ value: "all", label: "全部状态" }, ...reviewFacetOptions(records, (record) => record.review_status)],
    state.reviewStatus,
  );

  state.reviewLayer = selectAvailable(elements.reviewLayer, state.reviewLayer);
  state.reviewCode = selectAvailable(elements.reviewCode, state.reviewCode);
  state.reviewFamily = selectAvailable(elements.reviewFamily, state.reviewFamily);
  state.reviewEntity = selectAvailable(elements.reviewEntity, state.reviewEntity);
  state.reviewMap = selectAvailable(elements.reviewMap, state.reviewMap);
  state.reviewStatus = selectAvailable(elements.reviewStatus, state.reviewStatus);
  elements.reviewSearch.value = state.reviewQuery;
}

function renderReviewLayerCards() {
  const fragment = document.createDocumentFragment();
  for (const layer of state.reviewIndex.layers) {
    const button = createElement("button", { className: "review-layer-card", type: "button" });
    button.dataset.layer = layer.key;
    const active = state.reviewQueue === "residual" && state.reviewLayer === layer.key;
    const recordCount = layer.scope_record_counts?.[state.entityType] || 0;
    const issueCount = layer.scope_issue_counts?.[state.entityType] || 0;
    button.setAttribute("aria-pressed", String(active));
    button.append(
      createElement("strong", { text: layer.label }),
      createElement("span", {
        text: `${formatCount(recordCount)} 条记录 · ${formatCount(issueCount)} 个问题`,
      }),
    );
    button.addEventListener("click", () => {
      state.reviewQueue = "residual";
      state.reviewLayer = active ? "all" : layer.key;
      state.reviewCode = "all";
      state.reviewPage = 1;
      state.selectedReviewId = null;
      window.location.hash = reviewHash();
    });
    fragment.append(button);
  }
  elements.reviewLayerCards.replaceChildren(fragment);
}

function reviewMatches(record) {
  if (record.review_scope !== state.entityType) return false;
  if (state.reviewLayer !== "all" && !record.layers.includes(state.reviewLayer)) return false;
  if (state.reviewCode !== "all" && !record.issues.some((issue) => issue.code === state.reviewCode)) return false;
  if (state.reviewFamily !== "all" && record.family !== state.reviewFamily) return false;
  if (state.reviewEntity !== "all" && record.entity.alias !== state.reviewEntity) return false;
  if (state.reviewMap !== "all" && (record.map?.key || "base") !== state.reviewMap) return false;
  if (state.reviewStatus !== "all" && record.review_status !== state.reviewStatus) return false;
  const query = state.reviewQuery.trim().toLocaleLowerCase("zh-CN");
  if (!query) return true;
  const issueText = record.issues.flatMap((issue) => [issue.code, issue.message]).join(" ");
  const haystack = [
    record.record_id,
    record.raw_event_name,
    record.zh_cn_short,
    record.zh_cn_detail,
    record.entity.alias,
    record.entity.kind,
    record.family,
    record.profile,
    record.frame_type,
    record.map?.label,
    record.map?.en_name,
    record.map?.alias,
    issueText,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase("zh-CN");
  return haystack.includes(query);
}

function filteredReviewRecords() {
  return state.reviewRecords.filter(reviewMatches);
}

function scopedReviewRecordCount() {
  return state.reviewRecords.filter((record) => record.review_scope === state.entityType).length;
}

function reviewLayerLabel(key) {
  return state.reviewIndex.layers.find((layer) => layer.key === key)?.label || key;
}

function renderReviewRow(record) {
  const row = createElement("tr");
  row.dataset.recordId = record.record_id;
  row.dataset.selected = String(record.record_id === state.selectedReviewId);

  const locatorCell = createElement("td");
  locatorCell.dataset.label = "定位";
  const rawButton = createElement("button", {
    className: "event-name-button",
    text: record.raw_event_name,
    type: "button",
  });
  rawButton.title = "查看 Review 详情";
  rawButton.setAttribute("translate", "no");
  rawButton.addEventListener("click", () => selectReviewRecord(record.record_id));
  locatorCell.append(
    rawButton,
    createElement("span", {
      className: record.zh_cn_short ? "event-short-label" : "event-short-label event-missing-label",
      text: record.zh_cn_short || "尚无中文短标签",
    }),
    createElement("span", { className: "event-detail-label", text: record.zh_cn_detail || "未生成中文详情" }),
  );

  const issueCell = createElement("td");
  issueCell.dataset.label = "问题";
  if (record.layers.length) {
    for (const layer of record.layers) issueCell.append(badge(reviewLayerLabel(layer), "layer", layer));
  } else {
    issueCell.append(badge("未归属", "layer", "unassigned"));
  }
  const codeLine = createElement("span", {
    className: "review-code-line",
    text: record.issues.map((issue) => issue.code).join(" · ") || "无安全英雄归属证据",
  });
  issueCell.append(codeLine);

  const entityCell = createElement("td");
  entityCell.dataset.label = "实体";
  entityCell.append(
    createElement("strong", { text: record.entity.alias }),
    createElement("span", { className: "event-detail-label", text: `${record.entity.kind} · ${record.family}` }),
  );
  if (record.map?.key && record.map.key !== "base") {
    const mapBadges = createElement("div", { className: "context-badges" });
    mapBadges.append(badge(`地图 · ${record.map.label}`, "map", record.map.key));
    entityCell.append(mapBadges);
  }

  const statusCell = createElement("td");
  statusCell.dataset.label = "状态";
  statusCell.append(
    badge(record.review_status, "status", record.review_status),
    badge(record.support_level, "support", record.support_level),
    createElement("span", { className: "event-detail-label", text: record.render_status }),
  );
  row.append(locatorCell, issueCell, entityCell, statusCell);
  return row;
}

function renderReviewDetail() {
  const record = state.reviewRecords.find((item) => item.record_id === state.selectedReviewId);
  if (!record) {
    elements.reviewDetail.hidden = true;
    return;
  }
  elements.reviewDetail.hidden = false;
  elements.reviewDetailTitle.textContent = record.raw_event_name;
  elements.reviewDetailLocator.textContent = `record_id=${record.record_id}`;
  elements.reviewDetailLabels.replaceChildren(
    createElement("strong", { text: record.zh_cn_short || "尚无中文短标签" }),
    createElement("span", { text: record.zh_cn_detail || "未生成中文详情" }),
  );

  const meta = [
    ["实体", `${record.entity.alias} · ${record.entity.kind}`],
    [
      "地图",
      mapIdentityText(record.map),
    ],
    ["语义族", `${record.family} · ${record.profile}`],
    ["Frame", record.frame_type],
    ["状态", `${record.review_status} · ${record.support_level} · ${record.render_status}`],
  ];
  const metaFragment = document.createDocumentFragment();
  for (const [label, value] of meta) {
    metaFragment.append(createElement("dt", { text: label }), createElement("dd", { text: value }));
  }
  elements.reviewDetailMeta.replaceChildren(metaFragment);

  const issueFragment = document.createDocumentFragment();
  if (record.issues.length) {
    for (const issue of record.issues) {
      const item = createElement("li");
      item.append(
        createElement("code", { text: issue.code }),
        createElement("span", { text: issue.message || "validator 未提供额外说明" }),
      );
      issueFragment.append(item);
    }
  } else {
    issueFragment.append(
      createElement("li", { text: "没有足够结构化证据把该记录安全归入某位基础英雄。" }),
    );
  }
  elements.reviewIssueList.replaceChildren(issueFragment);
}

function renderReviewRecords() {
  const filtered = filteredReviewRecords();
  const pageCount = Math.max(1, Math.ceil(filtered.length / state.reviewPageSize));
  state.reviewPage = Math.min(Math.max(state.reviewPage, 1), pageCount);
  const start = (state.reviewPage - 1) * state.reviewPageSize;
  const fragment = document.createDocumentFragment();
  for (const record of filtered.slice(start, start + state.reviewPageSize)) {
    fragment.append(renderReviewRow(record));
  }
  elements.reviewTableBody.replaceChildren(fragment);
  elements.reviewEmptyState.hidden = filtered.length !== 0;
  elements.reviewResultsSummary.textContent = `${formatCount(filtered.length)} / ${formatCount(scopedReviewRecordCount())} 条`;
  elements.reviewPageStatus.textContent = `第 ${state.reviewPage} / ${pageCount} 页`;
  elements.reviewPreviousPage.disabled = state.reviewPage <= 1;
  elements.reviewNextPage.disabled = state.reviewPage >= pageCount;
  renderReviewDetail();
}

function selectReviewRecord(recordId) {
  state.selectedReviewId = recordId;
  const filtered = filteredReviewRecords();
  const index = filtered.findIndex((record) => record.record_id === recordId);
  if (index >= 0) state.reviewPage = Math.floor(index / state.reviewPageSize) + 1;
  replaceReviewHash();
  renderReviewRecords();
}

function applyReviewFilters() {
  state.reviewPage = 1;
  state.selectedReviewId = null;
  renderReviewLayerCards();
  replaceReviewHash();
  renderReviewRecords();
}

function resetReviewFilters() {
  clearReviewFilterState();
  populateReviewFilters();
  renderReviewLayerCards();
  replaceReviewHash();
  renderReviewRecords();
}

function clearReviewFilterState() {
  state.reviewQuery = "";
  state.reviewLayer = "all";
  state.reviewCode = "all";
  state.reviewFamily = "all";
  state.reviewEntity = "all";
  state.reviewMap = "all";
  state.reviewStatus = "all";
  state.reviewPage = 1;
  state.selectedReviewId = null;
}

async function loadReviewView(params) {
  const sequence = ++state.reviewLoadSequence;
  showView("review", state.entityType);
  setLoading("正在读取 Review 队列…");
  try {
    const index = await ensureReviewIndex();
    const requestedQueue = params.get("queue") || "residual";
    const queue = index.queues.find((item) => item.key === requestedQueue) || index.queues[0];
    if (!queue) throw new Error("Review 索引中没有可用队列");
    const records = await loadReviewQueue(queue);
    if (sequence !== state.reviewLoadSequence) return;

    state.reviewQueue = queue.key;
    state.reviewRecords = records;
    state.reviewQuery = params.get("q") || "";
    state.reviewLayer = params.get("layer") || "all";
    state.reviewCode = params.get("code") || "all";
    state.reviewFamily = params.get("family") || "all";
    state.reviewEntity = params.get("entity") || "all";
    state.reviewMap = state.entityType === "map" ? params.get("map") || "all" : "all";
    state.reviewStatus = params.get("status") || "all";
    state.reviewPage = Math.max(1, Number(params.get("page") || 1) || 1);
    state.selectedReviewId = params.get("record");
    updateReviewScopeSummary();
    populateReviewFilters();
    renderReviewLayerCards();
    if (!params.has("page") && state.selectedReviewId) {
      const selectedIndex = filteredReviewRecords().findIndex(
        (record) => record.record_id === state.selectedReviewId,
      );
      if (selectedIndex >= 0) state.reviewPage = Math.floor(selectedIndex / state.reviewPageSize) + 1;
    }
    if (!filteredReviewRecords().some((record) => record.record_id === state.selectedReviewId)) {
      state.selectedReviewId = null;
    }
    replaceReviewHash();
    renderReviewRecords();
    clearLoading();
    announce(`已载入${state.entityType === "map" ? "地图" : "英雄"}${queue.label}，共${formatCount(scopedReviewRecordCount())}条记录`);
  } catch (error) {
    if (sequence !== state.reviewLoadSequence) return;
    setLoading(`无法读取 Review 数据：${error.message}`, true);
  }
}

async function copyReviewLocator() {
  const record = state.reviewRecords.find((item) => item.record_id === state.selectedReviewId);
  if (!record) return;
  const locator = `record_id=${record.record_id}\nraw_event_name=${record.raw_event_name}`;
  if (!navigator.clipboard?.writeText) {
    announce("当前浏览器不允许自动复制，请手动选择定位信息");
    return;
  }
  try {
    await navigator.clipboard.writeText(locator);
    announce("已复制稳定 Review 定位");
  } catch {
    announce("复制失败，请手动选择定位信息");
  }
}

async function copyPageLink() {
  if (!navigator.clipboard?.writeText) {
    announce("当前浏览器不允许自动复制，请手动复制地址栏链接");
    return;
  }
  try {
    await navigator.clipboard.writeText(window.location.href);
    announce("已复制当前页面链接");
  } catch {
    announce("复制失败，请手动复制地址栏链接");
  }
}

function wireInteractions() {
  elements.confirmedStatusButton.addEventListener("click", () => {
    if (state.entityType === "map") {
      const initial =
        (state.status === "review" && state.reviewMap !== "all" ? state.reviewMap : null) ||
        state.map?.map.key ||
        state.index.maps[0]?.key;
      if (initial) window.location.hash = mapHash(initial);
      return;
    }
    const initial = state.champion?.champion.alias || state.index.champions[0]?.alias;
    if (initial) window.location.hash = championHash(initial);
  });
  elements.reviewStatusButton.addEventListener("click", () => {
    window.location.hash = reviewHash();
  });
  elements.championEntityButton.addEventListener("click", () => {
    state.browseQuery = "";
    if (state.status === "review") {
      state.entityType = "champion";
      clearReviewFilterState();
      window.location.hash = reviewHash();
      return;
    }
    const initial = state.champion?.champion.alias || state.index.champions[0]?.alias;
    if (initial) window.location.hash = championHash(initial);
  });
  elements.mapEntityButton.addEventListener("click", () => {
    state.browseQuery = "";
    if (state.status === "review") {
      state.entityType = "map";
      clearReviewFilterState();
      window.location.hash = reviewHash();
      return;
    }
    const initial = state.map?.map.key || state.index.maps[0]?.key;
    if (initial) window.location.hash = mapHash(initial);
  });
  elements.eventFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    applyEventFilters();
  });
  elements.browseSearch.addEventListener("input", (event) => {
    state.browseQuery = event.target.value;
    renderBrowseList();
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
  elements.mapFilter.addEventListener("change", (event) => {
    state.mapKey = event.target.value;
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
  elements.mapEventFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    applyMapFilters();
  });
  elements.mapEventSearch.addEventListener("input", (event) => {
    state.mapEventQuery = event.target.value;
    applyMapFilters();
  });
  elements.mapAssetFilter.addEventListener("change", (event) => {
    state.mapAssetKind = event.target.value;
    applyMapFilters();
  });
  elements.mapSupportFilter.addEventListener("change", (event) => {
    state.mapSupportLevel = event.target.value;
    applyMapFilters();
  });
  elements.mapModeFilter.addEventListener("change", (event) => {
    state.mapModeKey = event.target.value;
    applyMapFilters();
  });
  elements.mapEntityFilter.addEventListener("change", (event) => {
    state.mapEntity = event.target.value;
    applyMapFilters();
  });
  elements.resetMapFilters.addEventListener("click", () => resetMapFilters(true));
  elements.mapPreviousPage.addEventListener("click", () => {
    state.mapPage -= 1;
    renderMapEvents();
  });
  elements.mapNextPage.addEventListener("click", () => {
    state.mapPage += 1;
    renderMapEvents();
  });
  elements.mapPageSize.addEventListener("change", (event) => {
    state.mapPageSize = Number(event.target.value);
    state.mapPage = 1;
    renderMapEvents();
  });
  elements.reviewFilters.addEventListener("submit", (event) => {
    event.preventDefault();
    applyReviewFilters();
  });
  elements.reviewQueue.addEventListener("change", (event) => {
    state.reviewQueue = event.target.value;
    state.reviewQuery = "";
    state.reviewLayer = "all";
    state.reviewCode = "all";
    state.reviewFamily = "all";
    state.reviewEntity = "all";
    state.reviewMap = "all";
    state.reviewStatus = "all";
    state.reviewPage = 1;
    state.selectedReviewId = null;
    window.location.hash = reviewHash();
  });
  elements.reviewSearch.addEventListener("input", (event) => {
    state.reviewQuery = event.target.value;
    applyReviewFilters();
  });
  elements.reviewLayer.addEventListener("change", (event) => {
    state.reviewLayer = event.target.value;
    applyReviewFilters();
  });
  elements.reviewCode.addEventListener("change", (event) => {
    state.reviewCode = event.target.value;
    applyReviewFilters();
  });
  elements.reviewFamily.addEventListener("change", (event) => {
    state.reviewFamily = event.target.value;
    applyReviewFilters();
  });
  elements.reviewEntity.addEventListener("change", (event) => {
    state.reviewEntity = event.target.value;
    applyReviewFilters();
  });
  elements.reviewMap.addEventListener("change", (event) => {
    state.reviewMap = event.target.value;
    applyReviewFilters();
  });
  elements.reviewStatus.addEventListener("change", (event) => {
    state.reviewStatus = event.target.value;
    applyReviewFilters();
  });
  elements.resetReviewFilters.addEventListener("click", resetReviewFilters);
  elements.reviewPreviousPage.addEventListener("click", () => {
    state.reviewPage -= 1;
    replaceReviewHash();
    renderReviewRecords();
  });
  elements.reviewNextPage.addEventListener("click", () => {
    state.reviewPage += 1;
    replaceReviewHash();
    renderReviewRecords();
  });
  elements.reviewPageSize.addEventListener("change", (event) => {
    state.reviewPageSize = Number(event.target.value);
    state.reviewPage = 1;
    replaceReviewHash();
    renderReviewRecords();
  });
  elements.copyReviewLocator.addEventListener("click", copyReviewLocator);
  elements.copyPageLink.addEventListener("click", copyPageLink);
  window.addEventListener("hashchange", () => {
    syncRoute();
  });
}

async function syncRoute() {
  const params = hashParams();
  const legacyReview = params.get("view") === "review";
  const status = legacyReview || params.get("status") === "review" ? "review" : "confirmed";
  const requestedType = params.get("type");
  const inferredLegacyType = legacyReview && params.get("map") && params.get("map") !== "all" ? "map" : "champion";
  const entityType = requestedType === "map" || requestedType === "champion" ? requestedType : inferredLegacyType;
  if (state.entityType !== entityType) state.browseQuery = "";
  showView(status, entityType);

  if (status === "review") {
    document.title = `${entityType === "map" ? "地图" : "英雄"} Review · 峡谷事件语义图鉴`;
    await loadReviewView(params);
    return;
  }

  if (entityType === "map") {
    const requestedMap = params.get("map");
    const initial = state.index.maps.find((map) => map.key === requestedMap) || state.index.maps[0];
    if (!initial) throw new Error("索引中没有显式地图事件");
    if (window.location.hash !== mapHash(initial.key)) {
      window.history.replaceState(null, "", mapHash(initial.key));
    }
    if (state.map?.map.key === initial.key) {
      renderMapList();
      clearLoading();
      return;
    }
    await loadMap(initial.key);
    return;
  }

  const requestedAlias = params.get("champion");
  const initial =
    state.index.champions.find((champion) => champion.alias === requestedAlias) ||
    state.index.champions.find((champion) => champion.event_count > 0) ||
    state.index.champions[0];
  if (!initial) throw new Error("索引中没有英雄");
  if (window.location.hash !== championHash(initial.alias)) {
    window.history.replaceState(null, "", championHash(initial.alias));
  }
  if (state.champion?.champion.alias === initial.alias) {
    renderChampionList();
    clearLoading();
    return;
  }
  await loadChampion(initial.alias);
}

async function initialize() {
  wireInteractions();
  try {
    state.index = await fetchJson("data/index.json");
    if (state.index.schema_version !== supportedDemoSchemaVersion) {
      throw new Error(
        `不支持的 Demo schema：${state.index.schema_version}，需要 ${supportedDemoSchemaVersion}`,
      );
    }
    updateDatasetSummary();
    renderBrowseList();
    await syncRoute();
  } catch (error) {
    elements.appShell.setAttribute("aria-busy", "false");
    setLoading(`演示站无法启动：${error.message}。请通过 HTTP(S) 地址访问，并确认站点数据完整。`, true);
  }
}

initialize();
