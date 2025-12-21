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

    # [Ver 36.2] 扩展忽略关键词库 (包含用户请求的新增词汇)
    default_ignore = (
        "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴,"
        "好的呢,嗯,嗯嗯,谢了,okk,k,行,妥,了解,已收,没问题,好的收到,ok了,麻烦了"
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
MAX_CACHE_SIZE = 50000 

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
wait_timers = {}
followup_timers = {}
reply_timers = {}
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
    {% for title, timers in [('⏳ 稍等 (12m)', w), ('🕵️ 跟进 (15m)', f), ('🔔 漏回 (5m)', r)] %}
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
    <div style="text-align:center;color:#ccc;margin-top:30px;font-size:0.8rem">Ver 36.2 (Last Msg Audit)</div>
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
<html>
<head>
    <title>日志流</title>
    <style>
        :root { --bg: #121212; --bg-card: #1e1e1e; --text-main: #e0e0e0; --text-sub: #a0a0a0; --accent: #bb86fc; --user-msg: #263238; --cs-msg: #1b5e20; --alert: #b00020; --audit: #ff6f00; }
        body { background: var(--bg); color: var(--text-main); font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
        .toolbar { background: var(--bg-card); padding: 12px; display: flex; gap: 10px; border-bottom: 1px solid #333; box-shadow: 0 2px 4px rgba(0,0,0,0.5); z-index: 100; }
        input { background: #2c2c2c; border: 1px solid #444; color: #fff; padding: 8px 12px; border-radius: 6px; flex-grow: 1; outline: none; font-size: 14px; }
        input:focus { border-color: var(--accent); }
        button { background: var(--accent); color: #000; border: none; padding: 8px 16px; cursor: pointer; border-radius: 6px; font-weight: bold; }
        button:hover { opacity: 0.9; }
        #log-container { flex-grow: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 12px; }
        .msg-row { display: flex; flex-direction: column; width: 100%; position: relative; }
        .msg-meta { font-size: 12px; color: #666; margin-bottom: 4px; margin-left: 10px; font-family: monospace; display: flex; align-items: center; gap: 8px; }
        .bubble { max-width: 80%; padding: 12px 16px; border-radius: 12px; font-size: 14px; line-height: 1.5; word-wrap: break-word; position: relative; white-space: pre-wrap; box-shadow: 0 1px 2px rgba(0,0,0,0.3); }
        .msg-user .bubble { background-color: var(--user-msg); align-self: flex-start; border-bottom-left-radius: 2px; color: #eceff1; border-left: 3px solid #607d8b; }
        .msg-user .msg-meta { justify-content: flex-start; }
        .msg-cs .bubble { background-color: var(--cs-msg); align-self: flex-end; border-bottom-right-radius: 2px; color: #e8f5e9; border-right: 3px solid #66bb6a; }
        .msg-cs .msg-meta { justify-content: flex-end; margin-right: 10px; }
        .msg-cs { align-items: flex-end; }
        .msg-sys { align-items: center; margin: 5px 0; }
        .msg-sys .bubble { background: transparent; color: var(--text-sub); font-size: 12px; font-family: monospace; padding: 4px 10px; border: 1px solid #333; max-width: 90%; }
        .msg-alert .bubble { background-color: rgba(176, 0, 32, 0.2); border: 1px solid var(--alert); color: #ff8a80; width: 90%; text-align: center; }
        .msg-audit .bubble { background-color: rgba(255, 111, 0, 0.15); border: 1px solid var(--audit); color: #ffb74d; width: 90%; text-align: center; font-weight: bold; }
        .pill { display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 11px; margin: 0 2px; cursor: pointer; border: 1px solid rgba(255,255,255,0.1); background: rgba(0,0,0,0.3); }
        .pill:hover { background: rgba(255,255,255,0.1); }
        .highlight-row .bubble { box-shadow: 0 0 0 2px #ffd700, 0 0 15px rgba(255, 215, 0, 0.3); z-index: 2; }
        .btn-report { font-size: 11px; padding: 2px 8px; border-radius: 4px; cursor: pointer; border: 1px solid transparent; font-weight: bold; transition: all 0.2s; opacity: 0.7; }
        .btn-report:hover { opacity: 1; transform: scale(1.05); }
        .btn-missed { background: #ff9800; color: #000; border-color: #f57c00; }
        .btn-false { background: #f44336; color: #fff; border-color: #d32f2f; }
        .error-msg { color: #cf6679; text-align: center; padding: 20px; font-weight: bold; }
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
            // [Fix JS Escaping] Escape \d and \s for JS regex
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
                // [Fix JS Escaping] Escape \d and \s for JS regex
                const idRegex = /(Msg|User|Thread|流|归属|用户)[:=]?\\s?(\\d+)/g;
                let match;
                while ((match = idRegex.exec(content)) !== null) { ids.push(match[2]); }
                let idsStr = ids.join(',');

                if (raw.includes('📦')) { type = 'user'; content = content.replace('📦', '').trim(); }
                else if (raw.includes('客服操作') || (raw.includes('⚡️') && raw.includes('┣━━'))) { type = 'cs'; content = content.replace(/[┣┗]━━/, '').replace('⚡️', '⚡️ ').trim(); }
                else if (raw.includes('🚨') || raw.includes('[ALERT]')) { type = 'alert'; }
                else if (raw.includes('👮') || raw.includes('[AUDIT]')) { type = 'audit'; }
                else if (raw.includes('┣━━') || raw.includes('┗━━')) { type = 'sys'; }

                // [Fix JS Escaping] Escape \d, \s and quotes inside replacement string
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
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, current_time=now)

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
# [Ver 36.2] 增强巡检: 扫描过去 30 小时的消息
async def audit_pending_tasks():
    log_tree(4, "开始执行【下班巡检】...")
    await send_alert("👮 **开始执行下班自动巡检...**\n正在扫描最近活跃的消息流，检查是否有遗漏...", "")
    
    issues_found = 0
    # SCAN_LIMIT = 600 # 废弃固定 Limit
    
    # 计算 30 小时前的时间戳
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=30)
    
    # [Ver 36.2] 定义不参与巡检的黑名单群组
    EXCLUDED_GROUPS = [-1002169616907]

    for chat_id in CS_GROUP_IDS:
        # [Ver 36.2] 群组黑名单过滤
        if chat_id in EXCLUDED_GROUPS:
             log_tree(4, f"🚫 跳过群组 {chat_id} (黑名单)")
             continue

        try:
            log_tree(4, f"正在扫描群组 {chat_id} (最近 30 小时)...")
            history = []
            
            # 使用 iter_messages 按时间回溯
            # 设定 limit=5000 作为极端活跃群组的兜底，防止无限扫描
            async for m in client.iter_messages(chat_id, limit=5000):
                # 检查消息时间，如果早于 cutoff_time 则停止
                if m.date and m.date < cutoff_time:
                    break
                history.append(m)
            
            threads_map = defaultdict(list)
            # MsgID -> SenderID Map for quick lookup in this batch
            msg_sender_map = {m.id: m.sender_id for m in history}
            
            for m in history:
                thread_id = None
                if m.reply_to:
                    thread_id = m.reply_to.reply_to_top_id 
                    if not thread_id: thread_id = m.reply_to.reply_to_msg_id
                if not thread_id: thread_id = m.id
                threads_map[thread_id].append(m)
            
            for t_id, msgs in threads_map.items():
                if not msgs: continue

                # ==========================================
                # [Ver 36.2] 新增检测: 客户最后一条消息未回复
                # ==========================================
                latest_msg = msgs[0] # history 中 index 0 是最新的
                is_latest_cs = await is_official_cs(latest_msg)
                
                # 如果最新的一条消息不是客服发的（即客户发的），并且内容不是"谢谢"之类
                # 则认为有遗漏回复
                found_customer_no_reply = False
                
                if not is_latest_cs:
                    text_norm = normalize(latest_msg.text or "")
                    if text_norm and text_norm not in IGNORE_SIGNATURES:
                         found_customer_no_reply = True
                         
                         issues_found += 1
                         
                         root_text = (latest_msg.text or "[媒体文件]")[:50]
                         link = ""
                         if latest_msg.reply_to and latest_msg.reply_to.reply_to_top_id:
                              link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{latest_msg.id}?thread={latest_msg.reply_to.reply_to_top_id}"
                         else:
                              link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{latest_msg.id}"
                         
                         log_tree(4, f"❌ 发现遗漏(客户未回) | Msg={latest_msg.id} | Text={root_text} | Link={link}")
                         await send_alert(
                            f"👮 **下班巡检-发现遗漏**\n"
                            f"⚠️ 类型: **客户最后发言未回复**\n"
                            f"💬 客户消息: {root_text}\n"
                            f"🔗 [点击跳转对话]({link})", 
                            link,
                            f"Msg={latest_msg.id}"
                         )
                         await asyncio.sleep(1)
                
                # 如果已经发现了客户未回复的问题，跳过后续的"稍等"闭环检查，避免重复报警
                if found_customer_no_reply:
                    continue

                # ==========================================
                # 原有检测: 稍等未闭环
                # ==========================================
                last_wait_msg = None
                last_wait_idx = -1
                
                # 找到该 Thread 中最后一条客服发出的“稍等”
                for i, m in enumerate(msgs):
                    if await is_official_cs(m):
                        text = normalize(m.text or "")
                        # [Ver 35.0 Fix] Skip empty string matches to avoid image false positives
                        if not text: continue
                        
                        is_wait = any(k in text for k in WAIT_SIGNATURES)
                        is_keep = normalize(text) in KEEP_SIGNATURES 
                        
                        if is_wait or is_keep:
                            last_wait_msg = m
                            last_wait_idx = i
                            break 
                
                if last_wait_msg:
                    has_closed = False
                    if last_wait_idx > 0:
                        newer_msgs = msgs[:last_wait_idx]
                        for nm in newer_msgs:
                             if await is_official_cs(nm):
                                 # 检查是否为闭环消息
                                 # 注意：如果 nm 本身也是 Wait/Keep，外层循环会先找到它，所以这里的 nm 通常是回复/图片
                                 
                                 if nm.reply_to:
                                     target_id = nm.reply_to.reply_to_msg_id
                                     target_sender = msg_sender_map.get(target_id)
                                     
                                     # 1. 直接引用了这条“稍等”
                                     if target_id == last_wait_msg.id:
                                         has_closed = True; break
                                     
                                     # 2. 引用了“稍等”所回复的那条（即客户原消息）
                                     if last_wait_msg.reply_to and target_id == last_wait_msg.reply_to.reply_to_msg_id:
                                         has_closed = True; break
                                     
                                     # 3. [Ver 35.0] 引用了任意客户消息 (泛化严格模式)
                                     # 只要回复的目标不是客服，就算处理了
                                     if target_sender:
                                         is_target_cs = (target_sender == MY_ID) or (target_sender in OTHER_CS_IDS)
                                         if not is_target_cs:
                                             has_closed = True; break
                                     else:
                                         # 如果找不到目标消息（可能在缓存外），但它确实是一条回复
                                         # 为了防止像图片回复这种场景被误报，我们倾向于认为它是有效的回复
                                         # 只要不是回复自己（已知ID），就认为是回复客户
                                         has_closed = True; break

                    if not has_closed:
                        m = last_wait_msg
                        
                        # 死单检查
                        if m.reply_to and m.reply_to.reply_to_msg_id:
                            reply_id = m.reply_to.reply_to_msg_id
                            original_in_history = False
                            for hm in history:
                                if hm.id == reply_id:
                                    original_in_history = True
                                    break
                            if not original_in_history:
                                try:
                                    origin_msg = await client.get_messages(chat_id, ids=reply_id)
                                    if not origin_msg:
                                        log_tree(4, f"🛡️ 拦截误报 [巡检] | Msg={m.id} | 原因: 原消息已删除")
                                        continue
                                except Exception: continue

                        issues_found += 1
                        cs_name = "未知客服"
                        try:
                            sender = await m.get_sender()
                            if sender: cs_name = getattr(sender, 'first_name', 'Unknown')
                        except: pass

                        root_text = "无法获取源头"
                        root_msg = msgs[-1]
                        if root_msg: root_text = (root_msg.text or "[媒体文件]")[:50]

                        link = ""
                        if m.reply_to and m.reply_to.reply_to_top_id:
                             link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{m.id}?thread={m.reply_to.reply_to_top_id}"
                        else:
                             link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{m.id}"

                        debug_id_str = f"Msg={m.id}"
                        safe_text = (m.text or "[媒体]")[:50]
                        
                        log_tree(4, f"❌ 发现遗漏(稍等未闭环) | Msg={m.id} | CS={cs_name} | RootText={root_text} | Link={link}")
                        await send_alert(
                            f"👮 **下班巡检-发现遗漏**\n"
                            f"⚠️ 类型: **反馈核实未回复**\n"
                            f"👤 客服: {cs_name}\n"
                            f"💬 最后的回复: {safe_text}\n"
                            f"❓ 客户源头: {root_text}\n"
                            f"🔗 [点击跳转对话]({link})", 
                            link,
                            debug_id_str
                        )
                        await asyncio.sleep(1)

        except Exception as e:
            log_tree(9, f"群组 {chat_id} 巡检失败: {e}")

    log_tree(4, f"巡检结束，共发现 {issues_found} 个问题。")
    await send_alert(f"🏁 **下班巡检结束**\n共发现 **{issues_found}** 个未闭环的对话。", "")

async def perform_stop_work():
    global IS_WORKING
    if IS_WORKING:
        await audit_pending_tasks()
    IS_WORKING = False
    for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()): t.cancel()
    wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear()
    wait_timers.clear(); followup_timers.clear(); reply_timers.clear()
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

def cancel_tasks(chat_id, user_id, thread_id=None, reason="未知", types=None):
    if types is None: types = ['wait', 'followup', 'reply'] # Default to all
    
    targets = set()
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            targets.update(chat_user_active_msgs[u_key])
            if len(types) == 3: del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])
            if len(types) == 3: del chat_thread_active_msgs[t_key]

    if not targets: return

    log_tree(1, f" ┣━━ 尝试销单 | 用户: {user_id} | 流: {thread_id} | 类型: {types} | 任务池: {list(targets)}")
    count = 0
    cleared_ids = []
    for mid in targets:
        if 'wait' in types and mid in wait_tasks: wait_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'followup' in types and mid in followup_tasks: followup_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'reply' in types and mid in reply_tasks: reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
    
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
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])
        
        log_tree(1, f"启动 [稍等] 倒计时 (12m) {ids_str} | Thread={thread_id}")
        end_time = task_start_time + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id)
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

        is_safe_2, safe_reason_2 = check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id)
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

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, thread_id=None):
    task_start_time = time.time()
    try:
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])

        log_tree(1, f"启动 [跟进] 倒计时 (15m) {ids_str} | Thread={thread_id}")
        end_time = task_start_time + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, task_start_time, user_ids_list, thread_id)
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

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, thread_id=None):
    try:
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [漏回] 监控 (5m) {ids_str} | Target={target_name} | Thread={thread_id}")
        end_time = time.time() + REPLY_TIMEOUT
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
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}", "")

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
        # [Ver 34.0] KEEP = EXACT MATCH (Normalized), WAIT = CONTAINS
        is_keep_cmd = normalize(text) in KEEP_SIGNATURES 
        is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS)

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
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id)
            
            # [Ver 34.0] Edit Re-trigger Fix
            if isinstance(event, events.MessageEdited):
                 # Only cancel if editing to "Done" status
                 if real_customer_id or current_thread_id:
                     cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服编辑: [{text[:100]}...]")
                 return

            if reply_to_msg_id:
                source_info = "未知"
                if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
                elif real_customer_id: source_info = "API实时查询"
                else: source_info = "追踪失败" # [Ver 28.3] 明确失败状态
                
                log_tree(1, f"⚡️ 客服操作捕获 | Msg: {reply_to_msg_id} | 客服: {sender_name} | 内容: [{text[:100]}] | 归属: {real_customer_id} | 流: {current_thread_id} | 状态: {source_info}")

            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, current_thread_id, reason=f"客服回复: [{text[:100]}...]")
            
            if reply_to_msg_id and reply_to_msg_id in reply_tasks:
                reply_tasks[reply_to_msg_id].cancel()
                del reply_tasks[reply_to_msg_id]

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id:
                    related_users = [real_customer_id]

                if related_users:
                    if is_keep_cmd:
                        # [Fix Ver 36.1] 强制冲突检测：如果针对同一条消息已有 [稍等] 任务，立即销毁
                        # 解决并发导致 cancel_tasks 未能及时生效的问题
                        if reply_to_msg_id in wait_tasks:
                            wait_tasks[reply_to_msg_id].cancel()
                            del wait_tasks[reply_to_msg_id]
                            # 同时清理关联的 timer 和 map，防止残留
                            if reply_to_msg_id in wait_timers: del wait_timers[reply_to_msg_id]
                            log_tree(1, f"🔄 [跟进] 覆盖并销毁 [稍等] | Msg={reply_to_msg_id}")

                        # [Fix Ver 36.1] 自身去重：如果有旧的 [跟进]，也销毁
                        if reply_to_msg_id in followup_tasks:
                            followup_tasks[reply_to_msg_id].cancel()
                            del followup_tasks[reply_to_msg_id]

                        task = asyncio.create_task(task_followup_timeout(
                            reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, current_thread_id
                        ))
                        followup_tasks[reply_to_msg_id] = task
                        followup_msg_map[event.id] = reply_to_msg_id

                    elif is_wait_cmd:
                        # [Fix Ver 36.1] 强制冲突检测：防止“稍等”覆盖“跟进” (如果并发发生，跟进优先？不，后者优先)
                        # 如果用户先发跟进再发稍等，通常不建议，但逻辑上稍等应该覆盖跟进
                        if reply_to_msg_id in followup_tasks:
                            followup_tasks[reply_to_msg_id].cancel()
                            del followup_tasks[reply_to_msg_id]
                            log_tree(1, f"🔄 [稍等] 覆盖并销毁 [跟进] | Msg={reply_to_msg_id}")

                        if reply_to_msg_id in wait_tasks:
                            wait_tasks[reply_to_msg_id].cancel()
                            del wait_tasks[reply_to_msg_id]

                        task = asyncio.create_task(task_wait_timeout(
                            reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, current_thread_id
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
            
            log_tree(0, f"Msg={event.id} | User={sender_id} | [{chat_id}] {sender_name}: {text} [{msg_type}]")
            
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
                        task = asyncio.create_task(task_reply_timeout(event.id, sender_name, text[:50], msg_link, chat_id, sender_id, target_name, current_thread_id))
                        reply_tasks[event.id] = task
                except Exception as e:
                    log_tree(9, f"❌ Reply Check Error: {e}")

    except Exception as e:
        log_tree(9, f"❌ Handler 异常: {e}")

if __name__ == '__main__':
    try:
        # [Ver 31.2] 启动延迟
        delay = int(os.environ.get("STARTUP_DELAY", 20))
        if delay > 0:
            logger.info(f"⏳ 启动延迟: 等待 {delay} 秒以确保旧连接断开...")
            time.sleep(delay)
            
        bot_loop = asyncio.get_event_loop()
        bot_loop.create_task(maintenance_task())
        Thread(target=run_web).start()
        log_tree(0, "✅ 系统启动 (Ver 36.1 Fix Concurrency)")
        client.start()
        client.run_until_disconnected()
    except AuthKeyDuplicatedError:
        logger.critical("🚨 严重错误: SESSION_STRING 已失效！检测到多地登录冲突。")
        logger.critical("👉 请重新生成 SESSION_STRING 并更新环境变量。")
        sys.exit(1)
    except Exception as e:
        log_tree(9, f"❌ 启动失败: {e}")
