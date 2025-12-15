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
# 模块 0: 核心配置与日志系统
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
    """可视化日志树"""
    prefix = ""
    if level == 0:   prefix = "📦 "       # 入口
    elif level == 1: prefix = " ┣━━ "     # 逻辑
    elif level == 2: prefix = " ┗━━ "     # 结果
    elif level == 3: prefix = " 🚨 [ALERT] "
    elif level == 9: prefix = " ❌ [ERROR] "
    
    full_msg = f"{prefix}{msg}"
    if _sys_opt or level >= 2: logger.info(full_msg)
    else: logger.debug(full_msg)

# ==========================================
# 模块 1: 辅助工具
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
# 模块 2: 环境变量加载
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

    default_ignore = "好的,谢谢,收到,明白,好的谢谢,ok,thx,thanks,好的呢,好滴"
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x.strip()) for x in clean_ignore.split(',') if x.strip()}

except Exception as e:
    logger.error(f"❌ 配置错误: {e}")
    sys.exit(1)

log_tree(0, f"逻辑矩阵系统启动 | 客服数: {len(OTHER_CS_IDS)+1} | 稍等词: {len(WAIT_SIGNATURES)}")

# ==========================================
# 模块 3: 全局状态库
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60

# 任务句柄
wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}

# 前端展示数据
wait_timers = {}
followup_timers = {}
reply_timers = {}

# 关系映射
# 1. 消息归属缓存 (MsgID -> UserID)
msg_owner_cache = {} 
# 2. 用户活跃任务 (UserID -> Set[MsgID])
user_active_tasks = {}

IS_WORKING = False
MY_ID = None

# ==========================================
# 模块 4: Web 控制台
# ==========================================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>逻辑矩阵看板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5"> 
    <style>
        :root { --bg: #fff; --text: #333; --card: #f8f9fa; --border: #eee; --green: #28a745; --red: #dc3545; }
        body { background: var(--bg); color: var(--text); font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 1.4rem; }
        .tag { padding: 4px 10px; border-radius: 4px; color: #fff; font-weight: bold; font-size: 0.9rem; }
        .on { background: var(--green); } .off { background: var(--red); }
        .box { margin-bottom: 20px; }
        .title { font-weight: bold; border-left: 4px solid #333; padding-left: 8px; margin-bottom: 8px; color: #555; display: flex; justify-content: space-between; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 10px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .t { font-family: monospace; font-weight: bold; font-size: 1.1rem; color: #d63384; }
        .late { color: red; text-decoration: underline; }
        .empty { color: #999; text-align: center; font-style: italic; padding: 10px; }
        .btn { display: block; width: 100%; padding: 12px; background: #222; color: #fff; text-align: center; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🧠 逻辑矩阵监控</h1>
        <div class="tag {{ 'on' if working else 'off' }}">{{ 'WORKING' if working else 'STOPPED' }}</div>
    </div>
    {% for title, timers in [('⏳ 稍等 (12m)', w), ('🕵️ 跟进 (15m)', f), ('🔔 漏回 (5m)', r)] %}
    <div class="box">
        <div class="title"><span>{{ title }}</span><span>{{ timers|length }}</span></div>
        {% if timers %}
            {% for mid, info in timers.items() %}
            <div class="card">
                <div><b>{{ info.user }}</b><br><a href="{{ info.url }}" target="_blank" style="font-size:0.8rem">🔗跳转</a></div>
                <span class="t" data-end="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        {% else %}<div class="empty">无任务</div>{% endif %}
    </div>
    {% endfor %}
    <a href="/log" target="_blank" class="btn">🔍 打开逻辑日志</a>
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 28.0 (Logic Matrix)</div>
    <script>
        setInterval(() => {
            const now = Date.now() / 1000;
            document.querySelectorAll('.t').forEach(el => {
                const diff = parseFloat(el.dataset.end) - now;
                el.innerText = diff <= 0 ? "超时" : `${Math.floor(diff/60)}:${Math.floor(diff%60).toString().padStart(2,'0')}`;
                if(diff<=0) el.classList.add('late');
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
    <title>逻辑日志</title>
    <style>
        body { background: #1e1e1e; color: #d4d4d4; font-family: 'Consolas', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; }
        .toolbar { background: #252526; padding: 10px; display: flex; gap: 10px; border-bottom: 1px solid #333; }
        input { background: #3c3c3c; border: 1px solid #333; color: #fff; padding: 6px; flex-grow: 1; border-radius: 4px; }
        button { background: #0e639c; color: white; border: none; padding: 6px 12px; cursor: pointer; border-radius: 4px; }
        #log-container { flex-grow: 1; overflow-y: auto; padding: 15px; white-space: pre; font-size: 13px; line-height: 1.5; }
        .line { padding: 2px 0; }
        .highlight { background: #444; color: #fff; font-weight: bold; }
        .time { color: #569cd6; margin-right: 10px; }
        .tree { color: #808080; }
        .alert { color: #f44747; font-weight: bold; }
        .success { color: #6a9955; font-weight: bold; }
        .error { color: #f48771; font-weight: bold; background: #2f0d0d; }
        .action { color: #d7ba7d; font-weight: bold; }
    </style>
</head>
<body>
    <div class="toolbar">
        <input type="text" id="search" placeholder="🔍 搜索日志..." onkeyup="filterLogs()">
        <button onclick="window.location.reload()">🔄 刷新</button>
        <button onclick="scrollToBottom()">⬇️ 到底部</button>
    </div>
    <div id="log-container">加载中...</div>
    <script>
        const container = document.getElementById('log-container');
        fetch('/log_raw').then(r => r.text()).then(text => {
            const lines = text.split('\\n');
            let html = '';
            lines.forEach(line => {
                if(!line.trim()) return;
                let className = 'line';
                if(line.includes('[ALERT]')) className += ' alert';
                else if(line.includes('销单') || line.includes('解决')) className += ' success';
                else if(line.includes('[ERROR]')) className += ' error';
                else if(line.includes('逻辑判定')) className += ' action';
                
                const timeMatch = line.match(/^(\\d{2}:\\d{2}:\\d{2})/);
                let formattedLine = line;
                if(timeMatch) formattedLine = `<span class="time">${timeMatch[1]}</span>` + line.substring(8);
                formattedLine = formattedLine.replace(/(┣━━|┗━━)/g, '<span class="tree">$1</span>');
                html += `<div class="${className}">${formattedLine}</div>`;
            });
            container.innerHTML = html;
            scrollToBottom();
        });
        function filterLogs() {
            const term = document.getElementById('search').value.toLowerCase();
            const divs = container.getElementsByTagName('div');
            for(let div of divs) {
                const text = div.innerText.toLowerCase();
                if(text.includes(term)) { div.style.display = "block"; if(term.length > 2) div.classList.add('highlight'); } 
                else { div.style.display = "none"; div.classList.remove('highlight'); }
            }
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

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ==========================================
# 模块 5: 通知与网络 (Fail-Safe)
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
        log_tree(2, f"⚠️ 网络异常，启动防漏报机制 ({e})")
        return True 

# ==========================================
# 模块 6: 任务管理 (缓存+任务)
# ==========================================
def register_msg_owner(msg_id, user_id):
    """注册消息归属权 (本地户口本)"""
    if msg_id and user_id:
        msg_owner_cache[msg_id] = user_id

def add_monitor_task(chat_id, user_id, msg_id):
    """添加监控任务"""
    if not user_id: return
    if user_id not in user_active_tasks: user_active_tasks[user_id] = set()
    user_active_tasks[user_id].add(msg_id)
    register_msg_owner(msg_id, user_id)

def remove_monitor_task(user_id, msg_id):
    """移除监控任务"""
    if user_id in user_active_tasks:
        user_active_tasks[user_id].discard(msg_id)
        if not user_active_tasks[user_id]: del user_active_tasks[user_id]

def resolve_user_tasks(user_id, reason="逻辑完结"):
    """
    【智能销单核心】
    只有确定了 "这个用户的问题被解决了" 才会调用。
    """
    if not user_id: return
    
    count = 0
    cleared_ids = []
    
    if user_id in user_active_tasks:
        # 复制列表防止迭代时修改
        current_tasks = list(user_active_tasks[user_id])
        for mid in current_tasks:
            cancelled = False
            if mid in wait_tasks: wait_tasks[mid].cancel(); del wait_tasks[mid]; cancelled=True
            if mid in followup_tasks: followup_tasks[mid].cancel(); del followup_tasks[mid]; cancelled=True
            if mid in reply_tasks: reply_tasks[mid].cancel(); del reply_tasks[mid]; cancelled=True
            
            # 清理定时器数据
            if mid in wait_timers: del wait_timers[mid]
            if mid in followup_timers: del followup_timers[mid]
            if mid in reply_timers: del reply_timers[mid]
            
            if cancelled:
                count += 1
                cleared_ids.append(mid)
        
        # 清理用户记录
        del user_active_tasks[user_id]
        
    if count > 0:
        log_tree(2, f"✅ 销单成功 | {reason} | 用户: {user_id} | 涉及任务: {cleared_ids}")
    return count

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        log_tree(1, f"启动 [稍等] 倒计时 (12m) Msg={key_id}")
        end_time = time.time() + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_monitor_task(chat_id, customer_id, key_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        remove_monitor_task(customer_id, key_id)
        if key_id in wait_timers: del wait_timers[key_id]

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        log_tree(1, f"启动 [跟进] 倒计时 (15m) Msg={key_id}")
        end_time = time.time() + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_monitor_task(chat_id, customer_id, key_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        log_tree(2, f"触发 [跟进] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass
    finally:
        remove_monitor_task(customer_id, key_id)
        if key_id in followup_timers: del followup_timers[key_id]

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, customer_id):
    try:
        log_tree(1, f"启动 [漏回] 监控 (5m) Msg={trigger_msg_id}")
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link}
        # 这里的 customer_id 就是 sender_id
        add_monitor_task(0, customer_id, trigger_msg_id) 

        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [漏回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **漏回消息提醒**\n👤 用户: {sender_name} 回复了你\n⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n🔗 [点击回复]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        remove_monitor_task(customer_id, trigger_msg_id)
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]

# ==========================================
# 模块 8: 客户端初始化 (严格Mac伪装)
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
    global IS_WORKING
    cmd = event.text.strip()
    log_tree(0, f"收到指令: {cmd}")
    if cmd == '下班':
        IS_WORKING = False
        # 取消所有任务
        for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
        wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
        wait_timers.clear(); followup_timers.clear(); reply_timers.clear()
        user_active_tasks.clear()
        msg_owner_cache.clear()
        await send_alert("🔴 **已切换为：下班模式**", "")
    elif cmd == '上班':
        IS_WORKING = True
        await send_alert("🟢 **已切换为：工作模式**", "")
    elif cmd == '状态':
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        # 如果是监控中的消息被删了，撤销任务
        owner_id = msg_owner_cache.get(msg_id)
        if owner_id:
            # 尝试在三个任务池里找
            found = False
            if msg_id in wait_tasks: wait_tasks[msg_id].cancel(); found=True
            if msg_id in followup_tasks: followup_tasks[msg_id].cancel(); found=True
            if msg_id in reply_tasks: reply_tasks[msg_id].cancel(); found=True
            
            if found:
                log_tree(2, f"🗑️ 物理删除 Msg={msg_id} -> 撤销任务")
            
            # 清理缓存
            remove_monitor_task(owner_id, msg_id)

# ==========================================
# 模块 9: 智能溯源 (Deep Trace)
# ==========================================
async def resolve_target_user(chat_id, reply_to_msg_id, recursion_depth=0):
    """
    逻辑矩阵的核心：找出一条回复消息背后的【真实客户】。
    
    返回: (user_id, is_agent)
    """
    if recursion_depth > 3: return None, False
    
    # 1. 先查本地缓存 (最快，最稳)
    if reply_to_msg_id in msg_owner_cache:
        cached_user_id = msg_owner_cache[reply_to_msg_id]
        # 判断这个缓存的用户是不是客服
        is_agent_cached = (cached_user_id == MY_ID) or (cached_user_id in OTHER_CS_IDS)
        
        if not is_agent_cached:
            if _sys_opt: log_tree(1, f" ┣━━ 缓存命中: Msg {reply_to_msg_id} 是客户 {cached_user_id}")
            return cached_user_id, False
        else:
            # 如果缓存显示是客服，说明是客服引用客服，需要继续查原始消息
            # 但本地缓存存不了引用链，所以这里得去API查
            pass

    # 2. 查 Telegram API
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs or not msgs[0]: return None, False
        target_msg = msgs[0]
    except Exception:
        return None, False

    target_id = target_msg.sender_id
    is_target_agent = (target_id == MY_ID) or (target_id in OTHER_CS_IDS)

    # 存入缓存
    register_msg_owner(reply_to_msg_id, target_id)

    # 情况 A: 找到了客户
    if not is_target_agent:
        return target_id, False

    # 情况 B: 找到了客服 (且有引用) -> 递归
    if is_target_agent and target_msg.reply_to_msg_id:
        log_tree(1, f" ┣━━ 递归溯源: 客服引用了 Msg {target_msg.reply_to_msg_id}")
        return await resolve_target_user(chat_id, target_msg.reply_to_msg_id, recursion_depth + 1)

    # 情况 C: 找到了客服 (无引用) -> 这是一条孤立的客服消息
    return target_id, True

# ==========================================
# 模块 10: 逻辑矩阵主 Handler
# ==========================================
@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    global MY_ID
    if not MY_ID: MY_ID = (await client.get_me()).id
    if not IS_WORKING: return

    text = event.text or ""
    sender_id = event.sender_id
    reply_to_msg_id = event.reply_to_msg_id
    sender = await event.get_sender()
    sender_name = getattr(sender, 'first_name', 'Unknown')
    chat_id = event.chat_id
    msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}"

    # 0. 登记每一条消息 (构建本地户口本)
    register_msg_owner(event.id, sender_id)

    norm_text = normalize(text)
    is_wait_cmd = any(k in norm_text for k in WAIT_SIGNATURES)
    is_keep_cmd = text.strip() in KEEP_SIGNATURES
    
    # 身份判定
    is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)
    role_str = "客服" if is_sender_cs else "客户"

    # 日志记录
    log_tree(0, f"[{role_str}] {sender_name}: {text} (Reply: {reply_to_msg_id})")

    # ========================================
    #  逻辑矩阵核心 (The Logic Matrix)
    # ========================================
    
    target_customer_id = None
    
    if reply_to_msg_id:
        # 分析被回复的对象是谁
        target_uid, target_is_agent = await resolve_target_user(chat_id, reply_to_msg_id)
        
        if target_uid and not target_is_agent:
            target_customer_id = target_uid
            if _sys_opt: log_tree(1, f" ┣━━ 逻辑判定: 目标是客户 {target_customer_id}")

    # --- 场景 1: 客服发言 ---
    if is_sender_cs:
        # 1.1 客服回复了某人
        if reply_to_msg_id:
            # 动作 A: 只要客服回复了，且被回复的消息正在漏回监控中，直接清理该消息的任务 (精准打击)
            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]
                log_tree(2, f"✅ [精准销单] 客服回复了漏回消息 Msg={reply_to_msg_id}")

            # 动作 B: 如果确认回复的是客户，清理该客户名下所有任务 (连坐)
            if target_customer_id:
                resolve_user_tasks(target_customer_id, reason=f"客服 [{sender_name}] 已解决该客户问题")
        
        # 1.2 客服触发新任务 (稍等/跟进)
        # 注意: 即使没有 reply_to_msg_id，客服也可能是在直接发指令，但只有有对象时才能绑定
        if reply_to_msg_id:
            # 如果没找到客户(比如网络断了)，就用被回复消息的发送者ID兜底，防止报错
            task_target_id = target_customer_id if target_customer_id else 0 
            
            # 获取被回复内容摘要
            try:
                r_msg = await event.get_reply_message()
                r_text = (r_msg.text or "[文件]")[:30] if r_msg else "未知"
            except: r_text = "未知"

            if is_keep_cmd:
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, r_text, msg_link, event.id, chat_id, task_target_id
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, r_text, msg_link, event.id, chat_id, task_target_id
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

    # --- 场景 2: 客户发言 ---
    else:
        # 2.1 客户自己说话了 (不管是回复别人还是新发)
        # 逻辑: 客户一旦开口，说明他还在活跃。
        # 动作: 清除他自己 *之前* 的漏回监控 (防止旧消息 5分钟后误报)
        # 注意: 不清除客服给他的 "稍等/跟进" (因为客服还没回他)
        if event.id in reply_tasks: pass # 新消息还没建任务，不用管
        
        # 2.2 客户在追问 (回复了客服)
        if reply_to_msg_id:
            # 如果回复的是客服 -> 建立漏回监控
            # 判断 target 是否为客服
            target_uid, target_is_agent = await resolve_target_user(chat_id, reply_to_msg_id)
            if target_is_agent:
                if normalize(text.strip()) in IGNORE_SIGNATURES: return # 忽略 "谢谢" 等
                
                # 建立监控
                task = asyncio.create_task(task_reply_timeout(
                    event.id, sender_name, text[:50], msg_link, sender_id
                ))
                reply_tasks[event.id] = task
                add_monitor_task(chat_id, sender_id, event.id)
                return

        # 2.3 客户直接发消息 (无引用) -> 视为新提问，建立漏回监控
        if not reply_to_msg_id:
             if normalize(text.strip()) in IGNORE_SIGNATURES: return
             task = asyncio.create_task(task_reply_timeout(
                event.id, sender_name, text[:50], msg_link, sender_id
             ))
             reply_tasks[event.id] = task
             add_monitor_task(chat_id, sender_id, event.id)

if __name__ == '__main__':
    Thread(target=run_web).start()
    log_tree(0, "✅ 系统启动 (Ver 28.0 Logic Matrix)")
    client.start()
    client.run_until_disconnected()
