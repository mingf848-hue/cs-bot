import asyncio
import time
import logging
import re
from datetime import datetime, timedelta, timezone
from flask import request, render_template_string

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

# HTML 模板
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
        
        /* 关键词输入框：可编辑 */
        textarea.keywords-box { 
            width: 100%; 
            height: 350px; 
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
        
        /* 表格样式 */
        table { width: 100%; border-collapse: collapse; margin-top: 30px; background: #fff; }
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
        
        /* 复制按钮样式 */
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

        .hint { font-size: 13px; color: #777; margin-top: 10px; }
        .info-tag { display: inline-block; background: #e0f7fa; color: #006064; padding: 2px 6px; border-radius: 4px; font-size: 12px; margin-bottom: 5px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>📊 工作量统计 (推广/协助)</h1>
        
        <form method="POST">
            <div class="form-group">
                <label>📅 统计日期 (输入当月几号):</label>
                <input type="number" name="day" placeholder="例如: 26" value="{{ day }}" required min="1" max="31">
                <div style="font-size:12px;color:#888;margin-top:5px">范围：所选日期的 00:00:00 至 23:59:59 (北京时间)</div>
            </div>
            
            <div class="form-group">
                <label>📝 稍等词列表 (每行一个):</label>
                <div class="info-tag">ℹ️ 已启用智能匹配：不区分大小写，不区分波浪号(～/~)</div>
                <textarea name="keywords" class="keywords-box">{{ keywords_text }}</textarea>
            </div>
            
            <button type="submit" class="submit-btn">🚀 开始统计</button>
        </form>

        {% if results %}
        <div style="margin-top:30px; border-top: 2px solid #eee; padding-top:20px;">
            <h3>统计结果 (共命中 {{ total_hits }} 条)</h3>
            <div class="hint">💡 第一列关键词已锁定无法选中，方便直接框选数字或使用上方复制按钮。</div>
            
            <table>
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
                <tbody>
                    {% for row in results %}
                    <tr>
                        <td class="col-kw">{{ row.kw }}</td>
                        <td class="col-promo val-promo">{{ row.promo }}</td>
                        <td class="col-assist val-assist">{{ row.assist }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        
        {% if error %}
        <div style="color: #d32f2f; background: #ffebee; padding: 15px; border-radius: 6px; margin-top: 20px; border: 1px solid #ffcdd2;">
            ❌ 错误: {{ error }}
        </div>
        {% endif %}
    </div>

    <script>
        function copyColumn(className) {
            const cells = document.querySelectorAll('.' + className);
            let textToCopy = '';
            cells.forEach(cell => {
                // 去除空白，只取数字，并换行
                textToCopy += cell.innerText.trim() + '\\n';
            });
            
            if (!textToCopy) {
                alert('没有数据可复制');
                return;
            }

            navigator.clipboard.writeText(textToCopy).then(() => {
                alert('✅ 已复制列数据！请直接去 Excel 粘贴。');
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
    # 核心清洗逻辑
    return text.lower().replace("～", "~").strip()

async def perform_scan(client, start_time, end_time, keywords):
    """
    异步执行器：扫描指定群组并在内存中分类统计
    """
    # 初始化统计结构：{原关键词: {'promo': 0, 'assist': 0}}
    stats = {kw: {'promo': 0, 'assist': 0} for kw in keywords}
    
    # 建立清洗后的映射表: normalized -> list of original keywords
    # 因为用户可能输入了两个只是大小写不同的词，我们需要都统计到对应的原词上
    # 但为了简单，我们假设用户输入的列表是唯一的，或者我们只匹配第一个
    # 更稳妥的做法：
    norm_map = []
    for kw in keywords:
        norm_map.append((kw, normalize_text(kw))) # [(原词, 清洗词), ...]

    # 转换为 UTC 时间 (Telethon 使用 UTC)
    utc_start = start_time.astimezone(timezone.utc)
    utc_end = end_time.astimezone(timezone.utc)
    
    logger.info(f"📊 [Stats] 启动清洗统计: {start_time} - {end_time} (BJ)")
    
    # 遍历所有目标群组 (硬编码的列表)
    for chat_id in ALL_TARGET_GROUPS:
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
                
                # 检查关键词 (使用清洗后的版本进行比对)
                for original_kw, kw_norm in norm_map:
                    if kw_norm in content_norm:
                        stats[original_kw][category] += 1
                        break # 一条消息只统计第一个命中的关键词
                        
        except Exception as e:
            logger.error(f"[Stats] 群组 {chat_id} 扫描失败: {e}")
            
    return stats

def init_stats_blueprint(app, client, bot_loop, _unused_args=None):
    """
    初始化 Flask 路由
    """
    
    @app.route('/tool/work_stats', methods=['GET', 'POST'])
    def work_stats_view():
        day_input = ""
        keywords_text = DEFAULT_KEYWORDS # 默认值
        results = None
        error = None
        total_hits = 0

        if request.method == 'POST':
            try:
                # 1. 解析日期
                day_input = request.form.get('day')
                # 2. 获取用户提交的关键词 (允许修改)
                raw_keywords = request.form.get('keywords', '')
                if raw_keywords.strip():
                    keywords_text = raw_keywords
                
                now = datetime.now(BJ_TZ)
                try:
                    target_day = int(day_input)
                    start_time = now.replace(day=target_day, hour=0, minute=0, second=0, microsecond=0)
                    end_time = now.replace(day=target_day, hour=23, minute=59, second=59, microsecond=999999)
                except ValueError:
                    raise ValueError("日期格式错误，请输入数字")

                # 解析关键词列表 (按行分割)
                keywords_list = [line.strip() for line in keywords_text.splitlines() if line.strip()]

                if not bot_loop or not client:
                    raise ValueError("Bot 未就绪")

                # 3. 执行扫描
                future = asyncio.run_coroutine_threadsafe(
                    perform_scan(client, start_time, end_time, keywords_list),
                    bot_loop
                )
                
                # 等待结果 (超时 180秒)
                stats_map = future.result(timeout=180)

                # 4. 格式化结果用于模板显示
                results = []
                for kw in keywords_list:
                    data = stats_map.get(kw, {'promo': 0, 'assist': 0})
                    p_count = data['promo']
                    a_count = data['assist']
                    
                    results.append({
                        'kw': kw,
                        'promo': p_count,
                        'assist': a_count
                    })
                    total_hits += (p_count + a_count)

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
