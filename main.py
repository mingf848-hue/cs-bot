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

    default_ignore = "好的,谢谢,收到,明白,好的谢谢,ok,thx,thanks,好的呢,好滴"
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
# [Ver 27.3] 缓存系统: (chat_id, msg_id) -> user_id
msg_to_user_cache = {} 

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
    <title>监控看板</title>
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
        <h1>⚡️ 实时监控</h1>
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
    <a href="/log" target="_blank" class="btn">🔍 打开日志分析器</a>
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 27.3 (Memory Fix)</div>
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
    <title>日志分析器</title>
    <style>
        body { background: #1e1e1e; color: #d4d4d4; font-family: 'Consolas', 'Monaco', monospace; margin: 0; display: flex; flex-direction: column; height: 100vh; }
        .toolbar { background: #252526; padding: 10px; display: flex; gap: 10px; border-bottom: 1px solid #333; }
        input { background: #3c3c3c; border: 1px solid #333; color: #fff; padding: 6px; flex-grow: 1; border-radius: 4px; }
        button { background: #0e639c; color: white; border: none; padding: 6px 12px; cursor: pointer; border-radius: 4px; }
        button:hover { background: #1177bb; }
        #log-container { flex-grow: 1; overflow-y: auto; padding: 15px; white-space: pre; font-size: 13px; line-height: 1.5; }
        .line { padding: 2px 0; }
        .highlight { background: #444; color: #fff; font-weight: bold; }
        .time { color: #569cd6; margin-right: 10px; }
        .tree { color: #808080; }
        .alert { color: #f44747; font-weight: bold; }
        .success { color: #6a9955; font-weight: bold; }
        .error { color: #f48771; font-weight: bold; background: #2f0d0d; }
        .delete { color: #d7ba7d; font-weight: bold; }
    </style>
</head>
<body>
    <div class="toolbar">
        <input type="text" id="search" placeholder="🔍 输入 ID / 关键词 / 时间 (回车搜索)..." onkeyup="filterLogs()">
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
                else if(line.includes('销单成功') || line.includes('任务已完成')) className += ' success';
                else if(line.includes('物理删除')) className += ' delete';
                else if(line.includes('[ERROR]') || line.includes('❌')) className += ' error';
                else if(line.includes('客服操作')) className += ' delete';
                else if(line.includes('新知识')) className += ' success'; /* 高亮新学到的缓存 */
                
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
# 模块 6: 任务管理
# ==========================================
def add_user_task(chat_id, user_id, msg_id):
    if not user_id: return
    key = (chat_id, user_id)
    if key not in chat_user_active_msgs: chat_user_active_msgs[key] = set()
    chat_user_active_msgs[key].add(msg_id)
    # [Ver 27.3] 即使是任务绑定，也顺便更新缓存
    msg_to_user_cache[(chat_id, msg_id)] = user_id

def remove_user_task(chat_id, user_id, msg_id):
    if not user_id: return
    key = (chat_id, user_id)
    if key in chat_user_active_msgs:
        chat_user_active_msgs[key].discard(msg_id)
        if not chat_user_active_msgs[key]: del chat_user_active_msgs[key]

def cancel_all_tasks_for_user(chat_id, user_id, reason="未知"):
    if not user_id: return
    key = (chat_id, user_id)
    if key in chat_user_active_msgs:
        active_msgs = list(chat_user_active_msgs[key])
        
        log_tree(1, f" ┣━━ 尝试销单: 用户 {user_id} | 当前任务池: {active_msgs}")
        
        count = 0
        cleared_ids = []
        for mid in active_msgs:
            if mid in wait_tasks: wait_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
            if mid in followup_tasks: followup_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
            if mid in reply_tasks: reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        
        if key in chat_user_active_msgs: del chat_user_active_msgs[key]
        
        if count > 0:
            log_tree(2, f"销单成功 | {reason} | 归属用户: {user_id} | 任务: {cleared_ids}")

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list):
    try:
        log_tree(1, f"启动 [稍等] 倒计时 (12m) Msg={key_id} | 关联用户组: {user_ids_list}")
        end_time = time.time() + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list:
            add_user_task(chat_id, uid, key_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        for uid in user_ids_list:
            remove_user_task(chat_id, uid, key_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list):
    try:
        log_tree(1, f"启动 [跟进] 倒计时 (15m) Msg={key_id} | 关联用户组: {user_ids_list}")
        end_time = time.time() + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list:
            add_user_task(chat_id, uid, key_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        log_tree(2, f"触发 [跟进] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        for uid in user_ids_list:
            remove_user_task(chat_id, uid, key_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id):
    try:
        log_tree(1, f"启动 [漏回] 监控 (5m) Msg={trigger_msg_id} | 用户: {user_id}")
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link}
        add_user_task(chat_id, user_id, trigger_msg_id)
        
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [漏回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **漏回消息提醒**\n👤 用户: {sender_name} 回复了你\n⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n🔗 [点击回复]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
        remove_user_task(chat_id, user_id, trigger_msg_id)

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
    global IS_WORKING
    cmd = event.text.strip()
    log_tree(0, f"收到指令: {cmd}")
    if cmd == '下班':
        IS_WORKING = False
        for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
        wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
        wait_timers.clear(); followup_timers.clear(); reply_timers.clear()
        wait_msg_map.clear(); followup_msg_map.clear()
        chat_user_active_msgs.clear()
        msg_to_user_cache.clear()
        await send_alert("🔴 **已切换为：下班模式**", "")
    elif cmd == '上班':
        IS_WORKING = True
        await send_alert(f"🟢 **已切换为：工作模式**", "")
    elif cmd == '状态':
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.add(msg_id)
        if msg_id in wait_tasks: wait_tasks[msg_id].cancel()
        if msg_id in followup_tasks: followup_tasks[msg_id].cancel()
        if msg_id in reply_tasks: reply_tasks[msg_id].cancel()

async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    # 优先查缓存
    if (chat_id, reply_to_msg_id) in msg_to_user_cache:
        return msg_to_user_cache[(chat_id, reply_to_msg_id)]

    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
        
        # [Ver 27.3] 关键修复: 如果API查到了，立刻写入缓存！
        # 这样下次再引用这条消息时，就不需要API了，直接读缓存
        if target_msg.sender_id:
            cs_ids = [MY_ID] + OTHER_CS_IDS
            if target_msg.sender_id not in cs_ids:
                msg_to_user_cache[(chat_id, reply_to_msg_id)] = target_msg.sender_id
                log_tree(1, f" ┣━━ 🧠 学习新知识: Msg({reply_to_msg_id}) 属于 User({target_msg.sender_id})")
            return target_msg.sender_id
            
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
            # [Ver 27.3] 顺手缓存一下当前消息
            if msg.sender_id not in ([MY_ID] + OTHER_CS_IDS):
                msg_to_user_cache[(chat_id, msg_id)] = msg.sender_id
        
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

    norm_text = normalize(text)
    is_wait_cmd = any(k in norm_text for k in WAIT_SIGNATURES)
    is_keep_cmd = text.strip() in KEEP_SIGNATURES
    is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)

    real_customer_id = None
    if reply_to_msg_id:
        # 1. 查缓存
        if (chat_id, reply_to_msg_id) in msg_to_user_cache:
            real_customer_id = msg_to_user_cache[(chat_id, reply_to_msg_id)]
        
        # 2. 查任务反推
        if not real_customer_id and reply_to_msg_id in wait_msg_map:
            wait_origin_msg = wait_msg_map[reply_to_msg_id]
            for (cid, uid), msg_set in chat_user_active_msgs.items():
                if cid == chat_id and wait_origin_msg in msg_set:
                    real_customer_id = uid
                    break
        
        # 3. 查API (Ver 27.3: 如果查到了会自动写入缓存)
        if not real_customer_id:
            real_customer_id = await get_traceable_sender(chat_id, reply_to_msg_id)

    # ==================== 客服发言 ====================
    if is_sender_cs:
        if reply_to_msg_id:
            # 记录详细日志，帮助排查
            source_info = "未知"
            if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
            elif real_customer_id: source_info = "API实时查询"
            
            log_tree(1, f"⚡️ 客服操作捕获 | 引用 Msg: {reply_to_msg_id} | 判定归属: {real_customer_id} ({source_info})")

        # 销单逻辑
        if real_customer_id:
            cancel_all_tasks_for_user(chat_id, real_customer_id, reason=f"客服回复: [{text[:10]}...]")
        
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
                        reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users
                    ))
                    followup_tasks[reply_to_msg_id] = task
                    followup_msg_map[event.id] = reply_to_msg_id

                elif is_wait_cmd:
                    task = asyncio.create_task(task_wait_timeout(
                        reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users
                    ))
                    wait_tasks[reply_to_msg_id] = task
                    wait_msg_map[event.id] = reply_to_msg_id

    # ==================== 客户发言 ====================
    else:
        # [Ver 27.3] 只要有人说话，就强行记忆，防止未来引用找不到人
        msg_to_user_cache[(chat_id, event.id)] = sender_id
        
        cancel_all_tasks_for_user(chat_id, sender_id, reason=f"客户发言: [{text[:10]}...]")
        
        log_tree(0, f"[{chat_id}] {sender_name}: {text}")
        if reply_to_msg_id:
            try:
                target_id = None
                replied_msg = await event.get_reply_message()
                if replied_msg: target_id = replied_msg.sender_id
                else: 
                    msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
                    if msgs: target_id = msgs[0].sender_id

                if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                    if normalize(text.strip()) in IGNORE_SIGNATURES: return
                    if event.id in reply_tasks: reply_tasks[event.id].cancel()
                    
                    task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link, chat_id, sender_id))
                    reply_tasks[event.id] = task
            except Exception: pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    log_tree(0, "✅ 系统启动 (Ver 27.3 Memory Fix)")
    client.start()
    client.run_until_disconnected()
