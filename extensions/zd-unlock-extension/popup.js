const DEFAULT_CONFIG = {
  botBase: 'https://arcshelp.zeabur.app',
  cmdSecret: 'J7kN3mQxR9vTsW2pYzBf',
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

const enabled = document.getElementById('enabled');
const botBase = document.getElementById('botBase');
const cmdSecret = document.getElementById('cmdSecret');
const value = document.getElementById('value');
const headers = document.getElementById('headers');
const status = document.getElementById('status');
const detail = document.getElementById('detail');

function renderStatus(s) {
  status.textContent = `${s.message || '暂无状态'} (${s.time || '-'})`;
  detail.textContent = s.detail || s.state || '';
}

async function load() {
  const data = await chrome.storage.local.get(['config', 'enabled', 'status', 'pageAuth']);
  const config = { ...DEFAULT_CONFIG, ...(data.config || {}), headers: { ...DEFAULT_CONFIG.headers, ...((data.config || {}).headers || {}) } };
  enabled.checked = data.enabled !== false;
  botBase.value = config.botBase || '';
  cmdSecret.value = config.cmdSecret || '';
  value.value = config.value || '';
  headers.value = JSON.stringify(config.headers || {}, null, 2);
  const currentStatus = data.status || { message: '尚未轮询' };
  if (data.pageAuth && data.pageAuth.capturedAt) {
    currentStatus.detail = `${currentStatus.detail || ''}\n已捕获9site登录态: ${data.pageAuth.capturedAt}`;
  }
  renderStatus(currentStatus);
}

async function save() {
  let parsedHeaders;
  try {
    parsedHeaders = JSON.parse(headers.value || '{}');
  } catch (err) {
    renderStatus({ message: 'Headers JSON 格式错误', detail: err.message });
    return;
  }
  const config = {
    ...DEFAULT_CONFIG,
    botBase: botBase.value.trim(),
    cmdSecret: cmdSecret.value.trim(),
    value: value.value.trim(),
    headers: parsedHeaders
  };
  chrome.runtime.sendMessage({ type: 'saveConfig', config, enabled: enabled.checked }, (resp) => {
    if (!resp || !resp.ok) renderStatus({ message: '保存失败', detail: (resp && resp.error) || '' });
    else renderStatus({ message: '已保存，正在轮询' });
  });
}

document.getElementById('save').addEventListener('click', save);
document.getElementById('poll').addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'pollNow' }, () => load());
});

load();
setInterval(load, 3000);
