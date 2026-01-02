import asyncio
import logging
import time
import random
import json
import os
import re
from flask import request, jsonify, Response
from telethon import events

try: import redis
except ImportError: redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"
global_main_handler = None

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "default_rule",
            "name": "示例规则",
            "groups": [-1002169616907],
            "keywords": ["对比上时段缺少"],
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [{"text": "请稍等ART", "min": 3, "max": 5}]
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
redis_client = None

def init_redis_connection():
    global redis_client
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PUBLIC_URL")
    if redis and redis_url:
        try:
            redis_client = redis.from_url(redis_url, decode_responses=True)
            logger.info("✅ [Monitor] Redis 数据库连接成功")
        except Exception as e:
            logger.error(f"❌ [Monitor] Redis 连接失败: {e}")
            redis_client = None

def load_config(system_cs_prefixes):
    global current_config
    loaded = False
    if redis_client:
        try:
            data = redis_client.get(REDIS_KEY)
            if data:
                saved = json.loads(data)
                if "rules" in saved:
                    current_config = saved
                    loaded = True
                    logger.info("📥 [Monitor] 已从 Redis 加载配置")
        except: pass

    if not loaded and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if "rules" in saved:
                    current_config = saved
                    loaded = True
                    logger.info("📂 [Monitor] 已从本地文件加载配置")
        except: pass

    if not loaded: current_config = DEFAULT_CONFIG.copy()
    
    for rule in current_config["rules"]:
        if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
            rule["sender_prefixes"] = list(system_cs_prefixes)

def save_config(new_config):
    global current_config
    try:
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式"

        for rule in new_config.get("rules", []):
            clean_groups = []
            raw_groups = rule.get("groups", [])
            if isinstance(raw_groups, str): raw_groups = raw_groups.split('\n')
            for g in raw_groups:
                g_str = str(g).strip()
                match = re.search(r'-?\d+', g_str)
                if match:
                    try: clean_groups.append(int(match.group()))
                    except: pass
            rule["groups"] = clean_groups
            
            try: rule["cooldown"] = int(rule.get("cooldown", 60))
            except: rule["cooldown"] = 60
            for r in rule.get("replies", []):
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
        
        if redis_client:
            try: redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
            except: pass
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        
        current_config = new_config
        logger.info(f"💾 [Monitor] 配置已更新并保存")
        return True, "保存成功"
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存失败: {e}")
        return False, str(e)

# --- Web UI (Professional SaaS Style) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AutoResponder Dashboard</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    
    <style>
        :root {
            --primary: #000000; /* Vercel Black */
            --primary-hover: #333333;
            --accent: #0070f3; /* Azure Blue */
            --bg-page: #FAFAFA;
            --bg-card: #FFFFFF;
            --text-main: #171717;
            --text-sub: #666666;
            --border: #EAEAEA;
            --border-hover: #999;
            --danger: #E00;
            --success: #0070f3;
            --shadow-card: 0 5px 10px rgba(0,0,0,0.04);
            --shadow-hover: 0 8px 30px rgba(0,0,0,0.08);
            --radius: 8px;
        }
        
        * { box-sizing: border-box; }
        
        body { 
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; 
            background: var(--bg-page); 
            color: var(--text-main); 
            margin: 0; 
            padding-top: 80px; /* Space for fixed header */
            font-size: 14px;
            -webkit-font-smoothing: antialiased;
        }
        
        /* Fixed Header with Blur */
        .navbar {
            position: fixed; top: 0; left: 0; right: 0; height: 64px;
            background: rgba(255, 255, 255, 0.8); backdrop-filter: saturate(180%) blur(12px);
            border-bottom: 1px solid var(--border); z-index: 100;
            display: flex; justify-content: center;
        }
        .nav-content {
            width: 100%; max-width: 1200px; padding: 0 24px;
            display: flex; justify-content: space-between; align-items: center;
        }
        .brand { font-weight: 700; font-size: 18px; display: flex; align-items: center; gap: 10px; letter-spacing: -0.5px; }
        .brand-icon { width: 24px; height: 24px; background: var(--text-main); border-radius: 6px; display:flex; align-items:center; justify-content:center; color:white; font-size:14px; }
        
        .nav-actions { display: flex; align-items: center; gap: 16px; }
        
        /* Main Container */
        .container { max-width: 1200px; margin: 0 auto; padding: 0 24px 40px; }
        
        /* Switch Component */
        .toggle-switch { display: flex; align-items: center; gap: 8px; font-weight: 500; font-size: 13px; color: var(--text-sub); cursor: pointer; }
        .switch-base { 
            width: 36px; height: 20px; background: #EAEAEA; border-radius: 20px; 
            position: relative; transition: 0.3s; 
        }
        .switch-base::after {
            content: ''; position: absolute; left: 2px; top: 2px; width: 16px; height: 16px; 
            background: white; border-radius: 50%; box-shadow: 0 1px 2px rgba(0,0,0,0.1); 
            transition: 0.3s;
        }
        input:checked + .switch-base { background: var(--success); }
        input:checked + .switch-base::after { transform: translateX(16px); }
        
        /* Button Styles */
        .btn {
            height: 36px; padding: 0 16px; border-radius: 6px; font-weight: 500; font-size: 13px;
            cursor: pointer; border: 1px solid transparent; transition: all 0.2s;
            display: inline-flex; align-items: center; justify-content: center; gap: 6px;
        }
        .btn-primary { background: var(--text-main); color: white; border-color: var(--text-main); }
        .btn-primary:hover { background: #333; }
        .btn-outline { background: white; border-color: var(--border); color: var(--text-main); }
        .btn-outline:hover { border-color: var(--text-main); }
        .btn-danger-ghost { background: transparent; color: #999; width: 32px; padding:0; }
        .btn-danger-ghost:hover { color: var(--danger); background: #FFF0F0; }
        
        /* Grid Layout */
        .grid-layout {
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr)); 
            gap: 24px;
        }
        
        /* Card Component */
        .card {
            background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius);
            box-shadow: var(--shadow-card); transition: all 0.3s ease;
            display: flex; flex-direction: column; overflow: hidden;
        }
        .card:hover { box-shadow: var(--shadow-hover); border-color: #CCC; transform: translateY(-2px); }
        
        .card-header {
            padding: 16px; border-bottom: 1px solid var(--border); background: #FCFCFC;
            display: flex; justify-content: space-between; align-items: center;
        }
        .card-title-wrap { display: flex; align-items: center; gap: 8px; width: 100%; }
        .status-dot { width: 8px; height: 8px; background: var(--success); border-radius: 50%; box-shadow: 0 0 0 2px rgba(0,112,243,0.2); }
        .input-title { 
            border: none; background: transparent; font-weight: 600; font-size: 14px; 
            color: var(--text-main); width: 100%; padding: 4px 0;
        }
        .input-title:focus { outline: none; border-bottom: 1px solid var(--accent); }
        
        .card-body { padding: 20px; display: flex; flex-direction: column; gap: 20px; }
        
        /* Section Styling */
        .section-label { 
            font-size: 11px; font-weight: 600; text-transform: uppercase; color: #888; 
            letter-spacing: 0.5px; margin-bottom: 8px; display: flex; align-items: center; gap: 6px;
        }
        
        /* Form Inputs */
        .input-wrapper { position: relative; }
        .form-input, .form-select, .form-textarea {
            width: 100%; padding: 10px 12px; font-size: 13px; color: var(--text-main);
            border: 1px solid var(--border); border-radius: 6px; background: #FFFFFF;
            transition: all 0.2s; font-family: 'Inter', monospace;
        }
        .form-textarea { min-height: 70px; resize: vertical; line-height: 1.5; }
        .form-input:focus, .form-select:focus, .form-textarea:focus {
            outline: none; border-color: var(--text-main); ring: 2px rgba(0,0,0,0.05);
        }
        
        /* Action Flow List */
        .action-list { display: flex; flex-direction: column; gap: 10px; }
        .action-item {
            display: flex; align-items: center; gap: 10px; padding: 8px 12px;
            background: #FAFAFA; border: 1px solid var(--border); border-radius: 6px;
        }
        .delay-pill {
            background: white; border: 1px solid var(--border); border-radius: 4px;
            padding: 2px 6px; font-size: 11px; font-weight: 600; color: var(--text-sub);
            display: flex; align-items: center; gap: 4px; white-space: nowrap; box-shadow: 0 1px 2px rgba(0,0,0,0.03);
        }
        .delay-input { 
            width: 24px; text-align: center; border: none; font-weight: 700; color: var(--text-main); 
            border-bottom: 1px dashed #CCC; padding: 0;
        }
        .action-input { border: none; background: transparent; flex: 1; font-size: 13px; font-weight: 500; }
        .action-input:focus { outline: none; }
        
        /* Add Card */
        .card-add {
            border: 2px dashed var(--border); background: transparent; box-shadow: none;
            align-items: center; justify-content: center; min-height: 300px;
            cursor: pointer; color: var(--text-sub); transition: 0.2s;
        }
        .card-add:hover { border-color: var(--text-sub); color: var(--text-main); background: #F5F5F5; }
        
        .toast {
            position: fixed; bottom: 32px; right: 32px; background: var(--text-main); color: white;
            padding: 12px 20px; border-radius: 6px; font-weight: 500; font-size: 14px;
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); transform: translateY(100px); opacity: 0;
            transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1); z-index: 200;
            display: flex; align-items: center; gap: 10px;
        }
        .toast.show { transform: translateY(0); opacity: 1; }
        
        /* Icons */
        .icon-sm { font-size: 12px; }
    </style>
</head>
<body>
<div id="app">
    <nav class="navbar">
        <div class="nav-content">
            <div class="brand">
                <div class="brand-icon"><i class="fa-solid fa-bolt"></i></div>
                <span>AutoResponse <span style="color:#999;font-weight:400">Pro</span></span>
            </div>
            
            <div class="nav-actions">
                <label class="toggle-switch">
                    <input type="checkbox" v-model="config.enabled" hidden>
                    <span class="switch-base"></span>
                    <span>System {{ config.enabled ? 'ON' : 'OFF' }}</span>
                </label>
                <div style="width: 1px; height: 24px; background: var(--border);"></div>
                <button class="btn btn-primary" @click="saveConfig">
                    <i class="fa-solid fa-floppy-disk"></i> Save Changes
                </button>
            </div>
        </div>
    </nav>

    <div class="container">
        <div class="grid-layout">
            <div v-for="(rule, index) in config.rules" :key="index" class="card">
                <div class="card-header">
                    <div class="card-title-wrap">
                        <div class="status-dot" title="Active"></div>
                        <input v-model="rule.name" class="input-title" placeholder="Untitled Rule">
                    </div>
                    <button class="btn btn-danger-ghost" @click="removeRule(index)" title="Delete Rule">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>
                
                <div class="card-body">
                    <div>
                        <div class="section-label"><i class="fa-solid fa-satellite-dish icon-sm"></i> 监听配置</div>
                        <div style="display:grid; gap:12px">
                            <textarea class="form-textarea" :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" placeholder="Target Group IDs (-100...)" style="height:60px"></textarea>
                            <textarea class="form-textarea" :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" placeholder="Keywords (Empty = All)" style="height:60px"></textarea>
                        </div>
                    </div>

                    <div>
                        <div class="section-label"><i class="fa-solid fa-filter icon-sm"></i> 过滤与限制</div>
                        <div style="display:grid; grid-template-columns: 1.5fr 1fr; gap:12px; margin-bottom:12px;">
                            <select v-model="rule.sender_mode" class="form-select">
                                <option value="exclude">🚫 排除名单</option>
                                <option value="include">✅ 白名单</option>
                            </select>
                            <div style="position:relative">
                                <input type="number" v-model.number="rule.cooldown" class="form-input" style="padding-right:32px">
                                <span style="position:absolute; right:10px; top:10px; font-size:11px; color:#999; pointer-events:none">sec</span>
                            </div>
                        </div>
                        <textarea class="form-textarea" :value="listToString(rule.sender_prefixes)" @input="stringToList($event, rule, 'sender_prefixes')" placeholder="Prefixes (e.g. YY_)" style="height:50px"></textarea>
                    </div>

                    <div style="flex:1; display:flex; flex-direction:column;">
                        <div class="section-label" style="justify-content:space-between">
                            <span><i class="fa-solid fa-bolt icon-sm"></i> 执行流</span>
                            <span @click="rule.replies.push({text:'', min:2, max:4})" style="cursor:pointer; color:var(--accent); font-size:11px">+ Add Step</span>
                        </div>
                        
                        <div class="action-list">
                            <div v-if="rule.replies.length === 0" style="text-align:center; padding:15px; color:#999; font-size:12px; background:#FAFAFA; border-radius:6px; border:1px dashed #DDD;">
                                No actions defined
                            </div>
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="action-item">
                                <div class="delay-pill">
                                    <i class="fa-regular fa-clock icon-sm"></i>
                                    <input v-model.number="reply.min" type="number" class="delay-input">
                                    -
                                    <input v-model.number="reply.max" type="number" class="delay-input">
                                </div>
                                <input v-model="reply.text" class="action-input" placeholder="Reply content...">
                                <i class="fa-solid fa-xmark" style="color:#CCC; cursor:pointer; font-size:12px;" @click="rule.replies.splice(rIndex, 1)"></i>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div class="card card-add" @click="addRule">
                <i class="fa-solid fa-plus" style="font-size:24px; margin-bottom:12px; color:#DDD"></i>
                <span style="font-weight:600">Create New Rule</span>
            </div>
        </div>
    </div>

    <div :class="['toast', toast.show ? 'show' : '']">
        <i v-if="toast.type==='success'" class="fa-solid fa-circle-check" style="color:#4ADE80"></i>
        <i v-else class="fa-solid fa-circle-exclamation" style="color:#F87171"></i>
        <span>{{ toast.msg }}</span>
    </div>
</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });

            // Initialize
            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { config.enabled = data.enabled; config.rules = data.rules || []; });

            // Helpers
            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            // Actions
            const addRule = () => {
                config.rules.push({
                    name: 'New Rule ' + (config.rules.length + 1),
                    groups: [], keywords: [], sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{text: '', min: 2, max: 4}]
                });
            };
            
            const removeRule = (index) => {
                if(confirm('Are you sure you want to delete this rule?')) config.rules.splice(index, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        showToast('Configuration saved successfully', 'success');
                    } else {
                        showToast('Save failed: ' + json.msg, 'error');
                    }
                } catch(e) {
                    showToast('Network error occurred', 'error');
                }
            };

            const showToast = (msg, type) => {
                toast.msg = msg; toast.type = type; toast.show = true;
                setTimeout(() => toast.show = false, 3000);
            };

            return { config, toast, listToString, stringToList, stringToIntList, addRule, removeRule, saveConfig };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

def analyze_message(rule, event, other_cs_ids, sender_name):
    if event.chat_id not in rule.get("groups", []): return False, "群组不符"
    if event.is_reply: return False, "是回复消息"
    if event.out: return False, "Bot自己发送"
    if event.sender_id in other_cs_ids: return False, "ID是客服"
    
    text = event.text or ""
    keywords = rule.get("keywords", [])
    if keywords and not any(kw in text for kw in keywords): return False, "关键词不匹配"
    
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    if sender_mode == "exclude" and match_prefix: return False, "前缀被排除"
    elif sender_mode == "include" and not match_prefix: return False, "前缀不在白名单"
    
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中"
    
    return True, "✅ 匹配成功"

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global_main_handler = main_handler
    init_redis_connection()
    load_config(main_cs_prefixes)

    @app.route('/tool/monitor_settings')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)
    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.json)
        if success: return jsonify({"success": True})
        return jsonify({"success": False, "msg": msg}), 200

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug":
            await event.reply("Monitor Debug: Alive")
            return

        if not current_config.get("enabled", True): return
        
        sender_name = ""
        try:
            event.sender = await event.get_sender()
            sender_name = getattr(event.sender, 'first_name', '') or ''
        except: pass

        for rule in current_config.get("rules", []):
            try:
                is_match, reason = analyze_message(rule, event, other_cs_ids, sender_name)
                if is_match:
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发! 开始回复...")
                    rule_id = rule.get("id", str(rule.get("groups")))
                    rule_timers[rule_id] = time.time()
                    
                    for reply in rule.get("replies", []):
                        content = reply.get("text", "")
                        if not content: continue
                        delay = random.uniform(reply.get("min", 1), reply.get("max", 3))
                        await asyncio.sleep(delay)
                        
                        sent_msg = await event.reply(content)
                        
                        if global_main_handler:
                            try:
                                fake_event = events.NewMessage.Event(sent_msg)
                                asyncio.create_task(global_main_handler(fake_event))
                            except: pass
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] SaaS Pro UI 已启动")
