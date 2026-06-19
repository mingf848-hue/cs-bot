const CONFIG = {
  sheetName: 'JN/ML大额催促登记表',
  token: 'CHANGE_ME_TO_A_RANDOM_SECRET',
  headerRow: 1,
  lookbackDays: 30,
  columns: {
    orderNo: '提款订单',
    withdrawAt: '提款时间',
    completionAt: '订单完成时间',
    status: '订单状态'
  }
};

function doGet(e) {
  return handleRequest(e);
}

function doPost(e) {
  return handleRequest(e);
}

function handleRequest(e) {
  try {
    const params = (e && e.parameter) || {};
    if (params.token !== CONFIG.token) {
      return json({ ok: false, error: 'unauthorized' });
    }

    const action = params.action || '';
    if (action === 'pending') {
      return json({ ok: true, rows: getPendingRows() });
    }

    if (action === 'update') {
      const body = JSON.parse((e.postData && e.postData.contents) || '{}');
      return json(updateRows(body.results || []));
    }

    return json({ ok: false, error: 'unknown action' });
  } catch (err) {
    return json({ ok: false, error: String((err && err.message) || err) });
  }
}

function getSheetAndColumns() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sheet = ss.getSheetByName(CONFIG.sheetName);
  if (!sheet) {
    throw new Error('找不到工作表：' + CONFIG.sheetName);
  }

  const lastCol = sheet.getLastColumn();
  const headers = sheet.getRange(CONFIG.headerRow, 1, 1, lastCol).getValues()[0]
    .map((value) => String(value || '').trim());

  const col = {};
  Object.entries(CONFIG.columns).forEach(([key, name]) => {
    const index = headers.indexOf(name);
    if (index < 0) {
      throw new Error('找不到列名：' + name);
    }
    col[key] = index + 1;
  });

  return { sheet, col };
}

function getPendingRows() {
  const { sheet, col } = getSheetAndColumns();
  const lastRow = sheet.getLastRow();
  if (lastRow <= CONFIG.headerRow) {
    return [];
  }

  const values = sheet
    .getRange(CONFIG.headerRow + 1, 1, lastRow - CONFIG.headerRow, sheet.getLastColumn())
    .getValues();
  const rows = [];
  const completedByOrder = {};
  const cutoff = cutoffDate();

  values.forEach((row) => {
    const orderNo = normalizeOrderNo(row[col.orderNo - 1]);
    const completionAt = cellText(row[col.completionAt - 1]);
    if (!orderNo || !isCompletionDateTime(row[col.completionAt - 1]) || completedByOrder[orderNo]) {
      return;
    }
    completedByOrder[orderNo] = {
      completionAt,
      status: cellText(row[col.status - 1])
    };
  });

  values.forEach((row, index) => {
    const rowNumber = CONFIG.headerRow + 1 + index;
    const orderNo = normalizeOrderNo(row[col.orderNo - 1]);
    const withdrawAt = parseSheetDate(row[col.withdrawAt - 1]);

    if (!orderNo || !withdrawAt || withdrawAt < cutoff || isCompletionDateTime(row[col.completionAt - 1])) {
      return;
    }

    const item = {
      row: rowNumber,
      orderNo,
      withdrawAt: formatDateTime(withdrawAt)
    };
    if (completedByOrder[orderNo]) {
      item.existingCompletionAt = completedByOrder[orderNo].completionAt;
      item.existingStatus = completedByOrder[orderNo].status;
      item.skipQuery = true;
    }
    rows.push(item);
  });

  return rows;
}

function normalizeOrderNo(value) {
  return String(value || '').trim().toUpperCase();
}

function cellText(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value.getTime())) {
    return formatDateTime(value);
  }
  return String(value || '').trim();
}

function isCompletionDateTime(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value.getTime())) {
    return true;
  }
  return /^\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?$/.test(String(value || '').trim());
}

function cutoffDate() {
  const date = new Date();
  date.setDate(date.getDate() - Number(CONFIG.lookbackDays || 30));
  date.setHours(0, 0, 0, 0);
  return date;
}

function parseSheetDate(value) {
  if (Object.prototype.toString.call(value) === '[object Date]' && !isNaN(value.getTime())) {
    return value;
  }

  const text = String(value || '').trim();
  const match = text.match(/^(\d{4})[-/](\d{1,2})[-/](\d{1,2})(?:\s+(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?)?$/);
  if (!match) {
    return null;
  }

  const date = new Date(
    Number(match[1]),
    Number(match[2]) - 1,
    Number(match[3]),
    Number(match[4] || 0),
    Number(match[5] || 0),
    Number(match[6] || 0)
  );
  return isNaN(date.getTime()) ? null : date;
}

function formatDateTime(date) {
  return [
    date.getFullYear(),
    '-',
    pad2(date.getMonth() + 1),
    '-',
    pad2(date.getDate()),
    ' ',
    pad2(date.getHours()),
    ':',
    pad2(date.getMinutes()),
    ':',
    pad2(date.getSeconds())
  ].join('');
}

function pad2(value) {
  return String(value).padStart(2, '0');
}

function updateRows(results) {
  const { sheet, col } = getSheetAndColumns();
  const updated = [];
  const skipped = [];
  const lastRow = sheet.getLastRow();
  const validItems = (results || [])
    .map((item) => Object.assign({}, item, { row: Number(item.row || 0) }))
    .filter((item) => item.row > CONFIG.headerRow && item.row <= lastRow);
  if (!validItems.length) {
    return { ok: true, updated, skipped: results || [] };
  }

  const startRow = Math.min.apply(null, validItems.map((item) => item.row));
  const endRow = Math.max.apply(null, validItems.map((item) => item.row));
  const rowCount = endRow - startRow + 1;
  const completionRange = sheet.getRange(startRow, col.completionAt, rowCount, 1);
  const statusRange = sheet.getRange(startRow, col.status, rowCount, 1);
  const completionValues = completionRange.getValues();
  const statusValues = statusRange.getValues();
  let completionChanged = false;
  let statusChanged = false;
  const validByRow = {};
  validItems.forEach((item) => {
    validByRow[item.row] = item;
  });
  (results || []).forEach((item) => {
    const row = Number(item.row || 0);
    if (!validByRow[row]) {
      skipped.push(item);
    }
  });

  validItems.forEach((item) => {
    const row = item.row;
    const completionAt = String(item.completionAt || '').trim();
    const status = String(item.status || '').trim();

    const index = row - startRow;
    const currentCompletionAtValue = completionValues[index][0];
    if (isCompletionDateTime(currentCompletionAtValue)) {
      skipped.push(Object.assign({}, item, { reason: 'already_completed' }));
      return;
    }

    if (completionAt && isCompletionDateTime(completionAt)) {
      completionValues[index][0] = completionAt;
      completionChanged = true;
    }
    if (status) {
      statusValues[index][0] = status;
      statusChanged = true;
    }

    updated.push({ row, completionAt, status });
  });

  if (completionChanged) {
    completionRange.setValues(completionValues);
  }
  if (statusChanged) {
    statusRange.setValues(statusValues);
  }

  return { ok: true, updated, skipped };
}

function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
