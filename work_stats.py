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
# 兜底关键词 (表格连不上时使用)
# ==========================================
FALLBACK_KEYWORDS = """稍等-an
请稍等elk
稍等～ys""" 

def normalize_text(text):
    if not text: return ""
    return text.lower().replace("～", "~").strip()

# ==========================================
# 核心功能模块 (GAS 通信)
# ==========================================
def get_gas_url():
    return os.environ.get("GOOGLE_SCRIPT_URL")

# 1. 从 GAS 获取关键词
def fetch_keywords_from_gas():
    url = get_gas_url()
    if not url: return None, "未配置 GOOGLE_SCRIPT_URL"
    
    try:
        resp = requests.post(url, json={"action": "get_keywords"}, timeout=10, allow_redirects=True)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success"):
                kw_list = data.get("keywords", [])
                if kw_list: return kw_list, "获取成功"
                else: return None, "表格返回空列表"
            else: return None, f"GAS错误: {data.get('msg')}"
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

# 3. 静默扫描函数 (后台自动任务用)
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

# 4. 每日定时调度器
async def daily_scheduler(client):
    logger.info("⏰ 自动统计任务调度器已启动 (目标: 每天北京时间 04:10)")
    while True:
        try:
            now = datetime.now(BJ_TZ)
            target_today = now.replace(hour=4, minute=10, second=0, microsecond=0)
            target = target_today + timedelta(days=1) if now > target_today else target_today
            
            wait_seconds = (target - now).total_seconds()
            logger.info(f"⏳ 下次执行: {target.strftime('%Y-%m-%d %H:%M:%S')}")
            await asyncio.sleep(wait_seconds)
            
            logger.info("🤖 开始执行自动统计...")
            
            # A. 自动拉取关键词
            online_kws, msg = fetch_keywords_from_gas()
            final_keywords = online_kws if online_kws else [k.strip() for k in FALLBACK_KEYWORDS.splitlines() if k.strip()]
            
            if not final_keywords:
                logger.error("❌ 关键词为空，跳过")
                continue

            # B. 统计昨天的数据
            yesterday = datetime.now(BJ_TZ) - timedelta(days=1)
            day_str = str(yesterday.day)
            start = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
            end = yesterday.replace(hour=23, minute=59, second=59, microsecond=999999)
            
            stats = await quiet_scan(client, start, end, final_keywords)
            
            # C. 自动同步
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
# 前端 UI (Pro Version)
# ==========================================
STATS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>工作量统计 Pro</title>
    <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
    <style>
        :root {
            --primary: #007AFF;
            --bg-body: #F5F7FA;
            --bg-card: #FFFFFF;
            --text-main: #1D1D1F;
            --text-sub: #86868B;
            --border: #E5E5EA;
            --radius: 12px;
            --shadow: 0 4px 20px rgba(0,0,0,0.04);
            --success: #34C759;
            --error: #FF3B30;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", Arial, sans-serif;
            background-color: var(--bg-body);
            color: var(--text-main);
            margin: 0; padding: 20px;
            display: flex; justify-content: center; min-height: 100vh;
        }
        .container {
            width: 100%; max-width: 800px;
            background: var(--bg-card); border-radius: var(--radius);
            box-shadow: var(--shadow); padding: 40px;
            height: fit-content;
        }
        header {
            margin-bottom: 30px; border-bottom: 1px solid var(--border);
            padding-bottom: 20px; display: flex; justify-content: space-between; align-items: center;
        }
        h1 { margin: 0; font-size: 24px; font-weight: 700; letter-spacing: -0.5px; }
        .status-badge {
            font-size: 12px; color: var(--primary); background: rgba(0, 122, 255, 0.1);
            padding: 4px 10px; border-radius: 20px; font-weight: 600;
        }
        .form-section { display: grid; gap: 24px; }
        .input-group label {
            display: block; font-size: 13px; font-weight: 600; color: var(--text-sub);
            margin-bottom: 8px; text-transform: uppercase;
        }
        input[type="number"] {
            width: 100%; padding: 14px; font-size: 16px;
            border: 1px solid var(--border); border-radius: 8px; background: #FAFAFA;
            box-sizing: border-box; font-weight: 500;
        }
        input:focus { background: #FFF; border-color: var(--primary); outline: none; }
        
        textarea.keywords-box {
            width: 100%; height: 180px; padding: 14px;
            font-family: "SF Mono", monospace; font-size: 13px; line-height: 1.5;
            border: 1px solid var(--border); border-radius: 8px; background: #FAFAFA;
            box-sizing: border-box; resize: vertical;
        }
        textarea:focus { background: #FFF; border-color: var(--primary); outline: none; }
        
        .helper-text { font-size: 12px; color: var(--text-sub); margin-top: 6px; }
        .status-tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; margin-bottom: 5px; }
        .st-ok { background: #E8F5E9; color: #2E7D32; }
        .st-err { background: #FFEBEE; color: #C62828; }

        button.submit-btn {
            width: 100%; padding: 16px; background: var(--text-main);
            color: #FFF; border: none; border-radius: 8px;
            font-size: 16px; font-weight: 600; cursor: pointer;
            transition: opacity 0.2s; margin-top: 10px;
        }
        button:hover { opacity: 0.9; }
        button:disabled { background: #CCC; cursor: not-allowed; }
        
        button.sync-btn {
            background: var(--success); margin-top: 0; width: auto;
            padding: 8px 16px; font-size: 14px;
        }

        #progress-wrapper {
            margin-top: 24px; height: 4px; background: #F0F0F0;
            border-radius: 2px; overflow: hidden; display: none;
        }
        #progress-bar { height: 100%; background: var(--primary); width: 0%; transition: width 0.3s; }
        #progress-text { margin-top: 8px; font-size: 12px; color: var(--text-sub); text-align: center; display: none; }

        #result-area { margin-top: 40px; animation: fadeIn 0.5s ease; display: none; }
        .result-header {
            display: flex; align-items: center; justify-content: space-between;
            margin-bottom: 20px; padding-bottom: 10px; border-bottom: 1px solid var(--border);
        }
        .result-header h3 { margin: 0; font-size: 18px; font-weight: 600; }
        
        table { width: 100%; border-collapse: collapse; font-size: 14px; }
        th {
            background: #F9F9F9; text-align: left; padding: 12px 16px;
            font-weight: 600; color: var(--text-sub); border-bottom: 1px solid var(--border);
        }
        td { padding: 12px 16px; border-bottom: 1px solid var(--border); color: var(--text-main); }
        .col-kw { font-family: "SF Mono", monospace; width: 40%; user-select: none; }
        .col-promo { color: var(--primary); text-align: center; background: rgba(0,122,255,0.03); }
        .col-assist { color: #FF9500; text-align: center; background: rgba(255,149,0,0.03); }
        
        .hint-box {
            background: #F2F2F7; padding: 12px; border-radius: 8px;
            font-size: 13px; color: var(--text-sub); margin-bottom: 20px;
        }
        .error-box {
            background: #FFF2F2; color: #D12F2F; padding: 12px;
            border-radius: 8px; font-size: 14px; margin-top: 20px; display: none;
        }
        #sync-status { font-size: 13px; margin-right: 10px; font-weight: 600; }

        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>工作量统计</h1>
            <span class="status-badge">Pro Version</span>
        </header>
        
        <div class="form-section">
            <div class="input-group">
                <label>统计日期 (默认为昨日)</label>
                <input type="number" id="dayInput" placeholder="请输入日期..." min="1" max="31">
                <div class="helper-text">已自动填入“昨天”的日期，可手动修改。</div>
            </div>
            
            <div class="input-group">
                <label>关键词配置</label>
                {% if fetch_status %}
                    <span class="status-tag st-ok">✅ 已从表格同步</span>
                {% else %}
                    <span class="status-tag st-err">⚠️ 使用本地缓存 ({{ fetch_msg }})</span>
                {% endif %}
                <textarea id="keywordsInput" class="keywords-box">{{ default_keywords }}</textarea>
            </div>

            <button onclick="startStats()" id="btnSubmit" class="submit-btn">开始统计</button>
        </div>
        
        <div id="progress-wrapper"><div id="progress-bar"></div></div>
        <div id="progress-text">准备就绪...</div>
        <div id="error-box" class="error-box"></div>

        <div id="result-area">
            <div class="result-header">
                <h3 id="result-title">统计结果</h3>
                <div style="display:flex; align-items:center">
                    <span id="sync-status"></span>
                    <button onclick="syncToCloud()" id="btnSync" class="submit-btn sync-btn">☁️ 同步到表格</button>
                </div>
            </div>
            
            <div class="hint-box">💡 确认数据无误后，点击上方“同步”按钮即可一键写入表格。</div>
            
            <div style="border: 1px solid var(--border); border-radius: 8px; overflow: hidden;">
                <table id="result-table">
                    <thead>
                        <tr>
                            <th>关键词</th>
                            <th style="text-align:center">推广群</th>
                            <th style="text-align:center">协助群</th>
                        </tr>
                    </thead>
                    <tbody id="result-body"></tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
        let currentStats = null;
        let currentDay = null;

        window.onload = function() {
            const now = new Date();
            const yesterday = new Date(now);
            yesterday.setDate(now.getDate() - 1);
            const input = document.getElementById('dayInput');
            if(input) input.value = yesterday.getDate();
        };

        async function startStats() {
            const day = document.getElementById('dayInput').value.trim();
            const keywords = document.getElementById('keywordsInput').value;
            
            if (!day || !keywords) { alert("请填写完整"); return; }
            currentDay = day;

            const btn = document.getElementById('btnSubmit');
            const pWrap = document.getElementById('progress-wrapper');
            const pBar = document.getElementById('progress-bar');
            const pText = document.getElementById('progress-text');
            const errBox = document.getElementById('error-box');
            const resArea = document.getElementById('result-area');
            const tbody = document.getElementById('result-body');
            const syncBtn = document.getElementById('btnSync');
            const syncSt = document.getElementById('sync-status');

            btn.disabled = true; btn.innerText = "统计中...";
            pWrap.style.display = 'block'; pText.style.display = 'block'; pBar.style.width = '2%';
            pText.innerText = '连接服务器...'; errBox.style.display = 'none';
            resArea.style.display = 'none'; tbody.innerHTML = '';
            syncSt.innerText = ''; syncBtn.disabled = false; syncBtn.innerText = "☁️ 同步到表格";

            try {
                const params = new URLSearchParams({day, keywords});
                const response = await fetch('/api/work_stats_stream?' + params);
                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    const lines = decoder.decode(value, {stream:true}).split('\\n');
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const data = JSON.parse(line);
                            if (data.type === 'progress') {
                                pBar.style.width = data.percent + '%';
                                pText.innerText = data.msg;
                            } else if (data.type === 'done') {
                                currentStats = data.results;
                                renderTable(data.results, keywords);
                                pBar.style.width = '100%';
                                pText.innerText = '✨ 统计完成';
                            } else if (data.type === 'error') throw new Error(data.msg);
                        } catch (e) {}
                    }
                }
            } catch (e) {
                errBox.innerText = "❌ 错误: " + e.message; errBox.style.display = 'block';
                pText.innerText = '失败'; pBar.style.backgroundColor = '#FF3B30';
            } finally {
                btn.disabled = false; btn.innerText = "开始统计";
            }
        }

        function renderTable(statsMap, rawKeywords) {
            const tbody = document.getElementById('result-body');
            const lines = rawKeywords.split('\\n');
            let total = 0;
            lines.forEach(line => {
                const kw = line.trim(); if (!kw) return;
                const data = statsMap[kw] || {promo: 0, assist: 0};
                total += (data.promo + data.assist);
                const tr = document.createElement('tr');
                tr.innerHTML = `<td class="col-kw">${kw}</td><td class="col-promo">${data.promo}</td><td class="col-assist">${data.assist}</td>`;
                tbody.appendChild(tr);
            });
            document.getElementById('result-area').style.display = 'block';
            document.getElementById('result-title').innerText = `统计结果 (${total})`;
        }

        async function syncToCloud() {
            if (!currentStats || !currentDay) return;
            const btn = document.getElementById('btnSync');
            const st = document.getElementById('sync-status');
            btn.disabled = true; btn.innerText = "同步中...";
            
            try {
                const res = await fetch('/api/sync_to_sheet', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({day: currentDay, stats: currentStats})
                });
                const json = await res.json();
                if (json.success) {
                    st.style.color = '#34C759'; st.innerText = "✅ " + json.msg;
                    btn.innerText = "已同步";
                } else {
                    st.style.color = '#FF3B30'; st.innerText = "❌ " + json.msg;
                    btn.disabled = false; btn.innerText = "重试";
                }
            } catch (e) {
                st.style.color = '#FF3B30'; st.innerText = "❌ 网络错误";
                btn.disabled = false; btn.innerText = "重试";
            }
        }
    </script>
</body>
</html>
"""

# ==========================================
# 任务执行器 (流式进度)
# ==========================================
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

# ==========================================
# 路由初始化
# ==========================================
def init_stats_blueprint(app, client, bot_loop, _unused_args=None):
    # 启动每日定时任务
    if bot_loop and client:
        bot_loop.create_task(daily_scheduler(client))

    @app.route('/tool/work_stats')
    def work_stats_view():
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
        if not day or not kws: return "Error", 400
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
