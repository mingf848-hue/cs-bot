import asyncio
import time
import logging
import re
import json
import queue
import os
import requests
from datetime import datetime, timedelta, timezone
from flask import request, render_template_string, Response, stream_with_context

# 定义北京时区
BJ_TZ = timezone(timedelta(hours=8))

# ==========================================
# 配置：群组分类定义
# ==========================================
PROMO_GROUPS = {
    -1001885279888, -1001800838000, -1001703213989, -1001972746703, -1001871198775,
}
ASSIST_GROUPS = {
    -1002169616907, -1002053064967, -1002728905038, -1002154594658, -1002004030172,
    -1002174533164, -1001978088089, -1001931146238, -1001911814916, -1001571955528,
    -1001587586041, -1002807120955,
}
ALL_TARGET_GROUPS = list(PROMO_GROUPS | ASSIST_GROUPS)

logger = logging.getLogger("BotLogger")

# ==========================================
# 兜底关键词 (如果连不上表格时使用)
# ==========================================
FALLBACK_KEYWORDS = """稍等-an
请稍等elk
稍等～ys""" # 你可以在这里保留一些基础词

def normalize_text(text):
    if not text: return ""
    return text.lower().replace("～", "~").strip()

# ==========================================
# 核心功能模块 (GAS 通信)
# ==========================================

def get_gas_url():
    return os.environ.get("GOOGLE_SCRIPT_URL")

# 1. 从 GAS 获取关键词 (New!)
def fetch_keywords_from_gas():
    url = get_gas_url()
    if not url: return None, "未配置 GOOGLE_SCRIPT_URL"
    
    try:
        # 发送 action=get_keywords
        resp = requests.post(url, json={"action": "get_keywords"}, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                kw_list = data.get("keywords", [])
                if kw_list:
                    return kw_list, "获取成功"
                else:
                    return None, "表格返回了空列表"
            else:
                return None, f"GAS错误: {data.get('msg')}"
        return None, f"HTTP {resp.status_code}"
    except Exception as e:
        logger.error(f"Fetch KW Error: {e}")
        return None, str(e)

# 2. 推送数据到 GAS
def sync_data_via_script(day, stats_data):
    url = get_gas_url()
    if not url: return False, "未配置 GOOGLE_SCRIPT_URL"
    try:
        payload = {"day": day, "stats": stats_data}
        response = requests.post(url, json=payload, allow_redirects=True, timeout=20)
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("success"): return True, res_json.get("msg")
            else: return False, "GAS返回错误: " + res_json.get("msg")
        return False, f"HTTP 请求失败: {response.status_code}"
    except Exception as e:
        return False, str(e)

# 3. 静默扫描函数
async def quiet_scan(client, start_time, end_time, keywords):
    stats = {kw: {'promo': 0, 'assist': 0} for kw in keywords}
    norm_map = [(kw, normalize_text(kw)) for kw in keywords]
    utc_start = start_time.astimezone(timezone.utc)
    utc_end = end_time.astimezone(timezone.utc)
    
    for chat_id in ALL_TARGET_GROUPS:
        category = 'promo' if chat_id in PROMO_GROUPS else 'assist' if chat_id in ASSIST_GROUPS else 'other'
        if category == 'other': continue
        try:
            async for message in client.iter_messages(chat_id, offset_date=utc_end, reverse=False):
                if message.date < utc_start: break
                if not message.text: continue
                content = normalize_text(message.text)
                for orig, norm in norm_map:
                    if norm in content:
                        stats[orig][category] += 1
                        break
        except Exception as e:
            logger.error(f"AutoScan Error Group {chat_id}: {e}")
    return stats

# 4. 每日定时调度器 (自动获取关键词版)
async def daily_scheduler(client):
    logger.info("⏰ 自动统计任务调度器已启动 (目标: 每天北京时间 00:05)")
    while True:
        try:
            now = datetime.now(BJ_TZ)
            target_today = now.replace(hour=6, minute=10, second=0, microsecond=0)
            target = target_today + timedelta(days=1) if now > target_today else target_today
            
            wait_seconds = (target - now).total_seconds()
            logger.info(f"⏳ 下次执行: {target.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(wait_seconds)
            
            # === 醒来后 ===
            logger.info("🤖 开始执行自动统计...")
            
            # A. 尝试从表格获取最新关键词
            logger.info("📡 正在从表格拉取最新关键词...")
            online_kws, msg = fetch_keywords_from_gas()
            
            final_keywords = []
            if online_kws:
                logger.info(f"✅ 成功获取 {len(online_kws)} 个关键词")
                final_keywords = online_kws
            else:
                logger.error(f"❌ 获取关键词失败 ({msg})，使用兜底列表")
                final_keywords = [k.strip() for k in FALLBACK_KEYWORDS.splitlines() if k.strip()]
            
            if not final_keywords:
                logger.error("❌ 关键词列表为空，跳过本次统计")
                continue

            # B. 执行扫描
            yesterday = datetime.now(BJ_TZ) - timedelta(days=1)
            day_str = str(yesterday.day)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            stats = await quiet_scan(client, start, end, final_keywords)
            
            # C. 同步结果
            logger.info(f"📊 同步 {yesterday.month}月{day_str}日 数据...")
            success, sync_msg = sync_data_via_script(day_str, stats)
            if success: logger.info(f"✅ 自动同步成功: {sync_msg}")
            else: logger.error(f"❌ 自动同步失败: {sync_msg}")
            
            await asyncio.sleep(60)
        except asyncio.CancelledError: break
        except Exception as e:
            logger.error(f"❌ 调度器错误: {e}")
            await asyncio.sleep(60)

# ==========================================
# 前端与路由
# ==========================================

STATS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>工作量统计 (云端同步版)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, sans-serif; background: #f0f2f5; padding: 20px; max-width: 900px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 25px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 15px; }
        .submit-btn { background: #0088cc; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; }
        .sync-btn { background: #0f9d58; margin-bottom: 15px; }
        textarea { width: 100%; height: 200px; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-family: monospace; }
        input { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; margin-bottom: 10px; }
        #progress-bar { height: 10px; background: #4caf50; width: 0%; transition: width 0.3s; margin-top:10px; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; display:none; }
        td, th { border: 1px solid #ddd; padding: 8px; }
        .col-promo { background: #e3f2fd; text-align: center; }
        .col-assist { background: #fff3e0; text-align: center; }
        .badge { background: #666; color: white; padding: 2px 6px; border-radius: 4px; font-size: 12px; }
        .status-tag { font-size: 12px; margin-bottom: 5px; display: inline-block; padding: 2px 8px; border-radius: 4px; }
        .status-ok { background: #e8f5e9; color: #2e7d32; }
        .status-err { background: #ffebee; color: #c62828; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📊 工作量统计 <span class="badge">自动运行中</span></h1>
        
        <div>
            <label>📅 手动补单 (输入日期号):</label>
            <input type="number" id="dayInput" placeholder="例如: 6">
        </div>
        
        <div>
            <label>📝 关键词列表:</label>
            {% if fetch_status %}
                <span class="status-tag status-ok">✅ 已从表格自动加载</span>
            {% else %}
                <span class="status-tag status-err">⚠️ 加载失败，使用本地缓存 ({{ fetch_msg }})</span>
            {% endif %}
            <textarea id="keywordsInput">{{ default_keywords }}</textarea>
        </div>
        
        <button onclick="startStats()" id="btnSubmit" class="submit-btn" style="margin-top:10px">🚀 手动开始统计</button>
        
        <div id="progress-container" style="display:none; margin-top:10px;">
            <div style="background:#eee; height:10px; border-radius:5px; overflow:hidden;"><div id="progress-bar"></div></div>
            <div id="progress-text" style="text-align:center; font-size:12px; color:#666; margin-top:5px;"></div>
        </div>
        
        <div id="result-area" style="display:none; margin-top:20px;">
            <button onclick="syncToCloud()" id="btnSync" class="submit-btn sync-btn">☁️ 一键同步到表格</button>
            <div id="sync-msg" style="text-align:center; font-weight:bold; margin-bottom:10px;"></div>
            <table id="result-table">
                <thead><tr><th>关键词</th><th class="col-promo">推广</th><th class="col-assist">协助</th></tr></thead>
                <tbody id="result-body"></tbody>
            </table>
        </div>
    </div>
    <script>
        let currentStats = null, currentDay = null;
        async function startStats() {
            const day = document.getElementById('dayInput').value;
            const kws = document.getElementById('keywordsInput').value;
            if(!day || !kws) return alert("请填写日期和关键词");
            currentDay = day;
            
            document.getElementById('btnSubmit').disabled = true;
            document.getElementById('progress-container').style.display = 'block';
            document.getElementById('result-area').style.display = 'none';
            document.getElementById('result-body').innerHTML = '';
            
            const params = new URLSearchParams({day: day, keywords: kws});
            const res = await fetch('/api/work_stats_stream?' + params);
            const reader = res.body.getReader();
            const decoder = new TextDecoder();
            
            while(true) {
                const {value, done} = await reader.read();
                if(done) break;
                const chunks = decoder.decode(value, {stream:true}).split('\\n');
                for(let chunk of chunks) {
                    if(!chunk.trim()) continue;
                    try {
                        const data = JSON.parse(chunk);
                        if(data.type === 'progress') {
                            document.getElementById('progress-bar').style.width = data.percent + '%';
                            document.getElementById('progress-text').innerText = data.msg;
                        } else if(data.type === 'done') {
                            currentStats = data.results;
                            renderTable(data.results, kws);
                            document.getElementById('progress-bar').style.width = '100%';
                            document.getElementById('progress-text').innerText = '完成';
                            document.getElementById('result-area').style.display = 'block';
                        }
                    } catch(e){}
                }
            }
            document.getElementById('btnSubmit').disabled = false;
        }
        function renderTable(stats, kws) {
            const tbody = document.getElementById('result-body');
            kws.split('\\n').forEach(k => {
                k = k.trim(); if(!k) return;
                const row = document.createElement('tr');
                const s = stats[k] || {promo:0, assist:0};
                row.innerHTML = `<td>${k}</td><td class="col-promo">${s.promo}</td><td class="col-assist">${s.assist}</td>`;
                tbody.appendChild(row);
            });
            document.getElementById('result-table').style.display = 'table';
        }
        async function syncToCloud() {
            const btn = document.getElementById('btnSync');
            const msg = document.getElementById('sync-msg');
            btn.disabled = true; btn.innerText = "同步中..."; msg.innerText = "";
            try {
                const res = await fetch('/api/sync_to_sheet', {
                    method:'POST', headers:{'Content-Type':'application/json'},
                    body: JSON.stringify({day: currentDay, stats: currentStats})
                });
                const json = await res.json();
                if(json.success) { msg.innerText = "✅ " + json.msg; msg.style.color = "green"; btn.innerText = "同步成功"; }
                else { msg.innerText = "❌ " + json.msg; msg.style.color = "red"; btn.innerText = "重试"; btn.disabled = false; }
            } catch(e) { msg.innerText = "❌ 网络错误"; btn.disabled = false; }
        }
    </script>
</body>
</html>
"""

# 手动扫描逻辑
async def perform_scan(client, start_time, end_time, keywords, result_queue):
    try:
        stats = {kw: {'promo': 0, 'assist': 0} for kw in keywords}
        norm_map = [(kw, normalize_text(kw)) for kw in keywords]
        utc_start = start_time.astimezone(timezone.utc)
        utc_end = end_time.astimezone(timezone.utc)
        total = len(ALL_TARGET_GROUPS)
        
        for idx, chat_id in enumerate(ALL_TARGET_GROUPS):
            percent = int((idx / total) * 100)
            result_queue.put(json.dumps({"type": "progress", "percent": percent, "msg": f"扫描中: {chat_id}"}))
            category = 'promo' if chat_id in PROMO_GROUPS else 'assist' if chat_id in ASSIST_GROUPS else 'other'
            if category == 'other': continue
            try:
                async for message in client.iter_messages(chat_id, offset_date=utc_end, reverse=False):
                    if message.date < utc_start: break
                    if not message.text: continue
                    content = normalize_text(message.text)
                    for orig, norm in norm_map:
                        if norm in content:
                            stats[orig][category] += 1
                            break 
            except: pass
        result_queue.put(json.dumps({"type": "done", "results": stats}))
    except Exception as e:
        result_queue.put(json.dumps({"type": "error", "msg": str(e)}))
    finally:
        result_queue.put(None)

def init_stats_blueprint(app, client, bot_loop, _unused_args=None):
    if bot_loop and client:
        bot_loop.create_task(daily_scheduler(client))

    @app.route('/tool/work_stats')
    def work_stats_view():
        # 打开网页时，尝试去表格拉取最新词
        online_kws, msg = fetch_keywords_from_gas()
        if online_kws:
            kw_str = "\n".join(online_kws)
            status = True
        else:
            kw_str = FALLBACK_KEYWORDS
            status = False
            
        return render_template_string(STATS_HTML, default_keywords=kw_str, fetch_status=status, fetch_msg=msg)

    @app.route('/api/work_stats_stream')
    def work_stats_stream():
        day = request.args.get('day')
        kws = request.args.get('keywords', '')
        if not day or not kws: return "Err", 400
        def generate():
            try:
                now = datetime.now(BJ_TZ)
                d = int(day)
                start = now.replace(day=d, hour=0, minute=0, second=0, microsecond=0)
                end = now.replace(day=d, hour=23, minute=59, second=59, microsecond=999999)
                kw_list = [x.strip() for x in kws.splitlines() if x.strip()]
                q = queue.Queue()
                asyncio.run_coroutine_threadsafe(perform_scan(client, start, end, kw_list, q), bot_loop)
                while True:
                    data = q.get()
                    if data is None: break
                    yield data + "\n"
            except Exception as e: yield json.dumps({"type":"error", "msg":str(e)})+"\n"
        return Response(stream_with_context(generate()), mimetype='text/plain')

    @app.route('/api/sync_to_sheet', methods=['POST'])
    def sync_to_sheet():
        data = request.json
        success, msg = sync_data_via_script(data.get('day'), data.get('stats', {}))
        return json.dumps({"success": success, "msg": msg}), 200, {'Content-Type': 'application/json'}
