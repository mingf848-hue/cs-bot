import asyncio
import time
import logging
import re
import json
import queue
from datetime import datetime, timedelta, timezone
from flask import request, render_template_string, Response, stream_with_context

# 定义北京时区
BJ_TZ = timezone(timedelta(hours=8))

# ==========================================
# 配置：群组分类定义
# ==========================================
# 推广群列表
PROMO_GROUPS = {
    -1001885279888, # 菲一
    -1001800838000, # 菲二
    -1001703213989, # 柬群
    -1001972746703, # 存款
    -1001871198775, # 产品反馈
}

# 协助群列表
ASSIST_GROUPS = {
    -1002169616907, # 用体
    -1002053064967, # 判定
    -1002728905038, # 敏感
    -1002154594658, # FD三方
    -1002004030172, # 赛事
    -1002174533164, # 站内
    -1001978088089, # 维护一
    -1001931146238, # 维护二
    -1001911814916, # 维护三
    -1001571955528, # 代理一
    -1001587586041, # 代理二
    -1002807120955, # AFF
}

# 合并所有需要扫描的目标群组
ALL_TARGET_GROUPS = list(PROMO_GROUPS | ASSIST_GROUPS)

# HTML 模板 (含 CSS 进度条和 JS 逻辑)
STATS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>工作量统计 (智能清洗版)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #f0f2f5; padding: 20px; max-width: 900px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 25px; border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 15px; font-size: 1.5rem; color: #1a1a1a; }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; font-weight: bold; color: #555; }
        
        input[type="number"] { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        
        /* 关键词输入框 */
        textarea.keywords-box { 
            width: 100%; 
            height: 300px; 
            font-family: monospace; 
            padding: 12px; 
            border: 1px solid #ddd; 
            border-radius: 6px; 
            box-sizing: border-box; 
            background-color: #fff; 
            color: #333;
            font-size: 14px;
        }
        textarea.keywords-box:focus { border-color: #0088cc; outline: none; }

        button.submit-btn { background: #0088cc; color: white; border: none; padding: 12px 25px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; transition: background 0.2s; }
        button.submit-btn:hover { background: #006699; }
        button.submit-btn:disabled { background: #ccc; cursor: not-allowed; }
        
        /* 进度条样式 */
        #progress-wrapper { margin-top: 20px; display: none; background: #f1f1f1; border-radius: 6px; overflow: hidden; height: 24px; position: relative; }
        #progress-bar { height: 100%; background: #4caf50; width: 0%; transition: width 0.3s ease; }
        #progress-text { margin-top: 8px; font-size: 13px; color: #666; text-align: center; display: none; }

        /* 表格样式 */
        table { width: 100%; border-collapse: collapse; margin-top: 30px; background: #fff; display: none; }
        th, td { border: 1px solid #e0e0e0; padding: 10px 12px; text-align: left; }
        th { background-color: #f8f9fa; font-weight: bold; color: #444; }
        
        /* 关键词列：禁止选中！ */
        .col-kw {
            user-select: none;
            -webkit-user-select: none;
            -moz-user-select: none;
            -ms-user-select: none;
            color: #555;
            background-color: #fafafa;
            cursor: default;
        }

        .col-promo { background-color: #e3f2fd; color: #1565c0; font-weight: bold; text-align: center; width: 120px; }
        .col-assist { background-color: #fff3e0; color: #ef6c00; font-weight: bold; text-align: center; width: 120px; }
        
        .copy-btn {
            font-size: 12px;
            padding: 4px 8px;
            margin-left: 8px;
            background: #fff;
            border: 1px solid #ccc;
            border-radius: 4px;
            cursor: pointer;
            color: #333;
            font-weight: normal;
        }
        .copy-btn:hover { background: #eee; }
        .copy-btn:active { background: #ddd; transform: translateY(1px); }

        .hint { font-size: 13px; color: #777; margin-top: 10px; display: none; }
        .info-tag { display: inline-block; background: #e0f7fa; color: #006064; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-bottom: 5px; }
        
        .error-box { display:none; color: #d32f2f; background: #ffebee; padding: 15px; border-radius: 6px; margin-top: 20px; border: 1px solid #ffcdd2; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📊 工作量统计 (智能清洗版)</h1>
        
        <div class="form-group">
            <label>📅 统计日期 (输入当月几号):</label>
            <input type="number" id="dayInput" placeholder="例如: 26" value="" min="1" max="31">
            <div style="font-size:12px;color:#888;margin-top:5px">范围：所选日期的 00:00:00 至 23:59:59 (北京时间)</div>
        </div>
        
        <div class="form-group">
            <label>📝 稍等词列表 (每行一个):</label>
            <div class="info-tag">ℹ️ 已启用智能匹配：不区分大小写，不区分波浪号(～/~)</div>
            <textarea id="keywordsInput" class="keywords-box">{{ default_keywords }}</textarea>
        </div>
        
        <button onclick="startStats()" id="btnSubmit" class="submit-btn">🚀 开始统计</button>
        
        <div id="progress-wrapper"><div id="progress-bar"></div></div>
        <div id="progress-text">准备就绪...</div>
        <div id="error-box" class="error-box"></div>

        <div id="result-area">
            <h3 id="result-title" style="display:none; margin-top:30px; border-top: 2px solid #eee; padding-top:20px;">统计结果</h3>
            <div id="result-hint" class="hint">💡 第一列关键词已锁定无法选中，方便直接框选数字或使用上方复制按钮。</div>
            
            <table id="result-table">
                <thead>
                    <tr>
                        <th class="col-kw">稍等关键词</th>
                        <th class="col-promo">
                            推广群
                            <button class="copy-btn" onclick="copyColumn('val-promo')">📋 复制</button>
                        </th>
                        <th class="col-assist">
                            协助群
                            <button class="copy-btn" onclick="copyColumn('val-assist')">📋 复制</button>
                        </th>
                    </tr>
                </thead>
                <tbody id="result-body"></tbody>
            </table>
        </div>
    </div>

    <script>
        async function startStats() {
            const day = document.getElementById('dayInput').value.trim();
            const keywords = document.getElementById('keywordsInput').value;
            
            if (!day) { alert("请输入日期"); return; }
            if (!keywords) { alert("请输入关键词"); return; }

            // UI Reset
            const btn = document.getElementById('btnSubmit');
            const pWrap = document.getElementById('progress-wrapper');
            const pBar = document.getElementById('progress-bar');
            const pText = document.getElementById('progress-text');
            const errBox = document.getElementById('error-box');
            const table = document.getElementById('result-table');
            const resTitle = document.getElementById('result-title');
            const resHint = document.getElementById('result-hint');
            const tbody = document.getElementById('result-body');

            btn.disabled = true;
            pWrap.style.display = 'block';
            pText.style.display = 'block';
            pBar.style.width = '1%';
            pText.innerText = '正在连接服务器...';
            errBox.style.display = 'none';
            table.style.display = 'none';
            resTitle.style.display = 'none';
            resHint.style.display = 'none';
            tbody.innerHTML = '';

            try {
                // 构建 URL 参数
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
                                renderTable(data.results, keywords);
                                pBar.style.width = '100%';
                                pText.innerText = '✅ 统计完成！';
                            } else if (data.type === 'error') {
                                throw new Error(data.msg);
                            }
                        } catch (e) {
                            console.error("Parse error", e);
                        }
                    }
                }
            } catch (e) {
                errBox.innerText = "发生错误: " + e.message;
                errBox.style.display = 'block';
                pText.innerText = '❌ 失败';
                pBar.style.backgroundColor = '#d32f2f';
            } finally {
                btn.disabled = false;
            }
        }

        function renderTable(statsMap, rawKeywords) {
            const tbody = document.getElementById('result-body');
            const lines = rawKeywords.split('\\n');
            let totalHits = 0;

            lines.forEach(line => {
                const kw = line.trim();
                if (!kw) return;
                
                const data = statsMap[kw] || {promo: 0, assist: 0};
                totalHits += (data.promo + data.assist);

                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td class="col-kw">${kw}</td>
                    <td class="col-promo val-promo">${data.promo}</td>
                    <td class="col-assist val-assist">${data.assist}</td>
                `;
                tbody.appendChild(tr);
            });

            document.getElementById('result-table').style.display = 'table';
            document.getElementById('result-title').style.display = 'block';
            document.getElementById('result-title').innerText = `统计结果 (共命中 ${totalHits} 条)`;
            document.getElementById('result-hint').style.display = 'block';
        }

        function copyColumn(className) {
            const cells = document.querySelectorAll('.' + className);
            let textToCopy = '';
            cells.forEach(cell => {
                textToCopy += cell.innerText.trim() + '\\n';
            });
            
            if (!textToCopy) return;

            navigator.clipboard.writeText(textToCopy).then(() => {
                alert('✅ 已复制列数据！');
            }).catch(err => {
                alert('❌ 复制失败: ' + err);
            });
        }
    </script>
</body>
</html>
"""

# 默认预设的关键词列表 (初始值)
DEFAULT_KEYWORDS = """稍等-an
请稍等elk
稍等～ys
请稍等~lofi
请稍等～aja
请稍等-HED
请稍等～yu
稍等-SO
请稍等 - AB
请稍等ART
请稍等-~cc
请稍等~d
请稍等-MAD
请稍等～～aug
请稍等-xxxx
稍等-Be
稍等-XW
稍等--Gr💬
稍等-jl"""

logger = logging.getLogger("BotLogger")

def normalize_text(text):
    """
    格式清洗函数：
    1. 转小写
    2. 将中文波浪号 ～ 替换为英文 ~
    3. 去除首尾空格
    """
    if not text:
        return ""
    return text.lower().replace("～", "~").strip()

async def perform_scan(client, start_time, end_time, keywords, result_queue):
    """
    异步执行器：扫描指定群组并在内存中分类统计，并通过 Queue 推送进度
    """
    try:
        # 初始化统计结构：{原关键词: {'promo': 0, 'assist': 0}}
        stats = {kw: {'promo': 0, 'assist': 0} for kw in keywords}
        
        # 建立清洗后的映射表: normalized -> list of original keywords
        norm_map = []
        for kw in keywords:
            norm_map.append((kw, normalize_text(kw)))

        # 转换为 UTC 时间 (Telethon 使用 UTC)
        utc_start = start_time.astimezone(timezone.utc)
        utc_end = end_time.astimezone(timezone.utc)
        
        total_groups = len(ALL_TARGET_GROUPS)
        
        # 遍历所有目标群组
        for idx, chat_id in enumerate(ALL_TARGET_GROUPS):
            # 推送进度
            percent = int((idx / total_groups) * 100)
            result_queue.put(json.dumps({
                "type": "progress", 
                "percent": percent, 
                "msg": f"正在扫描群组 {chat_id} ({idx+1}/{total_groups})..."
            }))

            # 确定当前群组属于哪个分类
            category = 'other'
            if chat_id in PROMO_GROUPS:
                category = 'promo'
            elif chat_id in ASSIST_GROUPS:
                category = 'assist'
            else:
                continue

            try:
                # 扫描该群组指定时间段的消息
                async for message in client.iter_messages(chat_id, offset_date=utc_end, reverse=False):
                    if message.date < utc_start:
                        break # 超出时间范围
                    
                    if not message.text:
                        continue
                    
                    # 清洗消息内容
                    content_norm = normalize_text(message.text)
                    
                    # 检查关键词
                    for original_kw, kw_norm in norm_map:
                        if kw_norm in content_norm:
                            stats[original_kw][category] += 1
                            break 
                            
            except Exception as e:
                logger.error(f"[Stats] 群组 {chat_id} 扫描失败: {e}")
        
        # 完成
        result_queue.put(json.dumps({
            "type": "done",
            "results": stats
        }))
        
    except Exception as e:
        logger.error(f"Scan Task Error: {e}")
        result_queue.put(json.dumps({"type": "error", "msg": str(e)}))
    finally:
        result_queue.put(None) # Sentinel to stop stream

def init_stats_blueprint(app, client, bot_loop, _unused_args=None):
    """
    初始化 Flask 路由 (包含 UI 和 API)
    """
    
    # 1. 渲染页面 UI
    @app.route('/tool/work_stats')
    def work_stats_view():
        return render_template_string(STATS_HTML, default_keywords=DEFAULT_KEYWORDS)

    # 2. 流式 API 接口
    @app.route('/api/work_stats_stream')
    def work_stats_stream():
        day_input = request.args.get('day')
        keywords_input = request.args.get('keywords', '')
        
        if not day_input or not keywords_input:
            return "Missing args", 400

        def generate():
            # 参数校验
            try:
                now = datetime.now(BJ_TZ)
                target_day = int(day_input)
                start_time = now.replace(day=target_day, hour=0, minute=0, second=0, microsecond=0)
                end_time = now.replace(day=target_day, hour=23, minute=59, second=59, microsecond=999999)
                
                keywords_list = [line.strip() for line in keywords_input.splitlines() if line.strip()]
                if not keywords_list: raise ValueError("关键词为空")
            except Exception as e:
                yield json.dumps({"type": "error", "msg": str(e)}) + "\n"
                return

            # 创建队列用于跨线程通信
            result_queue = queue.Queue()
            
            if not bot_loop or not client:
                yield json.dumps({"type": "error", "msg": "Bot未就绪"}) + "\n"
                return

            # 提交异步任务
            asyncio.run_coroutine_threadsafe(
                perform_scan(client, start_time, end_time, keywords_list, result_queue),
                bot_loop
            )
            
            # 阻塞读取队列并流式输出
            while True:
                data = result_queue.get()
                if data is None: break
                yield data + "\n"

        return Response(stream_with_context(generate()), mimetype='text/plain')
