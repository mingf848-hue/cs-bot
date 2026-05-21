const enabled = document.getElementById('enabled');
const status = document.getElementById('status');
const detail = document.getElementById('detail');
const auth9 = document.getElementById('auth9');
const auth6 = document.getElementById('auth6');
const poll = document.getElementById('poll');
const recStart = document.getElementById('recStart');
const recStop = document.getElementById('recStop');
const recExport = document.getElementById('recExport');
const recClear = document.getElementById('recClear');
const recorderBadge = document.getElementById('recorderBadge');
const recorderDetail = document.getElementById('recorderDetail');

function authHost(auth = {}) {
  try {
    return new URL(auth.href || '').host;
  } catch {
    return '';
  }
}

function siteAuth(pageAuth = null, pageAuthByHost = {}, host, site) {
  const auth = pageAuthByHost[host]
    || (authHost(pageAuth) === host ? pageAuth : null)
    || (String(((pageAuth || {}).headers || {})['x-api-site'] || '') === site ? pageAuth : null);
  const headers = (auth && auth.headers) || {};
  return !!(auth && auth.capturedAt && (headers['x-api-token'] || headers['x-api-user']));
}

function renderSiteLight(el, on) {
  if (!el) return;
  el.classList.toggle('on', on);
  el.title = on ? '已登录' : '未登录';
}

function renderStatus(s = {}, pageAuth = null, pageAuthByHost = {}) {
  status.textContent = `${s.message || '暂无状态'} (${s.time || '-'})`;
  const lines = [];
  if (s.detail && s.state !== 'auth') lines.push(s.detail);
  else if (s.state && s.state !== 'auth') lines.push(s.state);
  detail.textContent = lines.join('\n');
  renderSiteLight(auth9, siteAuth(pageAuth, pageAuthByHost, '9sitebg.mvj4e7.com', '9001'));
  renderSiteLight(auth6, siteAuth(pageAuth, pageAuthByHost, '6sitebg.oj61i4.com', '6001'));
}

async function load() {
  const data = await chrome.storage.local.get(['enabled', 'status', 'pageAuth', 'pageAuthByHost', 'recorderState', 'recorderRecords']);
  enabled.checked = data.enabled !== false;
  renderStatus(data.status || { message: '尚未轮询' }, data.pageAuth, data.pageAuthByHost || {});
  renderRecorder(data.recorderState || {}, data.recorderRecords || []);
}

function setEnabled() {
  chrome.runtime.sendMessage({ type: 'setEnabled', enabled: enabled.checked }, (resp) => {
    if (!resp || !resp.ok) {
      renderStatus({ message: '切换失败', detail: (resp && resp.error) || '' });
    } else {
      load();
    }
  });
}

enabled.addEventListener('change', setEnabled);
poll.addEventListener('click', () => {
  chrome.runtime.sendMessage({ type: 'pollNow' }, () => load());
});

function renderRecorder(state = {}, records = []) {
  const count = records.length || Number(state.count || 0);
  recorderBadge.textContent = state.enabled ? `录制中 · ${count}` : `已停止 · ${count}`;
  recorderBadge.classList.toggle('on', !!state.enabled);
  recStart.disabled = !!state.enabled;
  recStop.disabled = !state.enabled;
  recExport.disabled = count === 0;
  recorderDetail.textContent = state.enabled
    ? `开始时间：${formatTime(state.startedAt)}\n操作完后点“停止录制”，再导出 JSON。`
    : `记录数：${count}${state.stoppedAt ? `\n停止时间：${formatTime(state.stoppedAt)}` : ''}`;
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

recStart.addEventListener('click', async () => {
  const resp = await send('recorderStart');
  if (!resp.ok) renderStatus({ message: '开始录制失败', detail: resp.error || '' });
  await load();
});

recStop.addEventListener('click', async () => {
  const resp = await send('recorderStop');
  if (!resp.ok) renderStatus({ message: '停止录制失败', detail: resp.error || '' });
  await load();
});

recClear.addEventListener('click', async () => {
  const resp = await send('recorderClear');
  if (!resp.ok) renderStatus({ message: '清空失败', detail: resp.error || '' });
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
