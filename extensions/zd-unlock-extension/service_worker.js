const DEFAULT_BOT_BASE = 'https://yyhelp.zeabur.app';
const LEGACY_BOT_BASES = new Set();
const BLOCKED_BOT_BASE_HOSTS = new Set(['cs', 'arcs'].map((prefix) => `${prefix}help.zeabur.app`));
const SITE_9_PRIMARY_HOST = '9aynxg.hh9al.com';
const SITE_6_PRIMARY_HOST = '6aopna.fa69m.com';
const SITE_9_HOSTS = [
  SITE_9_PRIMARY_HOST,
  '9aynxg.hp9yk.com',
  '9aynxg.jr91e.com',
  '9aynxg.w77n3i.com',
  '9aynxg.ls3v0z.com'
];
const SITE_6_HOSTS = [
  SITE_6_PRIMARY_HOST,
  '6aopna.f26g7.com',
  '6aopna.fb6e5.com',
  '6aopna.30g7e1.com',
  '6aopna.a079a8.com'
];
const siteBackendUrl = (host, path) => `https://${host}${path}`;
const SITE_BACKEND_PATHS = {
  memberListUrl: '/central/admin/site/admin/v1/user/memberInfo/list',
  dataDecryptionUrl: '/central/admin/site/admin/v1/component/dataDecryption',
  unlockUrl: '/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
  loginErrorUrl: '/central/admin/site/admin/v1/user/memberInfo/clearLoginErrorRedisKey',
  proxyWhitelistUrl: '/central/admin/site/admin/v1/system/siteAccessManage/add',
  siteInnerMsgTemplateUrl: '/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
  siteInnerMsgAddUrl: '/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
  memberGameTotalInfoUrl: '/central/admin/site/admin/v1/user/game/totalInfo2',
  memberFinanceTotalAmountUrl: '/central/admin/site/admin/v1/user/finance/totalAmount2',
  loginLogUrl: '/central/admin/fd/admin/v1/risk/loginLog',
  bulletFrameLogUrl: '/central/admin/fd/admin/v1/risk/bulletFrameLog',
  gameWalletListUrl: '/central/admin/game/admin/v1/user/game/list',
  gameTransferOutUrl: '/central/admin/game/admin/v1/user/game/transferOut',
  gameTransferIntoUrl: '/central/admin/game/admin/v1/user/game/transferInto',
  venueQueryUrl: '/central/admin/game/admin/v1/venue/queryByName',
  rebateLevelListUrl: '/central/admin/act/admin/v1/fanshui/level/list',
  rebateLevelInfoListUrl: '/central/admin/act/admin/v1/fanshui/level/infoList',
  rebateLevelInfoSaveUrl: '/central/admin/act/admin/v1/fanshui/level/infoSave'
};
const SITE_URL_KEYS = [
  ...Object.keys(SITE_BACKEND_PATHS),
  'migrationRecordsUrl',
  'migrateMilanUrl'
];
const legacySiteHost = (site, token) => `${site}${['si', 'tebg'].join('')}.${token}.com`;
const LEGACY_SITE_HOST_REPLACEMENTS = new Map([
  [legacySiteHost('9', 'mvj4e7'), SITE_9_PRIMARY_HOST],
  [legacySiteHost('6', 'oj61i4'), SITE_6_PRIMARY_HOST]
]);
const siteBackendConfig = (host, keys = Object.keys(SITE_BACKEND_PATHS)) => Object.fromEntries(
  keys.map((key) => [key, siteBackendUrl(host, SITE_BACKEND_PATHS[key])])
);
const MERCHANT_ENDPOINT_PATHS = {
  merchantStatisticsUrl: '/admin/userReport/getStatistics',
  merchantTicketListUrl: '/admin/userReport/queryTicketList',
  merchantNoticeUrl: '/admin/noticeNew/notice',
  merchantNoticeDetailUrl: '/admin/noticeNew/noticeDetail',
  merchantSettlementListUrl: '/admin/settlement/queryNoSettleTicketList',
  merchantSettlementStatisticsUrl: '/admin/settlement/getStatistics',
  merchantSettlementApplyUrl: '/admin/settlement/sendMqSaveSettleInfo'
};

const DEFAULT_CONFIG = {
  botBase: DEFAULT_BOT_BASE,
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  ...siteBackendConfig(SITE_9_PRIMARY_HOST),
  migrationRecordsUrl: siteBackendUrl(SITE_6_PRIMARY_HOST, '/central/admin/site/admin/v1/pilgrimage/recordsV2'),
  migrateMilanUrl: siteBackendUrl(SITE_6_PRIMARY_HOST, '/central/admin/site/admin/v1/pilgrimage/migration'),
  merchantStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/getStatistics',
  merchantTicketListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/queryTicketList',
  merchantNoticeUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/notice',
  merchantNoticeDetailUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/noticeDetail',
  merchantSettlementListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/queryNoSettleTicketList',
  merchantSettlementStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/getStatistics',
  merchantSettlementApplyUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/sendMqSaveSettleInfo',
  value: '',
  headers: {
    accept: '*/*',
    'accept-language': 'zh-CN,zh;q=0.9',
    'content-type': 'application/json',
    'use-new-api': 'true'
  }
};

let polling = false;
let activeBackendCommands = 0;
const RECORDER_LIMIT = 400;
const RECORDER_BODY_LIMIT = 8000;
const RECORDER_RESPONSE_LIMIT = 20000;
const AUTH_SYNC_WAIT_MS = 12000;
const MERCHANT_RELOGIN_WAIT_MS = 90000;
const FETCH_RETRY_DELAYS_MS = [800, 1800, 3200];
const TRANSIENT_HTTP_RETRY_STATUSES = [502, 503, 504];
const MAX_ACTIVE_BACKEND_COMMANDS = 4;
const COMMAND_POLL_WAIT_SECONDS = 5;
const COMMAND_POLL_ALARM_MINUTES = 0.25;
const MERCHANT_URGE_MATCH_STATS_KEY = 'merchantUrgeMatchStatsV1';
const MERCHANT_URGE_MATCH_LIMIT = 2;
const MERCHANT_URGE_MATCH_TTL_MS = 24 * 60 * 60 * 1000;
const MERCHANT_URGE_BATCH_WAIT_MS = 6000;
const MERCHANT_URGE_BATCH_POLL_MS = 150;
const DEFAULT_DISABLE_DEVICE_TG_TARGET = '-1003511979135';
const merchantUrgeTelegramBatches = new Map();
const SITE_PROFILES = {
  '9001': {
    host: SITE_9_PRIMARY_HOST,
    authHosts: SITE_9_HOSTS,
    label: '9站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    ...siteBackendConfig(SITE_9_PRIMARY_HOST, Object.keys(SITE_BACKEND_PATHS).filter((key) => !['unlockUrl', 'loginErrorUrl', 'proxyWhitelistUrl'].includes(key))),
    siteInnerMsgClients: '0,1,2,3,8,9',
  },
  '6001': {
    host: SITE_6_PRIMARY_HOST,
    authHosts: SITE_6_HOSTS,
    label: '6站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    ...siteBackendConfig(SITE_6_PRIMARY_HOST, Object.keys(SITE_BACKEND_PATHS).filter((key) => !['unlockUrl', 'loginErrorUrl', 'proxyWhitelistUrl'].includes(key))),
    siteInnerMsgClients: '0,1,2,3,8',
  },
  'merchant': {
    host: 'api-merchant-backstage.dbsportxxxwo8.com',
    authHosts: ['merchant-own-backstage.dbsportxxxwo8.com', 'api-merchant-backstage.dbsportxxxwo8.com'],
    label: '场馆后台',
    requiredAuthHeaders: ['authorization', 'user-id'],
    merchantStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/getStatistics',
    merchantTicketListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/queryTicketList',
    merchantNoticeUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/notice',
    merchantNoticeDetailUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/noticeDetail',
    merchantSettlementListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/queryNoSettleTicketList',
    merchantSettlementStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/getStatistics',
    merchantSettlementApplyUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/sendMqSaveSettleInfo'
  }
};

function nowText() {
  return new Date().toLocaleString('zh-CN', { hour12: false });
}

function notify(title, message) {
  try {
    chrome.action.setBadgeBackgroundColor({ color: '#16a34a' });
    chrome.action.setBadgeText({ text: 'OK' });
    setTimeout(() => chrome.action.setBadgeText({ text: '' }), 6000);
  } catch {
    // badge is best effort
  }
  try {
    if (!chrome.notifications || !chrome.notifications.create) return;
    chrome.notifications.create({
      type: 'basic',
      iconUrl: chrome.runtime.getURL('icon-128.png'),
      title,
      message
    }, () => {
      // Reading lastError prevents an unchecked extension error if the host
      // blocks notifications or image loading fails.
      void chrome.runtime.lastError;
    });
  } catch {
    // notification is best effort
  }
}

function authHost(auth = {}) {
  try {
    return new URL(auth.href || '').host;
  } catch {
    return '';
  }
}

function siteFromCommand(action, cmd = {}) {
  const hint = String(cmd.backend_site || cmd.site || cmd.site_id || cmd.siteId || '').trim().toLowerCase();
  if (
    action === 'merchant_order_statistics'
    || action === 'urge_settlement'
    || action === 'query_ticket_cancel_reason'
    || action === 'venue_order_statistics'
    || action === 'merchant_order_query'
    || action === 'venue_order_query'
    || hint === '3'
    || hint === 'merchant'
    || hint === 'venue'
    || hint === 'stadium'
    || hint === 'changguan'
    || hint === '场馆'
  ) return 'merchant';
  if (action === 'migrate_milan' || hint === '6' || hint === '6001' || hint === '6zc' || hint === 'jn') return '6001';
  return '9001';
}

function profileForSite(site) {
  return SITE_PROFILES[String(site)] || SITE_PROFILES['9001'];
}

function actionHost(action, cmd = {}) {
  return profileForSite(siteFromCommand(action, cmd)).host;
}

function actionSite(action, cmd = {}) {
  return siteFromCommand(action, cmd);
}

function authMatches(auth, targetHost, targetSite, targetHosts = [targetHost]) {
  const headers = (auth && auth.headers) || {};
  const host = authHost(auth);
  const href = String((auth && auth.href) || '');
  return targetHosts.includes(host)
    || targetHosts.some((item) => href.includes(item))
    || String(headers['x-api-site'] || '') === targetSite;
}

function backendSiteFailureLabel(host) {
  const normalized = String(host || '').trim().toLowerCase();
  if (!normalized) return '';
  const replacement = LEGACY_SITE_HOST_REPLACEMENTS.get(normalized);
  if (SITE_9_HOSTS.includes(normalized) || normalized.startsWith('9aynxg.') || replacement === SITE_9_PRIMARY_HOST) return '9站接口请求失败';
  if (SITE_6_HOSTS.includes(normalized) || normalized.startsWith('6aopna.') || replacement === SITE_6_PRIMARY_HOST) return '6站接口请求失败';
  return '';
}

function profileForAuthOrigin(profile, auth) {
  const host = authHost(auth);
  const authHosts = profile.authHosts || [profile.host];
  if (!host || !authHosts.includes(host) || host === profile.host) return profile;
  const rewritten = { ...profile, host };
  for (const [key, value] of Object.entries(profile)) {
    if (!key.endsWith('Url') || typeof value !== 'string') continue;
    try {
      const parsed = new URL(value);
      if (parsed.host !== profile.host) continue;
      parsed.host = host;
      rewritten[key] = parsed.toString();
    } catch {
      // Keep non-URL values as-is.
    }
  }
  return rewritten;
}

function safeDecodeText(value) {
  const text = String(value || '').trim();
  if (!text) return '';
  try {
    return decodeURIComponent(text);
  } catch {
    return text;
  }
}

function pageAuthHeaders(auth = {}) {
  return (auth && auth.headers) || {};
}

function merchantAuthKey(auth = {}) {
  const headers = pageAuthHeaders(auth);
  const merchantName = safeDecodeText(headers.merchantname || headers.merchantName || '');
  const userId = String(headers['user-id'] || headers.userId || '').trim();
  if (!merchantName && !userId) return '';
  return `${merchantName || 'unknown'}|${userId || 'unknown'}`;
}

function merchantAuthLabel(authOrConfig = {}) {
  const headers = pageAuthHeaders(authOrConfig);
  const merchantName = safeDecodeText(headers.merchantname || headers.merchantName || '');
  const userId = String(headers['user-id'] || headers.userId || '').trim();
  if (merchantName && userId) return `${merchantName}(${userId})`;
  if (merchantName) return merchantName;
  if (userId) return `user-id ${userId}`;
  return '场馆账号';
}

function deepFindValue(input, names = [], depth = 0) {
  if (!input || typeof input !== 'object' || depth > 8) return '';
  const wanted = names.map((name) => String(name).toLowerCase());
  for (const [key, value] of Object.entries(input)) {
    if (wanted.includes(String(key).toLowerCase()) && value !== undefined && value !== null && typeof value !== 'object') {
      return String(value);
    }
  }
  for (const value of Object.values(input)) {
    const found = deepFindValue(value, names, depth + 1);
    if (found) return found;
  }
  return '';
}

function merchantAuthPriority(auth, cmd = {}, index = 0) {
  const label = merchantAuthLabel(auth).toLowerCase();
  const preferred = String(cmd.merchant_name || cmd.merchantName || cmd.venue_name || cmd.venueName || cmd.merchant || cmd.venue || '').trim().toLowerCase();
  if (preferred && label.includes(preferred)) return -100 + index / 1000;
  if (label.includes('冠名')) return index / 1000;
  if (label.includes('熊猫') || label.includes('panda')) return 1 + index / 1000;
  return 2 + index / 1000;
}

function limitedMerchantAuthStore(pageAuthByMerchant = {}, limit = 8) {
  return Object.fromEntries(
    Object.entries(pageAuthByMerchant)
      .sort((a, b) => String(b[1]?.capturedAt || '').localeCompare(String(a[1]?.capturedAt || '')))
      .slice(0, limit)
  );
}

function isAllowedMerchantEndpoint(key, url) {
  if (!MERCHANT_ENDPOINT_PATHS[String(key || '')]) return false;
  try {
    const parsed = new URL(String(url || ''));
    return parsed.protocol === 'https:' && parsed.hostname.endsWith('dbsportxxxwo8.com');
  } catch {
    return false;
  }
}

function merchantEndpointBaseFromUrl(url) {
  try {
    const parsed = new URL(String(url || ''));
    if (parsed.protocol !== 'https:' || !parsed.hostname.endsWith('dbsportxxxwo8.com')) return '';
    const matchedPath = Object.values(MERCHANT_ENDPOINT_PATHS).find((path) => parsed.pathname.endsWith(path));
    if (!matchedPath) return '';
    return `${parsed.origin}${parsed.pathname.slice(0, -matchedPath.length)}`;
  } catch {
    return '';
  }
}

function applyMerchantEndpointBase(config = {}) {
  const base = String(config.merchantEndpointBase || '').trim()
    || merchantEndpointBaseFromUrl(config.merchantTicketListUrl)
    || merchantEndpointBaseFromUrl(config.merchantStatisticsUrl)
    || merchantEndpointBaseFromUrl(config.merchantSettlementListUrl)
    || merchantEndpointBaseFromUrl(config.merchantNoticeUrl);
  if (!base) return config;
  const next = { ...config, merchantEndpointBase: base };
  for (const [key, path] of Object.entries(MERCHANT_ENDPOINT_PATHS)) {
    next[key] = `${base}${path}`;
  }
  return next;
}

async function getConfig() {
  const stored = await chrome.storage.local.get(['config', 'pageAuth', 'pageAuthByHost', 'pageAuthByMerchant']);
  const storedConfig = stored.config || {};
  let config = {
    ...DEFAULT_CONFIG,
    ...storedConfig,
    headers: {
      ...DEFAULT_CONFIG.headers,
      ...((storedConfig && storedConfig.headers) || {})
    },
    pageAuth: stored.pageAuth || null,
    pageAuthByHost: stored.pageAuthByHost || {},
    pageAuthByMerchant: stored.pageAuthByMerchant || {}
  };
  config = normalizeConfig(config);
  const normalizedStoredConfig = normalizeConfig(storedConfig, { normalizeEmptyBotBase: false });
  if (JSON.stringify(normalizedStoredConfig) !== JSON.stringify(storedConfig)) {
    await chrome.storage.local.set({ config: normalizedStoredConfig });
  }
  return {
    enabled: true,
    config
  };
}

function normalizeBotBase(value) {
  const raw = String(value || '').trim().replace(/\/+$/, '');
  if (!raw || LEGACY_BOT_BASES.has(raw)) return DEFAULT_BOT_BASE;
  try {
    if (BLOCKED_BOT_BASE_HOSTS.has(new URL(raw).host)) return DEFAULT_BOT_BASE;
  } catch {
    // Keep existing fallback behavior for non-URL custom values.
  }
  return raw;
}

function normalizeBackendUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return raw;
  try {
    const parsed = new URL(raw);
    const replacement = LEGACY_SITE_HOST_REPLACEMENTS.get(parsed.host.toLowerCase());
    if (!replacement) return raw;
    parsed.host = replacement;
    return parsed.toString();
  } catch {
    return raw;
  }
}

function normalizeConfig(config = {}, options = {}) {
  const next = { ...config };
  if (options.normalizeEmptyBotBase !== false || Object.prototype.hasOwnProperty.call(next, 'botBase')) {
    next.botBase = normalizeBotBase(next.botBase);
  }
  for (const key of SITE_URL_KEYS) {
    if (typeof next[key] === 'string') next[key] = normalizeBackendUrl(next[key]);
  }
  return next;
}

function uniqueAuthCandidates(candidates = []) {
  const seen = new Set();
  const out = [];
  for (const auth of candidates) {
    if (!auth) continue;
    const headers = pageAuthHeaders(auth);
    const key = merchantAuthKey(auth)
      || `${authHost(auth)}|${auth.href || ''}|${headers['x-api-site'] || ''}|${headers['x-api-user'] || ''}|${headers.authorization || ''}|${headers['user-id'] || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(auth);
  }
  return out;
}

function authConfigsForAction(config, action, cmd = {}) {
  const targetSite = actionSite(action, cmd);
  const profile = profileForSite(targetSite);
  const targetHost = profile.host;
  const targetHosts = profile.authHosts || [targetHost];
  const requiredAuthHeaders = profile.requiredAuthHeaders || ['x-api-token', 'x-api-user'];
  const byHost = config.pageAuthByHost || {};
  const byMerchant = config.pageAuthByMerchant || {};
  const currentAuth = config.pageAuth || null;
  const merchantAuths = Object.values(byMerchant);
  const candidates = targetSite === 'merchant'
    ? [
        ...merchantAuths,
        byHost[targetSite],
        byHost[targetHost],
        ...targetHosts.map((host) => byHost[host]),
        currentAuth,
        ...Object.values(byHost)
      ]
    : [
        byHost[targetHost],
        ...targetHosts.map((host) => byHost[host]),
        byHost[targetSite],
        currentAuth,
        ...Object.values(byHost)
      ];
  let matchedAuths = uniqueAuthCandidates(candidates).filter((auth) => {
    const headers = pageAuthHeaders(auth);
    return authMatches(auth, targetHost, targetSite, targetHosts)
      && requiredAuthHeaders.every((key) => headers[key]);
  });
  if (targetSite === 'merchant') {
    matchedAuths = matchedAuths
      .map((auth, index) => ({ auth, priority: merchantAuthPriority(auth, cmd, index) }))
      .sort((a, b) => a.priority - b.priority)
      .map((item) => item.auth);
  }
  if (!matchedAuths.length) {
    throw new Error(`${profile.label}未登录`);
  }
  return matchedAuths.map((matchedAuth) => {
    const authProfile = profileForAuthOrigin(profile, matchedAuth);
    const nextConfig = {
      ...config,
      ...authProfile,
      headers: {
        ...config.headers,
        ...(matchedAuth.headers || {})
      },
      pageAuth: matchedAuth,
      pageAuthLabel: targetSite === 'merchant' ? merchantAuthLabel(matchedAuth) : ''
    };
    return targetSite === 'merchant' ? applyMerchantEndpointBase(nextConfig) : nextConfig;
  });
}

function configForAction(config, action, cmd = {}) {
  return authConfigsForAction(config, action, cmd)[0];
}

async function setStatus(status) {
  await chrome.storage.local.set({
    status: {
      time: nowText(),
      ...status
    }
  });
}

function truncateText(value, limit) {
  const text = typeof value === 'string' ? value : JSON.stringify(value ?? '');
  if (text.length <= limit) return text;
  return `${text.slice(0, limit)}\n...[truncated ${text.length - limit} chars]`;
}

function sanitizeHeaders(headers = {}) {
  const out = {};
  for (const [rawKey, rawValue] of Object.entries(headers || {})) {
    const key = String(rawKey).toLowerCase();
    const value = String(rawValue ?? '');
    if (/(token|authorization|cookie|secret|password)/i.test(key)) {
      out[key] = value ? '[redacted]' : '';
    } else {
      out[key] = value;
    }
  }
  return out;
}

function normalizeRecord(record = {}) {
  return {
    id: `rec_${Date.now()}_${Math.floor(Math.random() * 100000)}`,
    captured_at: new Date().toISOString(),
    page_url: String(record.page_url || ''),
    transport: String(record.transport || ''),
    method: String(record.method || 'GET').toUpperCase(),
    url: String(record.url || ''),
    request_headers: sanitizeHeaders(record.request_headers || {}),
    request_body: truncateText(record.request_body || '', RECORDER_BODY_LIMIT),
    status: record.status ?? null,
    ok: record.ok ?? null,
    duration_ms: Math.max(0, Number(record.duration_ms || 0)),
    response_url: String(record.response_url || ''),
    response_body: truncateText(record.response_body || '', RECORDER_RESPONSE_LIMIT),
    error: String(record.error || '')
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, Math.max(0, Number(ms || 0))));
}

async function fetchWithRetry(url, options = {}, retryDelays = FETCH_RETRY_DELAYS_MS) {
  let lastErr = null;
  const retryHttpStatuses = Array.isArray(options.retryHttpStatuses) ? options.retryHttpStatuses.map(Number) : [];
  const fetchOptions = { ...options };
  delete fetchOptions.retryHttpStatuses;
  for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
    try {
      const res = await fetch(url, fetchOptions);
      if (retryHttpStatuses.includes(Number(res.status)) && attempt < retryDelays.length) {
        await sleep(retryDelays[attempt]);
        continue;
      }
      return res;
    } catch (err) {
      lastErr = err;
      if (attempt >= retryDelays.length) break;
      await sleep(retryDelays[attempt]);
    }
  }
  throw lastErr;
}

async function startRecorder() {
  await chrome.storage.local.set({
    recorderState: { enabled: true, startedAt: new Date().toISOString(), stoppedAt: '', count: 0 },
    recorderRecords: []
  });
}

async function stopRecorder() {
  const stored = await chrome.storage.local.get(['recorderState', 'recorderRecords']);
  const records = stored.recorderRecords || [];
  await chrome.storage.local.set({
    recorderState: {
      ...(stored.recorderState || {}),
      enabled: false,
      stoppedAt: new Date().toISOString(),
      count: records.length
    }
  });
}

async function clearRecorder() {
  await chrome.storage.local.set({
    recorderState: { enabled: false, startedAt: '', stoppedAt: '', count: 0 },
    recorderRecords: []
  });
}

async function appendRecorderRecord(record) {
  const stored = await chrome.storage.local.get(['recorderState', 'recorderRecords']);
  const state = stored.recorderState || {};
  if (!state.enabled) return { ok: true, skipped: true };
  const records = Array.isArray(stored.recorderRecords) ? stored.recorderRecords : [];
  records.push(normalizeRecord(record));
  while (records.length > RECORDER_LIMIT) records.shift();
  await chrome.storage.local.set({
    recorderRecords: records,
    recorderState: { ...state, count: records.length, lastCapturedAt: new Date().toISOString() }
  });
  return { ok: true, count: records.length };
}

async function authSyncRemainingMs() {
  const stored = await chrome.storage.local.get(['authSync']);
  const sync = stored.authSync || {};
  const remaining = Math.max(0, Number(sync.until || 0) - Date.now());
  if (!sync.active || !remaining) return 0;
  return Math.min(remaining, AUTH_SYNC_WAIT_MS);
}

async function waitForAuthSyncReady() {
  const remaining = await authSyncRemainingMs();
  if (!remaining) return false;
  await setStatus({
    state: 'auth_refresh',
    message: '等待后台登录态同步',
    detail: `约 ${Math.ceil(remaining / 1000)} 秒后继续执行命令。`
  });
  await sleep(remaining);
  return true;
}

function queryTabs(query) {
  return new Promise((resolve, reject) => {
    chrome.tabs.query(query, (tabs) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(tabs || []);
    });
  });
}

function reloadTab(tabId) {
  return new Promise((resolve) => {
    chrome.tabs.reload(tabId, {}, () => {
      const err = chrome.runtime.lastError;
      resolve({ ok: !err, error: err ? err.message : '' });
    });
  });
}

function createTab(url) {
  return new Promise((resolve, reject) => {
    chrome.tabs.create({ url, active: false }, (tab) => {
      const err = chrome.runtime.lastError;
      if (err) reject(new Error(err.message));
      else resolve(tab || null);
    });
  });
}

async function waitForMerchantAuthRefresh(startedAtMs, waitMs = MERCHANT_RELOGIN_WAIT_MS) {
  const deadline = Date.now() + Math.max(1000, Number(waitMs || MERCHANT_RELOGIN_WAIT_MS));
  while (Date.now() < deadline) {
    const stored = await chrome.storage.local.get(['pageAuthByMerchant']);
    const auths = Object.values(stored.pageAuthByMerchant || {});
    const refreshed = auths.find((auth) => {
      const headers = pageAuthHeaders(auth);
      const capturedAt = Date.parse(auth && auth.capturedAt || '');
      return headers.authorization && headers['user-id'] && capturedAt >= startedAtMs;
    });
    if (refreshed) return true;
    await sleep(1500);
  }
  return false;
}

function accountMatchesLabels(account = {}, labels = []) {
  const haystack = [
    account.name,
    account.merchant,
    account.merchantName,
    account.username,
    account.user
  ].map((value) => String(value || '').toLowerCase()).join(' ');
  const cleanLabels = (labels || []).map((item) => String(item || '').toLowerCase()).filter(Boolean);
  return !cleanLabels.length || cleanLabels.some((label) => haystack.includes(label) || label.includes(haystack));
}

function merchantLoginPasswordValue(account = {}) {
  const hash = String(account.passwordHash || account.password_hash || '').trim();
  if (/^[a-f0-9]{32}$/i.test(hash)) return hash.toLowerCase();
  const password = String(account.password || '').trim();
  if (/^[a-f0-9]{32}$/i.test(password)) return password.toLowerCase();
  return '';
}

async function saveMerchantLoginAuth(config, account, loginData) {
  const data = (loginData && loginData.data) || {};
  const token = deepFindValue(data, ['token', 'accessToken', 'access_token', 'authorization', 'jwt']);
  if (!token) throw new Error('登录成功但未找到token');
  const userId = deepFindValue(data, ['userId', 'user-id', 'id', 'uid']) || String(account.userId || account.user_id || '').trim();
  const merchantName = deepFindValue(data, ['merchantName', 'merchantname']) || String(account.merchantName || account.merchant || account.name || '').trim();
  const headers = {
    authorization: token,
    language: String(account.language || 'zs')
  };
  if (userId) headers['user-id'] = userId;
  if (merchantName) headers.merchantname = encodeURIComponent(merchantName);
  const auth = {
    href: String(config.merchantLoginUrl || 'https://merchant-own-backstage.dbsportxxxwo8.com/'),
    capturedAt: new Date().toISOString(),
    headers
  };
  const key = merchantAuthKey(auth) || `${merchantName || account.username || 'merchant'}|${userId || 'unknown'}`;
  const stored = await chrome.storage.local.get(['pageAuthByHost', 'pageAuthByMerchant']);
  await chrome.storage.local.set({
    pageAuth: auth,
    pageAuthByHost: {
      ...(stored.pageAuthByHost || {}),
      merchant: auth,
      'merchant-own-backstage.dbsportxxxwo8.com': auth,
      'api-merchant-backstage.dbsportxxxwo8.com': auth
    },
    pageAuthByMerchant: limitedMerchantAuthStore({
      ...(stored.pageAuthByMerchant || {}),
      [key]: auth
    })
  });
  return auth;
}

async function loginMerchantAccount(config, account) {
  const username = String(account.username || account.user || '').trim();
  const password = merchantLoginPasswordValue(account);
  if (!username || !password) throw new Error('场馆自动登录账号缺少username或passwordHash');
  const loginUrl = String(config.merchantLoginApiUrl || config.merchantLoginUrlApi || 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/auth/login');
  const res = await fetchWithRetry(loginUrl, {
    method: 'POST',
    mode: 'cors',
    credentials: 'include',
    headers: {
      accept: 'application/json, text/plain, */*',
      'content-type': 'application/json',
      language: String(account.language || 'zs'),
      'request-id': `${Math.random().toString(16).slice(2)}-${Date.now()}`
    },
    body: JSON.stringify({ username, password }),
    retryHttpStatuses: TRANSIENT_HTTP_RETRY_STATUSES
  });
  const text = await res.text();
  let data = {};
  try { data = JSON.parse(text || '{}'); } catch { data = {}; }
  if (!res.ok || data.code !== '0000000' || data.status === false) {
    throw new Error(`场馆自动登录失败 HTTP ${res.status}: ${String(data.msg || data.message || text).slice(0, 160)}`);
  }
  return saveMerchantLoginAuth(config, account, data);
}

async function tryMerchantApiRelogin(config, labels = []) {
  const accounts = Array.isArray(config.merchantLoginAccounts) ? config.merchantLoginAccounts : [];
  const candidates = accounts.filter((item) => item && item.enabled !== false && merchantLoginPasswordValue(item));
  if (!candidates.length) return false;
  const preferred = candidates.filter((item) => accountMatchesLabels(item, labels));
  const queue = preferred.length ? preferred : candidates;
  const errors = [];
  for (const account of queue) {
    try {
      await setStatus({
        state: 'auth_refresh',
        message: `场馆登录失效，正在接口重登 ${account.name || account.username || ''}`.trim(),
        detail: '登录成功后会自动重试原后台操作。'
      });
      await loginMerchantAccount(config, account);
      return true;
    } catch (err) {
      errors.push(err && err.message ? err.message : String(err || ''));
    }
  }
  console.warn('[CS Bot ZD Unlock] merchant api relogin failed', errors);
  return false;
}

async function requestMerchantRelogin(config, labels = []) {
  if (await tryMerchantApiRelogin(config, labels)) return true;
  const startedAt = Date.now();
  await chrome.storage.local.set({
    pendingMerchantRelogin: {
      active: true,
      labels: [...new Set((labels || []).map((item) => String(item || '').trim()).filter(Boolean))],
      startedAt,
      until: startedAt + MERCHANT_RELOGIN_WAIT_MS
    }
  });
  await setStatus({
    state: 'auth_refresh',
    message: '场馆登录失效，正在尝试重新登录',
    detail: '已打开场馆后台登录页，等待新登录态同步后自动重试。'
  });
  try {
    await createTab(String(config.merchantLoginUrl || 'https://merchant-own-backstage.dbsportxxxwo8.com/'));
  } catch (err) {
    console.warn('[CS Bot ZD Unlock] open merchant login failed', err);
  }
  const ok = await waitForMerchantAuthRefresh(startedAt);
  const stored = await chrome.storage.local.get(['pendingMerchantRelogin']);
  await chrome.storage.local.set({
    pendingMerchantRelogin: {
      ...(stored.pendingMerchantRelogin || {}),
      active: false,
      completedAt: new Date().toISOString(),
      until: 0
    }
  });
  return ok;
}

async function refreshBackstageTabs() {
  const tabs = await queryTabs({
    url: [
      ...SITE_9_HOSTS.map((host) => `https://${host}/*`),
      ...SITE_6_HOSTS.map((host) => `https://${host}/*`),
      'https://merchant-own-backstage.dbsportxxxwo8.com/*',
      'https://*.dbsportxxxwo8.com/*'
    ]
  });
  const uniqueTabs = [...new Map(tabs.filter((tab) => tab && tab.id != null).map((tab) => [tab.id, tab])).values()];
  const startedAt = new Date();
  const waitMs = uniqueTabs.length ? AUTH_SYNC_WAIT_MS : 0;
  await chrome.storage.local.set({
    authSync: {
      active: waitMs > 0,
      startedAt: startedAt.toISOString(),
      until: Date.now() + waitMs,
      tabCount: uniqueTabs.length,
      authMessages: 0
    }
  });
  const results = await Promise.all(uniqueTabs.map((tab) => reloadTab(tab.id)));
  const failed = results.filter((item) => !item.ok);
  if (waitMs) await sleep(waitMs);
  const stored = await chrome.storage.local.get(['authSync']);
  await chrome.storage.local.set({
    authSync: {
      ...(stored.authSync || {}),
      active: false,
      completedAt: new Date().toISOString(),
      until: 0
    }
  });
  const detail = failed.length
    ? `部分页面刷新失败：${failed.map((item) => item.error).filter(Boolean).join('；')}`
    : '后台页面已刷新，登录状态已等待同步。';
  await setStatus({
    state: 'auth_refresh',
    message: `已刷新并同步 ${uniqueTabs.length} 个后台页面`,
    detail
  });
  pollOnce();
  return { ok: true, count: uniqueTabs.length, failed: failed.length, detail, waitedMs: waitMs };
}

async function ack(config, cmd, status, detail = '', extra = {}) {
  if (!cmd || !cmd.id) return;
  try {
    await fetch(`${config.botBase}/api/cmd/ack?secret=${encodeURIComponent(config.cmdSecret)}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        id: cmd.id,
        status,
        member_name: cmd.member_name,
        detail: String(detail || '').slice(0, 500),
        ...extra
      })
    });
  } catch (err) {
    console.warn('[CS Bot ZD Unlock] ack failed', err);
  }
}

async function reportProgress(config, cmd, payload = {}) {
  if (!cmd || !cmd.id) return;
  try {
    await fetch(`${config.botBase}/api/cmd/progress?secret=${encodeURIComponent(config.cmdSecret)}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        id: cmd.id,
        member_name: cmd.member_name,
        ...payload
      })
    });
  } catch (err) {
    console.warn('[CS Bot ZD Unlock] progress failed', err);
  }
}

function commandLabel(action) {
  if (action === 'add_proxy_whitelist') return '代理IP加白';
  if (action === 'clear_login_error') return '登录限制解锁';
  if (action === 'migrate_milan') return '迁移米兰';
  if (action === 'send_site_inner_msg') return '发送站内信';
  if (action === 'member_data_overview') return '查数据';
  if (action === 'query_member_line') return '查线';
  if (action === 'query_login_device_ip') return '查登录设备/IP';
  if (action === 'query_same_ip_device') return '查同IP/设备';
  if (action === 'disable_login_device') return '禁用设备申请';
  if (action === 'query_venue_turnover') return '查场馆流水锁定';
  if (action === 'configure_rebate') return '配置返水';
  if (action === 'merchant_order_statistics') return '场馆注单查询';
  if (action === 'urge_settlement') return '催结算';
  if (action === 'query_ticket_cancel_reason') return '注单取消/失败原因';
  if (action === 'venue_display_control') return '场馆上下架';
  return '短信/验证码解锁';
}

function rawErrorText(err) {
  if (!err) return '';
  return String(err.message || err.stack || err || '').trim();
}

function compactRequestFailureReason(text, action = '') {
  const raw = String(text || '').replace(/\s+/g, ' ');
  const urlMatch = raw.match(/https?:\/\/[^\s。；]+/);
  let endpoint = '';
  let label = action === 'urge_settlement' || action === 'merchant_order_statistics' || action === 'query_ticket_cancel_reason' ? '场馆接口请求失败' : '后台接口请求失败';
  if (urlMatch) {
    try {
      const parsed = new URL(urlMatch[0].replace(/[),，。；]+$/, ''));
      const parts = parsed.pathname.split('/').filter(Boolean);
      endpoint = parts.slice(-2).join('/') || parsed.hostname;
      if (parsed.hostname.includes('api-merchant-backstage')) label = '场馆接口请求失败';
      else {
        const siteLabel = backendSiteFailureLabel(parsed.hostname);
        if (siteLabel) label = siteLabel;
        else endpoint = parsed.hostname + (endpoint ? `/${endpoint}` : '');
      }
    } catch {
      endpoint = '';
    }
  }
  const transport = /Failed to fetch/i.test(raw) ? 'Failed to fetch'
    : /Load failed/i.test(raw) ? 'Load failed'
      : (raw.match(/HTTP\s*[45]\d\d/i) || [''])[0].trim();
  const suffix = [endpoint, transport].filter(Boolean).join('，');
  return suffix ? `${label}：${suffix}` : label;
}

function friendlyErrorReason(err, action = '', label = '') {
  const raw = rawErrorText(err);
  const text = raw.replace(/\s+/g, ' ');
  if (!text) return '后台处理失败';
  if (/代理编码不正确|代理编号不正确|上级代理编号不匹配|上级代理.*不匹配/.test(text)) return '代理编码不正确，请核实';
  if (/未提取到会员账号|会员账号为空/.test(text)) return '未提取到会员账号';
  if (/未找到会员|未找到迁移会员|会员不存在/.test(text)) return '会员不存在';
  if (/所有场馆账号均未找到注单|未找到注单|merchant_order_not_found/.test(text)) return '未找到注单';
  if (/未到开赛时间|注单未开赛/.test(text)) return '注单未开赛';
  if (/所有场馆账号登录失效|场馆后台未登录|场馆.*未登录|登录失效/.test(text)) return '场馆登录失效';
  if (/9站未登录|9001未登录/.test(text)) return '9站未登录';
  if (/6站未登录|6001未登录/.test(text)) return '6站未登录';
  if (/未登录/.test(text)) {
    if (action === 'urge_settlement' || action === 'merchant_order_statistics' || action === 'query_ticket_cancel_reason') return '场馆登录失效';
    return `${label || '后台'}未登录`;
  }
  if (/扩展已重新加载|拓展已重新加载|刷新对应后台页面|登录态同步|后台页面已刷新/.test(text)) return '拓展未同步登录态';
  if (/Failed to fetch|Load failed|请求失败|HTTP\s*[45]\d\d|接口.*失败|查询.*失败/.test(text)) {
    return compactRequestFailureReason(text, action);
  }
  if (/未配置.*TG群|TG发送失败|telegram|send_telegram/i.test(text)) return 'TG发送失败';
  if (/等待后台回执超时|超时|timeout/i.test(text)) return '后台处理超时';
  return text.split('\n')[0].slice(0, 120);
}

function hasMerchantCommandHint(cmd = {}) {
  const hint = String(cmd.backend_site || cmd.site || cmd.site_id || cmd.siteId || '').trim().toLowerCase();
  return !!(cmd.orderNo || cmd.order_no)
    || ['3', 'merchant', 'venue', 'stadium', 'changguan', '场馆'].includes(hint);
}

function normalizeCommandAction(action, cmd = {}) {
  const raw = String(action || 'unlock_sms').trim();
  const aliases = {
    venue_order_statistics: 'merchant_order_statistics',
    venue_order_query: 'merchant_order_statistics',
    merchant_order_query: 'merchant_order_statistics',
    query_venue_order: 'merchant_order_statistics',
    query_merchant_order: 'merchant_order_statistics',
    settlement_urge: 'urge_settlement',
    urge_settle: 'urge_settlement',
    urge_settlement_order: 'urge_settlement',
    ticket_cancel_reason: 'query_ticket_cancel_reason',
    ticket_failure_reason: 'query_ticket_cancel_reason',
    query_ticket_failure_reason: 'query_ticket_cancel_reason',
    invalid_ticket_reason: 'query_ticket_cancel_reason',
    query_invalid_ticket_reason: 'query_ticket_cancel_reason',
    venue_maintenance: 'venue_display_control',
    venue_enable: 'venue_display_control',
    venue_display: 'venue_display_control',
    data_overview: 'member_data_overview',
    query_member_data: 'member_data_overview',
    member_data_query: 'member_data_overview',
    line_query: 'query_member_line',
    query_line: 'query_member_line',
    agent_line_query: 'query_member_line',
    query_agent_line: 'query_member_line',
    login_device_ip: 'query_login_device_ip',
    query_device_ip: 'query_login_device_ip',
    same_ip_device: 'query_same_ip_device',
    query_same_device_ip: 'query_same_ip_device',
    disable_device: 'disable_login_device',
    disable_login_device_apply: 'disable_login_device',
    venue_turnover: 'query_venue_turnover',
    venue_turnover_lock: 'query_venue_turnover',
    query_venue_turnover_lock: 'query_venue_turnover',
    rebate_config: 'configure_rebate',
    configure_rebate_rate: 'configure_rebate',
    '查数据': 'member_data_overview',
    '数据概览': 'member_data_overview',
    '查线': 'query_member_line',
    '查代理线': 'query_member_line',
    '登录设备': 'query_login_device_ip',
    '查询登录设备': 'query_login_device_ip',
    '查同IP': 'query_same_ip_device',
    '查同ip': 'query_same_ip_device',
    '查同设备': 'query_same_ip_device',
    '禁用设备': 'disable_login_device',
    '申请禁用设备': 'disable_login_device',
    '查场馆流水': 'query_venue_turnover',
    '流水锁定': 'query_venue_turnover',
    '配置返水': 'configure_rebate',
    '催结算': 'urge_settlement',
    '注单取消原因': 'query_ticket_cancel_reason',
    '取消原因': 'query_ticket_cancel_reason',
    '失败原因': 'query_ticket_cancel_reason',
    '无效原因': 'query_ticket_cancel_reason',
    '投注失败': 'query_ticket_cancel_reason'
  };
  const normalized = aliases[raw] || raw;
  if (hasMerchantCommandHint(cmd) && (raw === '' || raw === 'unlock_sms')) return 'merchant_order_statistics';
  return ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan', 'send_site_inner_msg', 'member_data_overview', 'query_member_line', 'query_login_device_ip', 'query_same_ip_device', 'disable_login_device', 'query_venue_turnover', 'configure_rebate', 'merchant_order_statistics', 'urge_settlement', 'query_ticket_cancel_reason', 'venue_display_control'].includes(normalized)
    ? normalized
    : 'unlock_sms';
}

function isSupportedCommandAction(action, cmd = {}) {
  const raw = String(action || 'unlock_sms').trim();
  return ((raw === '' || raw === 'unlock_sms') && hasMerchantCommandHint(cmd)) || [
    '',
    'unlock_sms',
    'clear_login_error',
    'add_proxy_whitelist',
    'migrate_milan',
    'send_site_inner_msg',
    'member_data_overview',
    'query_member_line',
    'query_login_device_ip',
    'query_same_ip_device',
    'disable_login_device',
    'query_venue_turnover',
    'configure_rebate',
    'merchant_order_statistics',
    'urge_settlement',
    'query_ticket_cancel_reason',
    'venue_order_statistics',
    'venue_order_query',
    'merchant_order_query',
    'query_venue_order',
    'query_merchant_order',
    'data_overview',
    'query_member_data',
    'member_data_query',
    'line_query',
    'query_line',
    'agent_line_query',
    'query_agent_line',
    'login_device_ip',
    'query_device_ip',
    'same_ip_device',
    'query_same_device_ip',
    'disable_device',
    'disable_login_device_apply',
    'venue_turnover',
    'venue_turnover_lock',
    'query_venue_turnover_lock',
    'rebate_config',
    'configure_rebate_rate',
    '查数据',
    '数据概览',
    '查线',
    '查代理线',
    '登录设备',
    '查询登录设备',
    '查同IP',
    '查同ip',
    '查同设备',
    '禁用设备',
    '申请禁用设备',
    '查场馆流水',
    '流水锁定',
    '配置返水',
    'settlement_urge',
    'urge_settle',
    'urge_settlement_order',
    'ticket_cancel_reason',
    'ticket_failure_reason',
    'query_ticket_failure_reason',
    'invalid_ticket_reason',
    'query_invalid_ticket_reason',
    'venue_display_control',
    'venue_maintenance',
    'venue_enable',
    'venue_display',
    '催结算',
    '注单取消原因',
    '取消原因',
    '失败原因',
    '无效原因',
    '投注失败'
  ].includes(raw);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomDelaySeconds(min, max) {
  const minValue = Number(min);
  const maxValue = Number(max);
  if (!Number.isFinite(minValue) || !Number.isFinite(maxValue) || maxValue <= 0) return 0;
  const lo = Math.max(0, Math.min(minValue, maxValue));
  const hi = Math.max(lo, Math.max(minValue, maxValue));
  return lo + Math.random() * (hi - lo);
}

function commandRequest(config, action, targetValue, cmd = {}) {
  if (action === 'add_proxy_whitelist') {
    return {
      url: config.proxyWhitelistUrl,
      body: {
        ruleType: '2',
        clientType: 'agent_web',
        ipType: 0,
        expMatcher: targetValue,
        isOperatorOther: 0
      }
    };
  }
  if (action === 'clear_login_error') {
    return {
      url: config.loginErrorUrl,
      body: { name: targetValue }
    };
  }
  if (action === 'migrate_milan') {
    if (!config.migrateMilanUrl) throw new Error('迁移米兰接口未配置');
    return {
      url: config.migrateMilanUrl,
      body: { members: [{ name: targetValue }], siteIdTo: 9001 }
    };
  }
  const unlockValue = String(cmd.value || config.value || '').trim();
  if (!unlockValue) {
    throw new Error('短信解锁 value 未配置：请先在后台手动执行一次短信解锁，让扩展自动捕获参数');
  }
  return {
    url: config.unlockUrl,
    body: { value: unlockValue, name: targetValue }
  };
}

function parseJsonText(text) {
  try {
    return JSON.parse(text || '{}');
  } catch {
    return {};
  }
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function formatDateTime(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())} ${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`;
}

function formatDate(date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}`;
}

function dateOnly(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  const next = dateOnly(date);
  next.setDate(next.getDate() + Number(days || 0));
  return next;
}

function parseDateToken(value) {
  const match = String(value || '').trim().match(/^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$/);
  if (!match) return null;
  const [, y, m, d] = match;
  const date = new Date(Number(y), Number(m) - 1, Number(d));
  if (
    date.getFullYear() !== Number(y)
    || date.getMonth() !== Number(m) - 1
    || date.getDate() !== Number(d)
  ) return null;
  return date;
}

function chineseNumberToInt(value) {
  const text = String(value || '').trim();
  if (!text) return null;
  if (/^\d+$/.test(text)) return Number(text);
  const digits = { 零: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
  if (text === '十') return 10;
  const tenIndex = text.indexOf('十');
  if (tenIndex >= 0) {
    const before = text.slice(0, tenIndex);
    const after = text.slice(tenIndex + 1);
    const tens = before ? digits[before] : 1;
    const ones = after ? digits[after] : 0;
    if (tens === undefined || ones === undefined) return null;
    return tens * 10 + ones;
  }
  if (text.length === 1 && digits[text] !== undefined) return digits[text];
  return null;
}

function orderedDateRange(start, end) {
  const startDate = dateOnly(start);
  const endDate = dateOnly(end);
  if (startDate.getTime() <= endDate.getTime()) {
    return { startAt: formatDate(startDate), endAt: formatDate(endDate) };
  }
  return { startAt: formatDate(endDate), endAt: formatDate(startDate) };
}

function commandSourceText(cmd = {}) {
  return [
    cmd.source_text,
    cmd.sourceText,
    cmd.original_text,
    cmd.originalText,
    cmd.message,
    cmd.text
  ].filter(Boolean).join('\n');
}

function memberDataOverviewDateRange(cmd = {}) {
  const today = dateOnly(new Date());
  const directStart = String(cmd.startAt || cmd.start_at || cmd.start_date || cmd.startDate || '').trim();
  const directEnd = String(cmd.endAt || cmd.end_at || cmd.end_date || cmd.endDate || '').trim();
  if (directStart || directEnd) {
    return {
      startAt: directStart || '2020-01-01',
      endAt: directEnd || formatDate(today)
    };
  }

  const text = commandSourceText(cmd).replace(/\s+/g, '');
  const explicitRange = text.match(/(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})(?:到|至|~|～|--|－|—)(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})/);
  if (explicitRange) {
    const start = parseDateToken(explicitRange[1]);
    const end = parseDateToken(explicitRange[2]);
    if (start && end) return orderedDateRange(start, end);
  }
  const spacedRange = text.match(/(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})-(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})/);
  if (spacedRange) {
    const start = parseDateToken(spacedRange[1]);
    const end = parseDateToken(spacedRange[2]);
    if (start && end) return orderedDateRange(start, end);
  }
  const singleDate = text.match(/(?:日期|时间|当天|查)(\d{4}[-/.]\d{1,2}[-/.]\d{1,2})/);
  if (singleDate) {
    const date = parseDateToken(singleDate[1]);
    if (date) return orderedDateRange(date, date);
  }
  const recent = text.match(/(?:近|最近)([\d一二两三四五六七八九十]+)天/) || text.match(/([\d一二两三四五六七八九十]+)天内/);
  if (recent) {
    const days = Math.max(1, chineseNumberToInt(recent[1]) || 1);
    return orderedDateRange(addDays(today, 1 - days), today);
  }
  if (text.includes('今天') || text.includes('今日')) return orderedDateRange(today, today);
  if (text.includes('昨天') || text.includes('昨日')) {
    const yesterday = addDays(today, -1);
    return orderedDateRange(yesterday, yesterday);
  }
  if (text.includes('本月') || text.includes('这个月')) {
    return orderedDateRange(new Date(today.getFullYear(), today.getMonth(), 1), today);
  }
  if (text.includes('上月') || text.includes('上个月')) {
    const start = new Date(today.getFullYear(), today.getMonth() - 1, 1);
    const end = new Date(today.getFullYear(), today.getMonth(), 0);
    return orderedDateRange(start, end);
  }
  if (text.includes('今年') || text.includes('本年')) {
    return orderedDateRange(new Date(today.getFullYear(), 0, 1), today);
  }
  return { startAt: '2020-01-01', endAt: formatDate(today) };
}

function merchantDateRange(cmd = {}) {
  const end = new Date();
  const start = new Date(end.getTime() - (90 * 24 * 60 * 60 * 1000));
  return {
    startTime: String(cmd.startTime || cmd.start_time || formatDateTime(start)),
    endTime: String(cmd.endTime || cmd.end_time || formatDateTime(end))
  };
}

function merchantHeaders(config, cmd = {}) {
  const stamp = Date.now();
  return {
    ...config.headers,
    accept: 'application/json, text/plain, */*',
    'content-type': 'application/json',
    language: String(cmd.language || config.headers.language || 'zs'),
    'request-id': String(cmd.request_id || cmd.requestId || `${Math.random().toString(16).slice(2)}-${stamp}`)
  };
}

async function postJson(url, headers, body) {
  return postJsonText(url, headers, JSON.stringify(body));
}

function retryHttpStatusesForUrl(url) {
  const raw = String(url || '');
  const retryablePaths = [
    '/admin/userReport/queryTicketList',
    '/admin/userReport/getStatistics',
    '/admin/settlement/queryNoSettleTicketList',
    '/admin/settlement/getStatistics',
    '/admin/noticeNew/notice',
    '/admin/noticeNew/noticeDetail',
    '/admin/noticeNew/getLightNews',
    '/admin/abnormal/queryAbnormalCount',
    '/admin/player/getSportList',
    '/admin/userReport/queryHmOrderPlayName',
    '/admin/userReport/getCancelMatchResultTypes'
  ];
  return retryablePaths.some((path) => raw.includes(path)) ? TRANSIENT_HTTP_RETRY_STATUSES : [];
}

async function postJsonText(url, headers, bodyText) {
  let res;
  try {
    res = await fetchWithRetry(url, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers,
      body: String(bodyText || '{}'),
      retryHttpStatuses: retryHttpStatusesForUrl(url)
    });
  } catch (err) {
    throw new Error(`请求失败 ${url}: ${err && err.message ? err.message : String(err || 'unknown')}。请确认扩展已重新加载，并刷新对应后台页面。`);
  }
  const text = await res.text();
  return { res, text, data: parseJsonText(text) };
}

async function postForm(url, headers, body) {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(body || {})) {
    if (value !== undefined && value !== null) params.set(key, String(value));
  }
  let res;
  try {
    res = await fetchWithRetry(url, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers: {
        ...headers,
        'content-type': 'application/x-www-form-urlencoded; charset=UTF-8'
      },
      body: params.toString(),
      retryHttpStatuses: retryHttpStatusesForUrl(url)
    });
  } catch (err) {
    throw new Error(`请求失败 ${url}: ${err && err.message ? err.message : String(err || 'unknown')}。请确认扩展已重新加载，并刷新对应后台页面。`);
  }
  const text = await res.text();
  return { res, text, data: parseJsonText(text) };
}

function merchantUrl(baseUrl) {
  return `${baseUrl}?rnd_str_st=${Date.now()}`;
}

function merchantHttpError(label, url, result) {
  return new Error(`${label} ${url} HTTP ${result.res.status}: ${String(result.text || '').slice(0, 300)}`);
}

function merchantApiOk(result) {
  const data = (result && result.data) || {};
  return !!(result && result.res && result.res.ok && (data.code === undefined || data.code === '0000000') && data.status !== false);
}

function merchantNoSettlementData(result) {
  const data = (result && result.data) || {};
  const text = `${data.msg || data.message || ''} ${(result && result.text) || ''}`.replace(/\s+/g, '');
  return !!(result && result.res && result.res.ok && data.code === '9999999' && /没有未结算赛事id数据|沒有未結算賽事id數據|未结算赛事id数据|未結算賽事id數據/.test(text));
}

function merchantAuthFailed(result) {
  const status = Number((result && result.res && result.res.status) || 0);
  if (status === 401 || status === 403) return true;
  const text = String((result && result.text) || '');
  return /token|未登录|登录已失效|登录过期|授权|unauthori[sz]ed/i.test(text);
}

function merchantTicketBody(cmd = {}, orderNo, overrides = {}) {
  const { startTime, endTime } = merchantDateRange(cmd);
  return {
    filter: String(cmd.filter || '1'),
    orderNo,
    databaseSwitch: Number(cmd.databaseSwitch ?? cmd.database_switch ?? 1),
    userIdList: Array.isArray(cmd.userIdList) ? cmd.userIdList : [],
    startTime,
    endTime,
    pageNum: Number(cmd.pageNum || cmd.page_num || 1),
    fromAppointment: Number(cmd.fromAppointment ?? cmd.from_appointment ?? 0),
    pageSize: Number(cmd.pageSize || cmd.page_size || 20),
    accountTag: Number(cmd.accountTag ?? cmd.account_tag ?? 0),
    ...overrides
  };
}

function merchantSettlementBody(cmd = {}, orderNo) {
  return merchantTicketBody(cmd, orderNo, {
    filter: String(cmd.settlementFilter || cmd.settlement_filter || '2'),
    seriesType: Number(cmd.seriesType || cmd.series_type || 1),
    orderStatusList: Array.isArray(cmd.orderStatusList) ? cmd.orderStatusList : [0],
    fromNoSettle: 1
  });
}

function merchantSettlementApplyBody(order = {}, detail = {}, orderNo = '') {
  return {
    orderNo: String(order.orderNo || orderNo || '').trim(),
    matchId: String(detail.matchId || order.standardMatchId || detail.standardMatchId || '').trim(),
    sportId: Number(detail.sportId || order.sportId || 0),
    betNo: String(detail.betNo || '').trim(),
    userId: String(order.uid || order.userId || detail.uid || detail.userId || '').trim(),
    matchStatus: 1
  };
}

function merchantList(data) {
  return ((((data || {}).data || {}).list) || []);
}

function htmlText(value) {
  return String(value || '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/<[^>]+>/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

function normalizeText(value) {
  return htmlText(value).toLowerCase().replace(/\s+/g, '');
}

function firstOrderDetail(order = {}) {
  const details = Array.isArray(order.orderDetailList) ? order.orderDetailList : [];
  return details[0] || {};
}

function orderDetails(order = {}) {
  return Array.isArray(order.orderDetailList) ? order.orderDetailList.filter(Boolean) : [];
}

function numericValue(value) {
  const num = Number(value);
  return Number.isFinite(num) ? num : null;
}

const DATA_OVERVIEW_FIELDS = [
  { key: 'net', label: '总输赢', source: 'finance', aliases: ['总输赢', '输赢', '总盈亏', '盈亏'] },
  { key: 'validBet', label: '总流水', source: 'finance', aliases: ['总流水', '流水', '有效流水', '有效投注'] },
  { key: 'deposit', label: '总存款', source: 'game', aliases: ['总存款', '存款', '总充值', '充值'] },
  { key: 'withdraw', label: '总提款', source: 'game', aliases: ['总提款', '提款', '总取款', '取款'] },
  { key: 'bonus', label: '总红利', source: 'game', aliases: ['总红利', '红利', '优惠'] },
  { key: 'rebate', label: '总返水', source: 'game', aliases: ['总返水', '总反水', '返水', '反水'] }
];

function formatMoney(value) {
  const num = Number(value || 0);
  return (Number.isFinite(num) ? num : 0).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2
  });
}

function escapeRegExp(value) {
  return String(value || '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function exactMemberIdFromListText(text, targetValue) {
  const jsonName = JSON.stringify(String(targetValue || '').toLowerCase()).slice(1, -1);
  if (!jsonName) return '';
  const matcher = new RegExp(`"id"\\s*:\\s*"?([0-9]{12,24})"?[\\s\\S]{0,6000}?"name"\\s*:\\s*"${escapeRegExp(jsonName)}"`, 'i');
  const matched = String(text || '').match(matcher);
  return matched ? matched[1] : '';
}

function memberDataOverviewBodyText(memberId, startAt, endAt) {
  const id = String(memberId || '').trim();
  if (!/^\d{12,24}$/.test(id)) {
    throw new Error(`会员ID无效：${id || '-'}`);
  }
  return `{"memberId":${id},"startAt":${JSON.stringify(String(startAt || ''))},"endAt":${JSON.stringify(String(endAt || ''))}}`;
}

function expectedAgentCodeFromText(cmd = {}, targetValue = '', explicitCode = '') {
  if (explicitCode) return String(explicitCode).trim();
  const directCode = String(cmd.agent_code || cmd.agentCode || cmd.expected_agent_code || cmd.expectedAgentCode || '').trim();
  if (directCode) return directCode;
  const text = commandSourceText(cmd);
  if (!text) return '';
  const escapedTarget = escapeRegExp(String(targetValue || '').trim());
  const afterTarget = escapedTarget
    ? (text.match(new RegExp(`${escapedTarget}([\\s\\S]{0,80})`, 'i')) || [])[1] || text
    : text;
  const matched = afterTarget.match(/\b(\d{5,12})\b/);
  return matched ? matched[1] : '';
}

function cleanDataOverviewLine(line) {
  return String(line || '')
    .replace(/\[[^\]]+\]\s*[^:：]{0,120}[:：]/g, ' ')
    .replace(/https?:\/\/\S+/gi, ' ')
    .replace(/[，,。；;|]+/g, ' ')
    .trim();
}

function isLikelyDataOverviewAccount(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!/^[a-z][a-z0-9]{2,31}$/.test(text)) return false;
  if (!/\d/.test(text)) return false;
  if (/^(vip\d*|hwd\d*|tg\d*|bot\d*|http|https|www|admin|user|member|query|data|total|sum|win|loss|null|none|true|false)$/i.test(text)) return false;
  return true;
}

function isFallbackDataOverviewAccount(value) {
  const text = String(value || '').trim().toLowerCase();
  if (!/^[a-z0-9][a-z0-9._-]{1,63}$/.test(text)) return false;
  return /[a-z]/i.test(text);
}

function addDataOverviewTarget(targets, seen, name, agentCode = '') {
  const memberName = String(name || '').trim().toLowerCase();
  if (!memberName || seen.has(memberName)) return;
  if (!isLikelyDataOverviewAccount(memberName)) return;
  seen.add(memberName);
  targets.push({ name: memberName, agentCode: String(agentCode || '').trim() });
}

function dataOverviewTargetsFromText(cmd = {}, fallbackTarget = '') {
  const targets = [];
  const seen = new Set();
  const text = commandSourceText(cmd);
  const lines = String(text || '').split(/\r?\n/).map(cleanDataOverviewLine).filter(Boolean);

  for (const line of lines) {
    const accountMatch = line.match(/\b([a-z][a-z0-9]{2,31})\b/i);
    if (!accountMatch || !isLikelyDataOverviewAccount(accountMatch[1])) continue;
    const afterAccount = line.slice((accountMatch.index || 0) + accountMatch[1].length);
    const codeMatch = afterAccount.match(/\b(\d{5,12})\b/);
    addDataOverviewTarget(targets, seen, accountMatch[1], codeMatch ? codeMatch[1] : '');
  }

  const compactText = cleanDataOverviewLine(text);
  const pairPattern = /\b([a-z][a-z0-9]{2,31})\b(?:\s+(\d{5,12}))?/ig;
  let match;
  while ((match = pairPattern.exec(compactText))) {
    addDataOverviewTarget(targets, seen, match[1], match[2] || '');
  }

  const fallback = String(fallbackTarget || cmd.target_value || cmd.member_name || '').trim().toLowerCase();
  if (fallback && !seen.has(fallback) && isFallbackDataOverviewAccount(fallback)) {
    targets.unshift({ name: fallback, agentCode: expectedAgentCodeFromText(cmd, fallback) });
  }
  if (targets.length) return targets;
  return fallback && isFallbackDataOverviewAccount(fallback)
    ? [{ name: fallback, agentCode: expectedAgentCodeFromText(cmd, fallback) }]
    : [];
}

async function decryptedTopInviteCode(config, member = {}) {
  if (member.topInviteCode) return String(member.topInviteCode).trim();
  const signature = String(member.xsS34Sign || '').trim();
  const encryptedString = String(member.topInviteCodeDesensitization || '').trim();
  const fingerprint = String(member.topInviteCodeCipher || '').trim();
  if (!signature || !encryptedString || !fingerprint) return '';
  if (!config.dataDecryptionUrl) throw new Error('数据解密接口未配置');
  const decrypted = await postJson(config.dataDecryptionUrl, config.headers, [{
    signature,
    encryptedString,
    fingerprint
  }]);
  if (!apiOk(decrypted.res, decrypted.data)) {
    throw new Error(`解密上级代理编号失败 HTTP ${decrypted.res.status}: ${decrypted.text.slice(0, 300)}`);
  }
  return String((((decrypted.data || {}).data || {})[signature]) || '').replace(/\*/g, '').trim();
}

function commandAiParse(cmd = {}) {
  return (cmd && typeof cmd.ai_parse === 'object' && cmd.ai_parse) ? cmd.ai_parse : {};
}

function cleanMemberToken(value) {
  return String(value || '').trim().toLowerCase();
}

function isLikelyBackendMember(value) {
  const text = cleanMemberToken(value);
  if (!/^[a-z0-9][a-z0-9._-]{1,63}$/.test(text)) return false;
  if (/^\d{5,24}$/.test(text)) return false;
  if (!/[a-z]/.test(text) || !/\d/.test(text)) return false;
  if (/^(vip\d*|tg\d*|bot\d*|http|https|www|ip|ios|android|web|h5|jn|ml|art|ok|yes|no)$/i.test(text)) return false;
  return true;
}

function commandMembers(cmd = {}, fallbackTarget = '') {
  const out = [];
  const seen = new Set();
  const add = (value) => {
    const member = cleanMemberToken(value);
    if (!member || seen.has(member) || !isLikelyBackendMember(member)) return;
    seen.add(member);
    out.push(member);
  };
  const ai = commandAiParse(cmd);
  const rawMembers = [
    ...(Array.isArray(cmd.members) ? cmd.members : []),
    ...(Array.isArray(ai.members) ? ai.members : []),
    cmd.member,
    ai.member,
    fallbackTarget,
    cmd.target_value,
    cmd.member_name
  ];
  rawMembers.forEach(add);
  const text = commandSourceText(cmd);
  for (const match of text.matchAll(/\b([a-zA-Z][a-zA-Z0-9._-]{1,63})\b/g)) {
    add(match[1]);
  }
  return out.slice(0, 12);
}

function commandAgentCodes(cmd = {}) {
  const ai = commandAiParse(cmd);
  const out = [];
  const seen = new Set();
  const add = (value) => {
    const code = String(value || '').trim();
    if (!/^\d{5,12}$/.test(code) || seen.has(code)) return;
    seen.add(code);
    out.push(code);
  };
  [
    ...(Array.isArray(cmd.agent_codes) ? cmd.agent_codes : []),
    ...(Array.isArray(ai.agent_codes) ? ai.agent_codes : []),
    cmd.agent_code,
    cmd.agentCode,
    ai.agent_code
  ].forEach(add);
  for (const match of commandSourceText(cmd).matchAll(/\b(\d{5,12})\b/g)) {
    add(match[1]);
  }
  return out.slice(0, 30);
}

function memberLineFromSource(cmd = {}, memberName = '', codes = []) {
  const cleanCodes = codes.filter(Boolean);
  if (cleanCodes.length <= 1) return cleanCodes;
  const lines = commandSourceText(cmd).split(/\r?\n/);
  const member = cleanMemberToken(memberName);
  for (const line of lines) {
    if (!line.toLowerCase().includes(member)) continue;
    const matched = [...line.matchAll(/\b(\d{5,12})\b/g)].map((item) => item[1]);
    if (matched.length) return matched.filter((code) => cleanCodes.includes(code));
  }
  return cleanCodes;
}

async function queryMemberLine(config, memberName, expectedCodes = []) {
  const member = await findExactMember(config, memberName);
  const actualCode = await decryptedTopInviteCode(config, member);
  const code = String(actualCode || '').trim();
  const official = !code || code === '0';
  const matched = !!(code && expectedCodes.includes(code));
  return {
    member,
    actualCode: code,
    official,
    matched,
    otherLine: !!(code && expectedCodes.length && !matched)
  };
}

function lineReplyText(result, expectedCodes = [], agentLineMode = false) {
  if (expectedCodes.length) {
    if (result.matched) return agentLineMode ? result.actualCode : '在线下';
    return result.official ? '官网' : '其他线下';
  }
  return result.official ? '官网' : '在线下';
}

async function runQueryMemberLineCommand(config, cmd, targetValue) {
  const members = commandMembers(cmd, targetValue);
  if (!members.length) throw new Error('未提取到会员账号');
  const codes = commandAgentCodes(cmd);
  const agentLineMode = /查代理线/.test(commandSourceText(cmd)) || String(commandAiParse(cmd).line_mode || cmd.line_mode || '').includes('agent');
  const multiple = members.length > 1;
  const lines = [];
  for (const memberName of members) {
    const result = await queryMemberLine(config, memberName, memberLineFromSource(cmd, memberName, codes));
    const reply = lineReplyText(result, codes, agentLineMode);
    lines.push(multiple ? `${memberName} ${reply}` : reply);
  }
  const replyText = lines.join('\n');
  await setStatus({ state: 'success', message: `查线完成：${members.length}人`, detail: replyText });
  await ack(config, cmd, 'reply_origin', `查线完成：${members.length}人`, { reply_text: replyText, stop_actions: true });
}

function loginLogRange(days = 360) {
  const today = dateOnly(new Date());
  const safeDays = Math.min(360, Math.max(1, Math.floor(Number(days || 360))));
  return {
    startTime: `${formatDate(addDays(today, 1 - safeDays))} 00:00:00`,
    endTime: `${formatDate(today)} 23:59:59`
  };
}

async function queryLoginLogs(config, memberName, days = 360) {
  if (!config.loginLogUrl) throw new Error('登录日志接口未配置');
  const safeDays = Math.min(360, Math.max(1, Math.floor(Number(days || 360))));
  const range = loginLogRange(safeDays);
  await setStatus({ state: 'running', message: `查询登录日志 ${memberName}` });
  const result = await postJson(config.loginLogUrl, config.headers, {
    pageNum: 1,
    pageSize: 10,
    startTime: range.startTime,
    endTime: range.endTime,
    name: memberName,
    topName: ''
  });
  if (!apiOk(result.res, result.data)) {
    const message = `${((result.data || {}).message) || ''} ${result.text || ''}`;
    if (/日期查询范围不能超过360天|不能超过\s*360\s*天/.test(message) && safeDays > 1) {
      return queryLoginLogs(config, memberName, Math.min(359, safeDays - 1));
    }
    throw new Error(`查询登录日志失败 HTTP ${result.res.status}: ${result.text.slice(0, 300)}`);
  }
  return ((((result.data || {}).data || {}).list) || []).filter((item) => String(item.name || '').toLowerCase() === memberName);
}

function terminalLabel(value) {
  const text = String(value || '').toLowerCase();
  if (text.includes('sport_android')) return '安卓体育';
  if (text.includes('sport_ios')) return '苹果体育';
  if (text.includes('android')) return '安卓全站';
  if (text.includes('ios')) return '苹果全站';
  if (text.includes('h5')) return 'H5';
  if (text.includes('web') || text.includes('pc')) return 'PC网页';
  return String(value || '未知设备');
}

function provinceLabel(address) {
  const parts = String(address || '').split('|').map((item) => item.trim()).filter(Boolean);
  return parts[1] || parts[2] || parts[0] || '未知地区';
}

function loginDeviceIpSummary(memberName, logs = []) {
  const seen = new Set();
  const parts = [];
  for (const log of logs) {
    const item = `${terminalLabel(log.terminal)} ${provinceLabel(log.address)}`.trim();
    if (!item || seen.has(item)) continue;
    seen.add(item);
    parts.push(item);
  }
  return parts.length ? `${memberName} ${parts.join('；')}` : `${memberName} 暂无登录记录`;
}

function relatedCountLabel(label, count) {
  const value = Math.max(0, Number(count || 0));
  if (value <= 0) return `${label}无关联`;
  if (value >= 5) return `${label}关联多个`;
  return `${label}关联 ${value}`;
}

function sameIpDeviceSummary(memberName, logs = []) {
  let deviceTotal = 1;
  let ipTotal = 1;
  for (const log of logs) {
    const related = Number(log.related || 0);
    const ipRelated = Number(log.ipRelateUserCount || log.countIp || 0);
    if (Number.isFinite(related) && related > deviceTotal) deviceTotal = related;
    if (Number.isFinite(ipRelated) && ipRelated > ipTotal) ipTotal = ipRelated;
  }
  const deviceRelated = Math.max(0, deviceTotal - 1);
  const ipRelated = Math.max(0, ipTotal - 1);
  return `${memberName} ${relatedCountLabel('设备', deviceRelated)}  ${relatedCountLabel('IP', ipRelated)}`;
}

async function guardedLineCheck(config, cmd, memberName, codes) {
  const expectedCodes = memberLineFromSource(cmd, memberName, codes);
  if (!expectedCodes.length) return { ok: true };
  const result = await queryMemberLine(config, memberName, expectedCodes);
  if (!result.matched) return { ok: false, replyText: `${memberName}不在线下。` };
  return { ok: true };
}

async function runQueryLoginDeviceIpCommand(config, cmd, targetValue) {
  const members = commandMembers(cmd, targetValue);
  if (!members.length) throw new Error('未提取到会员账号');
  const codes = commandAgentCodes(cmd);
  const lines = [];
  for (const memberName of members) {
    const line = await guardedLineCheck(config, cmd, memberName, codes);
    if (!line.ok) {
      lines.push(line.replyText);
      continue;
    }
    const logs = await queryLoginLogs(config, memberName, 360);
    lines.push(loginDeviceIpSummary(memberName, logs));
  }
  const replyText = lines.join('\n');
  await setStatus({ state: 'success', message: `登录设备/IP查询完成：${members.length}人`, detail: replyText });
  await ack(config, cmd, 'reply_origin', `登录设备/IP查询完成：${members.length}人`, { reply_text: replyText, stop_actions: true });
}

async function runQuerySameIpDeviceCommand(config, cmd, targetValue) {
  const members = commandMembers(cmd, targetValue);
  if (!members.length) throw new Error('未提取到会员账号');
  const codes = commandAgentCodes(cmd);
  const lines = [];
  for (const memberName of members) {
    const line = await guardedLineCheck(config, cmd, memberName, codes);
    if (!line.ok) {
      lines.push(line.replyText);
      continue;
    }
    const logs = await queryLoginLogs(config, memberName, 360);
    lines.push(logs.length ? sameIpDeviceSummary(memberName, logs) : `${memberName} 暂无登录记录`);
  }
  const replyText = lines.join('\n');
  await setStatus({ state: 'success', message: `同IP/设备查询完成：${members.length}人`, detail: replyText });
  await ack(config, cmd, 'reply_origin', `同IP/设备查询完成：${members.length}人`, { reply_text: replyText, stop_actions: true });
}

async function queryBulletFrameLogs(config, uuid) {
  if (!config.bulletFrameLogUrl) throw new Error('设备关联接口未配置');
  const cleanUuid = String(uuid || '').trim();
  if (!cleanUuid) throw new Error('登录记录缺少设备UUID');
  const pageSize = 50;
  const list = [];
  for (let pageNum = 1; pageNum <= 5; pageNum += 1) {
    await setStatus({ state: 'running', message: `查询同设备关联 ${cleanUuid}` });
    const result = await postJson(config.bulletFrameLogUrl, config.headers, {
      pageNum,
      pageSize,
      ifNeedTag: 1,
      uuid: cleanUuid
    });
    if (!apiOk(result.res, result.data)) {
      throw new Error(`查询同设备关联失败 HTTP ${result.res.status}: ${result.text.slice(0, 300)}`);
    }
    const data = ((result.data || {}).data || {});
    list.push(...(Array.isArray(data.list) ? data.list : []));
    const pages = Math.max(1, Number(data.pages || 1));
    if (pageNum >= pages) break;
  }
  return list;
}

function commandSiteLabel(cmd = {}, fallbackSite = '') {
  const source = commandSourceText(cmd).replace(/\s+/g, '').toLowerCase();
  const hint = String(cmd.backend_site || cmd.site || commandAiParse(cmd).site || fallbackSite || '').trim().toLowerCase();
  if (source.includes('jn站') || source.includes('6站') || hint === '6' || hint === '6001' || hint === 'jn') return 'jn站';
  return 'ml站';
}

function disableDeviceReason(cmd = {}) {
  const match = commandSourceText(cmd).match(/禁用原因\s*[:：]\s*([^\r\n]+)/i);
  return String((match && match[1]) || cmd.disable_reason || commandAiParse(cmd).reason || '广告引流').trim() || '广告引流';
}

function disableDeviceFlag(cmd = {}) {
  const match = commandSourceText(cmd).match(/设备号禁用\s*[:：]\s*([^\r\n]+)/i);
  return String((match && match[1]) || cmd.disable_device_flag || '是').trim() || '是';
}

function disableDeviceTelegramText(cmd = {}, memberName = '', uuid = '') {
  return [
    commandSiteLabel(cmd, actionSite('disable_login_device', cmd)),
    '申请禁用设备',
    `会员账号： ${memberName}`,
    `设备号禁用：${disableDeviceFlag(cmd)}`,
    `禁用原因： ${disableDeviceReason(cmd)}`,
    String(uuid || '').trim()
  ].filter((line) => line !== '').join('\n');
}

function relatedDeviceAccounts(items = [], memberName = '') {
  const current = cleanMemberToken(memberName);
  const seen = new Set();
  const accounts = [];
  for (const item of items || []) {
    const name = cleanMemberToken(item && item.name);
    if (!name || name === current || seen.has(name) || !isLikelyBackendMember(name)) continue;
    seen.add(name);
    accounts.push(name);
  }
  return accounts;
}

async function runDisableLoginDeviceCommand(config, cmd, targetValue) {
  if (!config.loginLogUrl) throw new Error('登录日志接口未配置');
  if (!config.bulletFrameLogUrl) throw new Error('设备关联接口未配置');
  const memberName = commandMembers(cmd, targetValue)[0] || cleanMemberToken(targetValue);
  if (!memberName || !isLikelyBackendMember(memberName)) throw new Error('未提取到会员账号');
  const logs = await queryLoginLogs(config, memberName, 360);
  const latest = logs.find((item) => String(item.uuid || '').trim())
    || logs.find((item) => cleanMemberToken(item.name) === memberName);
  const uuid = String((latest && latest.uuid) || '').trim();
  if (!uuid) throw new Error(`${memberName} 未找到可用设备UUID`);
  const deviceLogs = await queryBulletFrameLogs(config, uuid);
  const accounts = relatedDeviceAccounts(deviceLogs, memberName);
  const replyText = accounts.length ? `${accounts.join('、')} 已反馈。` : '无关联，已反馈。';
  const telegramText = disableDeviceTelegramText(cmd, memberName, uuid);
  const telegramTarget = String(cmd.telegram_target || cmd.forward_to || DEFAULT_DISABLE_DEVICE_TG_TARGET);
  await sendTelegramMessageFromCommand(
    config,
    { ...cmd, action: 'disable_login_device', telegram_target: telegramTarget },
    telegramText,
    { action: 'disable_login_device', targetLabel: '禁用设备TG群' }
  );
  const msg = `禁用设备申请已反馈：${memberName} ${uuid}`;
  await setStatus({ state: 'success', message: msg, detail: `${replyText}\n${telegramText}` });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
}

function normalizeVenueName(value) {
  return String(value || '').replace(/\s+/g, '').replace(/场馆|钱包|游戏|查询|流水|还差多少/g, '').toLowerCase();
}

function commandVenueHint(cmd = {}) {
  const ai = commandAiParse(cmd);
  return String(cmd.venue || cmd.venue_name || cmd.venueName || ai.venue || ai.venue_name || '').trim();
}

function resolveWalletChannel(wallets = [], cmd = {}) {
  const text = `${commandVenueHint(cmd)} ${commandSourceText(cmd)}`;
  const normalizedText = normalizeVenueName(text);
  const aliases = [
    ['米兰体育', 'YBTY'],
    ['熊猫体育', 'DBTY'],
    ['米兰电竞', 'YBDJ'],
    ['米兰棋牌', 'YBQP'],
    ['米兰彩票', 'YBCP'],
    ['米兰真人', 'YBZR'],
    ['米兰电子', 'PMDZ'],
    ['米兰捕鱼', 'YBDZ']
  ];
  for (const [label, code] of aliases) {
    if (normalizedText.includes(normalizeVenueName(label))) {
      const matched = wallets.find((item) => String(item.channelCode || '').toUpperCase() === code);
      if (matched) return matched;
    }
  }
  return wallets.find((item) => {
    const name = normalizeVenueName(item.channelName);
    const code = normalizeVenueName(item.channelCode);
    return (name && normalizedText.includes(name)) || (code && normalizedText.includes(code));
  }) || null;
}

async function runQueryVenueTurnoverCommand(config, cmd, targetValue) {
  if (!config.gameWalletListUrl || !config.gameTransferOutUrl || !config.gameTransferIntoUrl) {
    throw new Error('场馆钱包接口未配置');
  }
  const memberName = (commandMembers(cmd, targetValue)[0] || cleanMemberToken(targetValue));
  if (!memberName) throw new Error('未提取到会员账号');
  const member = await findExactMember(config, memberName);
  const memberId = member.exactId || member.id;
  await setStatus({ state: 'running', message: `查询场馆钱包 ${memberName}` });
  const wallet = await postJson(config.gameWalletListUrl, config.headers, { id: Number(memberId) });
  if (!apiOk(wallet.res, wallet.data)) {
    throw new Error(`查询场馆钱包失败 HTTP ${wallet.res.status}: ${wallet.text.slice(0, 300)}`);
  }
  const venues = Array.isArray((wallet.data || {}).data) ? wallet.data.data : [];
  const channel = resolveWalletChannel(venues, cmd);
  if (!channel) throw new Error('未匹配到场馆钱包');
  const body = { id: Number(memberId), channelCode: String(channel.channelCode || ''), money: 1, name: memberName };
  await setStatus({ state: 'running', message: `尝试转出 ${channel.channelName || channel.channelCode} 1元` });
  const out = await postJson(config.gameTransferOutUrl, config.headers, body);
  const outMsg = String((out.data || {}).message || out.text || '').trim();
  let replyText = '';
  if (apiOk(out.res, out.data)) {
    await setStatus({ state: 'running', message: `转回 ${channel.channelName || channel.channelCode} 1元` });
    const back = await postJson(config.gameTransferIntoUrl, config.headers, body);
    if (!apiOk(back.res, back.data)) {
      throw new Error(`1元转回失败 HTTP ${back.res.status}: ${back.text.slice(0, 300)}`);
    }
    replyText = '场馆未锁定，清除缓存刷新再试一下。';
  } else if (outMsg) {
    replyText = outMsg;
  } else {
    throw new Error(`查询流水锁定失败 HTTP ${out.res.status}: ${out.text.slice(0, 300)}`);
  }
  const msg = `场馆流水锁定查询完成：${memberName} ${channel.channelName || channel.channelCode}`;
  await setStatus({ state: 'success', message: msg, detail: replyText });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
}

async function queryAllRebateLevels(config) {
  if (!config.rebateLevelListUrl) throw new Error('返水等级接口未配置');
  const levels = [];
  let pageNum = 1;
  let totalPage = 1;
  do {
    const result = await postJson(config.rebateLevelListUrl, config.headers, { pageNum, pageSize: 10 });
    if (!apiOk(result.res, result.data)) {
      throw new Error(`查询返水等级失败 HTTP ${result.res.status}: ${result.text.slice(0, 300)}`);
    }
    const data = ((result.data || {}).data || {});
    levels.push(...(Array.isArray(data.list) ? data.list : []));
    totalPage = Math.max(1, Number(data.totalPage || 1));
    pageNum += 1;
  } while (pageNum <= totalPage && pageNum <= 20);
  return levels;
}

async function queryVenueList(config) {
  if (!config.venueQueryUrl) throw new Error('场馆列表接口未配置');
  const result = await postJson(config.venueQueryUrl, config.headers, {});
  if (!apiOk(result.res, result.data)) {
    throw new Error(`查询场馆列表失败 HTTP ${result.res.status}: ${result.text.slice(0, 300)}`);
  }
  return Array.isArray((result.data || {}).data) ? result.data.data : [];
}

function parseRebateInfoData(result) {
  const losslessText = String((result && result.text) || '').replace(
    /("id"\s*:\s*)(-?\d{16,})(?=\s*[,}])/g,
    '$1"$2"'
  );
  const parsed = parseJsonText(losslessText);
  return ((parsed || {}).data || {});
}

function rebateInfoSaveBodyText(body) {
  return JSON.stringify(body).replace(/("infoId"\s*:\s*)"(\d+)"/g, '$1$2');
}

function resolveRebateVenue(venues = [], cmd = {}) {
  const text = `${commandVenueHint(cmd)} ${commandSourceText(cmd)}`;
  const normalizedText = normalizeVenueName(text);
  return venues.find((item) => {
    const zh = normalizeVenueName(item.zhName);
    const en = normalizeVenueName(item.enName);
    return (zh && normalizedText.includes(zh)) || (en && normalizedText.includes(en));
  }) || null;
}

function commandGameHint(cmd = {}, venue = {}) {
  const ai = commandAiParse(cmd);
  const direct = String(cmd.game || cmd.game_name || cmd.gameName || ai.game || ai.game_name || '').trim();
  if (direct) return direct;
  let text = commandSourceText(cmd)
    .replace(/配置返水|返水配置|配置|返水|场馆|游戏|查询/g, ' ')
    .replace(/6站|9站|JN站|ML站|JN|ML/gi, ' ');
  [venue.zhName, venue.enName].filter(Boolean).forEach((name) => {
    text = text.replace(new RegExp(escapeRegExp(name), 'gi'), ' ');
  });
  return text.split(/[\s,，。；;、]+/).map((item) => item.trim()).filter((item) => item && item.length <= 40)[0] || '';
}

async function runConfigureRebateCommand(config, cmd, targetValue) {
  if (!config.rebateLevelInfoListUrl || !config.rebateLevelInfoSaveUrl) {
    throw new Error('返水配置接口未配置');
  }
  const siteLabel = profileForSite(actionSite('configure_rebate', cmd)).label;
  const venues = await queryVenueList(config);
  const venue = resolveRebateVenue(venues, cmd);
  if (!venue) throw new Error(`${siteLabel}未匹配到返水场馆`);
  const gameHint = commandGameHint(cmd, venue) || String(targetValue || '').trim();
  if (!gameHint) throw new Error(`${siteLabel}未提取到返水游戏`);
  const levels = await queryAllRebateLevels(config);
  if (!levels.length) throw new Error(`${siteLabel}未查询到返水等级`);
  let matchedLevels = 0;
  for (const level of levels) {
    await setStatus({ state: 'running', message: `配置返水 ${venue.zhName || venue.enName} ${gameHint} ${level.name || `VIP${level.level}`}` });
    const baseInfo = await postJson(config.rebateLevelInfoListUrl, config.headers, {
      parentId: Number(level.id),
      venueId: '50'
    });
    if (!apiOk(baseInfo.res, baseInfo.data)) {
      throw new Error(`${siteLabel}查询返水默认配置失败 HTTP ${baseInfo.res.status}: ${baseInfo.text.slice(0, 300)}`);
    }
    const baseInfoData = parseRebateInfoData(baseInfo);
    let targetInfoData = baseInfoData;
    if (String(venue.id) !== '50') {
      const targetInfo = await postJson(config.rebateLevelInfoListUrl, config.headers, {
        parentId: Number(level.id),
        venueId: String(venue.id)
      });
      if (!apiOk(targetInfo.res, targetInfo.data)) {
        throw new Error(`${siteLabel}查询返水游戏失败 HTTP ${targetInfo.res.status}: ${targetInfo.text.slice(0, 300)}`);
      }
      targetInfoData = parseRebateInfoData(targetInfo);
    }
    const baseInfoList = Array.isArray(baseInfoData.infoList) ? baseInfoData.infoList : [];
    const targetInfoList = Array.isArray(targetInfoData.infoList) ? targetInfoData.infoList : [];
    const matchedGame = targetInfoList.find((item) => {
      const gameName = normalizeVenueName(item.gameName);
      const gameCode = normalizeVenueName(item.gameCode);
      const hint = normalizeVenueName(gameHint);
      return hint && ((gameName && gameName.includes(hint)) || (gameCode && gameCode.includes(hint)) || (gameName && hint.includes(gameName)));
    });
    if (!matchedGame) continue;
    const infoList = String(venue.id) === '50' ? baseInfoList : [...baseInfoList, ...targetInfoList];
    const saveBody = {
      isLimit: Number(level.isLimit ?? 1),
      minLimit: Number(targetInfoData.minLimit ?? baseInfoData.minLimit ?? level.minLimit ?? 1),
      maxLimit: Number(targetInfoData.maxLimit ?? baseInfoData.maxLimit ?? level.maxLimit ?? 0),
      infoList: infoList.map((item) => ({
        venueId: String(item.venueId || venue.id),
        infoId: String(item.id || ''),
        rate: Number(item.rate || 0)
      })).filter((item) => item.infoId)
    };
    const saved = await postJsonText(config.rebateLevelInfoSaveUrl, config.headers, rebateInfoSaveBodyText(saveBody));
    if (!apiOk(saved.res, saved.data)) {
      throw new Error(`${siteLabel}保存返水配置失败 HTTP ${saved.res.status}: ${saved.text.slice(0, 300)}`);
    }
    matchedLevels += 1;
  }
  if (!matchedLevels) throw new Error(`${siteLabel}未找到返水游戏：${venue.zhName || venue.enName} ${gameHint}`);
  const replyText = `${siteLabel}返水配置已提交：${venue.zhName || venue.enName} ${gameHint}`;
  await setStatus({ state: 'success', message: replyText, detail: `已处理 ${matchedLevels} 个VIP等级` });
  await ack(config, cmd, 'reply_origin', replyText, { reply_text: replyText, stop_actions: true });
}

function requestedDataOverviewFields(cmd = {}) {
  const sourceText = [
    cmd.source_text,
    cmd.sourceText,
    cmd.original_text,
    cmd.originalText,
    cmd.message,
    cmd.text
  ].filter(Boolean).join(' ');
  const sourceLabels = dataOverviewFieldLabelsFromText(sourceText);
  if (sourceLabels.length) return dataOverviewFieldsFromLabels(sourceLabels);
  const explicitLabels = dataOverviewFieldLabelsFromValues([cmd.data_fields, cmd.dataFields]);
  return explicitLabels.length ? dataOverviewFieldsFromLabels(explicitLabels) : [];
}

function normalizeDataOverviewFieldLabel(value) {
  const text = normalizeText(value);
  if (!text) return '';
  for (const field of DATA_OVERVIEW_FIELDS) {
    if ((field.aliases || []).some((alias) => text === normalizeText(alias))) {
      return field.label;
    }
  }
  return '';
}

function pushDataOverviewLabel(out, seen, label) {
  if (!label || seen.has(label)) return;
  seen.add(label);
  out.push(label);
}

function dataOverviewFieldLabelsFromValues(values = []) {
  const out = [];
  const seen = new Set();
  const add = (value) => {
    if (Array.isArray(value)) {
      value.forEach(add);
      return;
    }
    String(value || '').split(/[，,、;；\s]+/).forEach((item) => {
      pushDataOverviewLabel(out, seen, normalizeDataOverviewFieldLabel(item));
    });
  };
  values.forEach(add);
  return out;
}

function dataOverviewFieldLabelsFromText(value = '') {
  const raw = String(value || '');
  const matches = [];
  for (const field of DATA_OVERVIEW_FIELDS) {
    for (const alias of field.aliases || []) {
      const matcher = new RegExp(escapeRegExp(alias), 'g');
      let match;
      while ((match = matcher.exec(raw))) {
        matches.push({ index: match.index, length: alias.length, label: field.label });
        if (!alias.length) break;
      }
    }
  }
  matches.sort((a, b) => (a.index - b.index) || (b.length - a.length));
  const out = [];
  const seen = new Set();
  for (const item of matches) {
    pushDataOverviewLabel(out, seen, item.label);
  }
  return out;
}

function dataOverviewFieldsFromLabels(labels = []) {
  const fieldsByLabel = new Map(DATA_OVERVIEW_FIELDS.map((field) => [field.label, field]));
  return labels.map((label) => fieldsByLabel.get(label)).filter(Boolean);
}

function settlementRollbackText(order = {}, detail = {}) {
  return normalizeText([
    order.remark,
    order.description,
    detail.remark,
    detail.description,
    detail.noSettlementRemark,
    detail.noSettlementLog
  ].filter(Boolean).join(' '));
}

function isSettlementRollback(order = {}, detail = {}) {
  return /结算回滚|結算回滾|回滚结算|回滾結算|settlementrollback|rollback/.test(settlementRollbackText(order, detail));
}

function orderHasSettlementRollback(order = {}) {
  return isSettlementRollback(order, {}) || orderDetails(order).some((detail) => isSettlementRollback(order, detail));
}

function withSettlementRollbackPrefix(replyText, order = {}, detail = {}) {
  const raw = String(replyText || '').trim();
  if (!raw || !isSettlementRollback(order, detail)) return raw;
  if (/^结算回滚|^結算回滾/.test(raw)) return raw;
  return `结算回滚了 请等待重新结算 ${raw}`;
}

function detailIsSettled(detail = {}, order = {}) {
  if (isSettlementRollback(order, detail)) return false;
  const settleTimes = numericValue(detail.settleTimes);
  if (settleTimes !== null) return settleTimes > 0;
  const betStatus = numericValue(detail.betStatus);
  const betResult = numericValue(detail.betResult);
  return betStatus === 1 && betResult !== 0;
}

function detailIsUnsettled(detail = {}, order = {}) {
  if (isSettlementRollback(order, detail)) return true;
  const settleTimes = numericValue(detail.settleTimes);
  if (settleTimes !== null) return settleTimes <= 0;
  const betStatus = numericValue(detail.betStatus);
  if (betStatus === 0 || betStatus === 6) return true;
  const betResult = numericValue(detail.betResult);
  return betResult === 0 && betStatus !== 1;
}

function unresolvedOrderDetails(order = {}) {
  const details = orderDetails(order);
  if (!details.length) return [];
  return details.filter((detail) => detailIsUnsettled(detail, order));
}

function detailMatchId(order = {}, detail = {}) {
  return String(detail.matchId || order.standardMatchId || detail.standardMatchId || '').trim();
}

function detailsMatchIds(order = {}, details = []) {
  return [...new Set(details.map((detail) => detailMatchId(order, detail)).filter(Boolean))];
}

function detailsText(order = {}, details = []) {
  return details.map((detail) => {
    const matchId = detailMatchId(order, detail);
    const match = ticketMatchText(detail);
    return [matchId ? `赛事ID ${matchId}` : '', match].filter(Boolean).join(' ');
  }).filter(Boolean).join('；');
}

function settlementReasonText(detail = {}) {
  return htmlText([
    detail.noSettlementRemark,
    detail.noSettlementLog,
    detail.noSettlementMarket,
    detail.noSettlementMatch,
    detail.noSettlementStatus
  ].filter(Boolean).join(' '));
}

function parseBeijingTime(value) {
  if (value === undefined || value === null || value === '') return null;
  const rawText = String(value).trim();
  if (/^\d+$/.test(rawText)) {
    const raw = Number(rawText);
    if (!Number.isFinite(raw) || raw <= 0) return null;
    return raw > 100000000000 ? raw : raw * 1000;
  }
  const match = rawText.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+|T)(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?/);
  if (!match) return null;
  const [, y, m, d, h, min, s] = match;
  const utcMs = Date.UTC(
    Number(y),
    Number(m) - 1,
    Number(d),
    Number(h) - 8,
    Number(min),
    Number(s || 0)
  );
  return Number.isFinite(utcMs) ? utcMs : null;
}

function orderBeginTimeMillis(order = {}, detail = {}) {
  const candidates = [
    detail.beginTimeStr,
    detail.beginTime,
    detail.matchBeginTime,
    detail.begin_time,
    detail.startTime,
    detail.start_time,
    order.beginTimeStr,
    order.beginTime,
    order.matchBeginTime,
    order.begin_time,
    order.startTime,
    order.start_time
  ];
  for (const item of candidates) {
    const ms = parseBeijingTime(item);
    if (ms) return ms;
  }
  return null;
}

function orderStatusLabel(status) {
  const value = Number(status);
  if (value === 0) return '未结算';
  if (value === 1) return '已结算';
  if (value === 2) return '已取消';
  return `状态${status}`;
}

function failureRiskText(order = {}, detail = {}) {
  const direct = String(detail.riskEvent || order.riskEvent || '').trim();
  if (direct) return normalizeRiskReason(direct);
  const remark = String(detail.remark || order.remark || '').trim();
  const reasonMatch = remark.match(/原因[:：]\s*([^，,。\s]+)/);
  if (reasonMatch) return normalizeRiskReason(reasonMatch[1].trim());
  const eventMatch = remark.match(/([A-Za-z_]+|[\u4e00-\u9fa5]+)事件拒单/);
  if (eventMatch) return normalizeRiskReason(eventMatch[1].trim());
  return '盘口变动';
}

function normalizeRiskReason(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const compact = normalizeText(raw);
  const lower = raw.toLowerCase().replace(/[\s-]+/g, '_');
  const aliases = [
    [/possible_penalty|penalty/i, '可能点球'],
    [/suspected_match_fixing|match_fixing|fixed_match|suspected_fixed|agreement_match|protocol_match|suspected_agreement|suspected_protocol/i, '疑似协议赛'],
    [/odds_change|odds_changed|odds_error|odd_change|price_change/i, '盘口变动'],
    [/late_bet|delay_bet|bet_delay/i, '延迟投注'],
    [/abnormal_bet|risk_bet/i, '异常投注']
  ];
  for (const [pattern, label] of aliases) {
    if (pattern.test(lower)) return label;
  }
  if (/疑似.*协议|协议赛|假球|操控|对打/.test(compact)) return '疑似协议赛';
  if (/可能点球|点球/.test(compact)) return '可能点球';
  if (/盘口|赔率/.test(compact)) return '盘口变动';
  return raw;
}

function ticketMatchText(detail = {}) {
  return String(detail.matchInfo || [detail.homeName, detail.awayName].filter(Boolean).join(' v ') || '').trim();
}

function betFailureReply(order = {}, detail = {}) {
  const matchText = ticketMatchText(detail) || '相关赛事';
  const riskText = failureRiskText(order, detail);
  return `您好，经核实，因用户下注确认期间其中赛事：${matchText} ${riskText} 导致投注失败，属于系统正常拒单，本金已退回，谢谢。`;
}

function detailIsBetFailed(detail = {}) {
  return Number(detail.betStatus) === 5;
}

function detailIsCanceled(detail = {}) {
  return Number(detail.betStatus) === 3 || Number(detail.cancelType) > 0;
}

function orderIsBetFailed(order = {}, detail = {}) {
  return Number(order.orderStatus) === 4 || detailIsBetFailed(detail);
}

function orderIsCanceled(order = {}, detail = {}) {
  return Number(order.orderStatus) === 2 || detailIsCanceled(detail);
}

function detailReasonRawText(detail = {}) {
  return [
    detail.cancelReasonName,
    detail.cancelReason,
    detail.riskEvent,
    detail.remark,
    detail.description
  ].filter(Boolean).join(' ');
}

function detailHasCancelReason(detail = {}) {
  if (detailIsCanceled(detail)) return true;
  const text = normalizeText(detailReasonRawText(detail));
  return /取消|无效|無效|退回|退还|退還|本金|弃赛|棄賽|退赛|退賽|中断|中斷|延期|推迟|推遲|协议赛|協議賽|盘口|盤口|赔率|賠率|点球|點球/.test(text);
}

function detailHasFailureReason(detail = {}) {
  if (detailIsBetFailed(detail)) return true;
  const text = normalizeText(detailReasonRawText(detail));
  return /拒单|拒單|投注失败|投注失敗|失败|失敗|延迟投注|延遲投注|盘口|盤口|赔率|賠率|点球|點球|协议赛|協議賽/.test(text);
}

function ticketReasonMode(cmd = {}) {
  const text = commandSourceText(cmd);
  if (/取消原因|无效原因|注单取消|取消|无效|無效/.test(text)) return 'cancel';
  if (/投注失败|投注失敗|失败原因|失敗原因|失败|失敗/.test(text)) return 'failure';
  return 'any';
}

function ticketReasonDetail(order = {}, fallback = {}, mode = 'any') {
  const details = orderDetails(order);
  const canceled = details.find((detail) => detailHasCancelReason(detail));
  const failed = details.find((detail) => detailHasFailureReason(detail));
  if (mode === 'cancel' && canceled) return canceled;
  if (mode === 'failure' && failed) return failed;
  if (canceled) return canceled;
  if (failed) return failed;
  const reasonDetails = details.filter((detail) => detailHasCancelReason(detail) || detailHasFailureReason(detail));
  if (reasonDetails.length) return reasonDetails[0];
  if (orderIsCanceled(order, fallback) || orderIsBetFailed(order, fallback)) {
    return fallback && Object.keys(fallback).length ? fallback : firstOrderDetail(order);
  }
  return fallback && Object.keys(fallback).length ? fallback : firstOrderDetail(order);
}

function cancelReasonText(order = {}, detail = {}) {
  return normalizeRiskReason(
    detail.cancelReasonName
    || detail.cancelReason
    || detail.riskEvent
    || order.cancelReasonName
    || order.cancelReason
    || order.riskEvent
    || failureRiskText(order, detail)
  ) || '风控原因';
}

function ticketCancelReasonReply(order = {}, detail = {}) {
  return `因${cancelReasonText(order, detail)}，注单取消，退本金。`;
}

function ticketReasonRequested(cmd = {}) {
  const text = commandSourceText(cmd);
  return /无效|失败原因|投注失败|取消原因|无效原因|注单取消/.test(text)
    || (/失败/.test(text) && !/催结算失败|自动处理失败|后台.*失败/.test(text));
}

function ticketReasonReplyForOrder(order = {}, detail = {}, orderNo = '', cmd = {}) {
  if (orderIsBetFailed(order, detail)) {
    return {
      replyText: betFailureReply(order, detail),
      msg: `注单失败原因已回复：${orderNo}`
    };
  }
  if (orderIsCanceled(order, detail)) {
    return {
      replyText: ticketCancelReasonReply(order, detail),
      msg: `注单取消原因已回复：${orderNo}`
    };
  }
  if (Number(order.orderStatus) !== 0) {
    return {
      replyText: String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。'),
      msg: `注单取消/失败原因跳过：${orderNo} ${orderStatusLabel(order.orderStatus)}`
    };
  }
  return {
    replyText: String(cmd.pending_reply || '注单目前未结算，暂未取消。'),
    msg: `注单取消/失败原因跳过：${orderNo} 未结算`
  };
}

function ticketReasonTerms(order = {}, detail = {}) {
  const reason = normalizeRiskReason(cancelReasonText(order, detail));
  const terms = new Set();
  const add = (value) => {
    const text = normalizeText(value);
    if (text && text.length >= 2) terms.add(text);
  };
  add(reason);
  if (/疑似协议赛/.test(reason)) {
    ['协议赛', '疑似协议', '假球', '操控', '对打'].forEach(add);
  }
  if (/可能点球/.test(reason)) {
    ['点球', '可能点球', 'penalty'].forEach(add);
  }
  if (/盘口变动|赔率/.test(reason)) {
    ['盘口', '盘口变动', '赔率', '赔率错误', 'odds'].forEach(add);
  }
  if (/延迟投注/.test(reason)) {
    ['延迟投注', '延迟下注', 'latebet'].forEach(add);
  }
  if (/弃赛|棄賽|退赛|退賽|中途/.test(reason)) {
    ['弃赛', '棄賽', '中途弃赛', '中途棄賽', '退赛', '退賽', 'retired', 'walkover'].forEach(add);
  }
  if (/中断|中斷/.test(reason)) {
    ['比赛中断', '比賽中斷', '赛事中断', '賽事中斷', '中断', '中斷', 'interrupted'].forEach(add);
  }
  if (/延期|推迟|推遲/.test(reason)) {
    ['比赛延期', '比賽延期', '赛事延期', '賽事延期', '推迟', '推遲', '延期', 'postponed'].forEach(add);
  }
  return [...terms];
}

function noticeText(item = {}) {
  return [
    item.title,
    item.context,
    item.zhTitle,
    item.zhContext,
    item.enTitle,
    item.enContext
  ].filter(Boolean).join(' ');
}

function scoreInvalidNotice(item = {}, order = {}, detail = {}) {
  const text = normalizeText(noticeText(item));
  const matchInfo = normalizeText(ticketMatchText(detail));
  const home = normalizeText(detail.homeName);
  const away = normalizeText(detail.awayName);
  const play = normalizeText(detail.playName || detail.originalPlay || '');
  const option = normalizeText(detail.playOptionName || detail.marketValue || '');
  const reasonTerms = ticketReasonTerms(order, detail);
  const marketScore = scoreNoticeMarketTerms(item, detail);
  let score = 0;
  let reasonMatched = false;
  for (const term of reasonTerms) {
    if (term && text.includes(term)) {
      score += 160;
      reasonMatched = true;
      break;
    }
  }
  score += marketScore;
  if (matchInfo && text.includes(matchInfo.replace('v', 'vs'))) score += 30;
  if (home && text.includes(home)) score += 15;
  if (away && text.includes(away)) score += 15;
  if (play && text.includes(play)) score += 10;
  if (option && text.includes(option)) score += 6;
  if (/无效|無效|invalid|取消|取消订单|取消訂單|拒单|拒單|失败|失敗|退回|退还|退還|本金|赔率错误|賠率錯誤|盘口错误|盤口錯誤/.test(text)) score += 50;
  if (/不能按时结算|不能按時結算|delaysettlement|赛果不明确|賽果不明確|赛果将进一步核实|賽果將進一步核實|核实完毕后会进行结算|核實完畢後會進行結算/.test(text)) score -= 500;
  if (!reasonMatched && marketScore <= 0 && !/无效|無效|invalid|取消|拒单|拒單|失败|失敗|退回|本金|赔率|賠率|盘口|盤口/.test(text)) score -= 80;
  return score;
}

function detailMarketCategory(detail = {}) {
  const text = normalizeText([
    detail.playName,
    detail.originalPlay,
    detail.playOptionName,
    detail.playOptions,
    detail.marketValue
  ].filter(Boolean).join(' '));
  if (/罚牌|黄牌|红牌|booking|bookings|card|cards/.test(text)) {
    return {
      label: '罚牌',
      include: ['罚牌', 'booking', 'bookings', 'card', 'cards'],
      exclude: ['角球', 'corner']
    };
  }
  if (/角球|corner/.test(text)) {
    return {
      label: '角球',
      include: ['角球', 'corner'],
      exclude: ['罚牌', 'booking', 'bookings', 'card', 'cards']
    };
  }
  if (/进球|入球|goal|goals/.test(text)) {
    return {
      label: '进球',
      include: ['进球', '入球', 'goal', 'goals'],
      exclude: []
    };
  }
  return null;
}

function addMarketTerm(terms, value) {
  const raw = htmlText(value);
  if (!raw) return;
  const parts = [raw, ...raw.split(/[，,、;；|/()（）【】\[\]\s]+/)].map((item) => normalizeText(item));
  for (const part of parts) {
    // 保留完整时间范围（含数字），用于区分同一赛事不同盘口（如 45:00-59:59 vs 60:00-74:59）
    const timeRangeMatch = part.match(/\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}/);
    if (timeRangeMatch) {
      terms.add(timeRangeMatch[0].replace(/\s+/g, ''));
    }

    let term = part.replace(/[0-9.]+/g, '');
    if (!term || /^(大|小|单双|单|双|over|under|yes|no)$/.test(term)) continue;
    if (term.length >= 2) terms.add(term);

    let core = term
      .replace(/^(全场|上半场|下半场|半场|fulltime|ft)/, '')
      .replace(/(大小|总数|总分|单双)$/, '');
    if (core.length >= 2) terms.add(core);

    if (/罚牌|黄牌|红牌/.test(term)) {
      ['罚牌', 'booking', 'bookings', 'card', 'cards'].forEach((item) => terms.add(item));
    }
    if (/角球/.test(term)) {
      ['角球', 'corner'].forEach((item) => terms.add(item));
    }
    if (/进球|入球/.test(term)) {
      ['进球', '入球', 'goal', 'goals'].forEach((item) => terms.add(item));
    }
  }
}

function detailMarketTerms(detail = {}) {
  const terms = new Set();
  [
    detail.playName,
    detail.originalPlay,
    detail.marketName,
    detail.playTypeName,
    detail.betItemName,
    detail.playOptionName,
    detail.playOptions,
    detail.marketValue,
    detail.optionValue
  ].forEach((item) => addMarketTerm(terms, item));
  return [...terms];
}

function noticeMarketSegmentText(item = {}) {
  const raw = htmlText(noticeText(item));
  const segments = [...raw.matchAll(/[【\[]([^】\]]+)[】\]]/g)].map((match) => match[1]).filter(Boolean);
  return segments.join(' ');
}

function scoreNoticeMarketTerms(item = {}, detail = {}) {
  const terms = detailMarketTerms(detail);
  if (!terms.length) return 0;
  const fullText = normalizeText(noticeText(item));
  const segment = normalizeText(noticeMarketSegmentText(item));
  const segmentTerms = new Set();
  if (segment) addMarketTerm(segmentTerms, segment);
  let score = 0;
  let matched = false;
  for (const term of terms) {
    if (term.length < 2) continue;
    if (fullText.includes(term)) {
      score += 20;
      matched = true;
    }
    if (segment && segment.includes(term)) {
      score += 45;
      matched = true;
    }
    for (const noticeTerm of segmentTerms) {
      if (noticeTerm.length >= 2 && (term.includes(noticeTerm) || noticeTerm.includes(term))) {
        score += 35;
        matched = true;
      }
    }
  }
  if (segmentTerms.size && !matched) score -= 25;
  return score;
}

function scoreSettlementNotice(item = {}, order = {}, detail = {}) {
  const text = normalizeText(noticeText(item));
  const home = normalizeText(detail.homeName);
  const away = normalizeText(detail.awayName);
  const matchName = normalizeText(detail.matchName || detail.tournamentName || order.matchName || '');
  const beginText = String(detail.beginTimeStr || order.beginTimeStr || '').replace(/-/g, '/').slice(0, 16);
  const begin = normalizeText(beginText);
  const category = detailMarketCategory(detail);
  let score = 0;
  if (home && text.includes(home)) score += 20;
  if (away && text.includes(away)) score += 20;
  if (matchName && text.includes(matchName)) score += 12;
  if (begin && text.includes(begin)) score += 12;
  if (/赛果不明确|不能按时结算|delaysettlement|delay settlement|noclearresult|no clear result/.test(text)) score += 10;
  score += scoreNoticeMarketTerms(item, detail);
  if (category) {
    if (category.include.some((token) => text.includes(normalizeText(token)))) score += 100;
    if (category.exclude.some((token) => text.includes(normalizeText(token)))) score -= 120;
    if (!category.include.some((token) => text.includes(normalizeText(token))) && !category.exclude.some((token) => text.includes(normalizeText(token)))) {
      score += 3;
    }
  }
  return score;
}

function bestChineseNoticeContext(detailData = {}) {
  const list = Array.isArray(detailData.list) ? detailData.list : [];
  const zh = list.find((item) => Number(item.langType) === 1 && item.context)
    || list.find((item) => item.context);
  return htmlText((zh && zh.context) || detailData.context || '');
}

async function replyOrigin(config, cmd, statusMessage, replyText, ticketText = '') {
  await setStatus({ state: 'success', message: statusMessage, detail: String(ticketText || '').slice(0, 300) });
  await ack(config, cmd, 'reply_origin', statusMessage, { reply_text: replyText, stop_actions: true });
}

function settlementTemplate(template, context = {}) {
  const text = String(template || '{order_no}    注单催结算    US\n赛事ID：{match_id}');
  return text.replace(/\{([a-zA-Z0-9_]+)\}/g, (_all, key) => String(context[key] ?? ''));
}

function cleanMatchIdList(matchIds = []) {
  const seen = new Set();
  const result = [];
  for (const item of matchIds || []) {
    const matchId = String(item || '').trim();
    if (!matchId || seen.has(matchId)) continue;
    seen.add(matchId);
    result.push(matchId);
  }
  return result;
}

async function loadMerchantUrgeMatchStats() {
  const stored = await chrome.storage.local.get([MERCHANT_URGE_MATCH_STATS_KEY]);
  const raw = stored[MERCHANT_URGE_MATCH_STATS_KEY] || {};
  const now = Date.now();
  const stats = {};
  for (const [matchId, item] of Object.entries(raw)) {
    const count = Number((item || {}).count || 0);
    const updatedAt = Number((item || {}).updatedAt || 0);
    if (!matchId || !count || !updatedAt || now - updatedAt > MERCHANT_URGE_MATCH_TTL_MS) continue;
    stats[matchId] = { count, updatedAt };
  }
  return stats;
}

async function splitTelegramUrgeMatchIds(matchIds = []) {
  const cleanIds = cleanMatchIdList(matchIds);
  const stats = await loadMerchantUrgeMatchStats();
  const allowed = [];
  const blocked = [];
  for (const matchId of cleanIds) {
    const count = Number((stats[matchId] || {}).count || 0);
    if (count >= MERCHANT_URGE_MATCH_LIMIT) blocked.push(matchId);
    else allowed.push(matchId);
  }
  return { allowed, blocked };
}

async function recordTelegramUrgeMatchIds(matchIds = []) {
  const cleanIds = cleanMatchIdList(matchIds);
  if (!cleanIds.length) return;
  const stats = await loadMerchantUrgeMatchStats();
  const now = Date.now();
  for (const matchId of cleanIds) {
    const current = Number((stats[matchId] || {}).count || 0);
    stats[matchId] = { count: current + 1, updatedAt: now };
  }
  await chrome.storage.local.set({ [MERCHANT_URGE_MATCH_STATS_KEY]: stats });
}

async function sendTelegramMessageFromCommand(config, cmd, text, options = {}) {
  const target = String(cmd.telegram_target || cmd.forward_to || '').trim();
  const targetLabel = String(options.targetLabel || 'TG目标群');
  if (!target) throw new Error(`未配置${targetLabel}`);
  const action = String(options.action || cmd.action || 'urge_settlement').trim() || 'urge_settlement';
  const res = await fetch(`${config.botBase}/api/cmd/send_telegram?secret=${encodeURIComponent(config.cmdSecret)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      id: cmd.id,
      action,
      rule: cmd.rule || '',
      chat_id: cmd.chat_id || cmd.source_chat_id || '',
      message_id: cmd.message_id || cmd.source_message_id || '',
      source_text: cmd.source_text || '',
      orderNo: cmd.orderNo || cmd.order_no || cmd.target_value || '',
      target_value: cmd.target_value || cmd.orderNo || cmd.order_no || '',
      target,
      account: String(cmd.telegram_account || ''),
      text
    })
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || !data.ok) {
    throw new Error(data.error || `TG发送失败 HTTP ${res.status}`);
  }
  return data;
}

async function sendTelegramFromCommand(config, cmd, text) {
  return sendTelegramMessageFromCommand(config, cmd, text, {
    action: 'urge_settlement',
    targetLabel: '催结算TG群'
  });
}

function commandArrayValue(cmd = {}, ...keys) {
  const ai = commandAiParse(cmd);
  for (const key of keys) {
    const value = cmd[key] ?? ai[key];
    if (Array.isArray(value)) return value.map((item) => String(item || '').trim()).filter(Boolean);
    if (typeof value === 'string' && value.trim()) {
      return value.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean);
    }
  }
  return [];
}

function commandNumberValue(cmd = {}, ...keys) {
  const ai = commandAiParse(cmd);
  for (const key of keys) {
    const value = cmd[key] ?? ai[key];
    const number = Number(value);
    if (Number.isFinite(number) && number > 0) return number;
  }
  return 0;
}

function commandStringValue(cmd = {}, ...keys) {
  const ai = commandAiParse(cmd);
  for (const key of keys) {
    const value = cmd[key] ?? ai[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value).trim();
  }
  return '';
}

function merchantUrgeBatchKey(cmd = {}, matchIds = []) {
  const matchKey = cleanMatchIdList(matchIds).join(',');
  const sourceKey = commandStringValue(cmd, 'urge_batch_id')
    || [
      cmd.chat_id || cmd.source_chat_id || '',
      cmd.message_id || cmd.source_message_id || '',
      commandArrayValue(cmd, 'order_nos', 'orderNos').join(',')
    ].join(':');
  return [
    sourceKey,
    matchKey,
    String(cmd.telegram_target || cmd.forward_to || '').trim(),
    String(cmd.telegram_account || ''),
    String(cmd.telegram_template || '')
  ].join('|');
}

function combineUrgeContexts(contexts = []) {
  const cleanContexts = contexts.filter(Boolean);
  const base = { ...(cleanContexts[0] || {}) };
  const unique = (values) => [...new Set(values.map((item) => String(item || '').trim()).filter(Boolean))];
  const orderNos = unique(cleanContexts.map((item) => item.order_no || item.orderNo || item.order_id || item.orderId));
  const matchIds = unique(cleanContexts.flatMap((item) => String(item.match_id || item.matchId || '').split(/[，,\s]+/)));
  const matchManageIds = unique(cleanContexts.flatMap((item) => String(item.match_manage_id || item.matchManageId || '').split(/[，,\s]+/)));
  const matchInfos = unique(cleanContexts.map((item) => item.match_info || item.matchInfo));
  const users = unique(cleanContexts.map((item) => item.user_name || item.userName));
  const orderText = orderNos.join('、');
  return {
    ...base,
    order_no: orderText,
    order_id: orderText,
    orderNo: orderText,
    orderId: orderText,
    order_nos: orderNos.join('\n'),
    orderNos: orderNos.join('\n'),
    match_id: matchIds.join('，'),
    matchId: matchIds.join('，'),
    match_manage_id: matchManageIds.join('，'),
    matchManageId: matchManageIds.join('，'),
    match_info: matchInfos.join('；'),
    matchInfo: matchInfos.join('；'),
    user_name: users.join('，'),
    userName: users.join('，')
  };
}

async function sendTelegramFromCommandBatched(config, cmd, context, matchIds = []) {
  const cleanIds = cleanMatchIdList(matchIds);
  const key = merchantUrgeBatchKey(cmd, cleanIds);
  const expected = commandNumberValue(cmd, 'urge_batch_total', 'urgeBatchTotal');
  let batch = merchantUrgeTelegramBatches.get(key);
  if (!batch) {
    batch = {
      expected,
      contexts: new Map(),
      promise: null
    };
    merchantUrgeTelegramBatches.set(key, batch);
  }
  batch.expected = Math.max(Number(batch.expected || 0), expected);
  const orderNo = String(context.order_no || context.orderNo || context.order_id || context.orderId || '').trim();
  if (orderNo) batch.contexts.set(orderNo, context);
  if (!batch.promise) {
    batch.promise = (async () => {
      const deadline = Date.now() + MERCHANT_URGE_BATCH_WAIT_MS;
      while (batch.expected > 1 && batch.contexts.size < batch.expected && Date.now() < deadline) {
        await sleep(MERCHANT_URGE_BATCH_POLL_MS);
      }
      const contexts = [...batch.contexts.values()];
      const combinedContext = combineUrgeContexts(contexts);
      const text = settlementTemplate(cmd.telegram_template, combinedContext);
      await sendTelegramFromCommand(config, cmd, text);
      await recordTelegramUrgeMatchIds(cleanIds);
      return {
        text,
        orderNos: String(combinedContext.order_no || '').split('、').filter(Boolean)
      };
    })().finally(() => {
      merchantUrgeTelegramBatches.delete(key);
    });
  }
  return batch.promise;
}

function apiOk(res, data) {
  return res.ok && (data.status_code === undefined || Number(data.status_code) === 6000);
}

function actionOk(res, data, text, action) {
  if (apiOk(res, data)) return true;
  const raw = `${(data && data.message) || ''} ${text || ''}`.replace(/\s+/g, '');
  if (action === 'add_proxy_whitelist' && res.ok && /IP[:：]?[0-9.]+已经存在|已经存在|已存在/.test(raw)) {
    return true;
  }
  return false;
}

function actionSuccessReplyText(action, targetValue) {
  if (action === 'add_proxy_whitelist') return '已处理';
  return '';
}

function normalizeVenueControlSites(rawSites) {
  const values = Array.isArray(rawSites) ? rawSites : String(rawSites || '').split(/[\s,，;；]+/);
  const sites = [];
  for (const raw of values) {
    let site = String(raw || '').trim().toLowerCase();
    if (['9', '9站', 'ml', '9001'].includes(site)) site = '9001';
    else if (['6', '6站', 'jn', '6001'].includes(site)) site = '6001';
    else continue;
    if (!sites.includes(site)) sites.push(site);
  }
  return sites.length ? sites : ['9001', '6001'];
}

function venueUpdateUrl(config) {
  if (config.venueUpdateUrl) return config.venueUpdateUrl;
  if (!config.venueQueryUrl) throw new Error('场馆查询接口未配置');
  return String(config.venueQueryUrl).replace(/\/queryByName(?:\?.*)?$/, '/update');
}

function findVenueByCommand(list = [], targetValue = '') {
  const target = String(targetValue || '').trim().toLowerCase();
  return list.find((item) => {
    const names = [item.zhName, item.enName, item.channelName, item.name, item.id].map((value) => String(value || '').trim().toLowerCase());
    return names.some((name) => name && (name === target || name.includes(target) || target.includes(name)));
  }) || null;
}

async function runVenueDisplayControlCommand(baseConfig, cmd, targetValue) {
  const mode = String(cmd.venue_mode || cmd.mode || '').trim().toLowerCase() === 'enable' ? 'enable' : 'maintenance';
  const sites = normalizeVenueControlSites(cmd.sites || cmd.backend_sites || cmd.site);
  const results = [];
  const errors = [];

  for (const site of sites) {
    try {
      const config = configForAction(baseConfig, 'venue_display_control', { ...cmd, backend_site: site });
      await setStatus({ state: 'running', message: `${profileForSite(site).label}${mode === 'enable' ? '启用' : '维护'} ${targetValue}` });
      const query = await postJson(config.venueQueryUrl, config.headers, { category: String(cmd.category || '4') });
      if (!apiOk(query.res, query.data)) {
        throw new Error(`查询场馆失败 HTTP ${query.res.status}: ${query.text.slice(0, 300)}`);
      }
      const list = Array.isArray(query.data?.data) ? query.data.data : (((query.data || {}).data || {}).list || []);
      const venue = findVenueByCommand(list, targetValue) || (Number(cmd.venue_id || cmd.id) ? { id: Number(cmd.venue_id || cmd.id), zhName: targetValue } : null);
      if (!venue || !venue.id) throw new Error(`未找到场馆：${targetValue}`);

      const body = mode === 'enable'
        ? {
            operate: 1,
            id: Number(venue.id),
            isDisplay: '0',
            reasonRemark: '',
            hint: ''
          }
        : {
            operate: 1,
            id: Number(venue.id),
            isDisplay: '1',
            isDisplayMaintain: 1,
            jumpVenueId: Number(cmd.jump_venue_id || cmd.jumpVenueId || 14),
            reasonRemark: String(cmd.reasonRemark || ''),
            hint: String(cmd.hint || ''),
            startAt: String(cmd.maintenance_start_at || cmd.startAt || ''),
            endAt: String(cmd.maintenance_end_at || cmd.endAt || ''),
            isAutoJump: 1,
            isUnknowEnd: 0
          };
      const saved = await postJson(venueUpdateUrl(config), config.headers, body);
      if (!apiOk(saved.res, saved.data)) {
        throw new Error(`更新场馆失败 HTTP ${saved.res.status}: ${saved.text.slice(0, 300)}`);
      }
      results.push(`${profileForSite(site).label}${mode === 'enable' ? '已启用' : '已维护'}`);
    } catch (err) {
      errors.push(`${profileForSite(site).label}: ${err && err.message ? err.message : String(err || '')}`);
    }
  }

  if (errors.length) throw new Error(errors.join('；'));
  const replyText = `${targetValue}${mode === 'enable' ? '启用完成' : '维护设置完成'}：${results.join('、')}`;
  await setStatus({ state: 'success', message: replyText, detail: replyText });
  await ack(baseConfig, cmd, 'success', replyText, { reply_text: replyText });
}

async function findExactMember(config, targetValue) {
  if (!config.memberListUrl) throw new Error('会员查询接口未配置');
  await setStatus({ state: 'running', message: `查询会员 ${targetValue}` });
  const query = await postJson(config.memberListUrl, config.headers, {
    name: targetValue,
    pageNum: 1,
    pageSize: 20
  });
  if (!apiOk(query.res, query.data)) {
    throw new Error(`查询会员失败 HTTP ${query.res.status}: ${query.text.slice(0, 300)}`);
  }
  const list = (((query.data || {}).data || {}).list || []);
  const member = list.find((item) => String(item.name || '').toLowerCase() === targetValue);
  if (!member) {
    throw new Error(`未找到会员：${targetValue}`);
  }
  member.exactId = exactMemberIdFromListText(query.text, targetValue) || String(member.id || '');
  return member;
}

async function queryOneMemberDataOverview(config, cmd, target, fields, range, multiple) {
  const targetValue = String((target && target.name) || '').trim().toLowerCase();
  if (!targetValue) throw new Error('会员账号为空');
  const member = await findExactMember(config, targetValue);
  const memberId = member.exactId || member.id;
  if (!memberId) throw new Error(`会员缺少ID：${targetValue}`);
  const expectedAgentCode = expectedAgentCodeFromText(cmd, targetValue, target.agentCode);
  if (expectedAgentCode) {
    await setStatus({ state: 'running', message: `核实上级代理编号 ${targetValue}` });
    const actualAgentCode = await decryptedTopInviteCode(config, member);
    if (String(actualAgentCode || '') !== String(expectedAgentCode)) {
      const reason = '代理编码不正确，请核实';
      return {
        ok: false,
        memberName: targetValue,
        reason,
        replyText: multiple ? `${targetValue}：${reason}` : reason
      };
    }
  }

  const bodyText = memberDataOverviewBodyText(memberId, range.startAt, range.endAt);
  let gameTotal = {};
  let financeTotal = {};

  if (fields.some((field) => field.source === 'game')) {
    await setStatus({ state: 'running', message: `查询会员数据概览 ${targetValue}` });
    const game = await postJsonText(config.memberGameTotalInfoUrl, config.headers, bodyText);
    if (!apiOk(game.res, game.data)) {
      throw new Error(`查询会员数据概览失败 HTTP ${game.res.status}: ${game.text.slice(0, 300)}`);
    }
    gameTotal = (((game.data || {}).data || {}).totalStat || {});
  }

  if (fields.some((field) => field.source === 'finance')) {
    await setStatus({ state: 'running', message: `查询会员输赢流水 ${targetValue}` });
    const finance = await postJsonText(config.memberFinanceTotalAmountUrl, config.headers, bodyText);
    if (!apiOk(finance.res, finance.data)) {
      throw new Error(`查询会员输赢流水失败 HTTP ${finance.res.status}: ${finance.text.slice(0, 300)}`);
    }
    financeTotal = ((finance.data || {}).data || {});
  }

  const values = {
    net: financeTotal.sumNetAmount,
    validBet: financeTotal.sumValidBetAmount,
    deposit: gameTotal.totalPayAmount,
    withdraw: gameTotal.totalWithdrawAmount,
    bonus: gameTotal.sumDividendMoney,
    rebate: gameTotal.sumFsMoney
  };
  const memberName = String(member.name || targetValue);
  return {
    ok: true,
    memberName,
    replyText: [
      memberName,
      ...fields.map((field) => `${field.label}：${formatMoney(values[field.key])}`)
    ].join('\n')
  };
}

async function runMemberDataOverviewCommand(config, cmd, targetValue) {
  if (!config.memberGameTotalInfoUrl) throw new Error('会员游戏数据概览接口未配置');
  if (!config.memberFinanceTotalAmountUrl) throw new Error('会员流水输赢接口未配置');
  const fields = requestedDataOverviewFields(cmd);
  if (!fields.length) throw new Error('未指定查数据字段');
  const range = memberDataOverviewDateRange(cmd);
  const targets = dataOverviewTargetsFromText(cmd, targetValue);
  if (!targets.length) throw new Error('未提取到会员账号');
  const multiple = targets.length > 1;
  const blocks = [];
  let successCount = 0;

  for (const target of targets) {
    try {
      const result = await queryOneMemberDataOverview(config, cmd, target, fields, range, multiple);
      if (result.ok) successCount += 1;
      blocks.push(result.replyText);
    } catch (err) {
      if (!multiple) throw err;
      const reason = friendlyErrorReason(err, 'member_data_overview', '查数据');
      blocks.push(`${target.name}：${reason}`);
    }
  }

  const replyText = blocks.join('\n\n');
  const msg = multiple
    ? `查数据完成：${successCount}/${targets.length} ${range.startAt}~${range.endAt}`
    : (successCount > 0
      ? `查数据成功：${targets[0].name} ${fields.map((field) => field.label).join('、')} ${range.startAt}~${range.endAt}`
      : `查数据跳过：${targets[0].name}`);
  await setStatus({ state: successCount > 0 ? 'success' : 'error', message: msg, detail: replyText });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
}

function requireSixSiteAuth(config) {
  const site = String((config.headers && config.headers['x-api-site']) || '');
  const href = String((config.pageAuth && config.pageAuth.href) || '');
  if (site !== '6001' && !SITE_6_HOSTS.some((host) => href.includes(host))) {
    throw new Error('未登录');
  }
}

async function runMigrateMilanCommand(config, cmd, targetValue) {
  requireSixSiteAuth(config);
  await setStatus({ state: 'running', message: `查询迁移记录 ${targetValue}` });

  const query = await postJson(config.migrationRecordsUrl, config.headers, {
    name: targetValue,
    pageNum: 1,
    pageSize: 20
  });
  if (!apiOk(query.res, query.data)) {
    throw new Error(`查询迁移记录失败 HTTP ${query.res.status}: ${query.text.slice(0, 300)}`);
  }

  const list = (((query.data || {}).data || {}).list || []);
  const member = list.find((item) => String(item.name || '').toLowerCase() === targetValue) || list[0];
  if (!member || !member.id || !member.name) {
    throw new Error(`未找到迁移会员：${targetValue}`);
  }
  if (isSelfMilanUpgradeComplete(member, targetValue)) {
    const replyText = '会员已自助升级完成。';
    await setStatus({ state: 'success', message: replyText, detail: `${member.name} 已自助从6站迁移到米兰` });
    await ack(config, cmd, 'reply_origin', replyText, { reply_text: replyText, stop_actions: true });
    return;
  }

  await setStatus({ state: 'running', message: `执行迁移米兰 ${member.name}` });
  const migration = await postJson(config.migrateMilanUrl, config.headers, {
    members: [{ id: member.id, name: member.name }],
    siteIdTo: 9001
  });
  const result = ((migration.data || {}).data || {});
  const ok = apiOk(migration.res, migration.data) && Number(result.passCount || 0) > 0;
  const reason = result.firstRefuseReason || migration.data.message || migration.text.slice(0, 300);
  const detail = ok
    ? `迁移米兰成功：${member.name} pass=${result.passCount || 1}/${result.total || 1}`
    : `迁移米兰失败：${member.name} ${reason}`;

  await setStatus({
    state: ok ? 'success' : 'error',
    message: detail,
    detail: migration.text.slice(0, 300)
  });
  await ack(config, cmd, ok ? 'success' : 'failed', detail);
}

function isSelfMilanUpgradeComplete(record = {}, targetValue = '') {
  const memberName = String(record.name || targetValue || '').trim().toLowerCase();
  const targetName = String(targetValue || record.name || '').trim().toLowerCase();
  const operator = String(record.operatorNext || '').trim().toLowerCase();
  const fromSite = String(record.siteIdFromNext ?? '').trim();
  const toSite = String(record.siteIdToNext ?? '').trim();
  const state = Number(record.migrationStateNext);
  const result = String(record.migrationResultNext || '').trim();
  return !!(
    memberName
    && operator
    && (operator === memberName || operator === targetName)
    && fromSite === '6001'
    && toSite === '9001'
    && state === 2
    && result.includes('迁移成功')
  );
}

async function runSiteInnerMessageCommand(config, cmd) {
  if (!config.siteInnerMsgAddUrl) throw new Error('站内信接口未配置');
  const members = Array.isArray(cmd.members)
    ? cmd.members.map((item) => String(item || '').trim().toLowerCase()).filter(Boolean)
    : String(cmd.target_value || cmd.member_name || '').split(/[,，\s]+/).map((item) => item.trim().toLowerCase()).filter(Boolean);
  const uniqueMembers = [...new Set(members)];
  if (!uniqueMembers.length) throw new Error('站内信账号列表为空');

  const steps = Array.isArray(cmd.site_message_steps) && cmd.site_message_steps.length
    ? cmd.site_message_steps
    : [cmd];
  const strategyName = String(cmd.site_message_strategy_name || (steps.length > 1 ? '站内信策略' : '站内信'));
  const sentTitles = [];
  let totalSuccess = 0;
  await reportProgress(config, cmd, {
    status: 'running',
    message: `${strategyName}开始发送`,
    step: 0,
    total: steps.length,
    success: 0,
    percent: 0
  });

  for (let index = 0; index < steps.length; index += 1) {
    const step = { ...cmd, ...(steps[index] || {}) };
    const result = await sendOneSiteInnerMessage(config, step, uniqueMembers, index + 1, steps.length);
    sentTitles.push(result.title);
    totalSuccess += result.success;
    if (!result.ok) {
      await reportProgress(config, cmd, {
        status: 'failed',
        message: result.detail,
        title: result.title,
        step: index + 1,
        total: steps.length,
        success: totalSuccess,
        percent: Math.floor(((index + 1) / steps.length) * 100)
      });
      await ack(config, cmd, 'failed', result.detail);
      return;
    }
    await reportProgress(config, cmd, {
      status: 'running',
      message: `${strategyName}已发送 ${index + 1}/${steps.length}`,
      title: result.title,
      step: index + 1,
      total: steps.length,
      success: totalSuccess,
      percent: Math.floor(((index + 1) / steps.length) * 100)
    });
    if (index < steps.length - 1) {
      const waitSeconds = randomDelaySeconds(cmd.step_delay_min, cmd.step_delay_max);
      if (waitSeconds > 0) {
        await setStatus({ state: 'running', message: `${strategyName}等待 ${waitSeconds.toFixed(1)}秒` });
        await reportProgress(config, cmd, {
          status: 'waiting',
          message: `等待 ${waitSeconds.toFixed(1)}秒后发送下一条`,
          title: result.title,
          step: index + 1,
          total: steps.length,
          success: totalSuccess,
          percent: Math.floor(((index + 1) / steps.length) * 100)
        });
        await sleep(waitSeconds * 1000);
      }
    }
  }

  const detail = steps.length > 1
    ? `${strategyName}发送成功：${steps.length}/${steps.length}条，提交${uniqueMembers.length}人，成功${totalSuccess}次`
    : `站内信发送成功：${sentTitles[0] || ''}，提交${uniqueMembers.length}人，成功${totalSuccess}人`;
  await setStatus({ state: 'success', message: detail });
  await reportProgress(config, cmd, {
    status: 'success',
    message: detail,
    step: steps.length,
    total: steps.length,
    success: totalSuccess,
    percent: 100
  });
  await ack(config, cmd, 'success', detail);
}

async function sendOneSiteInnerMessage(config, cmd, uniqueMembers, stepIndex, totalSteps) {
  const template = await loadSiteInnerMessageTemplate(config, cmd);
  const title = template.title;
  const content = template.content;
  const moduleId = template.id;
  const msgType = Number(cmd.msg_type || cmd.msgType || 1);
  const iconUrl = String(cmd.icon_url || cmd.iconUrl || '17');
  const clients = String(cmd.clients || config.siteInnerMsgClients || '0,1,2,3,8,9');
  await setStatus({
    state: 'running',
    message: totalSteps > 1 ? `发送站内信 ${stepIndex}/${totalSteps}` : `发送站内信 ${uniqueMembers.length}人`
  });
  const sent = await postJson(config.siteInnerMsgAddUrl, config.headers, {
    clients,
    sendType: 1,
    module: moduleId,
    sendMembers: uniqueMembers.join(','),
    title,
    msgType,
    content,
    sort: 0,
    iconUrl,
    pcPath: String(cmd.pc_path || cmd.pcPath || template.pcPath || ''),
    h5Path: String(cmd.h5_path || cmd.h5Path || template.h5Path || ''),
    imgTop: 0,
    jumpUrlType: 1,
    pushFlag: 0,
    devices: '0,1',
    pcUrl: String(cmd.pc_url || cmd.pcUrl || template.pcUrl || ''),
    h5Url: String(cmd.h5_url || cmd.h5Url || template.h5Url || ''),
    sysStatus: '0'
  });
  const result = ((sent.data || {}).data || {});
  const success = Number(result.success || 0);
  const fail = Number(result.fail || 0);
  const invalid = Number(result.invalid || 0);
  const ok = apiOk(sent.res, sent.data) && success > 0 && fail === 0 && invalid === 0;
  const invalidMembers = Array.isArray(result.invalidMembers) ? result.invalidMembers.join(',') : (result.invalidMembers || '');
  const detail = ok
    ? `站内信发送成功：${title}，提交${uniqueMembers.length}人，成功${success}人`
    : `站内信发送失败：${title}，提交${uniqueMembers.length}人，成功${success}，失败${fail}，无效${invalid}${invalidMembers ? `，无效账号:${invalidMembers}` : ''}`;
  await setStatus({
    state: ok ? 'success' : 'error',
    message: detail,
    detail: sent.text.slice(0, 300)
  });
  return { ok, detail, title, success };
}

async function loadSiteInnerMessageTemplate(config, cmd) {
  const fallback = {
    id: Number(cmd.template_id || 243),
    title: String(cmd.title || '【存款温馨提示】'),
    content: String(cmd.content || '系统检测到您的存款订单已取消，为了让您的存款更加通畅，请您使用银联支付的方式存款，联系私人专属经理，申请更高彩金活动加赠！ 👉如无私人专属经理，截图此条消息，联系在线客服发送：“申请专属经理”，享更多优惠～'),
    pcUrl: '',
    h5Url: '',
    pcPath: '',
    h5Path: ''
  };
  if (!config.siteInnerMsgTemplateUrl) return fallback;

  await setStatus({ state: 'running', message: '读取站内信模板' });
  const query = await postJson(config.siteInnerMsgTemplateUrl, config.headers, {
    ifCommon: '1',
    module: '1',
    pageNum: 1,
    pageSize: 100
  });
  if (!apiOk(query.res, query.data)) {
    throw new Error(`读取站内信模板失败 HTTP ${query.res.status}: ${query.text.slice(0, 300)}`);
  }
  const list = (((query.data || {}).data || {}).list || []);
  const templateId = Number(cmd.template_id || 243);
  const matched = list.find((item) => Number(item.id) === templateId)
    || list.find((item) => String(item.title || '').trim() === fallback.title);
  if (!matched || !matched.title || !matched.content) {
    throw new Error(`未找到站内信模板：${templateId}`);
  }
  return {
    id: Number(matched.id),
    title: String(matched.title),
    content: String(matched.content),
    pcUrl: String(matched.webLink || matched.pcUrl || ''),
    h5Url: String(matched.h5Link || matched.h5Url || ''),
    pcPath: String(matched.webPic || matched.pcPath || ''),
    h5Path: String(matched.h5Pic || matched.h5Path || '')
  };
}

async function runMerchantOrderStatisticsCommand(config, cmd, targetValue) {
  if (!config.merchantStatisticsUrl) throw new Error('场馆注单查询接口未配置');
  const { startTime, endTime } = merchantDateRange(cmd);
  const stamp = Date.now();
  const url = `${config.merchantStatisticsUrl}?rnd_str_st=${stamp}`;
  const body = {
    filter: String(cmd.filter || '1'),
    orderNo: targetValue,
    databaseSwitch: Number(cmd.databaseSwitch ?? cmd.database_switch ?? 1),
    userIdList: Array.isArray(cmd.userIdList) ? cmd.userIdList : [],
    startTime,
    endTime,
    pageNum: Number(cmd.pageNum || cmd.page_num || 1),
    fromAppointment: Number(cmd.fromAppointment ?? cmd.from_appointment ?? 0),
    pageSize: Number(cmd.pageSize || cmd.page_size || 20),
    accountTag: Number(cmd.accountTag ?? cmd.account_tag ?? 0)
  };

  await setStatus({ state: 'running', message: `查询场馆注单 ${targetValue}` });
  const query = await postJson(url, merchantHeaders(config, cmd), body);
  const ok = query.res.ok;
  const detail = ok
    ? `场馆注单查询成功：${targetValue}`
    : `场馆注单查询失败：${targetValue} HTTP ${query.res.status}`;
  await setStatus({
    state: ok ? 'success' : 'error',
    message: detail,
    detail: query.text.slice(0, 300)
  });
  await ack(config, cmd, ok ? 'success' : `http_${query.res.status}`, query.text.slice(0, 500));
}

async function queryMerchantTicketOrder(config, cmd, orderNo, statusPrefix = '查询注单') {
  if (!config.merchantTicketListUrl) throw new Error('场馆注单列表接口未配置');
  const headers = merchantHeaders(config, cmd);
  const venueLabel = config.pageAuthLabel || merchantAuthLabel(config);
  await setStatus({ state: 'running', message: `${statusPrefix} ${orderNo} (${venueLabel})` });
  const ticketUrl = merchantUrl(config.merchantTicketListUrl);
  const ticket = await postJson(ticketUrl, headers, merchantTicketBody(cmd, orderNo));
  if (!merchantApiOk(ticket)) {
    const err = merchantHttpError('查询注单失败', ticketUrl, ticket);
    if (merchantAuthFailed(ticket)) {
      err.code = 'merchant_auth_failed';
      err.venueLabel = venueLabel;
    }
    throw err;
  }
  const order = merchantList(ticket.data)[0];
  if (!order) {
    const err = new Error(`未找到注单：${orderNo}`);
    err.code = 'merchant_order_not_found';
    err.venueLabel = venueLabel;
    throw err;
  }
  return { headers, ticket, order, venueLabel };
}

async function runTicketCancelReasonCommand(config, cmd, orderNo) {
  const { headers, ticket, order } = await queryMerchantTicketOrder(config, cmd, orderNo, '查询注单取消/失败原因');
  const detail = ticketReasonDetail(order, {}, ticketReasonMode(cmd));
  const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail);
  await replyOrigin(config, cmd, msg, replyText, ticket.text);
}

async function queryTicketReasonNotice(config, headers, order = {}, detail = {}) {
  if (!config.merchantNoticeUrl) throw new Error('场馆公告接口未配置');
  const matchId = String(detail.matchId || order.standardMatchId || detail.standardMatchId || '').trim();
  if (!matchId) return null;
  const noticeUrl = merchantUrl(config.merchantNoticeUrl);
  const notice = await postForm(noticeUrl, headers, {
    mid: matchId,
    status: 1,
    pgNum: 1,
    pgSize: 20
  });
  if (!merchantApiOk(notice)) {
    throw merchantHttpError('查询失效公告失败', noticeUrl, notice);
  }
  const notices = merchantList(notice.data)
    .map((item) => ({ item, score: scoreInvalidNotice(item, order, detail) }))
    .sort((a, b) => b.score - a.score);
  const selected = notices[0];
  if (!selected || selected.score < 80) {
    return null;
  }
  return selected.item;
}

async function ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail) {
  if (orderIsBetFailed(order, detail) || orderIsCanceled(order, detail)) {
    const selected = await queryTicketReasonNotice(config, headers, order, detail);
    if (selected) {
      const context = await noticeReplyText(config, headers, selected);
      if (context) {
        return {
          replyText: context,
          msg: `注单取消/失败原因已回复公告：${orderNo}`
        };
      }
    }
  }
  return ticketReasonReplyForOrder(order, detail, orderNo, cmd);
}

async function replyInvalidTicketNotice(config, cmd, headers, orderNo, order, detail, ticketText) {
  const selected = await queryTicketReasonNotice(config, headers, order, detail);
  if (!selected) throw new Error(`未匹配到注单失效公告：${orderNo}`);
  const context = await noticeReplyText(config, headers, selected);
  if (!context) throw new Error(`公告无可回复中文内容：${orderNo}`);
  await replyOrigin(config, cmd, `注单失效已回复公告：${orderNo}`, context, ticketText);
}

async function noticeReplyText(config, headers, noticeItem = {}) {
  const noticeId = noticeItem.noticeId || noticeItem.id;
  if (config.merchantNoticeDetailUrl && noticeId) {
    try {
      const noticeDetail = await postForm(merchantUrl(config.merchantNoticeDetailUrl), headers, { id: noticeId });
      if (merchantApiOk(noticeDetail)) {
        const context = bestChineseNoticeContext(noticeDetail.data.data || {});
        if (context) return context;
      }
    } catch {
      // fall back to list content
    }
  }
  return htmlText([
    noticeItem.title || noticeItem.zhTitle || noticeItem.enTitle || '',
    noticeItem.context || noticeItem.zhContext || noticeItem.enContext || ''
  ].filter(Boolean).join('\n'));
}

async function runUrgeSettlementCommand(config, cmd, orderNo) {
  if (!config.merchantTicketListUrl) throw new Error('场馆注单列表接口未配置');
  if (!config.merchantNoticeUrl) throw new Error('场馆公告接口未配置');
  if (!config.merchantSettlementListUrl) throw new Error('场馆结算状态接口未配置');
  if (!config.merchantSettlementApplyUrl) throw new Error('场馆催促结算申请接口未配置');

  const headers = merchantHeaders(config, cmd);
  const venueLabel = config.pageAuthLabel || merchantAuthLabel(config);
  await setStatus({ state: 'running', message: `催结算查询注单 ${orderNo} (${venueLabel})` });
  const ticketUrl = merchantUrl(config.merchantTicketListUrl);
  const ticket = await postJson(ticketUrl, headers, merchantTicketBody(cmd, orderNo));
  if (!merchantApiOk(ticket)) {
    const err = merchantHttpError('查询注单失败', ticketUrl, ticket);
    if (merchantAuthFailed(ticket)) {
      err.code = 'merchant_auth_failed';
      err.venueLabel = venueLabel;
    }
    throw err;
  }
  const order = merchantList(ticket.data)[0];
  if (!order) {
    const err = new Error(`未找到注单：${orderNo}`);
    err.code = 'merchant_order_not_found';
    err.venueLabel = venueLabel;
    throw err;
  }

  const allDetails = orderDetails(order);
  const pendingDetails = unresolvedOrderDetails(order);
  const detail = pendingDetails[0] || firstOrderDetail(order);
  const rollbackOrder = orderHasSettlementRollback(order);
  const statusLabel = orderStatusLabel(order.orderStatus);
  if (ticketReasonRequested(cmd)) {
    const reasonDetail = ticketReasonDetail(order, detail, ticketReasonMode(cmd));
    const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, reasonDetail);
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }
  if (orderIsBetFailed(order, detail)) {
    const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail);
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }
  if (orderIsCanceled(order, detail)) {
    const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail);
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }
  if (!rollbackOrder && Number(order.orderStatus) !== 0) {
    const replyText = String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。');
    const msg = `催结算跳过：${orderNo} ${statusLabel}`;
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }

  if (!rollbackOrder && allDetails.length && !pendingDetails.length) {
    const replyText = String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。');
    const msg = `催结算跳过：${orderNo} 串关明细均已结算`;
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }

  const detailsToCheck = pendingDetails.length ? pendingDetails : [detail].filter(Boolean);
  const futureDetail = detailsToCheck.find((item) => {
    const beginMs = orderBeginTimeMillis(order, item);
    return beginMs && Date.now() < beginMs;
  });
  if (futureDetail) {
    const replyText = String(cmd.not_started_reply || '当前注单暂未开赛，请耐心等待。');
    const beginText = String(futureDetail.beginTimeStr || futureDetail.beginTime || order.beginTimeStr || order.beginTime || '');
    const msg = `催结算跳过：${orderNo} 未到开赛时间${beginText ? ` ${beginText}` : ''}`;
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }

  const matchIds = detailsMatchIds(order, detailsToCheck);
  if (!matchIds.length) {
    throw new Error(`注单未找到未结算赛事ID：${orderNo}`);
  }

  for (const item of detailsToCheck) {
    const matchId = detailMatchId(order, item);
    if (!matchId) continue;
    await setStatus({ state: 'running', message: `查询赛事公告 ${matchId}` });
    const noticeUrl = merchantUrl(config.merchantNoticeUrl);
    const notice = await postForm(noticeUrl, headers, {
      mid: matchId,
      status: 1,
      pgNum: 1,
      pgSize: 20
    });
    if (!merchantApiOk(notice)) {
      throw merchantHttpError('查询公告失败', noticeUrl, notice);
    }
    const notices = merchantList(notice.data)
      .map((noticeItem) => ({ item: noticeItem, score: scoreSettlementNotice(noticeItem, order, item) }))
      .sort((a, b) => b.score - a.score);
    if (notices.length) {
      const selected = notices[0] || {};
      const selectedNotice = selected.item || {};
      const noticeText = await noticeReplyText(config, headers, selectedNotice);
      const marketLabel = detailMarketCategory(item)?.label || '';
      const replyText = withSettlementRollbackPrefix(noticeText || '赛果核实中，请耐心等待。', order, item);
      await replyOrigin(config, cmd, `赛事 ${matchId} 已有公告${marketLabel ? `（${marketLabel}）` : ''}`, replyText, notice.text);
      return;
    }
  }

  await setStatus({ state: 'running', message: `查询结算状态 ${orderNo}` });
  const settlementUrl = merchantUrl(config.merchantSettlementListUrl);
  const settlement = await postJson(settlementUrl, headers, merchantSettlementBody(cmd, orderNo));
  if (!merchantApiOk(settlement)) {
    if (merchantNoSettlementData(settlement)) {
      console.info('[CS Bot ZD Unlock] no settlement data, continue TG urge', orderNo, settlement.data && settlement.data.msg);
    } else {
      throw merchantHttpError('查询结算状态失败', settlementUrl, settlement);
    }
  }
  const settlementTotal = Number((((settlement.data || {}).data || {}).total) || 0);
  if (settlementTotal > 0 || merchantList(settlement.data).length > 0) {
    const settlementOrder = merchantList(settlement.data)[0] || {};
    const settlementDetails = unresolvedOrderDetails(settlementOrder).length
      ? unresolvedOrderDetails(settlementOrder)
      : detailsToCheck;
    const applyItems = settlementDetails.length ? settlementDetails : detailsToCheck;
    const seenApply = new Set();
    let applied = 0;
    for (const item of applyItems) {
      const body = merchantSettlementApplyBody(settlementOrder.orderNo ? settlementOrder : order, item, orderNo);
      const key = `${body.orderNo}:${body.matchId}:${body.betNo}`;
      if (!body.orderNo || !body.matchId || !body.betNo || !body.userId || seenApply.has(key)) continue;
      seenApply.add(key);
      await setStatus({ state: 'running', message: `递交催促结算申请 ${body.orderNo} ${body.matchId}` });
      const applyUrl = merchantUrl(config.merchantSettlementApplyUrl);
      const apply = await postJson(applyUrl, headers, body);
      if (!merchantApiOk(apply)) {
        throw merchantHttpError('递交催促结算申请失败', applyUrl, apply);
      }
      applied += 1;
    }
    if (!applied) {
      throw new Error(`催促结算申请缺少必要字段：${orderNo}`);
    }
    const msg = `催结算已提交申请：${orderNo}`;
    const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, applyItems[0] || detail);
    await replyOrigin(config, cmd, msg, replyText, settlement.text);
    return;
  }

  const matchId = matchIds.join('，');
  // TG urge throttling only applies after the notice and settlement-apply checks above.
  // If a notice appears later, the command returns the notice before reaching this block.
  const urgeMatchSplit = await splitTelegramUrgeMatchIds(matchIds);
  if (!urgeMatchSplit.allowed.length) {
    const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, detailsToCheck[0] || detail);
    const msg = `催结算跳过TG重复无公告赛事：${orderNo} 赛事ID ${matchId}`;
    await replyOrigin(config, cmd, msg, replyText, settlement.text);
    return;
  }
  const limitedMatchId = urgeMatchSplit.allowed.join('，');
  const matchManageId = [...new Set(detailsToCheck.map((item) => String(item.matchManageId || '').trim()).filter(Boolean))].join('，');
  const matchInfo = detailsText(order, detailsToCheck);
  const context = {
    order_no: orderNo,
    order_id: orderNo,
    orderNo,
    orderId: orderNo,
    match_id: limitedMatchId,
    matchId: limitedMatchId,
    match_manage_id: matchManageId,
    matchManageId,
    sport: detail.sportName || '',
    sport_name: detail.sportName || '',
    match_info: matchInfo,
    matchInfo,
    begin_time: detail.beginTimeStr || '',
    beginTime: detail.beginTimeStr || '',
    user_name: order.userName || '',
    userName: order.userName || ''
  };
  const batchResult = await sendTelegramFromCommandBatched(config, cmd, context, urgeMatchSplit.allowed);
  const text = batchResult.text || settlementTemplate(cmd.telegram_template, context);
  const batchOrderNos = Array.isArray(batchResult.orderNos) && batchResult.orderNos.length ? batchResult.orderNos : [orderNo];
  const msgTarget = batchOrderNos.length > 1 ? batchOrderNos.join('、') : orderNo;
  const msg = urgeMatchSplit.blocked.length
    ? `催结算已提交：${msgTarget} 赛事ID ${limitedMatchId}（跳过重复：${urgeMatchSplit.blocked.join('，')}）`
    : `催结算已提交：${msgTarget} 赛事ID ${limitedMatchId}`;
  const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, detailsToCheck[0] || detail);
  await setStatus({ state: 'success', message: msg, detail: text.slice(0, 300) });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
}

async function runUrgeSettlementCommandWithFallback(configs, cmd, orderNo) {
  const tried = [];
  const authFailed = [];
  for (const candidate of configs) {
    try {
      await runUrgeSettlementCommand(candidate, cmd, orderNo);
      return;
    } catch (err) {
      const label = err.venueLabel || candidate.pageAuthLabel || merchantAuthLabel(candidate);
      if (err && err.code === 'merchant_order_not_found') {
        tried.push(label);
        continue;
      }
      if (err && err.code === 'merchant_auth_failed') {
        authFailed.push(label);
        continue;
      }
      throw err;
    }
  }
  const parts = [];
  if (tried.length) parts.push(`未找到：${[...new Set(tried)].join('、')}`);
  if (authFailed.length) parts.push(`登录失效：${[...new Set(authFailed)].join('、')}`);
  const suffix = parts.length ? `（${parts.join('；')}）` : '';
  if (!tried.length && authFailed.length) {
    if (cmd.__merchantReloginRetried) {
      throw new Error(`所有场馆账号登录失效，未能查询注单：${orderNo}${suffix}`);
    }
    const refreshed = await requestMerchantRelogin(configs[0] || {}, authFailed);
    if (refreshed) {
      const nextConfig = (await getConfig()).config;
      const nextConfigs = authConfigsForAction(nextConfig, 'urge_settlement', { ...cmd, __merchantReloginRetried: true });
      return runUrgeSettlementCommandWithFallback(nextConfigs, { ...cmd, __merchantReloginRetried: true }, orderNo);
    }
    throw new Error(`所有场馆账号登录失效，未能查询注单：${orderNo}${suffix}`);
  }
  throw new Error(`所有场馆账号均未找到注单：${orderNo}${suffix}`);
}

async function runTicketCancelReasonCommandWithFallback(configs, cmd, orderNo) {
  const tried = [];
  const authFailed = [];
  for (const candidate of configs) {
    try {
      await runTicketCancelReasonCommand(candidate, cmd, orderNo);
      return;
    } catch (err) {
      const label = err.venueLabel || candidate.pageAuthLabel || merchantAuthLabel(candidate);
      if (err && err.code === 'merchant_order_not_found') {
        tried.push(label);
        continue;
      }
      if (err && err.code === 'merchant_auth_failed') {
        authFailed.push(label);
        continue;
      }
      throw err;
    }
  }
  const parts = [];
  if (tried.length) parts.push(`未找到：${[...new Set(tried)].join('、')}`);
  if (authFailed.length) parts.push(`登录失效：${[...new Set(authFailed)].join('、')}`);
  const suffix = parts.length ? `（${parts.join('；')}）` : '';
  if (!tried.length && authFailed.length) {
    if (cmd.__merchantReloginRetried) {
      throw new Error(`所有场馆账号登录失效，未能查询注单：${orderNo}${suffix}`);
    }
    const refreshed = await requestMerchantRelogin(configs[0] || {}, authFailed);
    if (refreshed) {
      const nextConfig = (await getConfig()).config;
      const nextConfigs = authConfigsForAction(nextConfig, 'query_ticket_cancel_reason', { ...cmd, __merchantReloginRetried: true });
      return runTicketCancelReasonCommandWithFallback(nextConfigs, { ...cmd, __merchantReloginRetried: true }, orderNo);
    }
    throw new Error(`所有场馆账号登录失效，未能查询注单：${orderNo}${suffix}`);
  }
  throw new Error(`所有场馆账号均未找到注单：${orderNo}${suffix}`);
}

async function runBackendCommand(config, cmd) {
  const action = normalizeCommandAction(cmd.action, cmd);
  let rawValue = action === 'merchant_order_statistics' || action === 'urge_settlement' || action === 'query_ticket_cancel_reason'
    ? (cmd.orderNo || cmd.order_no || cmd.target_value || cmd.member_name || '')
    : (cmd.target_value || cmd.member_name || '');
  const label = commandLabel(action);
  if (!String(rawValue || '').trim() && ['member_data_overview', 'query_member_line', 'query_login_device_ip', 'query_same_ip_device', 'disable_login_device', 'query_venue_turnover'].includes(action)) {
    rawValue = commandMembers(cmd, '')[0] || '';
  }
  const targetValue = action === 'add_proxy_whitelist' || action === 'merchant_order_statistics' || action === 'urge_settlement' || action === 'query_ticket_cancel_reason'
    ? String(rawValue).trim()
    : String(rawValue).trim().toLowerCase();
  if (!targetValue) {
    const detail = `${label}未提取到目标`;
    await setStatus({ state: 'error', message: detail, detail });
    await ack(config, cmd, 'no_target', detail);
    return;
  }
  await setStatus({ state: 'running', message: `执行${label} ${targetValue}` });
  try {
    if (action === 'urge_settlement') {
      const configs = authConfigsForAction(config, action, cmd);
      await runUrgeSettlementCommandWithFallback(configs, cmd, targetValue);
      return;
    }
    if (action === 'query_ticket_cancel_reason') {
      const configs = authConfigsForAction(config, action, cmd);
      await runTicketCancelReasonCommandWithFallback(configs, cmd, targetValue);
      return;
    }
    if (action === 'venue_display_control') {
      await runVenueDisplayControlCommand(config, cmd, targetValue);
      return;
    }
    config = configForAction(config, action, cmd);
    if (action === 'migrate_milan') {
      await runMigrateMilanCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'send_site_inner_msg') {
      await runSiteInnerMessageCommand(config, cmd);
      return;
    }
    if (action === 'member_data_overview') {
      await runMemberDataOverviewCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'query_member_line') {
      await runQueryMemberLineCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'query_login_device_ip') {
      await runQueryLoginDeviceIpCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'query_same_ip_device') {
      await runQuerySameIpDeviceCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'disable_login_device') {
      await runDisableLoginDeviceCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'query_venue_turnover') {
      await runQueryVenueTurnoverCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'configure_rebate') {
      await runConfigureRebateCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'merchant_order_statistics') {
      await runMerchantOrderStatisticsCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'unlock_sms' || action === 'clear_login_error') {
      await findExactMember(config, targetValue);
    }
    const request = commandRequest(config, action, targetValue, cmd);
    const res = await fetchWithRetry(request.url, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers: config.headers,
      body: JSON.stringify(request.body)
    });
    const text = await res.text();
    const data = parseJsonText(text);
    const ok = actionOk(res, data, text, action);
    const reason = (data && data.message) || text;
    const replyText = ok ? actionSuccessReplyText(action, targetValue) : '';
    await setStatus({
      state: ok ? 'success' : 'error',
      message: `${label} ${targetValue} HTTP ${res.status}`,
      detail: text.slice(0, 300)
    });
    await ack(
      config,
      cmd,
      ok ? 'success' : `http_${res.status}`,
      reason,
      replyText ? { reply_text: replyText, stop_actions: action === 'add_proxy_whitelist' } : {}
    );
  } catch (err) {
    const reason = friendlyErrorReason(err, action, label);
    const raw = rawErrorText(err).replace(/\s+/g, ' ').slice(0, 240);
    const detail = raw && !raw.includes(reason)
      ? `${label}失败 ${targetValue}: ${reason}；${raw}`
      : `${label}失败 ${targetValue}: ${reason}`;
    await setStatus({ state: 'error', message: `${label}失败 ${targetValue}`, detail: detail.slice(0, 500) });
    await ack(config, cmd, 'fetch_failed', detail);
  }
}

async function pollOnce() {
  if (polling) return;
  if (activeBackendCommands >= MAX_ACTIVE_BACKEND_COMMANDS) {
    await setStatus({
      state: 'busy',
      message: `后台处理中 ${activeBackendCommands}/${MAX_ACTIVE_BACKEND_COMMANDS}`,
      detail: '达到并发上限，稍后继续轮询。'
    });
    return;
  }
  polling = true;
  let shouldPollAgain = false;
  try {
    const remaining = await authSyncRemainingMs();
    if (remaining) {
      await setStatus({
        state: 'auth_refresh',
        message: '后台登录态同步中，暂停轮询',
        detail: `约 ${Math.ceil(remaining / 1000)} 秒后恢复。`
      });
      return;
    }
    const { config } = await getConfig();
    await setStatus({ state: 'polling', message: '正在轮询命令' });
    const waitSeconds = activeBackendCommands > 0 ? 0 : COMMAND_POLL_WAIT_SECONDS;
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=${waitSeconds}&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const rawText = await res.text();
    let data = null;
    try {
      data = rawText ? JSON.parse(rawText) : {};
    } catch (_err) {
      const snippet = rawText.slice(0, 120).replace(/\s+/g, ' ');
      throw new Error(`命令轮询返回非JSON：HTTP ${res.status} ${res.statusText || ''} ${snippet}`);
    }
    if (!res.ok) {
      throw new Error(`命令轮询失败：HTTP ${res.status} ${data?.msg || data?.error || rawText.slice(0, 120)}`);
    }
    if (data && data.ok && data.cmd && isSupportedCommandAction(data.cmd.action, data.cmd)) {
      await setStatus({ state: 'received', message: `收到命令 ${data.cmd.orderNo || data.cmd.order_no || data.cmd.target_value || data.cmd.member_name || ''}` });
      activeBackendCommands += 1;
      shouldPollAgain = true;
      (async () => {
        try {
          const waited = await waitForAuthSyncReady();
          const nextConfig = waited ? (await getConfig()).config : config;
          await runBackendCommand(nextConfig, data.cmd);
        } finally {
          activeBackendCommands = Math.max(0, activeBackendCommands - 1);
          setTimeout(() => pollOnce(), 200);
        }
      })();
    } else {
      await setStatus({ state: 'idle', message: '暂无命令' });
    }
  } catch (err) {
    await setStatus({ state: 'error', message: '轮询失败', detail: err.message });
  } finally {
    polling = false;
    if (shouldPollAgain) setTimeout(() => pollOnce(), 100);
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(['config', 'recorderState', 'recorderRecords']);
  await chrome.storage.local.set({
    config: normalizeConfig({
      ...DEFAULT_CONFIG,
      ...(stored.config || {}),
      headers: {
        ...DEFAULT_CONFIG.headers,
        ...((stored.config && stored.config.headers) || {})
      }
    }),
    enabled: true,
    recorderState: stored.recorderState || { enabled: false, startedAt: '', stoppedAt: '', count: 0 },
    recorderRecords: stored.recorderRecords || []
  });
  chrome.alarms.create('poll', { periodInMinutes: COMMAND_POLL_ALARM_MINUTES });
  pollOnce();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('poll', { periodInMinutes: COMMAND_POLL_ALARM_MINUTES });
  pollOnce();
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'poll') pollOnce();
});

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message && message.type === 'pollNow') {
    pollOnce().then(() => sendResponse({ ok: true })).catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'saveConfig') {
    chrome.storage.local.get(['config'])
      .then((stored) => {
        const current = stored.config || {};
        const incoming = message.config || {};
        const config = {
          ...DEFAULT_CONFIG,
          ...current,
          ...incoming,
          value: String(incoming.value || current.value || '').trim(),
          headers: {
            ...DEFAULT_CONFIG.headers,
            ...(current.headers || {}),
            ...(incoming.headers || {})
          }
        };
        return chrome.storage.local.set({ config: normalizeConfig(config), enabled: true });
      })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: COMMAND_POLL_ALARM_MINUTES });
        pollOnce();
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'setEnabled') {
    chrome.storage.local.set({ enabled: true })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: COMMAND_POLL_ALARM_MINUTES });
        pollOnce();
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'refreshBackstageTabs') {
    refreshBackstageTabs()
      .then((resp) => sendResponse(resp))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'pageAuth') {
    const host = authHost(message.auth || {});
    const headers = ((message.auth || {}).headers || {});
    const site = String(headers['x-api-site'] || (headers.authorization && headers['user-id'] ? 'merchant' : '') || '');
    const merchantKey = site === 'merchant' ? merchantAuthKey(message.auth || {}) : '';
    chrome.storage.local.get(['pageAuthByHost', 'pageAuthByMerchant', 'authSync'])
      .then((stored) => {
        const pageAuthByHost = stored.pageAuthByHost || {};
        const pageAuthByMerchant = stored.pageAuthByMerchant || {};
        const authSync = stored.authSync || {};
        if (host) pageAuthByHost[host] = message.auth;
        if (site) pageAuthByHost[site] = message.auth;
        if (merchantKey) pageAuthByMerchant[merchantKey] = message.auth;
        const nextAuthSync = authSync.active
          ? {
              ...authSync,
              authMessages: Number(authSync.authMessages || 0) + 1,
              lastAuthAt: new Date().toISOString()
            }
          : authSync;
        return chrome.storage.local.set({
          pageAuth: message.auth,
          pageAuthByHost,
          pageAuthByMerchant: limitedMerchantAuthStore(pageAuthByMerchant),
          authSync: nextAuthSync
        });
      })
      .then(() => setStatus({
        state: 'auth',
        message: '已登录',
        detail: ''
      }))
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'unlockValue') {
    const value = String(message.value || '').trim();
    if (!value) {
      sendResponse({ ok: false, error: 'empty value' });
      return true;
    }
    chrome.storage.local.get(['config'])
      .then((stored) => {
        const config = {
          ...(stored.config || DEFAULT_CONFIG),
          value,
          unlockValueSavedAt: new Date().toISOString()
        };
        return chrome.storage.local.set({ config: normalizeConfig(config) });
      })
      .then(() => setStatus({
        state: 'sms_value_saved',
        message: '短信参数已保存',
        detail: '可以测试自动短信解锁'
      }))
      .then(() => notify('CS Bot Unlock', '短信解锁参数已保存'))
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'merchantEndpoint') {
    const key = String(message.key || '');
    const url = String(message.url || '').trim();
    if (!isAllowedMerchantEndpoint(key, url)) {
      sendResponse({ ok: false, error: 'invalid merchant endpoint' });
      return true;
    }
    chrome.storage.local.get(['config'])
      .then((stored) => {
        const endpointBase = merchantEndpointBaseFromUrl(url);
        const config = {
          ...(stored.config || DEFAULT_CONFIG),
          [key]: url,
          ...(endpointBase ? { merchantEndpointBase: endpointBase } : {})
        };
        return chrome.storage.local.set({ config: normalizeConfig(applyMerchantEndpointBase(config)) });
      })
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'recorderStart') {
    startRecorder()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'recorderStop') {
    stopRecorder()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'recorderClear') {
    clearRecorder()
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'recorderRecord') {
    appendRecorderRecord(message.record || {})
      .then((result) => sendResponse(result))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  return false;
});

chrome.alarms.create('poll', { periodInMinutes: COMMAND_POLL_ALARM_MINUTES });
pollOnce();
