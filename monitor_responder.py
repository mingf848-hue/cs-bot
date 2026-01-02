import asyncio
import logging
import time
import random
import json
import os
from flask import request, jsonify, Response
from telethon import events

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "default_rule",
            "name": "示例规则-监控非客服",
            "groups": [-1002169616907],
            "keywords": ["对比上时段缺少"],
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                {"text": "请稍等ART", "min": 3, "max": 5},
                {"text": "通道临时调整", "min": 2, "max": 4}
            ]
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}

# --- 配置管理 ---
def load_config(system_cs_prefixes):
    global current_config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if "rules" in saved:
                    current_config = saved
                else:
                    current_config = DEFAULT_CONFIG.copy()
        else:
            current_config = DEFAULT_CONFIG.copy()
            
        for rule in current_config["rules"]:
            if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
                rule["sender_prefixes"] = list(system_cs_prefixes)
                
        logger.info(f"✅ [Monitor] 配置已加载，共 {len(current_config['rules'])} 条规则")
    except Exception as e:
        logger.error(f"❌ [Monitor] 加载配置失败: {e}")
        current_config = DEFAULT_CONFIG.copy()

def save_config(new_config):
    global current_config
    try:
        for rule in new_config.get("rules", []):
            rule["groups"] = [int(x) for x in rule["groups"]]
            rule["cooldown"] = int(rule["cooldown"])
            for r in rule["replies"]:
                r["min"] = float(r["min"])
                r["max"] = float(r["max"])
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        current_config = new_config
        logger.info("💾 [Monitor] 配置已保存")
        return True
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存失败: {e}")
        return False

# --- Web UI (已更换国内CDN) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>高级自动响应配置</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <style>
        :root { --primary: #007AFF; --danger: #FF3B30; --bg: #F5F5F7; --card: #FFF; --border: #E5E5EA; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: #1D1D1F; padding: 20px; max-width: 800px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; flex-wrap: wrap; gap: 10px; }
        .btn { padding: 8px 16px; border-radius: 8px; border: none; font-weight: 600; cursor: pointer; font-size: 14px; transition: 0.2s; white-space: nowrap; }
        .btn-primary { background: var(--primary); color: white; }
        .btn-danger { background: var(--danger); color: white; }
        .btn-outline { border: 1px solid var(--border); background: transparent; color: #666; }
        
        .rule-card { background: var(--card); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.04); border: 1px solid var(--border); }
        .rule-header { display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 15px; align-items: center; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; font-size: 12px; font-weight: 600; color: #86868B; margin-bottom: 5px; text-transform: uppercase; }
        input, textarea, select { width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; box-sizing: border-box; background: #FAFAFA; }
        textarea { resize: vertical; min-height: 60px; font-family: monospace; }
        
        .reply-item { background: #F2F2F7; padding: 10px; border-radius: 8px; margin-bottom: 8px; display: flex; gap: 10px; align-items: center; }
        .reply-text { flex: 2; }
        .reply-time { flex: 1; }
        
        .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.85); color: white; padding: 12px 24px; border-radius: 30px; font-weight: 600; opacity: 0; transition: 0.3s; pointer-events: none; z-index: 999; }
        .toast.show { opacity: 1; }
        #loading { text-align: center; margin-top: 50px; color: #666; }
    </style>
</head>
<body>
<div id="app">
    <div id="loading" v-if="false">正在加载配置面板...</div>
    
    <div class="header">
        <h2>⚡️ 自动响应规则 ({{ config.rules.length }})</h2>
        <div style="display:flex; gap:10px; align-items:center">
             <label style="font-size:14px; font-weight:600;"><input type="checkbox" v-model="config.enabled"> 全局启用</label>
             <button class="btn btn-primary" @click="saveConfig">保存配置</button>
        </div>
    </div>

    <div v-for="(rule, index) in config.rules" :key="rule.id" class="rule-card">
        <div class="rule-header">
            <input v-model="rule.name" style="width: 200px; font-weight:bold; border:none; background:transparent; padding:0; font-size:16px;" placeholder="规则名称...">
            <button class="btn btn-danger" @click="removeRule(index)" style="padding:4px 10px; font-size:12px">删除规则</button>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px;">
            <div class="form-group">
                <label>监控群组 ID (换行分隔)</label>
                <textarea v-model="groupsToString(rule)" @input="stringToGroups($event, rule)"></textarea>
            </div>
            <div class="form-group">
                <label>触发关键词 (换行分隔)</label>
                <textarea v-model="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')"></textarea>
            </div>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px;">
            <div class="form-group">
                <label>发送人前缀模式</label>
                <select v-model="rule.sender_mode">
                    <option value="exclude">🚫 排除模式 (不回复此类人)</option>
                    <option value="include">✅ 仅限模式 (只回复此类人)</option>
                </select>
            </div>
            <div class="form-group">
                <label>前缀列表 (换行分隔)</label>
                <textarea v-model="listToString(rule.sender_prefixes)" @input="stringToList($event, rule, 'sender_prefixes')" placeholder="留空则应用默认值"></textarea>
            </div>
            <div class="form-group">
                <label>规则冷却时间 (秒)</label>
                <input type="number" v-model.number="rule.cooldown">
            </div>
        </div>

        <div class="form-group">
            <label>回复流程 (按顺序执行)</label>
            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="reply-item">
                <div class="reply-text">
                    <input type="text" v-model="reply.text" placeholder="回复内容...">
                </div>
                <div class="reply-time" style="display:flex; gap:5px; align-items:center">
                    <input type="number" step="0.1" v-model.number="reply.min" placeholder="Min" style="width:60px">
                    <span>-</span>
                    <input type="number" step="0.1" v-model.number="reply.max" placeholder="Max" style="width:60px">
                    <span style="font-size:12px;color:#888">秒</span>
                </div>
                <button class="btn btn-outline" @click="rule.replies.splice(rIndex, 1)">✕</button>
            </div>
            <button class="btn btn-outline" @click="addReply(rule)" style="width:100%; border-style:dashed; margin-top:5px">+ 添加回复步骤</button>
        </div>
    </div>

    <button class="btn btn-outline" @click="addRule" style="width:100%; padding: 15px; border-style:dashed; margin-bottom: 50px;">+ 添加新规则</button>
    <div :class="['toast', toast.show ? 'show' : '']">{{ toast.msg }}</div>
</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '' });

            // 获取后端数据
            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => {
                    config.enabled = data.enabled;
                    config.rules = data.rules || [];
                })
                .catch(err => {
                    alert("无法连接服务器，请检查网络或刷新页面。");
                });

            const groupsToString = (rule) => (rule.groups || []).join('\\n');
            const stringToGroups = (e, rule) => { rule.groups = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            
            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    id: 'rule_' + Date.now(),
                    name: '新规则',
                    groups: [],
                    keywords: [],
                    sender_mode: 'exclude',
                    sender_prefixes: [],
                    cooldown: 60,
                    replies: [{text: '', min: 2, max: 4}]
                });
            };

            const addReply = (rule) => {
                rule.replies.push({text: '', min: 1, max: 3});
            };

            const removeRule = (idx) => {
                if(confirm('确定删除此规则吗？')) config.rules.splice(idx, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', {
                        method: 'POST', headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify(config)
                    });
                    const j = await res.json();
                    showToast(j.success ? "✅ 保存成功" : "❌ 保存失败");
                } catch(e) { showToast("❌ 网络错误: " + e); }
            };

            const showToast = (msg) => {
                toast.msg = msg; toast.show = true;
                setTimeout(() => toast.show = false, 3000);
            };

            return { config, toast, groupsToString, stringToGroups, listToString, stringToList, addRule, addReply, removeRule, saveConfig };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

# --- 核心逻辑 ---
def check_rule_match(rule, event, other_cs_ids):
    if event.chat_id not in rule.get("groups", []):
        return False
    if event.is_reply:
        return False
    if event.out or (event.sender_id in other_cs_ids):
        return False

    text = event.text or ""
    keywords = rule.get("keywords", [])
    if not keywords or not any(kw in text for kw in keywords):
        return False

    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    sender_name = getattr(event.sender, 'first_name', '') or ''
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    
    if sender_mode == "exclude":
        if match_prefix: return False
    elif sender_mode == "include":
        if not match_prefix: return False

    rule_id = rule.get("id", "unknown")
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    cooldown = rule.get("cooldown", 60)
    
    if now - last_time < cooldown:
        return False
    
    rule_timers[rule_id] = now
    return True

# --- 初始化与挂载 ---
def init_monitor(client, app, other_cs_ids, main_cs_prefixes):
    load_config(main_cs_prefixes)

    # 返回纯HTML，无需模板解析
    @app.route('/tool/monitor_settings')
    def monitor_settings_page():
        return Response(SETTINGS_HTML, mimetype='text/html')
    
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json():
        return jsonify(current_config)

    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        if save_config(request.json):
            return jsonify({"success": True})
        return jsonify({"success": False}), 500

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if not current_config.get("enabled", True):
            return
        try:
            event.sender = await event.get_sender()
        except:
            return 

        for rule in current_config.get("rules", []):
            try:
                if check_rule_match(rule, event, other_cs_ids):
                    logger.info(f"🔎 [Monitor] 规则 '{rule.get('name')}' 触发 | Group={event.chat_id} | User={event.sender_id}")
                    for reply in rule.get("replies", []):
                        content = reply.get("text", "")
                        if not content: continue
                        min_d = reply.get("min", 1)
                        max_d = reply.get("max", 3)
                        delay = random.uniform(min_d, max_d)
                        await asyncio.sleep(delay)
                        await event.reply(content)
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor v2] 多规则监控系统已启动")
