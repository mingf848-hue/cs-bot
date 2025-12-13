
import os
import sys
import asyncio
import logging
import requests
from threading import Thread
from flask import Flask, request, render_template_string, redirect
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 1. 配置加载 =================
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    
    cs_groups_env = os.environ["CS_GROUP_IDS"]
    CS_GROUP_IDS = [int(x.strip()) for x in cs_groups_env.split(',') if x.strip()]
    
    ALERT_GROUP_ID = int(os.environ["ALERT_GROUP_ID"])
    
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    WAIT_SIGNATURES = {x.strip() for x in clean_env.split(',') if x.strip()}

    # 面板密码 (默认 123456)
    PANEL_PASSWORD = os.environ.get("PANEL_PASSWORD", "123456")

except KeyError as e:
    print(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    print(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

# 初始化系统优化选项 (实际为隐蔽模式开关)
_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

print(f"✅ 配置加载成功。监控群组: {len(CS_GROUP_IDS)}")

# ================= 2. 全局参数 =================
WAIT_TIMEOUT = 12 * 60
REPLY_TIMEOUT = 5 * 60

wait_tasks = {}
reply_tasks = {}
wait_msg_map = {}
deleted_cache = set()
IS_WORKING = True
MY_ID = None

# ================= 3. Web服务 (控制面板) =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>System Control</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: monospace; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }
        .container { background: #161b22; padding: 2rem; border-radius: 12px; border: 1px solid #30363d; box-shadow: 0 4px 20px rgba(0,0,0,0.5); width: 300px; text-align: center; }
        h1 { font-size: 1.2rem; color: #58a6ff; margin-bottom: 1.5rem; text-transform: uppercase; letter-spacing: 2px; }
        .stat-box { background: #21262d; padding: 10px; margin: 10px 0; border-radius: 6px; border: 1px solid #30363d; }
        .stat-label { font-size: 0.8rem; color: #8b949e; }
        .stat-value { font-size: 1.2rem; font-weight: bold; }
        .btn { width: 100%; padding: 12px; margin-top: 10px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; transition: 0.2s; font-family: monospace; }
        .btn-work { background: #238636; color: white; }
        .btn-off { background: #da3633; color: white; }
        .btn-spy-on { background: #1f6feb; color: white; } 
        .btn-spy-off { background: #21262d; color: #8b949e; border: 1px solid #30363d; }
        .login-input { width: 90%; padding: 10px; margin-bottom: 10px; background: #0d1117; border: 1px solid #30363d; color: white; border-radius: 6px; text-align: center; }
    </style>
</head>
<body>
    <div class="container">
        {% if not auth %}
            <h1>Security Check</h1>
            <form method="post">
                <input type="password" name="password" class="login-input" placeholder="ACCESS CODE" required>
                <button type="submit" class="btn btn-work">LOGIN</button>
            </form>
        {% else %}
            <h1>Core System</h1>
            
            <div class="stat-box">
                <div class="stat-label">STATUS</div>
                <div class="stat-value" style="color: {{ 'lightgreen' if working else 'red' }}">
                    {{ 'ACTIVE' if working else 'OFFLINE' }}
                </div>
            </div>

            <div class="stat-box">
                <div class="stat-label">PENDING TASKS</div>
                <div class="stat-value">{{ tasks }}</div>
            </div>

            <form method="post" action="/action">
                <input type="hidden" name="password" value="{{ password }}">
                
                {% if working %}
                    <button name="cmd" value="toggle_work" class="btn btn-off">STOP SYSTEM</button>
                {% else %}
                    <button name="cmd" value="toggle_work" class="btn btn-work">START SYSTEM</button>
                {% endif %}

                <button name="cmd" value="toggle_spy" class="btn {{ 'btn-spy-on' if spy else 'btn-spy-off' }}" style="margin-top: 20px; font-size: 0.8rem;">
                    {{ 'DEBUG MODE: ON' if spy else 'DEBUG MODE: OFF' }}
                </button>
            </form>
        {% endif %}
    </div>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def home():
    auth = False
    password = ""
    
    if request.method == 'POST':
        pwd = request.form.get('password')
        if pwd == PANEL_PASSWORD:
            auth = True
            password = pwd
    
    if request.args.get('pwd') == PANEL_PASSWORD:
        auth = True
        password = PANEL_PASSWORD

    return render_template_string(HTML_TEMPLATE, auth=auth, password=password, working=IS_WORKING, tasks=len(wait_tasks), spy=_sys_opt)

@app.route('/action', methods=['POST'])
def action():
    global IS_WORKING, _sys_opt, wait_tasks, reply_tasks, wait_msg_map, deleted_cache
    
    pwd = request.form.get('password')
    if pwd != PANEL_PASSWORD:
        return "ACCESS DENIED", 403
        
    cmd = request.form.get('cmd')
    
    if cmd == 'toggle_work':
        IS_WORKING = not IS_WORKING
        if not IS_WORKING:
            for task in wait_tasks.values(): task.cancel()
            for task in reply_tasks.values(): task.cancel()
            wait_tasks.clear()
            reply_tasks.clear()
            wait_msg_map.clear()
            deleted_cache.clear()
        asyncio.run_coroutine_threadsafe(send_alert(f"{'🟢' if IS_WORKING else '🔴'} **面板操作**: {'工作模式' if IS_WORKING else '下班模式'}", ""), client.loop)

    elif cmd == 'toggle_spy':
        _sys_opt = not _sys_opt

    return redirect(f"/?pwd={pwd}")

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================= 4. 通知模块 =================
def _post_request(url, payload):
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"❌ 发送失败: {resp.status_code}")
    except Exception as e:
        print(f"❌ 网络异常: {e}")

async def send_alert(text, link):
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ALERT_GROUP_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _post_request(url, payload))

# ================= 5. 任务逻辑 =================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_wait_msg_id):
    try:
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
        if my_wait_msg_id in wait_msg_map: del wait_msg_map[my_wait_msg_id]
        if my_wait_msg_id in deleted_cache: deleted_cache.discard(my_wait_msg_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link):
    try:
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

# ================= 6. 客户端实例 (参数已锁死) =================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="Mac mini M2",
    app_version="5.10.7 arm64",     
    system_version="macOS 15.6.1",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

# ================= 7. 控制指令 =================
@client.on(events.NewMessage(chats='me', pattern='^(上班|下班|状态)$'))
async def command_handler(event):
    global IS_WORKING, wait_tasks, reply_tasks, wait_msg_map, deleted_cache
    cmd = event.text
    if cmd == '下班':
        IS_WORKING = False
        for task in wait_tasks.values(): task.cancel()
        for task in reply_tasks.values(): task.cancel()
        wait_tasks.clear()
        reply_tasks.clear()
        wait_msg_map.clear()
        deleted_cache.clear()
        await send_alert("🔴 **已切换为：下班模式**", "")
    elif cmd == '上班':
        IS_WORKING = True
        await send_alert("🟢 **已切换为：工作模式**", "")
    elif cmd == '状态':
        status_icon = "🟢" if IS_WORKING else "🔴"
        msg = (
            f"{status_icon} **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n"
            f"⏳ 稍等任务: {len(wait_tasks)}\n"
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
            customer_msg_id = wait_msg_map[msg_id]
            if customer_msg_id in wait_tasks:
                wait_tasks[customer_msg_id].cancel()
                del wait_tasks[customer_msg_id]
            del wait_msg_map[msg_id]

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

    if sender_id == MY_ID:
        if reply_to_msg_id and reply_to_msg_id in reply_tasks:
            reply_tasks[reply_to_msg_id].cancel()
            del reply_tasks[reply_to_msg_id]
        
        if reply_to_msg_id and reply_to_msg_id in wait_tasks:
            wait_tasks[reply_to_msg_id].cancel()
            if reply_to_msg_id in wait_tasks: del wait_tasks[reply_to_msg_id] 

        matched = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
        if matched and reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[无引用]"
            if event.id in deleted_cache: return
            task = asyncio.create_task(task_wait_timeout(
                reply_to_msg_id, sender_name, reply_content, msg_link, event.id
            ))
            wait_tasks[reply_to_msg_id] = task
            wait_msg_map[event.id] = reply_to_msg_id

    else:
        # [System Debug Stream] - 修复后的隐蔽日志输出
        if _sys_opt:
            print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in wait_tasks: del wait_tasks[reply_to_msg_id]
            
            try:
                replied_msg = await event.get_reply_message()
                if replied_msg and replied_msg.sender_id == MY_ID:
                    task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link))
                    reply_tasks[event.id] = task
            except Exception as e:
                pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 系统启动完成")
    client.start()
    client.run_until_disconnected()
