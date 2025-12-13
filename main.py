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

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}

# 倒计时时间戳存储
wait_timers = {}
followup_timers = {}
reply_timers = {}

wait_msg_map = {}     
followup_msg_map = {} 
deleted_cache = set()

wait_task_grouped_index = {} 
followup_task_grouped_index = {} 
reply_task_grouped_index = {}

IS_WORKING = False
MY_ID = None

# ================= 3. Web服务 (UI升级版) =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

# 全新的现代化 HTML 模板
HTML_TEMPLATE_DYNAMIC = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>客服监控看板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="10"> 
    <style>
        :root {
            --primary: #1890ff;
            --success: #52c41a;
            --error: #ff4d4f;
            --warning: #faad14;
            --bg: #f0f2f5;
            --card-bg: #ffffff;
            --text-main: #000000;
            --text-sub: #8c8c8c;
        }
        body { 
            background-color: var(--bg); 
            color: var(--text-main); 
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            margin: 0; 
            padding: 20px;
            display: flex;
            justify-content: center;
        }
        .container { 
            width: 100%; 
            max-width: 600px; 
            display: flex; 
            flex-direction: column; 
            gap: 16px; 
        }
        
        /* 顶部状态卡片 */
        .header-card {
            background: var(--card-bg);
            border-radius: 12px;
            padding: 24px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
            text-align: center;
            border-top: 4px solid var(--text-sub);
        }
        .header-card.online { border-top-color: var(--success); }
        .header-card.offline { border-top-color: var(--error); }
        
        .status-title { font-size: 14px; color: var(--text-sub); margin-bottom: 8px; }
        .status-value { font-size: 24px; font-weight: 700; }
        .online .status-value { color: var(--success); }
        .offline .status-value { color: var(--error); }

        /* 任务列表卡片 */
        .task-card {
            background: var(--card-bg);
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
        }
        .card-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 16px;
            padding-bottom: 12px;
            border-bottom: 1px solid #f0f0f0;
        }
        .card-title { font-size: 16px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
        .badge { 
            background: #f5f5f5; 
            color: var(--text-sub); 
            padding: 2px 8px; 
            border-radius: 10px; 
            font-size: 12px; 
            font-weight: normal; 
        }
        .badge.active { background: #e6f7ff; color: var(--primary); font-weight: bold; }

        .task-list { display: flex; flex-direction: column; gap: 10px; }
        .task-item { 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            font-size: 14px; 
            padding: 8px;
            background: #fafafa;
            border-radius: 6px;
        }
        .timer-text { font-family: 'Monaco', monospace; font-weight: 600; color: var(--primary); }
        .timer-text.urgent { color: var(--error); }
        .empty-tip { text-align: center; color: var(--text-sub); font-size: 13px; padding: 10px 0; }

        .footer { text-align: center; font-size: 12px; color: var(--text-sub); margin-top: 20px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header-card {{ 'online' if working else 'offline' }}">
            <div class="status-title">当前系统状态</div>
            <div class="status-value">
                {{ '🟢 客服工作中' if working else '🔴 已下班 (暂停监控)' }}
            </div>
        </div>

        <div class="task-card">
            <div class="card-header">
                <div class="card-title">⏳ 稍等任务 (12分钟)</div>
                <div class="badge {{ 'active' if wait_timers|length > 0 }}">
                    {{ wait_timers|length }}
                </div>
            </div>
            <div class="task-list">
                {% if wait_timers %}
                    {% for mid, end_ts in wait_timers.items() %}
                    <div class="task-item">
                        <span>消息ID: {{ mid }}</span>
                        <span class="timer-text" data-end="{{ end_ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">暂无排队任务</div>
                {% endif %}
            </div>
        </div>

        <div class="task-card">
            <div class="card-header">
                <div class="card-title">🕵️ 跟进任务 (15分钟)</div>
                <div class="badge {{ 'active' if followup_timers|length > 0 }}">
                    {{ followup_timers|length }}
                </div>
            </div>
            <div class="task-list">
                {% if followup_timers %}
                    {% for mid, end_ts in followup_timers.items() %}
                    <div class="task-item">
                        <span>消息ID: {{ mid }}</span>
                        <span class="timer-text" data-end="{{ end_ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">暂无跟进任务</div>
                {% endif %}
            </div>
        </div>

        <div class="task-card">
            <div class="card-header">
                <div class="card-title">🔔 漏回监控 (5分钟)</div>
                <div class="badge {{ 'active' if reply_timers|length > 0 }}">
                    {{ reply_timers|length }}
                </div>
            </div>
            <div class="task-list">
                {% if reply_timers %}
                    {% for mid, end_ts in reply_timers.items() %}
                    <div class="task-item">
                        <span>消息ID: {{ mid }}</span>
                        <span class="timer-text" data-end="{{ end_ts }}">计算中...</span>
                    </div>
                    {% endfor %}
                {% else %}
                    <div class="empty-tip">暂无漏回预警</div>
                {% endif %}
            </div>
        </div>

        <div class="footer">最后更新时间: {{ current_time }}</div>
    </div>

    <script>
        function updateTimers() {
            const now = Date.now() / 1000;
            document.querySelectorAll('.timer-text').forEach(el => {
                const endTs = parseFloat(el.getAttribute('data-end'));
                const diff = endTs - now;
                
                if (diff <= 0) {
                    el.innerText = "已超时";
                    el.classList.add('urgent');
                } else {
                    const m = Math.floor(diff / 60);
                    const s = Math.floor(diff % 60);
                    el.innerText = `${m}分 ${s.toString().padStart(2, '0')}秒`;
                    // 剩余时间少于1分钟变红
                    if (diff < 60) {
                        el.classList.add('urgent');
                    }
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

# ================= 5. 任务逻辑 =================

async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, grouped_id=None):
    try:
        end_time = time.time() + WAIT_TIMEOUT
        wait_timers[key_id] = end_time
        
        if grouped_id:
            if grouped_id not in wait_task_grouped_index: wait_task_grouped_index[grouped_id] = set()
            wait_task_grouped_index[grouped_id].add(key_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return
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
        if grouped_id and grouped_id in wait_task_grouped_index:
            wait_task_grouped_index[grouped_id].discard(key_id)
            if not wait_task_grouped_index[grouped_id]: del wait_task_grouped_index[grouped_id]

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, grouped_id=None):
    try:
        end_time = time.time() + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = end_time

        if grouped_id:
            if grouped_id not in followup_task_grouped_index: followup_task_grouped_index[grouped_id] = set()
            followup_task_grouped_index[grouped_id].add(key_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return
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
        if grouped_id and grouped_id in followup_task_grouped_index:
            followup_task_grouped_index[grouped_id].discard(key_id)
            if not followup_task_grouped_index[grouped_id]: del followup_task_grouped_index[grouped_id]

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, grouped_id=None):
    try:
        end_time = time.time() + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = end_time

        if grouped_id:
            if grouped_id not in reply_task_grouped_index: reply_task_grouped_index[grouped_id] = set()
            reply_task_grouped_index[grouped_id].add(trigger_msg_id)

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
        if grouped_id and grouped_id in reply_task_grouped_index:
            reply_task_grouped_index[grouped_id].discard(trigger_msg_id)
            if not reply_task_grouped_index[grouped_id]: del reply_task_grouped_index[grouped_id]

# ================= 6. 客户端实例 (严格禁止修改) =================
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

# ================= 7. 控制指令 =================
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
        wait_task_grouped_index.clear(); followup_task_grouped_index.clear(); reply_task_grouped_index.clear()
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

# ================= 8. 删除同步 =================
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

# ================= 9. 消息处理主循环 =================
@client.on(events.NewMessage(chats=CS_GROUP_IDS))
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
    is_cs_action = is_sender_cs or is_wait_cmd or is_keep_cmd

    if is_cs_action:
        if reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            reply_gid = getattr(reply_msg, 'grouped_id', None)

            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel(); del reply_tasks[reply_to_msg_id]
            if reply_gid and reply_gid in reply_task_grouped_index:
                for mid in list(reply_task_grouped_index[reply_gid]):
                    if mid in reply_tasks: reply_tasks[mid].cancel(); del reply_tasks[mid]

            if is_keep_cmd:
                if _sys_opt: print(f"[DEBUG] 触发精准跟进({sender_name}): {text.strip()}")
                
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_gid:
                    if reply_gid in wait_task_grouped_index:
                        for mid in list(wait_task_grouped_index[reply_gid]):
                            if mid in wait_tasks: wait_tasks[mid].cancel()
                    if reply_gid in followup_task_grouped_index:
                        for mid in list(followup_task_grouped_index[reply_gid]):
                            if mid in followup_tasks: followup_tasks[mid].cancel()
                
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, reply_gid
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                if _sys_opt: print(f"[DEBUG] 触发稍等({sender_name}): {text.strip()}")
                
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_gid:
                    if reply_gid in followup_task_grouped_index:
                        for mid in list(followup_task_grouped_index[reply_gid]):
                            if mid in followup_tasks: followup_tasks[mid].cancel()
                    if reply_gid in wait_task_grouped_index:
                        for mid in list(wait_task_grouped_index[reply_gid]):
                            if mid in wait_tasks: wait_tasks[mid].cancel()

                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, reply_gid
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

            else:
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_gid:
                    if reply_gid in wait_task_grouped_index:
                        for mid in list(wait_task_grouped_index[reply_gid]):
                            if mid in wait_tasks: wait_tasks[mid].cancel()
                    if reply_gid in followup_task_grouped_index:
                        for mid in list(followup_task_grouped_index[reply_gid]):
                            if mid in followup_tasks: followup_tasks[mid].cancel()

                if _sys_opt: print(f"[DEBUG] 普通结果回复({sender_name})，任务清除: {reply_to_msg_id}")

    else:
        if _sys_opt: print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            if reply_to_msg_id in wait_tasks: 
                wait_tasks[reply_to_msg_id].cancel(); del wait_tasks[reply_to_msg_id]
            if reply_to_msg_id in followup_tasks:
                followup_tasks[reply_to_msg_id].cancel(); del followup_tasks[reply_to_msg_id]
            
            reply_msg = await event.get_reply_message()
            reply_gid = getattr(reply_msg, 'grouped_id', None)
            if reply_gid:
                if reply_gid in wait_task_grouped_index:
                    for mid in list(wait_task_grouped_index[reply_gid]):
                        if mid in wait_tasks: wait_tasks[mid].cancel()
                if reply_gid in followup_task_grouped_index:
                    for mid in list(followup_task_grouped_index[reply_gid]):
                        if mid in followup_tasks: followup_tasks[mid].cancel()

            try:
                replied_msg = await event.get_reply_message()
                target_id = replied_msg.sender_id
                
                if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                    if event.id in reply_tasks: reply_tasks[event.id].cancel()
                    current_grouped_id = getattr(event.message, 'grouped_id', None)
                    task = asyncio.create_task(task_reply_timeout(
                        event.id, sender_name, text[:50], msg_link, current_grouped_id
                    ))
                    reply_tasks[event.id] = task
            except Exception as e: pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 系统启动完成 (默认下班模式)")
    client.start()
    
    try:
        start_msg = "🤖 **系统启动成功**\n当前状态: 🔴 下班 (默认)\n版本: Ver 20.0 (UI Redesign)"
        client.loop.run_until_complete(send_alert(start_msg, ""))
    except Exception as e:
        print(f"❌ 启动通知发送失败: {e}")

    client.run_until_disconnected()
