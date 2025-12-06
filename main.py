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
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError, DisconnectionError

# ================= 配置区域 =================
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'
CS_GROUP_ID = -1003400471795      
ALERT_GROUP_ID = -5093247908   
CS_GROUP_USERNAME = 'adsgsh' 
TIMEOUT_SECONDS = 60    # SLA 超时 (15分钟)
GHOST_TIMEOUT = 60      # 无人回复超时 (10分钟)

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
# 降低日志噪音
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# ================= 数据库连接 (自动修复版) =================
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# ✅ 关键优化：激进的连接回收策略
engine = create_engine(
    database_url,
    pool_recycle=600,   # 每10分钟强制回收连接，防止超时
    pool_pre_ping=True, # 每次使用前检查连接是否活着
    pool_size=5,        # 保持5个连接
    max_overflow=5,     # 最多再借5个
    pool_timeout=30     # 如果30秒拿不到连接就报错，不卡死
)

# ✅ 强力补丁：每次从连接池拿连接时，手动 Ping 一下
@event.listens_for(engine, "checkout")
def ping_connection(dbapi_connection, connection_record, connection_proxy):
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SELECT 1")
        cursor.close()
    except:
        # 如果 Ping 失败，抛出异常，让 SQLAlchemy 自动丢弃这个坏连接并重连
        raise DisconnectionError()

jobstores = {'default': SQLAlchemyJobStore(engine=engine)}
executors = {'default': ThreadPoolExecutor(10)} # 10个线程足够了
# ✅ 允许任务迟到 2 小时 (防止长时间卡顿后任务被丢弃)
job_defaults = {'coalesce': False, 'max_instances': 5, 'misfire_grace_time': 7200} 

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
            if job.id.startswith('ghost_'): continue
            if job.args and len(job.args) >= 4:
                JOB_CACHE[job.id] = {'agent_id': job.args[2], 'agent_name': job.args[3]}
        print(f"✅ 同步完成！当前活跃 SLA 任务数: {len(JOB_CACHE)}")
    except Exception as e:
        print(f"⚠️ 同步失败 (数据库错误): {e}")

def send_startup_notification():
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        beijing_time = datetime.now(timezone.utc) + timedelta(hours=8)
        time_str = beijing_time.strftime("%Y-%m-%d %H:%M:%S")
        alert_text = f"♻️ **机器人已重启 (防断连版)**\n📅 时间: `{time_str}`\n✅ 状态: 数据库连接池已重置。"
        loop.run_until_complete(temp_bot.send_message(chat_id=ALERT_GROUP_ID, text=alert_text, parse_mode='Markdown'))
    except: pass
    finally: loop.close()

# ✅ 看门狗任务：每分钟报一次平安，证明调度器没死锁
def watchdog_job():
    print(f"💓 [看门狗] 调度器正常: {datetime.now(timezone.utc)}")

scheduler.add_job(watchdog_job, 'interval', minutes=1, id='watchdog', replace_existing=True)
scheduler.start()
sync_cache_from_db()
send_startup_notification()

# ================= Flask Web 服务器 =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index(): return f"Bot Running. Active Jobs: {len(JOB_CACHE)}"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    try:
        jobs = scheduler.get_jobs()
        job_list = []
        current_time = datetime.now(timezone.utc)
        
        for job in jobs:
            time_diff = "未知"
            status = "🟢 等待中"
            if job.next_run_time:
                diff = job.next_run_time - current_time
                seconds = diff.total_seconds()
                time_diff = f"{seconds:.1f}秒"
                # 如果延迟超过 10 秒，标记为红色
                if seconds < -10: status = "🔴 **卡顿/积压**"
            
            job_list.append(f"<li>{status} | ID: {job.id} | 倒计时: {time_diff}</li>")
            
        return f"<h1>任务监控 (UTC时间: {current_time.strftime('%H:%M:%S')})</h1><hr><ul>{''.join(job_list)}</ul>"
    except Exception as e:
        return f"<h1>数据库错误</h1><p>{str(e)}</p>"

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try: await application.initialize()
    except: pass 
    
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
    except Exception as e:
        print(f"Webhook Error: {e}")
        
    return "ok"

# ================= 任务执行函数 =================

# 1. SLA 预警
def send_alert_job(chat_id, text, agent_id, agent_name, job_id_for_cleanup=None):
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(temp_bot.send_message(chat_id=chat_id, text=text, parse_mode='Markdown', disable_web_page_preview=True))
    except Exception as e: print(f"❌ SLA 预警失败: {e}")
    finally: loop.close()
    
    if job_id_for_cleanup and job_id_for_cleanup in JOB_CACHE:
        del JOB_CACHE[job_id_for_cleanup]

# 2. Ghost 预警
def send_ghost_alert(chat_id, msg_id, user_name, text_preview, user_id):
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{msg_id}"
    user_mention = f"[{user_name}](tg://user?id={user_id})"
    
    alert_text = (
        f"⚠️ **群消息遗漏警告 (Ghost)**\n\n"
        f"👤 用户: {user_mention}\n"
        f"⏳ 已等待: {GHOST_TIMEOUT // 60} 分钟\n"
        f"💬 内容: `{text_preview}`\n"
        f"👉 [点击立即回复]({msg_link})"
    )
    
    try:
        loop.run_until_complete(temp_bot.send_message(chat_id=chat_id, text=alert_text, parse_mode='Markdown', disable_web_page_preview=True))
    except Exception as e: print(f"❌ Ghost 预警失败: {e}")
    finally: loop.close()

# 3. 追问预警
async def send_chase_alert(context, agent_id, agent_name, original_msg_id, chase_text):
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{original_msg_id}"
    clean_text = chase_text.replace('`', "'")[:30] + "..." if len(chase_text)>30 else chase_text
    text = f"🔔 **检测到未引用追问**\n👤 客服: [{agent_name}](tg://user?id={agent_id})\n💬 内容: `{clean_text}`\n🔗 [点击跳转]({msg_link})"
    try: await context.bot.send_message(chat_id=ALERT_GROUP_ID, text=text, parse_mode='Markdown', disable_web_page_preview=True)
    except: pass

def get_job_with_retry(job_id, max_retries=3):
    for i in range(max_retries):
        try: return scheduler.get_job(job_id)
        except Exception: time.sleep(0.5)
    return None

# ================= Bot 核心逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or msg.chat_id != CS_GROUP_ID:
        return

    # 打印日志证明机器人收到了消息
    print(f"📩 收到消息: {msg.text[:10]}... (用户: {msg.from_user.first_name})")

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)
    
    if msg.reply_to_message:
        original_msg_id = msg.reply_to_message.message_id
        job_id = str(original_msg_id)
        
        # 移除 Ghost 任务 (有人回了就不算遗漏)
        ghost_user_job_id = f"ghost_user_{msg.reply_to_message.from_user.id}"
        try:
            if scheduler.get_job(ghost_user_job_id):
                scheduler.remove_job(ghost_user_job_id)
                print(f"✨ 移除 Ghost 任务: {ghost_user_job_id}")
        except: pass

        # -> 分支 A: 客服回复“稍等”
        if matched_signature:
            if job_id in JOB_CACHE: return 
            
            user = msg.from_user
            raw_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本]"
            safe_text = raw_text.replace('`', "'")[:50] + "..." if len(raw_text) > 50 else raw_text.replace('`', "'")
            agent_mention = f"@{user.username.replace('_', '\\_')}" if user.username else f"[{user.first_name}](tg://user?id={user.id})"
            
            if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:] 
            else: pid = str(abs(CS_GROUP_ID))
            msg_link = f"https://t.me/c/{pid}/{original_msg_id}"
            timeout_disp = "60秒" if TIMEOUT_SECONDS == 60 else f"{TIMEOUT_SECONDS // 60}分钟"
            
            alert_text = (
                f"📩 原始消息: `{safe_text}`\n\n🚨 **稍等超时预警 ({timeout_disp})**\n"
                f"👤 回复人: {agent_mention}\n🔑 稍等: `{matched_signature}`\n"
                f"⚠️ 状态: 回复稍等后，超过 {timeout_disp} 未进一步回复。\n\n🔗 [点击进行回复]({msg_link})"
            )
            print(f"📥 [SLA任务] ID: {job_id}")
            
            run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
            JOB_CACHE[job_id] = {'agent_id': user.id, 'agent_name': user.first_name}
            
            try:
                scheduler.add_job(
                    send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                    args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id], 
                    misfire_grace_time=3600
                )
            except: pass
            return

        # -> 分支 B: 普通回复
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
    # 场景 2: 新消息 (Ghost)
    # ----------------------------------------------------
    if not msg.reply_to_message and not matched_signature:
        if msg.from_user.is_bot: return

        user_id = msg.from_user.id
        ghost_user_job_id = f"ghost_user_{user_id}"
        
        if scheduler.get_job(ghost_user_job_id):
            print(f"⏳ [Ghost] 任务已存在: {ghost_user_job_id}")
        else:
            msg_id = msg.message_id
            user_name = msg.from_user.first_name
            text_preview = msg.text.replace('`', "'")[:30] + "..." if len(msg.text) > 30 else msg.text
            print(f"👻 [Ghost计时] 新用户: {user_name}")
            
            run_time = datetime.now(timezone.utc) + timedelta(seconds=GHOST_TIMEOUT)
            try:
                scheduler.add_job(
                    send_ghost_alert, 'date', run_date=run_time, id=ghost_user_job_id, replace_existing=True,
                    args=[ALERT_GROUP_ID, msg_id, user_name, text_preview, user_id],
                    misfire_grace_time=300
                )
            except: pass

# ================= 启动逻辑 =================
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("Run with 'gunicorn main:app'")
