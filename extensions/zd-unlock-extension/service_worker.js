const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
  unlockUrl: 'https://9sitebg.mvj4e7.com/central/admin/site/admin/v1/user/memberInfo/unlockIpOrNameForCheckPhone',
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
  const stored = await chrome.storage.local.get(['config', 'enabled']);
  return {
    enabled: stored.enabled !== false,
    config: {
      ...DEFAULT_CONFIG,
      ...(stored.config || {}),
      headers: {
        ...DEFAULT_CONFIG.headers,
        ...((stored.config && stored.config.headers) || {})
      }
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

async function unlock(config, cmd) {
  const memberName = String(cmd.member_name || '').trim().toLowerCase();
  if (!memberName) return;
  await setStatus({ state: 'running', message: `执行解锁 ${memberName}` });
  try {
    const res = await fetch(config.unlockUrl, {
      method: 'POST',
      mode: 'cors',
      credentials: 'include',
      headers: config.headers,
      body: JSON.stringify({ value: config.value, name: memberName })
    });
    const text = await res.text();
    await setStatus({
      state: res.ok ? 'success' : 'error',
      message: `解锁 ${memberName} HTTP ${res.status}`,
      detail: text.slice(0, 300)
    });
    await ack(config, cmd, res.ok ? 'success' : `http_${res.status}`, text);
  } catch (err) {
    await setStatus({ state: 'error', message: `解锁失败 ${memberName}`, detail: err.message });
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
    await setStatus({ state: 'polling', message: '正在轮询命令' });
    const res = await fetch(`${config.botBase}/api/cmd/poll?wait=25&secret=${encodeURIComponent(config.cmdSecret)}`, {
      cache: 'no-store'
    });
    const data = await res.json();
    if (data && data.ok && data.cmd && data.cmd.action === 'unlock_sms') {
      await setStatus({ state: 'received', message: `收到命令 ${data.cmd.member_name}` });
      await unlock(config, data.cmd);
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
  return false;
});

chrome.alarms.create('poll', { periodInMinutes: 0.5 });
pollOnce();
