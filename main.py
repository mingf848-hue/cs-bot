import os
import sys
import asyncio
import logging
import requests
import re
import time
from collections import deque, defaultdict
from datetime import datetime, timedelta, timezone
from threading import Thread
from flask import Flask, render_template_string, Response, request
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

    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    keep_list = keep_keywords_env.split('|')
    raw_keep = {normalize(x) for x in keep_list if x.strip()}
    KEEP_SIGNATURES = {x for x in raw_keep if x}
    
    log_tree(0, f"🔍 关键词配置 (Normalized): WAIT={WAIT_SIGNATURES} | KEEP={KEEP_SIGNATURES}")

    # [Ver 36.3] 扩展忽略关键词库
    default_ignore = (
        "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴,"
        "好的呢,嗯,嗯嗯,谢了,okk,k,行,妥,了解,已收,没问题,好的收到,ok了,麻烦了,"
        "好的感谢,哦"
    )
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x) for x in clean_ignore.split(',') if x.strip()}
    
    CS_NAME_PREFIXES = ["YY_6/9_值班号", "Y_YY"]

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
SELF_REPLY_TIMEOUT = 3 * 60 # [Ver 38.1] 自回倒计时

MAX_CACHE_SIZE = 50000 

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
self_reply_tasks = {} # [Ver 38.1]

wait_timers = {}
followup_timers = {}
reply_timers = {}
self_reply_timers = {} # [Ver 38.1]

wait_msg_map = {}        
followup_msg_map = {} 
deleted_cache = deque(maxlen=10000)

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

def record_cs_activity(chat_id, user_id=None, thread_id=None):
    now = time.time()
    if user_id: cs_activity_log[(chat_id, user_id)] = now
    if thread_id: cs_activity_log[(chat_id, thread_id)] = now

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

async def maintenance_task():
    while True:
        try:
            await asyncio.sleep(600)
            now = time.time()
            expired_keys = [k for k, v in cs_activity_log.items() if now - v > 3600]
            for k in expired_keys: del cs_activity_log[k]
        except Exception as e: logger.error(f"维护任务出错: {e}")

# ==========================================
# 模块 4: Web 控制台 (Modern UI 版)
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
    {% for title, timers in [('⏳ 稍等 (12m)', w), ('🕵️ 跟进 (15m)', f), ('🔔 漏回 (5m)', r), ('🗣️ 自回 (3m)', sr)] %}
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
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 38.1 (Self-Reply Guard Integrated)</div>
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
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: var(--bg-body); }
        ::-webkit-scrollbar-thumb { background: var(--bg-input); border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

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
        
        #log-container {
            flex-grow: 1;
            overflow-y: auto;
            padding: 24px;
            display: flex;
            flex-direction: column;
            gap: 16px;
            scroll-behavior: smooth;
        }

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

        .msg-user { align-items: flex-start; }
        .msg-user .bubble {
            background-color: var(--user-bubble);
            border-top-left-radius: 2px;
            color: #e2e8f0;
        }

        .msg-cs { align-items: flex-end; }
        .msg-cs .bubble {
            background-color: var(--cs-bubble);
            border-top-right-radius: 2px;
            color: #f0fdfa;
        }
        .msg-cs .msg-meta { flex-direction: row-reverse; }

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

@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, sr=self_reply_timers, current_time=now)

@app.route('/log')
def log_ui(): return render_template_string(LOG_VIEWER_HTML)

@app.route('/log_raw')
def log_raw():
    try:
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
    log_tree(4, "开始执行【下班巡检】...")
    await send_alert("👮 **开始执行下班自动巡检...**\n正在扫描最近活跃的消息流，检查是否有遗漏...", "")
    
    issues_found = 0
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=30)
    EXCLUDED_GROUPS = [-1002807120955, -1002169616907]

    def is_junk_message(text):
        if not text: return True
        clean = re.sub(r'[\s\d\.,;!?。，；！？、=]+', '', text)
        return len(clean) == 0 and len(text) < 10

    for chat_id in CS_GROUP_IDS:
        if chat_id in EXCLUDED_GROUPS:
             log_tree(4, f"🚫 跳过群组 {chat_id} (黑名单)")
             continue

        try:
            log_tree(4, f"正在扫描群组 {chat_id} (最近 30 小时)...")
            history = []
            async for m in client.iter_messages(chat_id, limit=5000):
                if m.date and m.date < cutoff_time: break
                history.append(m)
            
            msg_map = {m.id: m for m in history}
            msg_sender_map = {m.id: m.sender_id for m in history}
            
            replied_grouped_ids = set() 
            user_max_reply_id = defaultdict(int) 
            user_max_reply_time = defaultdict(float) 
            
            threads_map = defaultdict(list)

            for m in history:
                thread_id = None
                if m.reply_to:
                    thread_id = m.reply_to.reply_to_top_id 
                    if not thread_id: thread_id = m.reply_to.reply_to_msg_id
                if not thread_id: thread_id = m.id
                threads_map[thread_id].append(m)

                if await is_official_cs(m):
                    if m.reply_to:
                        reply_id = m.reply_to.reply_to_msg_id
                        target_msg = msg_map.get(reply_id)
                        if target_msg and target_msg.grouped_id: replied_grouped_ids.add(target_msg.grouped_id)
                        
                        target_user_id = None
                        if target_msg: target_user_id = target_msg.sender_id
                        elif reply_id in msg_sender_map: target_user_id = msg_sender_map[reply_id]
                        elif (chat_id, reply_id) in msg_to_user_cache: target_user_id = msg_to_user_cache[(chat_id, reply_id)]
                        
                        if target_user_id:
                            if m.id > user_max_reply_id[target_user_id]:
                                user_max_reply_id[target_user_id] = m.id
                                user_max_reply_time[target_user_id] = m.date.timestamp()

            active_task_users = set()
            for (cid, uid) in chat_user_active_msgs.keys():
                if cid == chat_id: active_task_users.add(uid)

            processed_user_latest = set()
            for m in history:
                if await is_official_cs(m): continue
                sender_id = m.sender_id
                if not sender_id: continue
                if sender_id in processed_user_latest: continue
                processed_user_latest.add(sender_id)

                # 豁免逻辑
                if sender_id in active_task_users: continue
                if user_max_reply_id[sender_id] > m.id: continue
                latest_reply_ts = user_max_reply_time.get(sender_id, 0)
                if latest_reply_ts > 0 and abs(m.date.timestamp() - latest_reply_ts) < 10: continue
                if m.grouped_id and m.grouped_id in replied_grouped_ids: continue
                text_norm = normalize(m.text or "")
                if text_norm and text_norm in IGNORE_SIGNATURES: continue
                if is_junk_message(m.text): continue

                issues_found += 1
                # [Ver 38.1 Audit Update] 识别自回场景
                audit_type = "客户自回/追问未回复" if (m.reply_to and m.reply_to.reply_to_msg_id in msg_sender_map and msg_sender_map[m.reply_to.reply_to_msg_id] == sender_id) else "客户最后发言未回复"
                
                root_text = (m.text or "[媒体文件]")[:50]
                link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{m.id}"
                if m.reply_to and m.reply_to.reply_to_top_id: link += f"?thread={m.reply_to.reply_to_top_id}"
                
                log_tree(4, f"❌ 发现遗漏({audit_type}) | Msg={m.id} | Text={root_text}")
                await send_alert(f"👮 **下班巡检-发现遗漏**\n⚠️ 类型: **{audit_type}**\n💬 客户消息: {root_text}\n🔗 [点击跳转对话]({link})", link, f"Msg={m.id}")
                await asyncio.sleep(1)

            # 稍等未闭环原逻辑...
            for t_id, msgs in threads_map.items():
                last_wait_msg = None
                last_wait_idx = -1
                for i, m in enumerate(msgs):
                    if await is_official_cs(m):
                        text = normalize(m.text or "")
                        if not text: continue
                        if any(k in text for k in WAIT_SIGNATURES) or normalize(text) in KEEP_SIGNATURES:
                            last_wait_msg = m
                            last_wait_idx = i
                            break 
                
                if last_wait_msg:
                    has_closed = False
                    if last_wait_idx > 0:
                        for nm in msgs[:last_wait_idx]:
                             if await is_official_cs(nm) and nm.reply_to:
                                 tid = nm.reply_to.reply_to_msg_id
                                 if tid == last_wait_msg.id: has_closed = True; break
                                 if last_wait_msg.reply_to and tid == last_wait_msg.reply_to.reply_to_msg_id: has_closed = True; break
                    
                    if not has_closed and last_wait_msg.reply_to:
                        reply_id = last_wait_msg.reply_to.reply_to_msg_id
                        t_cust_id = msg_sender_map.get(reply_id) or (msg_to_user_cache.get((chat_id, reply_id)))
                        if t_cust_id and user_max_reply_id[t_cust_id] > last_wait_msg.id: has_closed = True

                    if not has_closed:
                        issues_found += 1
                        cs_name = "未知客服"
                        try:
                            s_obj = await last_wait_msg.get_sender()
                            if s_obj: cs_name = getattr(s_obj, 'first_name', 'Unknown')
                        except: pass
                        link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{last_wait_msg.id}"
                        log_tree(4, f"❌ 发现遗漏(稍等未闭环) | Msg={last_wait_msg.id}")
                        await send_alert(f"👮 **下班巡检-发现遗漏**\n⚠️ 类型: **反馈核实未回复**\n👤 客服: {cs_name}\n💬 最后的回复: {(last_wait_msg.text or '')[:50]}\n🔗 [点击跳转对话]({link})", link, f"Msg={last_wait_msg.id}")
                        await asyncio.sleep(1)

        except Exception as e: log_tree(9, f"群组 {chat_id} 巡检失败: {e}")

    log_tree(4, f"巡检结束，共发现 {issues_found} 个问题。")
    await send_alert(f"🏁 **下班巡检结束**\n共发现 **{issues_found}** 个未闭环的对话。", "")

async def perform_stop_work():
    global IS_WORKING
    if IS_WORKING: await audit_pending_tasks()
    IS_WORKING = False
    for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()) + list(self_reply_tasks.values()): t.cancel()
    wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear(); self_reply_tasks.clear()
    wait_timers.clear(); followup_timers.clear(); reply_timers.clear(); self_reply_timers.clear()
    wait_msg_map.clear(); followup_msg_map.clear()
    chat_user_active_msgs.clear(); chat_thread_active_msgs.clear()
    msg_to_user_cache.clear(); msg_content_cache.clear(); group_to_user_cache.clear(); cs_activity_log.clear()
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

def cancel_tasks(chat_id, user_id, thread_id=None, reason="未知", types=None):
    if types is None: types = ['wait', 'followup', 'reply', 'self_reply'] 
    targets = set()
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            targets.update(chat_user_active_msgs[u_key])
            if len(types) >= 3: del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])
            if len(types) >= 3: del chat_thread_active_msgs[t_key]
    if not targets: return
    log_tree(1, f" ┣━━ 尝试销单 | 用户: {user_id} | 流: {thread_id} | 任务池: {list(targets)}")
    count = 0
    for mid in targets:
        if 'wait' in types and mid in wait_tasks: wait_tasks[mid].cancel(); count += 1
        if 'followup' in types and mid in followup_tasks: followup_tasks[mid].cancel(); count += 1
        if 'reply' in types and mid in reply_tasks: reply_tasks[mid].cancel(); count += 1
        if 'self_reply' in types and mid in self_reply_tasks: self_reply_tasks[mid].cancel(); count += 1
    if count > 0: log_tree(2, f"销单成功 | {reason}")

def check_recent_activity_safe(chat_id, task_start_time, user_ids=None, thread_id=None):
    buffer_seconds = 10
    if user_ids:
        for uid in user_ids:
            if cs_activity_log.get((chat_id, uid), 0) > task_start_time + buffer_seconds: return True, "有新回复"
    if thread_id:
        if cs_activity_log.get((chat_id, thread_id), 0) > task_start_time + buffer_seconds: return True, "流有新回复"
    return False, None

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        log_tree(1, f"启动 [稍等] 倒计时 (12m) Msg={key_id}")
        wait_timers[key_id] = {'ts': task_start_time + WAIT_TIMEOUT, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)
        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING or (my_msg_id and not await check_msg_exists(chat_id, my_msg_id)): return
        if (check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id))[0]: return
        await send_alert(f"🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n⚠️ 状态: 已过 {WAIT_TIMEOUT // 60} 分钟\n🔗 [点击处理]({link})", link, f"Msg={key_id}")
        await asyncio.sleep(10 * 60) # CRITICAL
        if not IS_WORKING or (check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id))[0]: return
        await send_alert(f"🔥 **严重超时警报**\n👤 客服: {agent_name}\n❌ 即将处理，请立即回复！\n🔗 [点击处理]({link})", link, f"Msg={key_id}")
    except asyncio.CancelledError: pass 
    finally:
        for k in [wait_tasks, wait_timers]: k.pop(key_id, None)
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        followup_timers[key_id] = {'ts': task_start_time + FOLLOWUP_TIMEOUT, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)
        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING or (check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id))[0]: return
        await send_alert(f"🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n🔗 [点击处理]({link})", link, f"Msg={key_id}")
    except asyncio.CancelledError: pass
    finally:
        for k in [followup_tasks, followup_timers]: k.pop(key_id, None)
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, thread_id=None):
    try:
        reply_timers[trigger_msg_id] = {'ts': time.time() + REPLY_TIMEOUT, 'user': sender_name, 'url': link, 'target': target_name}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        await send_alert(f"🔔 **漏回消息提醒**\n👤 用户: {sender_name} 回复了客服 {target_name}\n🔗 [点击回复]({link})", link, f"Msg={trigger_msg_id}")
    except asyncio.CancelledError: pass 
    finally:
        for k in [reply_tasks, reply_timers]: k.pop(trigger_msg_id, None)
        remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

# [Ver 38.1] 自回监测任务
async def task_self_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, thread_id=None):
    try:
        log_tree(1, f"启动 [自回] 监控 (3m) User={sender_name}")
        self_reply_timers[trigger_msg_id] = {'ts': time.time() + SELF_REPLY_TIMEOUT, 'user': sender_name, 'url': link, 'target': 'Self'}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        await asyncio.sleep(SELF_REPLY_TIMEOUT)
        if not IS_WORKING: return
        await send_alert(f"🗣️ **客户自言自语/追问提醒**\n👤 用户: {sender_name} @了自己\n📩 内容: `{content[:50]}`\n🔗 [点击处理]({link})", link, f"Msg={trigger_msg_id}")
    except asyncio.CancelledError: pass 
    finally:
        for k in [self_reply_tasks, self_reply_timers]: k.pop(trigger_msg_id, None)
        remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

# ==========================================
# 模块 8: 客户端核心
# ==========================================
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    cmd = event.text.strip()
    if cmd == '下班': await perform_stop_work()
    elif cmd == '上班': await perform_start_work()
    elif cmd == '状态': await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.append(msg_id)
        for tasks in [wait_tasks, followup_tasks, reply_tasks, self_reply_tasks]:
            if msg_id in tasks: tasks[msg_id].cancel()

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    try:
        global MY_ID
        if not MY_ID: MY_ID = (await client.get_me()).id
        if not IS_WORKING: return
        text = event.text or ""
        sender_id = event.sender_id
        reply_to_msg_id = event.reply_to_msg_id
        chat_id = event.chat_id
        msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}"
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', 'Unknown')
        update_content_cache(chat_id, event.id, sender_name, text)
        is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)
        curr_thread, _ = get_thread_context(event)

        real_customer_id = None
        if reply_to_msg_id:
            real_customer_id = msg_to_user_cache.get((chat_id, reply_to_msg_id)) or await get_traceable_sender(chat_id, reply_to_msg_id)

        if is_sender_cs:
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=curr_thread)
            cancel_tasks(chat_id, real_customer_id, curr_thread, reason="客服回复")
            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id) or ([real_customer_id] if real_customer_id else [])
                if related_users:
                    if any(k in normalize(text) for k in WAIT_SIGNATURES):
                        wait_tasks[reply_to_msg_id] = asyncio.create_task(task_wait_timeout(reply_to_msg_id, sender_name, text, msg_link, event.id, chat_id, related_users, curr_thread))
                    elif normalize(text) in KEEP_SIGNATURES:
                        followup_tasks[reply_to_msg_id] = asyncio.create_task(task_followup_timeout(reply_to_msg_id, sender_name, text, msg_link, event.id, chat_id, related_users, curr_thread))
        else:
            update_msg_cache(chat_id, event.id, sender_id)
            cancel_tasks(chat_id, sender_id, curr_thread, reason="客户发言", types=['reply', 'self_reply'])
            
            if reply_to_msg_id:
                target_id = msg_to_user_cache.get((chat_id, reply_to_msg_id)) or await get_traceable_sender(chat_id, reply_to_msg_id)
                # [Ver 38.1] 自回逻辑判断
                if target_id == sender_id:
                    is_wait_active = False
                    if (chat_id, sender_id) in chat_user_active_msgs:
                        for mid in chat_user_active_msgs[(chat_id, sender_id)]:
                            if mid in wait_tasks: is_wait_active = True; break
                    if is_wait_active:
                        self_reply_tasks[event.id] = asyncio.create_task(task_self_reply_timeout(event.id, sender_name, text, msg_link, chat_id, sender_id, curr_thread))
                
                # 漏回逻辑
                msg_obj = await event.get_reply_message()
                if msg_obj and await is_official_cs(msg_obj):
                    if normalize(text) not in IGNORE_SIGNATURES:
                        reply_tasks[event.id] = asyncio.create_task(task_reply_timeout(event.id, sender_name, text, msg_link, chat_id, sender_id, getattr(await msg_obj.get_sender(), 'first_name', 'CS'), curr_thread))
    except Exception as e: log_tree(9, f"Handler: {e}")

if __name__ == '__main__':
    bot_loop = asyncio.get_event_loop()
    bot_loop.create_task(maintenance_task())
    Thread(target=run_web).start()
    client.start()
    client.run_until_disconnected()
