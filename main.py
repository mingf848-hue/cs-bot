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
# 核心配置区域 (Ver 28.0 StreamFlow)
# ==========================================
VERSION = "Ver 28.0 [StreamFlow]"
LOG_FILE_PATH = 'bot_debug.log'

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
    
    def parse_ids(env_val):
        if not env_val: return []
        return [int(x) for x in re.findall(r'-?\d+', env_val.replace("，", ","))]

    CS_GROUP_IDS = parse_ids(get_env("CS_GROUP_IDS"))
    ALERT_GROUP_IDS = parse_ids(get_env("ALERT_GROUP_ID"))
    OTHER_CS_IDS = parse_ids(get_env("OTHER_CS_IDS", ""))

    def parse_keywords(env_val, sep=','):
        if not env_val: return set()
        cleaned = env_val.replace("，", ",")
        return {x.lower().strip().replace('～', '~') for x in cleaned.split(sep) if x.strip()}

    WAIT_SIGNATURES = parse_keywords(get_env("WAIT_KEYWORDS"))
    KEEP_SIGNATURES = {x.strip() for x in get_env("KEEP_KEYWORDS", "").split('|') if x.strip()}
    
    default_ignore = "好的,谢谢,收到,明白,好的谢谢,ok,thx,thanks,好的呢,好滴,1"
    IGNORE_SIGNATURES = parse_keywords(get_env("IGNORE_KEYWORDS", default_ignore))

except Exception as e:
    print(f"❌ 配置解析崩溃: {traceback.format_exc()}")
    sys.exit(1)

# ==========================================
# 模块 0: 日志系统
# ==========================================
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)

class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    def formatTime(self, record, datefmt=None):
        return self.converter(record.created).strftime('%H:%M:%S')

file_fmt = BeijingFormatter('%(asctime)s | %(levelname)-7s | %(message)s', datefmt='%H:%M:%S')
file_handler = logging.FileHandler(LOG_FILE_PATH, mode='w', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_fmt)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(file_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)

def log(level, msg, tag="SYS"):
    icon = "📝"
    if level == 0: icon = "ℹ️ "
    elif level == 1: icon = "  ├ "
    elif level == 2: icon = "🌊 " # StreamFlow Icon
    elif level == 3: icon = "🚨 "
    elif level == 9: icon = "❌ "
    
    full_msg = f"[{tag}] {icon} {msg}"
    if level >= 9: logger.error(full_msg)
    elif level == 3: logger.warning(full_msg)
    elif level == 0 or level == 2: logger.info(full_msg)
    else: logger.debug(full_msg)

# ==========================================
# 模块 1: 核心逻辑 - 消息流识别
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60

# 任务池: MsgID -> Task
tasks_pool = {'wait': {}, 'followup': {}, 'reply': {}}
timers_pool = {'wait': {}, 'followup': {}, 'reply': {}}

# 索引: 
# 1. 消息流索引: (ChatID, ThreadID) -> Set(MsgIDs)
#    只要在这个"房间"里的任务，都在这里
thread_active_tasks = {}

# 2. 用户索引: (ChatID, UserID) -> Set(MsgIDs)
#    保底用，万一没有 ThreadID
user_active_tasks = {}

IS_WORKING = False
MY_ID = None

def get_thread_id(event):
    """
    🔥 核心函数: 获取消息流 ID (即 Topic ID / 房间号)
    """
    msg = event.message
    if not msg: return None
    
    # 1. 尝试获取 reply_to.reply_to_top_id (最准确的 Topic ID)
    if msg.reply_to and hasattr(msg.reply_to, 'reply_to_top_id') and msg.reply_to.reply_to_top_id:
        return msg.reply_to.reply_to_top_id
    
    # 2. 如果是普通回复链，尝试 reply_to_msg_id
    # (但在普通群，这只能代表上一条。为了严谨，我们尽量只用 TopID 做流式销单)
    # 如果没有 TopID，返回 None，退化为基于 User 的销单
    return None

def register_task(chat_id, user_id, msg_id, thread_id, task_type):
    """注册任务到双重索引"""
    # 1. 注册到 Thread 索引 (流式)
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key not in thread_active_tasks: thread_active_tasks[t_key] = set()
        thread_active_tasks[t_key].add(msg_id)
        log(1, f"加入消息流: Thread={thread_id} | Msg={msg_id}", "STREAM")
    
    # 2. 注册到 User 索引 (点对点保底)
    u_key = (chat_id, user_id)
    if u_key not in user_active_tasks: user_active_tasks[u_key] = set()
    user_active_tasks[u_key].add(msg_id)

def cancel_task_by_id(msg_id, reason="未知"):
    found = False
    for t_type in ['wait', 'followup', 'reply']:
        if msg_id in tasks_pool[t_type]:
            tasks_pool[t_type][msg_id].cancel()
            del tasks_pool[t_type][msg_id]
            if msg_id in timers_pool[t_type]: del timers_pool[t_type][msg_id]
            log(2, f"✅ 任务销单 ({t_type.upper()}) | Msg={msg_id} | Reason={reason}", "CANCEL")
            found = True
    return found

def clean_thread(chat_id, thread_id, trigger_event):
    """
    🔥 核心逻辑: 清理整个消息流 (Topic) 的任务
    """
    if not thread_id: return
    key = (chat_id, thread_id)
    if key not in thread_active_tasks: return
    
    msg_ids = list(thread_active_tasks[key])
    if not msg_ids: return

    log(2, f"🌊 流式销单触发 | Thread={thread_id} | 任务数={len(msg_ids)} | 触发={trigger_event}", "STREAM")
    
    for mid in msg_ids:
        cancel_task_by_id(mid, reason=f"流式响应: {trigger_event}")
    
    del thread_active_tasks[key]

def clean_user_backup(chat_id, user_id, trigger_event):
    """保底逻辑: 基于用户的销单"""
    if not user_id: return
    key = (chat_id, user_id)
    if key not in user_active_tasks: return
    msg_ids = list(user_active_tasks[key])
    for mid in msg_ids:
        cancel_task_by_id(mid, reason=f"用户响应: {trigger_event}")
    if key in user_active_tasks: del user_active_tasks[key]

# ==========================================
# 模块 2: 异步计时器
# ==========================================
async def generic_timer(task_type, timeout, msg_id, chat_id, user_id, thread_id, agent_name, text_preview, link):
    try:
        register_task(chat_id, user_id, msg_id, thread_id, task_type)
        
        # 记录到 Web 面板数据
        timers_pool[task_type][msg_id] = {'ts': time.time() + timeout, 'user': agent_name, 'cid': chat_id}
        log(1, f"⏳ 启动计时 [{task_type.upper()}] | Thread={thread_id} | User={user_id}", "TIMER")

        await asyncio.sleep(timeout)
        if not IS_WORKING: return

        # 检查物理存活
        try:
            msg = await client.get_messages(chat_id, ids=msg_id)
            if not msg: raise Exception("Deleted")
        except:
            log(2, f"超时但消息已删除 Msg={msg_id}", "SKIP")
            return

        # 报警
        title_map = {'wait': '稍等-超时预警', 'followup': '跟进-超时预警', 'reply': '漏回消息提醒'}
        log(3, f"🔥 报警触发 [{task_type.upper()}] Msg={msg_id}", "ALERT")
        
        alert_text = (
            f"📩 消息: `{text_preview.replace('`', '')}`\n"
            f"🚨 **{title_map.get(task_type, '未知报警')}**\n"
            f"👤 对象: {agent_name}\n"
            f"⚠️ 状态: 消息流 {timeout // 60} 分钟无新响应\n"
            f"🔗 [点击查看]({link})"
        )
        await send_alert(alert_text)

    except asyncio.CancelledError: pass
    finally:
        # 清理索引
        if thread_id:
            t_key = (chat_id, thread_id)
            if t_key in thread_active_tasks: thread_active_tasks[t_key].discard(msg_id)
        u_key = (chat_id, user_id)
        if u_key in user_active_tasks: user_active_tasks[u_key].discard(msg_id)
        
        if msg_id in tasks_pool[task_type]: del tasks_pool[task_type][msg_id]
        if msg_id in timers_pool[task_type]: del timers_pool[task_type][msg_id]

# ==========================================
# 模块 3: 辅助 (Web & Alert)
# ==========================================
app = Flask(__name__)
@app.route('/')
def index():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string("""
    <!DOCTYPE html><html><head><meta http-equiv="refresh" content="3">
    <style>body{background:#222;color:#eee;font-family:monospace}</style></head>
    <body><h2>System: {{s}}</h2><p>Wait: {{w}} | Follow: {{f}} | Reply: {{r}}</p>
    <p>Last Update: {{t}}</p><a href="/log" style="color:#0ff">View Log</a></body></html>
    """, s='🟢 ON' if IS_WORKING else '🔴 OFF', w=len(timers_pool['wait']), 
         f=len(timers_pool['followup']), r=len(timers_pool['reply']), t=now)

@app.route('/log')
def show_log():
    try:
        with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f: return Response(f.read(), mimetype='text/plain')
    except: return "No Log"

def run_web(): app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))

client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH,
    device_model="Mac mini M2", app_version="5.8.3 arm64 Mac App Store",
    system_version="macOS 15.6.1", lang_code="zh-hans", system_lang_code="zh-hans")

async def send_alert(text):
    if not BOT_TOKEN: return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for cid in ALERT_GROUP_IDS:
        try: requests.post(url, json={"chat_id": cid, "text": text, "parse_mode": "Markdown"}, timeout=5)
        except: pass

# ==========================================
# 模块 4: 主逻辑 (Client)
# ==========================================
@client.on(events.NewMessage(chats='me'))
async def sys_cmd(event):
    global IS_WORKING
    text = event.text.strip()
    if text == '上班':
        IS_WORKING = True
        log(0, "🟢 上班模式已启动", "SYS")
        await send_alert("🟢 **系统通知**\n👨‍💻 值班号状态：**上班中**\n✅ 流式监控已就绪")
    elif text == '下班':
        IS_WORKING = False
        log(0, "🔴 下班模式已启动", "SYS")
        for pool in tasks_pool.values():
            for t in pool.values(): t.cancel()
            pool.clear()
        timers_pool.clear(); thread_active_tasks.clear(); user_active_tasks.clear()
        await send_alert("🔴 **系统通知**\n🛌 值班号状态：**已下班**\n🛑 所有任务已清空")

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
    
    # 1. 获取身份和流 ID
    thread_id = get_thread_id(event)
    sender = await event.get_sender()
    sender_name = sender.first_name if sender else "Unknown"
    
    tag = f"MSG:{msg_id}"
    if thread_id: tag += f"|Th:{thread_id}"
    
    is_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)

    # ================= 客服发言 =================
    if is_cs:
        # 🔥 核心改变：客服只要在这个流(Topic)里说话，整个流的任务全消
        if thread_id:
            clean_thread(chat_id, thread_id, trigger_event=f"客服在流内回复({msg_id})")
        
        # 保底：如果是普通回复（引用），也消一下
        if event.reply_to_msg_id:
             # 这里逻辑简化，因为 Thread 清理更猛，这里只是为了兼容无 Thread 情况
             # 尝试反查引用对象去消单（代码略繁琐，依靠下面的 User 保底即可）
             pass

        # 如果客服发了指令 (稍等/跟进)
        reply_obj = await event.get_reply_message()
        if reply_obj:
            target_id = reply_obj.sender_id
            link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}"
            
            # 只有当 thread_id 存在时，我们才把这个任务绑定到 thread 上
            # 如果没有 thread_id，说明是普通群，退化为绑定到 user_id
            
            norm_text = text.lower()
            task_type = None
            if any(k in norm_text for k in WAIT_SIGNATURES): task_type = 'wait'
            elif text.strip() in KEEP_SIGNATURES: task_type = 'followup'
            
            if task_type:
                asyncio.create_task(generic_timer(
                    task_type, 
                    WAIT_TIMEOUT if task_type == 'wait' else FOLLOWUP_TIMEOUT,
                    reply_obj.id, # 监控的目标是客户的那条消息
                    chat_id, target_id, thread_id, 
                    sender_name, reply_obj.text[:20], link
                ))

    # ================= 客户发言 =================
    else:
        # 1. 客户说话，先消掉自己的所有任务 (点对点)
        clean_user_backup(chat_id, sender_id, trigger_event=f"客户发言({msg_id})")
        
        # 2. 如果在流里，消掉流的任务 (比如 A 提问，B 帮腔，把 A 的稍等也消了？)
        #    通常逻辑：客户B 说话，不应该取消 客户A 的稍等。
        #    但如果 客户B 是在该 Topic 下说话，通常意味着他们在讨论同一个问题。
        #    为了安全起见，这里**不**触发 clean_thread，只触发 clean_user
        #    (除非你希望客户说话也能打断客服的稍等？通常不用)
        
        # 3. 启动漏回监控
        if text.replace(' ','').lower() not in IGNORE_SIGNATURES:
             asyncio.create_task(generic_timer(
                'reply', REPLY_TIMEOUT, msg_id, chat_id, sender_id, thread_id, 
                sender_name, text[:20], f"https://t.me/c/{str(chat_id).replace('-100', '')}/{msg_id}"
            ))

if __name__ == '__main__':
    Thread(target=run_web).start()
    print(f"🚀 {VERSION} Started.")
    client.start()
    client.run_until_disconnected()
