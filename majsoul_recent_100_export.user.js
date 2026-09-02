// ==UserScript==
// @name         雀魂：导出自己的牌谱到 Windows
// @namespace    local.batchmortal
// @version      0.8.0
// @description  导出自己的雀魂牌谱，并与 Windows 桌面版配合从牌谱屋按玩家 ID 导入公开牌谱。
// @match        https://game.maj-soul.com/*
// @match        https://game.mahjongsoul.com/*
// @match        https://mahjongsoul.game.yo-star.com/*
// @match        https://amae-koromo.sapk.ch/*
// @run-at       document-start
// @grant        none
// ==/UserScript==

(() => {
  "use strict";

  const VERSION = "0.8.0";

  function runKoromoBridge() {
    const bridgeMatch = location.hash.match(/(?:^#|[&#])batchmortal=([0-9a-f]+)/i);
    const playerMatch = location.pathname.match(/^\/player\/(\d+)\/([0-9.]+)/);
    if (!bridgeMatch || !playerMatch) return;

    const bridgeToken = bridgeMatch[1].toLowerCase();
    const accountId = Number(playerMatch[1]);
    const selectedModes = new Set(
      playerMatch[2].split(".").map(Number).filter(Number.isFinite),
    );
    const requestedLimit = Number(new URLSearchParams(location.search).get("limit")) || null;
    const records = new Map();
    let selectedTotal = null;
    let recordResponses = 0;
    let lastRecordAt = 0;
    let finished = false;
    let statusElement = null;
    let downloadButton = null;
    let preparedPayload = null;

    const setBridgeStatus = (message, isError = false) => {
      if (!statusElement) return;
      statusElement.textContent = message;
      statusElement.style.color = isError ? "#ffb4ab" : "#d9e4f2";
    };

    const looksLikeKoromoRecords = (data) => Array.isArray(data) && data.every(
      (value) => !value || (typeof value === "object" && (value.uuid || value._id)),
    );

    const sameSelectedModes = (url) => {
      try {
        const apiModes = new Set(
          (new URL(url).searchParams.get("mode") || "")
            .split(/[,.]/).map(Number).filter(Number.isFinite),
        );
        return apiModes.size === selectedModes.size
          && [...selectedModes].every((mode) => apiModes.has(mode));
      } catch (_) {
        return false;
      }
    };

    const rememberResponse = (data, url) => {
      if (looksLikeKoromoRecords(data)) {
        const matching = data.filter(
          (record) => record?.uuid && Array.isArray(record.players) && selectedModes.has(Number(record.modeId)),
        );
        if (matching.length || /player_records\//.test(url)) {
          for (const record of matching) records.set(String(record.uuid), record);
          recordResponses += 1;
          lastRecordAt = Date.now();
          setBridgeStatus(`已从牌谱屋读取 ${records.size} 条公开记录……`);
        }
      } else if (
        data && typeof data === "object" && Number.isFinite(Number(data.count))
        && /player_stats\//.test(url) && sameSelectedModes(url)
      ) {
        selectedTotal = Number(data.count);
      }
    };

    const nativeFetch = window.fetch;
    window.fetch = async function patchedKoromoFetch(...args) {
      const response = await nativeFetch.apply(this, args);
      try {
        const url = String(response.url || args[0]?.url || args[0] || "");
        response.clone().json().then((data) => rememberResponse(data, url)).catch(() => {});
      } catch (_) {}
      return response;
    };

    const encodeAccountId = (value) => (
      1358437n + ((7n * BigInt(value) + 1117113n) ^ 86216345n)
    ).toString();

    const playerData = (record) => {
      const indexed = (record.players || []).map((player, index) => ({ player, index }));
      const target = indexed.find(({ player }) => Number(player.accountId) === accountId);
      if (!target) return null;
      const ranked = [...indexed].sort((left, right) => (
        Number(right.player.score || 0) + 5 - right.index
      ) - (
        Number(left.player.score || 0) + 5 - left.index
      ));
      return {
        player: target.player,
        placement: ranked.findIndex(({ index }) => index === target.index) + 1,
      };
    };

    const numberOrBlank = (value) => (
      value === null || value === undefined || value === "" ? "" : Number(value)
    );

    const buildSafePayload = () => {
      const selected = [...records.values()]
        .filter((record) => [9, 12, 16].includes(Number(record.modeId)))
        .sort((left, right) => Number(right.startTime || 0) - Number(left.startTime || 0));
      const limited = requestedLimit ? selected.slice(0, requestedLimit) : selected;
      const safeRecords = limited.map((record) => {
        const target = playerData(record);
        if (!target) return null;
        return {
          uuid: String(record.uuid),
          start_time: Number(record.startTime || 0),
          end_time: Number(record.endTime || 0),
          mode_id: Number(record.modeId || 0),
          record_type: "ranked",
          placement: target.placement,
          final_score: numberOrBlank(target.player.score),
          pt_delta: numberOrBlank(target.player.gradingScore),
          player_level: numberOrBlank(target.player.level),
          player_level_score: "",
          paipu_url: `https://game.maj-soul.com/1/?paipu=${record.uuid}_a${encodeAccountId(accountId)}`,
        };
      }).filter(Boolean);
      return {
        schema: "batchmortal-majsoul-links-v1",
        version: VERSION,
        exported_at: new Date().toISOString(),
        source_origin: location.origin,
        scope: requestedLimit ? `latest-${requestedLimit}` : "all",
        modes: [...selectedModes],
        account_id: accountId,
        count: safeRecords.length,
        records: safeRecords,
      };
    };

    const downloadBridgeFile = () => {
      if (!preparedPayload) return;
      const blob = new Blob([JSON.stringify(preparedPayload, null, 2)], {
        type: "application/json;charset=utf-8",
      });
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `koromo-bridge-${bridgeToken}.json`;
      document.documentElement.appendChild(anchor);
      anchor.click();
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setBridgeStatus(`已生成 ${preparedPayload.count} 局安全 JSON，Windows 正在接收。`);
    };

    const finish = () => {
      if (finished || !records.size) return;
      preparedPayload = buildSafePayload();
      if (!preparedPayload.records.length) {
        setBridgeStatus("记录中没有该玩家可用的四人南场牌谱。", true);
        return;
      }
      finished = true;
      if (downloadButton) downloadButton.disabled = false;
      downloadBridgeFile();
    };

    const mountBridgeUi = () => {
      const host = document.createElement("div");
      host.style.cssText = "position:fixed;right:16px;top:72px;z-index:2147483647";
      const root = host.attachShadow({ mode: "open" });
      root.innerHTML = `
        <style>
          .panel{width:300px;padding:13px;color:#fff;background:rgba(20,28,40,.95);border-radius:9px;box-shadow:0 8px 28px rgba(0,0,0,.35);font:13px/1.5 "Microsoft YaHei",sans-serif}
          h3{margin:0 0 7px;font-size:15px}.status{min-height:42px;padding:7px;background:rgba(255,255,255,.08);border-radius:6px}
          button{width:100%;margin-top:8px;padding:7px;border:0;border-radius:6px;background:#58a56b;color:#fff;cursor:pointer}button:disabled{opacity:.5}.note{margin-top:7px;color:#9fb0c4;font-size:11px}
        </style>
        <section class="panel"><h3>发送牌谱到 Windows</h3><div class="status">等待牌谱屋完成验证并返回公开记录……</div><button disabled>再次下载安全 JSON</button><div class="note">不读取或保存 CAP 令牌、Cookie、登录信息。</div></section>`;
      document.documentElement.appendChild(host);
      statusElement = root.querySelector(".status");
      downloadButton = root.querySelector("button");
      downloadButton.addEventListener("click", downloadBridgeFile);
    };

    if (document.documentElement) mountBridgeUi();
    else document.addEventListener("DOMContentLoaded", mountBridgeUi, { once: true });

    setInterval(() => {
      if (finished) return;
      if (!requestedLimit && records.size) {
        window.scrollTo(0, Math.max(document.body.scrollHeight, document.documentElement.scrollHeight));
      }
      const quiet = lastRecordAt && Date.now() - lastRecordAt > 2500;
      if (requestedLimit && recordResponses && records.size && quiet) finish();
      if (!requestedLimit && selectedTotal !== null && records.size >= selectedTotal) finish();
      if (recordResponses && !records.size && quiet) {
        setBridgeStatus("牌谱屋没有返回这个 ID/房间的公开记录。", true);
      }
    }, 700);
  }

  if (location.hostname === "amae-koromo.sapk.ch") {
    runKoromoBridge();
    return;
  }
  const RECENT_COUNT = 100;
  const PAGE_COUNT = 10;
  const LEGACY_PAGE_COUNT = 100;
  const PAGE_DELAY_MS = 180;
  const MAX_PAGE_REQUESTS = 10000;
  const REQUEST = 2;
  const RESPONSE = 3;
  const LEGACY_LIST = "fetchGameRecordList";
  const V2_LIST = "fetchGameRecordListV2";
  const V2_NEXT = "fetchNextGameRecordList";
  const LOGIN_METHODS = new Set(["login", "emailLogin", "oauth2Login"]);
  const SAFE_METHODS = new Set([...LOGIN_METHODS, LEGACY_LIST, V2_LIST, V2_NEXT]);
  const MORTAL_HANCHAN_MODE_IDS = new Set([2, 5, 9, 12, 16]);
  const V2_RECORD_TYPES = new Map([
    [1, "ranked"],
    [2, "friend"],
    [3, "match"],
    [4, "activity"],
  ]);
  const V2_QUERY_TAGS = { all: 0, ranked: 1, friend: 2 };
  const LEGACY_QUERY_TYPES = { all: 0, ranked: 2, friend: 1 };
  const textDecoder = new TextDecoder("utf-8");
  const textEncoder = new TextEncoder();
  const state = {
    accountId: 0,
    socket: null,
    nativeSend: null,
    inflight: new Map(),
    privateRequests: new Map(),
    nextPrivateId: 62000,
    records: new Map(),
    v2Token: "",
    scope: "recent",
    loadedScope: "",
    recordTypeFilter: "ranked",
    modeFilter: 0,
    daysFilter: 0,
    pagesRead: 0,
    truncated: false,
    status: "等待雀魂网页建立登录连接……",
    statusError: false,
    busy: false,
    ui: null,
  };

  function asBytes(value) {
    if (value instanceof ArrayBuffer) return new Uint8Array(value);
    if (ArrayBuffer.isView(value)) {
      return new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
    }
    return null;
  }

  function readVarint(bytes, cursor) {
    let value = 0n;
    let shift = 0n;
    let position = cursor;
    for (let count = 0; count < 10 && position < bytes.length; count += 1) {
      const current = bytes[position++];
      value |= BigInt(current & 0x7f) << shift;
      if ((current & 0x80) === 0) {
        return [
          value <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(value) : value,
          position,
        ];
      }
      shift += 7n;
    }
    throw new Error("无效的 protobuf varint");
  }

  function readFields(bytes) {
    const fields = [];
    let cursor = 0;
    while (cursor < bytes.length) {
      let tag;
      [tag, cursor] = readVarint(bytes, cursor);
      const numericTag = Number(tag);
      const field = Math.floor(numericTag / 8);
      const wire = numericTag & 7;
      if (!field) throw new Error("无效的 protobuf 字段");
      if (wire === 0) {
        let value;
        [value, cursor] = readVarint(bytes, cursor);
        fields.push({ field, wire, value });
      } else if (wire === 2) {
        let length;
        [length, cursor] = readVarint(bytes, cursor);
        length = Number(length);
        const end = cursor + length;
        if (length < 0 || end > bytes.length) throw new Error("protobuf 字段越界");
        fields.push({ field, wire, value: bytes.slice(cursor, end) });
        cursor = end;
      } else if (wire === 1) {
        cursor += 8;
      } else if (wire === 5) {
        cursor += 4;
      } else {
        throw new Error(`暂不支持的 protobuf wire type: ${wire}`);
      }
      if (cursor > bytes.length) throw new Error("protobuf 数据越界");
    }
    return fields;
  }

  function firstField(fields, field, wire) {
    return fields.find((item) => item.field === field && item.wire === wire)?.value;
  }

  function repeatedFields(fields, field, wire) {
    return fields.filter((item) => item.field === field && item.wire === wire);
  }

  function decodeString(value) {
    return value ? textDecoder.decode(value) : "";
  }

  function decodeWrapper(bytes) {
    const fields = readFields(bytes);
    return {
      name: decodeString(firstField(fields, 1, 2)),
      data: firstField(fields, 2, 2) || new Uint8Array(),
    };
  }

  function looksLikeUuid(value) {
    return /^[A-Za-z0-9-]{16,}$/.test(String(value || ""));
  }

  function decodeLegacyAccount(bytes) {
    const fields = readFields(bytes);
    return {
      accountId: Number(firstField(fields, 1, 0) || 0),
      seat: Number(firstField(fields, 2, 0) || 0),
      placement: null,
      finalScore: null,
      ptDelta: null,
      playerLevel: null,
      playerLevelScore: null,
    };
  }

  function decodeSignedVarint(value) {
    if (typeof value === "bigint") {
      const signed = value >= (1n << 63n) ? value - (1n << 64n) : value;
      return Number(signed);
    }
    return Number(value || 0);
  }

  function decodeAccountLevel(value) {
    if (!value) return { id: null, score: null };
    const fields = readFields(value);
    return {
      id: Number(firstField(fields, 1, 0) || 0) || null,
      score: Number(firstField(fields, 2, 0) || 0),
    };
  }

  function decodeV2Account(bytes) {
    const fields = readFields(bytes);
    const level = decodeAccountLevel(firstField(fields, 4, 2));
    return {
      accountId: Number(firstField(fields, 2, 0) || 0),
      placement: Number(firstField(fields, 1, 0) || 0) || null,
      finalScore: decodeSignedVarint(firstField(fields, 8, 0)),
      ptDelta: decodeSignedVarint(firstField(fields, 7, 0)),
      playerLevel: level.id,
      playerLevelScore: level.score,
    };
  }

  function decodeRecord(bytes) {
    const fields = readFields(bytes);
    const v2Uuid = decodeString(firstField(fields, 2, 2));
    if (looksLikeUuid(v2Uuid)) {
      return {
        uuid: v2Uuid,
        startTime: Number(firstField(fields, 3, 0) || 0),
        endTime: Number(firstField(fields, 4, 0) || 0),
        modeId: Number(firstField(fields, 6, 0) || 0),
        recordType: V2_RECORD_TYPES.get(Number(firstField(fields, 5, 0) || 0)) || "unknown",
        accounts: repeatedFields(fields, 7, 2).map((item) => decodeV2Account(item.value)),
      };
    }
    return {
      uuid: decodeString(firstField(fields, 1, 2)),
      startTime: Number(firstField(fields, 2, 0) || 0),
      endTime: Number(firstField(fields, 3, 0) || 0),
      modeId: 0,
      recordType: "unknown",
      accounts: repeatedFields(fields, 11, 2).map((item) => decodeLegacyAccount(item.value)),
    };
  }

  function decodeLegacyList(bytes) {
    const fields = readFields(bytes);
    return {
      total: Number(firstField(fields, 2, 0) || 0),
      records: repeatedFields(fields, 3, 2)
        .map((item) => decodeRecord(item.value))
        .filter((item) => looksLikeUuid(item.uuid)),
    };
  }

  function decodeV2Init(bytes) {
    const fields = readFields(bytes);
    return { token: decodeString(firstField(fields, 2, 2)) };
  }

  function decodeV2Page(bytes) {
    const fields = readFields(bytes);
    return {
      hasMore: Number(firstField(fields, 2, 0) || 0) === 1,
      records: repeatedFields(fields, 3, 2)
        .map((item) => decodeRecord(item.value))
        .filter((item) => looksLikeUuid(item.uuid)),
    };
  }

  function encodeVarint(value) {
    let current = Number(value) >>> 0;
    const output = [];
    do {
      let byte = current & 0x7f;
      current >>>= 7;
      if (current) byte |= 0x80;
      output.push(byte);
    } while (current);
    return output;
  }

  function encodeUintField(field, value) {
    return [...encodeVarint(field * 8), ...encodeVarint(value)];
  }

  function encodeBytesField(field, bytes) {
    return [...encodeVarint(field * 8 + 2), ...encodeVarint(bytes.length), ...bytes];
  }

  function encodeRpcWrapper(methodName, payload) {
    const method = textEncoder.encode(`.lq.Lobby.${methodName}`);
    return new Uint8Array([
      ...encodeBytesField(1, method),
      ...encodeBytesField(2, payload),
    ]);
  }

  function encodeV2ListRequest(recordType) {
    const queryTag = V2_QUERY_TAGS[recordType] ?? V2_QUERY_TAGS.ranked;
    const payload = new Uint8Array([
      ...encodeUintField(1, queryTag),
      ...encodeUintField(2, 0),
      ...encodeUintField(3, 0),
      ...[1, 2, 3, 4].flatMap((value) => encodeUintField(4, value)),
      ...[3, 4].flatMap((value) => encodeUintField(5, value)),
      ...encodeUintField(6, 0),
      ...[1, 2, 3, 4, 6].flatMap((value) => encodeUintField(7, value)),
    ]);
    return encodeRpcWrapper(V2_LIST, payload);
  }

  function encodeV2NextRequest(token) {
    const payload = new Uint8Array([
      ...encodeBytesField(1, textEncoder.encode(token)),
      ...encodeUintField(2, PAGE_COUNT),
    ]);
    return encodeRpcWrapper(V2_NEXT, payload);
  }

  function discoverLegacyAccountId() {
    const candidates = [
      window.GameMgr?.Inst?.account_id,
      window.app?.GameMgr?.Inst?.account_id,
      window.app?.PlayerManager?.Inst?.account_id,
      window.app?.PlayerManager?.account_id,
    ];
    const value = candidates.map(Number).find((item) => Number.isSafeInteger(item) && item > 0);
    if (value) state.accountId = value;
  }

  function inferAccountId() {
    if (state.accountId || state.records.size < 2) return;
    const counts = new Map();
    for (const record of state.records.values()) {
      for (const account of record.accounts) {
        if (!account.accountId) continue;
        counts.set(account.accountId, (counts.get(account.accountId) || 0) + 1);
      }
    }
    const ranked = [...counts.entries()].sort((left, right) => right[1] - left[1]);
    if (ranked[0] && ranked[0][1] === state.records.size) state.accountId = ranked[0][0];
  }

  function ingestRecords(records) {
    for (const record of records || []) {
      if (!looksLikeUuid(record?.uuid)) continue;
      state.records.set(record.uuid, record);
    }
    discoverLegacyAccountId();
    inferAccountId();
    render();
  }

  function setStatus(message, isError = false) {
    state.status = message;
    state.statusError = isError;
    render();
  }

  function acc2match(accountId) {
    return (((7n * BigInt(accountId) + 1117113n) ^ 86216345n) + 1358437n).toString();
  }

  function isMortalHanchan(record) {
    const modeId = Number(record?.modeId || 0);
    const recordType = String(record?.recordType || "unknown");
    if (recordType === "ranked") return MORTAL_HANCHAN_MODE_IDS.has(modeId);
    if (recordType === "friend") return modeId === 2;
    if (recordType === "match" || recordType === "activity") return false;
    return modeId <= 0 || MORTAL_HANCHAN_MODE_IDS.has(modeId);
  }

  function matchesSelectedFilters(record) {
    if (!isMortalHanchan(record)) return false;
    const recordType = String(record?.recordType || "unknown");
    if (state.recordTypeFilter === "ranked" && recordType !== "ranked") return false;
    if (state.recordTypeFilter === "friend" && recordType !== "friend") return false;
    if (state.recordTypeFilter === "all" && !["ranked", "friend"].includes(recordType)) return false;
    const modeId = Number(record?.modeId || 0);
    if (state.recordTypeFilter === "ranked" && state.modeFilter && modeId !== state.modeFilter) return false;
    if (state.daysFilter) {
      const startTime = Number(record?.startTime || 0);
      const cutoff = Math.floor(Date.now() / 1000) - state.daysFilter * 86400;
      if (!startTime || startTime < cutoff) return false;
    }
    return true;
  }

  function readUiFilters() {
    state.scope = state.ui?.scope?.value === "all" ? "all" : "recent";
    state.recordTypeFilter = ["ranked", "friend", "all"].includes(state.ui?.recordType?.value)
      ? state.ui.recordType.value
      : "ranked";
    state.modeFilter = Number(state.ui?.mode?.value || 0);
    if (state.recordTypeFilter !== "ranked") state.modeFilter = 0;
    state.daysFilter = Number(state.ui?.days?.value || 0);
  }

  function eligibleRecordCount() {
    let count = 0;
    for (const record of state.records.values()) {
      if (matchesSelectedFilters(record)) count += 1;
    }
    return count;
  }

  function sortedRecords() {
    const records = [...state.records.values()]
      .filter((record) => matchesSelectedFilters(record))
      .sort((left, right) => (right.startTime || 0) - (left.startTime || 0));
    return state.loadedScope === "all" ? records : records.slice(0, RECENT_COUNT);
  }

  function scopeLabel(scope = state.scope) {
    return scope === "all" ? "全部" : `最近 ${RECENT_COUNT} 局`;
  }

  function recordTypeLabel(recordType = state.recordTypeFilter) {
    if (recordType === "friend") return "友人场";
    if (recordType === "all") return "段位场和友人场";
    return "段位场";
  }

  function shouldReadNextPage(page) {
    if (!page.hasMore || !page.records.length) return false;
    if (state.pagesRead >= MAX_PAGE_REQUESTS) {
      state.truncated = true;
      return false;
    }
    return state.scope === "all" || eligibleRecordCount() < RECENT_COUNT;
  }

  function shareUrl(record) {
    const suffix = state.accountId ? `_a${acc2match(state.accountId)}` : "";
    return `https://game.maj-soul.com/1/?paipu=${record.uuid}${suffix}`;
  }

  function ensureAccountId() {
    if (state.accountId) return true;
    const answer = window.prompt(
      "未能自动识别你的数字账号 ID。请输入账号 ID，以便生成正确的本人视角链接：",
      "",
    );
    const parsed = Number(answer);
    if (!Number.isSafeInteger(parsed) || parsed <= 0) {
      setStatus("未填写有效账号 ID，已取消下载。", true);
      return false;
    }
    state.accountId = parsed;
    return true;
  }

  function downloadForWindows() {
    if (!state.loadedScope) {
      setStatus("请先选择读取范围并完成读取。", true);
      return;
    }
    const records = sortedRecords();
    if (!records.length) {
      setStatus("没有读取到 Mortal 支持的四人半庄牌谱。", true);
      return;
    }
    if (!ensureAccountId()) return;
    const payload = {
      schema: "batchmortal-majsoul-links-v1",
      version: VERSION,
      exported_at: new Date().toISOString(),
      source_origin: location.origin,
      scope: state.loadedScope,
      filters: {
        record_type: state.recordTypeFilter,
        mode_id: state.modeFilter || 0,
        recent_days: state.daysFilter || 0,
      },
      account_id: state.accountId,
      count: records.length,
      records: records.map((record) => {
        const player = record.accounts.find(
          (account) => Number(account.accountId) === Number(state.accountId),
        );
        return {
          uuid: record.uuid,
          start_time: record.startTime || 0,
          end_time: record.endTime || 0,
          mode_id: record.modeId || 0,
          record_type: record.recordType || "unknown",
          placement: player?.placement ?? "",
          final_score: player?.finalScore ?? "",
          pt_delta: player?.ptDelta ?? "",
          player_level: player?.playerLevel ?? "",
          player_level_score: player?.playerLevelScore ?? "",
          paipu_url: shareUrl(record),
        };
      }),
    };
    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    const scopeName = state.loadedScope === "all" ? "all" : "recent";
    anchor.download = `majsoul-${state.recordTypeFilter}-${scopeName}-${records.length}-for-windows-${new Date()
      .toISOString()
      .slice(0, 10)}.json`;
    document.documentElement.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setStatus(`已下载 ${records.length} 局四人半庄的 Windows 专用 JSON。`);
  }

  function legacyRecords(response, recordType) {
    const source = response?.record_list || response?.recordList || [];
    return source.map((record) => {
      const category = Number(record.config?.category || 0);
      const detectedType = ({ 1: "friend", 2: "ranked", 4: "activity" })[category]
        || (recordType === "all" ? "unknown" : recordType);
      const modeId = detectedType === "ranked"
        ? Number(record.config?.meta?.mode_id || 0)
        : Number(record.config?.mode?.mode || 0);
      return {
        uuid: String(record.uuid || ""),
        startTime: Number(record.start_time || record.startTime || 0),
        endTime: Number(record.end_time || record.endTime || 0),
        modeId,
        recordType: detectedType,
        accounts: (record.accounts || record.players || []).map((account) => ({
          accountId: Number(account.account_id || account.accountId || 0),
          seat: Number(account.seat || 0),
        })),
      };
    });
  }

  function fetchLegacyPage(sender, start) {
    state.pagesRead += 1;
    sender.call(
      window.app.NetAgent,
      "Lobby",
      LEGACY_LIST,
      {
        start,
        count: LEGACY_PAGE_COUNT,
        type: LEGACY_QUERY_TYPES[state.recordTypeFilter] ?? LEGACY_QUERY_TYPES.ranked,
      },
      (error, response) => {
        if (error) {
          state.busy = false;
          setStatus(`网页接口返回错误：${error.message || error}`, true);
          return;
        }
        const pageRecords = legacyRecords(response, state.recordTypeFilter);
        ingestRecords(pageRecords);
        const total = Number(response?.total || response?.total_count || response?.totalCount || 0);
        const nextStart = start + pageRecords.length;
        const hasMore = pageRecords.length > 0 && (total ? nextStart < total : pageRecords.length >= LEGACY_PAGE_COUNT);
        const continueReading = hasMore
          && state.pagesRead < MAX_PAGE_REQUESTS
          && (state.scope === "all" || eligibleRecordCount() < RECENT_COUNT);
        if (continueReading) {
          setStatus(`已找到 ${eligibleRecordCount()} 局符合筛选，继续读取${scopeLabel()}……`);
          setTimeout(() => fetchLegacyPage(sender, nextStart), PAGE_DELAY_MS);
        } else {
          state.truncated = state.scope === "all" && hasMore;
          finishReading();
        }
      },
    );
  }

  function tryLegacyRequest() {
    const sender = window.app?.NetAgent?.sendReq2Lobby;
    if (typeof sender !== "function") return false;
    state.busy = true;
    setStatus(`正在通过旧版网页会话读取${scopeLabel()}${recordTypeLabel()}……`);
    fetchLegacyPage(sender, 0);
    return true;
  }

  function allocatePrivateId() {
    for (let attempts = 0; attempts < 1000; attempts += 1) {
      state.nextPrivateId = state.nextPrivateId >= 65534 ? 62000 : state.nextPrivateId + 1;
      if (!state.inflight.has(state.nextPrivateId) && !state.privateRequests.has(state.nextPrivateId)) {
        return state.nextPrivateId;
      }
    }
    throw new Error("无法分配网页请求编号");
  }

  function sendPrivate(kind, wrapper) {
    if (!state.socket || !state.nativeSend || state.socket.readyState !== WebSocket.OPEN) {
      state.busy = false;
      setStatus("尚未连接雀魂大厅。请登录后完整刷新网页再试。", true);
      return;
    }
    const requestId = allocatePrivateId();
    const frame = new Uint8Array(3 + wrapper.length);
    frame[0] = REQUEST;
    frame[1] = requestId & 0xff;
    frame[2] = requestId >> 8;
    frame.set(wrapper, 3);
    state.privateRequests.set(requestId, { kind });
    state.socket.binaryType = "arraybuffer";
    state.nativeSend(frame.buffer);
  }

  function sendV2Next() {
    if (!state.v2Token) throw new Error("牌谱查询令牌为空");
    sendPrivate("v2-next", encodeV2NextRequest(state.v2Token));
  }

  function requestRecords() {
    if (state.busy) return;
    discoverLegacyAccountId();
    readUiFilters();
    state.records.clear();
    state.v2Token = "";
    state.loadedScope = "";
    state.pagesRead = 0;
    state.truncated = false;
    if (tryLegacyRequest()) return;
    if (!state.socket) {
      setStatus("未检测到大厅连接。请启用脚本后完整刷新雀魂网页并重新登录。", true);
      return;
    }
    state.busy = true;
    setStatus(`正在使用 2026 版网页协议读取${scopeLabel()}${recordTypeLabel()}四人半庄……`);
    sendPrivate("v2-init", encodeV2ListRequest(state.recordTypeFilter));
  }

  function finishReading() {
    state.busy = false;
    state.loadedScope = state.scope;
    const eligibleCount = eligibleRecordCount();
    const hanchanCount = [...state.records.values()].filter(isMortalHanchan).length;
    const skippedModeCount = state.records.size - hanchanCount;
    const filteredCount = hanchanCount - eligibleCount;
    if (eligibleCount) {
      const exportedCount = state.loadedScope === "all"
        ? eligibleCount
        : Math.min(eligibleCount, RECENT_COUNT);
      const truncatedText = state.truncated ? "；达到安全翻页上限，结果可能不完整" : "";
      setStatus(
        `已读取 ${exportedCount} 局符合筛选；另排除 ${filteredCount} 局筛选外半庄、${skippedModeCount} 局其他模式${truncatedText}。`,
      );
    } else {
      setStatus("登录会话没有返回 Mortal 支持的四人半庄牌谱。", true);
    }
  }

  function handlePrivateResponse(event, bytes, requestId) {
    event.stopImmediatePropagation();
    const request = state.privateRequests.get(requestId);
    state.privateRequests.delete(requestId);
    try {
      const wrapper = decodeWrapper(bytes.slice(3));
      if (request.kind === "v2-init") {
        state.v2Token = decodeV2Init(wrapper.data).token;
        if (!state.v2Token) throw new Error("网页没有返回 V2 牌谱查询令牌");
        sendV2Next();
        return;
      }
      if (request.kind === "v2-next") {
        const page = decodeV2Page(wrapper.data);
        state.pagesRead += 1;
        ingestRecords(page.records);
        if (shouldReadNextPage(page)) {
          setStatus(`已找到 ${eligibleRecordCount()} 局符合筛选，继续读取${scopeLabel()}……`);
          setTimeout(sendV2Next, PAGE_DELAY_MS);
        } else {
          finishReading();
        }
      }
    } catch (error) {
      state.busy = false;
      setStatus(`无法解析 2026 版牌谱列表：${error.message}`, true);
    }
  }

  function handleKnownResponse(bytes, methodName) {
    try {
      const wrapper = decodeWrapper(bytes.slice(3));
      if (LOGIN_METHODS.has(methodName)) {
        const fields = readFields(wrapper.data);
        const accountId = Number(firstField(fields, 2, 0) || 0);
        if (accountId) state.accountId = accountId;
      } else if (methodName === LEGACY_LIST) {
        ingestRecords(
          decodeLegacyList(wrapper.data).records.map((record) => ({
            ...record,
            recordType: state.recordTypeFilter === "all" ? "unknown" : state.recordTypeFilter,
          })),
        );
      } else if (methodName === V2_NEXT) {
        ingestRecords(decodeV2Page(wrapper.data).records);
        if (!state.busy && state.records.size) {
          state.loadedScope = "";
          setStatus(`已被动识别 ${state.records.size} 局；请选择范围并点击“开始读取”。`);
        }
      }
      render();
    } catch (error) {
      console.debug("[Majsoul recent exporter] safe response parse skipped:", error);
    }
  }

  function captureOutgoing(data, socket, originalSend) {
    const bytes = asBytes(data);
    if (!bytes || bytes.length < 4 || bytes[0] !== REQUEST) return;
    try {
      const requestId = bytes[1] | (bytes[2] << 8);
      const wrapper = decodeWrapper(bytes.slice(3));
      const methodName = wrapper.name.split(".").filter(Boolean).pop() || wrapper.name;
      if (!SAFE_METHODS.has(methodName)) return;
      // Only the method name is retained. OAuth codes, tokens, request payloads,
      // cookies, and raw WebSocket frames are never saved by this script.
      state.inflight.set(requestId, methodName);
      state.socket = socket;
      state.nativeSend = originalSend;
    } catch (error) {
      console.debug("[Majsoul recent exporter] request header parse skipped:", error);
    }
  }

  function captureIncoming(event) {
    const bytes = asBytes(event.data);
    if (!bytes || bytes.length < 3 || bytes[0] !== RESPONSE) return;
    const requestId = bytes[1] | (bytes[2] << 8);
    if (state.privateRequests.has(requestId)) {
      handlePrivateResponse(event, bytes, requestId);
      return;
    }
    const methodName = state.inflight.get(requestId);
    if (!methodName) return;
    state.inflight.delete(requestId);
    handleKnownResponse(bytes, methodName);
  }

  function hookSocket(socket) {
    const originalSend = socket.send.bind(socket);
    socket.addEventListener("message", captureIncoming);
    socket.send = function patchedSend(data) {
      captureOutgoing(data, socket, originalSend);
      return originalSend(data);
    };
    socket.addEventListener("open", () => {
      state.socket = socket;
      state.nativeSend = originalSend;
      setStatus("已连接雀魂网页会话；登录完成后可读取最近 100 局或全部历史。");
    });
    socket.addEventListener("close", () => {
      if (state.socket === socket) {
        state.socket = null;
        state.nativeSend = null;
        state.busy = false;
        setStatus("雀魂大厅连接已关闭；刷新页面后可重新连接。", true);
      }
    });
  }

  const NativeWebSocket = window.WebSocket;
  window.WebSocket = new Proxy(NativeWebSocket, {
    construct(Target, argumentsList, NewTarget) {
      const socket = Reflect.construct(Target, argumentsList, NewTarget);
      hookSocket(socket);
      return socket;
    },
  });

  function render() {
    if (!state.ui) return;
    const eligibleCount = eligibleRecordCount();
    const selectedScope = state.ui.scope?.value === "all" ? "all" : "recent";
    const count = selectedScope === "all" ? eligibleCount : Math.min(eligibleCount, RECENT_COUNT);
    state.ui.count.textContent = selectedScope === "all" ? String(count) : `${count} / ${RECENT_COUNT}`;
    state.ui.account.textContent = state.accountId ? String(state.accountId) : "自动识别中";
    state.ui.status.textContent = state.status;
    state.ui.status.style.color = state.statusError ? "#ff9d9d" : "#d9e4f2";
    state.ui.read.disabled = state.busy;
    state.ui.scope.disabled = state.busy;
    state.ui.recordType.disabled = state.busy;
    state.ui.mode.disabled = state.busy || state.ui.recordType.value !== "ranked";
    state.ui.days.disabled = state.busy;
    state.ui.read.textContent = selectedScope === "all" ? "读取全部" : "读取最近 100 局";
    state.ui.download.disabled = !state.loadedScope || count === 0 || state.busy;
  }

  function mountUi() {
    if (document.getElementById("majsoul-recent-exporter-host")) return;
    const host = document.createElement("div");
    host.id = "majsoul-recent-exporter-host";
    host.style.cssText = "position:fixed;right:16px;top:72px;z-index:2147483647";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        .panel{width:320px;padding:14px;color:#fff;background:rgba(20,28,40,.94);border:1px solid rgba(255,255,255,.2);border-radius:10px;box-shadow:0 8px 28px rgba(0,0,0,.35);font:13px/1.5 "Microsoft YaHei",sans-serif}
        h3{margin:0 0 8px;font-size:16px}.row{display:flex;justify-content:space-between;align-items:center;margin:4px 0}.label{color:#aebed0}.value{font-weight:700}select{padding:4px 7px;border-radius:5px}.status{min-height:40px;margin:10px 0;padding:7px;background:rgba(255,255,255,.07);border-radius:6px}
        .actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}button{padding:8px;border:0;border-radius:6px;cursor:pointer;font:inherit}button:disabled{cursor:default;opacity:.5}.read{background:#4a8dde;color:#fff}.download{background:#58a56b;color:#fff}.note{margin-top:9px;color:#9fb0c4;font-size:11px}
      </style>
      <section class="panel">
        <h3>自己的牌谱</h3>
        <div class="row"><span class="label">读取范围</span><select class="scope"><option value="recent">最近 100 局</option><option value="all">全部四人半庄</option></select></div>
        <div class="row"><span class="label">对局类型</span><select class="record-type"><option value="ranked">仅段位场</option><option value="friend">仅友人场</option><option value="all">段位场 + 友人场</option></select></div>
        <div class="row"><span class="label">房间筛选</span><select class="mode"><option value="0">全部房间</option><option value="2">铜之间·南</option><option value="5">银之间·南</option><option value="9">金之间·南</option><option value="12">玉之间·南</option><option value="16">王座之间·南</option></select></div>
        <div class="row"><span class="label">时间筛选</span><select class="days"><option value="0">全部时间</option><option value="30">最近 30 天</option><option value="90">最近 90 天</option><option value="180">最近 180 天</option><option value="365">最近 1 年</option></select></div>
        <div class="row"><span class="label">符合筛选</span><span class="value count">0 / 100</span></div>
        <div class="row"><span class="label">账号 ID</span><span class="value account">自动识别中</span></div>
        <div class="status"></div>
        <div class="actions"><button class="read">读取最近 100 局</button><button class="download" disabled>下载给 Windows</button></div>
        <div class="note">“全部”会逐页读取，历史较多时需要等待。友人场只保留四人东南战；房间筛选只适用于段位场。不保存 OAuth、Cookie、token、昵称或原始登录帧。</div>
      </section>`;
    document.documentElement.appendChild(host);
    state.ui = {
      count: root.querySelector(".count"),
      account: root.querySelector(".account"),
      scope: root.querySelector(".scope"),
      recordType: root.querySelector(".record-type"),
      mode: root.querySelector(".mode"),
      days: root.querySelector(".days"),
      status: root.querySelector(".status"),
      read: root.querySelector(".read"),
      download: root.querySelector(".download"),
    };
    const changeFilter = () => {
      if (state.busy) return;
      readUiFilters();
      state.records.clear();
      state.loadedScope = "";
      setStatus("已切换读取范围，请点击“开始读取”。");
    };
    state.ui.scope.addEventListener("change", changeFilter);
    state.ui.recordType.addEventListener("change", () => {
      if (state.ui.recordType.value !== "ranked") state.ui.mode.value = "0";
      changeFilter();
    });
    state.ui.mode.addEventListener("change", changeFilter);
    state.ui.days.addEventListener("change", changeFilter);
    state.ui.read.addEventListener("click", requestRecords);
    state.ui.download.addEventListener("click", downloadForWindows);
    render();
  }

  if (document.documentElement) mountUi();
  else document.addEventListener("DOMContentLoaded", mountUi, { once: true });
})();
