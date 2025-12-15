import os
import sys
import asyncio
import logging
import requests
import re
import time
from threading import Thread
from flask import Flask, render_template_string
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 0. 高级日志配置 (黑匣子) =================
# 这是核心修改：将运行细节同时写入文件和控制台
# ---------------------------------------------------------
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)

# 1. 文件处理器 (记录所有细节，用于查错)
file_handler = logging.FileHandler('bot_debug.log', mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_fmt = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
file_handler.setFormatter(file_fmt)

# 2. 控制台处理器 (只显示重要信息，保持界面清爽)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_fmt = logging.Formatter('%(asctime)s - %(message)s', datefmt='%H:%M:%S')
console_handler.setFormatter(console_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 屏蔽第三方库的噪音
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)

# 环境变量开关
_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

def log_debug(msg):
    """同时写入文件和控制台(如果是调试模式)"""
    if _sys_opt:
        logger.info(f"🔍 {msg}")
    else:
        logger.debug(f"[TRACE] {msg}") # 仅写入文件

def log_info(msg):
    logger.info(msg)

# ================= 1. 辅助函数 =================
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
            try:
                result.append(int(match.group()))
            except: pass
    return result

# ================= 2. 配置加载 =================
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

except KeyError as e:
    logger.error(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    logger.error(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

log_info(f"✅ 配置加载成功 | 稍等词: {len(WAIT_SIGNATURES)} | 跟进词: {len(KEEP_SIGNATURES)}")

# ================= 3. 全局参数 =================
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

IS_WORKING = False
MY_ID = None

# ================= 4. Web服务 (纯白高清版) =================
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>监控看板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5"> 
    <style>
        :root {
            --bg-body: #ffffff;
            --text-main: #000000;
            --text-sub: #666666;
            --card-bg: #f8f9fa;
            --border: #e9ecef;
            --green: #28a745;
            --red: #dc3545;
            --blue: #007bff;
        }
        body { 
            background-color: var(--bg-body); 
            color: var(--text-main); 
            font-family: -apple-system, "Microsoft YaHei", sans-serif; 
            margin: 0; 
            padding: 20px;
            max-width: 600px;
            margin: 0 auto;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid var(--text-main);
            padding-bottom: 15px;
            margin-bottom: 20px;
        }
        h1 { margin: 0; font-size: 1.4rem; font-weight: 800; }
        .status-tag {
            padding: 5px 10px;
            border-radius: 4px;
            font-weight: bold;
            font-size: 0.9rem;
        }
        .status-on { background: var(--green); color: #fff; }
        .status-off { background: var(--red); color: #fff; }
        .group-box { margin-bottom: 30px; }
        .group-title {
            font-size: 1rem;
            font-weight: 700;
            color: var(--text-sub);
            margin-bottom: 10px;
            border-left: 4px solid var(--text-main);
            padding-left: 10px;
            display: flex;
            justify-content: space-between;
        }
        .card { 
            background: var(--card-bg);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .card-left { display: flex; flex-direction: column; gap: 5px; }
        .user { font-weight: 700; font-size: 1.1rem; }
        .link { font-size: 0.85rem; color: var(--blue); text-decoration: none; }
        .timer { font-family: monospace; font-size: 1.2rem; font-weight: 700; color: #d63384; }
        .timer.late { color: var(--red); text-decoration: underline; }
        .empty { color: #ccc; text-align: center; padding: 10px; font-style: italic; }
        .footer { text-align: center; color: #ccc; font-size: 0.8rem; margin-top: 40px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡️ 实时监控</h1>
        <div class="status-tag {{ 'status-on' if working else 'status-off' }}">
            {{ '正在工作' if working else '休息中' }}
        </div>
    </div>
    
    <div class="group-box">
        <div class="group-title"><span>⏳ 稍等 (12m)</span><span>{{ wait_timers|length }}</span></div>
        {% if wait_timers %}
            {% for mid, info in wait_timers.items() %}
            <div class="card">
                <div class="card-left">
                    <span class="user">{{ info.user }}</span>
                    <a href="{{ info.url }}" target="_blank" class="link">跳转消息 &rarr;</a>
                </div>
                <span class="timer" data-end="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">无进行中任务</div>
        {% endif %}
    </div>

    <div class="group-box">
        <div class="group-title"><span>🕵️ 跟进 (15m)</span><span>{{ followup_timers|length }}</span></div>
        {% if followup_timers %}
            {% for mid, info in followup_timers.items() %}
            <div class="card">
                <div class="card-left">
                    <span class="user">{{ info.user }}</span>
                    <a href="{{ info.url }}" target="_blank" class="link">跳转消息 &rarr;</a>
                </div>
                <span class="timer" data-end="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">无进行中任务</div>
        {% endif %}
    </div>

    <div class="group-box">
        <div class="group-title"><span>🔔 漏回 (5m)</span><span>{{ reply_timers|length }}</span></div>
        {% if reply_timers %}
            {% for mid, info in reply_timers.items() %}
            <div class="card">
                <div class="card-left">
                    <span class="user">{{ info.user }}</span>
                    <a href="{{ info.url }}" target="_blank" class="link">跳转消息 &rarr;</a>
                </div>
                <span class="timer" data-end="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">无进行中任务</div>
        {% endif %}
    </div>
    <div class="footer">更新时间: {{ current_time }} | Ver: 25.0 (BlackBox)</div>
    <script>
        function update() {
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
        }
        setInterval(update, 1000);
        update();
    </script>
</body>
</html>
"""

@app.route('/')
def status_page():
    current_time_str = time.strftime("%H:%M:%S", time.localtime())
    return render_template_string(HTML_TEMPLATE, working=IS_WORKING, wait_timers=wait_timers, 
                                followup_timers=followup_timers, reply_timers=reply_timers, current_time=current_time_str)

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ================= 5. 通知与网络模块 =================
def _post_request(url, payload):
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        logger.error(f"❌ Telegram API发送异常: {e}")

async def send_alert(text, link):
    if not BOT_TOKEN: return
    log_debug(f"🔔 准备发送警报: {text.splitlines()[1] if len(text.splitlines())>1 else '...'} -> {link}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    tasks = []
    for chat_id in ALERT_GROUP_IDS:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
        tasks.append(loop.run_in_executor(None, lambda p=payload: _post_request(url, p)))
    if tasks:
        await asyncio.gather(*tasks)

async def check_msg_exists(channel_id, msg_id):
    """
    检查消息是否存在。
    FAIL-SAFE: 网络错误视为消息存在，防止漏报。
    """
    try:
        msg = await client.get_messages(channel_id, ids=msg_id)
        if not msg: 
            log_debug(f"❌ 消息 {msg_id} 已物理删除/不存在")
            return False 
        return True
    except Exception as e:
        log_debug(f"⚠️ 检查消息 {msg_id} 时网络异常 ({e}) -> 强制视为存在")
        return True # 关键：报错也当做存在

# ================= 6. 任务管理 =================
def add_user_task(chat_id, user_id, msg_id):
    if not user_id: return
    key = (chat_id, user_id)
    if key not in chat_user_active_msgs:
        chat_user_active_msgs[key] = set()
    chat_user_active_msgs[key].add(msg_id)

def remove_user_task(chat_id, user_id, msg_id):
    if not user_id: return
    key = (chat_id, user_id)
    if key in chat_user_active_msgs:
        chat_user_active_msgs[key].discard(msg_id)
        if not chat_user_active_msgs[key]:
            del chat_user_active_msgs[key]

def cancel_all_tasks_for_user(chat_id, user_id):
    if not user_id: return
    key = (chat_id, user_id)
    if key in chat_user_active_msgs:
        active_msgs = list(chat_user_active_msgs[key])
        count = 0
        for mid in active_msgs:
            if mid in wait_tasks: wait_tasks[mid].cancel(); count += 1
            if mid in followup_tasks: followup_tasks[mid].cancel(); count += 1
            if mid in reply_tasks: reply_tasks[mid].cancel(); count += 1
        
        if key in chat_user_active_msgs: del chat_user_active_msgs[key]
        if count > 0:
            log_debug(f"🗑️ 智能销单: 用户 {user_id} -> 清除 {count} 个任务")

# ================= 7. 倒计时任务逻辑 =================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        log_debug(f"⏳ [启动] 稍等倒计时: Msg={key_id} Agent={agent_name}")
        end_time = time.time() + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_user_task(chat_id, customer_id, key_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id):
            log_debug(f"🔕 [取消] 稍等报警: 触发消息 {my_msg_id} 已不存在")
            return

        log_debug(f"🚨 [触发] 稍等超时报警: Msg={key_id}")
        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **稍等-超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n"
            f"🔗 [点击处理]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError:
        log_debug(f"🛑 [中断] 稍等任务被取消: Msg={key_id}")
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        remove_user_task(chat_id, customer_id, key_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        log_debug(f"🕵️ [启动] 跟进倒计时: Msg={key_id} Agent={agent_name}")
        end_time = time.time() + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_user_task(chat_id, customer_id, key_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id):
            log_debug(f"🔕 [取消] 跟进报警: 触发消息 {my_msg_id} 已不存在")
            return

        log_debug(f"🚨 [触发] 跟进超时报警: Msg={key_id}")
        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **跟进-超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n"
            f"🔗 [点击处理]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError:
        log_debug(f"🛑 [中断] 跟进任务被取消: Msg={key_id}")
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        remove_user_task(chat_id, customer_id, key_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link):
    try:
        log_debug(f"🔔 [启动] 漏回监控: Msg={trigger_msg_id} User={sender_name}")
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link}
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_debug(f"🚨 [触发] 漏回报警: Msg={trigger_msg_id}")
        alert_text = (
            f"📩 内容: `{content.replace('`', '')}`\n"
            f"🔔 **漏回消息提醒**\n"
            f"👤 用户: {sender_name} 回复了你\n"
            f"⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n"
            f"🔗 [点击回复]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError:
        pass # 漏回任务取消很频繁，不打Log防止刷屏
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]

# ================= 8. Telethon 客户端 =================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="Mac mini M2",
    app_version="5.8.3",      
    system_version="macOS 15.6.1",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    global IS_WORKING
    cmd = event.text.strip()
    log_info(f"收到指令: {cmd}")
    if cmd == '下班':
        IS_WORKING = False
        for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
        wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
        wait_timers.clear(); followup_timers.clear(); reply_timers.clear()
        wait_msg_map.clear(); followup_msg_map.clear()
        chat_user_active_msgs.clear()
        await send_alert("🔴 **已切换为：下班模式**", "")
    elif cmd == '上班':
        IS_WORKING = True
        await send_alert("🟢 **已切换为：工作模式**", "")
    elif cmd == '状态':
        status_icon = "🟢" if IS_WORKING else "🔴"
        spy_status = "开启 (DEBUG)" if _sys_opt else "关闭 (Standard)"
        msg = (
            f"{status_icon} **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n"
            f"⚙️ 调试模式: {spy_status}\n"
            f"⏳ 稍等任务: {len(wait_tasks)}\n"
            f"🕵️ 跟进任务: {len(followup_tasks)}\n"
            f"🔔 漏回任务: {len(reply_tasks)}"
        )
        await send_alert(msg, "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.add(msg_id)
        # 这里不打log，因为删除事件非常频繁，只处理逻辑
        if msg_id in wait_msg_map:
            cid = wait_msg_map[msg_id]
            if cid in wait_tasks: wait_tasks[cid].cancel()
            del wait_msg_map[msg_id]
        if msg_id in followup_msg_map:
            cid = followup_msg_map[msg_id]
            if cid in followup_tasks: followup_tasks[cid].cancel()
            del followup_msg_map[msg_id]
        if msg_id in reply_tasks:
            reply_tasks[msg_id].cancel()
            del reply_tasks[msg_id]

# ================= 9. 溯源逻辑 (Deep Trace) =================
async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
    except Exception:
        return None

    sender_id = target_msg.sender_id
    cs_ids = [MY_ID] + OTHER_CS_IDS

    if sender_id and sender_id not in cs_ids:
        return sender_id

    if sender_id in cs_ids:
        if target_msg.reply_to_msg_id:
            log_debug(f"🔗 溯源递归: 消息 {reply_to_msg_id} 是客服 -> 继续查 {target_msg.reply_to_msg_id}")
            return await get_traceable_sender(chat_id, target_msg.reply_to_msg_id, current_recursion + 1)
    
    return None

# ================= 10. 主循环 Handler =================
@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    global MY_ID
    if not MY_ID: MY_ID = (await client.get_me()).id
    if not IS_WORKING: return

    text = event.text or ""
    log_text = text.replace('\n', ' ').replace('\r', '') 
    
    sender_id = event.sender_id
    reply_to_msg_id = event.reply_to_msg_id
    sender = await event.get_sender()
    sender_name = getattr(sender, 'first_name', 'Unknown')
    chat_id_str = str(event.chat_id).replace('-100', '')
    msg_link = f"https://t.me/c/{chat_id_str}/{event.id}"

    try:
        chat = await event.get_chat()
        group_title = getattr(chat, 'title', chat_id_str)
    except:
        group_title = chat_id_str

    norm_text = normalize(text)
    is_wait_cmd = any(k in norm_text for k in WAIT_SIGNATURES)
    is_keep_cmd = text.strip() in KEEP_SIGNATURES
    
    is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)
    is_cs_action = is_sender_cs 

    # --- 1. 智能溯源销单 ---
    real_customer_id = None
    if reply_to_msg_id:
        if reply_to_msg_id in wait_msg_map:
            wait_origin_msg = wait_msg_map[reply_to_msg_id]
            for (cid, uid), msg_set in chat_user_active_msgs.items():
                if cid == event.chat_id and wait_origin_msg in msg_set:
                    real_customer_id = uid
                    break
        
        if not real_customer_id:
            real_customer_id = await get_traceable_sender(event.chat_id, reply_to_msg_id)
            if real_customer_id and _sys_opt:
                log_debug(f"🎯 溯源成功: 消息 {event.id} 指向客户 {real_customer_id}")

    if real_customer_id:
        cancel_all_tasks_for_user(event.chat_id, real_customer_id)
    
    if not is_sender_cs:
        cancel_all_tasks_for_user(event.chat_id, sender_id)

    # --- 2. 客服操作 ---
    if is_cs_action:
        if reply_to_msg_id and reply_to_msg_id in reply_tasks:
            reply_tasks[reply_to_msg_id].cancel(); del reply_tasks[reply_to_msg_id]

        if reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            customer_id = reply_msg.sender_id if reply_msg else real_customer_id

            if is_keep_cmd:
                log_debug(f"⚙️ 触发跟进任务: {sender_name} -> ID {reply_to_msg_id}")
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                log_debug(f"⚙️ 触发稍等任务: {sender_name} -> ID {reply_to_msg_id}")
                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

    # --- 3. 客户操作 ---
    else:
        log_debug(f"📩 [{group_title}] {sender_name}: {log_text[:30]}")
        if reply_to_msg_id:
            try:
                target_id = None
                replied_msg = await event.get_reply_message()
                if replied_msg:
                    target_id = replied_msg.sender_id
                else:
                    msgs = await client.get_messages(event.chat_id, ids=[reply_to_msg_id])
                    if msgs: target_id = msgs[0].sender_id

                if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                    if normalize(text.strip()) in IGNORE_SIGNATURES:
                        return

                    if event.id in reply_tasks: reply_tasks[event.id].cancel()
                    task = asyncio.create_task(task_reply_timeout(
                        event.id, sender_name, text[:50], msg_link
                    ))
                    reply_tasks[event.id] = task
                    add_user_task(event.chat_id, sender_id, event.id)
            except Exception: pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    log_info(f"✅ 系统启动完成 (Ver 25.0 BlackBox) - 日志文件: bot_debug.log")
    client.start()
    
    try:
        start_msg = "🤖 **系统启动成功**\n当前状态: 🔴 下班 (默认)\n版本: Ver 25.0 (黑匣子日志版)"
        client.loop.run_until_complete(send_alert(start_msg, ""))
    except Exception as e:
        logger.error(f"❌ 启动通知发送失败: {e}")

    client.run_until_disconnected()
