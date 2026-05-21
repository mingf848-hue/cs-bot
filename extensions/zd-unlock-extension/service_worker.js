const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  memberListUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/list',
  unlockUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
  loginErrorUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/clearLoginErrorRedisKey',
  proxyWhitelistUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/system/siteAccessManage/add',
  migrationRecordsUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/recordsV2',
  migrateMilanUrl: 'https://6sitebg.oj61i4.com/central/admin/site/admin/v1/pilgrimage/migration',
  value: 'x8Bffk8DR9QOcdHPe6fFvQ==',
  headers: {
    accept: '*/*',
    'accept-language': 'zh-CN,zh;q=0.9',
    'content-type': 'application/json',
    'use-new-api': 'true',
    'x-api-appkey': 'NDbTd5RysclL',
    'x-api-client': 'web',
    'x-api-site': '9001',
    'x-api-token': 'ZD_BHoMOAbPiZVNsqZXLxn3nuZK6ow8s02i',
    'x-api-user': 'aratakito',
    'x-api-uuid': '8510640B-F2AC-4B05-9ADD-52C740C363DB',
    'x-api-version': '0.1',
    'x-api-xsn': 'ef187eb236d1f9c0455561a473a0dafc',
    'x-api-xts': '1779105168'
  }
};

let polling = false;
const RECORDER_LIMIT = 400;
const RECORDER_BODY_LIMIT = 8000;
const RECORDER_RESPONSE_LIMIT = 20000;

function nowText() {
  return new Date().toLocaleString('zh-CN', { hour12: false });
}

async function getConfig() {
  const stored = await chrome.storage.local.get(['config', 'enabled', 'pageAuth']);
  const dynamicHeaders = (stored.pageAuth && stored.pageAuth.headers) || {};
  return {
    enabled: stored.enabled !== false,
    config: {
      ...DEFAULT_CONFIG,
      ...(stored.config || {}),
      headers: {
        ...DEFAULT_CONFIG.headers,
        ...((stored.config && stored.config.headers) || {}),
        ...dynamicHeaders
      },
      pageAuth: stored.pageAuth || null
    }
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

function commandLabel(action) {
  if (action === 'add_proxy_whitelist') return '代理IP加白';
  if (action === 'clear_login_error') return '登录限制解锁';
  if (action === 'migrate_milan') return '迁移米兰';
  return '短信/验证码解锁';
}

function commandRequest(config, action, targetValue) {
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
  return {
    url: config.unlockUrl,
    body: { value: config.value, name: targetValue }
  };
}

function parseJsonText(text) {
  try {
    return JSON.parse(text || '{}');
  } catch {
    return {};
  }
}

async function postJson(url, headers, body) {
  const res = await fetch(url, {
    method: 'POST',
    mode: 'cors',
    credentials: 'include',
    headers,
    body: JSON.stringify(body)
  });
  const text = await res.text();
  return { res, text, data: parseJsonText(text) };
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

async function runBackendCommand(config, cmd) {
  const action = ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan'].includes(cmd.action) ? cmd.action : 'unlock_sms';
  const rawValue = cmd.target_value || cmd.member_name || '';
  const targetValue = action === 'add_proxy_whitelist'
    ? String(rawValue).trim()
    : String(rawValue).trim().toLowerCase();
  if (!targetValue) return;
  const label = commandLabel(action);
  await setStatus({ state: 'running', message: `执行${label} ${targetValue}` });
  try {
    if (action === 'migrate_milan') {
      await runMigrateMilanCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'unlock_sms' || action === 'clear_login_error') {
      await findExactMember(config, targetValue);
    }
    const request = commandRequest(config, action, targetValue);
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
    const { enabled, config } = await getConfig();
    if (!enabled) {
      await setStatus({ state: 'paused', message: '扩展已暂停' });
      return;
    }
    await setStatus({ state: 'polling', message: '正在轮询命令' });
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=25&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const data = await res.json();
    if (data && data.ok && data.cmd && ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan'].includes(data.cmd.action)) {
      await setStatus({ state: 'received', message: `收到命令 ${data.cmd.target_value || data.cmd.member_name || ''}` });
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
  const stored = await chrome.storage.local.get(['recorderState', 'recorderRecords']);
  await chrome.storage.local.set({
    config: DEFAULT_CONFIG,
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
    chrome.storage.local.set({ config: message.config, enabled: message.enabled !== false })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: 0.5 });
        pollOnce();
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'setEnabled') {
    chrome.storage.local.set({ enabled: message.enabled !== false })
      .then(() => {
        chrome.alarms.create('poll', { periodInMinutes: 0.5 });
        if (message.enabled !== false) {
          pollOnce();
        } else {
          setStatus({ state: 'paused', message: '扩展已暂停' });
        }
        sendResponse({ ok: true });
      })
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  if (message && message.type === 'pageAuth') {
    chrome.storage.local.set({ pageAuth: message.auth })
      .then(() => setStatus({
        state: 'auth',
        message: '已登录',
        detail: ''
      }))
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
