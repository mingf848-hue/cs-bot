import logging
import os
import sys
import asyncio
import time 
from flask import Flask, request
from telegram import Update, Bot
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone 
from telegram.request import HTTPXRequest 
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

# ================= 配置区域 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795      
ALERT_GROUP_ID = -5093247908  
CS_GROUP_USERNAME = 'adsgsh' 
TIMEOUT_SECONDS = 15 * 60    # 稍等超时 (15分钟)
GHOST_TIMEOUT = 10 * 60      # 无人理睬超时 (10分钟)

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
logging.getLogger('apscheduler').setLevel(logging.WARNING)

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

# 内存缓存
JOB_CACHE = {}

def sync_cache_from_db():
    print("🔄 正在从数据库同步任务...")
    try:
        jobs = scheduler.get_jobs()
        for job in jobs:
            if job.id.startswith('ghost_'):
                continue
            if job.args and len(job.args) >= 4:
                JOB_CACHE[job.id] = {
                    'agent_id': job.args[2],
                    'agent_name': job.args[3]
                }
        print(f"✅ 同步完成！活跃SLA任务: {len(JOB_CACHE)}")
    except Exception as e:
        print(f"⚠️ 缓存同步跳过: {e}")

# 启动通知 (北京时间)
def send_startup_notification():
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        beijing_time = datetime.now(timezone.utc) + timedelta(hours=8)
        time_str = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        alert_text = f"♻️ **机器人已重启**\n📅 时间: `{time_str}`\n✅ 状态: 服务已恢复。"
        loop.run_until_complete(temp_bot.send_message(chat_id=ALERT_GROUP_ID, text=alert_text, parse_mode='Markdown'))
    except: pass
    finally: loop.close()

# ================= 启动调度器 =================
# 注意：此处已移除了 heartbeat 任务
scheduler.start()
sync_cache_from_db()
send_startup_notification()

# ================= Flask =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index(): return f"Bot is running. Jobs: {len(JOB_CACHE)}"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    try:
        jobs = scheduler.get_jobs()
        job_list = []
        current_time = datetime.now(timezone.utc) + timedelta(hours=8)
        
        for job in jobs:
            time_diff = "未知"
            if job.next_run_time:
                diff = job.next_run_time - datetime.now(timezone.utc)
                time_diff = f"{diff.total_seconds():.1f} 秒后"
            
            job_list.append(f"<li>ID: {job.id} | 倒计时: {time_diff}</li>")
            
        return f"<h1>任务监控 (北京时间: {current_time.strftime('%H:%M:%S')})</h1><hr><ul>{''.join(job_list)}</ul>"
    except Exception as e:
        return str(e)

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try: await application.initialize()
    except: pass 
    update = Update.de_json(request.get_json(force=True), application.bot)
    await application.process_update(update)
    return "ok"

# ================= 任务函数 =================

# 1. 稍等超时预警
def send_alert_job(chat_id, text, agent_id, agent_name, job_id_for_cleanup=None):
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(temp_bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', disable_web_page_preview=True))
    except Exception as e: print(f"❌ SLA预警失败: {e}")
    finally: loop.close()
    if job_id_for_cleanup and job_id_for_cleanup in JOB_CACHE:
        del JOB_CACHE[job_id_for_cleanup]

# 2. 👻 无人理睬预警
def send_ghost_alert(chat_id, msg_id, user_name, text_preview, user_id):
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{msg_id}"
    
    user_mention = f"[{user_name}](tg://user?id={user_id})"
    
    alert_text = (
        f"⚠️ **群消息遗漏遗警告**\n\n"
        f"👤 用户: {user_mention}\n"
        f"⏳ 已等待: {GHOST_TIMEOUT // 60} 分钟\n"
        f"💬 内容: `{text_preview}`\n"
        f"👉 [点击立即回复]({msg_link})"
    )
    
    try:
        loop.run_until_complete(temp_bot.send_message(chat_id=chat_id, text=alert_text, parse_mode='Markdown', disable_web_page_preview=True))
    except Exception as e: print(f"❌ Ghost预警失败: {e}")
    finally: loop.close()

# 3. 追问提醒
async def send_chase_alert(context, agent_id, agent_name, original_msg_id, chase_text):
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{original_msg_id}"
    clean_text = chase_text.replace('`', "'")[:30] + "..." if len(chase_text)>30 else chase_text
    text = f"🔔 **未引用稍等提醒**\n👤 回复人: [{agent_name}](tg://user?id={agent_id})\n💬 内容: `{clean_text}`\n🔗 [点击回复]({msg_link})"
    try: await context.bot.send_message(chat_id=ALERT_GROUP_ID, text=text, parse_mode='Markdown', disable_web_page_preview=True)
    except: pass

def get_job_with_retry(job_id, max_retries=3):
    for i in range(max_retries):
        try: return scheduler.get_job(job_id)
        except Exception: time.sleep(0.5)
    return None

# ================= Bot 逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or msg.chat_id != CS_GROUP_ID:
        return

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)
    
    # ----------------------------------------------------
    # 场景 1: 回复消息 (客服稍等 / 普通回复)
    # ----------------------------------------------------
    if msg.reply_to_message:
        original_msg_id = msg.reply_to_message.message_id
        job_id = str(original_msg_id)
        
        # 消除鬼影 (只要有人回，就不算无人接待)
        ghost_user_job_id = f"ghost_user_{msg.reply_to_message.from_user.id}"
        try:
            if scheduler.get_job(ghost_user_job_id):
                scheduler.remove_job(ghost_user_job_id)
        except: pass

        # -> 分支 A: 客服回复“稍等” (开启 SLA)
        if matched_signature:
            if job_id in JOB_CACHE: return 
            
            user = msg.from_user
            raw_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本]"
            safe_text = raw_text.replace('`', "'")[:50] + "..." if len(raw_text) > 50 else raw_text.replace('`', "'")
            agent_mention = f"@{user.username.replace('_', '\\_')}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
            if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
            else: pid = str(abs(CS_GROUP_ID))
            msg_link = f"https://t.me/c/{pid}/{original_msg_id}"
            timeout_disp = "60 秒" if TIMEOUT_SECONDS == 60 else f"{TIMEOUT_SECONDS // 60} 分钟"
            
            alert_text = (
                f"📩 原始消息: `{safe_text}`\n\n🚨 **稍等超时预警 ({timeout_disp})**\n"
                f"👤 回复人: {agent_mention}\n🔑 稍等: `{matched_signature}`\n"
                f"⚠️ 状态: 回复稍等后，超过 {timeout_disp} 未进一步回复。\n\n🔗 [点击进行回复]({msg_link})"
            )
            print(f"📥 [SLA任务] ID: {job_id}")
            run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
            JOB_CACHE[job_id] = {'agent_id': user.id, 'agent_name': user.first_name}
            try:
                scheduler.add_job(send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                    args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id], misfire_grace_time=3600)
            except: pass
            return

        # -> 分支 B: 普通回复 (检测是否追问)
        if job_id in JOB_CACHE:
            cache = JOB_CACHE[job_id]
            if msg.from_user.id == msg.reply_to_message.from_user.id:
                print(f"🔔 [追问] ID: {job_id}")
                await send_chase_alert(context, cache['agent_id'], cache['agent_name'], original_msg_id, msg.text)
            else:
                print(f"🗑️ [完成] ID: {job_id}")
                del JOB_CACHE[job_id]
                try: 
                    get_job_with_retry(job_id)
                    scheduler.remove_job(job_id)
                except: pass
        return

    # ----------------------------------------------------
    # 场景 2: 新消息 (开启鬼影 10分钟 倒计时)
    # ----------------------------------------------------
    if not msg.reply_to_message and not matched_signature:
        if msg.from_user.is_bot: return

        user_id = msg.from_user.id
        ghost_user_job_id = f"ghost_user_{user_id}"
        
        if not scheduler.get_job(ghost_user_job_id):
            msg_id = msg.message_id
            user_name = msg.from_user.first_name
            text_preview = msg.text.replace('`', "'")[:30] + "..." if len(msg.text) > 30 else msg.text
            print(f"👻 [新客] 用户: {user_name}")
            
            run_time = datetime.now(timezone.utc) + timedelta(seconds=GHOST_TIMEOUT)
            try:
                scheduler.add_job(
                    send_ghost_alert, 'date', run_date=run_time, id=ghost_user_job_id, replace_existing=True,
                    args=[ALERT_GROUP_ID, msg_id, user_name, text_preview, user_id],
                    misfire_grace_time=300
                )
            except: pass

# ================= 启动 =================
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("Run with 'gunicorn main:app'")
