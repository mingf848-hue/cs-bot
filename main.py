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
TIMEOUT_SECONDS = 60    # 正式模式 15 分钟

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
    return "Bot is running (User Chase Alert Mode)"

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
            
            # 提取信息
            args_info = ""
            if job.args and len(job.args) >= 4:
                # args: [chat_id, text, agent_id, agent_name]
                args_info = f" (客服: {job.args[3]})"
                    
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

# ================= 预警任务函数 (15分钟超时) =================
# 注意：这里增加了 args 参数接收 agent 信息，虽然这个函数里可能暂时用不到
def send_alert_job(chat_id, text, agent_id, agent_name):
    print(f"⚡️ 正在执行超时预警... (客服: {agent_name})") 
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
        print("✅ 超时预警发送成功")
    except Exception as e:
        print(f"❌ 超时预警发送失败: {e}")
    finally:
        loop.close()

# ================= 🆕 客户追问提醒函数 (立即执行) =================
async def send_chase_alert(context, agent_id, agent_name, original_msg_id, chase_text):
    """
    当客户追问时，立即在预警群艾特客服
    """
    if str(CS_GROUP_ID).startswith('-100'):
        positive_chat_id = str(CS_GROUP_ID)[4:] 
    else:
        positive_chat_id = str(abs(CS_GROUP_ID))
    msg_link = f"https://t.me/c/{positive_chat_id}/{original_msg_id}"

    # 生成客服的艾特链接
    agent_mention = f"[{agent_name}](tg://user?id={agent_id})"
    
    # 截取客户追问内容
    safe_chase_text = chase_text.replace('`', "'")
    if len(safe_chase_text) > 30:
        safe_chase_text = safe_chase_text[:30] + "..."

    alert_text = (
        f"🔔 **客户追问提醒**\n\n"
        f"👤 客服: {agent_mention}\n"
        f"💬 追问: `{safe_chase_text}`\n"
        f"⚠️ 状态: 客户正在催促，请尽快回复！\n\n"
        f"🔗 [点击跳转回复]({msg_link})"
    )

    try:
        await context.bot.send_message(
            chat_id=ALERT_GROUP_ID,
            text=alert_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
        print(f"🔔 已发送客户追问提醒给 {agent_name}")
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

    # --- 逻辑 A: 客服回复“稍等” (开启监控) ---
    if matched_signature:
        user = msg.from_user
        
        # 1. 原始消息内容
        raw_original_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本消息]"
        safe_original_text = raw_original_text.replace('`', "'")
        if len(safe_original_text) > 50: safe_original_text = safe_original_text[:50] + "..."
        
        # 2. 生成艾特格式
        if user.username:
            agent_mention = f"@{user.username.replace('_', '\\_')}"
        else:
            agent_mention = f"[{user.first_name}](tg://user?id={user.id})"
        
        # 3. 链接
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

        print(f"📥 [新任务] ID: {job_id} | 客服: {user.first_name}")

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                # ✅ 关键修改：把 agent_id 和 agent_name 存入参数，方便后续提取
                args=[ALERT_GROUP_ID, alert_text, user.id, user.first_name], 
                misfire_grace_time=3600 
            )
            print(f"💾 [已存入] 计划执行(UTC): {run_time}")
        except Exception as e:
            print(f"❌ [失败] 数据库写入错误: {e}")
        
        await asyncio.sleep(0.1)
        return

    # --- 逻辑 B: 检测后续回复 (核心修改部分) ---
    try:
        # 1. 检查这个消息是否在监控列表中
        existing_job = scheduler.get_job(job_id)
        
        if existing_job:
            # 2. 判断是谁回复的
            # 原始消息发送者 ID (客户)
            original_sender_id = msg.reply_to_message.from_user.id
            # 当前回复者 ID
            current_sender_id = msg.from_user.id
            
            # 情况 1: 客户自己回复了 (追问) -> 触发提醒，不取消任务
            if current_sender_id == original_sender_id:
                print(f"🔔 [客户追问] ID: {job_id} | 客户正在催促...")
                
                # 从任务参数中提取客服 ID (args[2]是 agent_id, args[3]是 agent_name)
                # 注意：数据库里的 args 是 tuple
                agent_id = existing_job.args[2]
                agent_name = existing_job.args[3]
                
                # 发送追问提醒
                await send_chase_alert(context, agent_id, agent_name, original_msg_id, msg.text)
                
                # 任务继续保留，不删除！
                
            # 情况 2: 其他人回复了 (通常是客服) -> 任务完成，删除
            else:
                scheduler.remove_job(job_id)
                print(f"🗑️ [任务完成] ID: {job_id} | 客服已回复")

    except Exception as e:
        print(f"⚠️ 处理回复逻辑出错: {e}")

    await asyncio.sleep(0.1)

# ================= 启动逻辑 =================
request_config = HTTPXRequest(read_timeout=20.0, connect_timeout=20.0, http_version="1.1")
application = Application.builder().token(TOKEN).request(request_config).build()
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print("Run with 'gunicorn main:app'")
