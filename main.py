import os
import sys
import asyncio
import logging
from threading import Thread
from flask import Flask
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ================= 配置区域 (从环境变量读取) =================

# 1. 读取 API 配置
API_ID = int(os.environ.get("API_ID", 36407789))
API_HASH = os.environ.get("API_HASH", "8c305aab01036c7f2b08807b3b5f9e5e")
SESSION_STRING = os.environ.get("SESSION_STRING")

# 2. 读取监控群 ID (支持多群，用逗号分隔)
# 例如 Render 环境变量填: -100123456,-100987654
cs_groups_env = os.environ.get("CS_GROUP_IDS", "-1003400471795")
try:
    # 将字符串分割并转为整数列表
    CS_GROUP_IDS = [int(x.strip()) for x in cs_groups_env.split(',') if x.strip()]
except ValueError:
    print("❌ 错误: CS_GROUP_IDS 格式错误，请确保只包含数字和逗号")
    sys.exit(1)

# 3. 读取报警群 ID (单个)
alert_group_env = os.environ.get("ALERT_GROUP_ID", "-5093247908")
try:
    ALERT_GROUP_ID = int(alert_group_env)
except ValueError:
    print("❌ 错误: ALERT_GROUP_ID 必须是数字")
    sys.exit(1)

# 4. 其他配置
TIMEOUT_SECONDS = 12 * 60  # 12分钟

# 触发关键词
WAIT_SIGNATURES = {
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-hed", "请稍等-xxxx", "请稍等-mad", "请稍等 - ab", "请稍等art",
    "稍等～ys", "请稍等~lofi", "稍等-so", "请稍等～～aug", "稍等--gr💬",
    "稍等-be", "稍等-xw", "请稍等~d", "请稍等～yu"
}

# ================= 日志设置 =================
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO, stream=sys.stdout)

# ================= 1. Flask 伪装服务 =================
app = Flask(__name__)

@app.route('/')
def home():
    return f"✅ Userbot Running. Monitoring {len(CS_GROUP_IDS)} groups."

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# ================= 2. Telethon 监控逻辑 =================
if not SESSION_STRING:
    print("❌ 严重错误: 环境变量 SESSION_STRING 未设置！")
    sys.exit(1)

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

# 这里的 chats 参数接受一个列表，从而实现多群监控
@client.on(events.NewMessage(chats=CS_GROUP_IDS))
async def handler(event):
    text = event.text or ""
    matched = any(sig.lower() in text.lower() for sig in WAIT_SIGNATURES)
    
    if matched:
        print(f"⚡️ [监控触发] 群{event.chat_id} 内容: {text[:10]}...")
        
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', 'Unknown')
        
        # 获取群组 ID (去掉 -100 前缀用于链接)
        chat_id_str = str(event.chat_id).replace('-100', '')
        msg_link = f"https://t.me/c/{chat_id_str}/{event.id}"
        
        reply_msg = await event.get_reply_message()
        reply_content = reply_msg.text[:50] if reply_msg and reply_msg.text else "[无引用]"

        asyncio.create_task(wait_and_alert(event.id, sender_name, reply_content, msg_link))

async def wait_and_alert(msg_id, agent_name, original_text, link):
    try:
        await asyncio.sleep(TIMEOUT_SECONDS)
        
        alert_text = (
            f"📩 消息: `{original_text.replace('`', '')}`\n"
            f"🚨 **超时预警**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: 已等待 {TIMEOUT_SECONDS // 60} 分钟\n"
            f"🔗 [点击处理]({link})"
        )
        
        await client.send_message(ALERT_GROUP_ID, alert_text, link_preview=False)
        print(f"✅ [已报警] 原消息ID: {msg_id}")
        
    except Exception as e:
        print(f"❌ 报警发送失败: {e}")

# ================= 3. 启动入口 =================
if __name__ == '__main__':
    t = Thread(target=run_web)
    t.start()
    
    print(f"正在启动 Telethon... 监控群组列表: {CS_GROUP_IDS}")
    try:
        client.start()
        client.run_until_disconnected()
    except Exception as e:
        print(f"❌ 启动失败: {e}")
