import logging
import os
import sys
import time
import requests  # ✅ 新增：用最轻量的方式发请求
from flask import Flask, request
from telegram import Update
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
TIMEOUT_SECONDS = 1 * 60    # 15分钟
GHOST_TIMEOUT = 2 * 60      # 10分钟

# 触发关键词
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 日志 (仅保留关键信息) =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
# 屏蔽第三方库的废话日志
for lib in ['apscheduler', 'httpx', 'telegram', 'werkzeug', 'sqlalchemy']:
    logging.getLogger(lib).setLevel(logging.WARNING)

# ================= 数据库 (最低配模式) =================
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    database_url,
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_size=1,        # ✅ 极限压缩：只允许1个连接
    max_overflow=2      # ✅ 允许突发加2个
)

jobstores = {'default': SQLAlchemyJobStore(engine=engine)}
executors = {'default': ThreadPoolExecutor(3)} # ✅ 极限压缩：只开3个线程
job_defaults = {'coalesce': True, 'max_instances': 3}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone=timezone.utc
)

# 内存缓存
JOB_CACHE = {}

def sync_cache_from_db():
    if not scheduler.running: return
    try:
        jobs = scheduler.get_jobs()
        for job in jobs:
            if not job.id.startswith('ghost_') and job.args and len(job.args) >= 4:
                JOB_CACHE[job.id] = {'agent_id': job.args[2], 'agent_name': job.args[3]}
        print(f"✅ 缓存同步完成，监控任务数: {len(JOB_CACHE)}")
    except Exception as e:
        print(f"⚠️ 同步缓存失败: {e}")

# ================= ✅ 核心优化：纯 HTTP 发送函数 =================
# 这个函数不依赖 telegram 库，不创建大对象，内存占用极低
def send_raw_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        # 设置短超时，防止卡住线程
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"❌ 发送消息失败: {e}")

# ================= 任务逻辑 (重构为轻量级) =================

# 1. 稍等超时预警
def send_alert_job(chat_id, text, agent_id, agent_name, job_id):
    send_raw_message(chat_id, text)
    # 清理缓存
    if job_id in JOB_CACHE:
        del JOB_CACHE[job_id]

# 2. 👻 无人理睬预警
def send_ghost_alert(chat_id, msg_id, user_name, text_preview, user_id):
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:]
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{msg_id}"
    user_mention = f"[{user_name}](tg://user?id={user_id})"

    alert_text = (
        f"⚠️ **群消息遗漏警告**\n\n"
        f"👤 用户: {user_mention}\n"
        f"⏳ 已等待: {GHOST_TIMEOUT // 60} 分钟\n"
        f"💬 内容: `{text_preview}`\n"
        f"👉 [点击立即回复]({msg_link})"
    )
    send_raw_message(chat_id, alert_text)

# 3. 追问提醒 (必须用 context 发送，因为这是在主程序里运行的)
async def send_chase_alert(context, agent_id, agent_name, original_msg_id, chase_text):
    if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:]
    else: pid = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{pid}/{original_msg_id}"
    clean = chase_text.replace('`', "'")[:30]
    text = f"🔔 **未引用稍等提醒**\n👤 回复人: [{agent_name}](tg://user?id={agent_id})\n💬 内容: `{clean}`\n🔗 [点击回复]({msg_link})"
    try:
        await context.bot.send_message(chat_id=ALERT_GROUP_ID, text=text, parse_mode='Markdown', disable_web_page_preview=True)
    except: pass

# ================= 启动流程 =================
if not scheduler.running:
    scheduler.start()
    sync_cache_from_db()
    # 启动通知也改用轻量级发送
    beijing = datetime.now(timezone.utc) + timedelta(hours=8)
    send_raw_message(ALERT_GROUP_ID, f"♻️ **Bot 重启 (Ultra-Lite)**\n📅 {beijing.strftime('%H:%M:%S')}")

# ================= Flask & Bot =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return f"Lite Bot Running. Active Jobs: {len(JOB_CACHE)}"

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try: await application.initialize()
    except: pass
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
    except Exception as e:
        print(f"Update error: {e}")
    return "ok"

# ================= 主逻辑 (Handle Message) =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or msg.chat_id != CS_GROUP_ID:
        return

    # 场景 1: 回复消息
    if msg.reply_to_message:
        job_id = str(msg.reply_to_message.message_id)
        
        # 消除 Ghost 任务
        ghost_id = f"ghost_user_{msg.reply_to_message.from_user.id}"
        if scheduler.get_job(ghost_id):
            scheduler.remove_job(ghost_id)

        # 检查是否包含关键词
        matched_sig = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_sig:
            # ---> 客服说了“稍等”
            if job_id in JOB_CACHE: return # 只有第一次算数
            
            user = msg.from_user
            raw = msg.reply_to_message.text or "[非文本]"
            safe_txt = raw.replace('`', "'")[:40]
            agent_md = f"[{user.first_name}](tg://user?id={user.id})"
            if str(CS_GROUP_ID).startswith('-100'): pid = str(CS_GROUP_ID)[4:]
            else: pid = str(abs(CS_GROUP_ID))
            link = f"https://t.me/c/{pid}/{job_id}"
            
            alert_text = (
                f"📩 消息: `{safe_txt}`\n🚨 **超时预警**\n"
                f"👤 客服: {agent_md}\n🔑 触发: `{matched_sig}`\n"
                f"⚠️ 状态: 超过 {TIMEOUT_SECONDS // 60} 分钟未回复。\n🔗 [点击回复]({link})"
            )
            
            run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
            JOB_CACHE[job_id] = {'agent_id': user.id, 'agent_name': user.first_name}
            
            # ✅ 这里的 args 只有纯数据，没有 Bot 对象
            scheduler.add_job(send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id])
            
        elif job_id in JOB_CACHE:
            # ---> 普通回复（检查是否追问）
            cache = JOB_CACHE[job_id]
            # 如果是客服自己回复自己，说明在处理
            if msg.from_user.id == msg.reply_to_message.from_user.id:
                 await send_chase_alert(context, cache['agent_id'], cache['agent_name'], job_id, msg.text)
            else:
                # 别人回复了/或者完结了，移除任务
                del JOB_CACHE[job_id]
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)

    # 场景 2: 新消息 (Ghost)
    elif not msg.reply_to_message:
        if msg.from_user.is_bot: return
        
        ghost_id = f"ghost_user_{msg.from_user.id}"
        if not scheduler.get_job(ghost_id):
            run_time = datetime.now(timezone.utc) + timedelta(seconds=GHOST_TIMEOUT)
            txt = msg.text.replace('`', "'")[:30]
            scheduler.add_job(
                send_ghost_alert, 'date', run_date=run_time, id=ghost_id, replace_existing=True,
                args=[ALERT_GROUP_ID, msg.message_id, msg.from_user.first_name, txt, msg.from_user.id],
                misfire_grace_time=300
            )

# ================= Application Build =================
# 限制连接池，节省内存
req = HTTPXRequest(connection_pool_size=1, read_timeout=10.0)
application = Application.builder().token(TOKEN).request(req).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == '__main__':
    print("Use: gunicorn main:app --workers 1")
