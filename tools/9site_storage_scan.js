(() => {
  const mask = (v) => {
    v = String(v ?? '');
    if (v.length <= 12) return v;
    return v.slice(0, 6) + '...' + v.slice(-6);
  };

  const safeJson = (v) => {
    try {
      return JSON.parse(v);
    } catch {
      return null;
    }
  };

  const scanObj = (obj, path = '', out = []) => {
    if (!obj || typeof obj !== 'object') return out;
    for (const [k, v] of Object.entries(obj)) {
      const p = path ? `${path}.${k}` : k;
      const s = String(v ?? '');
      if (
        /token|user|uuid|xsn|xts|site|appkey|app|api|auth|login|phone|sms|unlock|value/i.test(p) ||
        /ZD_|x-api|token|uuid|xsn|xts|x8Bffk8DR9QOcdHPe6fFvQ/i.test(s)
      ) {
        out.push([p, typeof v === 'object' ? '[object]' : mask(s)]);
      }
      if (v && typeof v === 'object') scanObj(v, p, out);
    }
    return out;
  };

  const dumpStore = (store, name) => {
    const rows = [];
    for (const k of Object.keys(store)) {
      const raw = store.getItem(k);
      const parsed = safeJson(raw);
      rows.push({
        store: name,
        key: k,
        value_masked: mask(raw),
        parsed_hits: parsed ? scanObj(parsed).slice(0, 30) : []
      });
    }
    return rows;
  };

  const localRows = dumpStore(localStorage, 'localStorage');
  const sessionRows = dumpStore(sessionStorage, 'sessionStorage');

  const interesting = [...localRows, ...sessionRows].filter((row) => {
    const text = JSON.stringify(row);
    return /token|user|uuid|xsn|xts|site|appkey|app|api|auth|login|phone|sms|unlock|value|ZD_|x8Bffk8DR9QOcdHPe6fFvQ/i.test(text);
  });

  const cookies = document.cookie
    .split(';')
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => {
      const [k, ...rest] = x.split('=');
      return { key: k, value_masked: mask(rest.join('=')) };
    });

  const perf = performance
    .getEntriesByType('resource')
    .map((e) => e.name)
    .filter((u) => /unlock|checkPhone|memberInfo|phone|sms|login|token|xsn|xts/i.test(u))
    .slice(-50);

  const result = {
    href: location.href,
    localStorage_keys: Object.keys(localStorage),
    sessionStorage_keys: Object.keys(sessionStorage),
    interesting_storage: interesting,
    cookies,
    matching_resource_urls: perf,
    known_value_found_in_storage:
      JSON.stringify([...localRows, ...sessionRows]).includes('x8Bffk8DR9QOcdHPe6fFvQ')
  };

  console.log('===== CSBOT_9SITE_SCAN_RESULT_START =====');
  console.log(JSON.stringify(result, null, 2));
  console.log('===== CSBOT_9SITE_SCAN_RESULT_END =====');
})();
