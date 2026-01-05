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
            "check_file": False,
            "keywords": [],
            "file_extensions": ["xlsx"],
            "filename_keywords": ["结算"],
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
                },
                {
                    "type": "preempt_check",
                    "min": 0.5,
                    "max": 1.0
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
            
            rule["check_file"] = bool(rule.get("check_file", False))

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

# --- Web UI (Bento Grid / Linear Style + Typography Pro) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-[#F3F4F6]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Monitor Pro v13</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
        
        textarea, input, select { 
            font-family: 'JetBrains Mono', monospace; 
            font-size: 11px; 
            letter-spacing: -0.01em;
        }
        
        .bento-card {
            background: white;
            border: 1px solid #E5E7EB;
            border-radius: 8px;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            transition: all 0.2s ease;
        }
        .bento-card:hover {
            border-color: #D1D5DB;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
        }
        .bento-input {
            background-color: #F9FAFB;
            border: 1px solid #E5E7EB;
            border-radius: 6px;
            color: #374151;
            transition: all 0.15s;
        }
        .bento-input:focus {
            background-color: white;
            border-color: #6366F1;
            ring: 2px solid rgba(99, 102, 241, 0.1);
            outline: none;
        }
        .section-label {
            font-size: 10px;
            font-weight: 700;
            color: #6B7280;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        .recovery-panel {
            background: linear-gradient(135deg, #FFF1F2 0%, #FFF 100%);
            border: 1px solid #FECDD3;
        }
    </style>
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    fontFamily: {
                        sans: ['"Plus Jakarta Sans"', 'sans-serif'],
                        mono: ['"JetBrains Mono"', 'monospace'],
                    },
                    colors: {
                        primary: '#6366F1',
                        slate: { 50:'#f9fafb', 100:'#f3f4f6', 200:'#e5e7eb', 800:'#1f2937' }
                    }
                }
            }
        }
    </script>
</head>
<body class="text-slate-800 antialiased min-h-screen pb-20 font-sans">
<div id="app">
    
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 h-12 flex items-center px-4 justify-between bg-opacity-90 backdrop-blur-sm">
        <div class="flex items-center gap-2">
            <div class="w-6 h-6 bg-primary text-white rounded flex items-center justify-center text-xs">
                <i class="fa-solid fa-bolt"></i>
            </div>
            <span class="font-bold text-sm tracking-tight text-slate-900">Monitor <span class="text-xs text-primary font-medium bg-primary/10 px-1.5 py-0.5 rounded">Pro v13</span></span>
        </div>
        <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 cursor-pointer select-none bg-slate-50 px-2 py-1 rounded border border-slate-200 hover:border-slate-300 transition-colors">
                <div class="w-2 h-2 rounded-full" :class="config.enabled ? 'bg-green-500' : 'bg-slate-300'"></div>
                <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden">
                <span class="text-[11px] font-semibold text-slate-600">{{ config.enabled ? 'Active' : 'Paused' }}</span>
            </label>
            <button @click="saveConfig" class="bg-slate-900 hover:bg-black text-white px-3 py-1 rounded text-[11px] font-bold transition-colors flex items-center gap-1.5 shadow-sm">
                <i class="fa-solid fa-floppy-disk"></i> 保存
            </button>
        </div>
    </nav>

    <main class="max-w-[1400px] mx-auto px-4 py-6 space-y-6">
        
        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            
            <div v-for="(rule, index) in config.rules" :key="index" 
                 class="bento-card flex flex-col overflow-hidden relative group">
                <div class="px-3 py-2 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                    <div class="flex items-center gap-2 flex-1">
                        <span class="text-slate-400 text-[10px] font-mono">#{{index+1}}</span>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-700 focus:ring-0 placeholder-slate-300 w-full font-sans" placeholder="未命名规则">
                    </div>
                    <button @click="removeRule(index)" class="text-slate-300 hover:text-red-500 transition-colors px-1" title="删除">
                        <i class="fa-solid fa-trash text-[10px]"></i>
                    </button>
                </div>
                <div class="p-3 flex flex-col gap-3">
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between">
                            <span class="section-label"><i class="fa-solid fa-eye mr-1"></i>监听来源</span>
                            <label class="flex items-center gap-1 cursor-pointer select-none">
                                <input type="checkbox" v-model="rule.check_file" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0">
                                <span class="text-[10px] text-slate-500 font-medium" :class="{'text-primary': rule.check_file}">文件模式</span>
                            </label>
                        </div>
                        <div class="relative">
                            <textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" rows="1" class="bento-input w-full px-2 py-1.5 resize-none h-8 leading-tight font-mono text-[11px]" placeholder="群ID (换行分隔)"></textarea>
                        </div>
                        <div v-if="!rule.check_file" class="relative">
                            <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="1" class="bento-input w-full px-2 py-1.5 resize-none h-8 leading-tight font-mono text-[11px]" placeholder="文本关键词 (留空匹配所有)"></textarea>
                        </div>
                        <div v-else class="grid grid-cols-2 gap-2">
                            <input :value="listToString(rule.file_extensions).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'file_extensions')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="后缀: xlsx, png">
                            <input :value="listToString(rule.filename_keywords).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'filename_keywords')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="文件名关键词">
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="section-label"><i class="fa-solid fa-filter mr-1"></i>过滤与冷却</div>
                        <div class="grid grid-cols-5 gap-2">
                            <div class="col-span-2">
                                <select v-model="rule.sender_mode" class="bento-input w-full px-1 py-0 h-7 text-[10px] font-sans font-medium">
                                    <option value="exclude">🚫 排除前缀</option>
                                    <option value="include">✅ 只许前缀</option>
                                </select>
                            </div>
                            <div class="col-span-3">
                                <input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" class="bento-input w-full px-2 py-1.5 h-7 truncate font-mono text-[11px]" placeholder="前缀: YY, AA">
                            </div>
                            <div class="col-span-5 relative flex items-center gap-2 mt-0.5">
                                <span class="text-[10px] text-slate-400 font-medium">冷却CD:</span>
                                <input type="number" v-model.number="rule.cooldown" class="bento-input w-16 px-1 py-0 h-6 text-center text-[10px] font-mono font-bold">
                                <span class="text-[10px] text-slate-400 font-medium">秒</span>
                            </div>
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between">
                            <span class="section-label text-primary"><i class="fa-solid fa-bolt mr-1"></i>执行动作流</span>
                            <button @click="rule.replies.push({type:'text', text:'', forward_to:'', min:1, max:3})" class="text-[10px] text-primary hover:bg-primary/5 px-1.5 py-0.5 rounded transition-colors border border-transparent hover:border-primary/10 font-bold">
                                + 添加步骤
                            </button>
                        </div>
                        <div v-if="rule.replies.length === 0" class="text-center py-2 text-[10px] text-slate-300 border border-dashed border-slate-200 rounded font-medium">无动作</div>
                        <div class="space-y-1.5">
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="flex gap-1.5 group/item">
                                <div class="flex flex-col justify-center items-center w-8 bg-slate-50 border border-slate-200 rounded h-auto font-mono">
                                    <input v-model.number="reply.min" class="w-full text-center bg-transparent text-[9px] text-slate-500 focus:outline-none h-3 p-0" placeholder="min">
                                    <div class="w-3 h-px bg-slate-200 my-0.5"></div>
                                    <input v-model.number="reply.max" class="w-full text-center bg-transparent text-[9px] text-slate-500 focus:outline-none h-3 p-0" placeholder="max">
                                </div>
                                <div class="flex-1 bg-slate-50 border border-slate-200 rounded p-1.5 hover:border-primary/30 hover:bg-white transition-all">
                                    <div class="flex items-center gap-1.5 mb-1">
                                        <select v-model="reply.type" class="text-[10px] bg-transparent border-none p-0 text-slate-600 font-bold focus:ring-0 cursor-pointer w-auto font-sans">
                                            <option value="text">💬 发送文本</option>
                                            <option value="forward">🔀 直接转发</option>
                                            <option value="copy_file">📂 转发+新文案</option>
                                            <option value="preempt_check">⚡ 抢答检测 (自删)</option>
                                        </select>
                                        <button @click="rule.replies.splice(rIndex, 1)" class="ml-auto text-slate-300 hover:text-red-400">
                                            <i class="fa-solid fa-xmark text-[10px]"></i>
                                        </button>
                                    </div>
                                    <template v-if="reply.type === 'text'">
                                        <textarea v-model="reply.text" rows="2" class="bento-input w-full px-1.5 py-1 text-[10px] resize-none border-transparent bg-white focus:border-slate-200 font-mono" placeholder="内容... ({time})"></textarea>
                                    </template>
                                    <template v-if="reply.type === 'forward'">
                                        <input v-model="reply.forward_to" class="bento-input w-full px-1.5 py-1 h-6 text-[10px] font-mono text-blue-600" placeholder="目标群ID">
                                    </template>
                                    <template v-if="reply.type === 'copy_file'">
                                        <input v-model="reply.forward_to" class="bento-input w-full px-1.5 py-1 h-6 text-[10px] font-mono text-blue-600 mb-1" placeholder="目标群ID">
                                        <textarea v-model="reply.text" rows="2" class="bento-input w-full px-1.5 py-1 text-[10px] resize-none bg-yellow-50 border-yellow-100 focus:border-yellow-300 font-mono" placeholder="新文案... ({time})"></textarea>
                                    </template>
                                    <template v-if="reply.type === 'preempt_check'">
                                        <div class="px-1.5 py-1 bg-red-50 text-red-500 rounded text-[10px] font-medium border border-red-100 flex items-center gap-2">
                                            <i class="fa-solid fa-user-ninja"></i>
                                            <span>检测到中间有人插话则删除自己</span>
                                        </div>
                                    </template>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <div @click="addRule" class="border border-dashed border-slate-300 rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer hover:border-primary hover:bg-slate-50 transition-all min-h-[200px] text-slate-400 hover:text-primary group">
                <div class="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center mb-2 group-hover:bg-primary/10 transition-colors">
                    <i class="fa-solid fa-plus text-lg"></i>
                </div>
                <span class="text-xs font-bold">新建规则卡片</span>
            </div>
        </div>

        <div class="bento-card recovery-panel p-4 flex flex-col md:flex-row gap-4 items-center justify-between shadow-sm hover:shadow-md transition-all">
            <div class="flex items-center gap-3 w-full md:w-auto">
                <div class="w-10 h-10 bg-red-100 text-red-500 rounded-lg flex items-center justify-center text-xl shrink-0">
                    <i class="fa-solid fa-truck-medical"></i>
                </div>
                <div>
                    <h3 class="text-sm font-bold text-slate-800">突发事件批量回复 (Global Reply)</h3>
                    <p class="text-[10px] text-slate-500 mt-0.5">自动查找我的反馈消息，并回复给<strong class="text-red-500">原提问者</strong> (Original Sender)</p>
                </div>
            </div>
            
            <div class="flex flex-col md:flex-row gap-3 w-full md:w-auto flex-1 justify-end">
                <div class="flex flex-col gap-1 w-full md:w-48">
                    <label class="text-[9px] font-bold text-slate-500 uppercase">查找我的反馈话术</label>
                    <input v-model="recovery.search" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-red-200 focus:border-red-400" placeholder="例如: 场馆技术核实中...">
                </div>
                <div class="flex flex-col gap-1 w-full md:w-48">
                    <label class="text-[9px] font-bold text-slate-500 uppercase">回复给原提问者</label>
                    <input v-model="recovery.reply" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-green-200 focus:border-green-400" placeholder="例如: 已恢复，请刷新重试">
                </div>
                <div class="flex flex-col gap-1 w-full md:w-20">
                    <label class="text-[9px] font-bold text-slate-500 uppercase">范围(小时)</label>
                    <input type="number" v-model.number="recovery.hours" class="bento-input px-2 py-1.5 h-8 text-xs text-center font-bold" placeholder="5">
                </div>
                <div class="flex flex-col gap-1 w-full md:w-24">
                    <label class="text-[9px] font-bold text-slate-500 uppercase">间隔(秒)</label>
                    <div class="flex gap-1">
                        <input type="number" v-model.number="recovery.min" class="bento-input px-1 py-1.5 h-8 text-xs text-center font-bold w-1/2" placeholder="2">
                        <input type="number" v-model.number="recovery.max" class="bento-input px-1 py-1.5 h-8 text-xs text-center font-bold w-1/2" placeholder="5">
                    </div>
                </div>
                <div class="flex items-end">
                    <button @click="runRecovery" :disabled="!recovery.search || !recovery.reply" class="h-8 bg-red-500 hover:bg-red-600 disabled:bg-slate-300 text-white px-4 rounded text-xs font-bold transition-colors flex items-center gap-2 shadow-sm whitespace-nowrap">
                        <i class="fa-solid fa-paper-plane"></i> 执行回复
                    </button>
                </div>
            </div>
        </div>

    </main>

    <div class="fixed bottom-4 right-4 z-50 transition-all duration-300" :class="{'translate-y-20 opacity-0': !toast.show, 'translate-y-0 opacity-100': toast.show}">
        <div class="bg-slate-800 text-white px-3 py-2 rounded shadow-lg flex items-center gap-2 text-xs font-medium">
            <i v-if="toast.type==='success'" class="fa-solid fa-check text-green-400"></i>
            <i v-else class="fa-solid fa-triangle-exclamation text-red-400"></i>
            <span>{{ toast.msg }}</span>
        </div>
    </div>

</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: true, rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });
            // Recovery State with defaults
            const recovery = reactive({ search: '', reply: '', hours: 5, min: 2, max: 5 });

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
                if (val.includes(',')) {
                    rule[key] = val.split(',').map(x=>x.trim()).filter(x=>x);
                } else {
                    rule[key] = val.split('\\n').map(x=>x.trim()).filter(x=>x);
                }
            };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    name: '新规则 #' + (config.rules.length + 1),
                    groups: [], 
                    check_file: false,
                    keywords: [], file_extensions: [], filename_keywords: [],
                    sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{type:'text', text: '', min: 1, max: 2}]
                });
            };
            
            const removeRule = (index) => {
                if(confirm('确定删除此规则？')) config.rules.splice(index, 1);
            };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        showToast('配置已保存', 'success');
                    } else {
                        showToast('保存失败: ' + json.msg, 'error');
                    }
                } catch(e) {
                    showToast('网络错误', 'error');
                }
            };
            
            const runRecovery = async () => {
                const min = recovery.min || 1;
                const max = recovery.max || 3;
                if(!confirm(`⚠️ 确定要执行批量回复吗？\\n\\n范围: 过去 ${recovery.hours} 小时\\n目标: 我发送的 "${recovery.search}" \\n动作: 追溯回复给【原消息发送者】\\n间隔: ${min}-${max} 秒`)) return;
                
                try {
                    const res = await fetch('/api/batch_recovery', { 
                        method: 'POST', 
                        headers: {'Content-Type': 'application/json'}, 
                        body: JSON.stringify(recovery) 
                    });
                    const json = await res.json();
                    if (json.success) {
                        showToast(json.msg, 'success');
                    } else {
                        showToast('执行失败: ' + json.msg, 'error');
                    }
                } catch(e) {
                    showToast('网络请求错误', 'error');
                }
            };

            const showToast = (msg, type) => {
                toast.msg = msg; toast.type = type; toast.show = true;
                setTimeout(() => toast.show = false, 3000);
            };

            return { config, toast, recovery, listToString, stringToList, stringToIntList, addRule, removeRule, saveConfig, runRecovery };
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
    
    check_file = rule.get("check_file", False)
    text = (event.text or "").lower()
    
    if check_file:
        if not event.message.file: return False, "非文件消息"
        file_exts = rule.get("file_extensions", [])
        if file_exts:
            ext = (event.message.file.ext or "").lower().replace('.', '')
            if ext not in file_exts: return False, "后缀不符"
        fn_kws = rule.get("filename_keywords", [])
        if fn_kws:
            filename = ""
            if event.message.file.name: 
                filename = event.message.file.name
            else:
                for attr in event.message.file.attributes:
                    if hasattr(attr, 'file_name'):
                        filename = attr.file_name
                        break
            filename = (filename or "").lower()
            if not any(k.lower() in filename for k in fn_kws):
                return False, "文件名关键词不符"
    else:
        keywords = rule.get("keywords", [])
        if keywords:
            if not any(kw.lower() in text for kw in keywords):
                return False, "文本关键词不符"

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

def format_caption(tpl):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    return tpl.replace('{time}', now_str)

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global_main_handler = main_handler
    init_redis_connection()
    load_config(main_cs_prefixes)
    
    try:
        bot_loop = client.loop
    except:
        try:
            bot_loop = asyncio.get_event_loop()
        except:
            bot_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(bot_loop)

    @app.route('/zd')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)
    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.json)
        if success: return jsonify({"success": True})
        return jsonify({"success": False, "msg": msg}), 200

    @app.route('/api/batch_recovery', methods=['POST'])
    def trigger_batch_recovery():
        data = request.json
        search_kw = data.get('search')
        reply_kw = data.get('reply')
        hours = float(data.get('hours', 5))
        min_delay = float(data.get('min', 2.0))
        max_delay = float(data.get('max', 5.0))
        
        if not search_kw or not reply_kw:
            return jsonify({"success": False, "msg": "参数不完整"}), 200

        asyncio.run_coroutine_threadsafe(
            run_batch_recovery_task(client, search_kw, reply_kw, hours, min_delay, max_delay),
            bot_loop
        )
        return jsonify({"success": True, "msg": "批量回复任务已在后台启动，请留意日志"}), 200

    async def run_batch_recovery_task(cli, search, reply, hours, min_d, max_d):
        logger.info(f"🚑 [Reply] 开始执行批量回复... 搜索: '{search}', 回复: '{reply}', 范围: {hours}h, 间隔: {min_d}-{max_d}s")
        
        count = 0
        scanned_count = 0
        limit_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        try:
            async for msg in cli.iter_messages(None, search=search):
                if msg.date < limit_time:
                    break
                
                scanned_count += 1
                if not msg.is_group or not msg.out:
                    continue
                
                try:
                    final_text = format_caption(reply)
                    
                    target_id = msg.id 
                    if msg.is_reply and msg.reply_to_msg_id:
                        target_id = msg.reply_to_msg_id
                    
                    await cli.send_message(msg.chat_id, final_text, reply_to=target_id)
                    
                    count += 1
                    logger.info(f"✅ [Reply] 已回复 Group:{msg.chat_id} Origin:{target_id}")
                    # 关键修改：使用前端传入的 min/max 间隔
                    wait_time = random.uniform(min_d, max_d)
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    logger.error(f"❌ [Reply] 回复失败 Group:{msg.chat_id}: {e}")
                    
        except Exception as e:
            logger.error(f"⚠️ [Reply] 全局搜索出错: {e}")
        
        logger.info(f"🏁 [Reply] 任务完成! 扫描匹配 {scanned_count} 条，成功回复 {count} 条")

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug":
            await event.reply("Monitor Debug: Alive v13 Configurable Interval")
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
                    
                    sent_msgs = []
                    
                    for step in rule.get("replies", []):
                        delay = random.uniform(step.get("min", 1), step.get("max", 3))
                        await asyncio.sleep(delay)
                        
                        step_type = step.get("type", "text")

                        if step_type == "forward":
                            target = step.get("forward_to")
                            if target:
                                try:
                                    target_id = int(str(target).strip())
                                    msg = await client.forward_messages(target_id, event.message)
                                    sent_msgs.append(msg)
                                    logger.info(f"➡️ [Monitor] Forward -> {target_id}")
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] 转发失败: {e}")
                        
                        elif step_type == "copy_file":
                            target = step.get("forward_to")
                            caption_tpl = step.get("text", "")
                            if target and event.message.file:
                                try:
                                    target_id = int(str(target).strip())
                                    final_caption = format_caption(caption_tpl)
                                    msg = await client.send_file(target_id, event.message.file.media, caption=final_caption)
                                    sent_msgs.append(msg)
                                    logger.info(f"➡️ [Monitor] CopyFile -> {target_id}")
                                except Exception as e:
                                    logger.error(f"❌ [Monitor] 携带文案转发失败: {e}")

                        elif step_type == "preempt_check":
                            if not sent_msgs: continue 
                            try:
                                logger.info("⚡ [Monitor] 执行抢答检测...")
                                me = await client.get_me()
                                history = await client.get_messages(event.chat_id, limit=10, min_id=event.id)
                                preempted = False
                                for m in history:
                                    if m.sender_id == me.id: continue
                                    if m.sender_id == event.sender_id: continue
                                    logger.warning(f"⚠️ [Monitor] 检测到抢答消息 (ID: {m.id})")
                                    preempted = True
                                    break
                                
                                if preempted:
                                    logger.warning(f"🚫 [Monitor] 触发防撞车机制，撤回 {len(sent_msgs)} 条消息...")
                                    await client.delete_messages(event.chat_id, sent_msgs)
                                    sent_msgs = [] 
                                    break
                            except Exception as e:
                                logger.error(f"❌ [Monitor] 抢答检测出错: {e}")

                        else:
                            content = step.get("text", "")
                            if not content: continue
                            final_text = format_caption(content)
                            sent_msg = await event.reply(final_text)
                            sent_msgs.append(sent_msg)
                            
                            if global_main_handler:
                                try:
                                    fake_event = events.NewMessage.Event(sent_msg)
                                    asyncio.create_task(global_main_handler(fake_event))
                                except: pass
                    break
            except Exception as e:
                logger.error(f"❌ [Monitor] 规则执行错误: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v13 (Configurable Interval) 已启动")
