import asyncio
import logging
import time
import random
import json
import os
import re
import requests
from flask import request, jsonify, Response
from telethon import events

try: import redis
except ImportError: redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"
global_main_handler = None
global_client = None

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "default_rule",
            "name": "催单监控规则",
            "groups": [-1002169616907],
            "keywords": [],
            "regex": "(?=.*催)(?=.*5\\d{15})", # 默认填好，你可以在UI改
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                {"type": "text", "text": "请稍等ART", "min": 2, "max": 4},
                {"type": "teams_webhook", "webhook_url": "", "min": 0, "max": 1}
            ]
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
    
    # 确保每个规则都有 regex 字段，防止旧配置报错
    for rule in current_config["rules"]:
        if "regex" not in rule: rule["regex"] = ""
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
            
            # 确保保存 regex
            if "regex" not in rule: rule["regex"] = ""
            
            for r in rule.get("replies", []):
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
                if "type" not in r: r["type"] = "text"
                if "webhook_url" not in r: r["webhook_url"] = ""
        
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

# --- 核心 UI 代码 (Web 界面) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-slate-50">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>AutoResponder Pro</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>body { font-family: 'Inter', sans-serif; } ::-webkit-scrollbar { width: 6px; height: 6px; } ::-webkit-scrollbar-track { background: transparent; } ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; } textarea { font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 12px; } </style>
    <script> tailwind.config = { theme: { extend: { colors: { primary: '#3B82F6', secondary: '#64748B', success: '#10B981', danger: '#EF4444', slate: { 50:'#f8fafc', 100:'#f1f5f9', 200:'#e2e8f0', 800:'#1e293b', 900:'#0f172a' } } } } } </script>
</head>
<body class="text-slate-800 antialiased">
<div id="app" class="min-h-screen pb-20">
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 bg-opacity-90 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between h-16">
                <div class="flex items-center gap-3">
                    <div class="bg-primary/10 text-primary p-2 rounded-lg"><i class="fa-solid fa-robot text-xl"></i></div>
                    <div><h1 class="text-lg font-bold text-slate-900 tracking-tight">AutoResponder <span class="text-xs font-medium text-primary bg-primary/10 px-2 py-0.5 rounded-full ml-1">Pro</span></h1></div>
                </div>
                <div class="flex items-center gap-4">
                    <label class="flex items-center gap-2 bg-slate-100 px-3 py-1.5 rounded-full border border-slate-200 cursor-pointer">
                        <span class="relative flex h-2.5 w-2.5"><span v-if="config.enabled" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span><span :class="config.enabled ? 'bg-success' : 'bg-slate-400'" class="relative inline-flex rounded-full h-2.5 w-2.5"></span></span>
                        <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden"><span class="text-xs font-semibold text-slate-600">System {{ config.enabled ? 'Online' : 'Offline' }}</span>
                    </label>
                    <button @click="saveConfig" class="bg-slate-900 hover:bg-slate-800 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-lg flex items-center gap-2"><i class="fa-solid fa-floppy-disk"></i> 保存</button>
                </div>
            </div>
        </div>
    </nav>
    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            <div v-for="(rule, index) in config.rules" :key="index" class="bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-xl transition-all flex flex-col relative">
                <div class="px-5 py-4 border-b border-slate-100 bg-slate-50/50 flex justify-between items-center">
                    <div class="flex items-center gap-2 flex-1"><i class="fa-solid fa-hashtag text-slate-400 text-sm"></i><input v-model="rule.name" class="bg-transparent border-none p-0 text-sm font-bold w-full" placeholder="规则名称..."></div>
                    <button @click="removeRule(index)" class="text-slate-400 hover:text-danger p-1.5"><i class="fa-regular fa-trash-can"></i></button>
                </div>
                <div class="p-5 flex-1 flex flex-col gap-5">
                    <div class="space-y-3">
                        <div class="text-xs font-bold text-slate-500 uppercase"><i class="fa-solid fa-satellite-dish text-primary"></i> 监听配置</div>
                        <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs h-16" placeholder="群组 ID (每行一个)"></textarea>
                        
                        <div class="grid grid-cols-1 gap-2">
                             <div class="relative">
                                <label class="text-[10px] text-slate-400 uppercase font-bold">关键词 (可选)</label>
                                <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs h-12" placeholder="关键词..."></textarea>
                             </div>
                             <div class="relative">
                                <label class="text-[10px] text-slate-400 uppercase font-bold flex items-center gap-1">高级正则 <span class="bg-purple-100 text-purple-600 px-1 rounded">Pro</span></label>
                                <input v-model="rule.regex" class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs font-mono text-purple-600 focus:ring-primary focus:border-primary" placeholder="例如: (?=.*催)(?=.*5\d{15})">
                             </div>
                        </div>
                    </div>
                    <div class="space-y-3 pt-2 border-t border-slate-100">
                        <div class="text-xs font-bold text-slate-500 uppercase"><i class="fa-solid fa-filter text-primary"></i> 过滤 & 冷却</div>
                        <div class="grid grid-cols-2 gap-3">
                            <select v-model="rule.sender_mode" class="bg-slate-50 border border-slate-200 text-xs rounded-lg p-2"><option value="exclude">🚫 排除名单</option><option value="include">✅ 白名单</option></select>
                            <input type="number" v-model.number="rule.cooldown" class="bg-slate-50 border border-slate-200 text-xs rounded-lg p-2" placeholder="CD(秒)">
                            <input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" class="col-span-2 bg-slate-50 border border-slate-200 rounded-lg p-2 text-xs" placeholder="前缀列表...">
                        </div>
                    </div>
                    <div class="space-y-3 pt-2 border-t border-slate-100 flex-1">
                        <div class="flex justify-between"><div class="text-xs font-bold text-slate-500 uppercase"><i class="fa-solid fa-bolt text-primary"></i> 执行流</div><button @click="rule.replies.push({type:'text',text:'',min:2,max:4})" class="text-[10px] bg-primary/10 text-primary px-2 py-1 rounded">+ 步骤</button></div>
                        <div class="space-y-2">
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="flex gap-2 items-center">
                                <div class="w-1.5 h-1.5 rounded-full bg-primary"></div>
                                <select v-model="reply.type" class="w-20 text-[10px] bg-slate-50 border-none rounded py-1"><option value="text">💬 回复</option><option value="forward">🔀 转发</option><option value="teams_webhook">📢 Teams/Ntfy</option></select>
                                <input v-if="reply.type === 'teams_webhook'" v-model="reply.webhook_url" class="flex-1 text-xs border-b border-slate-200 focus:outline-none focus:border-primary text-purple-600" placeholder="填写你的 Ntfy 链接...">
                                <input v-else-if="reply.type === 'forward'" v-model="reply.forward_to" class="flex-1 text-xs border-b border-slate-200 focus:outline-none focus:border-primary text-blue-600" placeholder="Target ID...">
                                <input v-else v-model="reply.text" class="flex-1 text-xs border-b border-slate-200 focus:outline-none focus:border-primary" placeholder="回复内容...">
                                <button @click="rule.replies.splice(rIndex, 1)" class="text-slate-300 hover:text-danger"><i class="fa-solid fa-xmark"></i></button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
            <div @click="addRule" class="border-2 border-dashed border-slate-300 rounded-xl flex flex-col items-center justify-center p-10 cursor-pointer hover:border-primary hover:bg-blue-50/50 transition-all min-h-[400px] group">
                <div class="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center text-slate-400 group-hover:bg-blue-100 group-hover:text-primary transition-all mb-4"><i class="fa-solid fa-plus text-2xl"></i></div>
                <h3 class="text-slate-500 font-semibold group-hover:text-primary">添加规则</h3>
            </div>
        </div>
    </main>
    <div class="fixed bottom-6 right-6 z-50 transition-all duration-500 transform translate-y-20 opacity-0" :class="{'translate-y-0 opacity-100': toast.show}"><div class="bg-slate-800 text-white px-6 py-3 rounded-lg shadow-2xl flex items-center gap-3"><span class="font-medium text-sm">{{ toast.msg }}</span></div></div>
</div>
<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '' });
            fetch('/tool/monitor_settings_json').then(r => r.json()).then(data => { 
                config.enabled = data.enabled; 
                config.rules = (data.rules || []).map(r => {
                    if(r.replies) r.replies = r.replies.map(rep => ({...rep, type: rep.type || 'text', webhook_url: rep.webhook_url || ''}));
                    // 确保 regex 字段存在
                    r.regex = r.regex || ''; 
                    return r;
                });
            });
            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };
            const addRule = () => { config.rules.push({ name: 'New Rule #' + (config.rules.length + 1), groups: [], keywords: [], regex: '', sender_mode: 'exclude', sender_prefixes: [], cooldown: 60, replies: [{type:'text', text: '', min: 2, max: 4}] }); };
            const removeRule = (index) => { if(confirm('确定删除?')) config.rules.splice(index, 1); };
            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) { toast.msg = '保存成功'; toast.show = true; setTimeout(() => toast.show = false, 3000); }
                    else { toast.msg = '保存失败: ' + json.msg; toast.show = true; }
                } catch(e) { toast.msg = '网络错误'; toast.show = true; }
            };
            return { config, toast, listToString, stringToList, stringToIntList, addRule, removeRule, saveConfig };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

def analyze_message(rule, event, other_cs_ids, sender_name):
    # 1. 基础检查
    if event.chat_id not in rule.get("groups", []): return False, "群组不符"
    if event.is_reply: return False, "是回复消息"
    if event.out: return False, "Bot自己发送"
    if event.sender_id in other_cs_ids: return False, "ID是客服"
    
    text = event.text or ""
    
    # 2. 关键词检查 (OR 逻辑)
    keywords = rule.get("keywords", [])
    has_keyword = False
    if keywords:
        if any(kw in text for kw in keywords):
            has_keyword = True
    else:
        # 如果没填关键词，默认通过，交给正则去判断
        has_keyword = True 

    # 3. 高级正则检查 (AND 逻辑)
    regex_pattern = rule.get("regex", "")
    if regex_pattern:
        try:
            if not re.search(regex_pattern, text, re.DOTALL):
                return False, "正则不匹配"
            # 如果正则匹配了，也算命中
            has_keyword = True
        except Exception as e:
            logger.error(f"Regex Error: {e}")
            return False, "正则语法错误"
            
    if not has_keyword: return False, "无匹配"

    # 4. 发送者过滤
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    if sender_mode == "exclude" and match_prefix: return False, "前缀被排除"
    elif sender_mode == "include" and not match_prefix: return False, "前缀不在白名单"
    
    # 5. 冷却时间
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中"
    
    return True, "✅ 匹配成功"

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler, global_client
    global_main_handler = main_handler
    global_client = client
    init_redis_connection()
    load_config(main_cs_prefixes)

    @app.route('/zd')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)
    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.json)
        if success: return jsonify({"success": True})
        return jsonify({"success": False, "msg": msg}), 200

    # === Automa 回复接收接口 ===
    @app.route('/api/teams_reply', methods=['POST'])
    def receive_teams_reply():
        try:
            data = request.json
            reply_text = data.get('text', '')
            
            if reply_text and global_client:
                # 默认转发给第一个规则的第一个群
                # (为了简化逻辑，如果你有多个群，建议在 Automa 里不区分，直接发回来)
                target_group = None
                if current_config['rules'] and current_config['rules'][0]['groups']:
                    target_group = current_config['rules'][0]['groups'][0]
                
                if target_group:
                    logger.info(f"📨 [Teams回复] 转发: {reply_text} -> {target_group}")
                    async def send_back():
                        await global_client.send_message(target_group, f"**[Teams 客服回复]**\n{reply_text}")
                    global_client.loop.create_task(send_back())
                    return jsonify({"status": "sent"}), 200
            
            return jsonify({"status": "ignored"}), 200
        except Exception as e:
            logger.error(f"Teams Reply Error: {e}")
            return jsonify({"status": "error"}), 500

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
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发!")
                    rule_id = rule.get("id", str(rule.get("groups")))
                    rule_timers[rule_id] = time.time()
                    
                    for step in rule.get("replies", []):
                        delay = random.uniform(step.get("min", 1), step.get("max", 3))
                        await asyncio.sleep(delay)
                        
                        step_type = step.get("type", "text")

                        if step_type == "forward":
                            target = step.get("forward_to")
                            if target:
                                try:
                                    target_id = int(str(target).strip())
                                    await client.forward_messages(target_id, event.message)
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] 转发失败: {e}")
                                    
                        elif step_type == "teams_webhook":
                            url = step.get("webhook_url")
                            if url and url.startswith("http"):
                                try:
                                    msg_text = event.text or "[媒体消息]"
                                    # 构造 Ntfy 消息
                                    chat_id_str = str(event.chat_id).replace("-100", "")
                                    msg_link = f"https://t.me/c/{chat_id_str}/{event.id}"
                                    
                                    # === 核心逻辑：自动加 ART 后缀 ===
                                    # 这样 Automa 才能识别出这是机器人发的，不会死循环抓取
                                    content_str = f"🔔 {rule.get('name')}\nUser: {sender_name}\n{msg_text}\n{msg_link} ART"
                                    
                                    # 如果是 ntfy，发送纯文本
                                    if "ntfy.sh" in url:
                                        requests.post(url, data=content_str.encode('utf-8'), timeout=5)
                                        logger.info(f"📢 [Monitor] 已推送到 Ntfy (带ART后缀)")
                                    else:
                                        # 兼容普通 Teams Webhook
                                        payload = {
                                            "title": f"🔔 监控触发: {rule.get('name')}",
                                            "text": f"**发送者:** {sender_name}\n\n**内容:** {msg_text}\n\n[点击跳转]({msg_link}) ART"
                                        }
                                        requests.post(url, json=payload, timeout=5)
                                        logger.info(f"📢 [Monitor] 已推送到 Teams")
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] Webhook 异常: {e}")
                                    
                        else:
                            content = step.get("text", "")
                            if not content: continue
                            sent_msg = await event.reply(content)
                            if global_main_handler:
                                try:
                                    fake_event = events.NewMessage.Event(sent_msg)
                                    asyncio.create_task(global_main_handler(fake_event))
                                except: pass
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则错误: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI 已启动")
