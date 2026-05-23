const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  memberListUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/list',
  unlockUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
  loginErrorUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/clearLoginErrorRedisKey',
  proxyWhitelistUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/system/siteAccessManage/add',
  siteInnerMsgTemplateUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
  siteInnerMsgAddUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
  memberGameTotalInfoUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/game/totalInfo2',
  memberFinanceTotalAmountUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/finance/totalAmount2',
  migrationRecordsUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/recordsV2',
  migrateMilanUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/migration',
  merchantStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/getStatistics',
  merchantTicketListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/queryTicketList',
  merchantNoticeUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/notice',
  merchantNoticeDetailUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/noticeDetail',
  merchantSettlementListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/queryNoSettleTicketList',
  merchantSettlementStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/getStatistics',
  value: '',
  headers: {
    accept: '*/*',
    'accept-language': 'zh-CN,zh;q=0.9',
    'content-type': 'application/json',
    'use-new-api': 'true'
  }
};

let polling = false;
const RECORDER_LIMIT = 400;
const RECORDER_BODY_LIMIT = 8000;
const RECORDER_RESPONSE_LIMIT = 20000;
const AUTH_SYNC_WAIT_MS = 12000;
const FETCH_RETRY_DELAYS_MS = [800, 1800, 3200];
const SITE_PROFILES = {
  '9001': {
    host: '9sitebg.mvj4e7.com',
    authHosts: ['9sitebg.mvj4e7.com'],
    label: '9站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    memberListUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/list',
    siteInnerMsgTemplateUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
    siteInnerMsgAddUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
    siteInnerMsgClients: '0,1,2,3,8,9',
    memberGameTotalInfoUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/game/totalInfo2',
    memberFinanceTotalAmountUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/finance/totalAmount2'
  },
  '6001': {
    host: '6sitebg.oj61i4.com',
    authHosts: ['6sitebg.oj61i4.com'],
    label: '6站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    memberListUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/user/memberInfo/list',
    siteInnerMsgTemplateUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
    siteInnerMsgAddUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
    siteInnerMsgClients: '0,1,2,3,8',
    memberGameTotalInfoUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/user/game/totalInfo2',
    memberFinanceTotalAmountUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/user/finance/totalAmount2'
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
    merchantSettlementStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/getStatistics'
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
      iconUrl: chrome.runtime.getURL('icon.svg'),
      title,
      message
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
  if (action === 'migrate_milan' || hint === '6' || hint === '6001' || hint === '6zc') return '6001';
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
  const allowed = new Set([
    'merchantStatisticsUrl',
    'merchantTicketListUrl',
    'merchantNoticeUrl',
    'merchantNoticeDetailUrl',
    'merchantSettlementListUrl',
    'merchantSettlementStatisticsUrl'
  ]);
  if (!allowed.has(String(key || ''))) return false;
  try {
    const parsed = new URL(String(url || ''));
    return parsed.protocol === 'https:' && parsed.hostname.endsWith('dbsportxxxwo8.com');
  } catch {
    return false;
  }
}

async function getConfig() {
  const stored = await chrome.storage.local.get(['config', 'pageAuth', 'pageAuthByHost', 'pageAuthByMerchant']);
  return {
    enabled: true,
    config: {
      ...DEFAULT_CONFIG,
      ...(stored.config || {}),
      headers: {
        ...DEFAULT_CONFIG.headers,
        ...((stored.config && stored.config.headers) || {})
      },
      pageAuth: stored.pageAuth || null,
      pageAuthByHost: stored.pageAuthByHost || {},
      pageAuthByMerchant: stored.pageAuthByMerchant || {}
    }
  };
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
  return matchedAuths.map((matchedAuth) => ({
    ...config,
    ...profile,
    headers: {
      ...config.headers,
      ...(matchedAuth.headers || {})
    },
    pageAuth: matchedAuth,
    pageAuthLabel: targetSite === 'merchant' ? merchantAuthLabel(matchedAuth) : ''
  }));
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
  for (let attempt = 0; attempt <= retryDelays.length; attempt += 1) {
    try {
      return await fetch(url, options);
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

async function refreshBackstageTabs() {
  const tabs = await queryTabs({
    url: [
      'https://9sitebg.mvj4e7.com/*',
      'https://6sitebg.oj61i4.com/*',
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
  if (action === 'merchant_order_statistics') return '场馆注单查询';
  if (action === 'urge_settlement') return '催结算';
  return '短信/验证码解锁';
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
    data_overview: 'member_data_overview',
    query_member_data: 'member_data_overview',
    member_data_query: 'member_data_overview',
    '查数据': 'member_data_overview',
    '数据概览': 'member_data_overview',
    '催结算': 'urge_settlement'
  };
  const normalized = aliases[raw] || raw;
  if (hasMerchantCommandHint(cmd) && (raw === '' || raw === 'unlock_sms')) return 'merchant_order_statistics';
  return ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan', 'send_site_inner_msg', 'member_data_overview', 'merchant_order_statistics', 'urge_settlement'].includes(normalized)
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
    'merchant_order_statistics',
    'urge_settlement',
    'venue_order_statistics',
    'venue_order_query',
    'merchant_order_query',
    'query_venue_order',
    'query_merchant_order',
    'data_overview',
    'query_member_data',
    'member_data_query',
    '查数据',
    '数据概览',
    'settlement_urge',
    'urge_settle',
    'urge_settlement_order',
    '催结算'
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
  let res;
  try {
    res = await fetchWithRetry(url, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers,
      body: JSON.stringify(body)
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
      body: params.toString()
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

function merchantApiOk(result) {
  const data = (result && result.data) || {};
  return !!(result && result.res && result.res.ok && (data.code === undefined || data.code === '0000000') && data.status !== false);
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

function requestedDataOverviewFields(cmd = {}) {
  const rawText = [
    cmd.source_text,
    cmd.sourceText,
    cmd.original_text,
    cmd.originalText,
    cmd.message,
    cmd.text,
    cmd.data_fields,
    cmd.dataFields
  ].filter(Boolean).join(' ');
  const text = normalizeText(rawText);
  const selected = DATA_OVERVIEW_FIELDS.filter((field) => (
    field.aliases || []
  ).some((alias) => text.includes(normalizeText(alias))));
  return selected.length ? selected : DATA_OVERVIEW_FIELDS;
}

function detailIsSettled(detail = {}) {
  const settleTimes = numericValue(detail.settleTimes);
  if (settleTimes !== null) return settleTimes > 0;
  const betStatus = numericValue(detail.betStatus);
  const betResult = numericValue(detail.betResult);
  return betStatus === 1 && betResult !== 0;
}

function detailIsUnsettled(detail = {}) {
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
  return details.filter((detail) => detailIsUnsettled(detail));
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
  if (direct) return direct;
  const remark = String(detail.remark || order.remark || '').trim();
  const reasonMatch = remark.match(/原因[:：]\s*([^，,。\s]+)/);
  if (reasonMatch) return reasonMatch[1].trim();
  const eventMatch = remark.match(/([A-Za-z_]+|[\u4e00-\u9fa5]+)事件拒单/);
  if (eventMatch) return eventMatch[1].trim();
  return '盘口变动';
}

function ticketMatchText(detail = {}) {
  return String(detail.matchInfo || [detail.homeName, detail.awayName].filter(Boolean).join(' v ') || '').trim();
}

function betFailureReply(order = {}, detail = {}) {
  const matchText = ticketMatchText(detail) || '相关赛事';
  const riskText = failureRiskText(order, detail);
  return `您好，经核实，因用户下注确认期间其中赛事：${matchText} ${riskText} 导致投注失败，属于系统正常拒单，本金已退回，谢谢。`;
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
  const risk = normalizeText(failureRiskText(order, detail));
  const play = normalizeText(detail.playName || detail.originalPlay || '');
  const option = normalizeText(detail.playOptionName || detail.marketValue || '');
  let score = 0;
  if (risk && text.includes(risk)) score += 80;
  if (matchInfo && text.includes(matchInfo.replace('v', 'vs'))) score += 30;
  if (home && text.includes(home)) score += 15;
  if (away && text.includes(away)) score += 15;
  if (play && text.includes(play)) score += 10;
  if (option && text.includes(option)) score += 6;
  if (/无效|invalid|取消|退回|本金/.test(text)) score += 20;
  if (/不能按时结算|delaysettlement|赛果不明确/.test(text)) score -= 50;
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

async function sendTelegramFromCommand(config, cmd, text) {
  const target = String(cmd.telegram_target || cmd.forward_to || '').trim();
  if (!target) throw new Error('未配置催结算TG群');
  const res = await fetch(`${config.botBase}/api/cmd/send_telegram?secret=${encodeURIComponent(config.cmdSecret)}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      id: cmd.id,
      rule: cmd.rule || '',
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

function apiOk(res, data) {
  return res.ok && (data.status_code === undefined || Number(data.status_code) === 6000);
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
  return member;
}

async function runMemberDataOverviewCommand(config, cmd, targetValue) {
  if (!config.memberGameTotalInfoUrl) throw new Error('会员游戏数据概览接口未配置');
  if (!config.memberFinanceTotalAmountUrl) throw new Error('会员流水输赢接口未配置');
  const member = await findExactMember(config, targetValue);
  const memberId = member.id;
  if (!memberId) throw new Error(`会员缺少ID：${targetValue}`);

  const fields = requestedDataOverviewFields(cmd);
  const startAt = String(cmd.startAt || cmd.start_at || '2020-01-01');
  const endAt = String(cmd.endAt || cmd.end_at || formatDate(new Date()));
  const body = { memberId, startAt, endAt };
  let gameTotal = {};
  let financeTotal = {};

  if (fields.some((field) => field.source === 'game')) {
    await setStatus({ state: 'running', message: `查询会员数据概览 ${targetValue}` });
    const game = await postJson(config.memberGameTotalInfoUrl, config.headers, body);
    if (!apiOk(game.res, game.data)) {
      throw new Error(`查询会员数据概览失败 HTTP ${game.res.status}: ${game.text.slice(0, 300)}`);
    }
    gameTotal = (((game.data || {}).data || {}).totalStat || {});
  }

  if (fields.some((field) => field.source === 'finance')) {
    await setStatus({ state: 'running', message: `查询会员输赢流水 ${targetValue}` });
    const finance = await postJson(config.memberFinanceTotalAmountUrl, config.headers, body);
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
  const replyText = [
    memberName,
    ...fields.map((field) => `${field.label}：${formatMoney(values[field.key])}`)
  ].join('\n');
  const msg = `查数据成功：${memberName} ${fields.map((field) => field.label).join('、')}`;
  await setStatus({ state: 'success', message: msg, detail: replyText });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
}

function requireSixSiteAuth(config) {
  const site = String((config.headers && config.headers['x-api-site']) || '');
  const href = String((config.pageAuth && config.pageAuth.href) || '');
  if (site !== '6001' && !href.includes('6sitebg.oj61i4.com')) {
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

async function replyInvalidTicketNotice(config, cmd, headers, orderNo, order, detail, ticketText) {
  if (!config.merchantNoticeUrl) throw new Error('场馆公告接口未配置');
  const matchId = String(detail.matchId || order.standardMatchId || detail.standardMatchId || '').trim();
  if (!matchId) throw new Error(`注单失效未找到赛事ID：${orderNo}`);
  const notice = await postForm(merchantUrl(config.merchantNoticeUrl), headers, {
    mid: matchId,
    status: 1,
    pgNum: 1,
    pgSize: 20
  });
  if (!merchantApiOk(notice)) {
    throw new Error(`查询失效公告失败 HTTP ${notice.res.status}: ${notice.text.slice(0, 300)}`);
  }
  const notices = merchantList(notice.data)
    .map((item) => ({ item, score: scoreInvalidNotice(item, order, detail) }))
    .sort((a, b) => b.score - a.score);
  const selected = notices[0];
  if (!selected || selected.score <= 0) {
    throw new Error(`未匹配到注单失效公告：${orderNo}`);
  }
  const context = await noticeReplyText(config, headers, selected.item);
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

  const headers = merchantHeaders(config, cmd);
  const venueLabel = config.pageAuthLabel || merchantAuthLabel(config);
  await setStatus({ state: 'running', message: `催结算查询注单 ${orderNo} (${venueLabel})` });
  const ticket = await postJson(merchantUrl(config.merchantTicketListUrl), headers, merchantTicketBody(cmd, orderNo));
  if (!merchantApiOk(ticket)) {
    const err = new Error(`查询注单失败 HTTP ${ticket.res.status}: ${ticket.text.slice(0, 300)}`);
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
  const statusLabel = orderStatusLabel(order.orderStatus);
  if (Number(order.orderStatus) === 4 || Number(detail.betStatus) === 5) {
    const replyText = betFailureReply(order, detail);
    const msg = `投注失败退本金已回复：${orderNo}`;
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }
  if (Number(order.orderStatus) === 2 || Number(detail.betStatus) === 3) {
    await replyInvalidTicketNotice(config, cmd, headers, orderNo, order, detail, ticket.text);
    return;
  }
  if (Number(order.orderStatus) !== 0) {
    const replyText = String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。');
    const msg = `催结算跳过：${orderNo} ${statusLabel}`;
    await replyOrigin(config, cmd, msg, replyText, ticket.text);
    return;
  }

  if (allDetails.length && !pendingDetails.length) {
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
    const notice = await postForm(merchantUrl(config.merchantNoticeUrl), headers, {
      mid: matchId,
      status: 1,
      pgNum: 1,
      pgSize: 20
    });
    if (!merchantApiOk(notice)) {
      throw new Error(`查询公告失败 HTTP ${notice.res.status}: ${notice.text.slice(0, 300)}`);
    }
    const notices = merchantList(notice.data);
    if (notices.length) {
      const first = notices[0] || {};
      const noticeText = await noticeReplyText(config, headers, first);
      await replyOrigin(config, cmd, `赛事 ${matchId} 已有公告`, noticeText || '赛果核实中，请耐心等待。', notice.text);
      return;
    }
  }

  await setStatus({ state: 'running', message: `查询结算状态 ${orderNo}` });
  const settlement = await postJson(
    merchantUrl(config.merchantSettlementListUrl),
    headers,
    merchantSettlementBody(cmd, orderNo)
  );
  if (!merchantApiOk(settlement)) {
    throw new Error(`查询结算状态失败 HTTP ${settlement.res.status}: ${settlement.text.slice(0, 300)}`);
  }
  const settlementTotal = Number((((settlement.data || {}).data || {}).total) || 0);
  if (settlementTotal > 0 || merchantList(settlement.data).length > 0) {
    const settlementOrder = merchantList(settlement.data)[0] || {};
    const settlementDetails = unresolvedOrderDetails(settlementOrder).length
      ? unresolvedOrderDetails(settlementOrder)
      : detailsToCheck;
    const reason = settlementDetails.map(settlementReasonText).find(Boolean);
    const matchText = detailsText(settlementOrder.orderNo ? settlementOrder : order, settlementDetails) || detailsText(order, detailsToCheck);
    const replyText = reason
      ? `当前注单仍有未结算场次：${matchText}，原因：${reason}，请耐心等待。`
      : `当前注单仍有未结算场次：${matchText || matchIds.join('，')}，请耐心等待。`;
    const msg = `催结算跳过：${orderNo} 结算状态仍可查询到未结算记录`;
    await replyOrigin(config, cmd, msg, replyText, settlement.text);
    return;
  }

  const matchId = matchIds.join('，');
  const matchManageId = [...new Set(detailsToCheck.map((item) => String(item.matchManageId || '').trim()).filter(Boolean))].join('，');
  const matchInfo = detailsText(order, detailsToCheck);
  const context = {
    order_no: orderNo,
    orderNo,
    match_id: matchId,
    matchId,
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
  const text = settlementTemplate(cmd.telegram_template, context);
  await sendTelegramFromCommand(config, cmd, text);
  const msg = `已发送TG催结算：${orderNo} 赛事ID ${matchId}`;
  const replyText = String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。');
  await setStatus({ state: 'success', message: msg, detail: text.slice(0, 300) });
  await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText });
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
    throw new Error(`所有场馆账号登录失效，未能查询注单：${orderNo}${suffix}`);
  }
  throw new Error(`所有场馆账号均未找到注单：${orderNo}${suffix}`);
}

async function runBackendCommand(config, cmd) {
  const action = normalizeCommandAction(cmd.action, cmd);
  const rawValue = action === 'merchant_order_statistics' || action === 'urge_settlement'
    ? (cmd.orderNo || cmd.order_no || cmd.target_value || cmd.member_name || '')
    : (cmd.target_value || cmd.member_name || '');
  const targetValue = action === 'add_proxy_whitelist' || action === 'merchant_order_statistics' || action === 'urge_settlement'
    ? String(rawValue).trim()
    : String(rawValue).trim().toLowerCase();
  if (!targetValue) return;
  const label = commandLabel(action);
  await setStatus({ state: 'running', message: `执行${label} ${targetValue}` });
  try {
    if (action === 'urge_settlement') {
      const configs = authConfigsForAction(config, action, cmd);
      await runUrgeSettlementCommandWithFallback(configs, cmd, targetValue);
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
    const ok = apiOk(res, data);
    const reason = (data && data.message) || text;
    await setStatus({
      state: ok ? 'success' : 'error',
      message: `${label} ${targetValue} HTTP ${res.status}`,
      detail: text.slice(0, 300)
    });
    await ack(config, cmd, ok ? 'success' : `http_${res.status}`, reason);
  } catch (err) {
    const detail = `${label}失败 ${targetValue}: ${err && err.stack ? err.stack : (err && err.message ? err.message : String(err || 'unknown'))}`;
    await setStatus({ state: 'error', message: `${label}失败 ${targetValue}`, detail: detail.slice(0, 500) });
    await ack(config, cmd, 'fetch_failed', detail);
  }
}

async function pollOnce() {
  if (polling) return;
  polling = true;
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
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=25&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const data = await res.json();
    if (data && data.ok && data.cmd && isSupportedCommandAction(data.cmd.action, data.cmd)) {
      await setStatus({ state: 'received', message: `收到命令 ${data.cmd.orderNo || data.cmd.order_no || data.cmd.target_value || data.cmd.member_name || ''}` });
      const waited = await waitForAuthSyncReady();
      const nextConfig = waited ? (await getConfig()).config : config;
      await runBackendCommand(nextConfig, data.cmd);
    } else {
      await setStatus({ state: 'idle', message: '暂无命令' });
    }
  } catch (err) {
    await setStatus({ state: 'error', message: '轮询失败', detail: err.message });
  } finally {
    polling = false;
  }
}

chrome.runtime.onInstalled.addListener(async () => {
  const stored = await chrome.storage.local.get(['config', 'recorderState', 'recorderRecords']);
  await chrome.storage.local.set({
    config: {
      ...DEFAULT_CONFIG,
      ...(stored.config || {}),
      headers: {
        ...DEFAULT_CONFIG.headers,
        ...((stored.config && stored.config.headers) || {})
      }
    },
    enabled: true,
    recorderState: stored.recorderState || { enabled: false, startedAt: '', stoppedAt: '', count: 0 },
    recorderRecords: stored.recorderRecords || []
  });
  chrome.alarms.create('poll', { periodInMinutes: 0.5 });
  pollOnce();
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create('poll', { periodInMinutes: 0.5 });
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
        return chrome.storage.local.set({ config, enabled: true });
      })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: 0.5 });
        pollOnce();
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'setEnabled') {
    chrome.storage.local.set({ enabled: true })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: 0.5 });
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
        return chrome.storage.local.set({ config });
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
        const config = {
          ...(stored.config || DEFAULT_CONFIG),
          [key]: url
        };
        return chrome.storage.local.set({ config });
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

chrome.alarms.create('poll', { periodInMinutes: 0.5 });
pollOnce();
