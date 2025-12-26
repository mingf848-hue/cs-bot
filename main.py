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

# 尝试引入统计模块（如果有）
try:
    from work_stats import init_stats_blueprint
except ImportError:
    init_stats_blueprint = None

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
    # 移除所有标点符号和空白，只保留纯文本，用于严格匹配
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
    
    # 稍等关键词配置
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    clean_env = wait_keywords_env.replace("，", ",") 
    raw_wait = {normalize(x) for x in clean_env.split(',') if x.strip()}
    WAIT_SIGNATURES = {x for x in raw_wait if x} 

    # 跟进关键词配置
    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    if '|' in keep_keywords_env:
        keep_list = keep_keywords_env.split('|')
    else:
        keep_clean = keep_keywords_env.replace("，", ",")
        keep_list = keep_clean.split(',')
    raw_keep = {normalize(x) for x in keep_list if x.strip()}
    KEEP_SIGNATURES = {x for x in raw_keep if x}
    
    log_tree(0, f"🔍 关键词配置 (Normalized): WAIT={WAIT_SIGNATURES} | KEEP={KEEP_SIGNATURES}")

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
# 模块 3: 全局状态与缓存
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

# 映射表：CS回复的消息ID -> 客户原始消息ID
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
        if thread_id: kwargs['reply_to'] = thread_id
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
# 模块 4: Web UI (包含完整 HTML)
# ==========================================
app = Flask(__name__)

# 1. 仪表盘 HTML
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
        <h1>⚡️ 实时监控 (Ver 46.1 AI)</h1>
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
    <a href="/tool/wait_check" target="_blank" class="btn" style="margin-top:10px;background:#00695c">🛠️ 关键词闭环检测</a>
    <a href="/tool/ai_audit" target="_blank" class="btn" style="margin-top:10px;background:#d32f2f">🤖 AI 全能质检 (A1-A24)</a>
    <a href="/tool/work_stats" target="_blank" class="btn" style="margin-top:10px;background:#6a1b9a">📊 工作量统计</a>
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 46.1 (Triangle Fix + AI Rules)</div>
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

# 2. 日志查看器 HTML
LOG_VIEWER_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>系统日志流 | Log Viewer</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        :root { --bg-body: #0f172a; --bg-panel: #1e293b; --bg-input: #334155; --text-main: #f1f5f9; --text-muted: #94a3b8; --primary: #3b82f6; --user-bubble: #334155; --cs-bubble: #0f766e; --alert-bg: rgba(239, 68, 68, 0.15); --alert-border: #ef4444; --audit-bg: rgba(245, 158, 11, 0.15); --audit-border: #f59e0b; --shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3); }
        * { box-sizing: border-box; }
        body { background-color: var(--bg-body); color: var(--text-main); font-family: monospace; margin: 0; height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
        ::-webkit-scrollbar { width: 8px; } ::-webkit-scrollbar-track { background: var(--bg-body); } ::-webkit-scrollbar-thumb { background: var(--bg-input); border-radius: 4px; }
        .toolbar { background: rgba(15, 23, 42, 0.85); backdrop-filter: blur(12px); padding: 16px 24px; border-bottom: 1px solid var(--bg-input); display: flex; gap: 12px; align-items: center; z-index: 10; box-shadow: var(--shadow); }
        input { flex-grow: 1; background: var(--bg-panel); border: 1px solid var(--bg-input); color: var(--text-main); padding: 10px 16px; border-radius: 8px; transition: all 0.2s; }
        button { background: var(--bg-panel); color: var(--text-main); border: 1px solid var(--bg-input); padding: 10px 20px; border-radius: 8px; cursor: pointer; white-space: nowrap; }
        button:hover { background: var(--bg-input); }
        #log-container { flex-grow: 1; overflow-y: auto; padding: 24px; display: flex; flex-direction: column; gap: 8px; scroll-behavior: smooth; }
        .msg-row { display: flex; flex-direction: column; max-width: 100%; }
        .bubble { padding: 8px 12px; border-radius: 6px; font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-all; }
        .msg-user .bubble { background-color: var(--user-bubble); color: #e2e8f0; border-left: 3px solid #64748b; }
        .msg-cs .bubble { background-color: var(--cs-bubble); color: #f0fdfa; border-right: 3px solid #14b8a6; text-align: right; }
        .msg-alert .bubble { background: var(--alert-bg); color: #fca5a5; border: 1px solid var(--alert-border); }
        .msg-audit .bubble { background: var(--audit-bg); color: #fdba74; border: 1px solid var(--audit-border); }
        .msg-sys .bubble { color: var(--text-muted); font-size: 12px; }
        .pill { background: rgba(255,255,255,0.1); padding: 1px 4px; border-radius: 3px; cursor: pointer; }
        .pill:hover { background: rgba(255,255,255,0.2); }
        .highlight-row .bubble { box-shadow: 0 0 0 2px #fbbf24; }
    </style>
</head>
<body>
    <div class="toolbar">
        <input type="text" id="search" placeholder="🔍 输入 ID / 关键词..." onkeyup="if(event.key==='Enter') doSearch()">
        <button onclick="doSearch()">查找</button>
        <button onclick="window.location.reload()">🔄 刷新</button>
        <button onclick="scrollToBottom()">⬇️ 底部</button>
    </div>
    <div id="log-container">Loading...</div>
    <script>
        const container = document.getElementById('log-container');
        fetch('/log_raw?t=' + Date.now()).then(r => r.text()).then(text => {
            if(!text.trim()){ container.innerHTML = '<div>No logs</div>'; return; }
            const lines = text.split('\\n');
            let html = '';
            lines.forEach((line, idx) => {
                if(!line.trim()) return;
                let type = 'sys';
                if(line.includes('📦')) type = 'user';
                else if(line.includes('客服操作') || line.includes('⚡️')) type = 'cs';
                else if(line.includes('🚨')) type = 'alert';
                else if(line.includes('👮')) type = 'audit';
                
                let content = line.replace(/([a-zA-Z0-9_-]{15,})/g, '<span class="pill" onclick="searchId(\\'$1\\')">$1</span>');
                html += `<div class="msg-row msg-${type}" id="log-${idx}"><div class="bubble">${content}</div></div>`;
            });
            container.innerHTML = html;
            scrollToBottom();
        });
        function searchId(id) { document.getElementById('search').value = id; doSearch(); }
        function doSearch() {
            const term = document.getElementById('search').value.toLowerCase();
            if(!term) return;
            document.querySelectorAll('.highlight-row').forEach(el=>el.classList.remove('highlight-row'));
            const rows = Array.from(document.querySelectorAll('.bubble'));
            for(let row of rows.reverse()){
                if(row.innerText.toLowerCase().includes(term)){
                    row.parentElement.classList.add('highlight-row');
                    row.scrollIntoView({behavior:"smooth", block:"center"});
                    break;
                }
            }
        }
        function scrollToBottom() { container.scrollTop = container.scrollHeight; }
    </script>
</body>
</html>
"""

# 3. 闭环检测 HTML
WAIT_CHECK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>稍等关键词闭环检测</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, sans-serif; background: #f0f2f5; padding: 20px; max-width: 800px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        input[type="text"] { width: 100%; padding: 12px; margin: 10px 0; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; }
        button { background: #0088cc; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; width: 100%; font-weight: bold; }
        button:hover { background: #006699; }
        .result-item { padding: 15px; border-bottom: 1px solid #eee; background: #fff; margin-bottom: 10px; border-radius: 6px; }
        .status-closed { color: green; font-weight: bold; } .status-open { color: red; font-weight: bold; }
        .msg-text { background: #f5f5f5; padding: 8px; border-radius: 4px; margin: 5px 0; border-left: 3px solid #ccc; font-size: 14px; }
        .reason { color: #d32f2f; font-size: 13px; font-style: italic; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🔍 关键词闭环检测</h1>
        <input type="text" id="keyword" value="请稍等ART" placeholder="输入关键词...">
        <button onclick="startCheck()" id="btn-search">开始检测 (过去 10 小时)</button>
        <div id="status-text" style="margin-top:10px; text-align:center; color:#666;"></div>
    </div>
    <div id="result-list"></div>
    <script>
        async function startCheck() {
            const keyword = document.getElementById('keyword').value.trim();
            if (!keyword) return alert("请输入关键词");
            const btn = document.getElementById('btn-search');
            const resList = document.getElementById('result-list');
            const status = document.getElementById('status-text');
            
            btn.disabled = true; resList.innerHTML = ''; status.innerText = "正在扫描...";
            
            try {
                const response = await fetch(`/api/wait_check_stream?keyword=${encodeURIComponent(keyword)}`);
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    const lines = decoder.decode(value, {stream: true}).split('\\n');
                    for (const line of lines) {
                        if (!line.trim()) continue;
                        try {
                            const data = JSON.parse(line);
                            if (data.type === 'progress') status.innerText = data.msg;
                            else if (data.type === 'result') {
                                const div = document.createElement('div');
                                div.className = 'result-item';
                                div.innerHTML = `
                                    <div><span class="${data.is_closed?'status-closed':'status-open'}">${data.is_closed?'✅ 已闭环':'❌ 未闭环'}</span> ${data.time}</div>
                                    <div class="msg-text">${data.found_text}</div>
                                    ${data.reason ? `<div class="reason">${data.reason}</div>` : ''}
                                    <a href="${data.link}" target="_blank">🔗 跳转</a>
                                `;
                                resList.appendChild(div);
                            } else if (data.type === 'done') {
                                status.innerText = `检测完成: 共 ${data.total} 条，未闭环 ${data.open} 条`;
                                btn.disabled = false;
                            }
                        } catch(e){}
                    }
                }
            } catch (e) { status.innerText = "Error: " + e.message; btn.disabled = false; }
        }
    </script>
</body>
</html>
"""

# 4. AI 智能质检 HTML (Ver 46.1 新增)
AI_AUDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>AI 全能质检 (A1-A24)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, sans-serif; background: #f0f2f5; padding: 20px; max-width: 900px; margin: 0 auto; color: #333; }
        .card { background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); margin-bottom: 20px; }
        h1 { margin-top: 0; color: #1a1a1a; font-size: 1.5rem; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        .form-group { margin-bottom: 15px; }
        label { display: block; margin-bottom: 5px; font-weight: bold; }
        input[type="text"] { width: 100%; padding: 12px; border: 1px solid #ddd; border-radius: 6px; box-sizing: border-box; font-size: 16px; }
        button { background: #d32f2f; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 16px; width: 100%; font-weight: bold; transition: background 0.2s; }
        button:hover { background: #b71c1c; }
        button:disabled { background: #ccc; cursor: not-allowed; }
        
        #progress-container { margin-top: 20px; display: none; background: #fff3e0; padding: 15px; border-radius: 6px; border: 1px solid #ffe0b2; }
        #status-text { font-size: 14px; color: #e65100; text-align: center; }

        .result-item { padding: 15px; border-bottom: 1px solid #eee; background: #fff; margin-bottom: 10px; border-radius: 6px; border: 1px solid #eee; }
        .header-row { display: flex; justify-content: space-between; font-size: 12px; color: #888; margin-bottom: 8px; }
        .context-box { background: #fafafa; padding: 10px; border-radius: 4px; border: 1px solid #eee; font-family: monospace; font-size: 12px; white-space: pre-wrap; max-height: 150px; overflow-y: auto; color: #555; }
        
        .ai-verdict { margin-top: 10px; padding: 10px; border-radius: 4px; font-weight: bold; display: flex; align-items: flex-start; gap: 10px; }
        .ai-pass { background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
        .ai-fail { background: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
    </style>
</head>
<body>
    <div class="card">
        <h1>🤖 AI 全能质检 (A1-A24 增强版)</h1>
        <p style="font-size:13px; color:#666">
            此工具会自动抓取消息上下文，打包发给 AI，对照考核指标 A1/A3/A5/A6/A9/A13/A16/A23 等进行全方位判罚。
        </p>
        <div class="form-group">
            <label>1. 输入关键词 (例如: 请稍等ART)</label>
            <input type="text" id="keyword" value="请稍等ART">
        </div>
        <button onclick="startAudit()" id="btn-audit">🚀 开始 AI 审计 (过去 10 小时)</button>
        
        <div id="progress-container">
            <div id="status-text">准备就绪...</div>
        </div>
    </div>

    <div id="result-list"></div>

    <script>
        async function startAudit() {
            const keyword = document.getElementById('keyword').value.trim();
            if (!keyword) return alert("请输入关键词");
            
            const btn = document.getElementById('btn-audit');
            const pContainer = document.getElementById('progress-container');
            const pText = document.getElementById('status-text');
            const resList = document.getElementById('result-list');

            btn.disabled = true;
            pContainer.style.display = 'block';
            resList.innerHTML = '';
            pText.innerText = "正在扫描消息并调用 AI 分析 (可能较慢)...";

            try {
                const response = await fetch(`/api/ai_audit_stream?keyword=${encodeURIComponent(keyword)}`);
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
                                pText.innerText = data.msg;
                            } else if (data.type === 'audit_result') {
                                renderResult(data);
                            } else if (data.type === 'done') {
                                pText.innerText = `✅ 审计完成。共分析 ${data.total} 条对话。`;
                                btn.disabled = false;
                            }
                        } catch (e) { console.error(e); }
                    }
                }
            } catch (e) {
                pText.innerText = "错误: " + e.message;
                btn.disabled = false;
            }
        }

        function renderResult(data) {
            const div = document.createElement('div');
            div.className = 'result-item';
            
            let verdictHtml = '';
            if (data.ai_score.deducted) {
                verdictHtml = `<div class="ai-verdict ai-fail"><span>❌ 建议扣分</span><span>${data.ai_score.reason} (规则: ${data.ai_score.rule})</span></div>`;
            } else {
                verdictHtml = `<div class="ai-verdict ai-pass"><span>✅ 通过考核</span><span>${data.ai_score.reason}</span></div>`;
            }

            div.innerHTML = `
                <div class="header-row">
                    <span>📅 ${data.time}</span>
                    <span>📂 ${data.group}</span>
                    <a href="${data.link}" target="_blank">🔗 跳转</a>
                </div>
                <div class="context-box">${data.context_str}</div>
                ${verdictHtml}
            `;
            document.getElementById('result-list').appendChild(div);
        }
    </script>
</body>
</html>
"""

# ==========================================
# 模块 5: Flask 路由与 API
# ==========================================
@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, s=self_reply_timers, current_time=now)

@app.route('/log')
def log_ui(): return Response(LOG_VIEWER_HTML, mimetype='text/html')

@app.route('/tool/wait_check')
def wait_check_ui(): return Response(WAIT_CHECK_HTML, mimetype='text/html')

@app.route('/tool/ai_audit')
def ai_audit_ui(): return Response(AI_AUDIT_HTML, mimetype='text/html')

@app.route('/log_raw')
def log_raw():
    try:
        if not os.path.exists(LOG_FILE_PATH): return "Log file not created yet.", 200
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
# 模块 6: AI 核心逻辑 (增强版 Prompt)
# ==========================================
def _ai_check_reply_needed(text):
    # (轻量级：用于实时监控的快速判断)
    proxy_url = AI_PROXY_URL.rstrip('/')
    url = f"{proxy_url}/v1beta/models/{AI_MODEL_NAME}:generateContent"
    headers = {'Content-Type': 'application/json'}
    prompt = f"""
    判断客户最后回复是否需要跟进。
    客户消息："{text}"
    规则：礼貌结束语(好的/谢谢/ok)或情绪词(哈哈/嗯嗯)返回FALSE。明确问题返回TRUE。
    输出JSON: {{"need_reply": true/false, "reason": "..."}}
    """
    data = {"contents": [{"parts": [{"text": prompt}]}],"generationConfig": {"response_mime_type": "application/json"}}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=10)
        if resp.status_code == 200:
            ai_decision = json.loads(resp.json()['candidates'][0]['content']['parts'][0]['text'])
            return (ai_decision.get("need_reply", True), ai_decision.get("reason", ""))
    except: pass
    return (True, "AI Error")

def _ai_audit_context(context_text):
    """
    [AI-Audit] 全能质检模式 - 注入 A1-A24 完整规则
    """
    proxy_url = AI_PROXY_URL.rstrip('/')
    url = f"{proxy_url}/v1beta/models/{AI_MODEL_NAME}:generateContent"
    headers = {'Content-Type': 'application/json'}
    
    # 核心：将你的考核指标注入给 Gemini
    prompt = f"""
    你是一名严格的客服质检员。请根据对话流（含时间戳），对照以下【考核指标】判断客服是否违规。
    
    【核心考核指标】
    1. A1/A3/A4 (时效性): 
       - 客服回复“稍等”后，必须在30分钟内给出结果。若最后一条是“稍等”且距今超30分，判违规。
       - 客户提问后，客服若遗漏回复或超过30分钟未响应，判违规。
    2. A16 (闭环): 客服说“稍等”后若无后续结果回复，判违规。
    3. A5 (引导): 禁止无效引导（如仅回“看教程”），必须提供具体路径/截图。
    4. A6/A21 (推诿): 禁止直接让客户自己查（除非非本部门业务），禁止踢皮球。
    5. A13 (态度): 禁止反问、辱骂、嘲讽、个人情绪化表达。
    6. A23 (专业度): 禁止直接回答“不知道/不清楚”，应回复“请稍等，我确认下”。
    7. A9 (私聊): 禁止在群组引导私聊（特殊情况除外，若无上下文判违规）。
    8. A2/A14 (敏感信息): 禁止在群里直接发出或索要敏感信息（如完整IP、银行卡号），应打码或私发。

    【对话流】
    {context_text}
    
    请输出 JSON 格式: 
    {{
        "deducted": true/false, 
        "rule": "A3/A16/A13/...", 
        "reason": "简述违规理由"
    }}
    """
    data = {"contents": [{"parts": [{"text": prompt}]}],"generationConfig": {"response_mime_type": "application/json"}}
    try:
        resp = requests.post(url, json=data, headers=headers, timeout=15)
        if resp.status_code == 200:
            return json.loads(resp.json()['candidates'][0]['content']['parts'][0]['text'])
    except Exception as e:
        return {"deducted": False, "rule": "Error", "reason": f"AI 请求失败: {e}"}
    return {"deducted": False, "rule": "Unknown", "reason": "AI 无响应"}

async def _check_is_closed_logic(latest_msg):
    is_closed = False; reason = ""
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
    result_queue.put(json.dumps({"type": "done", "total": 0}))
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

@app.route('/api/ai_audit_stream')
def ai_audit_stream():
    keyword = request.args.get('keyword', '').strip()
    if not keyword: return "Keyword required", 400
    def generate():
        result_queue = queue.Queue()
        if not bot_loop: yield "Error: Bot loop not ready\n"; return
        asyncio.run_coroutine_threadsafe(perform_ai_audit_task(keyword, result_queue), bot_loop)
        while True:
            data = result_queue.get()
            if data is None: break
            yield data + "\n"
    return Response(stream_with_context(generate()), mimetype='text/plain')

async def perform_ai_audit_task(keyword, result_queue):
    try:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=10)
        found_count = 0
        for idx, chat_id in enumerate(CS_GROUP_IDS):
            if chat_id in [-1002807120955, -1002169616907]: continue
            result_queue.put(json.dumps({"type": "progress", "msg": f"正在扫描群组 {chat_id}..."}))
            try:
                history = []
                async for m in client.iter_messages(chat_id, limit=2000):
                    if m.date < cutoff_time: break
                    history.append(m)
                history.reverse()
                for m in history:
                    if not m.text: continue
                    if keyword in normalize(m.text):
                        is_cs = False
                        if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs = True
                        else:
                             s = await m.get_sender()
                             if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): is_cs = True
                        if not is_cs: continue
                        found_count += 1
                        thread_id = None
                        if m.reply_to: thread_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                        if not thread_id: thread_id = m.id
                        context_msgs = []
                        for hm in history:
                            cur_tid = None
                            if hm.reply_to: cur_tid = hm.reply_to.reply_to_top_id or hm.reply_to.reply_to_msg_id
                            if not cur_tid: cur_tid = hm.id
                            if cur_tid == thread_id: context_msgs.append(hm)
                        context_str = ""
                        for cm in context_msgs:
                            ts = cm.date.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M')
                            role = "客户"
                            s_obj = await cm.get_sender()
                            name = getattr(s_obj, 'first_name', 'Unknown')
                            if cm.sender_id in ([MY_ID] + OTHER_CS_IDS): role = "客服"
                            elif name.startswith(tuple(CS_NAME_PREFIXES)): role = "客服"
                            content = (cm.text or "[Media]").replace('\n', ' ')
                            context_str += f"[{ts}] {role}({name}): {content}\n"
                        result_queue.put(json.dumps({"type": "progress", "msg": f"AI 正在分析: {context_str[:20]}..."}))
                        ai_res = await asyncio.get_event_loop().run_in_executor(None, lambda: _ai_audit_context(context_str))
                        group_title = str(chat_id)
                        try: group_title = (await client.get_entity(chat_id)).title
                        except: pass
                        result_queue.put(json.dumps({
                            "type": "audit_result",
                            "time": m.date.astimezone(timezone(timedelta(hours=8))).strftime('%m-%d %H:%M'),
                            "group": group_title,
                            "context_str": context_str,
                            "ai_score": ai_res,
                            "link": f"https://t.me/c/{str(chat_id).replace('-100', '')}/{m.id}"
                        }))
            except Exception as e: logger.error(f"Audit group error: {e}")
        result_queue.put(json.dumps({"type": "done", "total": found_count}))
        result_queue.put(None)
    except Exception as e:
        logger.error(f"Audit Task Error: {e}")
        result_queue.put(None)

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)

# ==========================================
# 模块 7: 通知与消息检测
# ==========================================
def _post_request(url, payload):
    try: requests.post(url, json=payload, timeout=10)
    except: pass

async def send_alert(text, link, extra_log=""):
    if not BOT_TOKEN: return
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
        if not msg: return False 
        return True
    except: return True 

# ==========================================
# 模块 8: 任务管理与销单逻辑
# ==========================================
async def audit_pending_tasks():
    await send_alert(f"🏁 **下班巡检结束**", "")

async def perform_stop_work():
    global IS_WORKING
    if IS_WORKING: await audit_pending_tasks()
    IS_WORKING = False
    for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()) + list(self_reply_tasks.values()): t.cancel()
    wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear(); self_reply_tasks.clear()
    wait_timers.clear(); followup_timers.clear(); reply_timers.clear(); self_reply_timers.clear()
    wait_msg_map.clear(); followup_msg_map.clear()
    chat_user_active_msgs.clear(); chat_thread_active_msgs.clear()
    msg_to_user_cache.clear(); msg_content_cache.clear()
    group_to_user_cache.clear(); cs_activity_log.clear()
    await send_alert("🔴 **已切换为：下班模式**", "")

async def perform_start_work():
    global IS_WORKING
    IS_WORKING = True
    await send_alert(f"🟢 **已切换为：工作模式**", "")

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
    
    # 1. 精确打击
    if target_msg_id:
        if target_msg_id in wait_tasks or target_msg_id in followup_tasks or target_msg_id in reply_tasks or target_msg_id in self_reply_tasks:
            targets.add(target_msg_id); hit_specific = True
        if target_msg_id in wait_msg_map:
            targets.add(wait_msg_map[target_msg_id]); hit_specific = True
        if target_msg_id in followup_msg_map:
            targets.add(followup_msg_map[target_msg_id]); hit_specific = True
        if targets: hit_specific = True
        
    # 2. 三角修复：如果精确打击失败，但有 UserID，尝试回退到用户级销单
    should_check_user = False
    if not targets and target_msg_id and user_id: should_check_user = True
    
    # 3. 话题级销单
    if not hit_specific and thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs: targets.update(chat_thread_active_msgs[t_key])
        
    # 4. 用户级销单
    if (not hit_specific or should_check_user) and user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs: targets.update(chat_user_active_msgs[u_key])
        
    if not targets: return
    
    count = 0
    for mid in targets:
        if 'wait' in types and mid in wait_tasks: wait_tasks[mid].cancel(); count += 1
        if 'followup' in types and mid in followup_tasks: followup_tasks[mid].cancel(); count += 1
        if 'reply' in types and mid in reply_tasks: reply_tasks[mid].cancel(); count += 1
        if 'self_reply' in types and mid in self_reply_tasks: self_reply_tasks[mid].cancel(); count += 1
    if count > 0: log_tree(2, f"销单成功 | {reason} | 任务: {count}")

def check_recent_activity_safe(chat_id, task_start_time, user_ids=None, thread_id=None):
    buffer_seconds = 10
    if user_ids:
        for uid in user_ids:
            if cs_activity_log.get((chat_id, uid), 0) > task_start_time + buffer_seconds: return True, "有新回复"
    if thread_id:
        if cs_activity_log.get((chat_id, thread_id), 0) > task_start_time + buffer_seconds: return True, "有新回复"
    return False, None

# ==========================================
# 模块 9: 倒计时 Worker
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        end_time = trigger_timestamp + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)
        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return
        is_safe, _ = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe: return
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **稍等-超时预警**\n👤 客服: {agent_name}\n🔗 [点击处理]({link})", link)
        await asyncio.sleep(600)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return
        is_safe_2, _ = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe_2: return
        await send_alert(f"🔥 **严重超时警报**\n👤 客服: {agent_name}\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if key_id in wait_tasks: del wait_tasks[key_id]
        if key_id in wait_timers: del wait_timers[key_id]
        if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None):
    try:
        end_time = trigger_timestamp + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)
        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return
        is_safe, _ = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe: return
        await send_alert(f"📩 消息: `{original_text.replace('`', '')}`\n🚨 **跟进-超时预警**\n👤 客服: {agent_name}\n🔗 [点击处理]({link})", link)
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks: del followup_tasks[key_id]
        if key_id in followup_timers: del followup_timers[key_id]
        if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
        for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, trigger_timestamp, thread_id=None):
    try:
        end_time = trigger_timestamp + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link, 'target': target_name}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **漏回消息提醒**\n👤 用户: {sender_name}\n🔗 [点击回复]({link})", link)
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in reply_tasks: del reply_tasks[trigger_msg_id]
        if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
        remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

async def task_self_reply_timeout(trigger_msg_id, user_name, content, link, chat_id, user_id, trigger_timestamp, thread_id=None):
    try:
        end_time = trigger_timestamp + SELF_REPLY_TIMEOUT
        self_reply_timers[trigger_msg_id] = {'ts': end_time, 'user': user_name, 'url': link}
        await asyncio.sleep(SELF_REPLY_TIMEOUT)
        if not IS_WORKING: return
        await send_alert(f"📩 内容: `{content.replace('`', '')}`\n🔔 **自回防漏监测**\n👤 用户: {user_name}\n🔗 [点击回复]({link})", link)
    except asyncio.CancelledError: pass 

# ==========================================
# 模块 10: 客户端事件处理
# ==========================================
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH, device_model="Mac mini M2", app_version="5.8.3", system_version="macOS 15.6.1", lang_code="zh-hans", system_lang_code="zh-hans")

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    cmd = event.text.strip()
    if cmd == '下班': await perform_stop_work()
    elif cmd == '上班': await perform_start_work()
    elif cmd == '状态': await send_alert(f"🟢 状态: {'Working' if IS_WORKING else 'Stopped'}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not IS_WORKING: return
    for msg_id in event.deleted_ids:
        deleted_cache.append(msg_id)
        if msg_id in wait_tasks: wait_tasks[msg_id].cancel()
        if msg_id in wait_msg_map:
            target_id = wait_msg_map[msg_id]
            if target_id in wait_tasks: wait_tasks[target_id].cancel()
            del wait_msg_map[msg_id]
        if msg_id in followup_tasks: followup_tasks[msg_id].cancel()
        if msg_id in followup_msg_map:
            target_id = followup_msg_map[msg_id]
            if target_id in followup_tasks: followup_tasks[target_id].cancel()
            del followup_msg_map[msg_id]
        if msg_id in reply_tasks: reply_tasks[msg_id].cancel()
        if msg_id in self_reply_tasks: self_reply_tasks[msg_id].cancel()

async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    if (chat_id, reply_to_msg_id) in msg_to_user_cache: return msg_to_user_cache[(chat_id, reply_to_msg_id)]
    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if msgs and msgs[0]:
            sender_id = msgs[0].sender_id
            if sender_id and sender_id not in ([MY_ID] + OTHER_CS_IDS):
                update_msg_cache(chat_id, reply_to_msg_id, sender_id, msgs[0].grouped_id)
            return sender_id
    except: pass
    return None

async def get_context_users(chat_id, msg_id):
    users = set()
    try:
        msgs = await client.get_messages(chat_id, ids=[msg_id])
        if msgs and msgs[0]:
            msg = msgs[0]
            if msg.sender_id: users.add(msg.sender_id)
            if msg.reply_to_msg_id:
                p = await get_traceable_sender(chat_id, msg.reply_to_msg_id)
                if p: users.add(p)
    except: pass
    cs_ids = [MY_ID] + OTHER_CS_IDS
    return [u for u in users if u not in cs_ids]

@client.on(events.NewMessage(chats=CS_GROUP_IDS))
@client.on(events.MessageEdited(chats=CS_GROUP_IDS))
async def handler(event):
    try:
        global MY_ID
        if not MY_ID: MY_ID = (await client.get_me()).id
        if not IS_WORKING: return
        msg_timestamp = event.date.timestamp()
        text = event.text or ""
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
            if (chat_id, reply_to_msg_id) in msg_to_user_cache: real_customer_id = msg_to_user_cache[(chat_id, reply_to_msg_id)]
            if not real_customer_id and reply_to_msg_id in wait_msg_map:
                wait_origin_msg = wait_msg_map[reply_to_msg_id]
                for (cid, uid), msg_set in chat_user_active_msgs.items():
                    if cid == chat_id and wait_origin_msg in msg_set: real_customer_id = uid; break
            if not real_customer_id: real_customer_id = await get_traceable_sender(chat_id, reply_to_msg_id)

        if is_sender_cs:
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id, timestamp=msg_timestamp)
            if isinstance(event, events.MessageEdited):
                 if real_customer_id or current_thread_id:
                     cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服编辑")
            
            # 智能销单 (含三角修复)
            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, thread_id=current_thread_id, target_msg_id=reply_to_msg_id, reason=f"客服回复")
            
            if reply_to_msg_id and reply_to_msg_id in reply_tasks: reply_tasks[reply_to_msg_id].cancel(); del reply_tasks[reply_to_msg_id]
            if reply_to_msg_id and reply_to_msg_id in self_reply_tasks: self_reply_tasks[reply_to_msg_id].cancel(); del self_reply_tasks[reply_to_msg_id]

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id: related_users = [real_customer_id]
                if related_users:
                    if is_keep_cmd:
                        should_monitor_keep = await check_wait_in_history(chat_id, current_thread_id)
                        if should_monitor_keep:
                            if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel(); del wait_tasks[reply_to_msg_id]
                            if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel(); del followup_tasks[reply_to_msg_id]
                            task = asyncio.create_task(task_followup_timeout(reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, msg_timestamp, current_thread_id))
                            followup_tasks[reply_to_msg_id] = task; followup_msg_map[event.id] = reply_to_msg_id
                    elif is_wait_cmd:
                        if reply_to_msg_id in followup_tasks: followup_tasks[reply_to_msg_id].cancel(); del followup_tasks[reply_to_msg_id]
                        if reply_to_msg_id in wait_tasks: wait_tasks[reply_to_msg_id].cancel(); del wait_tasks[reply_to_msg_id]
                        task = asyncio.create_task(task_wait_timeout(reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, msg_timestamp, current_thread_id))
                        wait_tasks[reply_to_msg_id] = task; wait_msg_map[event.id] = reply_to_msg_id
        else:
            if isinstance(event, events.MessageEdited): return
            update_msg_cache(chat_id, event.id, sender_id, grouped_id)
            cancel_tasks(chat_id, sender_id, current_thread_id, reason=f"客户发言", types=['reply'])
            if reply_to_msg_id and real_customer_id:
                if sender_id == real_customer_id:
                     if normalize(text.strip()) not in IGNORE_SIGNATURES:
                         should_monitor = True
                         if grouped_id:
                             if grouped_id in self_reply_dedup: should_monitor = False
                             else: self_reply_dedup.append(grouped_id)
                         if should_monitor:
                             has_wait = await check_wait_in_history(chat_id, current_thread_id)
                             if has_wait:
                                 cancel_tasks(chat_id, sender_id, current_thread_id, reason="新自回", types=['self_reply'])
                                 register_task(chat_id, sender_id, event.id, current_thread_id)
                                 task = asyncio.create_task(task_self_reply_timeout(event.id, sender_name, text[:50], msg_link, chat_id, sender_id, msg_timestamp, current_thread_id))
                                 def cleanup_self_reply(_):
                                     if event.id in self_reply_tasks: del self_reply_tasks[event.id]
                                     if event.id in self_reply_timers: del self_reply_timers[event.id]
                                     remove_task_record(chat_id, sender_id, event.id, current_thread_id)
                                 task.add_done_callback(cleanup_self_reply)
                                 self_reply_tasks[event.id] = task
            if reply_to_msg_id:
                try:
                    target_id = None; target_name = "未知客服"
                    replied_msg = await event.get_reply_message()
                    if replied_msg: target_id = replied_msg.sender_id
                    if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                        if normalize(text.strip()) in IGNORE_SIGNATURES: return
                        if event.id in reply_tasks: reply_tasks[event.id].cancel()
                        task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link, chat_id, sender_id, target_name, msg_timestamp, current_thread_id))
                        reply_tasks[event.id] = task
                except: pass
    except Exception as e: logger.error(f"Handler Error: {e}")

if __name__ == '__main__':
    try:
        delay = int(os.environ.get("STARTUP_DELAY", 120))
        if delay > 0: time.sleep(delay)
        bot_loop = asyncio.get_event_loop()
        bot_loop.create_task(maintenance_task())
        if init_stats_blueprint: init_stats_blueprint(app, client, bot_loop, CS_GROUP_IDS)
        Thread(target=run_web).start()
        client.start()
        client.run_until_disconnected()
    except Exception as e:
        logger.critical(f"Startup Error: {e}")
        sys.exit(1)
