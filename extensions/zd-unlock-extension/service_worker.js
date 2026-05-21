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

function authHost(auth = {}) {
  try {
    return new URL(auth.href || '').host;
  } catch {
    return '';
  }
}

function actionHost(action) {
  return action === 'migrate_milan' ? '6sitebg.oj61i4.com' : '9sitebg.mvj4e7.com';
}

function actionSite(action) {
  return action === 'migrate_milan' ? '6001' : '9001';
}

async function getConfig() {
  const stored = await chrome.storage.local.get(['config', 'enabled', 'pageAuth', 'pageAuthByHost']);
  return {
    enabled: stored.enabled !== false,
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

function configForAction(config, action) {
  const targetHost = actionHost(action);
  const targetSite = actionSite(action);
  const byHost = config.pageAuthByHost || {};
  const currentAuth = config.pageAuth || null;
  const matchedAuth = byHost[targetHost]
    || (authHost(currentAuth) === targetHost ? currentAuth : null)
    || (String(((currentAuth || {}).headers || {})['x-api-site'] || '') === targetSite ? currentAuth : null);
  const authHeaders = (matchedAuth && matchedAuth.headers) || {};
  if (!matchedAuth || !authHeaders['x-api-token'] || !authHeaders['x-api-user']) {
    throw new Error(`${targetSite === '6001' ? '6站' : '9站'}未登录`);
  }
  return {
    ...config,
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

function commandLabel(action) {
  if (action === 'add_proxy_whitelist') return '代理IP加白';
  if (action === 'clear_login_error') return '登录限制解锁';
  if (action === 'migrate_milan') return '迁移米兰';
  if (action === 'send_site_inner_msg') return '发送站内信';
  return '短信/验证码解锁';
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

  for (let index = 0; index < steps.length; index += 1) {
    const step = { ...cmd, ...(steps[index] || {}) };
    const result = await sendOneSiteInnerMessage(config, step, uniqueMembers, index + 1, steps.length);
    sentTitles.push(result.title);
    totalSuccess += result.success;
    if (!result.ok) {
      await ack(config, cmd, 'failed', result.detail);
      return;
    }
    if (index < steps.length - 1) {
      const waitSeconds = randomDelaySeconds(cmd.step_delay_min, cmd.step_delay_max);
      if (waitSeconds > 0) {
        await setStatus({ state: 'running', message: `${strategyName}等待 ${waitSeconds.toFixed(1)}秒` });
        await sleep(waitSeconds * 1000);
      }
    }
  }

  const detail = steps.length > 1
    ? `${strategyName}发送成功：${steps.length}/${steps.length}条，提交${uniqueMembers.length}人，成功${totalSuccess}次`
    : `站内信发送成功：${sentTitles[0] || ''}，提交${uniqueMembers.length}人，成功${totalSuccess}人`;
  await setStatus({ state: 'success', message: detail });
  await ack(config, cmd, 'success', detail);
}

async function sendOneSiteInnerMessage(config, cmd, uniqueMembers, stepIndex, totalSteps) {
  const template = await loadSiteInnerMessageTemplate(config, cmd);
  const title = template.title;
  const content = template.content;
  const moduleId = template.id;
  const msgType = Number(cmd.msg_type || cmd.msgType || 1);
  const iconUrl = String(cmd.icon_url || cmd.iconUrl || '17');
  await setStatus({
    state: 'running',
    message: totalSteps > 1 ? `发送站内信 ${stepIndex}/${totalSteps}` : `发送站内信 ${uniqueMembers.length}人`
  });
  const sent = await postJson(config.siteInnerMsgAddUrl, config.headers, {
    clients: '0,1,2,3,8,9',
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

async function runBackendCommand(config, cmd) {
  const action = ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan', 'send_site_inner_msg'].includes(cmd.action) ? cmd.action : 'unlock_sms';
  const rawValue = cmd.target_value || cmd.member_name || '';
  const targetValue = action === 'add_proxy_whitelist'
    ? String(rawValue).trim()
    : String(rawValue).trim().toLowerCase();
  if (!targetValue) return;
  const label = commandLabel(action);
  await setStatus({ state: 'running', message: `执行${label} ${targetValue}` });
  try {
    config = configForAction(config, action);
    if (action === 'migrate_milan') {
      await runMigrateMilanCommand(config, cmd, targetValue);
      return;
    }
    if (action === 'send_site_inner_msg') {
      await runSiteInnerMessageCommand(config, cmd);
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
    if (data && data.ok && data.cmd && ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist', 'migrate_milan', 'send_site_inner_msg'].includes(data.cmd.action)) {
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
    const host = authHost(message.auth || {});
    chrome.storage.local.get(['pageAuthByHost'])
      .then((stored) => {
        const pageAuthByHost = stored.pageAuthByHost || {};
        if (host) pageAuthByHost[host] = message.auth;
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
