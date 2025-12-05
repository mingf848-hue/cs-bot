import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ================= 你的配置 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795  
ALERT_GROUP_ID = -5093247908

# 正式模式：15 分钟 (如果您还没改回 15*60，现在可以改了)
TIMEOUT_SECONDS =  60 

# 触发关键词列表
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= Web Server =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running (Link Fixed)"

def run_web_server():
    port = int(os.environ.get('PORT', 8080))
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# ================= 机器人逻辑 =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)

pending_jobs = {}

async def alert_callback(context: ContextTypes.DEFAULT_TYPE):
    """倒计时结束，执行报警"""
    job_data = context.job.data
    original_msg_id = job_data['original_msg_id']
    trigger_msg_link = job_data['trigger_msg_link']
    original_user = job_data['original_user']
    trigger_keyword = job_data['trigger_keyword']

    if original_msg_id in pending_jobs:
        del pending_jobs[original_msg_id]

    # 注意：这里的 TIMEOUT_SECONDS 应该用 15 * 60
    current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"
    if TIMEOUT_SECONDS == 60:
        current_timeout_display = "1 分钟 (测试版)"

    alert_text = (
        f"🚨 **客服超时预警 ({current_timeout_display})**\n\n"
        f"👤 客户: {original_user}\n"
        f"🔑 触发签名: `{trigger_keyword}`\n"
        f"⚠️ 状态: 客服回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
        f"🔗 [点击跳转处理]({trigger_msg_link})"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ALERT_GROUP_ID,
            text=alert_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    except Exception as e:
        print(f"❌ 发送失败！错误详情: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return

    if msg.chat_id == CS_GROUP_ID and msg.reply_to_message:
        original_msg_id = msg.reply_to_message.message_id
        
        matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_signature:
            original_user = "用户"
            if msg.reply_to_message.from_user:
                original_user = msg.reply_to_message.from_user.first_name
            
            # === 修正链接逻辑：确保生成的是 t.me/c/POSITIVE_ID/message_id ===
            # 1. 移除负号，得到正数 ID (e.g., -4990486181 -> 4990486181)
            positive_chat_id = str(CS_GROUP_ID).lstrip('-')
            # 2. 生成正确的深层链接
            msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"
            # =========================================================

            print(f"✅ 监控开启 ({TIMEOUT_SECONDS}s)")

            if original_msg_id in pending_jobs:
                pending_jobs[original_msg_id].schedule_removal()

            new_job = context.job_queue.run_once(
                alert_callback, 
                TIMEOUT_SECONDS, 
                data={
                    'original_msg_id': original_msg_id,
                    'trigger_msg_link': msg_link,
                    'original_user': original_user,
                    'trigger_keyword': matched_signature
                }
            )
            pending_jobs[original_msg_id] = new_job
            return

        if original_msg_id in pending_jobs:
            job = pending_jobs[original_msg_id]
            job.schedule_removal()
            del pending_jobs[original_msg_id]
            print(f"❎ 监控解除")

if __name__ == '__main__':
    threading.Thread(target=run_web_server).start()
    print("Bot 正在启动 (链接修正版)...")
    request_config = HTTPXRequest(connection_pool_size=8, read_timeout=20.0, write_timeout=20.0, connect_timeout=20.0, http_version="1.1")
    application = Application.builder().token(TOKEN).request(request_config).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, timeout=15)
