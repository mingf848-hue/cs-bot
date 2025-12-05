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
# 关闭详细 Debug，因为我们现在有了更高效的缓存机制，不需要看数据库日志了
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

# ✅ 全局内存缓存：{ 'job_id': { 'agent_id': 123, 'agent_name': 'Tom' } }
# 这就是机器人的“短期记忆”，查它比查数据库快一万倍
JOB_CACHE = {}

def sync_cache_from_db():
    """启动时，把数据库里的任务同步到内存缓存里"""
    print("🔄 正在从数据库同步任务到内存缓存...")
    jobs = scheduler.get_jobs()
    count = 0
    for job in jobs:
        # job.args 结构: [chat_id, text, agent_id, agent_name]
        if job.args and len(job.args) >= 4:
            JOB_CACHE[job.id] = {
                'agent_id': job.args[2],
                'agent_name': job.args[3]
            }
            count += 1
    print(f"✅ 同步完成！内存中现有 {count} 个活跃任务。")

# 启动调度器
scheduler.start()
# 启动后立刻同步一次缓存
sync_cache_from_db()

# ================= Flask Web Server =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return f"Bot is running with RAM Cache! (Active Jobs: {len(JOB_CACHE)})"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    # 直接读缓存，不读数据库，飞快
    return f"<h1>内存缓存监控</h1><p>当前活跃任务数: {len(JOB_CACHE)}</p><p>{JOB_CACHE}</p>"

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
def send_alert_job(chat_id, text, agent_id, agent_name, job_id_for_cleanup=None):
    # 发送预警
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
        print("✅ 预警发送成功")
    except Exception as e:
        print(f"❌ 预警发送失败: {e}")
    finally:
        loop.close()
    
    # 任务执行完了，清理内存缓存 (虽然 APScheduler 会删数据库，我们要手动删内存)
    if job_id_for_cleanup and job_id_for_cleanup in JOB_CACHE:
        del JOB_CACHE[job_id_for_cleanup]
        print(f"🧹 任务完成，已从缓存清理: {job_id_for_cleanup}")

# ================= 追问提醒函数 =================
async def send_chase_alert(context, agent_id, agent_name, original_msg_id, chase_text):
    if str(CS_GROUP_ID).startswith('-100'):
        positive_chat_id = str(CS_GROUP_ID)[4:] 
    else:
        positive_chat_id = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

    agent_mention = f"[{agent_name}](tg://user?id={agent_id})"
    safe_chase_text = chase_text.replace('`', "'")
    if len(safe_chase_text) > 30: safe_chase_text = safe_chase_text[:30] + "..."

    alert_text = (
        f"🔔 **客户追问提醒**\n\n"
        f"👤 客服: {agent_mention}\n"
        f"💬 追问: `{safe_chase_text}`\n"
        f"⚠️ 状态: 客户正在催促，请尽快回复！\n\n"
        f"🔗 [点击跳转回复]({msg_link})"
    )

    try:
        await context.bot.send_message(chat_id=ALERT_GROUP_ID, text=alert_text, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        print(f"❌ 追问提醒发送失败: {e}")

# ================= Bot 逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.reply_to_message or msg.chat_id != CS_GROUP_ID:
        return

    original_msg_id = msg.reply_to_message.message_id
    job_id = str(original_msg_id) 

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

    # --- 逻辑 A: 开启监控 (写 DB + 写 Cache) ---
    if matched_signature:
        user = msg.from_user
        
        raw_original_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本消息]"
        safe_original_text = raw_original_text.replace('`', "'")
        if len(safe_original_text) > 50: safe_original_text = safe_original_text[:50] + "..."
        
        if user.username:
            agent_mention = f"@{user.username.replace('_', '\\_')}"
        else:
            agent_mention = f"[{user.first_name}](tg://user?id={user.id})"
        
        if str(CS_GROUP_ID).startswith('-100'):
            positive_chat_id = str(CS_GROUP_ID)[4:] 
        else:
            positive_chat_id = str(abs(CS_GROUP_ID))
        msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

        current_timeout_display = f"{TIMEOUT_SECONDS // 60} 分钟"
        if TIMEOUT_SECONDS == 60: current_timeout_display = "60 秒"

        alert_text = (
            f"📩 原始消息: `{safe_original_text}`\n\n"
            f"🚨 **稍等超时预警 ({current_timeout_display})**\n"
            f"👤 回复人: {agent_mention}\n"
            f"🔑 稍等: `{matched_signature}`\n"
            f"⚠️ 状态: 回复稍等后，超过 {current_timeout_display} 未进一步回复。\n\n"
            f"🔗 [点击跳转处理]({msg_link})"
        )

        print(f"📥 [新任务] ID: {job_id}")

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        # 1. 存入内存缓存 (极速)
        JOB_CACHE[job_id] = {
            'agent_id': user.id,
            'agent_name': user.first_name
        }

        # 2. 存入数据库 (持久化)
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                # 多传一个 job_id 参数，方便回调里清理缓存
                args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id], 
                misfire_grace_time=3600 
            )
        except Exception as e:
            print(f"❌ DB写入失败: {e}")
        
        await asyncio.sleep(0.1)
        return

    # --- 逻辑 B: 检测后续回复 (只读 Cache，不读 DB) ---
    # ✅ 核心优化：直接查内存字典，不需要 await，不需要 IO，不需要 SSL，纳秒级响应
    if job_id in JOB_CACHE:
        cache_data = JOB_CACHE[job_id]
        
        original_sender_id = msg.reply_to_message.from_user.id
        current_sender_id = msg.from_user.id
        
        # 情况 1: 客户追问 (无需查库，极速响应)
        if current_sender_id == original_sender_id:
            print(f"🔔 [内存命中] 客户追问 ID: {job_id}")
            # 从缓存直接拿数据
            await send_chase_alert(context, cache_data['agent_id'], cache_data['agent_name'], original_msg_id, msg.text)
            
        # 情况 2: 客服回复 (需要操作 DB 删除任务)
        else:
            print(f"🗑️ [内存命中] 客服回复，清理 ID: {job_id}")
            # 1. 删缓存
            del JOB_CACHE[job_id]
            # 2. 删数据库 (异步操作，即便失败也不影响本次响应)
            try:
                scheduler.remove_job(job_id)
            except Exception:
                pass # 任务可能刚好执行完，忽略错误

    await asyncio.sleep(0.1)

# ================= 启动逻辑 =================
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("Run with 'gunicorn main:app'")
