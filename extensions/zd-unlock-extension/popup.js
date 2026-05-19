const enabled = document.getElementById('enabled');
const status = document.getElementById('status');
const detail = document.getElementById('detail');
const poll = document.getElementById('poll');

function renderStatus(s = {}, pageAuth = null) {
  status.textContent = `${s.message || '暂无状态'} (${s.time || '-'})`;
  const lines = [];
  if (s.detail) lines.push(String(s.detail).includes('9site') ? '登录态已更新' : s.detail);
  else if (s.state) lines.push(s.state);
  if (pageAuth && pageAuth.capturedAt) lines.push(`已捕获当前登录态: ${pageAuth.capturedAt}`);
  detail.textContent = lines.join('\n');
}

async function load() {
  const data = await chrome.storage.local.get(['enabled', 'status', 'pageAuth']);
  enabled.checked = data.enabled !== false;
  renderStatus(data.status || { message: '尚未轮询' }, data.pageAuth);
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

load();
setInterval(load, 3000);
