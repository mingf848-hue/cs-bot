import os
import sys
import asyncio
import logging
import requests
from threading import Thread
from flask import Flask, render_template_string
from telethon import TelegramClient, events
from telethon.sessions import StringSession
import time

# ================= 1. 配置加载 =================
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    
    cs_groups_env = os.environ["CS_GROUP_IDS"]
    CS_GROUP_IDS = [int(x.strip()) for x in cs_groups_env.split(',') if x.strip()]
    
    # 【重要修复】支持多人通知：按逗号分割读取多个 ALERT_ID，而不是强转 int
    alert_env = os.environ["ALERT_GROUP_ID"]
    ALERT_GROUP_IDS = [int(x.strip()) for x in alert_env.split(',') if x.strip()]
    
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    # 严格读取环境变量，不包含 =
    WAIT_SIGNATURES = {x.strip() for x in clean_env.split(',') if x.strip()}

except KeyError as e:
    print(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    print(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

# 【重要修复】独立停止词，只停止不计时
STOP_KEYWORDS = {'=', '＝'}

# 初始化系统优化选项
_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

print(f"✅ 配置加载成功。监控群组: {len(CS_GROUP_IDS)} | 报警接收人: {len(ALERT_GROUP_IDS)} | 监听模式: {'🟢 开启' if _sys_opt else '⚪️ 关闭'}")

# ================= 2. 全局参数 =================
WAIT_TIMEOUT = 12 * 60
REPLY_TIMEOUT = 5 * 60

wait_tasks = {}
reply_tasks = {}
wait_msg_map = {}
deleted_cache = set()

# 【重要修复】图集 ID 索引：用于解决回复图集某一张图无法取消任务的问题
wait_task_grouped_index = {} 
reply_task_grouped_index = {}

IS_WORKING = False  # 默认下班
MY_ID = None

# ================= 3. Web服务 (只读状态面板) =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

HTML_TEMPLATE_READONLY = """
<!DOCTYPE html>
<html>
<head>
    <title>系统状态监控</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5"> <style>
        body { background-color: #0d1117; color: #c9d1d9; font-family: monospace; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; padding: 20px 0; }
        .container { background: #161b22; padding: 2rem; border-radius: 12px; border: 1px solid #30363d; box-shadow: 0 4px 20px rgba(0,0,0,0.5); width: 80%; max-width: 450px; text-align: center; }
        h1 { font-size: 1.5rem; color: #58a6ff; margin-bottom: 2rem; border-bottom: 1px solid #30363d; padding-bottom: 10px; }
        .stat-box { background: #21262d; padding: 15px; margin: 15px 0; border-radius: 6px; border: 1px solid #30363d; display: flex; justify-content: space-between; align-items: center; }
        .stat-label { font-size: 0.9rem; color: #8b949e; text-align: left; flex-grow: 1; }
        .stat-value { font-size: 1.1rem; font-weight: bold; text-align: right; }
        .footer { margin-top: 25px; font-size: 0.7rem; color: #58a6ff; }
        .green { color: #238636; }
        .red { color: #da3633; }
        .blue { color: #1f6feb; }
    </style>
</head>
<body>
    <div class="container">
        <h1>系统状态监控 (只读)</h1>
        
        <div class="stat-box">
            <div class="stat-label">运行状态</div>
            <div class="stat-value {{ 'green' if working else 'red' }}">
                {{ '🟢 工作中' if working else '🔴 已下班' }}
            </div>
        </div>

        <div class="stat-box">
            <div class="stat-label">调试模式</div>
            <div class="stat-value {{ 'blue' if spy_on else 'red' }}">
                {{ '开启' if spy_on else '关闭' }}
            </div>
        </div>

        <div class="stat-box">
            <div class="stat-label">排队任务数 (稍等)</div>
            <div class="stat-value">{{ wait_tasks }}</div>
        </div>
        
        <div class="stat-box">
            <div class="stat-label">排队任务数 (漏回)</div>
            <div class="stat-value">{{ reply_tasks }}</div>
        </div>

        <div class="stat-box" style="border-color: #58a6ff;">
            <div class="stat-label">预警最大倒计时 (稍等)</div>
            <div class="stat-value green">
                {{ wait_timeout_min }} 分钟
            </div>
        </div>
        
        <div class="stat-box" style="border-color: #58a6ff;">
            <div class="stat-label">预警最大倒计时 (漏回)</div>
            <div class="stat-value green">
                {{ reply_timeout_min }} 分钟
            </div>
        </div>
        
        <div class="footer">
            最后刷新时间: {{ current_time }}<br>
            状态每 5 秒自动更新一次。
        </div>
    </div>
</body>
</html>
"""

@app.route('/')
def status_page():
    current_time_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    return render_template_string(
        HTML_TEMPLATE_READONLY,
        working=IS_WORKING,
        spy_on=_sys_opt,
        wait_tasks=len(wait_tasks),
        reply_tasks=len(reply_tasks),
        wait_timeout_min=WAIT_TIMEOUT // 60,
        reply_timeout_min=REPLY_TIMEOUT // 60,
        current_time=current_time_str
    )

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ================= 4. 通知模块 =================
def _post_request(url, payload):
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"❌ 网络异常: {e}")

async def send_alert(text, link):
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    
    # 【重要修复】循环发送给 ALERT_GROUP_IDS 列表里的所有人
    for chat_id in ALERT_GROUP_IDS:
        payload = {
            "chat_id": chat_id, 
            "text": text, 
            "parse_mode": "Markdown", 
            "disable_web_page_preview": True
        }
        await loop.run_in_executor(None, lambda: _post_request(url, payload))

# ================= 5. 任务逻辑 =================

# 【重要修复】增加了 grouped_id 参数处理图集
async def task_wait_timeout(key_id, agent_name, original_text, link, my_wait_msg_id, grouped_id=None):
    try:
        # 如果这个消息属于一个图集，记录到索引里
        if grouped_id:
            if grouped_id not in wait_task_grouped_index:
                wait_task_grouped_index[grouped_id] = set()
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
        if my_wait_msg_id in wait_msg_map: del wait_msg_map[my_wait_msg_id]
        if my_wait_msg_id in deleted_cache: deleted_cache.discard(my_wait_msg_id)
        # 清理图集索引
        if grouped_id and grouped_id in wait_task_grouped_index:
            wait_task_grouped_index[grouped_id].discard(key_id)
            if not wait_task_grouped_index[grouped_id]:
                del wait_task_grouped_index[grouped_id]

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, grouped_id=None):
    try:
        if grouped_id:
            if grouped_id not in reply_task_grouped_index:
                reply_task_grouped_index[grouped_id] = set()
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
        if grouped_id and grouped_id in reply_task_grouped_index:
            reply_task_grouped_index[grouped_id].discard(trigger_msg_id)
            if not reply_task_grouped_index[grouped_id]:
                del reply_task_grouped_index[grouped_id]

# ================= 6. 客户端实例 =================
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
        wait_task_grouped_index.clear()
        reply_task_grouped_index.clear()
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

    # ============ 客服 (我) 发言逻辑 ============
    if sender_id == MY_ID:
        
        # 1. 检查是否是停止指令 (包含 = 就算)
        is_stop_command = any(k in text for k in STOP_KEYWORDS)
        
        # 2. 处理“漏回提醒”的取消
        if reply_to_msg_id:
            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]
            
            # 【重要修复】如果是图集，顺便取消同组其他图片
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.grouped_id:
                gid = reply_msg.grouped_id
                if gid in reply_task_grouped_index:
                    ids_to_cancel = list(reply_task_grouped_index[gid])
                    for mid in ids_to_cancel:
                        if mid in reply_tasks:
                            reply_tasks[mid].cancel()
                            del reply_tasks[mid]
                            if _sys_opt: print(f"[DEBUG] 图集联动取消漏回任务: {mid} (Group: {gid})")

        # 3. 处理“稍等提醒”的启动与取消
        # 【重要修复】只有发 = 且有引用时，尝试取消稍等
        if is_stop_command and reply_to_msg_id:
            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()
            
            # 【重要修复】图集联动
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.grouped_id:
                gid = reply_msg.grouped_id
                if gid in wait_task_grouped_index:
                    ids_to_cancel = list(wait_task_grouped_index[gid])
                    for mid in ids_to_cancel:
                        if mid in wait_tasks:
                            wait_tasks[mid].cancel()
                            if _sys_opt: print(f"[DEBUG] 图集联动取消稍等任务: {mid} (Group: {gid})")

        # 4. 启动新的稍等任务 (仅当包含关键词且不包含=时)
        is_wait_command = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
        
        if is_wait_command and reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            # 记录 grouped_id
            target_grouped_id = getattr(reply_msg, 'grouped_id', None)

            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()

            # 启动任务时传入 grouped_id
            task = asyncio.create_task(task_wait_timeout(
                reply_to_msg_id, sender_name, reply_content, msg_link, event.id, target_grouped_id
            ))
            wait_tasks[reply_to_msg_id] = task
            wait_msg_map[event.id] = reply_to_msg_id

    # ============ 客户 (他人) 发言逻辑 ============
    else:
        if _sys_opt:
            print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            # 1. 客户回复了“稍等” -> 取消稍等任务
            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()
                del wait_tasks[reply_to_msg_id] 
            
            # 2. 客户回复了我 -> 启动漏回任务
            try:
                replied_msg = await event.get_reply_message()
                if replied_msg and replied_msg.sender_id == MY_ID:
                    if event.id in reply_tasks: reply_tasks[event.id].cancel()

                    current_grouped_id = getattr(event.message, 'grouped_id', None)

                    task = asyncio.create_task(task_reply_timeout(
                        event.id, sender_name, text[:50], msg_link, current_grouped_id
                    ))
                    reply_tasks[event.id] = task
            except Exception as e:
                pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 系统启动完成 (默认下班模式)")
    client.start()
    client.run_until_disconnected()
