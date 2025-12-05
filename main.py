import logging
import os
import threading
from flask import Flask
from telegram import Update
from telegram.request import HTTPXRequest
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ================= 你的配置 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1004990486181
ALERT_GROUP_ID = -1005093247908

# !!! 测试模式：60 秒 (测试成功后改为 15 * 60) !!!
TIMEOUT_SECONDS = 60 

# 触发关键词列表
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= Web Server (Render 保活必须) =================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running (Test Mode: 60s)"

def run_web_server():
    # Render 会自动提供 PORT，默认 8080
    port = int(os.environ.get('PORT', 8080))
    #以此禁止 Flask 打印烦人的日志
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)
    app.run(host='0.0.0.0', port=port)

# ================= 机器人逻辑 =================
# 设置日志格式
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
# 屏蔽 httpx 的底层日志
logging.getLogger("httpx").setLevel(logging.WARNING)

pending_jobs = {}

async def alert_callback(context: ContextTypes.DEFAULT_TYPE):
    """倒计时结束，执行报警"""
    job_data = context.job.data
    original_msg_id = job_data['original_msg_id']
    trigger_msg_link = job_data['trigger_msg_link']
    original_user = job_data['original_user']
    trigger_keyword = job_data['trigger_keyword']

    print(f"⏰ 倒计时结束！准备发送预警...")

    if original_msg_id in pending_jobs:
        del pending_jobs[original_msg_id]

    alert_text = (
        f"🚨 **超时测试 (1分钟)**\n\n"
        f"👤 客户: {original_user}\n"
        f"🔑 触发签名: `{trigger_keyword}`\n"
        f"⚠️ 状态: 客服回复稍等后，超过 1 分钟未进一步回复。\n\n"
        f"🔗 [点击跳转处理]({trigger_msg_link})"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ALERT_GROUP_ID,
            text=alert_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        print("✅ 预警消息发送成功！")
    except Exception as e:
        print(f"❌ 发送失败！错误详情: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text: return

    # --- 调试日志：打印所有收到消息的群ID ---
    # 如果机器人没反应，请去 Render Logs 找这一行，看 ID 是否匹配
    if msg.chat_id == CS_GROUP_ID or msg.chat_id == ALERT_GROUP_ID:
        pass # 目标群不刷屏
    else:
        print(f"收到非目标群消息 | 群名: {msg.chat.title} | ID: {msg.chat_id}")

    # 逻辑入口：必须在客服群，且必须是回复消息
    if msg.chat_id == CS_GROUP_ID and msg.reply_to_message:
        original_msg_id = msg.reply_to_message.message_id
        
        # --- 检查是否包含签名 (开启监控) ---
        matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_signature:
            original_user = "用户"
            if msg.reply_to_message.from_user:
                original_user = msg.reply_to_message.from_user.first_name
            
            clean_chat_id = str(CS_GROUP_ID).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"

            print(f"✅ 监控开启 (60秒): {original_user} | 签名: {matched_signature}")

            # 如果已有旧任务，先移除
            if original_msg_id in pending_jobs:
                pending_jobs[original_msg_id].schedule_removal()

            # 开启倒计时
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

        # --- 检查后续回复 (取消监控) ---
        # 只要是对原消息的回复，无论说什么，都取消监控
        if original_msg_id in pending_jobs:
            job = pending_jobs[original_msg_id]
            job.schedule_removal()
            del pending_jobs[original_msg_id]
            print(f"❎ 监控解除: 检测到后续回复")

if __name__ == '__main__':
    # 启动 Web Server 线程 (骗过 Render)
    threading.Thread(target=run_web_server).start()
    
    print("Bot 正在启动 (测试模式: 1分钟)...")
    
    # 优化网络连接参数，防止 ReadError
    request_config = HTTPXRequest(
        connection_pool_size=8,
        read_timeout=20.0,
        write_timeout=20.0,
        connect_timeout=20.0,
        http_version="1.1"
    )

    application = Application.builder().token(TOKEN).request(request_config).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # 启动轮询
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True, timeout=15)
