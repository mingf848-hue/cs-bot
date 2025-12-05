import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ================= 你的配置 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1004990486181
ALERT_GROUP_ID = -1005093247908
TIMEOUT_SECONDS = 15 * 60  # 15分钟

# 触发关键词列表
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= Web Server (Render 必须) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!"

def run_web_server():
    # Render 会提供 PORT 环境变量，默认用 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

# ================= 机器人逻辑 =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

pending_jobs = {}

async def alert_callback(context: ContextTypes.DEFAULT_TYPE):
    """时间到，发送预警"""
    job_data = context.job.data
    original_msg_id = job_data['original_msg_id']
    trigger_msg_link = job_data['trigger_msg_link']
    original_user = job_data['original_user']
    trigger_keyword = job_data['trigger_keyword']

    if original_msg_id in pending_jobs:
        del pending_jobs[original_msg_id]

    alert_text = (
        f"🚨 **超时预警 (15分钟)**\n\n"
        f"👤 客户: {original_user}\n"
        f"🔑 触发签名: `{trigger_keyword}`\n"
        f"⚠️ 状态: 客服回复稍等后，超过15分钟未进一步回复。\n\n"
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
        logging.error(f"发送预警失败: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return

    # 简单的ID打印调试
    if msg.chat_id != CS_GROUP_ID and msg.chat_id != ALERT_GROUP_ID:
        print(f"当前收到消息的群ID: {msg.chat_id}")

    # 必须在客服群，且必须是回复消息
    if msg.chat_id == CS_GROUP_ID and msg.reply_to_message:
        original_msg_id = msg.reply_to_message.message_id
        
        # --- 逻辑 A: 检查是否包含签名 (开启监控) ---
        matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_signature:
            original_user = "用户"
            if msg.reply_to_message.from_user:
                original_user = msg.reply_to_message.from_user.first_name
            
            clean_chat_id = str(CS_GROUP_ID).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"

            print(f"✅ 监控开启: {original_user} | 签名: {matched_signature}")

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

        # --- 逻辑 B: 检查后续回复 (取消监控) ---
        if original_msg_id in pending_jobs:
            job = pending_jobs[original_msg_id]
            job.schedule_removal()
            del pending_jobs[original_msg_id]
            print(f"❎ 监控解除: 检测到后续回复")

if __name__ == '__main__':
    # 在独立线程启动 Web Server (为了骗过 Render 的端口检测)
    threading.Thread(target=run_web_server).start()
    
    print("Bot 正在启动...")
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
