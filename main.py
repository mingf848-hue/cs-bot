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
import time

# ================= 0. 辅助函数 =================
def normalize(text):
    """归一化：转小写 + 半角波浪线"""
    if not text: return ""
    return text.lower().replace('～', '~')

def extract_id_list(env_str):
    """提取 ID 列表，支持备注"""
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

    # [新增] 忽略关键词 (结束语过滤)
    # 如果客户回复的内容是这些词，则不触发漏回警告
    default_ignore = "好的,谢谢,收到,明白,好的谢谢,ok,thx,thanks,好的呢,好滴,1"
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

# 任务对象存储
wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}

# 倒计时及信息存储: {msg_id: {'ts': end_time, 'user': name, 'url': link}}
wait_timers = {}
followup_timers = {}
reply_timers = {}

# 消息映射表
wait_msg_map = {}     
followup_msg_map = {} 
deleted_cache = set()

# 用户任务索引：(chat_id, user_id) -> Set[msg_id]
chat_user_active_msgs = {}

IS_WORKING = False
MY_ID = None

# ================= 3. Web服务 =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

HTML_TEMPLATE_DYNAMIC = """
<!DOCTYPE html>
<html>
<head>
    <title>系统状态监控</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="10"> 
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: 'Menlo', 'Monaco', monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px 0; }
        .container { background: #161b22; padding: 2rem; border-radius: 12px; border: 1px solid #30363d; box-shadow: 0 4px 20px rgba(0,0,0,0.5); width: 90%; max-width: 600px; text-align: center; }
        h1 { font-size: 1.4rem; color: #58a6ff; margin-bottom: 1.5rem; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
        .stat-box { background: #21262d; padding: 12px; margin: 10px 0; border-radius: 6px; border: 1px solid #30363d; }
        .stat-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 5px; }
        .stat-label { font-size: 0.9rem; color: #8b949e; font-weight: bold; }
        .stat-count { font-size: 1.1rem; font-weight: bold; }
        .task-list { text-align: left; font-size: 0.8rem; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #30363d; }
        .task-item { display: flex; justify-content: space-between; align-items: center; color: #79c0ff; margin: 6px 0; padding: 4px; background: #1a1f26; border-radius: 4px; }
        .task-info { display: flex; flex-direction: column; gap: 2px; text-align: left; overflow: hidden; }
        .user-name { color: #d2a8ff; font-weight: bold; font-size: 0.9rem; }
        .msg-link { color: #58a6ff; text-decoration: none; font-size: 0.75rem; }
        .msg-link:hover { text-decoration: underline; }
        .timer-text { color: #f0883e; font-family: monospace; font-size: 1rem; white-space: nowrap; margin-left: 10px; }
        .footer { margin-top: 25px; font-size: 0.7rem; color: #58a6ff; }
        .green { color: #238636; }
        .red { color: #da3633; }
        .empty-tip { color: #484f58; font-style: italic; padding: 5px; }
    </style>
</head>
<body>
    <div class="container">
        <h1>🔍 系统状态监控</h1>
        <div class="stat-box">
            <div class="stat-header">
                <div class="stat-label">运行状态</div>
                <div class="stat-count {{ 'green' if working else 'red' }}">{{ '🟢 工作中' if working else '🔴 已下班' }}</div>
            </div>
        </div>
        
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
                            <span class="user-name">👤 {{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">🔗 查看消息</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">无进行中任务</div>
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
                            <span class="user-name">👤 {{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">🔗 查看消息</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">无进行中任务</div>
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
                            <span class="user-name">👤 {{ info.user }}</span>
                            <a href="{{ info.url }}" target="_blank" class="msg-link">🔗 查看消息</a>
                        </div>
                        <span class="timer-text" data-end="{{ info.ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">无进行中任务</div>
                {% endif %}
            </div>
        </div>
        <div class="footer">更新时间: {{ current_time }}<br>Ver: 22.1 (Smart Cancel & Ignore)</div>
    </div>
    <script>
        function updateTimers() {
            const now = Date.now() / 1000;
            document.querySelectorAll('.timer-text').forEach(el => {
                const endTs = parseFloat(el.getAttribute('data-end'));
                const diff = endTs - now;
                if (diff <= 0) {
                    el.innerText = "00:00 (超时)";
                    el.style.color = "#da3633";
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
    """记录用户挂起的任务"""
    if not user_id: return
    key = (chat_id, user_id)
    if key not in chat_user_active_msgs:
        chat_user_active_msgs[key] = set()
    chat_user_active_msgs[key].add(msg_id)

def remove_user_task(chat_id, user_id, msg_id):
    """移除记录"""
    if not user_id: return
    key = (chat_id, user_id)
    if key in chat_user_active_msgs:
        chat_user_active_msgs[key].discard(msg_id)
        if not chat_user_active_msgs[key]:
            del chat_user_active_msgs[key]

async def check_msg_exists(channel_id, msg_id):
    """起飞前安检：检查消息是否还存在"""
    try:
        # 使用 Telethon 获取单条消息
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
@client.on(events.NewMessage(chats='me', pattern='^(上班|下班|状态)$'))
async def command_handler(event):
    global IS_WORKING
    cmd = event.text
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

# ================= 10. 消息处理主循环 =================
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

    # ==================== 客服发言逻辑 ====================
    if is_cs_action:
        if reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            
            customer_id = reply_msg.sender_id if reply_msg else None

            if customer_id:
                user_key = (event.chat_id, customer_id)
                if user_key in chat_user_active_msgs:
                    active_msgs = list(chat_user_active_msgs[user_key]) 
                    for mid in active_msgs:
                        if mid in wait_tasks: wait_tasks[mid].cancel()
                        if mid in followup_tasks: followup_tasks[mid].cancel()
                        if mid in reply_tasks: reply_tasks[mid].cancel()
                    if user_key in chat_user_active_msgs: del chat_user_active_msgs[user_key]
                    if _sys_opt: print(f"[DEBUG] 智能销单: 清除用户 {customer_id} 所有任务")

            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel(); del reply_tasks[reply_to_msg_id]

            if is_keep_cmd:
                if _sys_opt: print(f"[DEBUG] 触发精准跟进({sender_name}): {text.strip()}")
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                if _sys_opt: print(f"[DEBUG] 触发稍等({sender_name}): {text.strip()}")
                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, event.chat_id, customer_id
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

            else:
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()

    # ==================== 客户发言逻辑 ====================
    else:
        if _sys_opt: print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            # 1. 客户说话 -> 取消等待/跟进
            if reply_to_msg_id in wait_tasks: 
                wait_tasks[reply_to_msg_id].cancel(); del wait_tasks[reply_to_msg_id]
            if reply_to_msg_id in followup_tasks:
                followup_tasks[reply_to_msg_id].cancel(); del followup_tasks[reply_to_msg_id]
            
            # 2. 启动漏回
            try:
                replied_msg = await event.get_reply_message()
                target_id = replied_msg.sender_id
                
                if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                    # 【核心修复】检测是否为结束语
                    if normalize(text.strip()) in IGNORE_SIGNATURES:
                        if _sys_opt: print(f"[DEBUG] 忽略结束语({sender_name}): {text.strip()}")
                        # 仅忽略，不启动新任务，但上方已经执行了“取消等待/跟进”的操作
                        # 所以这里直接 return 即可
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
    print(f"✅ 系统启动完成 (默认下班模式) | Ver 22.1")
    client.start()
    
    try:
        start_msg = "🤖 **系统启动成功**\n当前状态: 🔴 下班 (默认)\n版本: Ver 22.1 (Smart Cancel & Ignore)"
        client.loop.run_until_complete(send_alert(start_msg, ""))
    except Exception as e:
        print(f"❌ 启动通知发送失败: {e}")

    client.run_until_disconnected()
