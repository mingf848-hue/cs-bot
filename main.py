没问题。这就为你加上**启动通知**功能。

只需要在 `client.start()` 之后，程序进入死循环监听之前，执行一次 `send_alert` 即可。

### 🟢 最终完整版 (Ver 12.0)

**更新内容**：

1.  **启动通知**：程序重启/启动完成后，会自动往报警群发送一条 `🤖 系统启动完成` 的消息。
2.  **保留所有功能**：严格的关键词匹配、图集联动、过程词检测、多管理员通知等逻辑全部保留。

<!-- end list -->

```python
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
    
    # 报警人配置
    alert_env = os.environ["ALERT_GROUP_ID"].replace("，", ",")
    ALERT_GROUP_IDS = [int(x.strip()) for x in alert_env.split(',') if x.strip()]
    
    # 【稍等】关键词 (触发12分钟) - 模糊匹配
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    WAIT_SIGNATURES = {x.strip() for x in clean_env.split(',') if x.strip()}

    # 【跟进】关键词 (触发15分钟) - 精准全等匹配
    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    clean_keep = keep_keywords_env.replace("，", ",")
    KEEP_SIGNATURES = {x.strip() for x in clean_keep.split(',') if x.strip()}

except KeyError as e:
    print(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    print(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

# 初始化系统优化选项
_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

print(f"✅ 配置加载成功。群组: {len(CS_GROUP_IDS)} | 稍等词: {len(WAIT_SIGNATURES)} | 精准跟进词: {len(KEEP_SIGNATURES)}")

# ================= 2. 全局参数 =================
WAIT_TIMEOUT = 12 * 60      # 稍等超时
FOLLOWUP_TIMEOUT = 15 * 60  # 跟进超时
REPLY_TIMEOUT = 5 * 60      # 漏回超时

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}

wait_msg_map = {}     
followup_msg_map = {} 
deleted_cache = set()

# 图集索引
wait_task_grouped_index = {} 
followup_task_grouped_index = {} 
reply_task_grouped_index = {}

IS_WORKING = False  # 默认下班
MY_ID = None

# ================= 3. Web服务 =================
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
        <div class="stat-box"><div class="stat-label">运行状态</div><div class="stat-value {{ 'green' if working else 'red' }}">{{ '🟢 工作中' if working else '🔴 已下班' }}</div></div>
        <div class="stat-box"><div class="stat-label">稍等任务 (12m)</div><div class="stat-value">{{ wait_tasks }}</div></div>
        <div class="stat-box"><div class="stat-label">跟进任务 (15m)</div><div class="stat-value">{{ followup_tasks }}</div></div>
        <div class="stat-box"><div class="stat-label">漏回任务 (5m)</div><div class="stat-value">{{ reply_tasks }}</div></div>
        <div class="footer">最后刷新时间: {{ current_time }}</div>
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
        wait_tasks=len(wait_tasks),
        followup_tasks=len(followup_tasks),
        reply_tasks=len(reply_tasks),
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
        print(f"❌ 发送异常 (ID: {payload.get('chat_id')}): {e}")

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

# 1. 稍等超时 (Wait) - 12分钟
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, grouped_id=None):
    try:
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
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        if grouped_id and grouped_id in wait_task_grouped_index:
            wait_task_grouped_index[grouped_id].discard(key_id)
            if not wait_task_grouped_index[grouped_id]: del wait_task_grouped_index[grouped_id]

# 2. 跟进超时 (Follow-up) - 15分钟
async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, grouped_id=None):
    try:
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
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        if grouped_id and grouped_id in followup_task_grouped_index:
            followup_task_grouped_index[grouped_id].discard(key_id)
            if not followup_task_grouped_index[grouped_id]: del followup_task_grouped_index[grouped_id]

# 3. 漏回超时 (Reply) - 5分钟
async def task_reply_timeout(trigger_msg_id, sender_name, content, link, grouped_id=None):
    try:
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
        if grouped_id and grouped_id in reply_task_grouped_index:
            reply_task_grouped_index[grouped_id].discard(trigger_msg_id)
            if not reply_task_grouped_index[grouped_id]: del reply_task_grouped_index[grouped_id]

# ================= 6. 客户端实例 =================
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, device_model="Mac mini M2", app_version="5.10.7", lang_code="zh-hans")

# ================= 7. 控制指令 =================
@client.on(events.NewMessage(chats='me', pattern='^(上班|下班|状态)$'))
async def command_handler(event):
    global IS_WORKING
    cmd = event.text
    if cmd == '下班':
        IS_WORKING = False
        # 清空所有类型的任务
        for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
        wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
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
        # 1. 稍等消息被删 -> 取消稍等
        if msg_id in wait_msg_map:
            cid = wait_msg_map[msg_id]
            if cid in wait_tasks: wait_tasks[cid].cancel()
            del wait_msg_map[msg_id]
        # 2. 跟进消息被删 -> 取消跟进
        if msg_id in followup_msg_map:
            cid = followup_msg_map[msg_id]
            if cid in followup_tasks: followup_tasks[cid].cancel()
            del followup_msg_map[msg_id]
        # 3. 客户回复被删 -> 取消漏回
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

    if sender_id == MY_ID:
        # 指令检测
        # 1. 稍等：模糊匹配 (包含即可)
        is_wait_cmd = any(k in text for k in WAIT_SIGNATURES)
        
        # 2. 跟进：【绝对精准匹配】(去除首尾空格)
        is_keep_cmd = text.strip() in KEEP_SIGNATURES

        if reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[图片/文件]"
            reply_gid = getattr(reply_msg, 'grouped_id', None)

            # ========================================================
            # 无论我回复什么，都先取消【漏回提醒】 (因为我已经回应了)
            # ========================================================
            if reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]
            if reply_gid and reply_gid in reply_task_grouped_index:
                for mid in list(reply_task_grouped_index[reply_gid]):
                    if mid in reply_tasks: reply_tasks[mid].cancel(); del reply_tasks[mid]

            # ========================================================
            # 状态分流逻辑
            # ========================================================
            if is_keep_cmd:
                # 【场景A】触发了精准的"跟进"回复 -> 启动15m任务
                if _sys_opt: print(f"[DEBUG] 触发精准跟进: {text.strip()}")

                # 1. 清除当前问题的所有旧状态 (Wait + Followup)
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_gid:
                    if reply_gid in wait_task_grouped_index:
                        for mid in list(wait_task_grouped_index[reply_gid]):
                            if mid in wait_tasks: wait_tasks[mid].cancel()
                    if reply_gid in followup_task_grouped_index:
                        for mid in list(followup_task_grouped_index[reply_gid]):
                            if mid in followup_tasks: followup_tasks[mid].cancel()
                
                # 2. 启动跟进任务
                task = asyncio.create_task(task_followup_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, reply_gid
                ))
                followup_tasks[reply_to_msg_id] = task
                followup_msg_map[event.id] = reply_to_msg_id

            elif is_wait_cmd:
                # 【场景B】触发了"请稍等"回复 -> 启动12m任务
                # 1. 清除旧状态
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_gid and reply_gid in followup_task_grouped_index:
                    for mid in list(followup_task_grouped_index[reply_gid]):
                        if mid in followup_tasks: followup_tasks[mid].cancel()

                # 2. 取消旧的稍等(为了重置)
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()

                # 3. 启动新的稍等 (12分钟)
                task = asyncio.create_task(task_wait_timeout(
                    reply_to_msg_id, sender_name, reply_content, msg_link, event.id, reply_gid
                ))
                wait_tasks[reply_to_msg_id] = task
                wait_msg_map[event.id] = reply_to_msg_id

            else:
                # 【场景C】普通结果回复，既不是稍等，也不是精准的跟进词
                # 认为已完成，取消当前问题的所有计时
                
                # 清除 Wait
                if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel()
                if reply_gid and reply_gid in wait_task_grouped_index:
                    for mid in list(wait_task_grouped_index[reply_gid]):
                        if mid in wait_tasks: wait_tasks[mid].cancel()
                
                # 清除 Followup
                if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel()
                if reply_gid and reply_gid in followup_task_grouped_index:
                    for mid in list(followup_task_grouped_index[reply_gid]):
                        if mid in followup_tasks: followup_tasks[mid].cancel()

                if _sys_opt: print(f"[DEBUG] 普通结果回复，任务清除: {reply_to_msg_id}")

    else:
        # 客户消息
        if _sys_opt: print(f"[DEBUG] [{group_title}] {sender_name}: {log_text}")

        if reply_to_msg_id:
            # 1. 客户说话了 -> 取消稍等 & 取消跟进 (客户有反馈了，重新开始)
            if reply_to_msg_id in wait_tasks: 
                wait_tasks[reply_to_msg_id].cancel(); del wait_tasks[reply_to_msg_id]
            if reply_to_msg_id in followup_tasks:
                followup_tasks[reply_to_msg_id].cancel(); del followup_tasks[reply_to_msg_id]
            
            # 图集联动取消
            reply_msg = await event.get_reply_message()
            reply_gid = getattr(reply_msg, 'grouped_id', None)
            
            if reply_gid:
                if reply_gid in wait_task_grouped_index:
                    for mid in list(wait_task_grouped_index[reply_gid]):
                        if mid in wait_tasks: wait_tasks[mid].cancel()
                if reply_gid in followup_task_grouped_index:
                    for mid in list(followup_task_grouped_index[reply_gid]):
                        if mid in followup_tasks: followup_tasks[mid].cancel()

            # 2. 启动漏回
            try:
                replied_msg = await event.get_reply_message()
                if replied_msg and replied_msg.sender_id == MY_ID:
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
    
    # 【新增】启动通知
    try:
        start_msg = "🤖 **系统启动成功**\n当前状态: 🔴 下班 (默认)\n版本: Ver 12.0"
        client.loop.run_until_complete(send_alert(start_msg, ""))
    except Exception as e:
        print(f"❌ 启动通知发送失败: {e}")

    client.run_until_disconnected()
```
