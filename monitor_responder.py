import asyncio
import logging
import time
import random
import json
import os
import re  # <--- 必须确保这一行存在
from flask import request, jsonify, Response
from telethon import events

try: import redis
except ImportError: redis = None

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
            
    logger.info("-" * 30)
    logger.info(f"✅ [Monitor] 配置加载完成，共 {len(current_config['rules'])} 条规则")
    for i, rule in enumerate(current_config['rules']):
        logger.info(f"   规则 {i+1}: {rule.get('name')} | 监控群: {rule.get('groups')}")
    logger.info("-" * 30)

def save_config(new_config):
    global current_config
    try:
        # 数据清洗与容错
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式 (Missing rules)"

        for rule in new_config.get("rules", []):
            clean_groups = []
            raw_groups = rule.get("groups", [])
            # 兼容：如果前端发来的是字符串（被改坏了的情况），尝试分割
            if isinstance(raw_groups, str):
                raw_groups = raw_groups.split('\n')
                
            for g in raw_groups:
                g_str = str(g).strip()
                # 强力提取：只要包含数字就尝试提取
                # 比如 "-100123(备注)" -> "-100123"
                match = re.search(r'-?\d+', g_str)
                if match:
                    try:
                        clean_groups.append(int(match.group()))
                    except: pass
            rule["groups"] = clean_groups
            
            # 数值字段容错
            try: rule["cooldown"] = int(rule.get("cooldown", 60))
            except: rule["cooldown"] = 60

            for r in rule.get("replies", []):
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
        
        # 尝试写入 Redis
        if redis_client:
            try:
                redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
            except Exception as e:
                logger.error(f"Redis Write Error: {e}")
                # Redis 失败不影响文件保存
        
        # 写入文件
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        
        current_config = new_config
        logger.info(f"💾 [Monitor] 配置已更新并保存 (规则数: {len(new_config['rules'])})")
        return True, "保存成功"
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存失败: {e}")
        # 返回具体错误信息给前端
        return False, str(e)

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
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: rgba(0,0,0,0.8); color: white; padding: 10px 20px; border-radius: 20px; opacity: 0; transition: 0.3s; pointer-events: none; z-index: 999; }
        .toast.show { opacity: 1; }
        .error-msg { color: red; font-size: 12px; margin-top: 5px; }
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
                <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" style="height:80px" placeholder="-100xxxxxx(备注)"></textarea>
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
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        toast.msg = "✅ 保存成功";
                    } else {
                        // 显示具体的错误信息
                        toast.msg = "❌ 保存失败: " + (json.msg || "未知错误");
                        console.error(json.msg);
                    }
                } catch(e) {
                    toast.msg = "❌ 网络错误或服务器崩溃(500)";
                }
                toast.show = true; setTimeout(()=>toast.show=false, 3000);
            };

            return { config, toast, listToString, stringToList, stringToIntList, addRule, saveConfig };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

# --- 核心判断逻辑 (/debug) ---
def analyze_message(rule, event, other_cs_ids, sender_name):
    target_groups = rule.get("groups", [])
    
    if event.chat_id not in target_groups:
        return False, f"群组不符 (当前: {event.chat_id})"
    
    if event.is_reply:
        return False, "是回复消息 (忽略)"
        
    if event.out: return False, "Bot自己发送"
    if event.sender_id in other_cs_ids:
        return False, f"ID是客服 ({event.sender_id})"

    text = event.text or ""
    keywords = rule.get("keywords", [])
    if keywords and not any(kw in text for kw in keywords):
        return False, f"无匹配关键词 (需: {keywords})"

    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    
    if sender_mode == "exclude" and match_prefix:
        return False, f"前缀被排除 ({sender_name})"
    elif sender_mode == "include" and not match_prefix:
        return False, f"前缀不在白名单 ({sender_name})"

    # 冷却
    rule_id = rule.get("id", str(target_groups))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    cooldown = rule.get("cooldown", 60)
    if now - last_time < cooldown:
        return False, f"冷却中 (剩余 {int(cooldown - (now - last_time))}s)"
    
    return True, "✅ 匹配成功"

def init_monitor(client, app, other_cs_ids, main_cs_prefixes):
    init_redis_connection()
    load_config(main_cs_prefixes)

    @app.route('/tool/monitor_settings')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
    
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)

    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.json)
        # 即使失败也返回 200，让前端能读取到错误信息 msg
        if success: return jsonify({"success": True})
        return jsonify({"success": False, "msg": msg}), 200

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug":
            debug_report = f"🛠️ **Monitor 诊断报告**\nChatID: `{event.chat_id}`\nUser: `{event.sender_id}`\n"
            try:
                sender = await event.get_sender()
                s_name = getattr(sender, 'first_name', 'Unknown')
                debug_report += f"SenderName: `{s_name}`\n\n"
                
                for i, rule in enumerate(current_config.get("rules", [])):
                    match, reason = analyze_message(rule, event, other_cs_ids, s_name)
                    icon = "✅" if match else "❌"
                    debug_report += f"Rule {i+1} ({rule.get('name')}): {icon} {reason}\n"
                
                await event.reply(debug_report)
                return
            except Exception as e:
                await event.reply(f"诊断出错: {e}")
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
                        await event.reply(content)
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] 防弹版已启动 (含正则清洗)")
