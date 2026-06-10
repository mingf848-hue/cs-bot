function doPost(e) {
  try {
    var params = JSON.parse(e.postData.contents);

    var sheetInfo = getSmartSheetInfo(params.day, params.month, params.year);
    var sheet = sheetInfo.sheet;

    if (params.action === "get_keywords") {
      if (!sheet) {
        sheet = SpreadsheetApp.getActiveSpreadsheet().getActiveSheet();
      }
      return handleGetKeywords(sheet);
    }

    if (!sheet) {
      return responseJSON({
        success: false,
        msg: "找不到工作表: [" + sheetInfo.name + "]。请确认表格已创建且名字正确。"
      });
    }

    if (params.action === "sync_manual_workload") {
      return handleWriteManualWorkload(sheet, params, sheetInfo.year, sheetInfo.month);
    }

    if (params.day && params.stats) {
      return handleWriteStats(sheet, params.day, params.stats, sheetInfo.year, sheetInfo.month);
    }

    return responseJSON({ success: false, msg: "未知请求类型" });
  } catch (err) {
    return responseJSON({ success: false, msg: "脚本报错: " + err.toString() });
  }
}

function getSmartSheetInfo(dayInput, monthInput, yearInput) {
  var now = new Date();
  var fullYear = yearInput ? parseInt(yearInput, 10) : now.getFullYear();
  var month = monthInput ? parseInt(monthInput, 10) : (now.getMonth() + 1);

  if (!monthInput && dayInput) {
    var currentDay = now.getDate();
    var inputDay = parseInt(dayInput, 10);
    if (currentDay <= 5 && inputDay >= 20) {
      now.setMonth(now.getMonth() - 1);
      fullYear = now.getFullYear();
      month = now.getMonth() + 1;
    }
  }

  var yearShort = fullYear.toString().slice(-2);
  var sheetName = "工作量统计（" + yearShort + "年" + month + "月份）";
  var sheet = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(sheetName);

  return {
    sheet: sheet,
    name: sheetName,
    year: fullYear,
    month: month
  };
}

function handleGetKeywords(sheet) {
  var lastRow = sheet.getLastRow();
  if (lastRow < 3) return responseJSON({ success: true, keywords: [] });

  var range = sheet.getRange(3, 4, lastRow - 2, 1);
  var values = range.getValues();
  var keywords = [];

  for (var i = 0; i < values.length; i++) {
    var val = values[i][0].toString().trim();
    if (val === "姓名") break;
    if (val.indexOf("处理量") > -1 || val.indexOf("总处理") > -1) break;
    if (val === "稍等快捷") continue;

    if (val && keywords.indexOf(val) === -1) {
      keywords.push(val);
    }
  }
  return responseJSON({ success: true, keywords: keywords });
}

function handleWriteStats(sheet, day, stats, targetYear, targetMonth) {
  var colIndex = findDateColumn(sheet, day, targetYear, targetMonth);

  if (colIndex === 0) {
    return responseJSON({
      success: false,
      msg: "在表 [" + sheet.getName() + "] 中未找到日期 [" + targetMonth + "月" + parseInt(day, 10) + "日]"
    });
  }

  var keywordValues = sheet.getRange("D:D").getValues();
  var updates = 0;

  for (var key in stats) {
    var promoCount = stats[key].promo || 0;
    var assistCount = stats[key].assist || 0;
    var foundRows = [];
    for (var i = 0; i < keywordValues.length; i++) {
      if (keywordValues[i][0].toString().trim() === key.trim()) foundRows.push(i + 1);
    }
    if (foundRows.length >= 1) {
      var promoCell = sheet.getRange(foundRows[0], colIndex);
      promoCell.setValue(promoCount);
      fixCellStyle(promoCell);
      updates++;
    }
    if (foundRows.length >= 2) {
      var assistCell = sheet.getRange(foundRows[1], colIndex);
      assistCell.setValue(assistCount);
      fixCellStyle(assistCell);
      updates++;
    }
  }

  return responseJSON({ success: true, msg: "成功更新 " + updates + " 条数据" });
}

function handleWriteManualWorkload(sheet, params, targetYear, targetMonth) {
  var worker = (params.worker || params.name || "ARATAKITO").toString().trim();
  var occurrence = parseInt(params.row_occurrence || 3, 10);
  var total = params.total;
  if (total === undefined || total === null || total === "") total = params.quantity;

  if (!worker) {
    return responseJSON({ success: false, msg: "缺少 worker/name" });
  }
  if (!occurrence || occurrence < 1) {
    occurrence = 3;
  }
  if (total === undefined || total === null || total === "") {
    return responseJSON({ success: false, msg: "缺少 total/quantity" });
  }

  var day = params.day;
  if (!day && params.date) {
    var dateParts = params.date.toString().split("-");
    day = dateParts.length >= 3 ? dateParts[2] : "";
  }
  var colIndex = findDateColumn(sheet, day, targetYear, targetMonth);
  if (colIndex === 0) {
    return responseJSON({
      success: false,
      msg: "在表 [" + sheet.getName() + "] 中未找到日期 [" + targetMonth + "月" + parseInt(day, 10) + "日]"
    });
  }

  var lastRow = sheet.getLastRow();
  var nameValues = sheet.getRange(1, 4, lastRow, 1).getValues();
  var foundRows = [];
  var targetName = worker.toUpperCase();

  for (var i = 0; i < nameValues.length; i++) {
    var name = nameValues[i][0].toString().trim();
    if (name.toUpperCase() === targetName) {
      foundRows.push(i + 1);
    }
  }

  if (foundRows.length < occurrence) {
    return responseJSON({
      success: false,
      msg: "在 D 列只找到 " + foundRows.length + " 个 [" + worker + "]，不足第 " + occurrence + " 个"
    });
  }

  var rowIndex = foundRows[occurrence - 1];
  var cell = sheet.getRange(rowIndex, colIndex);
  cell.setValue(total);
  fixCellStyle(cell);

  return responseJSON({
    success: true,
    msg: worker + " 工作量已写入 " + cell.getA1Notation() + " = " + total
  });
}

function findDateColumn(sheet, day, targetYear, targetMonth) {
  var dateRowIndex = 2;
  var range = sheet.getRange(dateRowIndex, 1, 1, sheet.getLastColumn());
  var displayValues = range.getDisplayValues()[0];
  var rawValues = range.getValues()[0];
  var targetDay = parseInt(day, 10);
  if (!targetDay) return 0;

  var month = parseInt(targetMonth, 10);
  var year = parseInt(targetYear, 10);
  var paddedMonth = month < 10 ? "0" + month : "" + month;
  var paddedDay = targetDay < 10 ? "0" + targetDay : "" + targetDay;
  var targets = [
    month + "月" + targetDay + "日",
    year + "-" + month + "-" + targetDay,
    year + "-" + paddedMonth + "-" + paddedDay,
    year + "/" + month + "/" + targetDay,
    year + "/" + paddedMonth + "/" + paddedDay,
    month + "/" + targetDay,
    paddedMonth + "/" + paddedDay,
    month + "." + targetDay,
    paddedMonth + "." + paddedDay
  ];

  for (var i = 0; i < displayValues.length; i++) {
    var raw = rawValues[i];
    if (raw instanceof Date) {
      if (raw.getFullYear() === year && raw.getMonth() + 1 === month && raw.getDate() === targetDay) {
        return i + 1;
      }
    }
    var display = displayValues[i].toString().trim();
    if (targets.indexOf(display) > -1) {
      return i + 1;
    }
  }
  return 0;
}

function fixCellStyle(cell) {
  cell.setHorizontalAlignment("center");
  cell.setVerticalAlignment("middle");
  cell.setFontSize(9);
}

function responseJSON(data) {
  return ContentService.createTextOutput(JSON.stringify(data)).setMimeType(ContentService.MimeType.JSON);
}
