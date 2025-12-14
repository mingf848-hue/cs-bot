import os
import sys
import asyncio
import logging
import requests
import re
from threading import Thread
from flask import Flask, render_template_string
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message
import time

# ================= 0. 辅助函数 =================
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

# ================= 1. 配置加载 =================
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
    print(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    print(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

print(f"✅ 配置加载成功。群组: {len(CS_GROUP_IDS)} | 客服ID: {len(OTHER_CS_IDS)+1} | 稍等词: {len(WAIT_SIGNATURES)}")

# ================= 2. 全局参数 =================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60

# 任务对象
wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}

# 倒计时及信息
wait_timers = {}
followup_timers = {}
reply_timers = {}

# 消息映射表
wait_msg_map = {}      
followup_msg_map = {} 
deleted_cache = set()

# 用户任务索引
chat_user_active_msgs = {}

IS_WORKING = False
MY_ID = None

# ================= 3. Web服务 & 前端优化 =================
logging.basicConfig(format='%(asctime)s - %(message)s', level=logging.INFO, stream=sys.stdout)
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)

# 优化后的前端：浅色背景、系统字体、卡片式设计
HTML_TEMPLATE_DYNAMIC = """
<!DOCTYPE html>
<html>
<head>
    <title>客服系统监控中心</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="10"> 
    <style>
        :root {
            --bg-color: #f5f7fa;
            --card-bg: #ffffff;
            --text-main: #2c3e50;
            --text-sub: #7f8c8d;
            --border-color: #ecf0f1;
            --accent-blue: #3498db;
            --success-green: #27ae60;
            --danger-red: #e74c3c;
            --warning-orange: #f39c12;
        }
        body { 
            background-color: var(--bg-color); 
            color: var(--text-main); 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; 
            margin: 0; 
            padding: 20px;
            display: flex;
            justify-content: center;
        }
        .container { 
            background: var(--card-bg); 
            width: 100%;
            max-width: 600px; 
            border-radius: 12px; 
            box-shadow: 0 4px 20px rgba(0,0,0,0.05); 
            padding: 24px;
        }
        h1 { 
            font-size: 1.5rem; 
            color: var(--text-main); 
            margin: 0 0 20px 0; 
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 2px solid var(--bg-color);
            padding-bottom: 15px;
        }
        .status-badge {
            font-size: 0.9rem;
            padding: 6px 12px;
            border-radius: 20px;
            font-weight: 600;
        }
        .status-on { background-color: #e8f8f5; color: var(--success-green); }
        .status-off { background-color: #fdedec; color: var(--danger-red); }

        .stat-box { 
            margin-bottom: 20px; 
        }
        .stat-header { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            margin-bottom: 10px;
            padding: 8px 0;
        }
        .stat-label { 
            font-size: 1rem; 
            font-weight: 600; 
            color: var(--text-main);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .stat-count { 
            background: var(--bg-color);
            padding: 2px 10px;
            border-radius: 10px;
            font-size: 0.9rem;
            font-weight: bold;
            color: var(--text-sub);
        }
        
        .task-list { 
            border: 1px solid var(--border-color);
            border-radius: 8px;
            overflow: hidden;
        }
        .task-item { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            padding: 12px 16px;
            border-bottom: 1px solid var(--border-color);
            background: #fff;
            transition: background 0.2s;
        }
        .task-item:last-child { border-bottom: none; }
        .task-item:hover { background: #fafafa; }

        .task-info { display: flex; flex-direction: column; gap: 4px; }
        .user-name { font-weight: 600; font-size: 0.95rem; color: var(--text-main); }
        .msg-link { 
            font-size: 0.8rem; 
            color: var(--accent-blue); 
            text-decoration: none; 
        }
        .msg-link:hover { text-decoration: underline; }
        
        .timer-text { 
            font-family: 'SF Mono', 'Roboto Mono', monospace; 
            font-weight: 600; 
            color: var(--warning-orange);
            font-size: 0.95rem;
        }
        .timer-overdue { color: var(--danger-red); }

        .empty-tip { 
            padding: 15px; 
            text-align: center; 
            color: var(--text-sub); 
            font-size: 0.9rem;
            background: #fafafa;
        }
        .footer { 
            margin-top: 30px; 
            text-align: center; 
            font-size: 0.75rem; 
            color: var(--text-sub); 
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>
            <span>🛡️ 监控中心</span>
            <span class="status-badge {{ 'status-on' if working else 'status-off' }}">
                {{ '🟢 监控中' if working else '🔴 已暂停' }}
            </span>
        </h1>
        
        <div class="stat-box">
            <div class="stat-header">
                <div class="stat-label">⏳ 稍等任务 (12m)</div>
                <div class="stat-count">{{ wait_timers|length }}</div>
            </div>
            <div class="task-list">
                {% if wait_timers %}
                    {% for mid, info in wait_timers.items() %}
                    <div class="task-item">
                        <div class="task-info">
                            <span class="user-name">{{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">查看消息 &rarr;</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">当前无活跃任务</div>
                {% endif %}
            </div>
        </div>

        <div class="stat-box">
            <div class="stat-header">
                <div class="stat-label">🕵️ 跟进任务 (15m)</div>
                <div class="stat-count">{{ followup_timers|length }}</div>
            </div>
            <div class="task-list">
                {% if followup_timers %}
                    {% for mid, info in followup_timers.items() %}
                    <div class="task-item">
                        <div class="task-info">
                            <span class="user-name">{{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">查看消息 &rarr;</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">当前无活跃任务</div>
                {% endif %}
            </div>
        </div>

        <div class="stat-box">
            <div class="stat-header">
                <div class="stat-label">🔔 漏回任务 (5m)</div>
                <div class="stat-count">{{ reply_timers|length }}</div>
            </div>
            <div class="task-list">
                {% if reply_timers %}
                    {% for mid, info in reply_timers.items() %}
                    <div class="task-item">
                        <div class="task-info">
                            <span class="user-name">{{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">查看消息 &rarr;</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">当前无活跃任务</div>
                {% endif %}
            </div>
        </div>

        <div class="footer">
            最后更新: {{ current_time }}<br>
            System Ver: 24.0 (DeepTrace & LightUI)
        </div>
    </div>
    <script>
        function updateTimers() {
            const now = Date.now() / 1000;
            document.querySelectorAll('.timer-text').forEach(el => {
                const endTs = parseFloat(el.getAttribute('data-end'));
                const diff = endTs - now;
                if (diff <= 0) {
                    el.innerText = "已超时";
                    el.classList.add('timer-overdue');
                } else {
                    const m = Math.floor(diff / 60);
                    const s = Math.floor(diff % 60);
                    el.innerText = `${m}分 ${s.toString().padStart(2, '0')}秒`;
                }
            });
        }
        setInterval(updateTimers, 1000);
        updateTimers();
    </script>
</body>
</html>
"""

@app.route('/')
def status_page():
    current_time_str = time.strftime("%H:%M:%S", time.localtime())
    return render_template_string(
        HTML_TEMPLATE_DYNAMIC,
        working=IS_WORKING,
        wait_timers=wait_timers,
        followup_timers=followup_timers,
        reply_timers=reply_timers,
        current_time=current_time_str
    )

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ================= 4. 通知模块 =================
def _post_request(url, payload):
    try:
        requests.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"❌ 发送异常: {e}")

async def send_alert(text, link):
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    tasks = []
    for chat_id in ALERT_GROUP_IDS:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
        tasks.append(loop.run_in_executor(None, lambda p=payload: _post_request(url, p)))
    if tasks:
        await asyncio.gather(*tasks)

# ================= 5. 任务辅助逻辑 =================

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

# 智能销单
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
        if _sys_opt and count > 0: print(f"[DEBUG] 智能销单: 已清除用户 {user_id} 的 {count} 个任务")

async def check_msg_exists(channel_id, msg_id):
    try:
        msg = await client.get_messages(channel_id, ids=msg_id)
        if not msg: return False 
        if msg.text is None and msg.media is None: return False
        return True
    except Exception:
        return False

# ================= 6. 任务逻辑 =================

async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        end_time = time.time() + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_user_task(chat_id, customer_id, key_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id):
            if _sys_opt: print(f"[DEBUG] 稍等消息 {my_msg_id} 已删除，取消报警")
            return

        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **稍等-超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n"
            f"🔗 [点击处理]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError: pass
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        remove_user_task(chat_id, customer_id, key_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, customer_id):
    try:
        end_time = time.time() + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        add_user_task(chat_id, customer_id, key_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return

        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id):
            if _sys_opt: print(f"[DEBUG] 跟进消息 {my_msg_id} 已删除，取消报警")
            return

        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **跟进-超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n"
            f"🔗 [点击处理]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        remove_user_task(chat_id, customer_id, key_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link):
    try:
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link}
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        alert_text = (
            f"📩 内容: `{content.replace('`', '')}`\n"
            f"🔔 **漏回消息提醒**\n"
            f"👤 用户: {sender_name} 回复了你\n"
            f"⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n"
            f"🔗 [点击回复]({link})"
        )
        await send_alert(alert_text, link)
    except asyncio.CancelledError: pass
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]

# ================= 7. 客户端实例 =================
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

# ================= 8. 控制指令 =================
@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    global IS_WORKING
    cmd = event.text.strip()
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

# ================= 9. 删除同步 =================
@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.add(msg_id)
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

# ================= 9.5 深度溯源函数 (核心修复) =================
async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    """
    深度查找：顺藤摸瓜找到真正的客户ID
    1. 查 API (最准，防止本地缓存缺失)
    2. 如果发现回复的是同事/自己，继续往上找 (递归)
    """
    if current_recursion > 3: return None
    
    try:
        # 强制获取消息对象（即使本地没有缓存）
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
    except Exception:
        return None

    sender_id = target_msg.sender_id
    cs_ids = [MY_ID] + OTHER_CS_IDS

    # 如果是客户，找到了
    if sender_id and sender_id not in cs_ids:
        return sender_id

    # 如果是客服/自己，继续往上找
    if sender_id in cs_ids:
        if target_msg.reply_to_msg_id:
            if _sys_opt: print(f"[DEBUG] 溯源: 客服引用消息 -> 继续查找 {target_msg.reply_to_msg_id}")
            return await get_traceable_sender(chat_id, target_msg.reply_to_msg_id, current_recursion + 1)
    
    return None

# ================= 10. 消息处理主循环 (智能版) =================
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

    # ==================== [核心优化] 智能溯源销单 ====================
    real_customer_id = None

    if reply_to_msg_id:
        # 1. 尝试本地映射表快速查找
        if reply_to_msg_id in wait_msg_map:
            wait_origin_msg = wait_msg_map[reply_to_msg_id]
            for (cid, uid), msg_set in chat_user_active_msgs.items():
                if cid == event.chat_id and wait_origin_msg in msg_set:
                    real_customer_id = uid
                    if _sys_opt: print(f"[DEBUG] 快速命中: 客户 {uid}")
                    break
        
        # 2. 深度溯源 (强制联网查询，解决误报核心)
        if not real_customer_id:
            real_customer_id = await get_traceable_sender(event.chat_id, reply_to_msg_id)
            if real_customer_id and _sys_opt:
                print(f"[DEBUG] 深度溯源: 消息最终指向客户 -> {real_customer_id}")

    # 执行销单
    if real_customer_id:
        cancel_all_tasks_for_user(event.chat_id, real_customer_id)
    
    if not is_sender_cs:
        cancel_all_tasks_for_user(event.chat_id, sender_id)

    # ==================== 客服发言处理 ====================
    if is_cs_action:
        if reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            customer_id = reply_msg.sender_id if reply_msg else real_customer_id

            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel(); del reply_tasks[reply_to_msg_id]

            if is_keep_cmd:
                if _sys_opt: print(f"[DEBUG] 触发精准跟进({sender_name})")
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                if _sys_opt: print(f"[DEBUG] 触发稍等({sender_name})")
                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

    # ==================== 客户发言处理 ====================
    else:
        if _sys_opt: print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            try:
                # 只有当客户确实在回复客服的时候，才算"漏回任务"
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
            except Exception as e: pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 系统启动完成 (默认下班模式) | Ver 24.0 (DeepTrace)")
    client.start()
    
    try:
        start_msg = "🤖 **系统启动成功**\n当前状态: 🔴 下班 (默认)\n版本: Ver 24.0 (智能溯源版)"
        client.loop.run_until_complete(send_alert(start_msg, ""))
    except Exception as e:
        print(f"❌ 启动通知发送失败: {e}")

    client.run_until_disconnected()
