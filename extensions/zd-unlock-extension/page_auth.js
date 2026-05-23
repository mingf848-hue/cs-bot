(() => {
  const API_HEADER_KEYS = [
    'x-api-appkey',
    'x-api-client',
    'x-api-site',
    'x-api-token',
    'x-api-user',
    'x-api-uuid',
    'x-api-version',
    'x-api-xsn',
    'x-api-xts',
    'use-new-api',
    'authorization',
    'language',
    'merchantname',
    'request-id',
    'user-id'
  ];
  let rememberedApiHeaders = {};

  function cookieValue(name) {
    const prefix = `${name}=`;
    return document.cookie
      .split(';')
      .map((item) => item.trim())
      .find((item) => item.startsWith(prefix))
      ?.slice(prefix.length) || '';
  }

  function parseUserInfo() {
    try {
      return JSON.parse(localStorage.getItem('userInfo') || '{}') || {};
    } catch {
      return {};
    }
  }

  function parseJsonValue(value) {
    try {
      return JSON.parse(String(value || ''));
    } catch {
      return null;
    }
  }

  function storageRows() {
    const rows = [];
    for (const store of [localStorage, sessionStorage]) {
      try {
        for (let i = 0; i < store.length; i += 1) {
          const key = store.key(i);
          rows.push({ key, value: store.getItem(key) || '' });
        }
      } catch {
        // ignore inaccessible storage
      }
    }
    return rows;
  }

  function findNestedValue(input, keys, depth = 0) {
    if (!input || typeof input !== 'object' || depth > 6) return '';
    const wanted = keys.map((key) => String(key).toLowerCase());
    for (const [key, value] of Object.entries(input)) {
      if (wanted.includes(String(key).toLowerCase()) && value != null && typeof value !== 'object') {
        return String(value);
      }
    }
    for (const value of Object.values(input)) {
      const found = findNestedValue(value, keys, depth + 1);
      if (found) return found;
    }
    return '';
  }

  function base64UrlDecode(value) {
    try {
      const normalized = String(value || '').replace(/-/g, '+').replace(/_/g, '/');
      const padded = normalized + '='.repeat((4 - (normalized.length % 4)) % 4);
      return decodeURIComponent(Array.from(atob(padded), (char) => `%${char.charCodeAt(0).toString(16).padStart(2, '0')}`).join(''));
    } catch {
      return '';
    }
  }

  function parseJwtPayload(token) {
    const parts = String(token || '').split('.');
    if (parts.length < 2) return {};
    return parseJsonValue(base64UrlDecode(parts[1])) || {};
  }

  function findJwtToken(rows) {
    const tokenPattern = /eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+/;
    for (const row of rows) {
      const direct = String(row.value || '').match(tokenPattern);
      if (direct) return direct[0];
      const parsed = parseJsonValue(row.value);
      if (parsed && typeof parsed === 'object') {
        const candidate = findNestedValue(parsed, ['authorization', 'accessToken', 'access_token', 'token', 'jwt']);
        const matched = String(candidate || '').match(tokenPattern);
        if (matched) return matched[0];
      }
    }
    return '';
  }

  function merchantHeadersFromStorage() {
    try {
      const host = location.hostname || '';
      if (!host.endsWith('dbsportxxxwo8.com')) return {};
      const rows = storageRows();
      const token = findJwtToken(rows);
      if (!token) return {};
      const payload = parseJwtPayload(token);
      const merchantId = String(payload.merchantId || payload.userId || payload.id || '').trim();
      const merchantName = String(payload.merchantName || payload.merchantname || '').trim();
      const headers = {
        authorization: token,
        language: 'zs'
      };
      if (merchantId) headers['user-id'] = merchantId;
      if (merchantName) headers.merchantname = encodeURIComponent(merchantName);
      return headers;
    } catch {
      return {};
    }
  }

  function normalizeHeaders(input) {
    const out = {};
    if (!input) return out;
    try {
      if (input instanceof Headers) {
        input.forEach((value, key) => {
          out[String(key).toLowerCase()] = String(value);
        });
        return out;
      }
    } catch {
      // ignore
    }
    if (Array.isArray(input)) {
      for (const [key, value] of input) out[String(key).toLowerCase()] = String(value);
      return out;
    }
    if (typeof input === 'object') {
      for (const [key, value] of Object.entries(input)) out[String(key).toLowerCase()] = String(value);
    }
    return out;
  }

  function collectPageAuth(extraHeaders = {}) {
    const userInfo = parseUserInfo();
    const headers = {
      'x-api-token': userInfo.token || cookieValue('tb-token') || '',
      'x-api-user': userInfo.username || cookieValue('user-name') || cookieValue('user-email') || '',
      'x-api-uuid': localStorage.getItem('_uuid') || '',
      ...merchantHeadersFromStorage(),
      ...extraHeaders
    };
    for (const key of Object.keys(headers)) {
      if (!headers[key]) delete headers[key];
    }
    return {
      href: location.href,
      capturedAt: new Date().toISOString(),
      headers
    };
  }

  function sendAuth(extraHeaders = {}) {
    rememberedApiHeaders = { ...rememberedApiHeaders, ...extraHeaders };
    const auth = collectPageAuth(rememberedApiHeaders);
    chrome.runtime.sendMessage({ type: 'pageAuth', auth }).catch(() => {});
  }

  function sendRecorderState(state = {}) {
    window.postMessage({
      source: 'csbot-zd-content',
      type: 'recorderControl',
      state: {
        enabled: !!state.enabled,
        startedAt: state.startedAt || ''
      }
    }, '*');
  }

  async function syncRecorderState() {
    try {
      const data = await chrome.storage.local.get(['recorderState']);
      sendRecorderState(data.recorderState || { enabled: false });
    } catch {
      sendRecorderState({ enabled: false });
    }
  }

  function captureHeaders(rawHeaders) {
    const normalized = normalizeHeaders(rawHeaders);
    const captured = {};
    for (const key of API_HEADER_KEYS) {
      if (normalized[key]) captured[key] = normalized[key];
    }
    if (Object.keys(captured).length) sendAuth(captured);
  }

  function showToast(text, ok = true) {
    try {
      const old = document.getElementById('csbot-zd-toast');
      if (old) old.remove();
      const box = document.createElement('div');
      box.id = 'csbot-zd-toast';
      box.textContent = text;
      box.style.cssText = [
        'position:fixed',
        'top:18px',
        'right:18px',
        'z-index:2147483647',
        'padding:10px 14px',
        'border-radius:8px',
        'font:600 13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif',
        `color:${ok ? '#14532d' : '#7f1d1d'}`,
        `background:${ok ? '#dcfce7' : '#fee2e2'}`,
        `border:1px solid ${ok ? '#86efac' : '#fecaca'}`,
        'box-shadow:0 12px 30px rgba(15,23,42,.16)'
      ].join(';');
      document.documentElement.appendChild(box);
      setTimeout(() => box.remove(), 4500);
    } catch {
      // best effort
    }
  }

  function injectMainWorldCapture() {
    const script = document.createElement('script');
    script.textContent = `(() => {
      const API_HEADER_KEYS = ${JSON.stringify(API_HEADER_KEYS)};
      let recorderEnabled = false;
      let recorderStartedAt = '';
      function normalizeHeaders(input) {
        const out = {};
        if (!input) return out;
        try {
          if (input instanceof Headers) {
            input.forEach((value, key) => { out[String(key).toLowerCase()] = String(value); });
            return out;
          }
        } catch (_) {}
        if (Array.isArray(input)) {
          for (const [key, value] of input) out[String(key).toLowerCase()] = String(value);
          return out;
        }
        if (typeof input === 'object') {
          for (const [key, value] of Object.entries(input)) out[String(key).toLowerCase()] = String(value);
        }
        return out;
      }
      function bodyToText(body) {
        if (body == null) return '';
        if (typeof body === 'string') return body;
        if (body instanceof URLSearchParams) return body.toString();
        if (body instanceof FormData) {
          const items = [];
          body.forEach((value, key) => {
            if (value instanceof File) items.push(String(key) + '=[File ' + value.name + ' ' + value.size + 'B]');
            else items.push(String(key) + '=' + String(value));
          });
          return items.join('&');
        }
        if (body instanceof Blob) return '[Blob ' + body.type + ' ' + body.size + 'B]';
        if (body instanceof ArrayBuffer) return '[ArrayBuffer ' + body.byteLength + 'B]';
        try {
          if (ArrayBuffer.isView(body)) return '[' + body.constructor.name + ' ' + body.byteLength + 'B]';
        } catch (_) {}
        try { return JSON.stringify(body); } catch (_) { return String(body); }
      }
      function absoluteUrl(input) {
        try { return new URL(String(input || ''), location.href).href; } catch (_) { return String(input || ''); }
      }
      function xhrResponseText(xhr) {
        try {
          if (!xhr.responseType || xhr.responseType === 'text' || xhr.responseType === 'json') {
            return typeof xhr.response === 'string' ? xhr.response : xhr.responseText || JSON.stringify(xhr.response || '');
          }
          return '[' + xhr.responseType + ' response]';
        } catch (_) {
          return '';
        }
      }
      function fetchInfo(input, init) {
        const request = input instanceof Request ? input : null;
        return {
          method: String((init && init.method) || (request && request.method) || 'GET').toUpperCase(),
          url: absoluteUrl(request ? request.url : input),
          headers: normalizeHeaders((init && init.headers) || (request && request.headers)),
          body: bodyToText(init && init.body)
        };
      }
      function parseJsonBody(text) {
        try { return JSON.parse(text || '{}') || {}; } catch (_) { return {}; }
      }
      function postUnlockValue(url, bodyText) {
        if (!String(url || '').includes('/user/memberInfo/unlockIpOrNameForCheckPhone')) return;
        const body = parseJsonBody(bodyText);
        const value = String((body && body.value) || '').trim();
        if (!value) return;
        window.postMessage({ source: 'csbot-zd-page', type: 'unlockValue', value }, '*');
      }
      function postMerchantEndpoint(url) {
        let parsed = null;
        try { parsed = new URL(String(url || ''), location.href); } catch (_) { return; }
        if (!parsed.hostname.endsWith('dbsportxxxwo8.com')) return;
        const endpointMap = [
          ['/admin/userReport/getStatistics', 'merchantStatisticsUrl'],
          ['/admin/userReport/queryTicketList', 'merchantTicketListUrl'],
          ['/admin/noticeNew/notice', 'merchantNoticeUrl'],
          ['/admin/noticeNew/noticeDetail', 'merchantNoticeDetailUrl'],
          ['/admin/settlement/queryNoSettleTicketList', 'merchantSettlementListUrl'],
          ['/admin/settlement/getStatistics', 'merchantSettlementStatisticsUrl']
        ];
        const matched = endpointMap.find(([path]) => parsed.pathname.endsWith(path));
        if (!matched) return;
        window.postMessage({
          source: 'csbot-zd-page',
          type: 'merchantEndpoint',
          key: matched[1],
          url: parsed.origin + parsed.pathname
        }, '*');
      }
      function postHeaders(rawHeaders) {
        const normalized = normalizeHeaders(rawHeaders);
        const captured = {};
        for (const key of API_HEADER_KEYS) {
          if (normalized[key]) captured[key] = normalized[key];
        }
        if (Object.keys(captured).length) {
          window.postMessage({ source: 'csbot-zd-page', type: 'apiHeaders', headers: captured }, '*');
        }
      }
      function postRecord(record) {
        if (!recorderEnabled) return;
        window.postMessage({
          source: 'csbot-zd-page',
          type: 'apiRecord',
          record: Object.assign({
            page_url: location.href,
            recorder_started_at: recorderStartedAt
          }, record)
        }, '*');
      }
      window.addEventListener('message', (event) => {
        if (event.source !== window) return;
        const data = event.data || {};
        if (data.source !== 'csbot-zd-content' || data.type !== 'recorderControl') return;
        recorderEnabled = !!(data.state && data.state.enabled);
        recorderStartedAt = (data.state && data.state.startedAt) || '';
      });
      const originalFetch = window.fetch;
      if (typeof originalFetch === 'function' && !window.__csbotFetchPatched) {
        window.__csbotFetchPatched = true;
        window.fetch = function patchedFetch(input, init = {}) {
          const info = fetchInfo(input, init || {});
          const started = Date.now();
          try { postHeaders(info.headers); } catch (_) {}
          try { postUnlockValue(info.url, info.body); } catch (_) {}
          try { postMerchantEndpoint(info.url); } catch (_) {}
          return originalFetch.apply(this, arguments).then((res) => {
            if (recorderEnabled) {
              try {
                const clone = res.clone();
                clone.text()
                  .then((text) => postRecord({
                    transport: 'fetch',
                    method: info.method,
                    url: info.url,
                    request_headers: info.headers,
                    request_body: info.body,
                    status: res.status,
                    ok: res.ok,
                    response_url: res.url,
                    response_body: text,
                    duration_ms: Date.now() - started
                  }))
                  .catch((err) => postRecord({
                    transport: 'fetch',
                    method: info.method,
                    url: info.url,
                    request_headers: info.headers,
                    request_body: info.body,
                    status: res.status,
                    ok: res.ok,
                    response_url: res.url,
                    response_body: '',
                    error: err && err.message ? err.message : String(err || ''),
                    duration_ms: Date.now() - started
                  }));
              } catch (err) {
                postRecord({
                  transport: 'fetch',
                  method: info.method,
                  url: info.url,
                  request_headers: info.headers,
                  request_body: info.body,
                  error: err && err.message ? err.message : String(err || ''),
                  duration_ms: Date.now() - started
                });
              }
            }
            return res;
          }, (err) => {
            postRecord({
              transport: 'fetch',
              method: info.method,
              url: info.url,
              request_headers: info.headers,
              request_body: info.body,
              error: err && err.message ? err.message : String(err || ''),
              duration_ms: Date.now() - started
            });
            throw err;
          });
        };
      }
      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
      const originalSend = XMLHttpRequest.prototype.send;
      if (!XMLHttpRequest.prototype.__csbotXhrPatched) {
        XMLHttpRequest.prototype.__csbotXhrPatched = true;
        XMLHttpRequest.prototype.open = function patchedOpen() {
          this.__csbotHeaders = {};
          this.__csbotMethod = arguments[0] || 'GET';
          this.__csbotUrl = absoluteUrl(arguments[1] || '');
          return originalOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.setRequestHeader = function patchedSetRequestHeader(key, value) {
          try {
            if (!this.__csbotHeaders) this.__csbotHeaders = {};
            this.__csbotHeaders[String(key).toLowerCase()] = String(value);
          } catch (_) {}
          return originalSetRequestHeader.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function patchedSend(body) {
          const started = Date.now();
          const requestBody = bodyToText(body);
          const xhr = this;
          let finished = false;
          function finish(error) {
            if (finished) return;
            finished = true;
            postRecord({
              transport: 'xhr',
              method: String(xhr.__csbotMethod || 'GET').toUpperCase(),
              url: xhr.__csbotUrl || '',
              request_headers: xhr.__csbotHeaders || {},
              request_body: requestBody,
              status: xhr.status || null,
              ok: xhr.status >= 200 && xhr.status < 300,
              response_url: xhr.responseURL || xhr.__csbotUrl || '',
              response_body: xhrResponseText(xhr),
              error: error || '',
              duration_ms: Date.now() - started
            });
          }
          try { this.addEventListener('loadend', () => finish(''), { once: true }); } catch (_) {}
          try { this.addEventListener('error', () => finish('xhr_error'), { once: true }); } catch (_) {}
          try { postHeaders(this.__csbotHeaders); } catch (_) {}
          try { postUnlockValue(this.__csbotUrl, requestBody); } catch (_) {}
          try { postMerchantEndpoint(this.__csbotUrl); } catch (_) {}
          return originalSend.apply(this, arguments);
        };
      }
      window.postMessage({ source: 'csbot-zd-page', type: 'installed' }, '*');
    })();`;
    (document.documentElement || document.head || document.body).appendChild(script);
    script.remove();
  }

  window.addEventListener('message', (event) => {
    if (event.source !== window) return;
    const data = event.data || {};
    if (data.source !== 'csbot-zd-page') return;
    if (data.type === 'apiHeaders') captureHeaders(data.headers);
    if (data.type === 'unlockValue') {
      chrome.runtime.sendMessage({ type: 'unlockValue', value: data.value })
        .then((resp) => showToast(resp && resp.ok ? '短信参数已保存' : '短信参数保存失败', !!(resp && resp.ok)))
        .catch(() => showToast('短信参数保存失败', false));
    }
    if (data.type === 'merchantEndpoint') {
      chrome.runtime.sendMessage({ type: 'merchantEndpoint', key: data.key, url: data.url }).catch(() => {});
    }
    if (data.type === 'installed') sendAuth();
    if (data.type === 'apiRecord') {
      chrome.runtime.sendMessage({ type: 'recorderRecord', record: data.record }).catch(() => {});
    }
  });

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && changes.recorderState) {
      sendRecorderState(changes.recorderState.newValue || { enabled: false });
    }
  });

  sendAuth();
  syncRecorderState();
  setInterval(() => sendAuth(), 5000);
  setInterval(() => syncRecorderState(), 5000);
})();
