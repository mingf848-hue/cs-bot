import asyncio
import logging
import time
import random
import json
import os
import uuid
from flask import request, render_template_string, jsonify
from telethon import events

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"

# --- 默认配置结构 ---
# 包含一条示例规则
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "default_rule",
            "name": "示例规则-监控非客服",
            "groups": [-1002169616907],
            "keywords": ["对比上时段缺少"],
            "sender_mode": "exclude",  # exclude(排除模式) 或 include(仅限模式)
            "sender_prefixes": [],     # 这里留空，加载时会自动填充 main.py 里的客服前缀
            "cooldown": 60,
            "replies": [
                {"text": "请稍等ART", "min": 3, "max": 5},
                {"text": "通道临时调整", "min": 2, "max": 4}
            ]
        }
    ]
}

# 全局状态
current_config = DEFAULT_CONFIG.copy()
# 记录每个规则的最后触发时间: { "rule_id": timestamp }
rule_timers = {}

# --- 配置管理 ---
def load_config(system_cs_prefixes):
    global current_config
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                # 简单合并
                if "rules" in saved:
                    current_config = saved
                else:
                    # 旧版本迁移或格式错误
                    logger.warning("⚠️ [Monitor] 检测到旧配置格式，正在重置为多规则模式")
                    current_config = DEFAULT_CONFIG.copy()
        else:
            current_config = DEFAULT_CONFIG.copy()
            
        # 初始化：确保所有 exclude 模式的规则，如果前缀为空，则使用系统的客服前缀
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
        # 简单清洗数据
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

# --- Web UI (Vue.js CDN版, 单文件) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>高级自动响应配置</title>
    <script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
    <style>
        :root { --primary: #007AFF; --danger: #FF3B30; --bg: #F5F5F7; --card: #FFF; --border: #E5E5EA; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: #1D1D1F; padding: 20px; max-width: 800px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .btn { padding: 8px 16px; border-radius: 8px; border: none; font-weight: 600; cursor: pointer; font-size: 14px; transition: 0.2s; }
        .btn-primary { background: var(--primary); color: white; }
        .btn-danger { background: var(--danger); color: white; }
        .btn-outline { border: 1px solid var(--border); background: transparent; color: #666; }
        
        .rule-card { background: var(--card); border-radius: 12px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 10px rgba(0,0,0,0.04); border: 1px solid var(--border); }
        .rule-header { display: flex; justify-content: space-between; border-bottom: 1px solid var(--border); padding-bottom: 10px; margin-bottom: 15px; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; font-size: 12px; font-weight: 600; color: #86868B; margin-bottom: 5px; text-transform: uppercase; }
        input, textarea, select { width: 100%; padding: 10px; border: 1px solid var(--border); border-radius: 8px; font-size: 14px; box-sizing: border-box; background: #FAFAFA; }
        textarea { resize: vertical; min-height: 60px; font-family: monospace; }
        
        .reply-item { background: #F2F2F7; padding: 10px; border-radius: 8px; margin-bottom: 8px; display: flex; gap: 10px; align-items: center; }
        .reply-text { flex: 2; }
        .reply-time { flex: 1; }
        
        .toast { position: fixed; bottom: 30px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.85); color: white; padding: 12px 24px; border-radius: 30px; font-weight: 600; opacity: 0; transition: 0.3s; pointer-events: none; }
        .toast.show { opacity: 1; }
    </style>
</head>
<body>
<div id="app">
    <div class="header">
        <h2>⚡️ 自动响应规则 ({{ config.rules.length }})</h2>
        <div style="display:flex; gap:10px; align-items:center">
             <label><input type="checkbox" v-model="config.enabled"> 全局启用</label>
             <button class="btn btn-primary" @click="saveConfig">保存配置</button>
        </div>
    </div>

    <div v-for="(rule, index) in config.rules" :key="rule.id" class="rule-card">
        <div class="rule-header">
            <input v-model="rule.name" style="width: 200px; font-weight:bold; border:none; background:transparent; padding:0;" placeholder="规则名称...">
            <button class="btn btn-danger" @click="removeRule(index)" style="padding:4px 10px; font-size:12px">删除</button>
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
                    <option value="exclude">🚫 排除模式 (不回复这些人)</option>
                    <option value="include">✅ 仅限模式 (只回复这些人)</option>
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

            // 初始化加载
            fetch('/tool/monitor_settings_json').then(r=>r.json()).then(data => {
                config.enabled = data.enabled;
                config.rules = data.rules || [];
            });

            // 辅助函数
            const groupsToString = (rule) => rule.groups.join('\\n');
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
    """判断单个规则是否命中"""
    # 1. 群组检查
    # 将 event.chat_id 转换为整数比较 (以防万一)
    if event.chat_id not in rule.get("groups", []):
        return False
    
    # 2. 消息流 (Reply) 检查：根据需求，必须不是回复消息
    if event.is_reply:
        return False
        
    # 3. 基础身份排除：如果是机器人自己发的，或者是其他已知客服发的(ID匹配)，直接跳过
    # 注意：这里只排除 ID 明确是客服的。对于名字前缀的检查，由下面的 sender_mode 决定。
    if event.out or (event.sender_id in other_cs_ids):
        return False

    # 4. 关键词检查
    text = event.text or ""
    keywords = rule.get("keywords", [])
    if not keywords or not any(kw in text for kw in keywords):
        return False

    # 5. 发送人前缀检查 (核心逻辑变化)
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    
    # 获取发送者名字
    # 这里需要 await，但在同步函数里没法 await，所以 sender 对象需要在外部传进来
    # 稍微重构一下调用逻辑，在 handler 里获取 sender
    sender_name = getattr(event.sender, 'first_name', '') or ''
    
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    
    if sender_mode == "exclude":
        # 排除模式：如果匹配了前缀（是客服），则【不】回复 -> return False
        if match_prefix:
            return False
    elif sender_mode == "include":
        # 仅限模式：如果【没】匹配前缀（不是指定的人），则【不】回复 -> return False
        if not match_prefix:
            return False

    # 6. 冷却检查
    rule_id = rule.get("id", "unknown")
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    cooldown = rule.get("cooldown", 60)
    
    if now - last_time < cooldown:
        # 命中但冷却中
        return False
    
    # 全部通过，更新冷却
    rule_timers[rule_id] = now
    return True

# --- 初始化与挂载 ---

def init_monitor(client, app, other_cs_ids, main_cs_prefixes):
    # 1. 加载配置
    load_config(main_cs_prefixes)

    # 2. 路由: 页面
    @app.route('/tool/monitor_settings')
    def monitor_settings_page():
        return render_template_string(SETTINGS_HTML)
    
    # 3. 路由: 获取 JSON 数据 (供 Vue 使用)
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json():
        return jsonify(current_config)

    # 4. 路由: 保存 API
    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        if save_config(request.json):
            return jsonify({"success": True})
        return jsonify({"success": False}), 500

    # 5. 注册监听器
    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if not current_config.get("enabled", True):
            return
            
        # 预先获取 Sender，避免在循环里重复请求
        try:
            event.sender = await event.get_sender()
        except:
            return # 无法获取发送者，跳过

        # 遍历所有规则
        for rule in current_config.get("rules", []):
            try:
                if check_rule_match(rule, event, other_cs_ids):
                    logger.info(f"🔎 [Monitor] 规则 '{rule.get('name')}' 触发 | Group={event.chat_id} | User={event.sender_id}")
                    
                    # 执行回复序列
                    for reply in rule.get("replies", []):
                        content = reply.get("text", "")
                        if not content: continue
                        
                        # 随机延迟
                        min_d = reply.get("min", 1)
                        max_d = reply.get("max", 3)
                        delay = random.uniform(min_d, max_d)
                        
                        await asyncio.sleep(delay)
                        await event.reply(content)
                        
                    # 一个消息只触发一条规则，防止冲突？
                    # 建议 break，否则如果多条规则重叠，会发多次
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor v2] 多规则监控系统已启动")
