import logging
import os
import sys
import time
import requests  # ✅ 新增：用于轻量级发信
from flask import Flask, request
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.pool import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from telegram.request import HTTPXRequest
from sqlalchemy import create_engine

# ================= ⚙️ 配置区域 =================

TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'

# ✅ 修改：支持多群监控。请把你要监控的群ID都放在这里
MONITORED_GROUPS = [
    -1003400471795, 
]

ALERT_GROUP_ID = -5093247908  
CS_GROUP_USERNAME = 'adsgsh' 

TIMEOUT_SECONDS = 2 * 60     # 稍等超时 (15分钟)
GHOST_TIMEOUT = 1 * 60       # 无人理睬超时 (10分钟)

# 触发关键词
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 📉 系统底层设置 (Lite优化) =================

logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout
)
# 屏蔽掉 APScheduler 的 DEBUG 日志
logging.getLogger('apscheduler').setLevel(logging.WARNING)

# 数据库连接 (内存优化版)
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    database_url,
    pool_recycle=1800,
    pool_pre_ping=True,
    # ✅ 关键：限制数据库连接数，防止内存溢出
    pool_size=2,          
    max_overflow=5
)

jobstores = {'default': SQLAlchemyJobStore(engine=engine)}

# ✅ 关键：开启 10 个线程，防止任务排队导致的倒计时负数
executors = {'default': ThreadPoolExecutor(10)} 

# ✅ 关键：允许任务迟到 120 秒 (misfire_grace_time)，防止服务器卡顿导致任务被丢弃
job_defaults = {
    'coalesce': True, 
    'max_instances': 10,
    'misfire_grace_time': 120 
}

scheduler = BackgroundScheduler(
    jobstores=jobstores,
    executors=executors,
    job_defaults=job_defaults,
    timezone=timezone.utc
)

# 内存缓存
JOB_CACHE = {}

def sync_cache_from_db():
    """启动时同步任务"""
    if not scheduler.running: return
    try:
        jobs = scheduler.get_jobs()
        for job in jobs:
            # 只缓存 SLA 任务，Ghost 任务不需要缓存
            if not job.id.startswith('ghost_') and job.args and len(job.args) >= 4:
                JOB_CACHE[job.id] = {'agent_id': job.args[2], 'agent_name': job.args[3]}
        print(f"✅ 缓存同步完成，当前活跃SLA任务: {len(JOB_CACHE)}")
    except Exception as e:
        print(f"⚠️ 缓存同步跳过: {e}")

# ================= 🛠 工具函数 (Requests版) =================

def get_msg_link(chat_id, msg_id):
    """动态生成消息链接"""
    pid = str(chat_id)
    if pid.startswith('-100'):
        pid = pid[4:]
    else:
        pid = str(abs(int(chat_id)))
    return f"https://t.me/c/{pid}/{msg_id}"

def send_raw_message(chat_id, text):
    """
    ✅ 核心优化：使用 requests 发送消息
    不依赖 Bot 对象，极度省内存
    """
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    try:
        # 设置 10 秒超时，防止网络卡顿阻塞线程
        resp = requests.post(url, json=payload, timeout=10) 
        if resp.status_code != 200:
            print(f"❌ Telegram API 报错: {resp.text}")
    except Exception as e:
        print(f"❌ 网络请求异常: {e}")

# ================= ⏱ 任务逻辑 (全部改为同步函数) =================

# 1. 启动通知
def send_startup_notification():
    beijing = datetime.now(timezone.utc) + timedelta(hours=8)
    text = (
        f"♻️ **机器人已重启 (Lite Requests版)**\n"
        f"📅 时间: `{beijing.strftime('%H:%M:%S')}`\n"
        f"✅ 状态: 内存优化已启用，监控中。"
    )
    send_raw_message(ALERT_GROUP_ID, text)

# 2. 稍等超时报警
def send_alert_job(chat_id, text, agent_id, agent_name, job_id):
    print(f"⚡ [执行中] 触发 SLA 报警任务: {job_id}")
    send_raw_message(chat_id, text)
    # 清理缓存
    if job_id in JOB_CACHE:
        del JOB_CACHE[job_id]

# 3. 鬼影(无人理睬)报警
def send_ghost_alert(alert_target_id, msg_id, user_name, text_preview, user_id, source_chat_id):
    print(f"⚡ [执行中] 触发 Ghost 报警: {user_name}")
    
    msg_link = get_msg_link(source_chat_id, msg_id)
    user_mention = f"[{user_name}](tg://user?id={user_id})"

    alert_text = (
        f"⚠️ **群消息遗漏警告**\n\n"
        f"📢 来源群: `{source_chat_id}`\n"
        f"👤 用户: {user_mention}\n"
        f"⏳ 已等待: {GHOST_TIMEOUT // 60} 分钟\n"
        f"💬 内容: `{text_preview}`\n"
        f"👉 [点击立即回复]({msg_link})"
    )
    send_raw_message(alert_target_id, alert_text)

# 4. 追问提醒 (直接发送)
def send_chase_alert_sync(agent_id, agent_name, original_msg_id, chase_text, source_chat_id):
    msg_link = get_msg_link(source_chat_id, original_msg_id)
    clean = chase_text.replace('`', "'")[:30]
    text = f"🔔 **未引用稍等提醒**\n👤 回复人: [{agent_name}](tg://user?id={agent_id})\n💬 内容: `{clean}`\n🔗 [点击回复]({msg_link})"
    send_raw_message(ALERT_GROUP_ID, text)

# ================= 🚀 启动入口 =================

if not scheduler.running:
    scheduler.start()
    sync_cache_from_db()
    # 延迟 2 秒发送启动通知
    time.sleep(2)
    scheduler.add_job(send_startup_notification, 'date', run_date=datetime.now(timezone.utc) + timedelta(seconds=1))

app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return f"Lite Bot Running. Active Jobs: {len(JOB_CACHE)}"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    try:
        jobs = scheduler.get_jobs()
        job_list = []
        current_time = datetime.now(timezone.utc)
        for job in jobs:
            time_diff = "未知"
            status = "等待中"
            if job.next_run_time:
                diff = job.next_run_time - current_time
                seconds = diff.total_seconds()
                time_diff = f"{seconds:.1f} 秒"
                if seconds < 0: status = f"<span style='color:red'>延迟 {abs(seconds):.1f}s</span>"
            job_list.append(f"<li>ID: {job.id} | {status} | {time_diff}</li>")
        return f"<h1>任务监控</h1><ul>{''.join(job_list)}</ul>"
    except Exception as e: return str(e)

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try: await application.initialize()
    except: pass
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
    except Exception: pass
    return "ok"

# ================= 🤖 消息处理主逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    # 1. 基础过滤：必须是监控列表里的群
    if not msg or not msg.text: return
    if msg.chat_id not in MONITORED_GROUPS: return

    # ----------------------------------------------------
    # 场景 1: 回复消息 (客服稍等 / 追问)
    # ----------------------------------------------------
    if msg.reply_to_message:
        job_id = str(msg.reply_to_message.message_id)
        
        # 消除 Ghost 任务 (只要有人回，就不算冷场)
        ghost_id = f"ghost_user_{msg.reply_to_message.from_user.id}"
        if scheduler.get_job(ghost_id):
            scheduler.remove_job(ghost_id)

        matched_sig = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_sig:
            # ---> 客服说了“稍等” (开启 SLA 倒计时)
            if job_id in JOB_CACHE: return 
            
            user = msg.from_user
            raw = msg.reply_to_message.text or "[非文本]"
            safe_txt = raw.replace('`', "'")[:40]
            agent_md = f"[{user.first_name}](tg://user?id={user.id})"
            link = get_msg_link(msg.chat_id, job_id)
            
            alert_text = (
                f"📩 消息: `{safe_txt}`\n🚨 **超时预警**\n"
                f"👤 客服: {agent_md}\n🔑 触发: `{matched_sig}`\n"
                f"⚠️ 状态: 超过 {TIMEOUT_SECONDS // 60} 分钟未回复。\n🔗 [点击回复]({link})"
            )
            
            run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
            JOB_CACHE[job_id] = {'agent_id': user.id, 'agent_name': user.first_name}
            
            print(f"📥 [SLA添加] ID: {job_id}")
            scheduler.add_job(send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id])
            
        elif job_id in JOB_CACHE:
            # ---> 普通回复 (检查是否追问)
            cache = JOB_CACHE[job_id]
            if msg.from_user.id == msg.reply_to_message.from_user.id:
                 # 使用同步函数发送，更稳定
                 send_chase_alert_sync(cache['agent_id'], cache['agent_name'], job_id, msg.text, msg.chat_id)
            else:
                del JOB_CACHE[job_id]
                if scheduler.get_job(job_id):
                    scheduler.remove_job(job_id)

    # ----------------------------------------------------
    # 场景 2: 新消息 (开启 Ghost 倒计时)
    # ----------------------------------------------------
    elif not msg.reply_to_message:
        if msg.from_user.is_bot: return
        
        ghost_id = f"ghost_user_{msg.from_user.id}"
        
        if not scheduler.get_job(ghost_id):
            run_time = datetime.now(timezone.utc) + timedelta(seconds=GHOST_TIMEOUT)
            txt = msg.text.replace('`', "'")[:30]
            
            print(f"👻 [Ghost添加] 新用户: {msg.from_user.first_name}")
            scheduler.add_job(
                send_ghost_alert, 'date', run_date=run_time, id=ghost_id, replace_existing=True,
                args=[ALERT_GROUP_ID, msg.message_id, msg.from_user.first_name, txt, msg.from_user.id, msg.chat_id]
            )

# ================= 构建 =================
# 连接池限制为 1
req = HTTPXRequest(connection_pool_size=1, read_timeout=10.0)
application = Application.builder().token(TOKEN).request(req).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

if __name__ == '__main__':
    print("Use: gunicorn main:app --workers 1")
