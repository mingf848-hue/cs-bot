(() => {
  if (window.__csbotMainCaptureInstalled) return;
  window.__csbotMainCaptureInstalled = true;

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
      return originalSend.apply(this, arguments);
    };
  }

  window.postMessage({ source: 'csbot-zd-page', type: 'installed' }, '*');
})();
