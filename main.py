import logging
import os
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from datetime import datetime, timedelta

# ================= 配置区域 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795     
ALERT_GROUP_ID = -5093247908  
CS_GROUP_USERNAME = 'adsgsh' 
TIMEOUT_SECONDS = 15 * 60    # 正式模式 15 分钟

# 触发关键词
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 数据库连接设置 =================
# 获取 Render 环境变量中的数据库地址
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')

# 兼容性处理：有些库返回 postgres://，但 SQLAlchemy 需要 postgresql://
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# 配置 APScheduler 使用 Neon 数据库
jobstores = {
    'default': SQLAlchemyJobStore(url=database_url)
}
executors = {
    'default': ThreadPoolExecutor(20)
}
job_defaults = {
    'coalesce': False,
    'max_instances': 3
}

# 初始化调度器
scheduler = BackgroundScheduler(jobstores=jobstores, executors=executors, job_defaults=job_defaults)
scheduler.start()

# ================= Flask Web Server (Webhook) =================
app = Flask(__name__)

# 1. 首页 (健康检查)
@app.route('/', methods=['GET'])
def index():
    return "Bot is running with Neon Database!"

# 2. Webhook 路由
@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    update = Update.de_json(await request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# ================= 预警任务函数 (独立静态函数) =================
def send_alert_job(chat_id, text):
    """
    这个函数由 APScheduler 从数据库读取并触发。
    """
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(temp_bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        ))
        print("✅ 预警已触发 (来源: Neon数据库)")
    except Exception as e:
        print(f"❌ 预警发送失败: {e}")
    finally:
        loop.close()

# ================= Bot 逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.reply_to_message or msg.chat_id != CS_GROUP_ID:
        return

    original_msg_id = msg.reply_to_message.message_id
    # 使用 original_msg_id 作为数据库任务 ID
    job_id = str(original_msg_id) 

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

    # --- 逻辑 A: 开启监控 (写入 Neon) ---
    if matched_signature:
        original_user = msg.reply_to_message.from_user.first_name if msg.reply_to_message.from_user else "用户"
        
        # 链接逻辑
        if str(CS_GROUP_ID).startswith('-100'):
            positive_chat_id = str(CS_GROUP_ID)[4:] 
        else:
            positive_chat_id = str(abs(CS_GROUP_ID))
        msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

        current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"
        alert_text = (
            f"🚨 **客服超时预警 ({current_timeout_display})**\n\n"
            f"👤 客户: {original_user}\n"
            f"🔑 触发签名: `{matched_signature}`\n"
            f"⚠️ 状态: 客服回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
            f"🔗 [点击跳转处理]({msg_link})"
        )

        print(f"📥 写入数据库: ID {job_id}")

        # 计算触发时间
        run_time = datetime.now() + timedelta(seconds=TIMEOUT_SECONDS)
        
        # 添加任务到数据库
        scheduler.add_job(
            send_alert_job,
            'date',
            run_date=run_time,
            id=job_id,
            replace_existing=True,
            args=[ALERT_GROUP_ID, alert_text]
        )
        
        await asyncio.sleep(0.1)
        return

    # --- 逻辑 B: 取消监控 (从 Neon 删除) ---
    # 只要是回复了该消息，尝试移除任务
    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            print(f"🗑️ 任务已移除: ID {job_id}")
    except Exception:
        pass 

    await asyncio.sleep(0.1)

# ================= 启动逻辑 =================
# 1. 初始化 Application
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
# 2. 注册 Handler
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

if __name__ == '__main__':
    # 本地测试用，Render 上由 Gunicorn 启动
    port = int(os.environ.get('PORT', 8080))
    # app.run(host='0.0.0.0', port=port)
    print("Run with 'gunicorn main:app'")
