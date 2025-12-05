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
    """启动时同步任务"""
    print("🔄 正在从数据库同步任务到内存缓存...")
    try:
        jobs = scheduler.get_jobs()
        count = 0
        for job in jobs:
            if job.args and len(job.args) >= 4:
                JOB_CACHE[job.id] = {
                    'agent_id': job.args[2],
                    'agent_name': job.args[3]
                }
                count += 1
        print(f"✅ 同步完成！内存中现有 {count} 个活跃任务。")
    except Exception as e:
        print(f"⚠️ 缓存同步跳过: {e}")

# 发送启动通知
def send_startup_notification():
    print("🚀 发送启动通知...")
    temp_bot = Bot(token=TOKEN)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        job_count = len(JOB_CACHE)
        
        # ✅ 修改点：去掉了“(防撞车版)”备注
        alert_text = (
            f"♻️ **机器人已重启 (System Restart)**\n\n"
            f"📅 时间: `{current_time}`\n"
            f"📊 活跃监控任务: {job_count} 个\n"
            f"✅ 状态: 服务已恢复在线，监控继续。"
        )
        
        loop.run_until_complete(temp_bot.send_message(
            chat_id=ALERT_GROUP_ID,
            text=alert_text,
            parse_mode='Markdown'
        ))
        print("✅ 启动通知发送成功")
    except Exception as e:
        print(f"❌ 启动通知发送失败: {e}")
    finally:
        loop.close()

def heartbeat():
    pass

scheduler.add_job(heartbeat, 'interval', seconds=10, id='heartbeat_job', replace_existing=True)
scheduler.start()
sync_cache_from_db()
send_startup_notification()

# ================= Flask Web Server =================
app = Flask(__name__)

@app.route('/', methods=['GET'])
def index():
    return f"Bot is running. Jobs: {len(JOB_CACHE)}"

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
            
            args_info = ""
            if job.args and len(job.args) > 1:
                try:
                    content = job.args[1]
                    if "👤 回复人:" in content:
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
def send_alert_job(chat_id, text, agent_id, agent_name, job_id_for_cleanup=None):
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
        print("✅ 预警消息已成功发送")
    except Exception as e:
        print(f"❌ 预警发送失败: {e}")
    finally:
        loop.close()
    
    if job_id_for_cleanup and job_id_for_cleanup in JOB_CACHE:
        del JOB_CACHE[job_id_for_cleanup]

def get_job_with_retry(job_id, max_retries=3):
    for i in range(max_retries):
        try:
            return scheduler.get_job(job_id)
        except Exception:
            time.sleep(0.5)
    return None

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

    # --- 逻辑 A: 开启监控 (防撞车) ---
    if matched_signature:
        # ✅ 防撞车逻辑：如果已经有人接了，直接忽略后续的“稍等”
        if job_id in JOB_CACHE:
            first_agent = JOB_CACHE[job_id]['agent_name']
            print(f"🛡️ [防撞车] 忽略 {msg.from_user.first_name}，因为 {first_agent} 已抢单")
            return
        
        user = msg.from_user
        
        # 原始消息处理
        raw_original_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本消息]"
        safe_original_text = raw_original_text.replace('`', "'")
        if len(safe_original_text) > 50: safe_original_text = safe_original_text[:50] + "..."
        
        # 艾特格式
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

        print(f"📥 [新任务] ID: {job_id} | 回复人: {user.first_name}")

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        # 更新内存
        JOB_CACHE[job_id] = {
            'agent_id': user.id,
            'agent_name': user.first_name
        }

        # 写入数据库
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name, job_id], 
                misfire_grace_time=3600 
            )
            print(f"💾 [已存入] 计划执行(UTC): {run_time}")
        except Exception as e:
            print(f"❌ DB写入失败: {e}")
        
        await asyncio.sleep(0.1)
        return

    # --- 逻辑 B: 检测后续回复 ---
    if job_id in JOB_CACHE:
        cache_data = JOB_CACHE[job_id]
        
        original_sender_id = msg.reply_to_message.from_user.id
        current_sender_id = msg.from_user.id
        
        # 情况 1: 客户追问
        if current_sender_id == original_sender_id:
            print(f"🔔 [内存命中] 客户追问 ID: {job_id}")
            await send_chase_alert(context, cache_data['agent_id'], cache_data['agent_name'], original_msg_id, msg.text)
            
        # 情况 2: 客服回复
        else:
            print(f"🗑️ [内存命中] 客服回复，清理 ID: {job_id}")
            del JOB_CACHE[job_id]
            try:
                get_job_with_retry(job_id)
                scheduler.remove_job(job_id)
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
