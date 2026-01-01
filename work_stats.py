import asyncio
import time
import logging
import re
import json
import queue
import os
import requests  # 必须引入
from datetime import datetime, timedelta, timezone
from flask import request, render_template_string, Response, stream_with_context

# 定义北京时区
BJ_TZ = timezone(timedelta(hours=8))

# ==========================================
# 配置：群组分类定义 (请确保和你的实际ID一致)
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
# Google Apps Script 同步函数
# ==========================================
def sync_data_via_script(day, stats_data):
    """
    发送数据到 Google Apps Script
    """
    try:
        # 获取环境变量中的 Webhook URL
        script_url = os.environ.get("GOOGLE_SCRIPT_URL")
        if not script_url:
            return False, "未配置 GOOGLE_SCRIPT_URL 环境变量"

        # 构造发送给 GAS 的数据包
        payload = {
            "day": day,         # 用户输入的号数，如 "6"
            "stats": stats_data # 完整的统计字典
        }

        # 发送 POST 请求 (GAS 有时会返回重定向，所以 allow_redirects=True)
        response = requests.post(script_url, json=payload, allow_redirects=True, timeout=15)
        
        if response.status_code == 200:
            res_json = response.json()
            if res_json.get("success"):
                return True, res_json.get("msg")
            else:
                return False, "GAS返回错误: " + res_json.get("msg")
        else:
            return False, f"HTTP 请求失败: {response.status_code}"

    except Exception as e:
        logger.error(f"Sync Error: {e}")
        return False, str(e)

# ==========================================
# 前端 HTML 模板 (包含同步按钮)
# ==========================================
STATS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>工作量统计 (矩阵同步版)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f0f2f5; padding: 20px; max-width: 900px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 25px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 15px; font-size: 1.5rem; color: #1a1a1a; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
        
        input[type="number"] { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        
        textarea.keywords-box { width: 100%; height: 200px; font-family: monospace; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }

        /* 按钮通用样式 */
        button.submit-btn { background: #0088cc; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; margin-bottom: 10px; transition: 0.2s; }
        button.submit-btn:hover { background: #006699; }
        button.submit-btn:disabled { background: #ccc; cursor: not-allowed; }

        /* 同步按钮特别样式 */
        .sync-btn { background: #0f9d58; } /* 谷歌绿 */
        .sync-btn:hover { background: #0b8043; }

        #progress-wrapper { margin-top: 20px; display: none; background: #f1f1f1; border-radius: 6px; overflow: hidden; height: 24px; position: relative; }
        #progress-bar { height: 100%; background: #4caf50; width: 0%; transition: width 0.3s ease; }
        #progress-text { margin-top: 8px; font-size: 13px; color: #666; text-align: center; display: none; }

        table { width: 100%; border-collapse: collapse; margin-top: 30px; background: #fff; display: none; }
        th, td { border: 1px solid #e0e0e0; padding: 10px 12px; text-align: left; }
        th { background-color: #f8f9fa; font-weight: bold; color: #444; }
        .col-promo { background-color: #e3f2fd; color: #1565c0; font-weight: bold; text-align: center; }
        .col-assist { background-color: #fff3e0; color: #ef6c00; font-weight: bold; text-align: center; }
        
        .error-box { display:none; color: #d32f2f; background: #ffebee; padding: 15px; border-radius: 6px; margin-top: 20px; border: 1px solid #ffcdd2; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📊 工作量统计 (矩阵同步版)</h1>
        
        <div class="form-group">
            <label>📅 统计日期 (输入当月几号):</label>
            <input type="number" id="dayInput" placeholder="例如: 6" value="" min="1" max="31">
            <div style="font-size:12px;color:#888;margin-top:5px">请输入数字，系统将自动匹配本月该日期的列</div>
        </div>
        
        <div class="form-group">
            <label>📝 稍等词列表:</label>
            <textarea id="keywordsInput" class="keywords-box">{{ default_keywords }}</textarea>
        </div>
        
        <button onclick="startStats()" id="btnSubmit" class="submit-btn">🚀 开始统计</button>
        
        <div id="progress-wrapper"><div id="progress-bar"></div></div>
        <div id="progress-text">准备就绪...</div>
        <div id="error-box" class="error-box"></div>

        <div id="result-area" style="display:none">
            <h3 style="margin-top:30px; border-top: 2px solid #eee; padding-top:20px;">统计结果</h3>
            
            <div id="sync-area" style="margin-bottom: 20px;">
                <button onclick="syncToCloud()" id="btnSync" class="submit-btn sync-btn">☁️ 一键填入谷歌表格</button>
                <div id="sync-msg" style="margin-top:5px; font-size:14px; font-weight:bold; text-align:center;"></div>
            </div>

            <table id="result-table">
                <thead>
                    <tr><th>稍等关键词</th><th class="col-promo">推广群</th><th class="col-assist">协助群</th></tr>
                </thead>
                <tbody id="result-body"></tbody>
            </table>
        </div>
    </div>

    <script>
        let currentStatsData = null; // 存储统计结果用于同步
        let currentDay = null;       // 存储日期

        async function startStats() {
            const day = document.getElementById('dayInput').value.trim();
            const keywords = document.getElementById('keywordsInput').value;
            
            if (!day) { alert("请输入日期"); return; }
            if (!keywords) { alert("请输入关键词"); return; }
            currentDay = day;

            // UI Reset
            const btn = document.getElementById('btnSubmit');
            const pWrap = document.getElementById('progress-wrapper');
            const pBar = document.getElementById('progress-bar');
            const pText = document.getElementById('progress-text');
            const errBox = document.getElementById('error-box');
            const resArea = document.getElementById('result-area');
            const tbody = document.getElementById('result-body');
            const syncBtn = document.getElementById('btnSync');
            const syncMsg = document.getElementById('sync-msg');

            btn.disabled = true;
            pWrap.style.display = 'block';
            pText.style.display = 'block';
            pBar.style.width = '1%';
            pText.innerText = '正在连接服务器...';
            errBox.style.display = 'none';
            resArea.style.display = 'none';
            tbody.innerHTML = '';
            syncMsg.innerText = '';
            syncBtn.disabled = false;
            syncBtn.innerText = '☁️ 一键填入谷歌表格';

            try {
                const params = new URLSearchParams();
                params.append('day', day);
                params.append('keywords', keywords);

                const response = await fetch('/api/work_stats_stream?' + params.toString());
                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    
                    const chunk = decoder.decode(value, {stream: true});
                    const lines = chunk.split('\\n');
                    
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const data = JSON.parse(line);
                            
                            if (data.type === 'progress') {
                                pBar.style.width = data.percent + '%';
                                pText.innerText = data.msg;
                            } else if (data.type === 'done') {
                                currentStatsData = data.results; // 保存数据用于同步
                                renderTable(data.results, keywords);
                                pBar.style.width = '100%';
                                pText.innerText = '✅ 统计完成！';
                                resArea.style.display = 'block'; // 显示结果和同步按钮
                            } else if (data.type === 'error') {
                                throw new Error(data.msg);
                            }
                        } catch (e) { console.error(e); }
                    }
                }
            } catch (e) {
                errBox.innerText = "发生错误: " + e.message;
                errBox.style.display = 'block';
                pText.innerText = '❌ 失败';
            } finally {
                btn.disabled = false;
            }
        }

        function renderTable(statsMap, rawKeywords) {
            const tbody = document.getElementById('result-body');
            const lines = rawKeywords.split('\\n');
            lines.forEach(line => {
                const kw = line.trim();
                if (!kw) return;
                const data = statsMap[kw] || {promo: 0, assist: 0};
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${kw}</td>
                    <td class="col-promo">${data.promo}</td>
                    <td class="col-assist">${data.assist}</td>
                `;
                tbody.appendChild(tr);
            });
            document.getElementById('result-table').style.display = 'table';
        }

        async function syncToCloud() {
            if (!currentStatsData || !currentDay) { alert("无数据"); return; }
            
            const btn = document.getElementById('btnSync');
            const msgDiv = document.getElementById('sync-msg');
            
            btn.disabled = true;
            btn.innerText = "正在同步...";
            msgDiv.innerText = "";
            
            try {
                const response = await fetch('/api/sync_to_sheet', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        day: currentDay,
                        stats: currentStatsData
                    })
                });
                
                const res = await response.json();
                if (res.success) {
                    msgDiv.style.color = '#0f9d58';
                    msgDiv.innerText = "✅ " + res.msg;
                    btn.innerText = "同步成功";
                } else {
                    msgDiv.style.color = '#d32f2f';
                    msgDiv.innerText = "❌ " + res.msg;
                    btn.innerText = "重试同步";
                    btn.disabled = false;
                }
            } catch (e) {
                msgDiv.style.color = '#d32f2f';
                msgDiv.innerText = "❌ 网络错误: " + e.message;
                btn.disabled = false;
                btn.innerText = "重试同步";
            }
        }
    </script>
</body>
</html>
"""

DEFAULT_KEYWORDS = """稍等-an
请稍等elk
请稍等~d
稍等--Gr💬
请稍等～aja
请稍等～～aug
稍等-jl
请稍等-MAD
稍等-Be
稍等-XW
稍等-SO
请稍等～yu
请稍等-xxxx
稍等～ys
请稍等~lofi
请稍等 - AB
请稍等ART
请稍等-~cc
请稍等-HED"""

def normalize_text(text):
    if not text: return ""
    return text.lower().replace("～", "~").strip()

async def perform_scan(client, start_time, end_time, keywords, result_queue):
    try:
        stats = {kw: {'promo': 0, 'assist': 0} for kw in keywords}
        norm_map = [(kw, normalize_text(kw)) for kw in keywords]

        utc_start = start_time.astimezone(timezone.utc)
        utc_end = end_time.astimezone(timezone.utc)
        
        total_groups = len(ALL_TARGET_GROUPS)
        
        for idx, chat_id in enumerate(ALL_TARGET_GROUPS):
            percent = int((idx / total_groups) * 100)
            result_queue.put(json.dumps({
                "type": "progress", "percent": percent, 
                "msg": f"正在扫描群组 {chat_id} ({idx+1}/{total_groups})..."
            }))

            category = 'promo' if chat_id in PROMO_GROUPS else 'assist' if chat_id in ASSIST_GROUPS else 'other'
            if category == 'other': continue

            try:
                async for message in client.iter_messages(chat_id, offset_date=utc_end, reverse=False):
                    if message.date < utc_start: break
                    if not message.text: continue
                    
                    content_norm = normalize_text(message.text)
                    for original_kw, kw_norm in norm_map:
                        if kw_norm in content_norm:
                            stats[original_kw][category] += 1
                            break 
            except Exception: pass
        
        result_queue.put(json.dumps({"type": "done", "results": stats}))
        
    except Exception as e:
        result_queue.put(json.dumps({"type": "error", "msg": str(e)}))
    finally:
        result_queue.put(None)

def init_stats_blueprint(app, client, bot_loop, _unused_args=None):
    @app.route('/tool/work_stats')
    def work_stats_view():
        return render_template_string(STATS_HTML, default_keywords=DEFAULT_KEYWORDS)

    @app.route('/api/work_stats_stream')
    def work_stats_stream():
        day_input = request.args.get('day')
        keywords_input = request.args.get('keywords', '')
        
        if not day_input or not keywords_input: return "Missing args", 400

        def generate():
            try:
                now = datetime.now(BJ_TZ)
                target_day = int(day_input)
                start_time = now.replace(day=target_day, hour=0, minute=0, second=0, microsecond=0)
                end_time = now.replace(day=target_day, hour=23, minute=59, second=59, microsecond=999999)
                keywords_list = [line.strip() for line in keywords_input.splitlines() if line.strip()]
            except Exception as e:
                yield json.dumps({"type": "error", "msg": str(e)}) + "\n"; return

            result_queue = queue.Queue()
            if not bot_loop or not client:
                yield json.dumps({"type": "error", "msg": "Bot未就绪"}) + "\n"; return

            asyncio.run_coroutine_threadsafe(
                perform_scan(client, start_time, end_time, keywords_list, result_queue),
                bot_loop
            )
            
            while True:
                data = result_queue.get()
                if data is None: break
                yield data + "\n"

        return Response(stream_with_context(generate()), mimetype='text/plain')

    # === 新增：同步 API 接口 ===
    @app.route('/api/sync_to_sheet', methods=['POST'])
    def sync_to_sheet():
        try:
            data = request.json
            day = data.get('day')
            stats = data.get('stats', {})
            
            # 调用上方的同步函数
            success, msg = sync_data_via_script(day, stats)
            
            return json.dumps({"success": success, "msg": msg}), 200, {'Content-Type': 'application/json'}
        except Exception as e:
            return json.dumps({"success": False, "msg": str(e)}), 500
