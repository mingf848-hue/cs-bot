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
    
    # 填充默认前缀
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

# --- Web UI (RPA 风格重构) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>自动化响应流 | AutoResponder</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root {
            --primary: #4F46E5; /* Indigo */
            --primary-hover: #4338ca;
            --bg-page: #F3F4F6;
            --bg-card: #FFFFFF;
            --text-main: #1F2937;
            --text-sub: #6B7280;
            --border: #E5E7EB;
            --success: #10B981;
            --danger: #EF4444;
            --line-color: #E0E7FF;
        }
        body { font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg-page); color: var(--text-main); margin: 0; padding: 20px; line-height: 1.5; }
        
        /* Layout */
        .container { max-width: 900px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; background: var(--bg-card); padding: 15px 25px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .header h2 { margin: 0; font-size: 1.25rem; color: var(--text-main); display: flex; align-items: center; gap: 10px; }
        .logo-icon { color: var(--primary); font-size: 1.5rem; }

        /* Buttons */
        .btn { padding: 10px 20px; border-radius: 8px; font-weight: 600; cursor: pointer; border: none; transition: all 0.2s; font-size: 0.9rem; display: inline-flex; align-items: center; gap: 6px; }
        .btn-primary { background: var(--primary); color: white; box-shadow: 0 4px 6px -1px rgba(79, 70, 229, 0.2); }
        .btn-primary:hover { background: var(--primary-hover); transform: translateY(-1px); }
        .btn-outline { background: white; border: 1px solid var(--border); color: var(--text-sub); }
        .btn-outline:hover { border-color: var(--primary); color: var(--primary); }
        .btn-danger-ghost { background: transparent; color: var(--danger); padding: 6px; }
        .btn-danger-ghost:hover { background: #FEF2F2; border-radius: 6px; }
        .btn-add { width: 100%; justify-content: center; padding: 15px; border: 2px dashed #CBD5E1; color: #64748B; background: transparent; }
        .btn-add:hover { border-color: var(--primary); color: var(--primary); background: #EEF2FF; }

        /* Switch */
        .switch { position: relative; display: inline-block; width: 44px; height: 24px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider { position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0; background-color: #ccc; transition: .4s; border-radius: 34px; }
        .slider:before { position: absolute; content: ""; height: 18px; width: 18px; left: 3px; bottom: 3px; background-color: white; transition: .4s; border-radius: 50%; }
        input:checked + .slider { background-color: var(--success); }
        input:checked + .slider:before { transform: translateX(20px); }

        /* Rule Card (RPA Flow Style) */
        .rpa-card { background: var(--bg-card); border-radius: 16px; margin-bottom: 24px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); overflow: hidden; position: relative; border-left: 5px solid var(--primary); }
        .card-header { padding: 15px 25px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; background: #FAFAFA; }
        .card-title-input { border: none; background: transparent; font-size: 1rem; font-weight: 700; color: var(--text-main); width: 200px; }
        .card-title-input:focus { outline: none; border-bottom: 2px solid var(--primary); }

        .flow-container { padding: 25px; position: relative; }
        
        /* The Connector Line */
        .flow-line { position: absolute; left: 45px; top: 20px; bottom: 20px; width: 2px; background: var(--line-color); z-index: 0; }

        /* Flow Steps */
        .step-block { display: flex; gap: 20px; margin-bottom: 25px; position: relative; z-index: 1; }
        .step-icon { width: 40px; height: 40px; background: white; border: 2px solid var(--line-color); border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 1.1rem; color: var(--text-sub); flex-shrink: 0; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
        .step-content { flex-grow: 1; background: #F8FAFC; border-radius: 12px; padding: 20px; border: 1px solid var(--border); }
        
        .step-block.trigger .step-icon { color: #D946EF; border-color: #D946EF; background: #FDF4FF; }
        .step-block.filter .step-icon { color: #F59E0B; border-color: #F59E0B; background: #FFFBEB; }
        .step-block.action .step-icon { color: var(--primary); border-color: var(--primary); background: #EEF2FF; }

        /* Form Elements */
        .form-label { display: block; font-size: 0.8rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-sub); margin-bottom: 8px; }
        .input-area { width: 100%; border: 1px solid #D1D5DB; border-radius: 8px; padding: 10px; font-family: monospace; font-size: 0.9rem; resize: vertical; box-sizing: border-box; transition: border 0.2s; }
        .input-area:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 0 3px rgba(79, 70, 229, 0.1); }
        .input-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        select { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid #D1D5DB; background: white; }

        /* Action Timeline */
        .action-timeline { display: flex; flex-direction: column; gap: 10px; }
        .reply-node { display: flex; align-items: center; gap: 10px; background: white; padding: 10px 15px; border-radius: 8px; border: 1px solid var(--border); box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
        .node-delay { display: flex; align-items: center; gap: 5px; background: #F3F4F6; padding: 5px 10px; border-radius: 6px; font-size: 0.85rem; color: #4B5563; white-space: nowrap; }
        .node-delay input { width: 40px; border: none; background: transparent; text-align: center; font-weight: bold; border-bottom: 1px dashed #9CA3AF; }
        .node-text { flex-grow: 1; }
        .node-text input { width: 100%; border: none; outline: none; font-size: 0.95rem; }

        .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: rgba(17, 24, 39, 0.9); color: white; padding: 12px 24px; border-radius: 30px; font-weight: 500; opacity: 0; transition: 0.3s; pointer-events: none; z-index: 999; display: flex; align-items: center; gap: 8px; box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1); }
        .toast.show { opacity: 1; transform: translateX(-50%) translateY(-10px); }
    </style>
</head>
<body>
<div id="app">
    <div class="container">
        <div class="header">
            <h2><i class="fa-solid fa-bolt logo-icon"></i> 自动响应流配置</h2>
            <div style="display:flex; gap: 20px; align-items:center">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:0.9rem; font-weight:600; color:var(--text-sub)">功能总开关</span>
                    <label class="switch">
                        <input type="checkbox" v-model="config.enabled">
                        <span class="slider"></span>
                    </label>
                </div>
                <button class="btn btn-primary" @click="saveConfig">
                    <i class="fa-solid fa-floppy-disk"></i> 保存配置
                </button>
            </div>
        </div>

        <div v-for="(rule, index) in config.rules" :key="index" class="rpa-card">
            <div class="card-header">
                <div style="display:flex; align-items:center; gap:10px;">
                    <i class="fa-solid fa-hashtag" style="color:#CBD5E1"></i>
                    <input v-model="rule.name" class="card-title-input" placeholder="规则名称...">
                </div>
                <button class="btn btn-danger-ghost" @click="removeRule(index)" title="删除此规则">
                    <i class="fa-solid fa-trash"></i>
                </button>
            </div>

            <div class="flow-container">
                <div class="flow-line"></div>

                <div class="step-block trigger">
                    <div class="step-icon"><i class="fa-solid fa-satellite-dish"></i></div>
                    <div class="step-content">
                        <div class="input-row">
                            <div>
                                <label class="form-label"><i class="fa-regular fa-comments"></i> 监控群组 (ID)</label>
                                <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" class="input-area" style="height:80px" placeholder="-100xxxxxx (每行一个)"></textarea>
                            </div>
                            <div>
                                <label class="form-label"><i class="fa-solid fa-key"></i> 触发关键词</label>
                                <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" class="input-area" style="height:80px" placeholder="留空则匹配所有消息..."></textarea>
                            </div>
                        </div>
                    </div>
                </div>

                <div class="step-block filter">
                    <div class="step-icon"><i class="fa-solid fa-filter"></i></div>
                    <div class="step-content">
                        <div class="input-row">
                            <div>
                                <label class="form-label">发送人模式</label>
                                <select v-model="rule.sender_mode">
                                    <option value="exclude">🚫 黑名单 (排除这些前缀)</option>
                                    <option value="include">✅ 白名单 (仅限这些前缀)</option>
                                </select>
                            </div>
                            <div>
                                <label class="form-label">冷却时间 (全局CD)</label>
                                <div style="position:relative">
                                    <input type="number" v-model.number="rule.cooldown" class="input-area" style="padding-right:30px">
                                    <span style="position:absolute; right:10px; top:10px; color:#9CA3AF; font-size:0.8rem">秒</span>
                                </div>
                            </div>
                        </div>
                        <div style="margin-top:15px">
                            <label class="form-label">前缀列表</label>
                            <textarea :value="listToString(rule.sender_prefixes)" @input="stringToList($event, rule, 'sender_prefixes')" class="input-area" style="height:60px" placeholder="例如: YY_ (每行一个)"></textarea>
                        </div>
                    </div>
                </div>

                <div class="step-block action">
                    <div class="step-icon"><i class="fa-solid fa-paper-plane"></i></div>
                    <div class="step-content">
                        <label class="form-label">执行回复流 (按顺序发送)</label>
                        <div class="action-timeline">
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="reply-node">
                                <div class="node-delay" title="随机延迟范围">
                                    <i class="fa-regular fa-clock"></i>
                                    <input v-model.number="reply.min" type="number" step="0.1">
                                    <span>-</span>
                                    <input v-model.number="reply.max" type="number" step="0.1">
                                    <span>s</span>
                                </div>
                                <div class="node-text">
                                    <input v-model="reply.text" placeholder="请输入回复内容...">
                                </div>
                                <button class="btn btn-danger-ghost" @click="rule.replies.splice(rIndex, 1)">
                                    <i class="fa-solid fa-xmark"></i>
                                </button>
                            </div>
                            <button class="btn btn-outline" style="justify-content:center; border-style:dashed" @click="rule.replies.push({text:'', min:2, max:4})">
                                <i class="fa-solid fa-plus"></i> 添加回复步骤
                            </button>
                        </div>
                    </div>
                </div>

            </div>
        </div>

        <button class="btn btn-add" @click="addRule">
            <i class="fa-solid fa-circle-plus"></i> 添加新的响应规则
        </button>

        <div style="height:50px"></div>
    </div>
    
    <div :class="['toast', toast.show ? 'show' : '']">
        <i v-if="toast.type=='success'" class="fa-solid fa-check-circle" style="color:#34D399"></i>
        <i v-else class="fa-solid fa-triangle-exclamation" style="color:#F87171"></i>
        {{ toast.msg }}
    </div>
</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });

            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { config.enabled = data.enabled; config.rules = data.rules || []; });

            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    name: '新规则 #' + (config.rules.length + 1), 
                    groups: [], keywords: [], sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{text: '', min: 2, max: 4}]
                });
            };
            
            const removeRule = (index) => {
                if(confirm('确定删除此规则吗？')) config.rules.splice(index, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        toast.msg = "配置已保存并生效"; toast.type = 'success';
                    } else {
                        toast.msg = "保存失败: " + (json.msg || "未知错误"); toast.type = 'error';
                    }
                } catch(e) {
                    toast.msg = "网络错误，无法连接服务器"; toast.type = 'error';
                }
                toast.show = true; setTimeout(()=>toast.show=false, 3000);
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
            # 简易诊断
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
                        
                        # 联动主程序
                        if global_main_handler:
                            try:
                                fake_event = events.NewMessage.Event(sent_msg)
                                asyncio.create_task(global_main_handler(fake_event))
                                logger.info(f"🔗 [Monitor] 联动汇报 Msg={sent_msg.id}")
                            except: pass
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] RPA UI版已启动")
