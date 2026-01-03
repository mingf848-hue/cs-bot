import asyncio
import logging
import time
import random
import json
import os
import re
from datetime import datetime, timedelta, timezone
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
            "check_file": False,          # 是否开启文件检测
            "keywords": [],               # 普通模式：文本关键词
            "file_extensions": ["xlsx"],  # 文件模式：后缀
            "filename_keywords": ["结算"],# 文件模式：文件名关键词
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                {
                    "type": "copy_file", 
                    "forward_to": -100123456789, 
                    "text": "#文件转发\n收到一份报表\n时间：{time}",
                    "min": 1, 
                    "max": 2
                }
            ]
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
redis_client = None

# 北京时区
BJ_TZ = timezone(timedelta(hours=8))

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
        # 兼容旧配置
        if "check_file" not in rule: rule["check_file"] = False
        if "filename_keywords" not in rule: rule["filename_keywords"] = []
        
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
            
            # 确保 check_file 是布尔值
            rule["check_file"] = bool(rule.get("check_file", False))

            # 清洗文件配置
            clean_exts = []
            raw_exts = rule.get("file_extensions", [])
            if isinstance(raw_exts, str): raw_exts = raw_exts.split('\n')
            for ext in raw_exts:
                e = str(ext).strip().lower().replace('.', '')
                if e: clean_exts.append(e)
            rule["file_extensions"] = clean_exts

            clean_fn_kws = []
            raw_fn_kws = rule.get("filename_keywords", [])
            if isinstance(raw_fn_kws, str): raw_fn_kws = raw_fn_kws.split('\n')
            for k in raw_fn_kws:
                k = str(k).strip()
                if k: clean_fn_kws.append(k)
            rule["filename_keywords"] = clean_fn_kws
            
            # 清洗前缀列表
            clean_prefixes = []
            raw_prefixes = rule.get("sender_prefixes", [])
            if isinstance(raw_prefixes, str): raw_prefixes = raw_prefixes.split('\n')
            for p in raw_prefixes:
                p = str(p).strip()
                if p: clean_prefixes.append(p)
            rule["sender_prefixes"] = clean_prefixes
            
            try: rule["cooldown"] = int(rule.get("cooldown", 60))
            except: rule["cooldown"] = 60
            for r in rule.get("replies", []):
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
                if "type" not in r: r["type"] = "text"
        
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
    <title>Monitor Pro v4</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    
    <style>
        body { font-family: 'Inter', sans-serif; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
        textarea { font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-size: 11px; line-height: 1.4; }
        input, select { font-size: 12px; }
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
    
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 bg-opacity-90 backdrop-blur-md shadow-sm">
        <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
            <div class="flex justify-between h-14">
                <div class="flex items-center gap-3">
                    <div class="bg-primary/10 text-primary p-1.5 rounded-lg">
                        <i class="fa-solid fa-robot text-lg"></i>
                    </div>
                    <div>
                        <h1 class="text-base font-bold text-slate-900 tracking-tight">Monitor <span class="text-[10px] font-medium text-primary bg-primary/10 px-1.5 py-0.5 rounded-full ml-1">Pro v4</span></h1>
                    </div>
                </div>
                <div class="flex items-center gap-3">
                    <div class="flex items-center gap-2 bg-slate-100 px-3 py-1 rounded-full border border-slate-200">
                        <span class="relative flex h-2 w-2">
                          <span v-if="config.enabled" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span>
                          <span :class="config.enabled ? 'bg-success' : 'bg-slate-400'" class="relative inline-flex rounded-full h-2 w-2"></span>
                        </span>
                        <label class="text-[11px] font-semibold text-slate-600 cursor-pointer select-none">
                            <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden">
                            System {{ config.enabled ? 'On' : 'Off' }}
                        </label>
                    </div>
                    <button @click="saveConfig" class="bg-slate-900 hover:bg-slate-800 text-white px-3 py-1.5 rounded-md text-xs font-medium transition-all shadow-md flex items-center gap-1.5">
                        <i class="fa-solid fa-floppy-disk"></i> 保存
                    </button>
                </div>
            </div>
        </div>
    </nav>

    <main class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-5">
            
            <div v-for="(rule, index) in config.rules" :key="index" 
                 class="group bg-white rounded-lg border border-slate-200 shadow-sm hover:shadow-lg hover:border-primary/30 transition-all duration-200 flex flex-col overflow-hidden relative">
                
                <div class="px-4 py-3 border-b border-slate-100 bg-slate-50/50 flex justify-between items-center">
                    <div class="flex items-center gap-2 flex-1">
                        <i class="fa-solid fa-hashtag text-slate-400 text-xs"></i>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-800 focus:ring-0 placeholder-slate-400 w-full" placeholder="输入规则名称...">
                    </div>
                    <button @click="removeRule(index)" class="text-slate-400 hover:text-danger hover:bg-red-50 p-1 rounded transition-colors" title="删除">
                        <i class="fa-regular fa-trash-can text-xs"></i>
                    </button>
                </div>

                <div class="p-4 flex-1 flex flex-col gap-4">
                    
                    <div class="space-y-2">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-1.5 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                                <i class="fa-solid fa-satellite-dish text-primary"></i> 监听配置
                            </div>
                            <label class="flex items-center gap-1.5 cursor-pointer select-none bg-slate-100 hover:bg-slate-200 px-2 py-0.5 rounded transition-colors">
                                <input type="checkbox" v-model="rule.check_file" class="w-3 h-3 rounded border-slate-300 text-primary focus:ring-primary">
                                <span class="text-[10px] font-semibold" :class="rule.check_file ? 'text-primary' : 'text-slate-500'">文件模式</span>
                            </label>
                        </div>
                        
                        <div class="space-y-2">
                            <div class="relative">
                                <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" rows="2"
                                    class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-primary/20 focus:border-primary transition-all resize-y"
                                    placeholder="-100xxxxxx"></textarea>
                                <div class="absolute right-1 bottom-1 text-[9px] text-slate-400 bg-slate-100 px-1 rounded opacity-60">IDs</div>
                            </div>
                            
                            <div v-if="!rule.check_file" class="relative animate-fade-in">
                                <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="2"
                                    class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-primary/20 focus:border-primary transition-all resize-y"
                                    placeholder="文本关键词..."></textarea>
                                <div class="absolute right-1 bottom-1 text-[9px] text-slate-400 bg-slate-100 px-1 rounded opacity-60">Keywords</div>
                            </div>

                            <div v-else class="grid grid-cols-2 gap-2 animate-fade-in">
                                <div class="relative">
                                    <textarea :value="listToString(rule.file_extensions)" @input="stringToList($event, rule, 'file_extensions')" rows="2"
                                        class="w-full bg-yellow-50 border border-yellow-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-primary/20 focus:border-primary transition-all resize-y"
                                        placeholder="png, xlsx"></textarea>
                                    <div class="absolute right-1 bottom-1 text-[9px] text-yellow-600 bg-yellow-100 px-1 rounded opacity-60">Exts</div>
                                </div>
                                <div class="relative">
                                    <textarea :value="listToString(rule.filename_keywords)" @input="stringToList($event, rule, 'filename_keywords')" rows="2"
                                        class="w-full bg-yellow-50 border border-yellow-200 rounded px-2 py-1.5 focus:ring-1 focus:ring-primary/20 focus:border-primary transition-all resize-y"
                                        placeholder="文件名关键词"></textarea>
                                    <div class="absolute right-1 bottom-1 text-[9px] text-yellow-600 bg-yellow-100 px-1 rounded opacity-60">Name</div>
                                </div>
                            </div>

                        </div>
                    </div>

                    <div class="space-y-2 pt-2 border-t border-slate-100">
                        <div class="flex items-center gap-1.5 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                            <i class="fa-solid fa-filter text-primary"></i> 过滤 & 冷却
                        </div>
                        <div class="grid grid-cols-2 gap-2">
                            <div class="col-span-1">
                                <select v-model="rule.sender_mode" class="w-full bg-slate-50 border border-slate-200 text-slate-700 rounded px-2 py-1 focus:ring-1 focus:ring-primary/20 focus:border-primary h-8">
                                    <option value="exclude">🚫 排除名单</option>
                                    <option value="include">✅ 仅限白名单</option>
                                </select>
                            </div>
                            <div class="col-span-1 relative">
                                <input type="number" v-model.number="rule.cooldown" class="w-full bg-slate-50 border border-slate-200 text-slate-700 rounded px-2 py-1 focus:ring-1 focus:ring-primary/20 focus:border-primary h-8">
                                <span class="absolute right-2 top-2 text-[10px] text-slate-400 pointer-events-none">秒</span>
                            </div>
                            <div class="col-span-2 relative">
                                <input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" 
                                    class="w-full bg-slate-50 border border-slate-200 rounded px-2 py-1 text-slate-600 focus:ring-1 focus:ring-primary/20 focus:border-primary truncate h-8"
                                    placeholder="前缀列表 (YY_, admin)... 逗号或换行分隔">
                                <i class="fa-solid fa-user-tag absolute right-2 top-2.5 text-slate-300 text-xs pointer-events-none"></i>
                            </div>
                        </div>
                    </div>

                    <div class="space-y-2 pt-2 border-t border-slate-100 flex-1">
                        <div class="flex items-center justify-between">
                            <div class="flex items-center gap-1.5 text-[10px] font-bold text-slate-500 uppercase tracking-wider">
                                <i class="fa-solid fa-bolt text-primary"></i> Timeline
                            </div>
                            <button @click="rule.replies.push({type:'text', text:'', forward_to:'', min:2, max:4})" class="text-[9px] bg-primary/10 text-primary px-1.5 py-0.5 rounded hover:bg-primary hover:text-white transition-colors">
                                + 添加
                            </button>
                        </div>
                        
                        <div class="space-y-1.5 relative">
                            <div class="absolute left-2.5 top-2 bottom-2 w-px bg-slate-200 z-0"></div>
                            
                            <div v-if="rule.replies.length === 0" class="text-center py-3 text-[10px] text-slate-400 bg-slate-50 rounded border border-dashed border-slate-200 z-10 relative">
                                无动作
                            </div>

                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="relative z-10 group/item">
                                <div class="flex items-start gap-1.5">
                                    <div class="flex flex-col items-center bg-white border border-slate-200 rounded px-0.5 py-0.5 min-w-[32px] z-10 mt-0.5">
                                        <div class="flex items-center gap-0.5 text-[9px] font-mono text-slate-500">
                                            <input v-model.number="reply.min" class="w-2.5 text-center bg-transparent border-b border-dashed border-slate-300 focus:outline-none focus:border-primary p-0">
                                            <span>-</span>
                                            <input v-model.number="reply.max" class="w-2.5 text-center bg-transparent border-b border-dashed border-slate-300 focus:outline-none focus:border-primary p-0">
                                        </div>
                                    </div>
                                    
                                    <div class="flex-1 bg-white border border-slate-200 rounded p-1.5 flex flex-col gap-1.5 shadow-sm group-hover/item:border-primary/50 transition-all">
                                        <div class="flex items-center gap-1.5">
                                            <div class="w-1 h-1 rounded-full bg-primary flex-shrink-0"></div>
                                            <select v-model="reply.type" class="w-20 text-[10px] bg-slate-50 border-none rounded focus:ring-0 text-slate-500 py-0.5 cursor-pointer h-5">
                                                <option value="text">💬 回复</option>
                                                <option value="forward">🔀 转发</option>
                                                <option value="copy_file">📂 文件+文案</option>
                                            </select>
                                            <button @click="rule.replies.splice(rIndex, 1)" class="ml-auto text-slate-300 hover:text-danger transition-colors px-1">
                                                <i class="fa-solid fa-xmark text-[10px]"></i>
                                            </button>
                                        </div>

                                        <template v-if="reply.type === 'text'">
                                            <textarea v-model="reply.text" rows="2" class="w-full text-xs border border-slate-100 rounded p-1 bg-slate-50 focus:ring-1 focus:ring-primary/20 resize-y" placeholder="回复内容 ({time})..."></textarea>
                                        </template>
                                        
                                        <template v-if="reply.type === 'forward'">
                                            <input v-model="reply.forward_to" class="w-full text-xs border border-slate-100 rounded p-1 bg-slate-50 focus:ring-1 focus:ring-primary/20 font-mono text-blue-600 h-6" placeholder="Target ID">
                                        </template>

                                        <template v-if="reply.type === 'copy_file'">
                                            <input v-model="reply.forward_to" class="w-full text-xs border border-slate-100 rounded p-1 bg-slate-50 focus:ring-1 focus:ring-primary/20 font-mono text-blue-600 h-6 mb-1" placeholder="Target ID">
                                            <textarea v-model="reply.text" rows="2" class="w-full text-xs border border-slate-100 rounded p-1 bg-yellow-50 focus:ring-1 focus:ring-primary/20 resize-y" placeholder="新文案 ({time})..."></textarea>
                                        </template>

                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                </div>
            </div>

            <div @click="addRule" class="border-2 border-dashed border-slate-300 rounded-lg flex flex-col items-center justify-center p-6 cursor-pointer hover:border-primary hover:bg-blue-50/50 transition-all min-h-[300px] group">
                <div class="w-12 h-12 bg-slate-100 rounded-full flex items-center justify-center text-slate-400 group-hover:bg-blue-100 group-hover:text-primary transition-all mb-3">
                    <i class="fa-solid fa-plus text-xl"></i>
                </div>
                <h3 class="text-slate-500 text-sm font-semibold group-hover:text-primary">添加规则</h3>
            </div>

        </div>
    </main>

    <div class="fixed bottom-6 right-6 z-50 transition-all duration-500 transform translate-y-20 opacity-0" :class="{'translate-y-0 opacity-100': toast.show}">
        <div class="bg-slate-800 text-white px-4 py-2 rounded shadow-xl flex items-center gap-2">
            <i v-if="toast.type==='success'" class="fa-solid fa-circle-check text-green-400 text-sm"></i>
            <i v-else class="fa-solid fa-triangle-exclamation text-red-400 text-sm"></i>
            <span class="font-medium text-xs">{{ toast.msg }}</span>
        </div>
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
                .then(data => { 
                    config.enabled = data.enabled; 
                    config.rules = (data.rules || []).map(r => {
                        if(r.replies) {
                            r.replies = r.replies.map(rep => ({...rep, type: rep.type || 'text'}));
                        }
                        if(r.check_file === undefined) r.check_file = false;
                        if(!r.file_extensions) r.file_extensions = [];
                        if(!r.filename_keywords) r.filename_keywords = [];
                        if(!r.sender_prefixes) r.sender_prefixes = [];
                        return r;
                    });
                });

            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { 
                const val = e.target.value;
                // 支持逗号或换行分隔
                if (val.includes(',')) {
                    rule[key] = val.split(',').map(x=>x.trim()).filter(x=>x);
                } else {
                    rule[key] = val.split('\\n').map(x=>x.trim()).filter(x=>x);
                }
            };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    name: 'New Rule #' + (config.rules.length + 1),
                    groups: [], 
                    check_file: false,
                    keywords: [], file_extensions: [], filename_keywords: [],
                    sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{type:'text', text: '', min: 2, max: 4}]
                });
            };
            
            const removeRule = (index) => {
                if(confirm('Delete this rule?')) config.rules.splice(index, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        showToast('Saved successfully', 'success');
                    } else {
                        showToast('Error: ' + json.msg, 'error');
                    }
                } catch(e) {
                    showToast('Network Error', 'error');
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
<style>
.animate-fade-in { animation: fadeIn 0.3s ease-in-out; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(-3px); } to { opacity: 1; transform: translateY(0); } }
</style>
</body>
</html>
"""

def analyze_message(rule, event, other_cs_ids, sender_name):
    if event.chat_id not in rule.get("groups", []): return False, "群组不符"
    if event.is_reply: return False, "是回复消息"
    if event.out: return False, "Bot自己发送"
    if event.sender_id in other_cs_ids: return False, "ID是客服"
    
    check_file = rule.get("check_file", False)
    text = (event.text or "").lower()
    
    if check_file:
        # --- 文件检测模式 ---
        if not event.message.file: return False, "非文件消息"
        
        # 1. 检查后缀 (如果有配置)
        file_exts = rule.get("file_extensions", [])
        if file_exts:
            ext = (event.message.file.ext or "").lower().replace('.', '')
            if ext not in file_exts: return False, "后缀不符"
            
        # 2. 检查文件名关键词 (如果有配置)
        fn_kws = rule.get("filename_keywords", [])
        if fn_kws:
            filename = ""
            if event.message.file.name: 
                filename = event.message.file.name
            else:
                # 尝试从属性中获取文件名
                for attr in event.message.file.attributes:
                    if hasattr(attr, 'file_name'):
                        filename = attr.file_name
                        break
            
            filename = (filename or "").lower()
            if not any(k.lower() in filename for k in fn_kws):
                return False, "文件名关键词不符"

    else:
        # --- 普通模式 (仅检测文本) ---
        keywords = rule.get("keywords", [])
        if keywords:
            if not any(kw.lower() in text for kw in keywords):
                return False, "文本关键词不符"

    # --- 发送者检查 ---
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    if sender_mode == "exclude" and match_prefix: return False, "前缀被排除"
    elif sender_mode == "include" and not match_prefix: return False, "前缀不在白名单"
    
    # --- 冷却 ---
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中"
    
    return True, "✅ 匹配成功"

def format_caption(tpl):
    """处理动态时间等变量"""
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    return tpl.replace('{time}', now_str)

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global_main_handler = main_handler
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

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug":
            await event.reply("Monitor Debug: Alive v4")
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
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发! 开始执行流程...")
                    rule_id = rule.get("id", str(rule.get("groups")))
                    rule_timers[rule_id] = time.time()
                    
                    for step in rule.get("replies", []):
                        # 1. 随机延迟
                        delay = random.uniform(step.get("min", 1), step.get("max", 3))
                        await asyncio.sleep(delay)
                        
                        # 2. 判断动作类型
                        step_type = step.get("type", "text")

                        if step_type == "forward":
                            target = step.get("forward_to")
                            if target:
                                try:
                                    target_id = int(str(target).strip())
                                    await client.forward_messages(target_id, event.message)
                                    logger.info(f"➡️ [Monitor] Forward -> {target_id}")
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] 转发失败: {e}")
                        
                        elif step_type == "copy_file":
                            # 新功能：复制文件并替换文案
                            target = step.get("forward_to")
                            caption_tpl = step.get("text", "")
                            
                            if target and event.message.file:
                                try:
                                    target_id = int(str(target).strip())
                                    final_caption = format_caption(caption_tpl)
                                    # 针对不同类型的文件发送
                                    await client.send_file(target_id, event.message.file.media, caption=final_caption)
                                    logger.info(f"➡️ [Monitor] CopyFile -> {target_id}")
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] 携带文案转发失败: {e}")
                            else:
                                logger.warning(f"⚠️ [Monitor] CopyFile 忽略: 目标ID为空或原消息无文件")

                        else:
                            # 默认为文本回复
                            content = step.get("text", "")
                            if not content: continue
                            
                            final_text = format_caption(content)
                            sent_msg = await event.reply(final_text)
                            
                            if global_main_handler:
                                try:
                                    fake_event = events.NewMessage.Event(sent_msg)
                                    asyncio.create_task(global_main_handler(fake_event))
                                except: pass
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v4 (Compact & Fixed) 已启动")
