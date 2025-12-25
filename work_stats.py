import asyncio
import time
import logging
import re
from datetime import datetime, timedelta, timezone
from flask import request, render_template_string

# 定义北京时区
BJ_TZ = timezone(timedelta(hours=8))

# HTML 模板
STATS_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>工作量统计 (Work Stats)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, sans-serif; background: #f0f2f5; padding: 20px; max-width: 800px; margin: 0 auto; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        h1 { margin-top: 0; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="number"], textarea { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; }
        textarea { height: 300px; font-family: monospace; }
        button { background: #0088cc; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; }
        button:hover { background: #006699; }
        
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .count-col { font-weight: bold; color: #d32f2f; width: 100px; text-align: center; }
        .copy-hint { font-size: 12px; color: #666; margin-top: 5px; text-align: right; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📊 稍等工作量统计</h1>
        <form method="POST">
            <div class="form-group">
                <label>日期 (输入当月几号):</label>
                <input type="number" name="day" placeholder="例如: 26" value="{{ day }}" required min="1" max="31">
                <div style="font-size:12px;color:#666;margin-top:4px">统计范围：所选日期的 00:00:00 至 23:59:59 (北京时间)</div>
            </div>
            <div class="form-group">
                <label>稍等词列表 (每行一个，按顺序统计):</label>
                <textarea name="keywords">{{ keywords_text }}</textarea>
            </div>
            <button type="submit">开始统计</button>
        </form>

        {% if results %}
        <h3>统计结果 ({{ total_hits }} 条)</h3>
        <div class="copy-hint">提示：可以直接复制右侧数字列</div>
        <table>
            <thead>
                <tr>
                    <th>关键词</th>
                    <th class="count-col">次数</th>
                </tr>
            </thead>
            <tbody>
                {% for kw, count in results %}
                <tr>
                    <td>{{ kw }}</td>
                    <td class="count-col">{{ count }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
        {% endif %}
        
        {% if error %}
        <div style="color:red; margin-top:20px; font-weight:bold;">❌ {{ error }}</div>
        {% endif %}
    </div>
</body>
</html>
"""

# 默认预设的关键词列表
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

async def perform_scan(client, group_ids, start_time, end_time, keywords):
    """
    异步执行器：扫描指定时间段的消息并统计关键词
    """
    stats = {kw: 0 for kw in keywords}
    total_scanned = 0
    
    # 转换为 UTC 时间用于 Telethon 查询 (因为 Telethon 内部使用 UTC)
    utc_start = start_time.astimezone(timezone.utc)
    utc_end = end_time.astimezone(timezone.utc)
    
    logger.info(f"📊 开始工作量统计: {start_time} - {end_time} (BJ)")

    for chat_id in group_ids:
        try:
            # 优化：只获取该时间段内的消息
            # iter_messages 从最新的消息开始往前翻，直到遇到比 utc_start 更老的消息
            async for message in client.iter_messages(chat_id, offset_date=utc_end, reverse=False):
                if message.date < utc_start:
                    break # 超出时间范围，停止该群扫描
                
                if not message.text:
                    continue
                
                # 统计逻辑：检查消息包含哪个关键词
                # 注意：如果一条消息包含多个关键词，通常只算一个，这里按列表顺序优先匹配
                content = message.text
                for kw in keywords:
                    if kw in content:
                        stats[kw] += 1
                        break # 命中一个后跳出，防止重复统计
                        
                total_scanned += 1
                
        except Exception as e:
            logger.error(f"统计群组 {chat_id} 出错: {e}")
            
    return stats

def init_stats_blueprint(app, client, bot_loop, group_ids):
    """
    初始化 Flask 路由
    """
    
    @app.route('/tool/work_stats', methods=['GET', 'POST'])
    def work_stats_view():
        day_input = ""
        keywords_text = DEFAULT_KEYWORDS
        results = None
        error = None
        total_hits = 0

        if request.method == 'POST':
            try:
                # 1. 解析日期
                day_input = request.form.get('day')
                raw_keywords = request.form.get('keywords', '')
                
                now = datetime.now(BJ_TZ)
                try:
                    target_day = int(day_input)
                    # 构造当月的目标日期
                    start_time = now.replace(day=target_day, hour=0, minute=0, second=0, microsecond=0)
                    end_time = now.replace(day=target_day, hour=23, minute=59, second=59, microsecond=999999)
                    
                    # 如果输入的日期比今天大，且不是想查未来的（逻辑上是查上个月？暂定查当月）
                    # 简单逻辑：只处理当月
                except ValueError:
                    raise ValueError("日期格式错误，请输入数字")

                # 2. 解析关键词 (保持顺序，去除空行)
                keywords_list = [line.strip() for line in raw_keywords.splitlines() if line.strip()]
                keywords_text = raw_keywords # 保持用户输入的样子回显

                if not keywords_list:
                    raise ValueError("关键词列表不能为空")

                # 3. 在主循环中执行扫描任务
                if not bot_loop or not client:
                    raise ValueError("Bot 未就绪")

                # 使用 run_coroutine_threadsafe 跨线程调用 Telethon
                future = asyncio.run_coroutine_threadsafe(
                    perform_scan(client, group_ids, start_time, end_time, keywords_list),
                    bot_loop
                )
                
                # 等待结果 (会阻塞 HTTP 请求直到扫描完成)
                # 设定 60 秒超时防止死锁
                stats_map = future.result(timeout=120)

                # 4. 格式化结果 (按输入顺序)
                results = []
                for kw in keywords_list:
                    count = stats_map.get(kw, 0)
                    results.append((kw, count))
                    total_hits += count

            except Exception as e:
                error = str(e)
                logger.error(f"统计页面错误: {e}")

        return render_template_string(
            STATS_HTML, 
            day=day_input, 
            keywords_text=keywords_text,
            results=results,
            error=error,
            total_hits=total_hits
        )
