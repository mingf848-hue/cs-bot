const status = document.getElementById('status');
const statusTime = document.getElementById('statusTime');
const statusState = document.getElementById('statusState');
const stateDot = document.getElementById('stateDot');
const detail = document.getElementById('detail');
const auth9 = document.getElementById('auth9');
const auth6 = document.getElementById('auth6');
const authMerchant = document.getElementById('authMerchant');
const authSummary = document.getElementById('authSummary');
const recStart = document.getElementById('recStart');
const recStop = document.getElementById('recStop');
const recExport = document.getElementById('recExport');
const recClear = document.getElementById('recClear');
const refreshBackstage = document.getElementById('refreshBackstage');
const recorderBadge = document.getElementById('recorderBadge');
const recorderDetail = document.getElementById('recorderDetail');
const smsValueBadge = document.getElementById('smsValueBadge');
const smsValueDetail = document.getElementById('smsValueDetail');
const versionBadge = document.getElementById('versionBadge');

const STATE_LABELS = {
  success: '正常',
  running: '执行中',
  error: '失败',
  auth: '登录',
  auth_refresh: '同步中',
  idle: '待机'
};
const SITE_9_HOSTS = [
  '9aynxg.hh9al.com',
  '9aynxg.hp9yk.com',
  '9aynxg.jr91e.com',
  '9aynxg.w77n3i.com',
  '9aynxg.ls3v0z.com'
];
const SITE_6_HOSTS = [
  '6aopna.fa69m.com',
  '6aopna.f26g7.com',
  '6aopna.fb6e5.com',
  '6aopna.30g7e1.com',
  '6aopna.a079a8.com'
];

try {
  versionBadge.textContent = `v${chrome.runtime.getManifest().version}`;
} catch {
  versionBadge.textContent = 'v-';
}

function authHost(auth = {}) {
  try {
    return new URL(auth.href || '').host;
  } catch {
    return '';
  }
}

function siteAuth(pageAuth = null, pageAuthByHost = {}, hosts, site, requiredHeaders = ['x-api-token', 'x-api-user'], extraAuths = []) {
  const hostList = Array.isArray(hosts) ? hosts : [hosts];
  const matches = (auth) => {
    const headers = (auth && auth.headers) || {};
    const href = String((auth && auth.href) || '');
    return hostList.includes(authHost(auth)) || hostList.some((host) => href.includes(host)) || String(headers['x-api-site'] || '') === site;
  };
  const candidates = [
    ...hostList.map((host) => pageAuthByHost[host]),
    pageAuthByHost[site],
    ...extraAuths,
    pageAuth,
    ...Object.values(pageAuthByHost || {})
  ].filter(Boolean);
  const auth = candidates.find((item) => {
    const headers = (item && item.headers) || {};
    return matches(item) && requiredHeaders.every((key) => headers[key]);
  });
  const headers = (auth && auth.headers) || {};
  return !!(auth && auth.capturedAt && requiredHeaders.every((key) => headers[key]));
}

function renderSiteLight(el, on, text = '') {
  if (!el) return;
  el.classList.toggle('on', on);
  el.title = text || (on ? '已同步' : '未同步');
  const statusEl = el.querySelector('[data-role="status"]');
  if (statusEl) statusEl.textContent = text || (on ? '已同步' : '未同步');
}

function setState(state) {
  const normalized = state || 'idle';
  statusState.dataset.state = normalized;
  stateDot.dataset.state = normalized;
  statusState.textContent = STATE_LABELS[normalized] || normalized;
}

function renderStatus(s = {}, pageAuth = null, pageAuthByHost = {}, pageAuthByMerchant = {}) {
  status.textContent = s.message || '暂无状态';
  statusTime.textContent = s.time || '-';
  setState(s.state || 'idle');
  const lines = [];
  if (s.detail && s.state !== 'auth') lines.push(s.detail);
  else if (s.state && s.state !== 'auth') lines.push(s.state);
  detail.textContent = lines.join('\n');

  const site9On = siteAuth(pageAuth, pageAuthByHost, SITE_9_HOSTS, '9001');
  const site6On = siteAuth(pageAuth, pageAuthByHost, SITE_6_HOSTS, '6001');
  const merchantAuths = Object.values(pageAuthByMerchant || {});
  const merchantOn = siteAuth(
    pageAuth,
    pageAuthByHost,
    ['merchant-own-backstage.dbsportxxxwo8.com', 'api-merchant-backstage.dbsportxxxwo8.com'],
    'merchant',
    ['authorization', 'user-id'],
    merchantAuths
  );
  renderSiteLight(auth9, site9On);
  renderSiteLight(auth6, site6On);
  renderSiteLight(authMerchant, merchantOn, merchantOn ? `已同步 ${Math.max(merchantAuths.length, 1)}` : '未同步');
  authSummary.textContent = `${[site9On, site6On, merchantOn].filter(Boolean).length}/3`;
}

async function load() {
  const data = await chrome.storage.local.get(['status', 'config', 'pageAuth', 'pageAuthByHost', 'pageAuthByMerchant', 'recorderState', 'recorderRecords']);
  renderStatus(data.status || { message: '尚未轮询' }, data.pageAuth, data.pageAuthByHost || {}, data.pageAuthByMerchant || {});
  renderSmsValue(data.config || {});
  renderRecorder(data.recorderState || {}, data.recorderRecords || []);
}

function renderSmsValue(config = {}) {
  const saved = !!String(config.value || '').trim();
  smsValueBadge.textContent = saved ? '已保存' : '未保存';
  smsValueBadge.classList.toggle('on', saved);
  smsValueDetail.textContent = saved ? `保存：${formatTime(config.unlockValueSavedAt)}` : '未捕获';
}

function renderRecorder(state = {}, records = []) {
  const count = records.length || Number(state.count || 0);
  recorderBadge.textContent = state.enabled ? `录制中 ${count}` : `已停止 ${count}`;
  recorderBadge.classList.toggle('on', !!state.enabled);
  recStart.disabled = !!state.enabled;
  recStop.disabled = !state.enabled;
  recExport.disabled = count === 0;
  recorderDetail.textContent = state.enabled
    ? `开始：${formatTime(state.startedAt)}\n记录：${count}`
    : `记录：${count}${state.stoppedAt ? `\n停止：${formatTime(state.stoppedAt)}` : ''}`;
}

function formatTime(value) {
  if (!value) return '-';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', { hour12: false });
}

function send(type) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ type }, (resp) => resolve(resp || { ok: false, error: 'no response' }));
  });
}

refreshBackstage.addEventListener('click', async () => {
  const originalText = refreshBackstage.textContent;
  refreshBackstage.disabled = true;
  refreshBackstage.textContent = '同步中';
  status.textContent = '正在刷新后台页面';
  statusTime.textContent = formatTime(new Date().toISOString());
  setState('auth_refresh');
  detail.textContent = '';
  const resp = await send('refreshBackstageTabs');
  if (!resp.ok) {
    status.textContent = '刷新失败';
    setState('error');
    detail.textContent = resp.error || '';
    refreshBackstage.disabled = false;
    refreshBackstage.textContent = originalText;
    return;
  }
  status.textContent = `已刷新并同步 ${resp.count || 0} 个后台页面`;
  detail.textContent = resp.detail || (resp.count ? '同步完成' : '未找到后台页面');
  await load();
  refreshBackstage.disabled = false;
  refreshBackstage.textContent = originalText;
});

recStart.addEventListener('click', async () => {
  const resp = await send('recorderStart');
  if (!resp.ok) renderStatus({ message: '开始录制失败', state: 'error', detail: resp.error || '' });
  await load();
});

recStop.addEventListener('click', async () => {
  const resp = await send('recorderStop');
  if (!resp.ok) renderStatus({ message: '停止录制失败', state: 'error', detail: resp.error || '' });
  await load();
});

recClear.addEventListener('click', async () => {
  const resp = await send('recorderClear');
  if (!resp.ok) renderStatus({ message: '清空失败', state: 'error', detail: resp.error || '' });
  await load();
});

recExport.addEventListener('click', async () => {
  const data = await chrome.storage.local.get(['recorderState', 'recorderRecords', 'pageAuth', 'pageAuthByHost']);
  const payload = {
    exported_at: new Date().toISOString(),
    recorder_state: data.recorderState || {},
    page_auth: data.pageAuth ? { href: data.pageAuth.href, capturedAt: data.pageAuth.capturedAt } : null,
    page_auth_hosts: Object.fromEntries(Object.entries(data.pageAuthByHost || {}).map(([host, auth]) => [host, { href: auth.href, capturedAt: auth.capturedAt }])),
    records: data.recorderRecords || []
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `csbot-api-recording-${Date.now()}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 500);
});

load();
setInterval(load, 3000);
