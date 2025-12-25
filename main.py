import os
import sys
import asyncio
import logging
import requests
import re
import time
import json
import queue
from collections import deque, defaultdict
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask, render_template_string, Response, request, stream_with_context
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError

# ==========================================
# 模块 0: 北京时间树状日志系统
# ==========================================
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)
LOG_FILE_PATH = 'bot_debug.log'

class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    def formatTime(self, record, datefmt=None):
        return self.converter(record.created).strftime('%Y-%m-%d %H:%M:%S')

file_fmt = BeijingFormatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
file_handler = logging.FileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8')
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_fmt)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(file_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)

_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

def log_tree(level, msg):
    prefix = ""
    if level == 0:   prefix = "📦 "
    elif level == 1: prefix = " ┣━━ "
    elif level == 2: prefix = " ┗━━ "
    elif level == 3: prefix = " 🚨 [ALERT] "
    elif level == 4: prefix = " 👮 [AUDIT] " 
    elif level == 9: prefix = " ❌ [ERROR] "
    
    full_msg = f"{prefix}{msg}"
    if _sys_opt or level >= 2: logger.info(full_msg)
    else: logger.debug(full_msg)

# ==========================================
# 模块 1: 基础函数 (强力清洗版)
# ==========================================
def normalize(text):
    if not text: return ""
    text = text.lower()
    # [Ver 34.0] 移除所有标点符号和空白，只保留纯文本
    text = re.sub(r'[^\w=]', '', text) 
    return text

def extract_id_list(env_str):
    if not env_str: return []
    clean_str = env_str.replace("，", ",")
    items = clean_str.split(',')
    result = []
    for item in items:
        match = re.search(r'-?\d+', item)
        if match:
            try: result.append(int(match.group()))
            except: pass
    return result

# ==========================================
# 模块 2: 配置加载
# ==========================================
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    cs_groups_env = os.environ["CS_GROUP_IDS"]
    CS_GROUP_IDS = extract_id_list(cs_groups_env)
    alert_env = os.environ["ALERT_GROUP_ID"]
    ALERT_GROUP_IDS = extract_id_list(alert_env)
    other_cs_env = os.environ.get("OTHER_CS_IDS", "")
    OTHER_CS_IDS = extract_id_list(other_cs_env)
    
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    # [Ver 35.0 Fix] 确保不包含空字符串，防止图片/无文字消息被误判
    raw_wait = {normalize(x) for x in clean_env.split(',') if x.strip()}
    WAIT_SIGNATURES = {x for x in raw_wait if x} 

    # [Ver 42.1] 修复 Keep 关键词解析逻辑：优先使用 | 分隔，防止句子中的逗号把关键词截断
    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    if '|' in keep_keywords_env:
        # 如果包含竖线，仅使用竖线分隔（允许关键词内包含逗号）
        keep_list = keep_keywords_env.split('|')
    else:
        # 兼容旧格式：如果没有竖线，则同时支持中文逗号和英文逗号
        keep_clean = keep_keywords_env.replace("，", ",")
        keep_list = keep_clean.split(',')
        
    raw_keep = {normalize(x) for x in keep_list if x.strip()}
    KEEP_SIGNATURES = {x for x in raw_keep if x}
    
    log_tree(0, f"🔍 关键词配置 (Normalized): WAIT={WAIT_SIGNATURES} | KEEP={KEEP_SIGNATURES}")

    # [Ver 36.3] 扩展忽略关键词库 (包含用户请求的新增词汇)
    # normalized 会去除标点，所以 "好的，感谢" 会匹配 "好的感谢"
    default_ignore = (
        "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴,"
        "好的呢,嗯,嗯嗯,谢了,okk,k,行,妥,了解,已收,没问题,好的收到,ok了,麻烦了,"
        "好的感谢,哦,知道了,好的知道了"
    )
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x) for x in clean_ignore.split(',') if x.strip()}
    
    CS_NAME_PREFIXES = ["YY_6/9_值班号", "Y_YY"]

    # [Ver 39.0] AI 配置
    AI_PROXY_URL = os.environ.get("AI_PROXY_URL", "https://geminiproxy-black-one.vercel.app")
    # GEMINI_API_KEY 已移除
    AI_MODEL_NAME = "gemini-3-flash-preview"

except Exception as e:
    logger.error(f"❌ 配置错误: {e}")
    sys.exit(1)

log_tree(0, f"系统启动 | 稍等词: {len(WAIT_SIGNATURES)} | 跟进词: {len(KEEP_SIGNATURES)} | 忽略词: {len(IGNORE_SIGNATURES)}")

# ==========================================
# 模块 3: 全局状态
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60
SELF_REPLY_TIMEOUT = 3 * 60 # [Ver 38.0] 自回检测 3分钟

MAX_CACHE_SIZE = 50000 

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
self_reply_tasks = {} # [Ver 38.0]

wait_timers = {}
followup_timers = {}
reply_timers = {}
self_reply_timers = {} # [Ver 38.0]

wait_msg_map = {}        
followup_msg_map = {} 
deleted_cache = deque(maxlen=10000)
self_reply_dedup = deque(maxlen=1000) # [Ver 39.4] 自回图集去重缓存

chat_user_active_msgs = {}
chat_thread_active_msgs = {}

msg_to_user_cache = {} 
msg_content_cache = {}
group_to_user_cache = {}

cs_activity_log = {}

IS_WORKING = False
MY_ID = None
bot_loop = None

def update_msg_cache(chat_id, msg_id, user_id, grouped_id=None):
    key = (chat_id, msg_id)
    if len(msg_to_user_cache) >= MAX_CACHE_SIZE:
        if key not in msg_to_user_cache: 
            try: msg_to_user_cache.pop(next(iter(msg_to_user_cache)))
            except StopIteration: pass
    msg_to_user_cache[key] = user_id
    if grouped_id:
        g_key = (chat_id, grouped_id)
        if len(group_to_user_cache) >= 5000:
             if g_key not in group_to_user_cache:
                 try: group_to_user_cache.pop(next(iter(group_to_user_cache)))
                 except StopIteration: pass
        group_to_user_cache[g_key] = user_id

def update_content_cache(chat_id, msg_id, name, text):
    key = (chat_id, msg_id)
    if len(msg_content_cache) >= MAX_CACHE_SIZE:
        if key not in msg_content_cache: 
            try: msg_content_cache.pop(next(iter(msg_content_cache)))
            except StopIteration: pass
    safe_text = text[:100].replace('\n', ' ') if text else "[非文本/空]"
    msg_content_cache[key] = {'name': name, 'text': safe_text}

# [Ver 41.0] 时间源优化：强制使用传入的 timestamp (来自消息 event.date)
def record_cs_activity(chat_id, user_id=None, thread_id=None, timestamp=None):
    if timestamp is None: 
        timestamp = time.time() # 兜底，理论上不应触发
    
    if user_id: 
        # 只记录最新的时间（防止乱序）
        # old = cs_activity_log.get((chat_id, user_id), 0)
        cs_activity_log[(chat_id, user_id)] = timestamp
        
    if thread_id: 
        cs_activity_log[(chat_id, thread_id)] = timestamp

def get_thread_context(event):
    if not event.message.reply_to: return None, None
    r = event.message.reply_to
    if r.reply_to_top_id: return r.reply_to_top_id, "Topic"
    if r.reply_to_msg_id: return r.reply_to_msg_id, "Reply"
    return None, None

async def is_official_cs(message):
    if not message: return False
    sender_id = message.sender_id
    if (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS): return True
    try:
        sender = await message.get_sender()
        if not sender: return False
        name = getattr(sender, 'first_name', '') or ''
        for prefix in CS_NAME_PREFIXES:
            if name.startswith(prefix): return True
    except: pass
    return False

# [Ver 43.3] Helper: Check history for WAIT keywords
async def check_wait_in_history(chat_id, thread_id=None, limit=30):
    try:
        # [Ver 43.3 Logic]
        # Use reply_to parameter to scan ONLY the specific message thread/topic.
        # This is precise: it fetches messages belonging to the conversation flow.
        
        # If thread_id is None, it means the message is in the main chat (no reply context),
        # so we scan the main chat history.
        # If thread_id is present, we scan that thread.
        
        kwargs = {'limit': limit}
        if thread_id:
             kwargs['reply_to'] = thread_id
             
        async for m in client.iter_messages(chat_id, **kwargs):
            if not m.text: continue
            
            # 1. Check if CS
            is_cs = False
            # Check ID
            if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs = True
            else:
                # Check Name Prefix
                try:
                    s = await m.get_sender()
                    if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)):
                        is_cs = True
                except: pass
            
            if is_cs:
                # 2. Check Keyword
                # Use normalize to match configured WAIT_KEYWORDS (which are normalized)
                text_norm = normalize(m.text)
                if any(k in text_norm for k in WAIT_SIGNATURES):
                    log_tree(2, f"✅ 自回判定: 历史流(Thread={thread_id})中找到稍等词: [{m.text[:10]}]")
                    return True # Found a WAIT instruction in this thread
                    
    except Exception as e:
        logger.error(f"History check failed: {e}")
        return False # Fail safe: don't alert if we can't check
    
    return False

async def maintenance_task():
    while True:
        try:
            await asyncio.sleep(600)
            now = time.time()
            # 这里的 expired_keys 可能会因为 now 是服务器时间而稍有偏差，但 3600s 的 buffer 足够大，不影响
            expired_keys = [k for k, v in cs_activity_log.items() if now - v > 3600]
            for k in expired_keys: del cs_activity_log[k]
        except Exception as e: logger.error(f"维护任务出错: {e}")

# ==========================================
# 模块 4: Web 控制台 (UI 修复版)
# ==========================================
app = Flask(__name__)

DASHBOARD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>监控看板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta http-equiv="refresh" content="5"> 
    <style>
        :root { --bg: #fff; --text: #333; --card: #f8f9fa; --border: #eee; --green: #28a745; --red: #dc3545; }
        body { background: var(--bg); color: var(--text); font-family: sans-serif; padding: 20px; max-width: 600px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #000; padding-bottom: 10px; margin-bottom: 20px; }
        h1 { margin: 0; font-size: 1.4rem; }
        .status-grp { display: flex; gap: 10px; align-items: center; }
        .tag { padding: 4px 10px; border-radius: 4px; color: #fff; font-weight: bold; font-size: 0.9rem; }
        .on { background: var(--green); } .off { background: var(--red); }
        .ctrl-btn { padding: 4px 8px; border: 1px solid #ccc; background: #eee; cursor: pointer; border-radius: 4px; font-size: 0.8rem; text-decoration: none; color: #333; }
        .ctrl-btn:hover { background: #ddd; }
        .audio-btn { cursor: pointer; font-size: 1.2rem; user-select: none; }
        .box { margin-bottom: 20px; }
        .title { font-weight: bold; border-left: 4px solid #333; padding-left: 8px; margin-bottom: 8px; color: #555; display: flex; justify-content: space-between; }
        .card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 10px; margin-bottom: 8px; display: flex; justify-content: space-between; align-items: center; }
        .t { font-family: monospace; font-weight: bold; font-size: 1.1rem; color: #d63384; }
        .late { color: red; text-decoration: underline; animation: flash 1s infinite; }
        .empty { color: #999; text-align: center; font-style: italic; padding: 10px; }
        .btn { display: block; width: 100%; padding: 12px; background: #222; color: #fff; text-align: center; text-decoration: none; border-radius: 6px; font-weight: bold; margin-top: 20px; }
        @keyframes flash { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡️ 实时监控</h1>
        <div class="status-grp">
            <span class="audio-btn" onclick="toggleAudio()" title="开启/关闭报警音">🔇</span>
            <a href="#" onclick="ctrl(1)" class="ctrl-btn">上班</a>
            <a href="#" onclick="ctrl(0)" class="ctrl-btn">下班</a>
            <div class="tag {{ 'on' if working else 'off' }}">{{ 'WORKING' if working else 'STOPPED' }}</div>
        </div>
    </div>
    {% for title, timers in [('⏳ 稍等 (12m)', w), ('🕵️ 跟进 (15m)', f), ('🔔 漏回 (5m)', r), ('🔄 自回 (3m)', s)] %}
    <div class="box">
        <div class="title"><span>{{ title }}</span><span>{{ timers|length }}</span></div>
        {% if timers %}
            {% for mid, info in timers.items() %}
            <div class="card">
                <div>
                    <b>{{ info.user }}</b>
                    {% if title == '🔔 漏回 (5m)' and info.target %}
                        <span style="font-size:0.85rem; color:#666"> ➔ {{ info.target }}</span>
                    {% endif %}
                    <br>
                    <a href="{{ info.url }}" target="_blank" style="font-size:0.8rem">🔗跳转</a>
                </div>
                <span class="t" data-end="{{ info.ts }}">--:--</span>
            </div>
            {% endfor %}
        {% else %}<div class="empty">无任务</div>{% endif %}
    </div>
    {% endfor %}
    <a href="/log" target="_blank" class="btn">🔍 打开交互式日志分析器</a>
    <a href="/tool/wait_check" target="_blank" class="btn" style="margin-top:10px;background:#00695c">🛠️ 稍等闭环检测工具</a>
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 43.4 (Fix Edit Logic & UI)</div>
    <script>
        let savedState = localStorage.getItem('tg_bot_audio_enabled');
        let audioEnabled = savedState === null ? true : (savedState === 'true');
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const audioBtn = document.querySelector('.audio-btn');
        if (audioBtn) { audioBtn.innerText = audioEnabled ? "🔊" : "🔇"; }
        function playAlarm() { if (!audioEnabled) return; if (audioCtx.state === 'suspended') audioCtx.resume().catch(e => console.log(e)); const oscillator = audioCtx.createOscillator(); const gainNode = audioCtx.createGain(); oscillator.type = 'square'; oscillator.frequency.setValueAtTime(800, audioCtx.currentTime); oscillator.frequency.exponentialRampToValueAtTime(400, audioCtx.currentTime + 0.1); gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime); gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.1); oscillator.connect(gainNode); gainNode.connect(audioCtx.destination); oscillator.start(); oscillator.stop(audioCtx.currentTime + 0.2); }
        function toggleAudio() { audioEnabled = !audioEnabled; localStorage.setItem('tg_bot_audio_enabled', audioEnabled); const btn = document.querySelector('.audio-btn'); btn.innerText = audioEnabled ? "🔊" : "🔇"; if(audioEnabled) { if (audioCtx.state === 'suspended') audioCtx.resume(); playAlarm(); } }
        function ctrl(s) { fetch('/api/ctrl?s=' + s + '&_t=' + new Date().getTime()).then(() => setTimeout(() => location.reload(), 500)); }
        setInterval(() => { const now = Date.now() / 1000; let hasLate = false; document.querySelectorAll('.t').forEach(el => { const diff = parseFloat(el.dataset.end) - now; if(diff <= 0) { el.innerText = "已超时"; el.classList.add('late'); hasLate = true; } else { const m = Math.floor(diff / 60); const s = Math.floor(diff % 60); el.innerText = `${m}:${s.toString().padStart(2, '0')}`; } }); if (hasLate && audioEnabled) playAlarm(); }, 1000);
    </script>
</body>
</html>
"""

LOG_VIEWER_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>系统日志流 | Log Viewer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root {
            --bg-body: #0f172a;
            --bg-panel: #1e293b;
            --bg-input: #334155;
            --text-main: #f1f5f9;
            --text-muted: #94a3b8;
            --primary: #3b82f6;
            --user-bubble: #334155;
            --cs-bubble: #0f766e;
            --alert-bg: rgba(239, 68, 68, 0.15);
            --alert-border: #ef4444;
            --audit-bg: rgba(245, 158, 11, 0.15);
            --audit-border: #f59e0b;
            --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
        }
        * { box-sizing: border-box; }
        body {
            background-color: var(--bg-body);
            color: var(--text-main);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            margin: 0;
            height: 100vh;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }
        /* Scrollbar */
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-body); }
        ::-webkit-scrollbar-thumb { background: var(--bg-input); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

        /* Toolbar */
        .toolbar {
            background: rgba(15, 23, 42, 0.85);
            backdrop-filter: blur(12px);
            padding: 16px 24px;
            border-bottom: 1px solid var(--bg-input);
            display: flex;
            gap: 12px;
            align-items: center;
            z-index: 10;
            box-shadow: var(--shadow);
        }
        input {
            flex-grow: 1;
            background: var(--bg-panel);
            border: 1px solid var(--bg-input);
            color: var(--text-main);
            padding: 10px 16px;
            border-radius: 8px;
            font-size: 14px;
            transition: all 0.2s;
        }
        input:focus {
            outline: none;
            border-color: var(--primary);
            box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.2);
        }
        button {
            background: var(--bg-panel);
            color: var(--text-main);
            border: 1px solid var(--bg-input);
            padding: 10px 20px;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            font-size: 14px;
            transition: all 0.2s;
            white-space: nowrap;
        }
        button:hover { background: var(--bg-input); transform: translateY(-1px); }
        
        /* Log Container */
        #log-container {
            flex-grow: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            scroll-behavior: smooth;
        }

        /* Message Rows */
        .msg-row {
            display: flex;
            flex-direction: column;
            max-width: 100%;
            animation: fadeIn 0.3s ease;
        }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }

        .msg-meta {
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 4px;
            font-family: "Menlo", "Consolas", monospace;
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 0 4px;
        }

        .bubble {
            padding: 12px 18px;
            border-radius: 16px;
            font-size: 14px;
            line-height: 1.6;
            position: relative;
            word-wrap: break-word;
            white-space: pre-wrap;
            box-shadow: var(--shadow);
            max-width: 85%;
        }

        /* User Message (Left) */
        .msg-user { align-items: flex-start; }
        .msg-user .bubble {
            background-color: var(--user-bubble);
            border-top-left-radius: 2px;
            color: #e2e8f0;
        }

        /* CS Message (Right) */
        .msg-cs { align-items: flex-end; }
        .msg-cs .bubble {
            background-color: var(--cs-bubble);
            border-top-right-radius: 2px;
            color: #f0fdfa;
        }
        .msg-cs .msg-meta { flex-direction: row-reverse; }

        /* System/Audit/Alert Messages */
        .msg-sys, .msg-alert, .msg-audit {
            align-items: center;
            width: 100%;
        }
        .msg-sys .bubble, .msg-alert .bubble, .msg-audit .bubble {
            max-width: 95%;
            background: transparent;
            box-shadow: none;
            padding: 8px 12px;
            border-radius: 6px;
            font-family: "Menlo", "Consolas", monospace;
            font-size: 12px;
            border-left: 3px solid;
        }

        .msg-sys .bubble {
            border-color: var(--text-muted);
            background: rgba(148, 163, 184, 0.05);
            color: var(--text-muted);
        }

        .msg-alert .bubble {
            border-color: var(--alert-border);
            background: var(--alert-bg);
            color: #fca5a5;
        }

        .msg-audit .bubble {
            border-color: var(--audit-border);
            background: var(--audit-bg);
            color: #fdba74;
        }

        /* Interactive Elements */
        .pill {
            display: inline-block;
            background: rgba(255, 255, 255, 0.1);
            padding: 2px 6px;
            border-radius: 4px;
            cursor: pointer;
            transition: background 0.2s;
            user-select: all;
        }
        .pill:hover { background: rgba(255, 255, 255, 0.2); color: #fff; }

        .highlight-row .bubble {
            box-shadow: 0 0 0 2px #fbbf24, 0 0 20px rgba(251, 191, 36, 0.2);
            z-index: 10;
        }

        .btn-report {
            font-size: 10px;
            padding: 2px 6px;
            border-radius: 4px;
            text-transform: uppercase;
            font-weight: bold;
            cursor: pointer;
            letter-spacing: 0.5px;
            border: 1px solid rgba(255,255,255,0.2);
        }
        .btn-missed { background: #f59e0b; color: black; }
        .btn-false { background: #ef4444; color: white; }

        .error-msg { text-align: center; padding: 40px; color: var(--text-muted); font-style: italic; }
    </style>
</head>
<body>
    <div class="toolbar">
        <input type="text" id="search" placeholder="🔍 输入 ID / 关键词 (回车跳转)..." onkeyup="if(event.key==='Enter') doSearch()">
        <button onclick="doSearch()">查找</button>
        <button onclick="window.location.reload()">🔄 刷新</button>
        <button onclick="scrollToBottom()">⬇️ 底部</button>
    </div>
    <div id="log-container">Loading logs...</div>
    <script>
        const container = document.getElementById('log-container');
        let parsedLogs = [];
        // [Ver 34.0] UI Fix: Handle date format
        fetch('/log_raw?t=' + Date.now())
            .then(r => { if (!r.ok) throw new Error('Network response was not ok'); return r.text(); })
            .then(text => {
                if (!text.trim()) { container.innerHTML = '<div class="error-msg">暂无日志数据</div>'; return; }
                try { parseLogs(text); renderLogs(); scrollToBottom(); } 
                catch (e) { console.error("Log parsing error:", e); container.innerHTML = `<div class="error-msg">日志解析错误: ${e.message}</div>`; }
            })
            .catch(err => { container.innerHTML = `<div class="error-msg">加载失败: ${err.message}</div>`; });

        function parseLogs(text) {
            // [Fix JS Escaping] Use double backslash for python string
            const rawLines = text.split(/\\r?\\n/);
            parsedLogs = [];
            let currentEntry = null;
            // [Fix JS Escaping] Escape \\d and \\s for JS regex
            const timeRegex = /^(\\d{4}-\\d{2}-\\d{2}\\s+)?(\\d{2}:\\d{2}:\\d{2})(.*)/;
            
            rawLines.forEach(line => {
                if(!line.trim()) return;
                const match = line.match(timeRegex);
                if (match) {
                    if (currentEntry) parsedLogs.push(currentEntry);
                    // match[2] is time, match[3] is content
                    currentEntry = { time: match[2], raw: match[3], content: match[3].trim(), fullText: match[3] };
                } else {
                    // [Fix JS Escaping] Use \\n for literal newline char in JS string
                    if (currentEntry) { currentEntry.fullText += '\\n' + line; currentEntry.content += '\\n' + line; }
                }
            });
            if (currentEntry) parsedLogs.push(currentEntry);
        }

        function renderLogs() {
            let html = '';
            parsedLogs.forEach((entry, idx) => {
                let type = 'sys';
                let content = entry.content;
                let raw = entry.raw || "";
                let ids = [];
                // [Fix JS Escaping] Escape \\d and \\s for JS regex
                const idRegex = /(Msg|User|Thread|流|归属|用户)[:=]?\\s?(\\d+)/g;
                let match;
                while ((match = idRegex.exec(content)) !== null) { ids.push(match[2]); }
                let idsStr = ids.join(',');

                if (raw.includes('📦')) { type = 'user'; content = content.replace('📦', '').trim(); }
                else if (raw.includes('客服操作') || (raw.includes('⚡️') && raw.includes('┣━━'))) { type = 'cs'; content = content.replace(/[┣┗]━━/, '').replace('⚡️', '⚡️ ').trim(); }
                else if (raw.includes('🚨') || raw.includes('[ALERT]')) { type = 'alert'; }
                else if (raw.includes('👮') || raw.includes('[AUDIT]')) { type = 'audit'; }
                else if (raw.includes('┣━━') || raw.includes('┗━━')) { type = 'sys'; }

                // [Fix JS Escaping] Escape \\d, \\s and quotes inside replacement string
                content = content.replace(/(Msg[:=]?\\s?)(\\d+)/g, '$1<span class="pill" onclick="searchId(\\'$2\\')">$2</span>');
                content = content.replace(/(User|用户|归属)[:=]?\\s?(\\d+)/g, '$1<span class="pill" onclick="searchId(\\'$2\\')">$2</span>');
                
                let actionBtn = '';
                // [Ver 43.4 Fix] Fixed string interpolation for reportBug by removing extra escaping
                if (type === 'user') {
                    actionBtn = ids.length > 0 ? `<span class="btn-report btn-missed" onclick="reportBug('漏报', '${idsStr}')">🐞 漏报</span>` : '';
                } else if (type === 'alert' || type === 'audit') {
                    actionBtn = ids.length > 0 ? `<span class="btn-report btn-false" onclick="reportBug('误报', '${idsStr}')">🐞 误报</span>` : '';
                }
                
                let metaHtml = `<div class="msg-meta">${entry.time} #${idx} ${actionBtn}</div>`;
                let rowClass = `msg-row msg-${type}`;
                
                if (type === 'user' || type === 'cs') {
                    html += `<div class="${rowClass}" id="log-${idx}">${type === 'cs' ? metaHtml : ''}<div class="bubble">${content}</div>${type === 'user' ? metaHtml : ''}</div>`;
                } else {
                    if (type === 'alert' || type === 'audit') {
                          html += `<div class="${rowClass}" id="log-${idx}"><div class="bubble">${actionBtn} <b>${content}</b></div></div>`;
                    } else {
                          html += `<div class="${rowClass}" id="log-${idx}"><div class="bubble">${entry.time} ${content}</div></div>`;
                    }
                }
            });
            container.innerHTML = html;
        }
        function searchId(id) { document.getElementById('search').value = id; doSearch(); }
        function doSearch() {
            const term = document.getElementById('search').value.toLowerCase();
            if (!term) return;
            document.querySelectorAll('.highlight-row').forEach(el => el.classList.remove('highlight-row'));
            let found = false;
            const rows = Array.from(document.querySelectorAll('.msg-row')).reverse();
            for (let row of rows) {
                if (row.innerText.toLowerCase().includes(term)) {
                    row.classList.add('highlight-row');
                    if (!found) { row.scrollIntoView({behavior: "smooth", block: "center"}); found = true; }
                }
            }
        }
        function reportBug(type, idsStr) {
            const ids = idsStr.split(',');
            if (ids.length === 0) return;
            let report = `=== ${type}反馈报告 ===\\n`;
            report += `类型: ${type}\\n涉及 ID: ${idsStr}\\n\\n-- 关键日志流 --\\n`;
            parsedLogs.forEach(entry => {
                let hit = false;
                for (let id of ids) { if (entry.raw.includes(id)) { hit = true; break; } }
                if (hit) { report += `[${entry.time}] ${entry.content}\\n`; }
            });
            navigator.clipboard.writeText(report).then(() => { alert(`✅ [${type}] 详情已复制！请直接粘贴发送。`); });
        }
        function scrollToBottom() { container.scrollTop = container.scrollHeight; }
    </script>
</body>
</html>
"""

WAIT_CHECK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>稍等关键词闭环检测工具</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, sans-serif; background: #f0f2f5; padding: 20px; max-width: 800px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        h1 { margin-top: 0; color: #1a1a1a; font-size: 1.5rem; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="text"] { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        button { background: #0088cc; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; transition: background 0.2s; }
        button:hover { background: #006699; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        
        #progress-container { margin-top: 20px; display: none; background: #f8f9fa; padding: 15px; border-radius: 6px; border: 1px solid #eee; }
        #progress-bar { width: 100%; height: 10px; background: #ddd; border-radius: 5px; overflow: hidden; margin-bottom: 8px; }
        #progress-fill { height: 100%; background: #4caf50; width: 0%; transition: width 0.3s; }
        #status-text { font-size: 14px; color: #666; text-align: center; }

        .result-list { margin-top: 20px; }
        .result-item { padding: 15px; border-bottom: 1px solid #eee; display: flex; align-items: flex-start; gap: 15px; background: #fff; transition: background 0.2s; }
        .result-item:hover { background: #fafafa; }
        .result-item:last-child { border-bottom: none; }
        
        .status-badge { padding: 6px 10px; border-radius: 6px; font-size: 13px; font-weight: bold; white-space: nowrap; display: flex; align-items: center; justify-content: center; min-width: 80px; }
        .status-closed { background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
        .status-open { background: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
        
        .msg-content { flex-grow: 1; min-width: 0; }
        .msg-meta { font-size: 12px; color: #888; margin-bottom: 4px; display: flex; gap: 10px; }
        .msg-text { font-size: 14px; line-height: 1.5; color: #333; word-wrap: break-word; background: #f5f5f5; padding: 8px; border-radius: 4px; margin: 5px 0; border-left: 3px solid #ccc; }
        .reason-text { color: #d32f2f; font-size: 13px; margin-top: 4px; font-style: italic; }
        .reason-success { color: #2e7d32; font-size: 13px; margin-top: 4px; font-style: italic; }
        .msg-link { text-decoration: none; color: #0088cc; font-size: 13px; display: inline-block; margin-top: 5px; font-weight: 500; }
        .msg-link:hover { text-decoration: underline; }
        
        .summary { font-weight: bold; margin-bottom: 20px; padding: 15px; background: #e3f2fd; border-radius: 6px; border: 1px solid #bbdefb; color: #0d47a1; display: none; }
        .filter-btn { cursor: pointer; color: #0056b3; text-decoration: underline; margin: 0 5px; }
        .filter-btn:hover { color: #003d80; }
        .filter-active { font-weight: 900; color: #d32f2f; text-decoration: none; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🔍 稍等关键词闭环检测</h1>
        <div class="form-group">
            <label>输入关键词 (例如: 请稍等ART)</label>
            <input type="text" id="keyword" placeholder="输入要搜索的关键词..." value="请稍等ART">
        </div>
        <button onclick="startCheck()" id="btn-search">开始检测 (过去 10 小时)</button>
        
        <div id="progress-container">
            <div id="progress-bar"><div id="progress-fill"></div></div>
            <div id="status-text">准备就绪...</div>
        </div>
    </div>

    <div class="card" id="result-card" style="display:none">
        <div class="summary" id="summary-box"></div>
        <div class="result-list" id="result-list"></div>
    </div>

    <script>
        let allResults = [];
        let currentFilter = 'all';

        async function startCheck() {
            const keyword = document.getElementById('keyword').value.trim();
            if (!keyword) return alert("请输入关键词");
            
            const btn = document.getElementById('btn-search');
            const pContainer = document.getElementById('progress-container');
            const pFill = document.getElementById('progress-fill');
            const pText = document.getElementById('status-text');
            const resCard = document.getElementById('result-card');
            const resList = document.getElementById('result-list');
            const summaryBox = document.getElementById('summary-box');

            btn.disabled = true;
            pContainer.style.display = 'block';
            resCard.style.display = 'block';
            resList.innerHTML = '';
            summaryBox.style.display = 'none';
            pFill.style.width = '1%';
            pText.innerText = "正在初始化...";
            
            allResults = [];

            try {
                // 使用流式 API
                const response = await fetch(`/api/wait_check_stream?keyword=${encodeURIComponent(keyword)}`);
                const reader = response.body.getReader();
                const decoder = new TextDecoder();

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    
                    const chunk = decoder.decode(value, {stream: true});
                    const lines = chunk.split('\\n');
                    
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const data = JSON.parse(line);
                            
                            if (data.type === 'progress') {
                                pFill.style.width = data.percent + '%';
                                pText.innerText = data.msg;
                            } else if (data.type === 'result') {
                                allResults.push(data);
                                // [Ver 43.0] Collect all first for sorting
                                pText.innerText = `已找到 ${allResults.length} 条结果...`;
                            } else if (data.type === 'done') {
                                pFill.style.width = '100%';
                                pText.innerText = '检测完成，正在排序...';
                                
                                // [Ver 43.0] Sort by time descending (Newest first)
                                allResults.sort((a, b) => {
                                    return new Date(b.time) - new Date(a.time);
                                });
                                
                                renderResults(allResults); // Batch render
                                renderSummary(data.total, data.closed, data.open);
                            }
                        } catch (e) {
                            console.error("Parse error", e);
                        }
                    }
                }
            } catch (e) {
                pText.innerText = "发生错误: " + e.message;
            } finally {
                btn.disabled = false;
            }
        }

        function renderSummary(total, closed, open) {
            const summaryBox = document.getElementById('summary-box');
            summaryBox.style.display = 'block';
            summaryBox.innerHTML = `
                检测完成: 共找到 ${total} 条消息。
                <span class="filter-btn" onclick="filterResults('closed')">✅ 已闭环: ${closed}</span>
                <span class="filter-btn" onclick="filterResults('open')">❌ 未闭环: ${open}</span>
                <span class="filter-btn" onclick="filterResults('all')">📝 显示全部</span>
            `;
        }

        function filterResults(type) {
            currentFilter = type;
            let filtered = [];
            if (type === 'all') filtered = allResults;
            else if (type === 'closed') filtered = allResults.filter(d => d.is_closed);
            else if (type === 'open') filtered = allResults.filter(d => !d.is_closed);
            
            // [Ver 43.0] Ensure sorted
            filtered.sort((a, b) => new Date(b.time) - new Date(a.time));
            
            renderResults(filtered);
            
            // Update active state visual
            document.querySelectorAll('.filter-btn').forEach(btn => {
                 if(btn.innerText.includes(type === 'all' ? '全部' : (type === 'closed' ? '已闭环' : '未闭环'))) {
                     btn.classList.add('filter-active');
                 } else {
                     btn.classList.remove('filter-active');
                 }
            });
        }
        
        // [Ver 43.0] New function to render a list of items
        function renderResults(list) {
            const resList = document.getElementById('result-list');
            resList.innerHTML = '';
            list.forEach(data => {
                const div = document.createElement('div');
                div.className = 'result-item';
                div.innerHTML = `
                    <div class="status-badge ${data.is_closed ? 'status-closed' : 'status-open'}">
                        ${data.is_closed ? '✅ 已闭环' : '❌ 未闭环'}
                    </div>
                    <div class="msg-content">
                        <div class="msg-meta">
                            <span>📅 ${data.time}</span>
                            <span>📂 ${data.group_name}</span>
                        </div>
                        <div class="msg-text">${data.found_text}</div>
                        ${data.reason ? `<div class="${data.is_closed ? 'reason-success' : 'reason-text'}">${data.is_closed ? '🤖 ' : '⚠️ '}${data.reason}</div>` : ''}
                        <a href="${data.link}" target="_blank" class="msg-link">🔗 跳转消息</a>
                    </div>
                `;
                resList.appendChild(div);
            });
        }
    </script>
</body>
</html>
"""

@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, s=self_reply_timers, current_time=now)

@app.route('/log')
def log_ui(): return render_template_string(LOG_VIEWER_HTML)

# [Ver 42.0] Fix 500 error by using raw Response instead of render_template_string for JS-heavy templates
@app.route('/tool/wait_check')
def wait_check_ui(): 
    return Response(WAIT_CHECK_HTML, mimetype='text/html')

@app.route('/log_raw')
def log_raw():
    try:
        # [Ver 38.1] Check file exists first
        if not os.path.exists(LOG_FILE_PATH):
            return "Log file not created yet.", 200
            
        file_size = os.path.getsize(LOG_FILE_PATH)
        read_size = 200 * 1024 
        # [Ver 43.0] Fix IndentationError from previous version
        with open(LOG_FILE_PATH, 'rb') as f:
            if file_size > read_size: f.seek(file_size - read_size)
            content = f.read().decode('utf-8', errors='ignore')
        return Response(content, mimetype='text/plain')
    except Exception as e: return f"Log read error: {e}"

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/api/ctrl')
def api_ctrl():
    s = request.args.get('s', type=int)
    log_tree(1, f"🌐 Web指令接收: {'上班' if s==1 else '下班'}")
    global bot_loop
    if not bot_loop: return "Error: Loop Not Ready", 500
    coro = perform_start_work() if s == 1 else perform_stop_work()
    try: asyncio.run_coroutine_threadsafe(coro, bot_loop)
    except Exception as e: return str(e), 500
    return "OK"

# ==========================================
# 模块 4.5: 稍等闭环检测 API (Async Generator)
# ==========================================
def _ai_check_reply_needed(text):
    """
    [Sync Function] 使用 Gemini AI 判断是否需要回复
    返回 (bool, reason_str)
    """
    log_prefix = f"🤖 [AI-Audit] Text='{text[:20]}...' | "
    
    # 1. 基础鉴权检查 (已移除，直接使用 Proxy URL)
    
    # 2. 构造请求
    # [Ver 40.0] URL 优化：移除末尾斜杠并直接请求，不带 API KEY 参数
    proxy_url = AI_PROXY_URL.rstrip('/')
    url = f"{proxy_url}/v1beta/models/{AI_MODEL_NAME}:generateContent"
    
    headers = {'Content-Type': 'application/json'}
    prompt = f"""
    判断客户的这条最后回复是否需要客服继续跟进回复。
    客户消息："{text}"
    
    规则：
    1. 如果包含明确的问题、投诉、未解决的诉求、需要确认的操作，返回 TRUE。
    2. 如果只是礼貌性的结束语（如“好的”、“谢谢”、“收到”、“明白了”、“ok”、“辛苦了”）、单纯的情绪表达（如“哈哈”）、或者表示话题已结束，返回 FALSE。
    3. 仅仅是“好的谢谢”这种组合，绝对是 FALSE。
    
    请输出 JSON 格式: {{"reason": "思考过程...", "need_reply": true/false}}
    """
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    # 3. 发送请求
    try:
        start_t = time.time()
        resp = requests.post(url, json=data, headers=headers, timeout=10)
        cost_t = time.time() - start_t
        
        if resp.status_code == 200:
            res_json = resp.json()
            try:
                # 解析 Gemini 的 JSON 响应
                candidates = res_json.get('candidates', [])
                if not candidates:
                    log_tree(9, log_prefix + "❌ AI返回空候选")
                    return (True, "AI返回空结果")
                
                raw_content = candidates[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                
                # 尝试解析 JSON
                ai_decision = json.loads(raw_content)
                reason = ai_decision.get("reason", "No reason")
                need_reply = ai_decision.get("need_reply", True)
                
                log_tree(2, log_prefix + f"✅ AI响应({cost_t:.2f}s): [{str(need_reply).upper()}] 理由: {reason}")
                return (need_reply, reason)
                
            except Exception as parse_e:
                log_tree(9, log_prefix + f"⚠️ 解析失败: {parse_e} | Raw: {raw_content}")
                # Fallback
                return (True, f"解析失败: {str(parse_e)}")
        else:
            log_tree(9, log_prefix + f"❌ 请求失败: {resp.status_code} {resp.text}")
            return (True, f"API请求失败: {resp.status_code}") 
    except Exception as e:
        log_tree(9, log_prefix + f"❌ 网络异常: {e}")
        return (True, f"网络异常: {str(e)}") 

# [Ver 41.8] 抽取公共闭环判断逻辑
async def _check_is_closed_logic(latest_msg):
    is_closed = False
    reason = ""
    
    # 检查最新消息的发送者
    last_sender_id = latest_msg.sender_id
    last_sender_is_cs = False
    if last_sender_id in ([MY_ID] + OTHER_CS_IDS):
         last_sender_is_cs = True
    else:
         try:
             s = await latest_msg.get_sender()
             if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)):
                 last_sender_is_cs = True
         except: pass
    
    if not last_sender_is_cs:
        # 最后是客户发言 -> AI 检测
        if not latest_msg.text or not latest_msg.text.strip():
            is_closed = False
            reason = "最后是客户[媒体/贴纸]"
        else:
            # 使用同步执行器调用 AI
            need_reply, ai_reason = await asyncio.get_event_loop().run_in_executor(
                None, lambda: _ai_check_reply_needed(latest_msg.text)
            )
            if not need_reply:
                is_closed = True
                reason = f"AI判定已闭环：{ai_reason}"
            else:
                is_closed = False
                reason = f"AI判定需回复：{ai_reason}"
    else:
        # 最后是客服发言 -> 检查内容是否仍包含等待词/跟进词
        # [Ver 41.8] 统一逻辑：Wait和Keep都使用“包含匹配”(in)，解决Keep词因标点或连词导致的漏判
        last_text_norm = normalize(latest_msg.text or "")
        is_wait = any(k in last_text_norm for k in WAIT_SIGNATURES)
        is_keep = any(k in last_text_norm for k in KEEP_SIGNATURES)
        
        if is_wait or is_keep:
            is_closed = False
            reason = f"客服最后仍回复{'稍等' if is_wait else '跟进'}词"
            # 检查是否因客户删消息导致
            if latest_msg.reply_to:
                try:
                    replied_obj = await latest_msg.get_reply_message()
                    if not replied_obj: reason += "(客户删消息)"
                except: pass
        else:
            is_closed = True
            
    return is_closed, reason

async def check_wait_keyword_logic(keyword, result_queue):
    """
    搜索过去10小时内包含 `keyword` 的消息，并检查闭环。
    将结果推送到 result_queue。
    """
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=10)
        total_groups = len(CS_GROUP_IDS)
        
        # 定义不参与检查的黑名单群组
        EXCLUDED_GROUPS = [-1002807120955, -1002169616907]
        
        found_count = 0
        closed_count = 0

        for idx, chat_id in enumerate(CS_GROUP_IDS):
            if chat_id in EXCLUDED_GROUPS: continue
            
            # 推送进度
            percent = int((idx / total_groups) * 100)
            result_queue.put(json.dumps({
                "type": "progress", "percent": percent, 
                "msg": f"正在扫描群组 {chat_id} ({idx+1}/{total_groups})..."
            }))

            try:
                # 1. 抓取该群组最近10小时的消息
                history = []
                # 限制3000条或时间截止
                async for m in client.iter_messages(chat_id, limit=3000):
                    if m.date and m.date < cutoff_time: break
                    history.append(m)
                
                # 2. 建立 Thread 状态表
                thread_latest_msg = {}
                for m in history:
                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id 
                        if not t_id: t_id = m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    if t_id not in thread_latest_msg:
                        thread_latest_msg[t_id] = m

                # 3. 在历史中查找包含 keyword 的消息
                for m in history:
                    if not m.text: continue
                    if keyword in m.text: # 只要包含关键词
                        found_count += 1
                        
                        t_id = None
                        if m.reply_to:
                            t_id = m.reply_to.reply_to_top_id 
                            if not t_id: t_id = m.reply_to.reply_to_msg_id
                        if not t_id: t_id = m.id
                        
                        latest_msg = thread_latest_msg.get(t_id, m)
                        
                        # [Ver 42.0] 调用统一闭环判断逻辑 (Consistency Fix)
                        is_closed, reason = await _check_is_closed_logic(latest_msg)
                        
                        if is_closed: closed_count += 1

                        # 构建结果并推送
                        group_name = str(chat_id)
                        try:
                            g = await client.get_entity(chat_id)
                            group_name = g.title
                        except: pass
                        
                        safe_text = (m.text or "")[:100].replace('\n', ' ')
                        beijing_time = m.date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                        
                        # [Link Fix] 链接逻辑优化
                        target_msg_for_link = latest_msg if not is_closed else m
                        
                        link = ""
                        real_chat_id = str(chat_id).replace('-100', '')
                        url_thread_id = None
                        
                        if "(客户删消息)" not in reason:
                            if target_msg_for_link.reply_to:
                                url_thread_id = target_msg_for_link.reply_to.reply_to_top_id
                                if not url_thread_id:
                                    url_thread_id = target_msg_for_link.reply_to.reply_to_msg_id
                        
                        if url_thread_id:
                             link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}?thread={url_thread_id}"
                        else:
                             link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}"

                        result_queue.put(json.dumps({
                            "type": "result",
                            "is_closed": is_closed,
                            "reason": reason,
                            "time": beijing_time,
                            "group_name": group_name,
                            "found_text": safe_text,
                            "link": link
                        }))

            except Exception as e:
                logger.error(f"Group {chat_id} check failed: {e}")

        result_queue.put(json.dumps({
            "type": "done", 
            "total": found_count, 
            "closed": closed_count, 
            "open": found_count - closed_count
        }))
        result_queue.put(None) # Sentinel

    except Exception as e:
        logger.error(f"Check Task Logic Error: {e}")
        result_queue.put(None)

# [Ver 42.8 Fix] Restore missing route
@app.route('/api/wait_check_stream')
def wait_check_stream():
    """
    [Added to fix 404] 流式 API 路由，对接 Telethon 逻辑
    """
    keyword = request.args.get('keyword', '').strip()
    if not keyword: return "Keyword required", 400
    
    def generate():
        result_queue = queue.Queue()
        # 将异步任务提交到全局 Bot Loop 中执行，并把结果放入 queue
        if not bot_loop: 
             yield "Error: Bot loop not ready\n"
             return

        asyncio.run_coroutine_threadsafe(
            check_wait_keyword_logic(keyword, result_queue), 
            bot_loop
        )
        
        while True:
            # 阻塞等待结果
            data = result_queue.get()
            if data is None: break
            yield data + "\n"
            
    return Response(stream_with_context(generate()), mimetype='text/plain')

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ==========================================
# 模块 5: 通知与网络
# ==========================================
def _post_request(url, payload):
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code != 200: log_tree(9, f"API推送失败: {resp.status_code}")
    except Exception as e: log_tree(9, f"网络异常: {e}")

async def send_alert(text, link, extra_log=""):
    if not BOT_TOKEN: return
    summary = text.splitlines()[1] if len(text.splitlines()) > 1 else '通知'
    log_tree(3, f"{extra_log} [ALERT] 发送报警 -> 全文:\n{text}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    tasks = []
    for chat_id in ALERT_GROUP_IDS:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
        tasks.append(loop.run_in_executor(None, lambda p=payload: _post_request(url, p)))
    if tasks: await asyncio.gather(*tasks)

async def check_msg_exists(channel_id, msg_id):
    try:
        msg = await client.get_messages(channel_id, ids=msg_id)
        if not msg: 
            log_tree(2, f"❌ 检查发现消息 {msg_id} 已物理删除")
            return False 
        return True
    except Exception as e:
        log_tree(2, f"⚠️ 网络检测失败 ({e}) -> 强制防漏报")
        return True 

# ==========================================
# 模块 6: 任务管理与核心逻辑
# ==========================================
# [Ver 42.2] 增强巡检: 修复大小写匹配问题 + 详细统计报告
async def audit_pending_tasks():
    log_tree(4, "开始执行【下班巡检】(关键词分批扫描)...")
    await send_alert("👮 **开始执行下班自动巡检...**\n正在分批扫描专属关键词...", "")
    
    # 1. 确定要扫描的关键词列表
    # [Ver 41.9] 只扫描 WAIT_KEYWORDS，不扫描 KEEP，防止误报他人任务
    all_keywords = sorted(list(WAIT_SIGNATURES))
    # 去重
    all_keywords = sorted(list(set(all_keywords)), key=lambda x: (len(x), x), reverse=True) 
    
    # 2. 预读取历史消息 (优化: 每个群只读一次，存入缓存)
    # Map: chat_id -> list of messages (past 10 hours)
    history_cache = {}
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=10) # 统一为10小时
    
    EXCLUDED_GROUPS = [-1002807120955, -1002169616907]

    log_tree(4, "正在预读取消息历史 (最近10小时)...")
    for chat_id in CS_GROUP_IDS:
        if chat_id in EXCLUDED_GROUPS: continue
        try:
            msgs = []
            # 限制3000条或时间截止
            async for m in client.iter_messages(chat_id, limit=3000):
                if m.date and m.date < cutoff_time: break
                msgs.append(m)
            history_cache[chat_id] = msgs
        except Exception as e:
            logger.error(f"Group {chat_id} fetch failed: {e}")

    total_issues = 0

    # 3. 分批扫描关键词
    for keyword in all_keywords:
        if not keyword.strip(): continue
        
        kw_issues = []
        found_count = 0
        closed_count = 0
        
        for chat_id, history in history_cache.items():
            # 建立线程上下文 (最新消息)
            thread_latest_msg = {}
            for m in history:
                t_id = None
                if m.reply_to:
                    t_id = m.reply_to.reply_to_top_id 
                    if not t_id: t_id = m.reply_to.reply_to_msg_id
                if not t_id: t_id = m.id
                if t_id not in thread_latest_msg:
                    thread_latest_msg[t_id] = m
            
            # 在历史中查找命中该 keyword 的消息
            for m in history:
                if not m.text: continue
                
                # [Ver 42.2 Fix] 必须 normalize 后再匹配，因为 keyword 已经是小写了
                # 修复: "请稍等art" in "请稍等ART" (False) 的问题
                if keyword in normalize(m.text):
                    # 检查发送者是否为客服 (ID匹配 或 名字前缀匹配)
                    is_cs_sender = False
                    if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs_sender = True
                    else:
                        sender = await m.get_sender()
                        name = getattr(sender, 'first_name', '') or ''
                        if name.startswith(tuple(CS_NAME_PREFIXES)): is_cs_sender = True
                    
                    # 只有客服发的关键词才算任务起点
                    if not is_cs_sender: continue

                    found_count += 1

                    # 找到该消息所属 Thread 的最新消息
                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    
                    latest_msg = thread_latest_msg.get(t_id, m)
                    
                    # [Ver 41.8] 调用统一闭环判断逻辑
                    is_closed, reason = await _check_is_closed_logic(latest_msg)
                    
                    if is_closed:
                        closed_count += 1
                    else:
                        # 发现未闭环问题
                        # 获取客服名字
                        cs_name_display = "未知客服"
                        try:
                            s = await m.get_sender()
                            if s: cs_name_display = getattr(s, 'first_name', 'Unknown')
                        except: pass

                        # 获取客户问题内容 (Reply To)
                        customer_text = "[无法获取原问题]"
                        if m.reply_to:
                            try:
                                r_msg = await m.get_reply_message()
                                if r_msg: customer_text = (r_msg.text or "[媒体]")[:20] + "..."
                            except: pass

                        link = ""
                        real_chat_id = str(chat_id).replace('-100', '')
                        url_thread_id = None
                        if "(客户删消息)" not in reason:
                             if latest_msg.reply_to:
                                 url_thread_id = latest_msg.reply_to.reply_to_top_id or latest_msg.reply_to.reply_to_msg_id
                        
                        if url_thread_id:
                             link = f"https://t.me/c/{real_chat_id}/{latest_msg.id}?thread={url_thread_id}"
                        else:
                             link = f"https://t.me/c/{real_chat_id}/{latest_msg.id}"
                        
                        # CS 回复的关键词消息内容
                        cs_reply_text = (latest_msg.text or "[媒体]")[:15]

                        kw_issues.append({
                            'cs_name': cs_name_display,
                            'customer_text': customer_text,
                            'cs_reply': cs_reply_text,
                            'reason': reason,
                            'link': link
                        })

        # [Ver 42.2] 只有当发现问题时才发送报警，并带上统计信息
        if kw_issues:
            total_issues += len(kw_issues)
            open_count = found_count - closed_count
            report_text = (
                f"👮 **下班巡检报告**\n"
                f"🔑 关键词: `{keyword}`\n"
                f"📊 命中: {found_count} | ✅ 闭环: {closed_count} | ❌ 未闭环: {open_count}\n\n"
            )
            
            for i, iss in enumerate(kw_issues[:8]): # 限制单条消息长度
                report_text += (
                    f"{i+1}. 👤 {iss['cs_name']}\n"
                    f"   💬 客户: {iss['customer_text']}\n"
                    f"   👉 结果: {iss['cs_reply']} ({iss['reason']})\n"
                    f"   🔗 [点击跳转]({iss['link']})\n\n"
                )
            
            if len(kw_issues) > 8:
                report_text += f"... (还有 {len(kw_issues)-8} 条未显示)"
            
            await send_alert(report_text, "", f"Audit-{keyword}")
            await asyncio.sleep(2) # 避免刷屏过快
        else:
            # 可选：如果该关键词完全干净，只在后台记录日志
            log_tree(4, f"关键词 '{keyword}' 巡检完成，无异常 (总数: {found_count})")

    await send_alert(f"🏁 **下班巡检结束**\n总计发现 **{total_issues}** 个未闭环问题。", "")

async def perform_stop_work():
    global IS_WORKING
    if IS_WORKING:
        await audit_pending_tasks()
    IS_WORKING = False
    for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()) + list(self_reply_tasks.values()): t.cancel()
    wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear(); self_reply_tasks.clear()
    wait_timers.clear(); followup_timers.clear(); reply_timers.clear(); self_reply_timers.clear()
    wait_msg_map.clear(); followup_msg_map.clear()
    chat_user_active_msgs.clear()
    chat_thread_active_msgs.clear()
    msg_to_user_cache.clear()
    msg_content_cache.clear()
    group_to_user_cache.clear()
    cs_activity_log.clear()
    await send_alert("🔴 **已切换为：下班模式 (网页/指令)**", "")

async def perform_start_work():
    global IS_WORKING
    IS_WORKING = True
    await send_alert(f"🟢 **已切换为：工作模式 (网页/指令)**", "")

def register_task(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key not in chat_user_active_msgs: chat_user_active_msgs[u_key] = set()
        chat_user_active_msgs[u_key].add(msg_id)
        update_msg_cache(chat_id, msg_id, user_id)
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key not in chat_thread_active_msgs: chat_thread_active_msgs[t_key] = set()
        chat_thread_active_msgs[t_key].add(msg_id)

def remove_task_record(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            chat_user_active_msgs[u_key].discard(msg_id)
            if not chat_user_active_msgs[u_key]: del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            chat_thread_active_msgs[t_key].discard(msg_id)
            if not chat_thread_active_msgs[t_key]: del chat_thread_active_msgs[t_key]

# [Ver 42.5] 修复：外科手术式精准销单，禁止连坐
def cancel_tasks(chat_id, user_id, thread_id=None, target_msg_id=None, reason="未知", types=None):
    if types is None: types = ['wait', 'followup', 'reply', 'self_reply'] # Default to all
    
    targets = set()
    hit_specific = False
    
    # 1. 精确打击：如果提供了 target_msg_id (即 Reply To ID)
    if target_msg_id:
        # [Ver 42.5 Fix] 无论在不在任务列表里，只要是有针对性的回复，就只针对这一个ID
        # 尝试去找到这个ID对应的任务 (可能在wait_msg_map映射里)
        real_task_id = target_msg_id
        
        # 检查反向映射表 (reply_id -> trigger_id)
        # 有时候 reply_to_msg_id 是触发消息，有时候是客服回复的那条消息
        # 我们需要找到“任务ID”，通常是触发消息的ID
        
        # 尝试查找直接任务
        if real_task_id in wait_tasks or real_task_id in followup_tasks or real_task_id in reply_tasks or real_task_id in self_reply_tasks:
            targets.add(real_task_id)
            hit_specific = True
        
        # [Ver 42.5 Fix] 尝试查找关联任务 (如果 target_msg_id 是客服发的上一条跟进，那任务ID可能是它的上一条)
        # 这里逻辑较复杂，简化为：如果提供了target，绝对不进行兜底删除。
        
        # 即使 targets 为空 (说明该消息没任务)，我们也标记 hit_specific = True
        # 意思是：“用户的意图是回复这条消息，如果这条消息没倒计时，那就算了，别动其他的”
        hit_specific = True
    
    # 2. 话题范围 (仅当没有精确命中时)
    if not hit_specific and thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])

    # 3. 用户范围 (仅当既没精确命中，也没在话题里，才执行全用户销单)
    if not hit_specific and not thread_id and user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            targets.update(chat_user_active_msgs[u_key])

    if not targets: return

    log_tree(1, f" ┣━━ 尝试销单 | 用户: {user_id} | 目标: {target_msg_id} | 命中: {hit_specific} | 任务池: {list(targets)}")
    count = 0
    cleared_ids = []
    for mid in targets:
        if 'wait' in types and mid in wait_tasks: wait_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'followup' in types and mid in followup_tasks: followup_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'reply' in types and mid in reply_tasks: reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'self_reply' in types and mid in self_reply_tasks: self_reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
    
    if count > 0:
        log_tree(2, f"销单成功 | {reason} | 流: {thread_id} | 任务: {cleared_ids}")

# [Ver 41.0] 时间源优化：这里的时间全是基于 Message Timestamp
def check_recent_activity_safe(chat_id, task_start_time, user_ids=None, thread_id=None):
    buffer_seconds = 10
    if user_ids:
        for uid in user_ids:
            last_act = cs_activity_log.get((chat_id, uid), 0)
            if last_act > task_start_time + buffer_seconds:
                return True, f"用户 {uid} 下有新回复"
    if thread_id:
        last_act = cs_activity_log.get((chat_id, thread_id), 0)
        if last_act > task_start_time + buffer_seconds:
            return True, f"消息流 {thread_id} 下有新回复"
    return False, None

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
# [Ver 41.0] 所有倒计时任务增加 trigger_timestamp 参数
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])
        
        log_tree(1, f"启动 [稍等] 倒计时 (12m) {ids_str} | Thread={thread_id}")
        
        # 这里的 end_time 用于 Web 展示，使用消息时间计算更准确
        end_time = trigger_timestamp + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        # 物理睡眠时间仍然需要等待这么久，这是服务器的职责
        await asyncio.sleep(WAIT_TIMEOUT)
        
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        # 醒来后，使用触发时的消息时间戳去对比活动日志
        is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [稍等] {ids_str} | 原因: {safe_reason} (客服已处理)")
            return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n🔗 [点击处理]({link})", link, ids_str)

        # [Ver 31.0] 严重超时监控
        CRITICAL_TIMEOUT = 10 * 60
        await asyncio.sleep(CRITICAL_TIMEOUT)
        
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe_2, safe_reason_2 = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe_2:
             log_tree(2, f"🛡️ 拦截严重误报 [稍等] {ids_str} | 原因: {safe_reason_2}")
             return

        log_tree(3, f"🔥 触发 [稍等] 严重超时 Msg={key_id}")
        await send_alert(
            f"🔥 **严重超时警报 (已超{int((WAIT_TIMEOUT+CRITICAL_TIMEOUT)/60)}分钟)**\n"
            f"👤 客服: {agent_name}\n"
            f"⚠️ 状态: 第一次报警后10分钟仍未回复！\n"
            f"❌ **即将执行扣分处理，请立即回复！**\n"
            f"📩 原消息: `{original_text.replace('`', '')}`\n"
            f"🔗 [点击处理]({link})",
            link, ids_str
        )

    except asyncio.CancelledError: pass 
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])

        log_tree(1, f"启动 [跟进] 倒计时 (15m) {ids_str} | Thread={thread_id}")
        end_time = trigger_timestamp + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [跟进] {ids_str} | 原因: {safe_reason}")
            return

        log_tree(2, f"触发 [跟进] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: **反馈核实内容超时未跟进回复** ({FOLLOWUP_TIMEOUT // 60} 分钟)\n🔗 [点击处理]({link})", link, ids_str)
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, trigger_timestamp, thread_id=None):
    try:
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [漏回] 监控 (5m) {ids_str} | Target={target_name} | Thread={thread_id}")
        
        end_time = trigger_timestamp + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link, 'target': target_name}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [漏回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **漏回消息提醒**\n👤 用户: {sender_name} 回复了客服 {target_name}\n⚠️ 状态: 已 {REPLY_TIMEOUT // 60} 分钟未回复\n🔗 [点击回复]({link})", link, ids_str)
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
        remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

# [Ver 39.3] 自回检测任务 - 竞态条件修复
async def task_self_reply_timeout(trigger_msg_id, user_name, content, link, chat_id, user_id, trigger_timestamp, thread_id=None):
    try:
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [自回] 监控 (3m) {ids_str} | Thread={thread_id}")
        
        end_time = trigger_timestamp + SELF_REPLY_TIMEOUT
        self_reply_timers[trigger_msg_id] = {'ts': end_time, 'user': user_name, 'url': link}
        
        # [Fix Ver 39.3] register_task 已在 handler 中同步执行，这里不再重复调用，避免状态冲突。
        
        await asyncio.sleep(SELF_REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        # [Ver 39.1] 报警前二次检查：如果期间客服活跃过，则视为已处理
        # [Ver 42.6] Removed CS Active Exemption due to user feedback - STRICT MODE
        # is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, [user_id], thread_id)
        # if is_safe:
        #     log_tree(2, f"🛡️ 拦截误报 [自回] {ids_str} | 原因: {safe_reason}")
        #     return

        log_tree(2, f"触发 [自回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **自回防漏监测**\n👤 用户: {user_name} (自行追加消息)\n⚠️ 状态: 已 {SELF_REPLY_TIMEOUT // 60} 分钟未处理\n🔗 [点击回复]({link})", link, ids_str)
    except asyncio.CancelledError: pass 
    # 资源清理工作由 handler 中的 done_callback 负责，这里无需 finally

# ==========================================
# 模块 8: 客户端与逻辑增强
# ==========================================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="Mac mini M2", 
    app_version="5.8.3 arm64 Mac App Store",      
    system_version="macOS 15.6.1",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    cmd = event.text.strip()
    log_tree(0, f"收到指令: {cmd}")
    if cmd == '下班':
        await perform_stop_work()
    elif cmd == '上班':
        await perform_start_work()
    elif cmd == '状态':
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}\n🔄 自回: {len(self_reply_tasks)}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.append(msg_id)
        
        deleted_info = {'name': '未知', 'text': '未知'}
        if event.chat_id:
             deleted_info = msg_content_cache.get((event.chat_id, msg_id), deleted_info)

        sender_info_str = f"发送者: {deleted_info['name']} | 内容: [{deleted_info['text']}]"

        if msg_id in wait_tasks: 
            wait_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务")

        if msg_id in wait_msg_map:
            target_id = wait_msg_map[msg_id]
            if target_id in wait_tasks:
                wait_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务(Target={target_id})")
            del wait_msg_map[msg_id]

        if msg_id in followup_tasks: 
            followup_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务")

        if msg_id in followup_msg_map:
            target_id = followup_msg_map[msg_id]
            if target_id in followup_tasks:
                followup_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务(Target={target_id})")
            del followup_msg_map[msg_id]

        if msg_id in reply_tasks: 
            reply_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测 Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [漏回] 监控")
            
        # [Ver 38.0]
        if msg_id in self_reply_tasks:
            self_reply_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测 Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [自回] 监控")

async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    if (chat_id, reply_to_msg_id) in msg_to_user_cache:
        return msg_to_user_cache[(chat_id, reply_to_msg_id)]

    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
        
        # [Ver 28.3] 深度捕获
        sender_id = target_msg.sender_id
        
        # 如果获取到了 ID，立即缓存 (包含 GroupedID 用于关联)
        if sender_id:
            cs_ids = [MY_ID] + OTHER_CS_IDS
            if sender_id not in cs_ids:
                update_msg_cache(chat_id, reply_to_msg_id, sender_id, target_msg.grouped_id)
                log_tree(1, f" ┣━━ 🧠 学习新知识: Msg({reply_to_msg_id}) 属于 User({sender_id})")
            return sender_id
            
        return None
    except Exception: return None

async def get_context_users(chat_id, msg_id):
    users = set()
    try:
        msgs = await client.get_messages(chat_id, ids=[msg_id])
        if not msgs or not msgs[0]: return []
        msg = msgs[0]
        
        if msg.sender_id: 
            users.add(msg.sender_id)
            if msg.sender_id not in ([MY_ID] + OTHER_CS_IDS):
                update_msg_cache(chat_id, msg_id, msg.sender_id, msg.grouped_id)
        
        if msg.reply_to_msg_id:
            parent_user_id = await get_traceable_sender(chat_id, msg.reply_to_msg_id)
            if parent_user_id:
                users.add(parent_user_id)
                log_tree(1, f" ┣━━ 🔗 三角关联探测: Msg({msg_id}) -> ParentUser({parent_user_id})")
                
    except Exception as e:
        log_tree(9, f"上下文获取失败: {e}")
        
    cs_ids = [MY_ID] + OTHER_CS_IDS
    return [u for u in users if u not in cs_ids]

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    try:
        global MY_ID
        if not MY_ID: MY_ID = (await client.get_me()).id
        if not IS_WORKING: return

        # [Ver 41.0] 获取消息的确切物理时间戳（UTC -> Timestamp）
        msg_timestamp = event.date.timestamp()
        # 日志格式优化，显示消息时间 T=...
        msg_time_str = event.date.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')

        text = event.text or ""
        msg_type = "文本"
        if event.message.file:
            msg_type = "文件/图片"
            if not text: text = "[媒体文件]"
        if event.message.sticker:
            msg_type = "贴纸"
            if not text: text = "[贴纸]"

        sender_id = event.sender_id
        reply_to_msg_id = event.reply_to_msg_id
        grouped_id = event.message.grouped_id
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', 'Unknown')
        chat_id = event.chat_id
        msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}"

        update_content_cache(chat_id, event.id, sender_name, text)

        norm_text = normalize(text)
        is_wait_cmd = any(k in norm_text for k in WAIT_SIGNATURES)
        # [Ver 42.3] 统一 Keep 判定逻辑为包含匹配，与 Audit 保持一致，防止"处理中..."被精确匹配漏掉
        is_keep_cmd = any(k in norm_text for k in KEEP_SIGNATURES)
        
        # [Ver 39.2] 增强客服身份识别: ID匹配 或 名字前缀匹配
        is_name_cs = False
        if sender_name:
             for prefix in CS_NAME_PREFIXES:
                 if sender_name.startswith(prefix): is_name_cs = True; break
        
        is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS) or is_name_cs

        current_thread_id, thread_type = get_thread_context(event)

        real_customer_id = None
        if reply_to_msg_id:
            if (chat_id, reply_to_msg_id) in msg_to_user_cache:
                real_customer_id = msg_to_user_cache[(chat_id, reply_to_msg_id)]
            
            if not real_customer_id and reply_to_msg_id in wait_msg_map:
                wait_origin_msg = wait_msg_map[reply_to_msg_id]
                for (cid, uid), msg_set in chat_user_active_msgs.items():
                    if cid == chat_id and wait_origin_msg in msg_set:
                        real_customer_id = uid
                        break
            
            if not real_customer_id:
                real_customer_id = await get_traceable_sender(chat_id, reply_to_msg_id)

        # [Ver 28.3] 组关联增强
        if not real_customer_id and reply_to_msg_id:
             pass 

        if is_sender_cs:
            # [Ver 41.0] 传入 msg_timestamp
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id, timestamp=msg_timestamp)
            
            # [Ver 43.4] Edit Re-trigger Fix & Logic Update
            if isinstance(event, events.MessageEdited):
                 # Only cancel if editing to "Done" status
                 if real_customer_id or current_thread_id:
                     cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服编辑: [{text[:100]}...]")
                 
                 # [Ver 43.4] 防止历史消息编辑触发任务 (Anti-Glitch)
                 # 如果编辑的不是最新一条消息，说明是修正历史记录，不应重新开启任务
                 try:
                     last_msgs = await client.get_messages(chat_id, limit=1)
                     if last_msgs and last_msgs[0].id != event.id:
                         log_tree(1, f"🛡️ 编辑忽略 | Msg={event.id} 非最新 (Top={last_msgs[0].id}) -> 终止触发")
                         return
                 except: pass

            if reply_to_msg_id:
                source_info = "未知"
                if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
                elif real_customer_id: source_info = "API实时查询"
                else: source_info = "追踪失败" # [Ver 28.3] 明确失败状态
                
                # [Ver 41.2] 日志增加 [T=...] 真实时间显示，证明逻辑使用的是 Telegram 时间而非服务器时间
                log_tree(1, f"⚡️ 客服操作捕获 | Msg: {reply_to_msg_id} [T={msg_time_str}] | 客服: {sender_name} | 内容: [{text[:100]}] | 归属: {real_customer_id} | 流: {current_thread_id} | 状态: {source_info}")

            # [Ver 42.4] 智能销单逻辑升级：精确打击
            # 如果客服回复的是某条特定消息，只取消该消息的任务。
            # 如果没有特定回复（如在话题中发言），才按话题取消。
            # 仅当完全没有上下文时，才兜底取消用户所有任务。
            
            cancel_types = None # Default: Cancel All types
            if is_wait_cmd or is_keep_cmd:
                cancel_types = ['reply', 'self_reply']

            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, 
                             thread_id=current_thread_id, 
                             target_msg_id=reply_to_msg_id, # [Ver 42.4] Pass exact target
                             reason=f"客服回复: [{text[:100]}...]", 
                             types=cancel_types)
            
            if reply_to_msg_id and reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]
            
            # [Ver 38.0] 客服回复了，也要消除自回任务
            if reply_to_msg_id and reply_to_msg_id in self_reply_tasks:
                self_reply_tasks[reply_to_msg_id].cancel()
                del self_reply_tasks[reply_to_msg_id]

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id:
                    related_users = [real_customer_id]

                if related_users:
                    # [Ver 41.6] 逻辑回调：按关键词触发任务
                    if is_keep_cmd:
                        # [Fix] 前置检查：只有历史中有明确的 WAIT 关键词（属于我的任务），才开启跟进监控
                        # 这避免了监控其他客服的“处理中”消息
                        should_monitor_keep = await check_wait_in_history(chat_id, current_thread_id)
                        
                        if not should_monitor_keep:
                             log_tree(1, f"🛡️ 豁免 [跟进] | Msg={event.id} | 原因: 历史流无本号[稍等]关键词")
                        else:
                            # [Fix Ver 36.1] 强制冲突检测
                            if reply_to_msg_id in wait_tasks:
                                wait_tasks[reply_to_msg_id].cancel()
                                del wait_tasks[reply_to_msg_id]
                                if reply_to_msg_id in wait_timers: del wait_timers[reply_to_msg_id]
                                log_tree(1, f"🔄 [跟进] 覆盖并销毁 [稍等] | Msg={reply_to_msg_id}")

                            if reply_to_msg_id in followup_tasks:
                                followup_tasks[reply_to_msg_id].cancel()
                                del followup_tasks[reply_to_msg_id]

                            task = asyncio.create_task(task_followup_timeout(
                                reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, 
                                trigger_timestamp=msg_timestamp,
                                thread_id=current_thread_id
                            ))
                            followup_tasks[reply_to_msg_id] = task
                            followup_msg_map[event.id] = reply_to_msg_id

                    elif is_wait_cmd:
                        if reply_to_msg_id in followup_tasks:
                            followup_tasks[reply_to_msg_id].cancel()
                            del followup_tasks[reply_to_msg_id]
                            log_tree(1, f"🔄 [稍等] 覆盖并销毁 [跟进] | Msg={reply_to_msg_id}")

                        if reply_to_msg_id in wait_tasks:
                            wait_tasks[reply_to_msg_id].cancel()
                            del wait_tasks[reply_to_msg_id]

                        task = asyncio.create_task(task_wait_timeout(
                            reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users,
                            trigger_timestamp=msg_timestamp,
                            thread_id=current_thread_id
                        ))
                        wait_tasks[reply_to_msg_id] = task
                        wait_msg_map[event.id] = reply_to_msg_id

        else:
            # [Ver 31.9] 双杀修复：忽略客户的编辑事件
            if isinstance(event, events.MessageEdited):
                return

            update_msg_cache(chat_id, event.id, sender_id, grouped_id)
            # [Ver 31.8] 客户说话 -> 只取消【漏回监控】，绝不取消【稍等/跟进任务】
            cancel_tasks(chat_id, sender_id, current_thread_id, reason=f"客户发言: [{text[:100]}...]", types=['reply'])
            
            # [Ver 41.0] 日志中增加时间显示
            log_tree(0, f"Msg={event.id} [T={msg_time_str}] | User={sender_id} | [{chat_id}] {sender_name}: {text} [{msg_type}]")
            
            # [Ver 38.0] 自回防漏监测 (Self-Reply Guard)
            if reply_to_msg_id and real_customer_id:
                if sender_id == real_customer_id:
                     # 确保忽略列表中的词不触发（例如连续说“谢谢”）
                     if normalize(text.strip()) not in IGNORE_SIGNATURES:
                         # [Ver 39.4] 图集去重: 如果属于同一个 Media Group，只监控第一条
                         should_monitor = True
                         if grouped_id:
                             if grouped_id in self_reply_dedup:
                                 log_tree(1, f"🛡️ 豁免 [自回-图集去重] | GroupID={grouped_id}")
                                 should_monitor = False
                             else:
                                 self_reply_dedup.append(grouped_id)
                         
                         if should_monitor:
                             # [Ver 43.3] Context Check: Only monitor if WAIT keyword exists in recent history (Thread-Aware)
                             has_wait = await check_wait_in_history(chat_id, current_thread_id)
                             
                             if not has_wait:
                                 log_tree(1, f"🛡️ 豁免 [自回-无稍等历史] | User={sender_id} | Msg={event.id}")
                             else:
                                 # [Ver 42.6] Removed CS Active Exemption - Strict Mode
                                 
                                 cancel_tasks(chat_id, sender_id, current_thread_id, reason="新自回覆盖旧自回", types=['self_reply'])
                                 
                                 register_task(chat_id, sender_id, event.id, current_thread_id)

                                 log_tree(1, f"🔥 侦测到自回行为 | User={sender_name} | Msg={event.id} -> {reply_to_msg_id}")
                                 
                                 # [Ver 42.8] Fix: Restore wait_check_stream route by adding it back
                                 
                                 task = asyncio.create_task(task_self_reply_timeout(
                                     event.id, sender_name, text[:50], msg_link, chat_id, sender_id, 
                                     trigger_timestamp=msg_timestamp,
                                     thread_id=current_thread_id
                                 ))
                                 
                                 def cleanup_self_reply(_):
                                     if event.id in self_reply_tasks: del self_reply_tasks[event.id]
                                     if event.id in self_reply_timers: del self_reply_timers[event.id]
                                     remove_task_record(chat_id, sender_id, event.id, current_thread_id)
                                     
                                 task.add_done_callback(cleanup_self_reply)
                                 self_reply_tasks[event.id] = task

            if reply_to_msg_id:
                try:
                    target_id = None
                    target_name = "未知客服" # Default
                    
                    replied_msg = await event.get_reply_message()
                    if replied_msg: 
                        target_id = replied_msg.sender_id
                        sender_obj = await replied_msg.get_sender()
                        if sender_obj: target_name = getattr(sender_obj, 'first_name', 'Unknown')
                    else: 
                        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
                        if msgs: 
                            target_id = msgs[0].sender_id
                            sender_obj = await msgs[0].get_sender()
                            if sender_obj: target_name = getattr(sender_obj, 'first_name', 'Unknown')

                    if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                        if normalize(text.strip()) in IGNORE_SIGNATURES: return
                        if event.id in reply_tasks: reply_tasks[event.id].cancel()
                        # [Ver 41.3 Fix Argument Error]
                        task = asyncio.create_task(task_reply_timeout(
                            event.id, sender_name, text[:50], msg_link, chat_id, sender_id, target_name, 
                            trigger_timestamp=msg_timestamp,
                            thread_id=current_thread_id
                        ))
                        reply_tasks[event.id] = task
                except Exception as e:
                    log_tree(9, f"❌ Reply Check Error: {e}")

    except Exception as e:
        log_tree(9, f"❌ Handler 异常: {e}")

if __name__ == '__main__':
    try:
        # [Ver 31.2] 启动延迟
        delay = int(os.environ.get("STARTUP_DELAY", 120))
        if delay > 0:
            logger.info(f"⏳ 启动延迟: 等待 {delay} 秒以确保旧连接断开...")
            time.sleep(delay)
            
        bot_loop = asyncio.get_event_loop()
        bot_loop.create_task(maintenance_task())
        Thread(target=run_web).start()
        # [Ver 43.4] 启动日志更新
        log_tree(0, "✅ 系统启动 (Ver 43.4 Fix Edit Logic & UI)")
        client.start()
        client.run_until_disconnected()
    except AuthKeyDuplicatedError:
        logger.critical("🚨 严重错误: SESSION_STRING 已失效！检测到多地登录冲突。")
        logger.critical("👉 请重新生成 SESSION_STRING 并更新环境变量。")
        sys.exit(1)
    except Exception as e:
        log_tree(9, f"❌ 启动失败: {e}")
