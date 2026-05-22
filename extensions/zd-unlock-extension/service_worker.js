const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  memberListUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/list',
  unlockUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
  loginErrorUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/clearLoginErrorRedisKey',
  proxyWhitelistUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/system/siteAccessManage/add',
  siteInnerMsgTemplateUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
  siteInnerMsgAddUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
  migrationRecordsUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/recordsV2',
  migrateMilanUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/migration',
  merchantStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/getStatistics',
  merchantTicketListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/queryTicketList',
  merchantNoticeUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/notice',
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
const SITE_PROFILES = {
  '9001': {
    host: '9sitebg.mvj4e7.com',
    authHosts: ['9sitebg.mvj4e7.com'],
    label: '9站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    siteInnerMsgTemplateUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
    siteInnerMsgAddUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
    siteInnerMsgClients: '0,1,2,3,8,9'
  },
  '6001': {
    host: '6sitebg.oj61i4.com',
    authHosts: ['6sitebg.oj61i4.com'],
    label: '6站',
    requiredAuthHeaders: ['x-api-token', 'x-api-user'],
    siteInnerMsgTemplateUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/operation/cmCfg/template/info/list',
    siteInnerMsgAddUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/operation/cmCfg/siteInnerMsg/add',
    siteInnerMsgClients: '0,1,2,3,8'
  },
  'merchant': {
    host: 'api-merchant-backstage.dbsportxxxwo8.com',
    authHosts: ['merchant-own-backstage.dbsportxxxwo8.com', 'api-merchant-backstage.dbsportxxxwo8.com'],
    label: '场馆后台',
    requiredAuthHeaders: ['authorization', 'user-id'],
    merchantStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/getStatistics',
    merchantTicketListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/userReport/queryTicketList',
    merchantNoticeUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/noticeNew/notice',
    merchantSettlementListUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/queryNoSettleTicketList',
    merchantSettlementStatisticsUrl: 'https://api-merchant-backstage.dbsportxxxwo8.com/yewu17/admin/settlement/getStatistics'
  }
};

function nowText() {
  return new Date().toLocaleString('zh-CN', { hour12: false });
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

function isAllowedMerchantEndpoint(key, url) {
  const allowed = new Set([
    'merchantStatisticsUrl',
    'merchantTicketListUrl',
    'merchantNoticeUrl',
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
  const stored = await chrome.storage.local.get(['config', 'pageAuth', 'pageAuthByHost']);
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
      pageAuthByHost: stored.pageAuthByHost || {}
    }
  };
}

function configForAction(config, action, cmd = {}) {
  const targetSite = actionSite(action, cmd);
  const profile = profileForSite(targetSite);
  const targetHost = profile.host;
  const targetHosts = profile.authHosts || [targetHost];
  const requiredAuthHeaders = profile.requiredAuthHeaders || ['x-api-token', 'x-api-user'];
  const byHost = config.pageAuthByHost || {};
  const currentAuth = config.pageAuth || null;
  const candidates = [
    byHost[targetHost],
    ...targetHosts.map((host) => byHost[host]),
    byHost[targetSite],
    currentAuth,
    ...Object.values(byHost)
  ].filter(Boolean);
  const matchedAuth = candidates.find((auth) => {
    const headers = (auth && auth.headers) || {};
    return authMatches(auth, targetHost, targetSite, targetHosts)
      && requiredAuthHeaders.every((key) => headers[key]);
  });
  const authHeaders = (matchedAuth && matchedAuth.headers) || {};
  if (!matchedAuth || !requiredAuthHeaders.every((key) => authHeaders[key])) {
    throw new Error(`${profile.label}未登录`);
  }
  return {
    ...config,
    ...profile,
    headers: {
      ...config.headers,
      ...(matchedAuth.headers || {})
    },
    pageAuth: matchedAuth
  };
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

async function ack(config, cmd, status, detail = '') {
  if (!cmd || !cmd.id) return;
  try {
    await fetch(`${config.botBase}/api/cmd/ack?secret=${encodeURIComponent(config.cmdSecret)}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        id: cmd.id,
        status,
        member_name: cmd.member_name,
        detail: String(detail || '').slice(0, 500)
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
    '催结算': 'urge_settlement'
  };
  const normalized = aliases[raw] || raw;
  if (hasMerchantCommandHint(cmd) && (raw === '' || raw === 'unlock_sms')) return 'merchant_order_statistics';
  return ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan', 'send_site_inner_msg', 'merchant_order_statistics', 'urge_settlement'].includes(normalized)
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
    'merchant_order_statistics',
    'urge_settlement',
    'venue_order_statistics',
    'venue_order_query',
    'merchant_order_query',
    'query_venue_order',
    'query_merchant_order',
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
    res = await fetch(url, {
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
    res = await fetch(url, {
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

function firstOrderDetail(order = {}) {
  const details = Array.isArray(order.orderDetailList) ? order.orderDetailList : [];
  return details[0] || {};
}

function orderStatusLabel(status) {
  const value = Number(status);
  if (value === 0) return '未结算';
  if (value === 1) return '已结算';
  if (value === 2) return '已取消';
  return `状态${status}`;
}

function settlementTemplate(template, context = {}) {
  const text = String(template || '{order_no}注单催结算\n赛事ID：{match_id}');
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

async function runUrgeSettlementCommand(config, cmd, orderNo) {
  if (!config.merchantTicketListUrl) throw new Error('场馆注单列表接口未配置');
  if (!config.merchantNoticeUrl) throw new Error('场馆公告接口未配置');
  if (!config.merchantSettlementListUrl) throw new Error('场馆结算状态接口未配置');

  const headers = merchantHeaders(config, cmd);
  await setStatus({ state: 'running', message: `催结算查询注单 ${orderNo}` });
  const ticket = await postJson(merchantUrl(config.merchantTicketListUrl), headers, merchantTicketBody(cmd, orderNo));
  if (!merchantApiOk(ticket)) {
    throw new Error(`查询注单失败 HTTP ${ticket.res.status}: ${ticket.text.slice(0, 300)}`);
  }
  const order = merchantList(ticket.data)[0];
  if (!order) {
    throw new Error(`未找到注单：${orderNo}`);
  }

  const detail = firstOrderDetail(order);
  const matchId = String(detail.matchId || order.standardMatchId || detail.standardMatchId || '').trim();
  const matchManageId = String(detail.matchManageId || '').trim();
  const statusLabel = orderStatusLabel(order.orderStatus);
  if (Number(order.orderStatus) !== 0) {
    const msg = `催结算跳过：${orderNo} ${statusLabel}`;
    await setStatus({ state: 'success', message: msg, detail: ticket.text.slice(0, 300) });
    await ack(config, cmd, 'success', msg);
    return;
  }
  if (!matchId) {
    throw new Error(`注单未找到赛事ID：${orderNo}`);
  }

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
    const noticeText = [
      htmlText(first.title || first.zhTitle || first.enTitle || ''),
      htmlText(first.context || first.zhContext || first.enContext || '')
    ].filter(Boolean).join('\n');
    const msg = `催结算跳过：赛事 ${matchId} 已有公告${noticeText ? `\n${noticeText}` : ''}`;
    await setStatus({ state: 'success', message: `赛事 ${matchId} 已有公告`, detail: noticeText.slice(0, 300) });
    await ack(config, cmd, 'success', msg.slice(0, 500));
    return;
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
    const msg = `催结算跳过：${orderNo} 结算状态仍可查询到未结算记录`;
    await setStatus({ state: 'success', message: msg, detail: settlement.text.slice(0, 300) });
    await ack(config, cmd, 'success', msg);
    return;
  }

  const context = {
    order_no: orderNo,
    orderNo,
    match_id: matchId,
    matchId,
    match_manage_id: matchManageId,
    matchManageId,
    sport: detail.sportName || '',
    sport_name: detail.sportName || '',
    match_info: detail.matchInfo || '',
    matchInfo: detail.matchInfo || '',
    begin_time: detail.beginTimeStr || '',
    beginTime: detail.beginTimeStr || '',
    user_name: order.userName || '',
    userName: order.userName || ''
  };
  const text = settlementTemplate(cmd.telegram_template, context);
  await sendTelegramFromCommand(config, cmd, text);
  const msg = `已发送TG催结算：${orderNo} 赛事ID ${matchId}`;
  await setStatus({ state: 'success', message: msg, detail: text.slice(0, 300) });
  await ack(config, cmd, 'success', msg);
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
    config = configForAction(config, action, cmd);
    if (action === 'migrate_milan') {
      await runMigrateMilanCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'send_site_inner_msg') {
      await runSiteInnerMessageCommand(config, cmd);
      return;
    }
    if (action === 'merchant_order_statistics') {
      await runMerchantOrderStatisticsCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'urge_settlement') {
      await runUrgeSettlementCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'unlock_sms' || action === 'clear_login_error') {
      await findExactMember(config, targetValue);
    }
    const request = commandRequest(config, action, targetValue, cmd);
    const res = await fetch(request.url, {
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
    const { config } = await getConfig();
    await setStatus({ state: 'polling', message: '正在轮询命令' });
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=25&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const data = await res.json();
    if (data && data.ok && data.cmd && isSupportedCommandAction(data.cmd.action, data.cmd)) {
      await setStatus({ state: 'received', message: `收到命令 ${data.cmd.orderNo || data.cmd.order_no || data.cmd.target_value || data.cmd.member_name || ''}` });
      await runBackendCommand(config, data.cmd);
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
  if (message && message.type === 'pageAuth') {
    const host = authHost(message.auth || {});
    const headers = ((message.auth || {}).headers || {});
    const site = String(headers['x-api-site'] || (headers.authorization && headers['user-id'] ? 'merchant' : '') || '');
    chrome.storage.local.get(['pageAuthByHost'])
      .then((stored) => {
        const pageAuthByHost = stored.pageAuthByHost || {};
        if (host) pageAuthByHost[host] = message.auth;
        if (site) pageAuthByHost[site] = message.auth;
        return chrome.storage.local.set({ pageAuth: message.auth, pageAuthByHost });
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
          value
        };
        return chrome.storage.local.set({ config });
      })
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
