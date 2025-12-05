import logging
import os
import sys
import asyncio
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone 
from telegram.request import HTTPXRequest 
# ✅ 新增引用
from sqlalchemy import create_engine

# ================= 配置区域 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795     
ALERT_GROUP_ID = -5093247908  
CS_GROUP_USERNAME = 'adsgsh' 
TIMEOUT_SECONDS = 60    # 测试模式 60秒

# 触发关键词
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout 
)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# ================= 数据库连接设置 (增强版) =================
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# ✅ 关键修复：创建一个健壮的 SQLAlchemy Engine
# pool_recycle=1800: 每30分钟回收一次连接，防止 SSL 超时
# pool_pre_ping=True: 每次执行 SQL 前检查连接是否存活，死了就重连
engine = create_engine(
    database_url,
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

# 使用这个增强的 engine 初始化 JobStore
jobstores = {
    'default': SQLAlchemyJobStore(engine=engine)
}
executors = {
    'default': ThreadPoolExecutor(30)
}
job_defaults = {
    'coalesce': False,
    'max_instances': 20,
    'misfire_grace_time': 3600 
}

scheduler = BackgroundScheduler(
    jobstores=jobstores, 
    executors=executors, 
    job_defaults=job_defaults,
    timezone=timezone.utc 
)

def heartbeat():
    print(f"💓 [系统存活] 调度器正在运行... {datetime.now(timezone.utc)}")

scheduler.add_job(heartbeat, 'interval', seconds=10, id='heartbeat_job', replace_existing=True)
scheduler.start()

# ================= Flask Web Server =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return "Bot is running with Robust DB Connection!"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    try:
        jobs = scheduler.get_jobs()
        job_list = []
        current_time = datetime.now(timezone.utc)
        for job in jobs:
            time_diff = "未知"
            if job.next_run_time:
                diff = job.next_run_time - current_time
                time_diff = f"{diff.total_seconds()} 秒后"
            job_list.append(f"<li><strong>ID:</strong> {job.id} <br> <strong>下次运行:</strong> {job.next_run_time} <br> <strong>倒计时:</strong> {time_diff}</li>")
        return f"<h1>任务监控面板</h1><p>当前时间: {current_time}</p><p>任务数: {len(jobs)}</p><hr><ul>{''.join(job_list)}</ul>"
    except Exception as e:
        return f"<h1>数据库错误</h1><p>{str(e)}</p>"

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try:
        await application.initialize()
    except Exception:
        pass 
    json_data = request.get_json(force=True)
    update = Update.de_json(json_data, application.bot)
    await application.process_update(update)
    return "ok"

# ================= 预警任务函数 =================
def send_alert_job(chat_id, text):
    print(f"⚡️ 正在执行预警任务... (Chat ID: {chat_id})") 
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(temp_bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', disable_web_page_preview=True))
        print("✅ 预警消息已成功发送")
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
    job_id = str(original_msg_id) 

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

    # --- 逻辑 A: 开启监控 ---
    if matched_signature:
        original_user = msg.reply_to_message.from_user.first_name if msg.reply_to_message.from_user else "用户"
        
        if str(CS_GROUP_ID).startswith('-100'):
            positive_chat_id = str(CS_GROUP_ID)[4:] 
        else:
            positive_chat_id = str(abs(CS_GROUP_ID))
        msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

        current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"
        if TIMEOUT_SECONDS == 60: current_timeout_display = "60 秒"

        alert_text = (
            f"🚨 **客服超时预警 ({current_timeout_display})**\n\n"
            f"👤 客户: {original_user}\n"
            f"🔑 触发签名: `{matched_signature}`\n"
            f"⚠️ 状态: 客服回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
            f"🔗 [点击跳转处理]({msg_link})"
        )

        print(f"📥 [新任务] ID: {job_id}")

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text], misfire_grace_time=3600 
            )
            print(f"💾 [成功] 任务已存入! 计划执行(UTC): {run_time}")
        except Exception as e:
            print(f"❌ [失败] 数据库写入错误: {e}")
        
        await asyncio.sleep(0.1)
        return

    # --- 逻辑 B: 取消监控 ---
    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            print(f"🗑️ [已取消] ID: {job_id}")
    except Exception:
        pass 

    await asyncio.sleep(0.1)

# ================= 启动逻辑 =================
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("Run with 'gunicorn main:app'")
