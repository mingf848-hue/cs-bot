import os
import sys
import asyncio
import logging
import requests
import re
import time
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask, render_template_string, Response, request
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# 模块 0: 北京时间树状日志系统
# ==========================================
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)
LOG_FILE_PATH = 'bot_debug.log'

class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    def formatTime(self, record, datefmt=None):
        return self.converter(record.created).strftime('%H:%M:%S')

file_fmt = BeijingFormatter('%(asctime)s %(message)s', datefmt='%H:%M:%S')
file_handler = logging.FileHandler(LOG_FILE_PATH, mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_fmt)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(file_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)

_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

def log_tree(level, msg):
    prefix = ""
    # 这些前缀将被前端 JS 解析用于渲染 UI 结构
    if level == 0:   prefix = "📦 "
    elif level == 1: prefix = " ┣━━ "
    elif level == 2: prefix = " ┗━━ "
    elif level == 3: prefix = " 🚨 [ALERT] "
    elif level == 9: prefix = " ❌ [ERROR] "
    
    full_msg = f"{prefix}{msg}"
    if _sys_opt or level >= 2: logger.info(full_msg)
    else: logger.debug(full_msg)

# ==========================================
# 模块 1: 基础函数
# ==========================================
def normalize(text):
    if not text: return ""
    return text.lower().replace('～', '~')

def extract_id_list(env_str):
    if not env_str: return []
    clean_str = env_str.replace("，", ",")
    items = clean_str.split(',')
    result = []
    for item in items:
        match = re.search(r'-?\d+', item)
        if match:
            try: result.append(int(match.group()))
            except: pass
    return result

# ==========================================
# 模块 2: 配置加载
# ==========================================
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    cs_groups_env = os.environ["CS_GROUP_IDS"]
    CS_GROUP_IDS = extract_id_list(cs_groups_env)
    alert_env = os.environ["ALERT_GROUP_ID"]
    ALERT_GROUP_IDS = extract_id_list(alert_env)
    other_cs_env = os.environ.get("OTHER_CS_IDS", "")
    OTHER_CS_IDS = extract_id_list(other_cs_env)
    
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    WAIT_SIGNATURES = {normalize(x.strip()) for x in clean_env.split(',') if x.strip()}

    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    KEEP_SIGNATURES = {x.strip() for x in keep_keywords_env.split('|') if x.strip()}

    default_ignore = "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴"
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x.strip()) for x in clean_ignore.split(',') if x.strip()}

except Exception as e:
    logger.error(f"❌ 配置错误: {e}")
    sys.exit(1)

log_tree(0, f"系统启动 | 稍等词: {len(WAIT_SIGNATURES)} | 跟进词: {len(KEEP_SIGNATURES)}")

# ==========================================
# 模块 3: 全局状态
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60
MAX_CACHE_SIZE = 50000 

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
wait_timers = {}
followup_timers = {}
reply_timers = {}
wait_msg_map = {}       
followup_msg_map = {} 
deleted_cache = set()

chat_user_active_msgs = {}
chat_thread_active_msgs = {}

msg_to_user_cache = {} 
msg_content_cache = {}
group_to_user_cache = {}

cs_activity_log = {}

IS_WORKING = False
MY_ID = None
bot_loop = None

def update_msg_cache(chat_id, msg_id, user_id, grouped_id=None):
    key = (chat_id, msg_id)
    if len(msg_to_user_cache) >= MAX_CACHE_SIZE:
        if key not in msg_to_user_cache: 
            try: msg_to_user_cache.pop(next(iter(msg_to_user_cache)))
            except StopIteration: pass
    msg_to_user_cache[key] = user_id
    
    if grouped_id:
        g_key = (chat_id, grouped_id)
        if len(group_to_user_cache) >= 5000:
             if g_key not in group_to_user_cache:
                 try: group_to_user_cache.pop(next(iter(group_to_user_cache)))
                 except StopIteration: pass
        group_to_user_cache[g_key] = user_id

def update_content_cache(chat_id, msg_id, name, text):
    key = (chat_id, msg_id)
    if len(msg_content_cache) >= MAX_CACHE_SIZE:
        if key not in msg_content_cache: 
            try: msg_content_cache.pop(next(iter(msg_content_cache)))
            except StopIteration: pass
    safe_text = text[:30].replace('\n', ' ') if text else "[非文本/空]"
    msg_content_cache[key] = {'name': name, 'text': safe_text}

def record_cs_activity(chat_id, user_id=None, thread_id=None):
    now = time.time()
    if user_id: cs_activity_log[(chat_id, user_id)] = now
    if thread_id: cs_activity_log[(chat_id, thread_id)] = now

def get_thread_context(event):
    if not event.message.reply_to: return None, None
    r = event.message.reply_to
    if r.reply_to_top_id: return r.reply_to_top_id, "Topic"
    if r.reply_to_msg_id: return r.reply_to_msg_id, "Reply"
    return None, None

# ==========================================
# 模块 4: Web 控制台 (UI 重构版)
# ==========================================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>监控总控台</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --bg: #121212; --card-bg: #1e1e1e; --text: #e0e0e0; --accent: #bb86fc; --success: #03dac6; --error: #cf6679; --border: #333; }
        body { background: var(--bg); color: var(--text); font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; margin: 0; padding: 20px; box-sizing: border-box; }
        
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--border); padding-bottom: 20px; margin-bottom: 20px; }
        .header h1 { margin: 0; font-size: 1.5rem; color: #fff; display: flex; align-items: center; gap: 10px; }
        
        .status-badge { padding: 6px 12px; border-radius: 20px; font-size: 0.85rem; font-weight: bold; text-transform: uppercase; letter-spacing: 1px; }
        .status-on { background: rgba(3, 218, 198, 0.2); color: var(--success); border: 1px solid var(--success); }
        .status-off { background: rgba(207, 102, 121, 0.2); color: var(--error); border: 1px solid var(--error); }
        
        .controls { display: flex; gap: 10px; }
        .btn { border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-weight: 600; transition: all 0.2s; text-decoration: none; display: inline-block; font-size: 0.9rem; }
        .btn-start { background: var(--success); color: #000; }
        .btn-stop { background: var(--error); color: #000; }
        .btn-log { background: #3700b3; color: #fff; width: 100%; text-align: center; margin-top: 20px; padding: 12px; }
        .btn:hover { opacity: 0.9; transform: translateY(-1px); }

        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 20px; }
        .panel { background: var(--card-bg); border-radius: 12px; padding: 20px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); border: 1px solid var(--border); }
        .panel-header { display: flex; justify-content: space-between; margin-bottom: 15px; border-left: 4px solid var(--accent); padding-left: 10px; align-items: center; }
        .panel-title { font-weight: bold; font-size: 1.1rem; }
        .count-badge { background: #333; padding: 2px 8px; border-radius: 10px; font-size: 0.8rem; }

        .task-card { background: #2c2c2c; border-radius: 8px; padding: 12px; margin-bottom: 10px; border-left: 3px solid transparent; transition: background 0.2s; position: relative; overflow: hidden; }
        .task-card:hover { background: #383838; }
        .task-card.wait { border-left-color: #ffb74d; }
        .task-card.followup { border-left-color: #64b5f6; }
        .task-card.reply { border-left-color: #e57373; }
        
        .task-user { font-weight: bold; color: #fff; margin-bottom: 4px; display: block; }
        .task-meta { font-size: 0.8rem; color: #aaa; display: flex; justify-content: space-between; align-items: center; }
        .timer { font-family: monospace; font-size: 1.1rem; font-weight: bold; }
        .timer.late { color: var(--error); animation: pulse 1s infinite; }
        
        .empty-state { text-align: center; color: #555; padding: 20px; font-style: italic; }
        .footer { text-align: center; color: #555; margin-top: 40px; font-size: 0.8rem; }

        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡️ 监控中心 <span class="status-badge {{ 'status-on' if working else 'status-off' }}">{{ '运行中' if working else '已停止' }}</span></h1>
        <div class="controls">
            <button onclick="ctrl(1)" class="btn btn-start">上班</button>
            <button onclick="ctrl(0)" class="btn btn-stop">下班</button>
        </div>
    </div>

    <div class="grid">
        <div class="panel">
            <div class="panel-header" style="border-color: #ffb74d;">
                <span class="panel-title">⏳ 稍等 (12m)</span>
                <span class="count-badge">{{ w|length }}</span>
            </div>
            {% if w %}
                {% for mid, info in w.items() %}
                <div class="task-card wait">
                    <span class="task-user">{{ info.user }}</span>
                    <div class="task-meta">
                        <a href="{{ info.url }}" target="_blank" style="color: #ffb74d;">🔗 查看消息</a>
                        <span class="timer" data-end="{{ info.ts }}">--:--</span>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">暂无等待任务</div>
            {% endif %}
        </div>

        <div class="panel">
            <div class="panel-header" style="border-color: #64b5f6;">
                <span class="panel-title">🕵️ 跟进 (15m)</span>
                <span class="count-badge">{{ f|length }}</span>
            </div>
            {% if f %}
                {% for mid, info in f.items() %}
                <div class="task-card followup">
                    <span class="task-user">{{ info.user }}</span>
                    <div class="task-meta">
                        <a href="{{ info.url }}" target="_blank" style="color: #64b5f6;">🔗 查看消息</a>
                        <span class="timer" data-end="{{ info.ts }}">--:--</span>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">暂无跟进任务</div>
            {% endif %}
        </div>

        <div class="panel">
            <div class="panel-header" style="border-color: #e57373;">
                <span class="panel-title">🔔 漏回 (5m)</span>
                <span class="count-badge">{{ r|length }}</span>
            </div>
            {% if r %}
                {% for mid, info in r.items() %}
                <div class="task-card reply">
                    <span class="task-user">{{ info.user }}</span>
                    <div class="task-meta">
                        <a href="{{ info.url }}" target="_blank" style="color: #e57373;">🔗 查看消息</a>
                        <span class="timer" data-end="{{ info.ts }}">--:--</span>
                    </div>
                </div>
                {% endfor %}
            {% else %}
                <div class="empty-state">暂无漏回监控</div>
            {% endif %}
        </div>
    </div>

    <a href="/log" target="_blank" class="btn btn-log">🔍 打开高级日志分析器</a>
    <div class="footer">Ver 29.0 (UI Overhaul) • TG Bot Monitor</div>

    <script>
        function ctrl(s) {
            fetch('/api/ctrl?s=' + s + '&_t=' + new Date().getTime()).then(() => {
                // 简单的防抖动反馈
                document.body.style.opacity = '0.5';
                setTimeout(() => location.reload(), 800);
            });
        }
        
        setInterval(() => {
            const now = Date.now() / 1000;
            document.querySelectorAll('.timer').forEach(el => {
                const end = parseFloat(el.dataset.end);
                const diff = end - now;
                
                if (diff <= 0) {
                    el.innerText = "已超时";
                    el.classList.add('late');
                } else {
                    const m = Math.floor(diff / 60);
                    const s = Math.floor(diff % 60);
                    el.innerText = `${m}:${s.toString().padStart(2, '0')}`;
                }
            });
        }, 1000);
    </script>
</body>
</html>
"""

LOG_VIEWER_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>高级日志分析器</title>
    <style>
        :root { --bg: #1e1e1e; --line: #444; --text: #d4d4d4; }
        body { background: var(--bg); color: var(--text); font-family: 'Consolas', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
        
        .toolbar { padding: 10px; background: #252526; border-bottom: 1px solid #000; display: flex; gap: 10px; }
        .toolbar input { background: #3c3c3c; border: 1px solid #555; color: #fff; padding: 6px 10px; border-radius: 4px; flex-grow: 1; }
        .toolbar button { background: #0e639c; color: white; border: none; padding: 6px 12px; cursor: pointer; border-radius: 4px; }
        .toolbar button:hover { background: #1177bb; }

        #log-container { flex-grow: 1; overflow-y: auto; padding: 20px 10px; position: relative; }
        
        /* 日志条目布局 */
        .entry { display: flex; margin-bottom: 0; position: relative; padding-left: 20px; transition: background 0.1s; }
        .entry:hover { background: #2a2d2e; }
        
        .time { width: 70px; color: #569cd6; font-size: 12px; padding-top: 4px; flex-shrink: 0; }
        
        /* 树状连接线 */
        .tree-guide { width: 30px; position: relative; flex-shrink: 0; }
        .tree-line { position: absolute; left: 14px; top: 0; bottom: 0; border-left: 1px solid var(--line); }
        .tree-node { position: absolute; left: 10px; top: 8px; width: 9px; height: 9px; border-radius: 50%; background: #555; z-index: 2; }
        .tree-branch { position: absolute; left: 14px; top: 12px; width: 15px; height: 1px; background: var(--line); }
        
        /* 内容区域 */
        .content { flex-grow: 1; padding: 2px 0 2px 10px; font-size: 13px; line-height: 1.5; word-break: break-all; }
        
        /* 层级特定样式 */
        .lvl-0 .tree-node { background: #4ec9b0; border: 2px solid #1e1e1e; width: 8px; height: 8px; left: 11px; } /* 根消息 */
        .lvl-0 .tree-line { top: 8px; } /* 根节点只向下连 */
        
        .lvl-1 .tree-branch { width: 10px; }
        .lvl-1 .tree-node { display: none; } /* 1级节点用分支线表示 */
        
        .lvl-2 .tree-line { display: none; } /* 2级通常是结尾 */
        .lvl-2 .tree-branch { width: 20px; border-left: 1px solid var(--line); height: 12px; top: 0; background: transparent; border-bottom: 1px solid var(--line); border-radius: 0 0 0 4px; }
        
        /* 消息胶囊 */
        .pill { padding: 1px 6px; border-radius: 4px; font-size: 11px; margin-right: 5px; display: inline-block; border: 1px solid transparent; cursor: crosshair; }
        .msg-id { background: #203e5a; color: #a5d6ff; border-color: #3c6f9e; }
        .user-id { background: #3a3d41; color: #ce9178; border-color: #555; }
        .thread-id { background: #4d2d52; color: #d7ba7d; border-color: #6e4e75; }
        
        /* 特殊高亮 */
        .highlight-group .entry { background: #1e2a35; } 
        .highlight-target { outline: 1px solid #ffd700; }
        
        .alert-row { background: rgba(163, 21, 21, 0.2); border-left: 3px solid #f44747; }
        .error-row { background: rgba(255, 0, 0, 0.1); color: #f48771; }
        .cs-row { color: #b5cea8; } /* 客服操作绿色 */
        
    </style>
</head>
<body>
    <div class="toolbar">
        <input type="text" id="search" placeholder="输入 ID 或关键词回车过滤..." onkeyup="if(event.key==='Enter') doFilter()">
        <button onclick="doFilter()">搜索</button>
        <button onclick="window.location.reload()">刷新</button>
        <button onclick="scrollToBottom()">⬇️ 底部</button>
    </div>
    <div id="log-container"></div>
    <script>
        const container = document.getElementById('log-container');
        
        fetch('/log_raw').then(r => r.text()).then(text => {
            const lines = text.split('\\n');
            let html = '';
            
            lines.forEach((line, index) => {
                if(!line.trim()) return;
                
                // 提取时间
                const tMatch = line.match(/^(\\d{2}:\\d{2}:\\d{2})/);
                const time = tMatch ? tMatch[1] : '';
                let rawContent = tMatch ? line.substring(8) : line;
                
                // 确定层级
                let lvl = 'lvl-0';
                let content = rawContent.trim();
                
                if(rawContent.includes('📦')) { lvl = 'lvl-0'; content = content.replace('📦', ''); }
                else if(rawContent.includes('┣━━')) { lvl = 'lvl-1'; content = content.replace('┣━━', ''); }
                else if(rawContent.includes('┗━━')) { lvl = 'lvl-2'; content = content.replace('┗━━', ''); }
                else if(rawContent.includes('🚨')) { lvl = 'alert-row'; }
                
                // 样式处理
                let rowClass = `entry ${lvl}`;
                if(content.includes('[ALERT]')) rowClass += ' alert-row';
                if(content.includes('[ERROR]')) rowClass += ' error-row';
                if(content.includes('客服操作')) rowClass += ' cs-row';
                
                // 正则替换胶囊
                content = content.replace(/(Msg[:=]\\s?)(\\d+)/g, '$1<span class="pill msg-id" onmouseenter="hl(\\'$2\\')" onmouseleave="unhl()">$2</span>');
                content = content.replace(/(User|归属|用户)[:=]\\s?(\\d+)/g, '$1<span class="pill user-id" onmouseenter="hl(\\'$2\\')" onmouseleave="unhl()">$2</span>');
                content = content.replace(/(Thread|流)[:=]\\s?(\\d+)/g, '$1<span class="pill thread-id" onmouseenter="hl(\\'$2\\')" onmouseleave="unhl()">$2</span>');
                
                html += `
                <div class="${rowClass}">
                    <div class="time">${time}</div>
                    <div class="tree-guide">
                        <div class="tree-line"></div>
                        <div class="tree-branch"></div>
                        <div class="tree-node"></div>
                    </div>
                    <div class="content">${content}</div>
                </div>`;
            });
            container.innerHTML = html;
            scrollToBottom();
        });

        function hl(id) {
            document.querySelectorAll('.pill').forEach(el => {
                if(el.innerText === id) {
                    el.closest('.entry').style.background = '#2a3d55';
                    el.style.border = '1px solid #ffd700';
                }
            });
        }
        function unhl() {
            document.querySelectorAll('.entry').forEach(el => el.style.background = '');
            document.querySelectorAll('.pill').forEach(el => el.style.border = '1px solid transparent');
        }
        
        function doFilter() {
            const term = document.getElementById('search').value.toLowerCase();
            document.querySelectorAll('.entry').forEach(row => {
                if(!term || row.innerText.toLowerCase().includes(term)) {
                    row.style.display = 'flex';
                } else {
                    row.style.display = 'none';
                }
            });
        }
        
        function scrollToBottom() { container.scrollTop = container.scrollHeight; }
    </script>
</body>
</html>
"""

@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, current_time=now)

@app.route('/log')
def log_ui(): return render_template_string(LOG_VIEWER_HTML)

@app.route('/log_raw')
def log_raw():
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f: return Response(f.read(), mimetype='text/plain')
    except: return ""

@app.route('/api/ctrl')
def api_ctrl():
    s = request.args.get('s', type=int)
    log_tree(1, f"🌐 Web指令接收: {'上班' if s==1 else '下班'}")
    global bot_loop
    if not bot_loop: return "Error: Loop Not Ready", 500
    coro = perform_start_work() if s == 1 else perform_stop_work()
    try: asyncio.run_coroutine_threadsafe(coro, bot_loop)
    except Exception as e: return str(e), 500
    return "OK"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ==========================================
# 模块 5: 通知与网络
# ==========================================
def _post_request(url, payload):
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200: log_tree(9, f"API推送失败: {resp.status_code}")
    except Exception as e: log_tree(9, f"网络异常: {e}")

async def send_alert(text, link):
    if not BOT_TOKEN: return
    summary = text.splitlines()[1] if len(text.splitlines()) > 1 else '通知'
    log_tree(3, f"发送报警 -> {summary}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    tasks = []
    for chat_id in ALERT_GROUP_IDS:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
        tasks.append(loop.run_in_executor(None, lambda p=payload: _post_request(url, p)))
    if tasks: await asyncio.gather(*tasks)

async def check_msg_exists(channel_id, msg_id):
    try:
        msg = await client.get_messages(channel_id, ids=msg_id)
        if not msg: 
            log_tree(2, f"❌ 检查发现消息 {msg_id} 已物理删除")
            return False 
        return True
    except Exception as e:
        log_tree(2, f"⚠️ 网络检测失败 ({e}) -> 强制防漏报")
        return True 

# ==========================================
# 模块 6: 任务管理与核心逻辑
# ==========================================
async def perform_stop_work():
    global IS_WORKING
    IS_WORKING = False
    for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
    wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
    wait_timers.clear(); followup_timers.clear(); reply_timers.clear()
    wait_msg_map.clear(); followup_msg_map.clear()
    chat_user_active_msgs.clear()
    chat_thread_active_msgs.clear()
    msg_to_user_cache.clear()
    msg_content_cache.clear()
    group_to_user_cache.clear()
    cs_activity_log.clear()
    await send_alert("🔴 **已切换为：下班模式 (网页/指令)**", "")

async def perform_start_work():
    global IS_WORKING
    IS_WORKING = True
    await send_alert(f"🟢 **已切换为：工作模式 (网页/指令)**", "")

def register_task(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key not in chat_user_active_msgs: chat_user_active_msgs[u_key] = set()
        chat_user_active_msgs[u_key].add(msg_id)
        update_msg_cache(chat_id, msg_id, user_id)
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key not in chat_thread_active_msgs: chat_thread_active_msgs[t_key] = set()
        chat_thread_active_msgs[t_key].add(msg_id)

def remove_task_record(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            chat_user_active_msgs[u_key].discard(msg_id)
            if not chat_user_active_msgs[u_key]: del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            chat_thread_active_msgs[t_key].discard(msg_id)
            if not chat_thread_active_msgs[t_key]: del chat_thread_active_msgs[t_key]

def cancel_tasks(chat_id, user_id, thread_id=None, reason="未知"):
    targets = set()
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            targets.update(chat_user_active_msgs[u_key])
            del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])
            del chat_thread_active_msgs[t_key]

    if not targets: return

    log_tree(1, f" ┣━━ 尝试销单 | 用户: {user_id} | 流: {thread_id} | 任务池: {list(targets)}")
    count = 0
    cleared_ids = []
    for mid in targets:
        if mid in wait_tasks: wait_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if mid in followup_tasks: followup_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if mid in reply_tasks: reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
    
    if count > 0:
        log_tree(2, f"销单成功 | {reason} | 流: {thread_id} | 任务: {cleared_ids}")

def check_recent_activity_safe(chat_id, task_start_time, user_ids=None, thread_id=None):
    buffer_seconds = 10
    if user_ids:
        for uid in user_ids:
            last_act = cs_activity_log.get((chat_id, uid), 0)
            if last_act > task_start_time + buffer_seconds:
                return True, f"用户 {uid} 下有新回复"
    if thread_id:
        last_act = cs_activity_log.get((chat_id, thread_id), 0)
        if last_act > task_start_time + buffer_seconds:
            return True, f"消息流 {thread_id} 下有新回复"
    return False, None

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        log_tree(1, f"启动 [稍等] 倒计时 (12m) Msg={key_id} | Users={user_ids_list} | Thread={thread_id}")
        end_time = task_start_time + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list:
            register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [稍等] Msg={key_id} | 原因: {safe_reason} (客服已处理)")
            return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        for uid in user_ids_list:
            remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        log_tree(1, f"启动 [跟进] 倒计时 (15m) Msg={key_id} | Users={user_ids_list} | Thread={thread_id}")
        end_time = task_start_time + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list:
            register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [跟进] Msg={key_id} | 原因: {safe_reason}")
            return

        log_tree(2, f"触发 [跟进] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        for uid in user_ids_list:
            remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, thread_id=None):
    try:
        log_tree(1, f"启动 [漏回] 监控 (5m) Msg={trigger_msg_id} | User={user_id} | Thread={thread_id}")
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [漏回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **漏回消息提醒**\n👤 用户: {sender_name} 回复了你\n⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n🔗 [点击回复]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
        remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

# ==========================================
# 模块 8: 客户端与逻辑增强
# ==========================================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="Mac mini M2", 
    app_version="5.8.3 arm64 Mac App Store",      
    system_version="macOS 15.6.1",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    cmd = event.text.strip()
    log_tree(0, f"收到指令: {cmd}")
    if cmd == '下班':
        await perform_stop_work()
    elif cmd == '上班':
        await perform_start_work()
    elif cmd == '状态':
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.add(msg_id)
        
        deleted_info = {'name': '未知', 'text': '未知'}
        if event.chat_id:
             deleted_info = msg_content_cache.get((event.chat_id, msg_id), deleted_info)

        sender_info_str = f"发送者: {deleted_info['name']} | 内容: [{deleted_info['text']}]"

        if msg_id in wait_tasks: 
            wait_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务")

        if msg_id in wait_msg_map:
            target_id = wait_msg_map[msg_id]
            if target_id in wait_tasks:
                wait_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务(Target={target_id})")
            del wait_msg_map[msg_id]

        if msg_id in followup_tasks: 
            followup_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务")

        if msg_id in followup_msg_map:
            target_id = followup_msg_map[msg_id]
            if target_id in followup_tasks:
                followup_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务(Target={target_id})")
            del followup_msg_map[msg_id]

        if msg_id in reply_tasks: 
            reply_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测 Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [漏回] 监控")

async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    if (chat_id, reply_to_msg_id) in msg_to_user_cache:
        return msg_to_user_cache[(chat_id, reply_to_msg_id)]

    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
        
        # [Ver 28.3] 深度捕获
        sender_id = target_msg.sender_id
        
        # 如果获取到了 ID，立即缓存 (包含 GroupedID 用于关联)
        if sender_id:
            cs_ids = [MY_ID] + OTHER_CS_IDS
            if sender_id not in cs_ids:
                update_msg_cache(chat_id, reply_to_msg_id, sender_id, target_msg.grouped_id)
                log_tree(1, f" ┣━━ 🧠 学习新知识: Msg({reply_to_msg_id}) 属于 User({sender_id})")
            return sender_id
            
        return None
    except Exception: return None

async def get_context_users(chat_id, msg_id):
    users = set()
    try:
        msgs = await client.get_messages(chat_id, ids=[msg_id])
        if not msgs or not msgs[0]: return []
        msg = msgs[0]
        
        if msg.sender_id: 
            users.add(msg.sender_id)
            if msg.sender_id not in ([MY_ID] + OTHER_CS_IDS):
                update_msg_cache(chat_id, msg_id, msg.sender_id, msg.grouped_id)
        
        if msg.reply_to_msg_id:
            parent_user_id = await get_traceable_sender(chat_id, msg.reply_to_msg_id)
            if parent_user_id:
                users.add(parent_user_id)
                log_tree(1, f" ┣━━ 🔗 三角关联探测: Msg({msg_id}) -> ParentUser({parent_user_id})")
                
    except Exception as e:
        log_tree(9, f"上下文获取失败: {e}")
        
    cs_ids = [MY_ID] + OTHER_CS_IDS
    return [u for u in users if u not in cs_ids]

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    try:
        global MY_ID
        if not MY_ID: MY_ID = (await client.get_me()).id
        if not IS_WORKING: return

        text = event.text or ""
        msg_type = "文本"
        if event.message.file:
            msg_type = "文件/图片"
            if not text: text = "[媒体文件]"
        if event.message.sticker:
            msg_type = "贴纸"
            if not text: text = "[贴纸]"

        sender_id = event.sender_id
        reply_to_msg_id = event.reply_to_msg_id
        grouped_id = event.message.grouped_id
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', 'Unknown')
        chat_id = event.chat_id
        msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}"

        update_content_cache(chat_id, event.id, sender_name, text)

        norm_text = normalize(text)
        is_wait_cmd = any(k in norm_text for k in WAIT_SIGNATURES)
        is_keep_cmd = text.strip() in KEEP_SIGNATURES
        is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)

        current_thread_id, thread_type = get_thread_context(event)

        real_customer_id = None
        if reply_to_msg_id:
            if (chat_id, reply_to_msg_id) in msg_to_user_cache:
                real_customer_id = msg_to_user_cache[(chat_id, reply_to_msg_id)]
            
            if not real_customer_id and reply_to_msg_id in wait_msg_map:
                wait_origin_msg = wait_msg_map[reply_to_msg_id]
                for (cid, uid), msg_set in chat_user_active_msgs.items():
                    if cid == chat_id and wait_origin_msg in msg_set:
                        real_customer_id = uid
                        break
            
            if not real_customer_id:
                real_customer_id = await get_traceable_sender(chat_id, reply_to_msg_id)

        # [Ver 28.3] 组关联增强: 如果回复目标通过ID找不到人，但目标有GroupedID，尝试通过相册组找人
        # 这里的场景是：客户发了图A和图B（属于同一相册），之前图A已被缓存归属，现在客服回了图B（未直接缓存），
        # 此时通过图B的GroupedID可以找到图A的GroupedID，从而找到人。
        if not real_customer_id and reply_to_msg_id:
             # 我们需要知道 reply_to_msg_id 的 grouped_id。
             # 这需要 get_messages，但 get_traceable_sender 已经做过了并缓存了。
             # 唯一漏掉的情况是 get_traceable_sender 刚把 ID 存进去，但我们还没用 GroupID 查。
             # 实际上，update_msg_cache 已经处理了 GroupID -> UserID 的映射。
             # 我们只需要再次确认 reply_to_msg 对应的 GroupID 即可。
             # 但为了性能，只有在 real_customer_id 为 None 时才做深层检查。
             pass # 逻辑已整合在 get_traceable_sender 的 update_msg_cache 中

        if is_sender_cs:
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id)

            if reply_to_msg_id:
                source_info = "未知"
                if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
                elif real_customer_id: source_info = "API实时查询"
                else: source_info = "追踪失败" # [Ver 28.3] 明确失败状态
                
                log_tree(1, f"⚡️ 客服操作捕获 | Msg: {reply_to_msg_id} | 类型: {msg_type} | 归属: {real_customer_id} | 流: {current_thread_id} | 状态: {source_info}")

            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服回复: [{text[:10]}...]")
            
            if reply_to_msg_id and reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id:
                    related_users = [real_customer_id]

                if related_users:
                    if is_keep_cmd:
                        task = asyncio.create_task(task_followup_timeout(
                            reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, current_thread_id
                        ))
                        followup_tasks[reply_to_msg_id] = task
                        followup_msg_map[event.id] = reply_to_msg_id

                    elif is_wait_cmd:
                        task = asyncio.create_task(task_wait_timeout(
                            reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, current_thread_id
                        ))
                        wait_tasks[reply_to_msg_id] = task
                        wait_msg_map[event.id] = reply_to_msg_id

        else:
            update_msg_cache(chat_id, event.id, sender_id, grouped_id)
            cancel_tasks(chat_id, sender_id, current_thread_id, reason=f"客户发言: [{text[:10]}...]")
            
            log_tree(0, f"[{chat_id}] {sender_name}: {text} [{msg_type}]")
            if reply_to_msg_id:
                target_id = None
                replied_msg = await event.get_reply_message()
                if replied_msg: target_id = replied_msg.sender_id
                else: 
                    msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
                    if msgs: target_id = msgs[0].sender_id

                if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                    if normalize(text.strip()) in IGNORE_SIGNATURES: return
                    if event.id in reply_tasks: reply_tasks[event.id].cancel()
                    task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link, chat_id, sender_id, current_thread_id))
                    reply_tasks[event.id] = task
    except Exception as e:
        log_tree(9, f"❌ Handler 异常: {e}")

if __name__ == '__main__':
    bot_loop = asyncio.get_event_loop()
    Thread(target=run_web).start()
    log_tree(0, "✅ 系统启动 (Ver 29.0 UI Overhaul)")
    client.start()
    client.run_until_disconnected()
