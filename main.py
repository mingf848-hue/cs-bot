import os
import sys
import asyncio
import logging
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 配置区域 =================
API_ID = int(os.environ.get("API_ID", 36407789))
API_HASH = os.environ.get("API_HASH", "8c305aab01036c7f2b08807b3b5f9e5e")
SESSION_STRING = os.environ.get("SESSION_STRING")

# 监控群组
cs_groups_env = os.environ.get("CS_GROUP_IDS")
try:
    CS_GROUP_IDS = [int(x.strip()) for x in cs_groups_env.split(',') if x.strip()]
except ValueError:
    print("❌ CS_GROUP_IDS 格式错误")
    sys.exit(1)

# 报警群组
alert_group_env = os.environ.get("ALERT_GROUP_ID")
ALERT_GROUP_ID = int(alert_group_env)

# 时间设置
WAIT_TIMEOUT = 12 * 60   # 我说完“稍等”后的超时时间
REPLY_TIMEOUT = 5 * 60   # 别人回复我，我多久没理就算超时 (5分钟)

# 触发关键词
WAIT_SIGNATURES = {
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-hed", "请稍等-xxxx", "请稍等-mad", "请稍等 - ab", "请稍等art",
    "稍等～ys", "请稍等~lofi", "稍等-so", "请稍等～～aug", "稍等--gr💬",
    "稍等-be", "稍等-xw", "请稍等~d", "请稍等～yu"
}

# ================= 内存状态管理 =================
# 1. 我让别人稍等: { 别人消息ID: Task }
wait_tasks = {}
# 2. 别人回复了我: { 别人消息ID: Task }
reply_tasks = {}

# 全局变量存我的ID
MY_ID = None

# ================= 日志与Web服务 =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)
app = Flask(__name__)

@app.route('/')
def home():
    return f"✅ Userbot Active. Wait Tasks: {len(wait_tasks)} | Reply Tasks: {len(reply_tasks)}"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================= 报警发送函数 =================
async def send_alert(text, link):
    try:
        await client.send_message(ALERT_GROUP_ID, text, link_preview=False)
    except Exception as e:
        print(f"❌ 报警发送失败: {e}")

# ================= 倒计时任务逻辑 =================

# A. “稍等”超时逻辑
async def task_wait_timeout(key_id, agent_name, original_text, link):
    try:
        await asyncio.sleep(WAIT_TIMEOUT)
        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **稍等-超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: 说完稍等已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n"
            f"🔗 [点击处理]({link})"
        )
        await send_alert(alert_text, link)
        if key_id in wait_tasks: del wait_tasks[key_id]
    except asyncio.CancelledError: pass

# B. “漏回”超时逻辑 (新增)
async def task_reply_timeout(trigger_msg_id, sender_name, content, link):
    try:
        await asyncio.sleep(REPLY_TIMEOUT)
        alert_text = (
            f"📩 内容: `{content.replace('`', '')}`\n"
            f"🔔 **漏回消息提醒**\n"
            f"👤 用户: {sender_name} 回复了你\n"
            f"⚠️ 状态: 你已经 {REPLY_TIMEOUT // 60} 分钟没理他了！\n"
            f"🔗 [点击回复]({link})"
        )
        await send_alert(alert_text, link)
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
    except asyncio.CancelledError: pass

# ================= 主监控逻辑 =================
if not SESSION_STRING: sys.exit("❌ SESSION_STRING Missing")
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="iPhone 14",  # 设备型号
    system_version="26.0.1",       # 系统版本
    app_version="10.6.1",          # App版本
    lang_code="zh-hans",           # 语言
    system_lang_code="zh-hans"
)

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
async def handler(event):
    global MY_ID
    # 确保获取到我的ID
    if not MY_ID: MY_ID = (await client.get_me()).id

    text = event.text or ""
    sender_id = event.sender_id
    reply_to_msg_id = event.reply_to_msg_id
    
    # 基础信息
    sender = await event.get_sender()
    sender_name = getattr(sender, 'first_name', 'Unknown')
    chat_id_str = str(event.chat_id).replace('-100', '')
    msg_link = f"https://t.me/c/{chat_id_str}/{event.id}"

    # ==========================================
    # 场景 1: 我说话了 (处理取消逻辑)
    # ==========================================
    if sender_id == MY_ID:
        # 如果我回复了某条消息，检查是不是在“漏回监控”里
        if reply_to_msg_id and reply_to_msg_id in reply_tasks:
            reply_tasks[reply_to_msg_id].cancel()
            del reply_tasks[reply_to_msg_id]
            print(f"✅ [已处理] 我回复了消息 {reply_to_msg_id}，取消漏回报警")
        
        # 同时也检查“稍等监控”的取消逻辑
        if reply_to_msg_id and reply_to_msg_id in wait_tasks:
            wait_tasks[reply_to_msg_id].cancel()
            del wait_tasks[reply_to_msg_id]
            print(f"✅ [已跟进] 我跟进了消息 {reply_to_msg_id}，取消稍等报警")

        # 检查是否触发新的“稍等”
        matched = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
        if matched and reply_to_msg_id:
            print(f"⚡️ [触发] 稍等关键词: {text[:10]}...")
            reply_msg = await event.get_reply_message()
            reply_content = reply_msg.text[:50] if reply_msg else "[无引用]"
            
            task = asyncio.create_task(task_wait_timeout(reply_to_msg_id, sender_name, reply_content, msg_link))
            wait_tasks[reply_to_msg_id] = task

    # ==========================================
    # 场景 2: 别人说话了 (检查是不是回我)
    # ==========================================
    else:
        # 如果这一句话是对某条消息的回复
        if reply_to_msg_id:
            # 1. 检查是否取消我的“稍等”任务 (客户回复了)
            if reply_to_msg_id in wait_tasks:
                wait_tasks[reply_to_msg_id].cancel()
                del wait_tasks[reply_to_msg_id]
                print(f"✅ [客户回复] 客户回应了 {reply_to_msg_id}，取消稍等报警")
            
            # 2. 核心新增：检查是不是【回复我】
            # 需要获取被回复的那条消息对象，看作者是不是我
            try:
                replied_msg = await event.get_reply_message()
                if replied_msg and replied_msg.sender_id == MY_ID:
                    print(f"👀 [有人回我] 用户 {sender_name} 回复了我的消息")
                    # 启动漏回报警倒计时 (以当前这条新消息ID为Key)
                    task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link))
                    reply_tasks[event.id] = task
            except Exception as e:
                print(f"获取引用消息失败: {e}")

# ================= 启动 =================
if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"✅ 双向监控已启动。监控群: {CS_GROUP_IDS}")
    client.start()
    client.run_until_disconnected()
