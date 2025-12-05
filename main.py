import logging
import os
import asyncio
from flask import Flask, request
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from telegram.constants import ChatType

# ================= 你的配置 (保持不变) =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795     
ALERT_GROUP_ID = -5093247908  

# ✅ 正式模式：15 分钟
TIMEOUT_SECONDS = 15 * 60 

# 触发关键词列表
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 核心组件和初始化 =================
# 1. 初始化 Flask App (用于接收 Webhook)
app = Flask(__name__)
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# 2. 初始化 Application (将 Bot 逻辑与 Webhook 连接)
REQUEST_CONFIG = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(REQUEST_CONFIG).build()

# 3. Bot 业务逻辑 (与 Polling 模式一致，但无需修改)
pending_jobs = {}

async def get_cached_group_username(context: ContextTypes.DEFAULT_TYPE):
    # (Link logic remains here, fetches username or uses numeric ID)
    if context.bot_data.get('cs_group_username'): return context.bot_data['cs_group_username']

    try:
        chat = await context.bot.get_chat(chat_id=CS_GROUP_ID)
        if chat.username:
            username = chat.username
            context.bot_data['cs_group_username'] = username
            return username
        else:
            context.bot_data['cs_group_username'] = 'numeric_id'
            return 'numeric_id'
    except Exception:
        return 'numeric_id'

async def alert_callback(context: ContextTypes.DEFAULT_TYPE):
    job_data = context.job.data
    original_msg_id = job_data['original_msg_id']
    trigger_msg_link = job_data['trigger_msg_link']
    original_user = job_data['original_user']
    trigger_keyword = job_data['trigger_keyword']

    if original_msg_id in pending_jobs: del pending_jobs[original_msg_id]

    current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"

    alert_text = (
        f"🚨 **客服超时预警 ({current_timeout_display})**\n\n"
        f"👤 客户: {original_user}\n"
        f"🔑 触发签名: `{trigger_keyword}`\n"
        f"⚠️ 状态: 客服回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
        f"🔗 [点击跳转处理]({trigger_msg_link})"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ALERT_GROUP_ID, text=alert_text, parse_mode='Markdown', disable_web_page_preview=True
        )
    except Exception as e:
        print(f"❌ 发送失败！Telegram Error: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.reply_to_message or msg.chat_id != CS_GROUP_ID:
        return

    original_msg_id = msg.reply_to_message.message_id
    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

    if matched_signature:
        original_user = msg.reply_to_message.from_user.first_name if msg.reply_to_message.from_user else "用户"
        
        # 链接生成逻辑
        link_type = await get_cached_group_username(context)
        if link_type == 'numeric_id':
            positive_chat_id = str(CS_GROUP_ID)[4:] if str(CS_GROUP_ID).startswith('-100') else str(abs(CS_GROUP_ID))
            msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"
        else:
            msg_link = f"https://t.me/{link_type}/{original_msg_id}"

        if original_msg_id in pending_jobs: pending_jobs[original_msg_id].schedule_removal()

        new_job = context.job_queue.run_once(
            alert_callback, TIMEOUT_SECONDS, 
            data={'original_msg_id': original_msg_id, 'trigger_msg_link': msg_link, 'original_user': original_user, 'trigger_keyword': matched_signature}
        )
        pending_jobs[original_msg_id] = new_job
        await asyncio.sleep(0.1) 
        return

    if original_msg_id in pending_jobs:
        job = pending_jobs[original_msg_id]
        job.schedule_removal()
        del pending_jobs[original_msg_id]
        await asyncio.sleep(0.1) 

# 4. 注册 Handler
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

# 5. Webhook 路由 (接收 Telegram 推送的消息)
@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    # 将 Telegram 发送的 JSON 转换成 Update 对象
    update = Update.de_json(await request.get_json(force=True), application.bot)
    
    # 将 Update 对象交给 Application 处理
    await application.process_update(update)
    
    # 必须立刻返回 200 OK，告诉 Telegram 消息已收到
    return "ok"

# 6. 首页路由 (Render 健康检查)
@app.route('/', methods=['GET'])
def index():
    return "Bot Webhook Server is running."

# 7. 主程序启动 (由 Gunicorn 负责)
if __name__ == '__main__':
    # 仅在本地测试时使用 Flask 自带的 run()
    port = int(os.environ.get('PORT', 8080))
    # Render 上不需要这个，由 Gunicorn 启动
    # app.run(host='0.0.0.0', port=port) 
    print("WARNING: Run with 'gunicorn main:app' on Render.")
