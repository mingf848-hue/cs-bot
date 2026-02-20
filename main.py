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
# 动态模块加载 (Stats & Responder)
# ==========================================
try:
    from work_stats import init_stats_blueprint
except ImportError as e:
    logger.warning(f"⚠️ 统计模块加载失败: {e}")
    init_stats_blueprint = None

try:
    from monitor_responder import init_monitor
    logger.info("✅ 自动回复模块 (monitor_responder) 导入成功")
except ImportError as e:
    logger.error(f"❌ 自动回复模块导入失败: {e}")
    init_monitor = None

# ==========================================
# 模块 1: 基础函数 (强力清洗版)
# ==========================================
def normalize(text):
    if not text: return ""
    text = text.lower()
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
    raw_wait = {normalize(x) for x in clean_env.split(',') if x.strip()}
    WAIT_SIGNATURES = {x for x in raw_wait if x} 

    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    if '|' in keep_keywords_env:
        keep_list = keep_keywords_env.split('|')
    else:
        keep_clean = keep_keywords_env.replace("，", ",")
        keep_list = keep_clean.split(',')
        
    raw_keep = {normalize(x) for x in keep_list if x.strip()}
    KEEP_SIGNATURES = {x for x in raw_keep if x}
    
    log_tree(0, f"🔍 关键词配置: WAIT={len(WAIT_SIGNATURES)} | KEEP={len(KEEP_SIGNATURES)}")

    default_ignore = (
        "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴,"
        "好的呢,嗯,嗯嗯,谢了,okk,k,行,妥,了解,已收,没问题,好的收到,ok了,麻烦了,"
        "好的感谢,哦,知道了,好的知道了,没事了"
    )
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x) for x in clean_ignore.split(',') if x.strip()}
    
    CS_NAME_PREFIXES = ["YY_6/9_值班号", "Y_YY"]

    AI_PROXY_URL = os.environ.get("AI_PROXY_URL")
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
SELF_REPLY_TIMEOUT = 3 * 60 

MAX_CACHE_SIZE = 50000 

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
self_reply_tasks = {} 

wait_timers = {}
followup_timers = {}
reply_timers = {}
self_reply_timers = {} 

# [重要] 映射表：CS回复的消息ID -> 客户原始消息ID
wait_msg_map = {}        
followup_msg_map = {} 
deleted_cache = deque(maxlen=10000)
self_reply_dedup = deque(maxlen=1000) 

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
        try: msg_content_cache.pop(next(iter(msg_content_cache)))
        except StopIteration: pass
    safe_text = text[:100].replace('\n', ' ') if text else "[非文本/空]"
    msg_content_cache[key] = {'name': name, 'text': safe_text}

def record_cs_activity(chat_id, user_id=None, thread_id=None, timestamp=None):
    if timestamp is None: 
        timestamp = time.time()
    
    if user_id: 
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

async def check_wait_in_history(chat_id, thread_id=None, limit=30):
    try:
        kwargs = {'limit': limit}
        if thread_id:
             kwargs['reply_to'] = thread_id
             
        async for m in client.iter_messages(chat_id, **kwargs):
            if not m.text: continue
            
            is_cs = False
            if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs = True
            else:
                try:
                    s = await m.get_sender()
                    if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)):
                        is_cs = True
                except: pass
            
            if is_cs:
                text_norm = normalize(m.text)
                if any(k in text_norm for k in WAIT_SIGNATURES):
                    return True
    except Exception as e:
        logger.error(f"History check failed: {e}")
        return False
    return False

async def maintenance_task():
    while True:
        try:
            await asyncio.sleep(600)
            now = time.time()
            expired_keys = [k for k, v in cs_activity_log.items() if now - v > 3600]
            for k in expired_keys: del cs_activity_log[k]
        except Exception as e: logger.error(f"维护任务出错: {e}")

# ==========================================
# 模块 4: Web 控制台
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
        <h1>⚡️ 实时监控 (Ver 45.21)</h1>
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
    <a href="/tool/work_stats" target="_blank" class="btn" style="margin-top:10px;background:#6a1b9a">📊 工作量统计 & Google同步</a>
    <a href="/zd" target="_blank" class="btn" style="margin-top:10px;background:#e65100">🤖 自动回复配置</a>
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 45.21 (Final Consolidated Version)</div>
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
        fetch('/log_raw?t=' + Date.now())
            .then(r => { if (!r.ok) throw new Error('Network response was not ok'); return r.text(); })
            .then(text => {
                if (!text.trim()) { container.innerHTML = '<div class="error-msg">暂无日志数据</div>'; return; }
                try { parseLogs(text); renderLogs(); scrollToBottom(); } 
                catch (e) { console.error("Log parsing error:", e); container.innerHTML = `<div class="error-msg">日志解析错误: ${e.message}</div>`; }
            })
            .catch(err => { container.innerHTML = `<div class="error-msg">加载失败: ${err.message}</div>`; });

        function parseLogs(text) {
            const rawLines = text.split(/\\r?\\n/);
            parsedLogs = [];
            let currentEntry = null;
            const timeRegex = /^(\\d{4}-\\d{2}-\\d{2}\\s+)?(\\d{2}:\\d{2}:\\d{2})(.*)/;
            
            rawLines.forEach(line => {
                if(!line.trim()) return;
                const match = line.match(timeRegex);
                if (match) {
                    if (currentEntry) parsedLogs.push(currentEntry);
                    currentEntry = { time: match[2], raw: match[3], content: match[3].trim(), fullText: match[3] };
                } else {
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
                const idRegex = /(Msg|User|Thread|流|归属|用户)[:=]?\\s?(\\d+)/g;
                let match;
                while ((match = idRegex.exec(content)) !== null) { ids.push(match[2]); }
                let idsStr = ids.join(',');

                if (raw.includes('📦')) { type = 'user'; content = content.replace('📦', '').trim(); }
                else if (raw.includes('客服操作') || (raw.includes('⚡️') && raw.includes('┣━━'))) { type = 'cs'; content = content.replace(/[┣┗]━━/, '').replace('⚡️', '⚡️ ').trim(); }
                else if (raw.includes('🚨') || raw.includes('[ALERT]')) { type = 'alert'; }
                else if (raw.includes('👮') || raw.includes('[AUDIT]')) { type = 'audit'; }
                else if (raw.includes('┣━━') || raw.includes('┗━━')) { type = 'sys'; }

                content = content.replace(/(Msg[:=]?\\s?)(\\d+)/g, '$1<span class="pill" onclick="searchId(\\'$2\\')">$2</span>');
                content = content.replace(/(User|用户|归属)[:=]?\\s?(\\d+)/g, '$1<span class="pill" onclick="searchId(\\'$2\\')">$2</span>');
                
                let actionBtn = '';
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
        .latest-text { font-size: 12px; color: #d32f2f; margin-top: 6px; background: #fff3e0; padding: 4px 8px; border-radius: 4px; border: 1px dashed #ffa726; }
        .reason-text { color: #d32f2f; font-size: 13px; margin-top: 4px; font-style: italic; }
        .reason-success { color: #2e7d32; font-size: 13px; margin-top: 4px; font-style: italic; }
        .msg-link { text-decoration: none; color: #0088cc; font-size: 13px; display: inline-block; margin-top: 5px; font-weight: 500; }
        .msg-link:hover { text-decoration: underline; }
        .copy-btn { cursor: pointer; background: #e0f7fa; padding: 2px 6px; border-radius: 4px; border: 1px solid #b2ebf2; }
        .copy-btn:hover { background: #b2ebf2; }
        
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
            <label>输入关键词 (输入"全体"可扫描漏回)</label>
            <input type="text" id="keyword" placeholder="输入关键词 (例如: 请稍等ART，或输入 '全体')" value="请稍等ART">
        </div>
        <button onclick="startCheck()" id="btn-search">开始检测</button>
        
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
                                pText.innerText = `已找到 ${allResults.length} 条结果...`;
                            } else if (data.type === 'done') {
                                pFill.style.width = '100%';
                                pText.innerText = '检测完成，正在排序...';
                                allResults.sort((a, b) => new Date(b.time) - new Date(a.time));
                                renderResults(allResults); 
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
            
            filtered.sort((a, b) => new Date(b.time) - new Date(a.time));
            renderResults(filtered);
            
            document.querySelectorAll('.filter-btn').forEach(btn => {
                 if(btn.innerText.includes(type === 'all' ? '全部' : (type === 'closed' ? '已闭环' : '未闭环'))) {
                     btn.classList.add('filter-active');
                 } else {
                     btn.classList.remove('filter-active');
                 }
            });
        }
        
        function renderResults(list) {
            const resList = document.getElementById('result-list');
            resList.innerHTML = '';
            list.forEach(data => {
                const div = document.createElement('div');
                div.className = 'result-item';
                
                const isAllSearch = (data.latest_text === '无人引用回复' || data.latest_text === '相邻消息被回复');
                const mainDisplay = isAllSearch ? data.found_text : data.latest_text;
                const subDisplay = isAllSearch ? data.latest_text : data.found_text;

                div.innerHTML = `
                    <div class="status-badge ${data.is_closed ? 'status-closed' : 'status-open'}">
                        ${data.is_closed ? '✅ 已闭环' : '❌ 未闭环'}
                    </div>
                    <div class="msg-content">
                        <div class="msg-meta">
                            <span>📅 ${data.time}</span>
                            <span>📂 ${data.group_name}</span>
                        </div>
                        <div class="msg-text">${mainDisplay}</div>
                        ${data.reason ? `<div class="${data.is_closed ? 'reason-success' : 'reason-text'}">${data.is_closed ? '🤖 ' : '⚠️ '}${data.reason}</div>` : ''}
                        <div class="latest-text">👀 ${isAllSearch ? '判定状态' : '触发消息'}: [${subDisplay}]</div>
                        <span class="msg-link copy-btn" onclick="copyLink('${data.link}', this)">🔗 点击复制链接</span>
                    </div>
                `;
                resList.appendChild(div);
            });
        }
        
        function copyLink(link, btnElement) {
            navigator.clipboard.writeText(link).then(() => {
                const originalText = btnElement.innerText;
                btnElement.innerText = "✅ 已复制";
                setTimeout(() => { btnElement.innerText = originalText; }, 1500);
            }).catch(err => {
                console.error('Failed to copy: ', err);
            });
        }
    </script>
</body>
</html>
"""

# ==========================================
# 补全的 Web 路由区域 (处理 404 错误)
# ==========================================
@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, s=self_reply_timers, current_time=now)

@app.route('/log')
def log_ui(): return Response(LOG_VIEWER_HTML, mimetype='text/html')

@app.route('/tool/wait_check')
def wait_check_ui(): 
    return Response(WAIT_CHECK_HTML, mimetype='text/html')

@app.route('/log_raw')
def log_raw():
    try:
        if not os.path.exists(LOG_FILE_PATH):
            return "Log file not created yet.", 200
        file_size = os.path.getsize(LOG_FILE_PATH)
        read_size = 200 * 1024 
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
# 模块 4.5: AI 分析模块 (Ver 45.21)
# ==========================================

def _ai_check_reply_needed(text):
    simple_text = normalize(text)
    if len(simple_text) <= 2 or simple_text.isdigit(): return (False, "本地兜底: 极简回复")
    if simple_text in IGNORE_SIGNATURES: return (False, "本地兜底: 命中忽略词")
    
    proxy_url = AI_PROXY_URL.rstrip('/')
    url = f"{proxy_url}/v1beta/models/{AI_MODEL_NAME}:generateContent"
    headers = {'Content-Type': 'application/json'}
    prompt = f"判断客户消息是否需要回复。消息: '{text}'\n如果是礼貌结束语或无意义，返回false。如果是问题或投诉，返回true。\nJSON: {{'reason': '...', 'need_reply': true/false}}"
    data = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"response_mime_type": "application/json"}}
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=10)
        if resp.status_code == 200:
            decision = json.loads(resp.json()['candidates'][0]['content']['parts'][0]['text'])
            return (decision.get("need_reply", True), decision.get("reason", "AI Decision"))
    except: pass
    return (True, "AI Fail")

def _ai_check_orphan_context(target_text, context_text_list, target_label="User"):
    """
    [Sync Function] [Ver 45.20/21]
    让 AI 自由思考上下文，移除死板规则。
    """
    if not target_text or len(target_text) < 1: return (True, "忽略空消息") 
    
    context_str = "\n".join(context_text_list)
    log_prefix = f"🤖 [AI-Orphan] Text='{target_text[:15]}...' | "
    
    proxy_url = AI_PROXY_URL.rstrip('/')
    url = f"{proxy_url}/v1beta/models/{AI_MODEL_NAME}:generateContent"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""
    你是一名经验丰富的客服质检员。
    请根据上下文判断下面的【目标消息】是否属于“客服漏回”的事故。

    目标发送者: "{target_label}"
    目标消息: "{target_text}"
    
    最近聊天记录 (包含时间、发送者、内容):
    {context_str}
    
    【分析逻辑】:
    请像人类一样综合思考。仔细观察上下文的时间流和对话流。
    - 豁免 (is_slip_up=true): 如果这条消息看起来是用户连续发言中的一句（分段发送）、对上一句的补充、无意义的语气词，或者客服在上下文中已经明显针对该【同一事件/话题】接待了该用户，请认为无需单独回复。
    - 漏回 (is_slip_up=false): 只有当这是一条被完全忽视的、独立的业务请求时，才标记为漏回。特别注意：如果客户在短时间内连续发送了两个完全不同的问题（例如一个问充值，一个问其它业务），而客服只回答了其中一个，那么未被回答的那个独立问题应判定为漏回 (is_slip_up=false)！
    
    请输出 JSON 格式: {{"reason": "用中文简短说明原因...", "is_slip_up": true/false}}
    """
    
    data = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"response_mime_type": "application/json"}
    }
    
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=10)
        if resp.status_code == 200:
            res_json = resp.json()
            raw_content = res_json.get('candidates', [])[0].get('content', {}).get('parts', [{}])[0].get('text', '')
            decision = json.loads(raw_content)
            is_slip_up = decision.get("is_slip_up", False)
            reason = decision.get("reason", "AI Decision")
            log_tree(2, log_prefix + f"✅ AI判定: 豁免={is_slip_up} | {reason}")
            return (is_slip_up, reason)
        else:
            return (False, f"API Error {resp.status_code}") 
    except Exception as e:
        log_tree(9, log_prefix + f"❌ AI Check Failed: {e}")
        return (False, f"Exception {str(e)}") 

async def _check_is_closed_logic(latest_msg):
    is_closed = False
    reason = ""
    last_sender_id = latest_msg.sender_id
    last_sender_is_cs = False
    if last_sender_id in ([MY_ID] + OTHER_CS_IDS): last_sender_is_cs = True
    else:
         try:
             s = await latest_msg.get_sender()
             if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): last_sender_is_cs = True
         except: pass
    
    if not last_sender_is_cs:
        if not latest_msg.text or not latest_msg.text.strip(): is_closed = False; reason = "最后是客户[媒体/贴纸]"
        else:
            need_reply, ai_reason = await asyncio.get_event_loop().run_in_executor(None, lambda: _ai_check_reply_needed(latest_msg.text))
            if not need_reply: is_closed = True; reason = f"AI判定已闭环：{ai_reason}"
            else: is_closed = False; reason = f"AI判定需回复：{ai_reason}"
    else:
        last_text_norm = normalize(latest_msg.text or "")
        is_wait = any(k in last_text_norm for k in WAIT_SIGNATURES)
        is_keep = last_text_norm in KEEP_SIGNATURES
        if is_wait or is_keep:
            is_closed = False; reason = f"客服最后仍回复{'稍等' if is_wait else '跟进'}词"
            if latest_msg.reply_to:
                try:
                    replied_obj = await latest_msg.get_reply_message()
                    if not replied_obj: is_closed = True; reason = "客户已删消息 (自动豁免)"
                except: pass
        else: is_closed = True
    return is_closed, reason

async def check_wait_keyword_logic(keyword, result_queue):
    try:
        cutoff_hours = 10
        limit_count = 3000
        if keyword in ["全体", "全体检测"]:
            cutoff_hours = 20
            limit_count = 6000 
            
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
        total_groups = len(CS_GROUP_IDS)
        EXCLUDED_GROUPS = [-1002807120955, -1002169616907]
        
        found_count = 0
        closed_count = 0

        for idx, chat_id in enumerate(CS_GROUP_IDS):
            if chat_id in EXCLUDED_GROUPS: continue
            
            percent = int((idx / total_groups) * 100)
            result_queue.put(json.dumps({"type": "progress", "percent": percent, "msg": f"正在扫描群组 {chat_id} ({idx+1}/{total_groups})..."}))

            try:
                history = []
                async for m in client.iter_messages(chat_id, limit=limit_count):
                    if m.date and m.date < cutoff_time: break
                    history.append(m)
                
                if keyword in ["全体", "全体检测"]:
                    msg_grouped_map = {}
                    user_msg_map = defaultdict(list)
                    for m in history:
                        if m.grouped_id: msg_grouped_map[m.id] = m.grouped_id
                        if m.sender_id: user_msg_map[m.sender_id].append(m)

                    replied_to_ids = set()
                    for m in history:
                        if m.reply_to and m.reply_to.reply_to_msg_id:
                            replied_to_ids.add(m.reply_to.reply_to_msg_id)
                    
                    replied_grouped_ids = set()
                    for mid in replied_to_ids:
                        if mid in msg_grouped_map:
                            replied_grouped_ids.add(msg_grouped_map[mid])

                    for i, m in enumerate(history):
                        is_cs = False
                        if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs = True
                        else:
                            try:
                                s = m.sender 
                                if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): is_cs = True
                            except: pass
                        if is_cs: continue

                        if m.reply_to and m.reply_to.reply_to_msg_id: continue

                        is_orphan = True
                        if m.id in replied_to_ids: is_orphan = False
                        elif m.grouped_id and m.grouped_id in replied_grouped_ids: is_orphan = False
                            
                        if is_orphan:
                            start = max(0, i - 6) 
                            end = min(len(history), i + 7)
                            context_slice = history[start:end]
                            context_slice.sort(key=lambda x: x.date)
                            
                            target_uid = m.sender_id
                            target_label = f"User({str(target_uid)[-4:]})" 

                            context_txts = []
                            for cm in context_slice:
                                if cm.sender_id in ([MY_ID] + OTHER_CS_IDS): c_label = "CS"
                                else:
                                    is_cm_cs = False
                                    try:
                                        if getattr(cm.sender, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): is_cm_cs = True
                                    except: pass
                                    if is_cm_cs: c_label = "CS"
                                    else: c_label = f"User({str(cm.sender_id)[-4:]})"

                                c_txt = (cm.text or "[Media]").replace('\n', ' ')
                                marker = " <<< TARGET" if cm.id == m.id else ""
                                context_txts.append(f"[{cm.date.strftime('%H:%M:%S')}] {c_label}: {c_txt}{marker}")
                            
                            is_slip_up, ai_reason = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: _ai_check_orphan_context(m.text or "[Media]", context_txts, target_label)
                            )
                            
                            found_count += 1
                            is_result_closed = False
                            display_reason = "孤立无回复 (No Quote Reply)"
                            
                            if is_slip_up:
                                is_result_closed = True
                                closed_count += 1
                                display_reason = f"🤖 {ai_reason}"
                            
                            group_name = str(chat_id)
                            try: g = await client.get_entity(chat_id); group_name = g.title
                            except: pass

                            safe_text = (m.text or "[媒体/空]")[:100].replace('\n', ' ')
                            beijing_time = m.date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                            real_chat_id = str(chat_id).replace('-100', '')
                            link = f"https://t.me/c/{real_chat_id}/{m.id}"
                            
                            result_queue.put(json.dumps({
                                "type": "result",
                                "is_closed": is_result_closed,
                                "reason": display_reason,
                                "time": beijing_time,
                                "group_name": group_name,
                                "found_text": safe_text,
                                "latest_text": "无人引用回复",
                                "link": link
                            }))
                            
                    continue 

                thread_latest_msg = {}
                for m in history:
                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id 
                        if not t_id: t_id = m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    if t_id not in thread_latest_msg:
                        thread_latest_msg[t_id] = m

                for m in history:
                    if not m.text: continue
                    if keyword in m.text: 
                        found_count += 1
                        t_id = None
                        if m.reply_to:
                            t_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                        if not t_id: t_id = m.id
                        
                        latest_msg = thread_latest_msg.get(t_id, m)
                        is_closed, reason = await _check_is_closed_logic(latest_msg)
                        if is_closed: closed_count += 1

                        group_name = str(chat_id)
                        try: g = await client.get_entity(chat_id); group_name = g.title
                        except: pass
                        
                        safe_text = (m.text or "")[:100].replace('\n', ' ')
                        beijing_time = m.date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                        
                        link = ""
                        real_chat_id = str(chat_id).replace('-100', '')
                        url_thread_id = None
                        target_msg_for_link = latest_msg if not is_closed else m
                        
                        if "(客户删消息)" not in reason:
                            if target_msg_for_link.reply_to:
                                url_thread_id = target_msg_for_link.reply_to.reply_to_top_id or target_msg_for_link.reply_to.reply_to_msg_id
                        
                        if url_thread_id: link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}?thread={url_thread_id}"
                        else: link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}"
                        
                        latest_content = (latest_msg.text or "[媒体]")[:60].replace('\n', ' ')

                        result_queue.put(json.dumps({
                            "type": "result",
                            "is_closed": is_closed,
                            "reason": reason,
                            "time": beijing_time,
                            "group_name": group_name,
                            "found_text": safe_text,
                            "latest_text": latest_content, 
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
        result_queue.put(None) 

    except Exception as e:
        logger.error(f"Check Task Logic Error: {e}")
        result_queue.put(None)

@app.route('/api/wait_check_stream')
def wait_check_stream():
    keyword = request.args.get('keyword', '').strip()
    if not keyword: return "Keyword required", 400
    def generate():
        result_queue = queue.Queue()
        if not bot_loop: yield "Error: Bot loop not ready\n"; return
        asyncio.run_coroutine_threadsafe(check_wait_keyword_logic(keyword, result_queue), bot_loop)
        while True:
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
async def audit_pending_tasks():
    log_tree(4, "开始执行【下班巡检】(关键词分批扫描)...")
    await send_alert("👮 **开始执行下班自动巡检...**\n正在分批扫描专属关键词...", "")
    
    all_keywords = sorted(list(WAIT_SIGNATURES))
    all_keywords = sorted(list(set(all_keywords)), key=lambda x: (len(x), x), reverse=True) 
    
    history_cache = {}
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=10) 
    
    EXCLUDED_GROUPS = [-1002807120955, -1002169616907]

    log_tree(4, "正在预读取消息历史 (最近10小时)...")
    for chat_id in CS_GROUP_IDS:
        if chat_id in EXCLUDED_GROUPS: continue
        try:
            msgs = []
            async for m in client.iter_messages(chat_id, limit=3000):
                if m.date and m.date < cutoff_time: break
                msgs.append(m)
            history_cache[chat_id] = msgs
        except Exception as e:
            logger.error(f"Group {chat_id} fetch failed: {e}")

    total_issues = 0

    for keyword in all_keywords:
        if not keyword.strip(): continue
        
        kw_issues = []
        found_count = 0
        closed_count = 0
        
        for chat_id, history in history_cache.items():
            thread_latest_msg = {}
            for m in history:
                t_id = None
                if m.reply_to:
                    t_id = m.reply_to.reply_to_top_id 
                    if not t_id: t_id = m.reply_to.reply_to_msg_id
                if not t_id: t_id = m.id
                if t_id not in thread_latest_msg:
                    thread_latest_msg[t_id] = m
            
            for m in history:
                if not m.text: continue
                
                if keyword in normalize(m.text):
                    is_cs_sender = False
                    if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs_sender = True
                    else:
                        sender = await m.get_sender()
                        name = getattr(sender, 'first_name', '') or ''
                        if name.startswith(tuple(CS_NAME_PREFIXES)): is_cs_sender = True
                    
                    if not is_cs_sender: continue

                    found_count += 1

                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    
                    latest_msg = thread_latest_msg.get(t_id, m)
                    is_closed, reason = await _check_is_closed_logic(latest_msg)
                    
                    if is_closed:
                        closed_count += 1
                    else:
                        cs_name_display = "未知客服"
                        try:
                            s = await m.get_sender()
                            if s: cs_name_display = getattr(s, 'first_name', 'Unknown')
                        except: pass

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
                        
                        cs_reply_text = (latest_msg.text or "[媒体]")[:15]

                        kw_issues.append({
                            'cs_name': cs_name_display,
                            'customer_text': customer_text,
                            'cs_reply': cs_reply_text,
                            'reason': reason,
                            'link': link
                        })

        if kw_issues:
            total_issues += len(kw_issues)
            open_count = found_count - closed_count
            report_text = (
                f"👮 **下班巡检报告**\n"
                f"🔑 关键词: `{keyword}`\n"
                f"📊 命中: {found_count} | ✅ 闭环: {closed_count} | ❌ 未闭环: {open_count}\n\n"
            )
            
            for i, iss in enumerate(kw_issues[:8]): 
                report_text += (
                    f"{i+1}. 👤 {iss['cs_name']}\n"
                    f"   💬 客户: {iss['customer_text']}\n"
                    f"   👉 结果: {iss['cs_reply']} ({iss['reason']})\n"
                    f"   🔗 [点击跳转]({iss['link']})\n\n"
                )
            
            if len(kw_issues) > 8:
                report_text += f"... (还有 {len(kw_issues)-8} 条未显示)"
            
            await send_alert(report_text, "", f"Audit-{keyword}")
            await asyncio.sleep(2) 
        else:
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

def cancel_tasks(chat_id, user_id, thread_id=None, target_msg_id=None, reason="未知", types=None):
    if types is None: types = ['wait', 'followup', 'reply', 'self_reply'] 
    
    targets = set()
    hit_specific = False
    
    if target_msg_id:
        if target_msg_id in wait_tasks or target_msg_id in followup_tasks or target_msg_id in reply_tasks or target_msg_id in self_reply_tasks:
            targets.add(target_msg_id)
            hit_specific = True
            
        if target_msg_id in wait_msg_map:
            targets.add(wait_msg_map[target_msg_id])
            hit_specific = True
            
        if target_msg_id in followup_msg_map:
            targets.add(followup_msg_map[target_msg_id])
            hit_specific = True

        if targets:
            hit_specific = True

    if not hit_specific and thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])

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
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        current_task = asyncio.current_task() 
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])
        
        log_tree(1, f"启动 [稍等] 倒计时 (12m) {ids_str} | Thread={thread_id}")
        
        end_time = trigger_timestamp + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [稍等] {ids_str} | 原因: {safe_reason} (客服已处理)")
            return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟 (无后续回复)\n🔗 [点击处理]({link})", link, ids_str)

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
        if key_id in wait_tasks and wait_tasks[key_id] == current_task:
            del wait_tasks[key_id]
            if key_id in wait_timers: del wait_timers[key_id]
            if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
            for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        current_task = asyncio.current_task()
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
        if key_id in followup_tasks and followup_tasks[key_id] == current_task:
            del followup_tasks[key_id]
            if key_id in followup_timers: del followup_timers[key_id]
            if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
            for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, trigger_timestamp, thread_id=None):
    try:
        current_task = asyncio.current_task()
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
        if trigger_msg_id in reply_tasks and reply_tasks[trigger_msg_id] == current_task:
            del reply_tasks[trigger_msg_id]
            if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
            remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

async def task_self_reply_timeout(trigger_msg_id, user_name, content, link, chat_id, user_id, trigger_timestamp, thread_id=None):
    try:
        current_task = asyncio.current_task()
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [自回] 监控 (3m) {ids_str} | Thread={thread_id}")
        
        end_time = trigger_timestamp + SELF_REPLY_TIMEOUT
        self_reply_timers[trigger_msg_id] = {'ts': end_time, 'user': user_name, 'url': link}
        
        await asyncio.sleep(SELF_REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [自回] 报警 Msg={trigger_msg_id}")
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **自回防漏监测**\n👤 用户: {user_name} (自行追加消息)\n⚠️ 状态: 已 {SELF_REPLY_TIMEOUT // 60} 分钟未处理\n🔗 [点击回复]({link})", link, ids_str)
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in self_reply_tasks and self_reply_tasks[trigger_msg_id] == current_task:
             del self_reply_tasks[trigger_msg_id]
             if trigger_msg_id in self_reply_timers: del self_reply_timers[trigger_msg_id]
             remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

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
        
        sender_id = target_msg.sender_id
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
        
        # 过滤服务消息
        if event.message.action:
            return 

        msg_timestamp = event.date.timestamp()
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
        is_keep_cmd = norm_text in KEEP_SIGNATURES
        
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

        if is_sender_cs:
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id, timestamp=msg_timestamp)
            
            if isinstance(event, events.MessageEdited):
                 if real_customer_id or current_thread_id:
                     cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服编辑: [{text[:100]}...]")
                 try:
                     is_latest = True
                     latest_found_id = event.id
                     if current_thread_id:
                         async for m in client.iter_messages(chat_id, limit=30):
                             is_in_thread = False
                             if m.reply_to:
                                 if m.reply_to.reply_to_top_id == current_thread_id: is_in_thread = True
                                 if m.reply_to.reply_to_msg_id == current_thread_id: is_in_thread = True
                             
                             if is_in_thread:
                                 if m.id > event.id:
                                     is_latest = False
                                     latest_found_id = m.id
                                     txt = normalize(m.text or "")
                                     if not any(k in txt for k in WAIT_SIGNATURES):
                                         log_tree(1, f"🛡️ 编辑拦截 | Msg={event.id} 被新消息 Msg={m.id} 覆盖 (内容非稍等) -> 忽略")
                                         return 
                                     else:
                                         log_tree(1, f"⚠️ 编辑放行 | Msg={event.id} 虽非最新 (Top={m.id}) 但Top仍为稍等")
                                 break 
                         else:
                             latest_batch = await client.get_messages(chat_id, limit=1)
                             if latest_batch:
                                 m = latest_batch[0]
                                 if m.id > event.id:
                                     txt = normalize(m.text or "")
                                     if not any(k in txt for k in WAIT_SIGNATURES):
                                         log_tree(1, f"🛡️ 编辑拦截(主群) | Msg={event.id} 被新消息 Msg={m.id} 覆盖 -> 忽略")
                                         return

                 except Exception as e:
                     log_tree(9, f"❌ 编辑检测失败: {e}")

            if reply_to_msg_id:
                source_info = "未知"
                if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
                elif real_customer_id: source_info = "API实时查询"
                else: source_info = "追踪失败" 
                
                log_tree(1, f"⚡️ 客服操作捕获 | Msg: {reply_to_msg_id} [T={msg_time_str}] | 客服: {sender_name} | 内容: [{text[:100]}] | 归属: {real_customer_id} | 流: {current_thread_id} | 状态: {source_info}")

            cancel_types = None 
            if is_wait_cmd or is_keep_cmd:
                cancel_types = ['reply', 'self_reply']

            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, 
                             thread_id=current_thread_id, 
                             target_msg_id=reply_to_msg_id, 
                             reason=f"客服回复: [{text[:100]}...]", 
                             types=cancel_types)
            
            if reply_to_msg_id and reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]
            
            if reply_to_msg_id and reply_to_msg_id in self_reply_tasks:
                self_reply_tasks[reply_to_msg_id].cancel()
                del self_reply_tasks[reply_to_msg_id]

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id:
                    related_users = [real_customer_id]

                if related_users:
                    if is_keep_cmd:
                        should_monitor_keep = await check_wait_in_history(chat_id, current_thread_id)
                        
                        if not should_monitor_keep:
                             log_tree(1, f"🛡️ 豁免 [跟进] | Msg={event.id} | 原因: 历史流无本号[稍等]关键词")
                        else:
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
            if isinstance(event, events.MessageEdited):
                return

            update_msg_cache(chat_id, event.id, sender_id, grouped_id)
            cancel_tasks(chat_id, sender_id, current_thread_id, reason=f"客户发言: [{text[:100]}...]", types=['reply'])
            
            log_tree(0, f"Msg={event.id} [T={msg_time_str}] | User={sender_id} | [{chat_id}] {sender_name}: {text} [{msg_type}]")
            
            if reply_to_msg_id and real_customer_id:
                if sender_id == real_customer_id:
                     if normalize(text.strip()) not in IGNORE_SIGNATURES:
                         should_monitor = True
                         if grouped_id:
                             if grouped_id in self_reply_dedup:
                                 log_tree(1, f"🛡️ 豁免 [自回-图集去重] | GroupID={grouped_id}")
                                 should_monitor = False
                             else:
                                 self_reply_dedup.append(grouped_id)
                         
                         if should_monitor:
                             has_wait = await check_wait_in_history(chat_id, current_thread_id)
                             
                             if not has_wait:
                                 log_tree(1, f"🛡️ 豁免 [自回-无稍等历史] | User={sender_id} | Msg={event.id}")
                             else:
                                 cancel_tasks(chat_id, sender_id, current_thread_id, reason="新自回覆盖旧自回", types=['self_reply'])
                                 register_task(chat_id, sender_id, event.id, current_thread_id)
                                 log_tree(1, f"🔥 侦测到自回行为 | User={sender_name} | Msg={event.id} -> {reply_to_msg_id}")
                                 
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
                    target_name = "未知客服" 
                    
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
        delay = int(os.environ.get("STARTUP_DELAY", 120))
        if delay > 0:
            logger.info(f"⏳ 启动延迟: 等待 {delay} 秒以确保旧连接断开...")
            time.sleep(delay)
            
        bot_loop = asyncio.get_event_loop()
        bot_loop.create_task(maintenance_task())
        
        if init_stats_blueprint:
            init_stats_blueprint(app, client, bot_loop, CS_GROUP_IDS)
        
        if init_monitor:
            init_monitor(client, app, OTHER_CS_IDS, CS_NAME_PREFIXES, handler)
            
        Thread(target=run_web).start()
        log_tree(0, "✅ 系统启动 (Ver 45.21 Final Consolidated)")
        client.start()
        client.run_until_disconnected()
    except AuthKeyDuplicatedError:
        logger.critical("🚨 严重错误: SESSION_STRING 已失效！检测到多地登录冲突。")
        sys.exit(1)
    except Exception as e:
        log_tree(9, f"❌ 启动失败: {e}")
