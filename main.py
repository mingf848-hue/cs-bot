import os
import sys
import asyncio
import logging
import requests
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 配置区域 =================
API_ID = int(os.environ.get("API_ID", 36407789))
API_HASH = os.environ.get("API_HASH", "8c305aab01036c7f2b08807b3b5f9e5e")
SESSION_STRING = os.environ.get("SESSION_STRING")
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# 监控群组
cs_groups_env = os.environ.get("CS_GROUP_IDS")
try:
    CS_GROUP_IDS = [int(x.strip()) for x in cs_groups_env.split(',') if x.strip()]
except ValueError:
    print("❌ CS_GROUP_IDS 格式错误")
    sys.exit(1)

# 报警接收人
alert_group_env = os.environ.get("ALERT_GROUP_ID")
ALERT_GROUP_ID = int(alert_group_env)

# 时间设置
WAIT_TIMEOUT = 12 * 60   # 稍等超时
REPLY_TIMEOUT = 5 * 60   # 漏回超时

# <核心修改> 触发关键词 (优先读取环境变量)
# 在 Render 环境变量里添加 WAIT_KEYWORDS，值用逗号分隔，如: 稍等1,稍等2
wait_keywords_env = os.environ.get("WAIT_KEYWORDS")

if wait_keywords_env:
    # 支持中文逗号和英文逗号，自动去空格
    clean_env = wait_keywords_env.replace("，", ",")
    WAIT_SIGNATURES = {x.strip() for x in clean_env.split(',') if x.strip()}
    print(f"✅ 已加载自定义稍等关键词 ({len(WAIT_SIGNATURES)}个)")
else:
    # 默认备份 (如果你没设变量，就用这组)
    WAIT_SIGNATURES = {
        "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
        "请稍等-hed", "请稍等-xxxx", "请稍等-mad", "请稍等 - ab", "请稍等art",
        "稍等～ys", "请稍等~lofi", "稍等-so", "请稍等～～aug", "稍等--gr💬",
        "稍等-be", "稍等-xw", "请稍等~d", "请稍等～yu"
    }
    print(f"⚠️ 未检测到 WAIT_KEYWORDS 变量，使用默认关键词列表")

# ================= 全局状态管理 =================
# 1. 任务字典
wait_tasks = {}
reply_tasks = {}

# 2. 稍等消息映射表：用于删除检测
wait_msg_map = {}

# 3. 死亡名单 (秒删防御)
deleted_cache = set()

# 4. 上下班开关 (默认上班)
IS_WORKING = True

# 5. 我的ID
MY_ID = None

# ================= 日志与Web服务 =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

@app.route('/')
def home():
    status = "🟢 工作中" if IS_WORKING else "🔴 已下班"
    return f"Status: {status} | Wait: {len(wait_tasks)} | Reply: {len(reply_tasks)}"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================= 报警发送函数 =================
async def send_alert(text, link):
    if not IS_WORKING: return
    if not BOT_TOKEN:
        print("❌ 未配置 BOT_TOKEN")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": ALERT_GROUP_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: requests.post(url, json=payload))
    except Exception as e:
        print(f"❌ 报警发送失败: {e}")

# ================= 倒计时任务逻辑 =================
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
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
    except asyncio.CancelledError: pass

# ================= 初始化客户端 =================
if not SESSION_STRING: sys.exit("❌ SESSION_STRING Missing")
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="iPhone 14",
    system_version="26.0.1",
    app_version="10.6.1",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

# ================= 1. 遥控指令处理 =================
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
        
        await event.reply("🔴 **已切换为：下班模式**\n所有监控暂停，任务已清空。")
        print("🔴 用户指令：下班")
        
    elif cmd == '上班':
        IS_WORKING = True
        await event.reply("🟢 **已切换为：工作模式**\n监控系统已激活。")
        print("🟢 用户指令：上班")
        
    elif cmd == '状态':
        status_icon = "🟢" if IS_WORKING else "🔴"
        msg = (
            f"{status_icon} **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n"
            f"⏳ 稍等任务: {len(wait_tasks)}\n"
            f"🔔 漏回任务: {len(reply_tasks)}"
        )
        await event.reply(msg)

# ================= 2. 消息删除监听 (秒删防御) =================
@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return

    for msg_id in event.deleted_ids:
        # 1. 记入死亡名单
        deleted_cache.add(msg_id)

        # 2. 如果任务已存在，立即取消
        if msg_id in wait_msg_map:
            customer_msg_id = wait_msg_map[msg_id]
            if customer_msg_id in wait_tasks:
                wait_tasks[customer_msg_id].cancel()
                del wait_tasks[customer_msg_id]
                print(f"🗑️ [删除检测] 消息 {msg_id} 已删，倒计时取消。")
            del wait_msg_map[msg_id]

# ================= 3. 主监控逻辑 =================
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
        # 正常取消逻辑
        if reply_to_msg_id and reply_to_msg_id in reply_tasks:
            reply_tasks[reply_to_msg_id].cancel()
            del reply_tasks[reply_to_msg_id]
            print(f"✅ [已处理] 取消漏回报警")
        
        if reply_to_msg_id and reply_to_msg_id in wait_tasks:
            wait_tasks[reply_to_msg_id].cancel()
            if reply_to_msg_id in wait_tasks: del wait_tasks[reply_to_msg_id] 
            print(f"✅ [已跟进] 取消稍等报警")

        # 触发“稍等”逻辑
        matched = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
        if matched and reply_to_msg_id:
            print(f"⚡️ [触发] 稍等关键词")
            
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[无引用]"
            
            # 秒删防御检查：创建任务前查名单
            if event.id in deleted_cache:
                print(f"🛡️ [秒删防御] 消息 {event.id} 在处理期间被删，放弃创建任务。")
                deleted_cache.discard(event.id)
                return

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

# ================= 启动 =================
if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 监控系统已就绪。")
    client.start()
    client.run_until_disconnected()
