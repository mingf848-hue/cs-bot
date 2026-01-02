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

# --- Web UI (Tailwind CSS Professional) ---
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
    
    <style>
        body { font-family: 'Inter', sans-serif; }
        /* 自定义滚动条 */
        ::-webkit-scrollbar { width: 6px; height: 6px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 3px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
        
        /* 针对 Textarea 的微调 */
        textarea { font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 12px; }
        
        /* 动画 */
        .fade-enter-active, .fade-leave-active { transition: opacity 0.3s ease; }
        .fade-enter-from, .fade-leave-to { opacity: 0; }
    </style>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        primary: '#3B82F6',
                        secondary: '#64748B',
                        success: '#10B981',
                        danger: '#EF4444',
                        slate: { 50:'#f8fafc', 100:'#f1f5f9', 200:'#e2e8f0', 800:'#1e293b', 900:'#0f172a' }
                    }
                }
            }
        }
    </script>
</head>
<body class="text-slate-800 antialiased">
<div id="app" class="min-h-screen pb-20">
    
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 bg-opacity-90 backdrop-blur-md">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between h-16">
                <div class="flex items-center gap-3">
                    <div class="bg-primary/10 text-primary p-2 rounded-lg">
                        <i class="fa-solid fa-robot text-xl"></i>
                    </div>
                    <div>
                        <h1 class="text-lg font-bold text-slate-900 tracking-tight">AutoResponder <span class="text-xs font-medium text-primary bg-primary/10 px-2 py-0.5 rounded-full ml-1">Pro</span></h1>
                        <p class="text-xs text-slate-500 font-medium">自动化响应规则管理系统</p>
                    </div>
                </div>
                <div class="flex items-center gap-4">
                    <div class="flex items-center gap-2 bg-slate-100 px-3 py-1.5 rounded-full border border-slate-200">
                        <span class="relative flex h-2.5 w-2.5">
                          <span v-if="config.enabled" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
                          <span :class="config.enabled ? 'bg-success' : 'bg-slate-400'" class="relative inline-flex rounded-full h-2.5 w-2.5"></span>
                        </span>
                        <label class="text-xs font-semibold text-slate-600 cursor-pointer select-none">
    <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden">
    System {{ config.enabled ? 'Online' : 'Offline' }}
</label>
                    </div>
                    <button @click="saveConfig" class="bg-slate-900 hover:bg-slate-800 text-white px-4 py-2 rounded-lg text-sm font-medium transition-all shadow-lg shadow-slate-900/20 flex items-center gap-2">
                        <i class="fa-solid fa-floppy-disk"></i> 保存配置
                    </button>
                </div>
            </div>
        </div>
    </nav>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-6">
            
            <div v-for="(rule, index) in config.rules" :key="index" 
                 class="group bg-white rounded-xl border border-slate-200 shadow-sm hover:shadow-xl hover:border-primary/30 transition-all duration-300 flex flex-col overflow-hidden relative">
                
                <div class="px-5 py-4 border-b border-slate-100 bg-slate-50/50 flex justify-between items-center">
                    <div class="flex items-center gap-2 flex-1">
                        <i class="fa-solid fa-hashtag text-slate-400 text-sm"></i>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-sm font-bold text-slate-800 focus:ring-0 placeholder-slate-400 w-full" placeholder="输入规则名称...">
                    </div>
                    <button @click="removeRule(index)" class="text-slate-400 hover:text-danger hover:bg-red-50 p-1.5 rounded transition-colors" title="删除规则">
                        <i class="fa-regular fa-trash-can"></i>
                    </button>
                </div>

                <div class="p-5 flex-1 flex flex-col gap-5">
                    
                    <div class="space-y-3">
                        <div class="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-wider">
                            <i class="fa-solid fa-satellite-dish text-primary"></i> 监听配置
                        </div>
                        <div class="grid grid-cols-1 gap-3">
                            <div class="relative">
                                <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')"
                                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all resize-none h-20"
                                    placeholder="-100xxxxxx (每行一个群ID)"></textarea>
                                <div class="absolute right-2 bottom-2 text-[10px] text-slate-400 bg-slate-100 px-1.5 rounded">Group IDs</div>
                            </div>
                            <div class="relative">
                                <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')"
                                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary transition-all resize-none h-20"
                                    placeholder="留空则匹配所有消息..."></textarea>
                                <div class="absolute right-2 bottom-2 text-[10px] text-slate-400 bg-slate-100 px-1.5 rounded">Keywords</div>
                            </div>
                        </div>
                    </div>

                    <div class="space-y-3 pt-2 border-t border-slate-100">
                        <div class="flex items-center justify-between text-xs font-bold text-slate-500 uppercase tracking-wider">
                            <div class="flex items-center gap-2"><i class="fa-solid fa-filter text-primary"></i> 过滤 & 冷却</div>
                        </div>
                        <div class="grid grid-cols-2 gap-3">
                            <div class="col-span-1">
                                <select v-model="rule.sender_mode" class="w-full bg-slate-50 border border-slate-200 text-slate-700 text-xs rounded-lg p-2 focus:ring-2 focus:ring-primary/20 focus:border-primary">
                                    <option value="exclude">🚫 排除名单</option>
                                    <option value="include">✅ 仅限白名单</option>
                                </select>
                            </div>
                            <div class="col-span-1 relative">
                                <input type="number" v-model.number="rule.cooldown" class="w-full bg-slate-50 border border-slate-200 text-slate-700 text-xs rounded-lg p-2 focus:ring-2 focus:ring-primary/20 focus:border-primary">
                                <span class="absolute right-3 top-2 text-xs text-slate-400 pointer-events-none">秒</span>
                            </div>
                            <div class="col-span-2">
                                <input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" 
                                    class="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-xs focus:ring-2 focus:ring-primary/20 focus:border-primary truncate"
                                    placeholder="前缀列表 (YY_, admin)... 使用换行分隔">
                            </div>
                        </div>
                    </div>

                    <div class="space-y-3 pt-2 border-t border-slate-100 flex-1">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-wider">
                                <i class="fa-solid fa-bolt text-primary"></i> 执行流 (Timeline)
                            </div>
                            <button @click="rule.replies.push({text:'', min:2, max:4})" class="text-[10px] bg-primary/10 text-primary px-2 py-1 rounded hover:bg-primary hover:text-white transition-colors">
                                + 添加步骤
                            </button>
                        </div>
                        
                        <div class="space-y-2 relative">
                            <div class="absolute left-3 top-2 bottom-2 w-0.5 bg-slate-200 z-0"></div>
                            
                            <div v-if="rule.replies.length === 0" class="text-center py-4 text-xs text-slate-400 bg-slate-50 rounded-lg border border-dashed border-slate-200 z-10 relative">
                                暂无回复动作
                            </div>

                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="relative z-10 group/item">
                                <div class="flex items-start gap-2">
                                    <div class="flex flex-col items-center bg-white border border-slate-200 rounded shadow-sm px-1 py-0.5 min-w-[40px] z-10 mt-1">
                                        <div class="flex items-center gap-0.5 text-[10px] font-mono text-slate-500">
                                            <input v-model.number="reply.min" class="w-3 text-center bg-transparent border-b border-dashed border-slate-300 focus:outline-none focus:border-primary p-0">
                                            <span>-</span>
                                            <input v-model.number="reply.max" class="w-3 text-center bg-transparent border-b border-dashed border-slate-300 focus:outline-none focus:border-primary p-0">
                                        </div>
                                        <div class="text-[9px] text-slate-300">sec</div>
                                    </div>
                                    
                                    <div class="flex-1 bg-white border border-slate-200 rounded-lg p-2 flex items-center gap-2 shadow-sm group-hover/item:border-primary/50 group-hover/item:shadow-md transition-all">
                                        <div class="w-1.5 h-1.5 rounded-full bg-primary flex-shrink-0"></div>
                                        <input v-model="reply.text" class="flex-1 text-xs border-none p-0 focus:ring-0 text-slate-700 placeholder-slate-300" placeholder="发送回复内容...">
                                        <button @click="rule.replies.splice(rIndex, 1)" class="text-slate-300 hover:text-danger transition-colors px-1">
                                            <i class="fa-solid fa-xmark"></i>
                                        </button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <div @click="addRule" class="border-2 border-dashed border-slate-300 rounded-xl flex flex-col items-center justify-center p-10 cursor-pointer hover:border-primary hover:bg-blue-50/50 transition-all min-h-[400px] group">
                <div class="w-16 h-16 bg-slate-100 rounded-full flex items-center justify-center text-slate-400 group-hover:bg-blue-100 group-hover:text-primary transition-all mb-4">
                    <i class="fa-solid fa-plus text-2xl"></i>
                </div>
                <h3 class="text-slate-500 font-semibold group-hover:text-primary">添加新规则卡片</h3>
                <p class="text-xs text-slate-400 mt-2">点击创建一个新的监听任务</p>
            </div>

        </div>
    </main>

    <div class="fixed bottom-6 right-6 z-50 transition-all duration-500 transform translate-y-20 opacity-0" :class="{'translate-y-0 opacity-100': toast.show}">
        <div class="bg-slate-800 text-white px-6 py-3 rounded-lg shadow-2xl flex items-center gap-3">
            <i v-if="toast.type==='success'" class="fa-solid fa-circle-check text-green-400 text-lg"></i>
            <i v-else class="fa-solid fa-triangle-exclamation text-red-400 text-lg"></i>
            <span class="font-medium text-sm">{{ toast.msg }}</span>
        </div>
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

            const addRule = () => {
                config.rules.push({
                    name: 'New Rule #' + (config.rules.length + 1),
                    groups: [], keywords: [], sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{text: '', min: 2, max: 4}]
                });
            };
            
            const removeRule = (index) => {
                if(confirm('确定删除此任务卡片吗？此操作无法撤销。')) config.rules.splice(index, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        showToast('配置已成功保存并生效', 'success');
                    } else {
                        showToast('保存失败: ' + json.msg, 'error');
                    }
                } catch(e) {
                    showToast('网络连接错误', 'error');
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

# [保留原有的后端逻辑，不做任何修改，确保稳定性]
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

    logger.info("🛠️ [Monitor] Ultimate UI 已启动")
