import os
import sys
import asyncio
import logging
import requests
import re
import time
import traceback
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask, render_template_string, Response, request
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ==========================================
# 核心配置区域
# ==========================================
VERSION = "Ver 27.0 [DeepTrace]"
LOG_FILE_PATH = 'bot_debug.log'

# 环境变量读取 (带详细错误提示)
def get_env(key, default=None, required=True):
    val = os.environ.get(key, default)
    if required and val is None:
        print(f"❌ 致命错误: 缺少环境变量 {key}")
        sys.exit(1)
    return val

try:
    API_ID = int(get_env("API_ID"))
    API_HASH = get_env("API_HASH")
    SESSION_STRING = get_env("SESSION_STRING")
    BOT_TOKEN = get_env("BOT_TOKEN")
    
    # 解析 ID 列表
    def parse_ids(env_val):
        if not env_val: return []
        return [int(x) for x in re.findall(r'-?\d+', env_val.replace("，", ","))]

    CS_GROUP_IDS = parse_ids(get_env("CS_GROUP_IDS"))
    ALERT_GROUP_IDS = parse_ids(get_env("ALERT_GROUP_ID"))
    OTHER_CS_IDS = parse_ids(get_env("OTHER_CS_IDS", ""))

    # 解析关键词集合
    def parse_keywords(env_val, sep=','):
        if not env_val: return set()
        cleaned = env_val.replace("，", ",")
        return {x.lower().strip().replace('～', '~') for x in cleaned.split(sep) if x.strip()}

    WAIT_SIGNATURES = parse_keywords(get_env("WAIT_KEYWORDS"))
    # 跟进关键词用 | 分割
    KEEP_SIGNATURES = {x.strip() for x in get_env("KEEP_KEYWORDS", "").split('|') if x.strip()}
    
    default_ignore = "好的,谢谢,收到,明白,好的谢谢,ok,thx,thanks,好的呢,好滴,1"
    IGNORE_SIGNATURES = parse_keywords(get_env("IGNORE_KEYWORDS", default_ignore))

except Exception as e:
    print(f"❌ 配置解析崩溃: {traceback.format_exc()}")
    sys.exit(1)

# ==========================================
# 模块 0: 增强型日志系统 (北京时间 + 结构化)
# ==========================================
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)

class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    def formatTime(self, record, datefmt=None):
        return self.converter(record.created).strftime('%H:%M:%S')

# 日志格式优化，增加对齐
file_fmt = BeijingFormatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S')
file_handler = logging.FileHandler(LOG_FILE_PATH, mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_fmt)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO) # 控制台只看 INFO，保持清爽
console_handler.setFormatter(file_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 屏蔽杂音
logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)
logging.getLogger('aiohttp').setLevel(logging.WARNING)

# 调试开关
DEBUG_MODE = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

def log(level, msg, tag="SYS"):
    """
    level: 0=INFO, 1=DEBUG(Tree), 2=IMPORTANT, 3=ALERT, 9=ERROR
    """
    icon = "📝"
    if level == 0: icon = "ℹ️ "
    elif level == 1: icon = "  ├ "
    elif level == 2: icon = "✨ "
    elif level == 3: icon = "🚨 "
    elif level == 9: icon = "❌ "
    
    full_msg = f"[{tag}] {icon} {msg}"
    
    if level >= 9: logger.error(full_msg)
    elif level == 3: logger.warning(full_msg)
    elif level == 0 or level == 2: logger.info(full_msg)
    else: logger.debug(full_msg) # DEBUG 级别

# ==========================================
# 模块 1: 全局状态与常量
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60

# 任务句柄存储 (MsgID -> Task)
tasks_pool = {
    'wait': {},
    'followup': {},
    'reply': {}
}

# 计时器信息存储 (MsgID -> InfoDict) - 用于 Web 展示
timers_pool = {
    'wait': {},
    'followup': {},
    'reply': {}
}

# 映射表
wait_msg_map = {}       # ReplyMsgID -> OriginMsgID
followup_msg_map = {}   # ReplyMsgID -> OriginMsgID

# 用户活跃任务索引: (ChatID, UserID) -> Set(MsgIDs)
# 用于快速通过 UserID 找到所有关联的任务并取消
user_active_tasks = {}

# 消息反查用户缓存: MsgID -> UserID
msg_user_cache = {}

IS_WORKING = False
MY_ID = None

# ==========================================
# 模块 2: 任务管理核心 (增加详细 TraceLog)
# ==========================================

def register_task(chat_id, user_id, msg_id, task_type):
    """注册任务到索引"""
    if not user_id: return
    key = (chat_id, user_id)
    if key not in user_active_tasks: user_active_tasks[key] = set()
    user_active_tasks[key].add(msg_id)
    msg_user_cache[msg_id] = user_id
    if DEBUG_MODE: log(1, f"任务注册: Type={task_type} Msg={msg_id} -> User={user_id}", "TASK")

def unregister_task(chat_id, user_id, msg_id):
    """从索引移除任务"""
    if not user_id: return
    key = (chat_id, user_id)
    if key in user_active_tasks:
        user_active_tasks[key].discard(msg_id)
        if not user_active_tasks[key]: del user_active_tasks[key]
    if msg_id in msg_user_cache: del msg_user_cache[msg_id]

def cancel_task_by_id(msg_id, reason="未知"):
    """精准取消某一条消息的任务"""
    found = False
    for t_type in ['wait', 'followup', 'reply']:
        if msg_id in tasks_pool[t_type]:
            tasks_pool[t_type][msg_id].cancel()
            del tasks_pool[t_type][msg_id]
            if msg_id in timers_pool[t_type]: del timers_pool[t_type][msg_id]
            log(2, f"任务取消 ({t_type.upper()}) | Msg={msg_id} | Reason={reason}", "CANCEL")
            found = True
    
    # 清理映射
    if msg_id in wait_msg_map: del wait_msg_map[msg_id]
    if msg_id in followup_msg_map: del followup_msg_map[msg_id]
    
    # 清理索引 (需要知道 UserID，如果不知道只能遍历)
    if msg_id in msg_user_cache:
        uid = msg_user_cache[msg_id]
        # 这里很难获取 ChatID，暂时不清理 user_active_tasks 的冗余，依靠 unregister_task 在任务结束时清理
        pass 
    return found

def cancel_all_for_user(chat_id, user_id, trigger_event):
    """销单：取消该用户在该群的所有任务"""
    if not user_id: return
    key = (chat_id, user_id)
    if key not in user_active_tasks: return

    msg_ids = list(user_active_tasks[key])
    if not msg_ids: return

    log(2, f"触发销单 | 用户={user_id} | 涉及任务数={len(msg_ids)} | 触发源={trigger_event}", "CLEAN")
    
    for mid in msg_ids:
        cancel_task_by_id(mid, reason=f"用户活跃: {trigger_event}")
    
    # 彻底清空索引
    if key in user_active_tasks: del user_active_tasks[key]

# ==========================================
# 模块 3: 异步倒计时逻辑 (带异常捕获)
# ==========================================

async def check_msg_alive(chat_id, msg_id):
    """检查消息是否被物理删除"""
    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
        if not msg:
            log(1, f"消息物理删除检测: Msg={msg_id} 已不存在", "CHECK")
            return False
        return True
    except Exception as e:
        log(1, f"网络检测失败 Msg={msg_id}: {e} (默认视为存在)", "CHECK")
        return True

async def generic_timer(task_type, timeout, msg_id, chat_id, user_id, agent_name, text_preview, link, my_reply_id=None):
    """通用倒计时处理器"""
    try:
        register_task(chat_id, user_id, msg_id, task_type)
        end_time = time.time() + timeout
        timers_pool[task_type][msg_id] = {'ts': end_time, 'user': agent_name, 'url': link, 'cid': chat_id}
        
        log(1, f"⏳ 启动计时 [{task_type.upper()}] {timeout}s | Msg={msg_id} | User={user_id}", "TIMER")

        await asyncio.sleep(timeout)

        if not IS_WORKING: return

        # 检查是否被物理删除
        alive_check_id = my_reply_id if my_reply_id else msg_id
        if not await check_msg_alive(chat_id, alive_check_id):
            log(2, f"超时触发但消息已删除，跳过报警 | Msg={msg_id}", "SKIP")
            return

        # 触发报警
        minutes = timeout // 60
        title_map = {'wait': '稍等-超时预警', 'followup': '跟进-超时预警', 'reply': '漏回消息提醒'}
        
        log(3, f"🔥 触发报警 [{task_type.upper()}] Msg={msg_id}", "ALERT")
        
        alert_text = (
            f"📩 消息: `{text_preview.replace('`', '')}`\n"
            f"🚨 **{title_map.get(task_type, '未知报警')}**\n"
            f"👤 对象: {agent_name}\n"
            f"⚠️ 状态: 超时 {minutes} 分钟未处理\n"
            f"🔗 [点击查看]({link})"
        )
        await send_alert(alert_text)

    except asyncio.CancelledError:
        log(1, f"计时被取消 [{task_type.upper()}] Msg={msg_id}", "TIMER")
    except Exception as e:
        log(9, f"计时器内部崩溃 Msg={msg_id}: {traceback.format_exc()}", "ERROR")
    finally:
        # 清理工作
        unregister_task(chat_id, user_id, msg_id)
        if msg_id in tasks_pool[task_type]: del tasks_pool[task_type][msg_id]
        if msg_id in timers_pool[task_type]: del timers_pool[task_type][msg_id]

# ==========================================
# 模块 4: Web 监控台 (Flask)
# ==========================================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>DeepTrace Monitor</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="3">
    <style>
        body { background: #222; color: #eee; font-family: monospace; padding: 20px; }
        .status { padding: 10px; background: #333; border-radius: 5px; margin-bottom: 20px; border-left: 5px solid #777; }
        .working { border-color: #28a745; } .stopped { border-color: #dc3545; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 15px; }
        .card { background: #2d2d2d; padding: 10px; border: 1px solid #444; border-radius: 5px; }
        .card h3 { margin: 0 0 10px 0; color: #aaa; border-bottom: 1px solid #444; padding-bottom: 5px; }
        .item { display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 5px; padding: 3px; background: #383838; }
        .time { color: #ff79c6; font-weight: bold; }
        a { color: #8be9fd; text-decoration: none; }
        .btn { display: inline-block; padding: 10px 20px; background: #007acc; color: white; border-radius: 4px; margin-top: 20px; }
    </style>
</head>
<body>
    <div class="status {{ 'working' if working else 'stopped' }}">
        <h2>System Status: {{ 'RUNNING 🟢' if working else 'STOPPED 🔴' }}</h2>
        <div>Ver: {{ ver }} | Time: {{ now }}</div>
    </div>
    
    <div class="grid">
        <div class="card">
            <h3>⏳ 稍等 ({{ w|length }})</h3>
            {% for mid, info in w.items() %}
            <div class="item">
                <span>{{ info.user }}</span>
                <span class="time" data-ts="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        </div>
        <div class="card">
            <h3>🕵️ 跟进 ({{ f|length }})</h3>
            {% for mid, info in f.items() %}
            <div class="item">
                <span>{{ info.user }}</span>
                <span class="time" data-ts="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        </div>
        <div class="card">
            <h3>🔔 漏回 ({{ r|length }})</h3>
            {% for mid, info in r.items() %}
            <div class="item">
                <span>{{ info.user }}</span>
                <span class="time" data-ts="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        </div>
    </div>

    <a href="/log" target="_blank" class="btn">📜 查看详细日志</a>

    <script>
        setInterval(() => {
            const now = Date.now() / 1000;
            document.querySelectorAll('.time').forEach(el => {
                const ts = parseFloat(el.dataset.ts);
                const diff = ts - now;
                if (diff < 0) el.innerText = "TIMEOUT";
                else el.innerText = `${Math.floor(diff/60)}m ${Math.floor(diff%60)}s`;
            });
        }, 1000);
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, 
        working=IS_WORKING, ver=VERSION, now=now,
        w=timers_pool['wait'], f=timers_pool['followup'], r=timers_pool['reply'])

@app.route('/log')
def show_log():
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    except: content = "暂无日志"
    return Response(content, mimetype='text/plain')

def run_web():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ==========================================
# 模块 5: Telegram Client (严禁修改伪装参数)
# ==========================================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    # 👇 以下参数必须与生成密钥时完全一致，不可更改 👇
    device_model="Mac mini M2", 
    app_version="5.8.3 arm64 Mac App Store",      # 👈 改回官方版本号，不要用 DeepTrace
    system_version="macOS 15.6.1", 
    lang_code="zh-hans", 
    system_lang_code="zh-hans"
)
async def send_alert(text):
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for chat_id in ALERT_GROUP_IDS:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=5)
        except Exception as e:
            log(9, f"报警发送失败: {e}", "NET")

async def get_recursive_sender(chat_id, msg_id, depth=0):
    """递归查找真实的发言人（穿透客服引用）"""
    if depth > 5: return None # 防止死循环
    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
        if not msg: return None
        
        sender_id = msg.sender_id
        if sender_id == MY_ID or sender_id in OTHER_CS_IDS:
            if msg.reply_to_msg_id:
                log(1, f"  └ 递归溯源: Msg={msg_id} 是客服 -> 继续查 Msg={msg.reply_to_msg_id}", "TRACE")
                return await get_recursive_sender(chat_id, msg.reply_to_msg_id, depth + 1)
            else:
                return None # 客服直接发言且无引用，无法溯源
        return sender_id
    except: return None

@client.on(events.NewMessage(chats='me'))
async def sys_cmd(event):
    global IS_WORKING
    text = event.text.strip()
    if text == '上班':
        IS_WORKING = True
        log(0, "🟢 系统切换为：上班模式", "SYS")
        await event.reply("🟢 上班啦！开始监控。")
    elif text == '下班':
        IS_WORKING = False
        log(0, "🔴 系统切换为：下班模式", "SYS")
        # 清空所有任务
        for pool in tasks_pool.values():
            for task in pool.values(): task.cancel()
            pool.clear()
        for pool in timers_pool.values(): pool.clear()
        wait_msg_map.clear(); followup_msg_map.clear(); user_active_tasks.clear()
        await event.reply("🔴 下班啦！任务已清空。")
    elif text == 'dump':
        # 调试用：打印内存状态
        status = f"Wait: {len(tasks_pool['wait'])}, Follow: {len(tasks_pool['followup'])}, Reply: {len(tasks_pool['reply'])}"
        log(0, f"状态快照: {status}", "DUMP")
        await event.reply(status)

@client.on(events.MessageDeleted)
async def on_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        # 如果这个 ID 正在被监控，取消它
        if cancel_task_by_id(msg_id, reason="物理删除"):
            log(2, f"捕获删除事件 Msg={msg_id} -> 任务已撤销", "DEL")

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def main_handler(event):
    global MY_ID
    if not MY_ID: MY_ID = (await client.get_me()).id
    if not IS_WORKING: return

    chat_id = event.chat_id
    msg_id = event.id
    text = event.text or ""
    sender_id = event.sender_id
    reply_to_id = event.reply_to_msg_id
    
    # 获取发送者名字
    sender = await event.get_sender()
    try: sender_name = sender.first_name or "Unknown"
    except: sender_name = "Unknown"

    log_tag = f"MSG:{msg_id}"
    log(1, f"收到消息 | Chat={chat_id} | User={sender_id}({sender_name}) | ReplyTo={reply_to_id} | Len={len(text)}", log_tag)

    # ----------------------------------------
    # 场景 1: 客服发言 (MY_ID 或 OTHER_CS_IDS)
    # ----------------------------------------
    if sender_id == MY_ID or sender_id in OTHER_CS_IDS:
        # A. 既然客服说话了，先尝试找到他是在回谁，把那个人的报警消掉
        target_customer_id = None
        
        # 1. 尝试缓存反查
        if reply_to_id and reply_to_id in msg_user_cache:
            target_customer_id = msg_user_cache[reply_to_id]
            log(1, f"缓存命中: 引用 {reply_to_id} -> 客户 {target_customer_id}", log_tag)
        
        # 2. 尝试映射表 (稍等/跟进任务的回复)
        if not target_customer_id and reply_to_id:
            if reply_to_id in wait_msg_map:
                # 这里的逻辑较复杂，简化处理：如果引用了之前的稍等消息，说明之前那个稍等任务结束了
                origin_id = wait_msg_map[reply_to_id]
                cancel_task_by_id(origin_id, reason="客服回复了稍等消息")
            
            if reply_to_id in reply_tasks:
                cancel_task_by_id(reply_to_id, reason="客服回复了漏回消息")

        # 3. 递归查 (保底)
        if not target_customer_id and reply_to_id:
            target_customer_id = await get_recursive_sender(chat_id, reply_to_id)
            if target_customer_id: log(1, f"API溯源成功: 客户 {target_customer_id}", log_tag)

        # 执行销单
        if target_customer_id:
            cancel_all_for_user(chat_id, target_customer_id, trigger_event=f"客服回复({msg_id})")

        # B. 判断是否触发新任务 (稍等/跟进)
        if not reply_to_id: return # 客服自言自语，不处理

        norm_text = text.lower().replace('～', '~')
        link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}"
        
        # 获取被回复的内容预览
        reply_obj = await event.get_reply_message()
        preview = reply_obj.text[:30] if reply_obj else "未知内容"
        customer_id = reply_obj.sender_id if reply_obj else target_customer_id

        if any(k in norm_text for k in WAIT_SIGNATURES):
            # 启动 [稍等]
            wait_msg_map[msg_id] = reply_to_id # 记录：这条稍等消息 对应 那个客户问题
            asyncio.create_task(generic_timer(
                'wait', WAIT_TIMEOUT, reply_to_id, chat_id, customer_id, sender_name, preview, link, my_reply_id=msg_id
            ))
        
        elif text.strip() in KEEP_SIGNATURES:
            # 启动 [跟进]
            followup_msg_map[msg_id] = reply_to_id
            asyncio.create_task(generic_timer(
                'followup', FOLLOWUP_TIMEOUT, reply_to_id, chat_id, customer_id, sender_name, preview, link, my_reply_id=msg_id
            ))

    # ----------------------------------------
    # 场景 2: 客户发言
    # ----------------------------------------
    else:
        # 客户只要说话，就取消他身上所有的漏回报警
        cancel_all_for_user(chat_id, sender_id, trigger_event=f"客户追问({msg_id})")

        # 判断是否被忽略
        norm_text = text.lower().replace('～', '~').replace(' ', '')
        if norm_text in IGNORE_SIGNATURES:
            log(1, f"命中忽略词: {text} -> 不监控", log_tag)
            return

        # 启动 [漏回] 监控
        # 只有当这消息是回复客服(或直接发群里)时才监控。
        # 简单起见：所有客户发言都监控，除非客服回了。
        link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}"
        asyncio.create_task(generic_timer(
            'reply', REPLY_TIMEOUT, msg_id, chat_id, sender_id, sender_name, text[:30], link
        ))


if __name__ == '__main__':
    # 启动 Web
    Thread(target=run_web).start()
    
    print(f"🚀 {VERSION} 启动中...")
    log(0, f"系统启动 | ID={API_ID} | 稍等词={len(WAIT_SIGNATURES)}", "BOOT")
    
    # 启动 Client
    client.start()
    client.run_until_disconnected()
