/** 按实体懒加载中文解释；仅解码发布合同，不在客户端推断语义。 */
const COLUMNS = ["raw", "label", "flags", "skin", "state", "source_index"];
const STATE_COLUMNS = ["asset_kind", "support_level", "render_status", "review_status", "label_basis"];
const FLAGS = {raw_contains_skin: 1, champion_context_omitted: 2, skin_context_omitted: 4,
  preview_label: 8, needs_review: 16, unresolved_entity: 32};
const STATE_VALUES = [
  ["vo", "sfx", "control", "unknown"], ["stable", "experimental", "unsupported", "unknown"],
  ["rendered", "fallback", "missing", "not_applicable", "unknown"],
  ["not_required", "pending", "accepted", "rejected", "superseded", "unknown"],
  ["translation", "preview", "missing"]
];
const encoder = new TextEncoder();
const decoder = new TextDecoder("utf-8", {fatal: true});

export class IntegrityError extends Error {
  constructor(message, options) { super(message, options); this.name = "IntegrityError"; }
}
export class NetworkError extends Error {
  constructor(message, options) { super(message, options); this.name = "NetworkError"; }
}

function assert(condition, message) {
  if (!condition) throw new IntegrityError(message);
}
function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function integer(value, min = 0) { return Number.isSafeInteger(value) && value >= min; }
function equal(left, right) { return canonical(left) === canonical(right); }
function compare(left, right) {
  const a = Array.from(left, c => c.codePointAt(0));
  const b = Array.from(right, c => c.codePointAt(0));
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}
function canonical(value) {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (object(value)) return "{" + Object.keys(value).sort(compare).map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}";
  return JSON.stringify(value);
}
async function sha256(bytes) {
  assert(globalThis.crypto?.subtle, "Web Crypto is required (use HTTPS or localhost)");
  return Array.from(new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes)), x => x.toString(16).padStart(2, "0")).join("");
}
function freeze(value) {
  if (value !== null && typeof value === "object" && !Object.isFrozen(value)) {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
function checkEntity(entity) {
  assert(object(entity) && ["kind", "alias"].every(key => typeof entity[key] === "string" && entity[key]), "invalid entity");
  assert(entity.base_champion === undefined || typeof entity.base_champion === "string", "invalid base champion");
}
function checkSource(source) {
  assert(object(source) && /^[0-9a-f]{64}$/.test(source.content_digest) && integer(source.record_count), "invalid source identity");
  assert(source.dataset_id === "local-v1-" + source.content_digest.slice(0, 16), "inconsistent source identity");
}

/** 解码已获取的实体片。此函数检查结构，不替代 createClient 的文件摘要验证。 */
export function decodeShard(data, manifest) {
  assert(object(data) && data.schema_version === 1 && equal(data.columns, COLUMNS), "unsupported shard contract");
  checkEntity(data.entity);
  assert(Array.isArray(data.labels) && data.labels.every(label => typeof label === "string" && label.length > 0), "invalid labels dictionary");
  assert(Array.isArray(data.states) && Array.isArray(data.rows), "invalid state dictionary or rows");
  if (manifest) {
    checkSource(manifest.source);
    assert(equal(manifest.state_columns, STATE_COLUMNS), "unknown state columns");
  }
  const indices = new Set();
  return freeze(data.rows.map(values => {
    assert(Array.isArray(values) && values.length === COLUMNS.length, "invalid compact row");
    const [raw, label, flags, skin, stateId, sourceIndex] = values;
    assert(typeof raw === "string" && raw.length > 0, "invalid raw event name");
    assert(label === null || integer(label) && label < data.labels.length, "label index out of range");
    assert(integer(flags) && flags < 64, "invalid flags");
    assert(skin === null || integer(skin), "invalid skin");
    assert(integer(stateId) && stateId < data.states.length, "state index out of range");
    assert(integer(sourceIndex, 1) && !indices.has(sourceIndex), "invalid or duplicate source index");
    indices.add(sourceIndex);
    assert(!manifest || sourceIndex <= manifest.source.record_count, "source index exceeds dataset");
    const state = data.states[stateId];
    assert(Array.isArray(state) && state.length === STATE_COLUMNS.length && state.every(x => typeof x === "string"), "invalid state");
    assert(state.every((value, index) => STATE_VALUES[index].includes(value)), "unknown state value");
    const row = {raw_event_name: raw, entity: data.entity, label: label === null ? null : data.labels[label],
      flags, skin, state, source_index: sourceIndex};
    if (manifest) row.source = {...manifest.source, path: "normalized-events.jsonl", source_index: sourceIndex};
    return row;
  }));
}

/**
 * 初始化只获取 manifest；source/packageId 可固定调用方期望的发布身份。
 * cacheSize 限制完成后的数据缓存数，进行中的相同请求共享 Promise。
 * 返回的 manifest 和记录只读；MISS 仅表示索引确认没有候选，下载失败始终抛错。
 */
export async function createClient(baseURL, options = {}) {
  const base = new URL(baseURL, globalThis.location?.href);
  if (!base.pathname.endsWith("/")) base.pathname += "/";
  if (!["https:", "http:"].includes(base.protocol) || base.search || base.hash || base.username || base.password) {
    throw new TypeError("baseURL must be an HTTP(S) package directory without credentials, query or fragment");
  }
  const fetcher = options.fetch ?? globalThis.fetch;
  const cacheSize = options.cacheSize ?? 16;
  if (typeof fetcher !== "function" || !integer(cacheSize, 1)) throw new TypeError("invalid fetch or cacheSize option");
  const pathURL = name => {
    assert(typeof name === "string" && name.length > 0 && !/[\\:%?#]/.test(name) && name.split("/").every(x => x && x !== "." && x !== ".."), "unsafe package path");
    const url = new URL(name, base);
    assert(url.origin === base.origin && url.pathname.startsWith(base.pathname), "path escapes package directory");
    return url;
  };
  async function fetchBytes(name) {
    const url = pathURL(name);
    let response;
    try { response = await fetcher(url.href, {redirect: "error", credentials: "same-origin"}); }
    catch (cause) { throw new NetworkError("package request failed: " + name, {cause}); }
    if (!response?.ok) throw new NetworkError("package HTTP error: " + name + " (" + response?.status + ")");
    assert(!response.redirected && (!response.url || response.url === url.href), "package response redirected");
    try { return new Uint8Array(await response.arrayBuffer()); }
    catch (cause) { throw new NetworkError("package response interrupted: " + name, {cause}); }
  }
  function parse(bytes) {
    try { return JSON.parse(decoder.decode(bytes)); }
    catch (cause) { throw new IntegrityError("invalid UTF-8 JSON payload", {cause}); }
  }
  const manifest = parse(await fetchBytes("manifest.json"));
  assert(object(manifest) && manifest.schema_version === 1 && manifest.profile === "web", "unsupported web package");
  checkSource(manifest.source);
  assert(equal(manifest.flags, FLAGS) && equal(manifest.state_columns, STATE_COLUMNS), "unknown flag or state contract");
  assert(manifest.locale === "zh_CN" && manifest.text_contract === "trigger-explanation.zh_CN.v1", "unsupported text contract");
  assert(integer(manifest.record_count) && manifest.record_count <= manifest.source.record_count, "invalid record count");
  assert(/^[0-9a-f]{64}$/.test(manifest.projection_digest), "invalid projection digest");
  const unsigned = {...manifest};
  delete unsigned.package_id;
  assert(manifest.package_id === "consumption-v1-" + await sha256(encoder.encode(canonical(unsigned))), "package identity mismatch");
  if (options.packageId !== undefined) assert(manifest.package_id === options.packageId, "unexpected package identity");
  if (options.source !== undefined) {
    assert(object(options.source), "expected source must be an object");
    for (const key of ["dataset_id", "content_digest", "record_count"]) {
      if (options.source[key] !== undefined) assert(options.source[key] === manifest.source[key], "unexpected source " + key);
    }
  }
  assert(Array.isArray(manifest.files), "invalid file manifest");
  const files = new Map();
  for (const file of manifest.files) {
    assert(object(file), "invalid file descriptor");
    pathURL(file.path);
    assert(file.path !== "manifest.json" && !files.has(file.path) && integer(file.bytes) && /^[0-9a-f]{64}$/.test(file.sha256), "invalid or duplicate file descriptor");
    files.set(file.path, file);
  }
  assert(Array.isArray(manifest.shards), "invalid shard index");
  const entityMap = new Map();
  const shardPaths = new Set();
  let count = 0;
  for (const [index, shard] of manifest.shards.entries()) {
    assert(object(shard), "invalid shard descriptor");
    checkEntity(shard.entity);
    const key = shard.entity.kind + ":" + shard.entity.alias;
    assert(!entityMap.has(key) && !shardPaths.has(shard.path), "duplicate shard");
    assert(files.has(shard.path) && integer(shard.record_count), "invalid shard file or count");
    shardPaths.add(shard.path);
    entityMap.set(key, index);
    count += shard.record_count;
  }
  assert(count === manifest.record_count, "shard coverage count mismatch");
  const routing = manifest.routing;
  assert(object(routing) && routing.algorithm === "sha256-utf8-prefix" && routing.prefix_length === 2 && routing.path === "routes/{bucket}.json", "unsupported routing contract");
  assert(Array.isArray(routing.buckets) && routing.buckets.every(bucket => typeof bucket === "string" && /^[0-9a-f]{2}$/.test(bucket) && files.has(`routes/${bucket}.json`)), "invalid routing buckets");
  assert(new Set(routing.buckets).size === routing.buckets.length, "duplicate routing bucket");
  freeze(manifest);
  const cache = new Map();
  const pending = new Map();
  async function load(name) {
    if (cache.has(name)) {
      const value = cache.get(name);
      cache.delete(name);
      cache.set(name, value);
      return value;
    }
    if (pending.has(name)) return pending.get(name);
    const job = (async () => {
      const file = files.get(name);
      assert(file, "file outside manifest closure");
      const bytes = await fetchBytes(name);
      assert(bytes.length === file.bytes && await sha256(bytes) === file.sha256, "payload integrity mismatch: " + name);
      const data = freeze(parse(bytes));
      cache.set(name, data);
      while (cache.size > cacheSize) cache.delete(cache.keys().next().value);
      return data;
    })();
    pending.set(name, job);
    try { return await job; }
    finally { pending.delete(name); }
  }
  async function loadShard(index) {
    const descriptor = manifest.shards[index];
    assert(descriptor, "shard index out of range");
    const data = await load(descriptor.path);
    assert(equal(data.entity, descriptor.entity), "shard entity mismatch");
    const rows = decodeShard(data, manifest);
    assert(rows.length === descriptor.record_count, "shard count mismatch");
    return rows;
  }
  function entityIndex(entity) {
    if (typeof entity === "string") return entityMap.get(entity);
    if (!object(entity) || typeof entity.kind !== "string" || typeof entity.alias !== "string") throw new TypeError("entity must be kind:alias or {kind,alias}");
    return entityMap.get(entity.kind + ":" + entity.alias);
  }
  async function loadEntity(entity) {
    const index = entityIndex(entity);
    return index === undefined ? Object.freeze([]) : loadShard(index);
  }
  async function lookup(raw, query = {}) {
    if (typeof raw !== "string" || !raw) throw new TypeError("raw must be a non-empty event name");
    let records;
    if (query.entity !== undefined) records = (await loadEntity(query.entity)).filter(row => row.raw_event_name === raw);
    else {
      const bucket = (await sha256(encoder.encode(raw))).slice(0, 2);
      if (!routing.buckets.includes(bucket)) return freeze({status: "MISS", records: []});
      const route = await load(`routes/${bucket}.json`);
      assert(object(route), "invalid raw routing object");
      const candidates = Object.hasOwn(route, raw) ? route[raw] : [];
      assert(Array.isArray(candidates) && new Set(candidates).size === candidates.length && candidates.every(index => integer(index) && index < manifest.shards.length), "invalid raw routing candidates");
      records = (await Promise.all(candidates.map(async index => {
        const matches = (await loadShard(index)).filter(row => row.raw_event_name === raw);
        assert(matches.length > 0, "route points to a shard without the event");
        return matches;
      }))).flat();
      assert(new Set(records.map(row => row.source_index)).size === records.length, "duplicate source record across shards");
    }
    return freeze({status: records.length ? "FOUND" : "MISS", records});
  }
  return Object.freeze({manifest, loadEntity, lookup});
}
