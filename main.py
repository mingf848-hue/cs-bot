import os
import sys
import asyncio
import logging
import requests
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 1. 强制读取配置 =================
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

except KeyError as e:
    print(f"❌ 启动失败：缺少必要环境变量 {e}")
    sys.exit(1)
except ValueError as e:
    print(f"❌ 启动失败：变量格式错误 -> {e}")
    sys.exit(1)

print(f"✅ 配置加载成功。监控群组数: {len(CS_GROUP_IDS)} | 关键词数: {len(WAIT_SIGNATURES)}")

# 时间设置
WAIT_TIMEOUT = 12 * 60   # 稍等超时
REPLY_TIMEOUT = 5 * 60   # 漏回超时

# ================= 2. 全局状态管理 =================
wait_tasks = {}
reply_tasks = {}
wait_msg_map = {}
deleted_cache = set()
IS_WORKING = True
MY_ID = None

# ================= 3. 日志与Web服务 =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

@app.route('/')
def home():
    status = "🟢 工作中" if IS_WORKING else "🔴 已下班"
    return f"Status: {status} | Wait: {len(wait_tasks)} | Reply: {len(reply_tasks)}"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================= 4. 报警发送函数 =================
def _post_request(url, payload):
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"❌ 报警发送被拒绝! 状态码: {resp.status_code}")
            print(f"❌ 错误详情: {resp.text}") 
        else:
            print(f"✅ 报警发送成功 (Status 200)")
    except Exception as e:
        print(f"❌ 网络请求异常: {e}")

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

# ================= 5. 倒计时任务逻辑 =================
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
    except asyncio.CancelledError:
        pass
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
    except asyncio.CancelledError:
        pass
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]

# ================= 6. 初始化客户端 (完全按你要求配置) =================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    
    # 1. 设备名称
    device_model="Mac mini M2", 
    
    # 2. App 版本号
    app_version="5.8.3 arm64",     
    
    # 3. 系统版本
    system_version="macOS 15.6.1",
    
    # 语言设置
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

# ================= 7. 遥控指令处理 =================
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
        
        await send_alert("🔴 **已切换为：下班模式**\n😴 所有监控暂停，任务已清空。好好休息！", "")
        print("🔴 用户指令：下班")
        
    elif cmd == '上班':
        IS_WORKING = True
        await send_alert("🟢 **已切换为：工作模式**\n💪 监控系统已激活，准备战斗！", "")
        print("🟢 用户指令：上班")
        
    elif cmd == '状态':
        status_icon = "🟢" if IS_WORKING else "🔴"
        msg = (
            f"{status_icon} **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n"
            f"⏳ 稍等任务: {len(wait_tasks)}\n"
            f"🔔 漏回任务: {len(reply_tasks)}"
        )
        await send_alert(msg, "")

# ================= 8. 消息删除监听 =================
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
                print(f"🗑️ [删除检测] 消息 {msg_id} 已删，倒计时取消。")
            del wait_msg_map[msg_id]

# ================= 9. 主监控逻辑 =================
@client.on(events.NewMessage(chats=CS_GROUP_IDS))
async def handler(event):
    global MY_ID
    if not MY_ID: MY_ID = (await client.get_me()).id
    if not IS_WORKING: return

    text = event.text or ""
    sender_id = event.sender_id
    reply_to_msg_id = event.reply_to_msg_id
    sender = await event.get_sender()
    sender_name = getattr(sender, 'first_name', 'Unknown')
    chat_id_str = str(event.chat_id).replace('-100', '')
    msg_link = f"https://t.me/c/{chat_id_str}/{event.id}"

    # 场景 1: 我说话了
    if sender_id == MY_ID:
        if reply_to_msg_id and reply_to_msg_id in reply_tasks:
            reply_tasks[reply_to_msg_id].cancel()
            del reply_tasks[reply_to_msg_id]
            print(f"✅ [已处理] 取消漏回报警")
        
        if reply_to_msg_id and reply_to_msg_id in wait_tasks:
            wait_tasks[reply_to_msg_id].cancel()
            if reply_to_msg_id in wait_tasks: del wait_tasks[reply_to_msg_id] 
            print(f"✅ [已跟进] 取消稍等报警")

        matched = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
        if matched and reply_to_msg_id:
            print(f"⚡️ [触发] 稍等关键词")
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[无引用]"
            
            if event.id in deleted_cache: return

            task = asyncio.create_task(task_wait_timeout(
                reply_to_msg_id, sender_name, reply_content, msg_link, event.id
            ))
            wait_tasks[reply_to_msg_id] = task
            wait_msg_map[event.id] = reply_to_msg_id

    # 场景 2: 别人说话了
    else:
        if reply_to_msg_id:
            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()
                if reply_to_msg_id in wait_tasks: del wait_tasks[reply_to_msg_id]
                print(f"✅ [客户回复] 取消稍等报警")
            
            try:
                replied_msg = await event.get_reply_message()
                if replied_msg and replied_msg.sender_id == MY_ID:
                    print(f"👀 [有人回我] 启动漏回监控")
                    task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link))
                    reply_tasks[event.id] = task
            except Exception as e:
                pass

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 监控系统启动。设备标识：Mac mini M2 (v5.8.3)。")
    client.start()
    client.run_until_disconnected()
