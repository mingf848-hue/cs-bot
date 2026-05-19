const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  unlockUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
  loginErrorUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/clearLoginErrorRedisKey',
  proxyWhitelistUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/system/siteAccessManage/add',
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

function headerSummary(headers = {}) {
  const keys = ['x-api-token', 'x-api-user', 'x-api-uuid', 'x-api-xsn', 'x-api-xts', 'x-api-appkey'];
  return keys.filter((key) => headers[key]).join(', ') || 'none';
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
  return {
    url: config.unlockUrl,
    body: { value: config.value, name: targetValue }
  };
}

async function runBackendCommand(config, cmd) {
  const action = ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist'].includes(cmd.action) ? cmd.action : 'unlock_sms';
  const rawValue = cmd.target_value || cmd.member_name || '';
  const targetValue = action === 'add_proxy_whitelist'
    ? String(rawValue).trim()
    : String(rawValue).trim().toLowerCase();
  if (!targetValue) return;
  const label = commandLabel(action);
  await setStatus({ state: 'running', message: `执行${label} ${targetValue}` });
  try {
    const request = commandRequest(config, action, targetValue);
    const res = await fetch(request.url, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers: config.headers,
      body: JSON.stringify(request.body)
    });
    const text = await res.text();
    await setStatus({
      state: res.ok ? 'success' : 'error',
      message: `${label} ${targetValue} HTTP ${res.status}`,
      detail: text.slice(0, 300)
    });
    await ack(config, cmd, res.ok ? 'success' : `http_${res.status}`, text);
  } catch (err) {
    await setStatus({ state: 'error', message: `${label}失败 ${targetValue}`, detail: err.message });
    await ack(config, cmd, 'fetch_failed', err.message);
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
    const authLabel = config.pageAuth
      ? `，已捕获当前登录态 ${headerSummary((config.pageAuth && config.pageAuth.headers) || {})}`
      : '，未捕获当前登录态';
    await setStatus({ state: 'polling', message: '正在轮询命令' + authLabel });
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=25&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const data = await res.json();
    if (data && data.ok && data.cmd && ['unlock_sms', 'clear_login_error', 'add_proxy_whitelist'].includes(data.cmd.action)) {
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
  await chrome.storage.local.set({ config: DEFAULT_CONFIG, enabled: true });
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
        message: `已捕获当前登录态: ${headerSummary((message.auth && message.auth.headers) || {})}`,
        detail: '登录态已更新'
      }))
      .then(() => sendResponse({ ok: true }))
      .catch((err) => sendResponse({ ok: false, error: err.message }));
    return true;
  }
  return false;
});

chrome.alarms.create('poll', { periodInMinutes: 0.5 });
pollOnce();
