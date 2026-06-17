const CONFIG = {
  sheetName: 'JN/ML大额催促登记表',
  token: 'CHANGE_ME_TO_A_RANDOM_SECRET',
  headerRow: 1,
  columns: {
    orderNo: '提款订单',
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
  const seen = {};

  values.forEach((row, index) => {
    const rowNumber = CONFIG.headerRow + 1 + index;
    const orderNo = String(row[col.orderNo - 1] || '').trim();
    const completionAt = String(row[col.completionAt - 1] || '').trim();
    const status = String(row[col.status - 1] || '').trim();

    if (!orderNo || completionAt || status || seen[orderNo]) {
      return;
    }

    seen[orderNo] = true;
    rows.push({ row: rowNumber, orderNo });
  });

  return rows;
}

function updateRows(results) {
  const { sheet, col } = getSheetAndColumns();
  const updated = [];
  const skipped = [];

  results.forEach((item) => {
    const row = Number(item.row || 0);
    const completionAt = String(item.completionAt || '').trim();
    const status = String(item.status || '').trim();

    if (!row || row <= CONFIG.headerRow) {
      skipped.push(item);
      return;
    }

    if (completionAt) {
      sheet.getRange(row, col.completionAt).setValue(completionAt);
    }
    if (status) {
      sheet.getRange(row, col.status).setValue(status);
    }

    updated.push({ row, completionAt, status });
  });

  return { ok: true, updated, skipped };
}

function json(obj) {
  return ContentService
    .createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
