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
from sqlalchemy import create_engine

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

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout 
)
logging.getLogger('apscheduler').setLevel(logging.DEBUG)

# ================= 数据库连接设置 =================
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    database_url,
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20
)

jobstores = {'default': SQLAlchemyJobStore(engine=engine)}
executors = {'default': ThreadPoolExecutor(30)}
job_defaults = {'coalesce': False, 'max_instances': 20, 'misfire_grace_time': 3600}

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
    return "Bot is running (Auto-Mention Agent)"

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
                time_diff = f"{diff.total_seconds():.1f} 秒后"
            
            # 尝试提取任务参数中的回复人信息
            args_info = ""
            if job.args and len(job.args) > 1:
                try:
                    content = job.args[1]
                    if "👤 回复人:" in content:
                        # 简单的文本提取，用于调试显示
                        agent_part = content.split("👤 回复人:")[1].split("\n")[0].strip()
                        args_info = f" (回复人: {agent_part})"
                except:
                    pass
                    
            job_list.append(f"<li><strong>ID:</strong> {job.id}{args_info} <br> <strong>下次运行:</strong> {job.next_run_time} <br> <strong>倒计时:</strong> {time_diff}</li>")
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
        loop.run_until_complete(temp_bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode='Markdown', # 必须开启 Markdown 才能支持链接形式的 @
            disable_web_page_preview=True
        ))
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
        # 获取当前发消息的回复人对象
        user = msg.from_user
        
        # ✅ 关键修改：生成“艾特”格式
        if user.username:
            # 如果有用户名，使用 @username (最显眼)
            # 注意：Markdown 中下划线需要转义，但用户名通常不需要，直接用即可
            agent_mention = f"@{user.username}"
        else:
            # 如果没有用户名，使用 [名字](tg://user?id=123) 进行强行艾特
            agent_mention = f"[{user.first_name}](tg://user?id={user.id})"
        
        # 生成跳转链接
        if str(CS_GROUP_ID).startswith('-100'):
            positive_chat_id = str(CS_GROUP_ID)[4:] 
        else:
            positive_chat_id = str(abs(CS_GROUP_ID))
        msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

        current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"
        if TIMEOUT_SECONDS == 60: current_timeout_display = "60 秒"

        # ✅ 修改文案，嵌入 agent_mention
        alert_text = (
            f"🚨 **回复人超时预警 ({current_timeout_display})**\n\n"
            f"👤 回复人: {agent_mention}\n"
            f"🔑 稍等: `{matched_signature}`\n"
            f"⚠️ 状态: 回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
            f"🔗 [点击跳转处理]({msg_link})"
        )

        print(f"📥 [新任务] ID: {job_id} | 回复人: {user.first_name}")

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text], misfire_grace_time=3600 
            )
            print(f"💾 [已存入] 计划执行(UTC): {run_time}")
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
