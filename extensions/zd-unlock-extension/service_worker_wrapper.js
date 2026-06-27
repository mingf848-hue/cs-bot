// Loads the original service worker, then patches settlement-urge notice selection.
// This keeps the large original file untouched while fixing unresolved tickets that
// were incorrectly matched to market-error / invalid-ticket announcements.
importScripts('service_worker.js');

(function patchUrgeSettlementNoticeSelection() {
  function isSettlementStillUnsettled(order = {}, detail = {}) {
    return Number(order.orderStatus) === 0
      && detailIsUnsettled(detail, order)
      && !detailIsCanceled(detail)
      && !detailIsBetFailed(detail);
  }

  function noticeIsUnsettledReason(item = {}) {
    const text = normalizeText(noticeText(item));
    return /不能按时结算|不能按時結算|赛果不明确|賽果不明確|赛果将进一步核实|賽果將進一步核實|核实完毕后会进行结算|核實完畢後會進行結算|比赛中断|比賽中斷|赛事中断|賽事中斷|match interruption|match interrupted|interrupted|delaysettlement|delay settlement|noclearresult|no clear result/.test(text);
  }

  function noticeIsInterruptedReason(item = {}) {
    const text = normalizeText(noticeText(item));
    return /比赛中断|比賽中斷|赛事中断|賽事中斷|match interruption|match interrupted|interrupted/.test(text);
  }

  function noticeIsCancelOrInvalidReason(item = {}) {
    const text = normalizeText(noticeText(item));
    return /盘口错误|盤口錯誤|赔率错误|賠率錯誤|无效|無效|取消|失败|失敗|退回|本金|受影响注单|受影響注單|一律视为无效|一律視為無效|相关盘口赔率以|相關盤口賠率以|marketerror|market error|odds error|invalid/.test(text);
  }

  function normalizeSettlementNoticeReply(text) {
    const raw = String(text || '').trim();
    if (/赛事状态不明确|賽事狀態不明確/.test(raw)) return raw;
    const oldText = '因赛果不明确，赛果将进一步核实，确认后再进行结算，造成不便之处，敬请见谅！';
    const replacement = '因赛果不明确，注单暂时无法结算，需待定24小时，如24小时无法确定，所有受影响的注单一律视为无效，连串注单该场赛事相关盘口赔率以（1）计算，谢谢！';
    const interruptedReplacement = '因赛事中断，注单暂时无法结算，赛果将进一步核实，需待定36小时，如36小时仍未恢复比赛或未指定新的开赛时间，除了已有明确赛果的注单以外，其余受影响的注单一律视为无效，串关中该赛事赔率以（1）计算，谢谢！';
    return raw
      .replace(/因(?:比赛|比賽|赛事|賽事)中[断斷]，?赛果将进一步核实，?确认后再进行结算，?造成不便之处，?敬请见谅[！!]?/g, interruptedReplacement)
      .split(oldText).join(replacement);
  }

  const originalScoreSettlementNotice = scoreSettlementNotice;
  scoreSettlementNotice = function patchedScoreSettlementNotice(item = {}, order = {}, detail = {}) {
    let score = originalScoreSettlementNotice(item, order, detail);
    const stillUnsettled = isSettlementStillUnsettled(order, detail);
    const unsettledNotice = noticeIsUnsettledReason(item);
    const interruptedNotice = noticeIsInterruptedReason(item);
    const cancelOrInvalidNotice = noticeIsCancelOrInvalidReason(item);

    if (unsettledNotice) score += 180;
    if (interruptedNotice) score += 35;
    if (stillUnsettled && cancelOrInvalidNotice && !unsettledNotice) score -= 500;
    return score;
  };

  function settlementNoticeCandidates(noticeData, order, detail) {
    const stillUnsettled = isSettlementStillUnsettled(order, detail);
    return merchantList(noticeData)
      .map((noticeItem) => ({
        item: noticeItem,
        score: scoreSettlementNotice(noticeItem, order, detail)
      }))
      .filter((entry) => {
        if (!entry || !entry.item) return false;
        if (noticeMarketStageMismatch(entry.item, detail, order)) return false;
        if (stillUnsettled && noticeIsCancelOrInvalidReason(entry.item) && !noticeIsUnsettledReason(entry.item)) return false;
        return Number(entry.score) >= 80;
      })
      .sort((a, b) => b.score - a.score);
  }

  runUrgeSettlementCommand = async function patchedRunUrgeSettlementCommand(config, cmd, orderNo) {
    if (!config.merchantTicketListUrl) throw new Error('场馆注单列表接口未配置');
    if (!config.merchantNoticeUrl) throw new Error('场馆公告接口未配置');
    if (!config.merchantSettlementListUrl) throw new Error('场馆结算状态接口未配置');
    if (!config.merchantSettlementApplyUrl) throw new Error('场馆催促结算申请接口未配置');

    const headers = merchantHeaders(config, cmd);
    const venueLabel = config.pageAuthLabel || merchantAuthLabel(config);
    await setStatus({ state: 'running', message: `催结算查询注单 ${orderNo} (${venueLabel})` });
    const ticketUrl = merchantUrl(config.merchantTicketListUrl);
    const ticket = await postJson(ticketUrl, headers, merchantTicketBody(cmd, orderNo));
    if (!merchantApiOk(ticket)) {
      const err = merchantHttpError('查询注单失败', ticketUrl, ticket);
      if (merchantAuthFailed(ticket)) {
        err.code = 'merchant_auth_failed';
        err.venueLabel = venueLabel;
      }
      throw err;
    }
    const order = merchantList(ticket.data)[0];
    if (!order) {
      const err = new Error(`未找到注单：${orderNo}`);
      err.code = 'merchant_order_not_found';
      err.venueLabel = venueLabel;
      throw err;
    }

    const allDetails = orderDetails(order);
    const pendingDetails = unresolvedOrderDetails(order);
    const detail = pendingDetails[0] || firstOrderDetail(order);
    const rollbackOrder = orderHasSettlementRollback(order);
    const statusLabel = orderStatusLabel(order.orderStatus);
    const stillUnsettled = isSettlementStillUnsettled(order, detail);

    // In urge-settlement flow, unresolved tickets must stay in settlement-reason logic.
    // Only genuinely canceled / failed tickets should use cancel/failure reason replies.
    if (!stillUnsettled && ticketReasonRequested(cmd)) {
      const reasonDetail = ticketReasonDetail(order, detail, ticketReasonMode(cmd));
      const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, reasonDetail);
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }
    if (!stillUnsettled && orderIsBetFailed(order, detail)) {
      const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail);
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }
    if (!stillUnsettled && orderIsCanceled(order, detail)) {
      const { replyText, msg } = await ticketReasonReplyWithNotice(config, cmd, headers, orderNo, order, detail);
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }
    if (!rollbackOrder && Number(order.orderStatus) !== 0) {
      const replyText = String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。');
      const msg = `催结算跳过：${orderNo} ${statusLabel}`;
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }

    if (!rollbackOrder && allDetails.length && !pendingDetails.length) {
      const replyText = String(cmd.settled_reply || '注单已结算，请刷新注单页面查看。');
      const msg = `催结算跳过：${orderNo} 串关明细均已结算`;
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }

    const detailsToCheck = pendingDetails.length ? pendingDetails : [detail].filter(Boolean);
    const futureDetail = detailsToCheck.find((item) => {
      const beginMs = orderBeginTimeMillis(order, item);
      return beginMs && Date.now() < beginMs;
    });
    if (futureDetail) {
      const replyText = String(cmd.not_started_reply || '当前注单暂未开赛，请耐心等待。');
      const beginText = String(futureDetail.beginTimeStr || futureDetail.beginTime || order.beginTimeStr || order.beginTime || '');
      const msg = `催结算跳过：${orderNo} 未到开赛时间${beginText ? ` ${beginText}` : ''}`;
      await replyOrigin(config, cmd, msg, replyText, ticket.text);
      return;
    }

    const matchIds = detailsMatchIds(order, detailsToCheck);
    if (!matchIds.length) {
      throw new Error(`注单未找到未结算赛事ID：${orderNo}`);
    }

    for (const item of detailsToCheck) {
      const matchId = detailMatchId(order, item);
      if (!matchId) continue;
      await setStatus({ state: 'running', message: `查询赛事公告 ${matchId}` });
      const noticeUrl = merchantUrl(config.merchantNoticeUrl);
      const notice = await postForm(noticeUrl, headers, {
        mid: matchId,
        status: 1,
        pgNum: 1,
        pgSize: 20
      });
      if (!merchantApiOk(notice)) {
        throw merchantHttpError('查询公告失败', noticeUrl, notice);
      }
      const notices = settlementNoticeCandidates(notice.data, order, item);
      if (notices.length) {
        const selected = notices[0] || {};
        const selectedNotice = selected.item || {};
        const noticeText = await noticeReplyText(config, headers, selectedNotice);
        const marketLabel = detailMarketCategory(item)?.label || '';
        const replyText = withSettlementRollbackPrefix(normalizeSettlementNoticeReply(noticeText) || '赛果核实中，请耐心等待。', order, item);
        await replyOrigin(config, cmd, `赛事 ${matchId} 已有未结算公告${marketLabel ? `（${marketLabel}）` : ''}`, replyText, notice.text);
        return;
      }
    }

    await setStatus({ state: 'running', message: `查询结算状态 ${orderNo}` });
    const settlementUrl = merchantUrl(config.merchantSettlementListUrl);
    const settlement = await postJson(settlementUrl, headers, merchantSettlementBody(cmd, orderNo));
    if (!merchantApiOk(settlement)) {
      if (merchantNoSettlementData(settlement)) {
        console.info('[CS Bot ZD Unlock] no settlement data, continue TG urge', orderNo, settlement.data && settlement.data.msg);
      } else {
        throw merchantHttpError('查询结算状态失败', settlementUrl, settlement);
      }
    }
    const settlementTotal = Number((((settlement.data || {}).data || {}).total) || 0);
    if (settlementTotal > 0 || merchantList(settlement.data).length > 0) {
      const settlementOrder = merchantList(settlement.data)[0] || {};
      const settlementDetails = unresolvedOrderDetails(settlementOrder).length
        ? unresolvedOrderDetails(settlementOrder)
        : detailsToCheck;
      const applyItems = settlementDetails.length ? settlementDetails : detailsToCheck;
      const seenApply = new Set();
      let applied = 0;
      for (const item of applyItems) {
        const body = merchantSettlementApplyBody(settlementOrder.orderNo ? settlementOrder : order, item, orderNo);
        const key = `${body.orderNo}:${body.matchId}:${body.betNo}`;
        if (!body.orderNo || !body.matchId || !body.betNo || !body.userId || seenApply.has(key)) continue;
        seenApply.add(key);
        await setStatus({ state: 'running', message: `递交催促结算申请 ${body.orderNo} ${body.matchId}` });
        const applyUrl = merchantUrl(config.merchantSettlementApplyUrl);
        const apply = await postJson(applyUrl, headers, body);
        if (!merchantApiOk(apply)) {
          throw merchantHttpError('递交催促结算申请失败', applyUrl, apply);
        }
        applied += 1;
      }
      if (!applied) {
        throw new Error(`催促结算申请缺少必要字段：${orderNo}`);
      }
      const msg = `催结算已提交申请：${orderNo}`;
      const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, applyItems[0] || detail);
      await replyOrigin(config, cmd, msg, replyText, settlement.text);
      return;
    }

    const matchId = matchIds.join('，');
    const urgeMatchSplit = await splitTelegramUrgeMatchIds(matchIds);
    if (!urgeMatchSplit.allowed.length) {
      const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, detailsToCheck[0] || detail);
      const msg = `催结算跳过TG重复无公告赛事：${orderNo} 赛事ID ${matchId}`;
      await replyOrigin(config, cmd, msg, replyText, settlement.text);
      return;
    }
    const limitedMatchId = urgeMatchSplit.allowed.join('，');
    const matchManageId = [...new Set(detailsToCheck.map((item) => String(item.matchManageId || '').trim()).filter(Boolean))].join('，');
    const matchInfo = detailsText(order, detailsToCheck);
    const context = {
      order_no: orderNo,
      order_id: orderNo,
      orderNo,
      orderId: orderNo,
      match_id: limitedMatchId,
      matchId: limitedMatchId,
      match_manage_id: matchManageId,
      matchManageId,
      sport: detail.sportName || '',
      sport_name: detail.sportName || '',
      match_info: matchInfo,
      matchInfo,
      begin_time: detail.beginTimeStr || '',
      beginTime: detail.beginTimeStr || '',
      user_name: order.userName || '',
      userName: order.userName || ''
    };
    const batchResult = await sendTelegramFromCommandBatched(config, cmd, context, urgeMatchSplit.allowed);
    const text = batchResult.text || settlementTemplate(cmd.telegram_template, context);
    const batchOrderNos = Array.isArray(batchResult.orderNos) && batchResult.orderNos.length ? batchResult.orderNos : [orderNo];
    const msgTarget = batchOrderNos.length > 1 ? batchOrderNos.join('、') : orderNo;
    const msg = urgeMatchSplit.blocked.length
      ? `催结算已提交：${msgTarget} 赛事ID ${limitedMatchId}（跳过重复：${urgeMatchSplit.blocked.join('，')}）`
      : `催结算已提交：${msgTarget} 赛事ID ${limitedMatchId}`;
    const replyText = withSettlementRollbackPrefix(String(cmd.urge_sent_reply || '赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。'), order, detailsToCheck[0] || detail);
    await setStatus({ state: 'success', message: msg, detail: text.slice(0, 300) });
    await ack(config, cmd, 'reply_origin', msg, { reply_text: replyText, stop_actions: true });
  };
})();
