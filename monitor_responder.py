import asyncio
import logging
import time
import random
import json
import os
from flask import request, jsonify, Response
from telethon import events

# 引入 Redis
try:
    import redis
except ImportError:
    redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "default_rule",
            "name": "示例规则",
            "groups": [-1002169616907], # 默认只有一个群，注意检查这里！
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
        except Exception as e:
            logger.error(f"❌ [Monitor] Redis 读取错误: {e}")

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
            
    # 打印当前的规则摘要，方便调试
    logger.info("-" * 30)
    logger.info(f"✅ [Monitor] 配置加载完成，共 {len(current_config['rules'])} 条规则")
    for i, rule in enumerate(current_config['rules']):
        logger.info(f"   规则 {i+1}: {rule.get('name')} | 监控群: {rule.get('groups')}")
    logger.info("-" * 30)

def save_config(new_config):
    global current_config
    try:
        # 数据清洗
        for rule in new_config.get("rules", []):
            rule["groups"] = [int(x) for x in rule["groups"]]
            rule["cooldown"] = int(rule["cooldown"])
            for r in rule["replies"]:
                r["min"] = float(r["min"]); r["max"] = float(r["max"])
        
        if redis_client:
            redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        
        current_config = new_config
        logger.info("💾 [Monitor] 配置已更新并保存")
        return True
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存失败: {e}")
        return False

# --- Web UI ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>自动响应配置</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <style>
        body { font-family: -apple-system, sans-serif; background: #F5F5F7; padding: 20px; max-width: 800px; margin: 0 auto; }
        .card { background: #FFF; padding: 20px; border-radius: 12px; margin-bottom: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
        input, textarea, select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 8px; box-sizing: border-box; margin-top: 5px; }
        button { background: #007AFF; color: white; border: none; padding: 10px 20px; border-radius: 8px; cursor: pointer; }
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); color: white; padding: 10px 20px; border-radius: 20px; opacity: 0; transition: 0.3s; pointer-events: none; }
        .toast.show { opacity: 1; }
    </style>
</head>
<body>
<div id="app">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px">
        <h2>⚡️ 自动响应配置</h2>
        <button @click="saveConfig">保存配置</button>
    </div>
    
    <div style="margin-bottom:15px">
        <label><input type="checkbox" v-model="config.enabled"> 启用自动响应功能</label>
    </div>

    <div v-for="(rule, index) in config.rules" :key="index" class="card">
        <div style="display:flex; justify-content:space-between; margin-bottom:10px; border-bottom:1px solid #eee; padding-bottom:10px">
            <input v-model="rule.name" style="font-weight:bold; width:200px; border:none; padding:0" placeholder="规则名称">
            <button style="background:#FF3B30; padding:5px 10px; font-size:12px" @click="config.rules.splice(index, 1)">删除规则</button>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom:15px">
            <div>
                <label>监控群组 ID (换行分隔)</label>
                <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" style="height:80px"></textarea>
            </div>
            <div>
                <label>触发关键词 (留空则匹配所有消息)</label>
                <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" style="height:80px" placeholder="留空则匹配所有消息"></textarea>
            </div>
        </div>

        <div style="display:grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom:15px">
            <div>
                <label>发送人前缀模式</label>
                <select v-model="rule.sender_mode">
                    <option value="exclude">🚫 排除模式 (不回复此类人)</option>
                    <option value="include">✅ 仅限模式 (只回复此类人)</option>
                </select>
            </div>
            <div>
                <label>前缀列表 (换行分隔)</label>
                <textarea :value="listToString(rule.sender_prefixes)" @input="stringToList($event, rule, 'sender_prefixes')" style="height:80px"></textarea>
            </div>
            <div>
                <label>规则冷却时间 (秒)</label>
                <input type="number" v-model.number="rule.cooldown">
            </div>
        </div>

        <div>
            <label>回复流程</label>
            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" style="display:flex; gap:10px; margin-top:5px">
                <input v-model="reply.text" placeholder="回复内容" style="flex:2">
                <input v-model.number="reply.min" type="number" step="0.1" placeholder="Min" style="width:60px">
                <input v-model.number="reply.max" type="number" step="0.1" placeholder="Max" style="width:60px">
                <button style="background:#ddd; color:#333; padding:5px 10px" @click="rule.replies.splice(rIndex, 1)">✕</button>
            </div>
            <button style="background:transparent; border:1px dashed #999; color:#666; width:100%; margin-top:5px" @click="rule.replies.push({text:'', min:2, max:4})">+ 添加步骤</button>
        </div>
    </div>

    <button style="width:100%; padding:15px; border:1px dashed #007AFF; background:#F0F8FF; color:#007AFF" @click="addRule">+ 添加新规则</button>
    <div :class="['toast', toast.show ? 'show' : '']">{{ toast.msg }}</div>
</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '' });

            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { config.enabled = data.enabled; config.rules = data.rules || []; });

            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    name: '新规则', groups: [], keywords: [], sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{text: '', min: 2, max: 4}]
                });
            };

            const saveConfig = async () => {
                const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                const json = await res.json();
                toast.msg = json.success ? "✅ 保存成功" : "❌ 保存失败"; toast.show = true; setTimeout(()=>toast.show=false, 3000);
            };

            return { config, toast, listToString, stringToList, stringToIntList, addRule, saveConfig };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

# --- 强制显示所有日志的检查逻辑 ---
def check_rule_match_debug(rule, event, other_cs_ids):
    rule_name = rule.get("name", "未命名")
    target_groups = rule.get("groups", [])
    
    # [强制打印] 每次匹配规则时，都打印当前消息在哪，规则要求在哪
    # 注意：这会产生很多日志，但能彻底解决疑问
    
    # 1. 群组检查
    if event.chat_id not in target_groups:
        # 这里改成了 INFO 级别，确保您能看到
        logger.info(f"🔍 [Monitor] 规则 '{rule_name}' 忽略 -> 消息所在群 ({event.chat_id}) 不在规则列表 {target_groups}")
        return False, "群组不匹配"
    
    # 2. 消息流检查
    if event.is_reply:
        logger.info(f"🚫 [Monitor] 规则 '{rule_name}' 跳过 -> 这是一条【回复消息】")
        return False, "是回复消息"
        
    # 3. 基础身份排除
    if event.out: return False, "Bot自己发送"
    if event.sender_id in other_cs_ids:
        logger.info(f"🚫 [Monitor] 规则 '{rule_name}' 跳过 -> 发送者ID在客服列表中 (ID={event.sender_id})")
        return False, "ID是客服"

    # 4. 关键词检查
    text = event.text or ""
    keywords = rule.get("keywords", [])
    if keywords:
        if not any(kw in text for kw in keywords):
            logger.info(f"🔍 [Monitor] 规则 '{rule_name}' 忽略 -> 关键词不匹配")
            return False, "关键词不匹配"

    # 5. 发送人前缀检查
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    sender_name = ""
    if event.sender:
        sender_name = getattr(event.sender, 'first_name', '') or ''
        
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    
    if sender_mode == "exclude" and match_prefix:
        logger.info(f"🚫 [Monitor] 规则 '{rule_name}' 跳过 -> 前缀排除命中 ({sender_name})")
        return False, "前缀排除"
    elif sender_mode == "include" and not match_prefix:
        logger.info(f"🚫 [Monitor] 规则 '{rule_name}' 跳过 -> 前缀不在白名单 ({sender_name})")
        return False, "前缀非白名单"

    # 6. 冷却检查
    rule_id = rule.get("id", str(target_groups))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    cooldown = rule.get("cooldown", 60)
    if now - last_time < cooldown:
        logger.info(f"⏳ [Monitor] 规则 '{rule_name}' 冷却中 (剩余 {int(cooldown - (now - last_time))}s)")
        return False, "冷却中"
    
    rule_timers[rule_id] = now
    return True, "匹配成功"

def init_monitor(client, app, other_cs_ids, main_cs_prefixes):
    init_redis_connection()
    load_config(main_cs_prefixes)

    @app.route('/tool/monitor_settings')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
    
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)

    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        if save_config(request.json): return jsonify({"success": True})
        return jsonify({"success": False}), 500

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        # [强制打印] 收到任何消息，先吼一声
        # 这样您就知道 Bot 到底有没有收到消息
        # logger.info(f"📨 [Monitor] 收到消息: ChatID={event.chat_id} | User={event.sender_id}")
        
        if not current_config.get("enabled", True): return
        try: event.sender = await event.get_sender()
        except: return 

        for rule in current_config.get("rules", []):
            try:
                is_match, reason = check_rule_match_debug(rule, event, other_cs_ids)
                if is_match:
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发! 开始回复...")
                    for reply in rule.get("replies", []):
                        content = reply.get("text", "")
                        if not content: continue
                        delay = random.uniform(reply.get("min", 1), reply.get("max", 3))
                        await asyncio.sleep(delay)
                        await event.reply(content)
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] 强制日志调试模式已启动")
