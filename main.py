import logging
import os
import sys
import asyncio
import httpx
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
TIMEOUT_SECONDS = 12 * 60

# 触发关键词
WAIT_SIGNATURES = {
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
}

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    stream=sys.stdout 
)
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING) # 屏蔽 httpx 的日志噪音

# ================= 数据库连接设置 (极限优化) =================
database_url = os.environ.get('DATABASE_URL', 'sqlite:///jobs.sqlite')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# ⬇️⬇️⬇️ 极限内存优化：连接池降为 1 ⬇️⬇️⬇️
# 对于单进程单调度器，且任务量不大，1-2 个连接足够了
engine = create_engine(
    database_url,
    pool_recycle=1800,
    pool_pre_ping=True,
    pool_size=1,        # 极限优化：只保持 1 个连接
    max_overflow=2      # 临时最多允许增加 2 个
)

jobstores = {'default': SQLAlchemyJobStore(engine=engine)}
# ⬇️⬇️⬇️ 线程池优化：降为 4 ⬇️⬇️⬇️
# 你的任务只是发一个 HTTP 请求，极快，不需要太多线程
executors = {'default': ThreadPoolExecutor(4)} 
job_defaults = {'coalesce': False, 'max_instances': 5, 'misfire_grace_time': 3600}

# ================= 初始化组件 =================
# 必须先创建 Flask app
app = Flask(__name__)

# 创建调度器 (但不立即启动，防止被 Gunicorn 多次初始化)
scheduler = BackgroundScheduler(
    jobstores=jobstores, 
    executors=executors, 
    job_defaults=job_defaults,
    timezone=timezone.utc 
)

# 创建 PTB Application (懒加载配置)
request_config = HTTPXRequest(connection_pool_size=1) # 限制 Bot 内部连接池
application = Application.builder().token(TOKEN).request(request_config).build()

# ================= 路由与逻辑 =================
@app.route('/', methods=['GET'])
def index():
    return "Bot is running (Memory Optimized)"

@app.route('/debug', methods=['GET'])
def debug_jobs():
    try:
        # 限制显示数量，防止 job 太多导致生成页面时内存溢出
        jobs = scheduler.get_jobs()
        job_count = len(jobs)
        display_jobs = jobs[:50] # 只显示前50个
        
        job_list = []
        current_time = datetime.now(timezone.utc)
        
        for job in display_jobs:
            time_diff = "未知"
            if job.next_run_time:
                diff = job.next_run_time - current_time
                time_diff = f"{diff.total_seconds():.1f}s"
            
            args_info = ""
            if job.args and len(job.args) > 1:
                try:
                    content = job.args[1]
                    if "👤 回复人:" in content:
                        agent_part = content.split("👤 回复人:")[1].split("\n")[0].strip()
                        args_info = f" ({agent_part})"
                except:
                    pass
            job_list.append(f"<li>{job.id}{args_info} | {time_diff}</li>")
            
        html = f"""
        <h3>监控面板 (内存优化版)</h3>
        <p>任务总数: {job_count}</p>
        <p>当前显示: 前 {len(display_jobs)} 个</p>
        <hr>
        <ul>{''.join(job_list)}</ul>
        """
        return html
    except Exception as e:
        return f"Error: {str(e)}"

@app.route('/webhook', methods=['POST'])
async def webhook_handler():
    try:
        if not application.running:
             await application.initialize()
    except Exception:
        pass 
    
    try:
        json_data = request.get_json(force=True)
        update = Update.de_json(json_data, application.bot)
        await application.process_update(update)
    except Exception as e:
        print(f"Update error: {e}")
        
    return "ok"

# ================= 纯净版预警函数 (无 Bot 实例) =================
def send_alert_job(chat_id, text):
    print(f"⚡️ 执行预警 -> {chat_id}") 
    api_url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }
    
    try:
        # 使用 with 语句确保连接及时释放
        with httpx.Client(timeout=10.0) as client:
            response = client.post(api_url, json=payload)
            if response.status_code != 200:
                print(f"❌ API Error: {response.text}")
    except Exception as e:
        print(f"❌ Network Error: {e}")

# ================= 消息处理逻辑 =================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text or not msg.reply_to_message or msg.chat_id != CS_GROUP_ID:
        return

    original_msg_id = msg.reply_to_message.message_id
    job_id = str(original_msg_id) 

    matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

    if matched_signature:
        user = msg.from_user
        raw_original_text = msg.reply_to_message.text if msg.reply_to_message.text else "[非文本]"
        safe_original_text = raw_original_text.replace('`', "'")[:50]
        
        if user.username:
            agent_mention = f"@{user.username.replace('_', '\\_')}"
        else:
            safe_name = user.first_name.replace("[", "").replace("]", "")
            agent_mention = f"[{safe_name}](tg://user?id={user.id})"
        
        # 链接处理
        chat_str = str(CS_GROUP_ID)
        pid = chat_str[4:] if chat_str.startswith('-100') else str(abs(CS_GROUP_ID))
        msg_link = f"https://t.me/c/{pid}/{original_msg_id}"

        alert_text = (
            f"📩 消息: `{safe_original_text}`\n"
            f"🚨 **超时预警**\n"
            f"👤 客服: {agent_mention}\n"
            f"⚠️ 状态: 已等待 {TIMEOUT_SECONDS // 60} 分钟\n"
            f"🔗 [点击处理]({msg_link})"
        )

        run_time = datetime.now(timezone.utc) + timedelta(seconds=TIMEOUT_SECONDS)
        
        try:
            scheduler.add_job(
                send_alert_job, 'date', run_date=run_time, id=job_id, replace_existing=True,
                args=[ALERT_GROUP_ID, alert_text], misfire_grace_time=3600 
            )
            print(f"➕ 任务添加: {job_id}")
        except Exception as e:
            print(f"❌ 数据库错误: {e}")
        
        return

    try:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)
            print(f"➖ 任务移除: {job_id}")
    except Exception:
        pass 

application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.REPLY, handle_message))

# ================= 启动控制 =================
# 这种写法可以防止 Gunicorn 每个 Worker 都启动一次调度器
# 但因为我们强制使用了 workers=1，所以这里直接启动也是安全的
try:
    if not scheduler.running:
        scheduler.start()
        print("✅ 调度器已启动")
except Exception:
    pass

if __name__ == '__main__':
    # 本地测试
    app.run(port=8080)
