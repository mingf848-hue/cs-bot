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
    'use-new-api'
  ];

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
    const auth = collectPageAuth(extraHeaders);
    chrome.runtime.sendMessage({ type: 'pageAuth', auth }).catch(() => {});
  }

  function captureHeaders(rawHeaders) {
    const normalized = normalizeHeaders(rawHeaders);
    const captured = {};
    for (const key of API_HEADER_KEYS) {
      if (normalized[key]) captured[key] = normalized[key];
    }
    if (Object.keys(captured).length) sendAuth(captured);
  }

  function injectMainWorldCapture() {
    const script = document.createElement('script');
    script.textContent = `(() => {
      const API_HEADER_KEYS = ${JSON.stringify(API_HEADER_KEYS)};
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
      const originalFetch = window.fetch;
      if (typeof originalFetch === 'function' && !window.__csbotFetchPatched) {
        window.__csbotFetchPatched = true;
        window.fetch = function patchedFetch(input, init = {}) {
          try { postHeaders(init.headers || (input && input.headers)); } catch (_) {}
          return originalFetch.apply(this, arguments);
        };
      }
      const originalOpen = XMLHttpRequest.prototype.open;
      const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
      const originalSend = XMLHttpRequest.prototype.send;
      if (!XMLHttpRequest.prototype.__csbotXhrPatched) {
        XMLHttpRequest.prototype.__csbotXhrPatched = true;
        XMLHttpRequest.prototype.open = function patchedOpen() {
          this.__csbotHeaders = {};
          return originalOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.setRequestHeader = function patchedSetRequestHeader(key, value) {
          try {
            if (!this.__csbotHeaders) this.__csbotHeaders = {};
            this.__csbotHeaders[String(key).toLowerCase()] = String(value);
          } catch (_) {}
          return originalSetRequestHeader.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function patchedSend() {
          try { postHeaders(this.__csbotHeaders); } catch (_) {}
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
    if (data.type === 'installed') sendAuth();
  });

  sendAuth();
  injectMainWorldCapture();
  setInterval(() => sendAuth(), 5000);
})();
