import asyncio
import logging
import time
import random
import json
import os
import re
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, Response, render_template_string
from telethon import events, TelegramClient
from telethon.sessions import StringSession

# [依赖] 导入 pyotp 用于计算谷歌验证码
try:
    import pyotp
except ImportError:
    pyotp = None

# [依赖] 尝试导入 redis
try: 
    import redis
except ImportError: 
    redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"
global_main_handler = None

# ==========================================
# [配置区] 时区设置 - 北京时间 (UTC+8)
# ==========================================
BJ_TZ = timezone(timedelta(hours=8))
TZ_NAME = "北京时间"

# ==========================================
# [多账号版] 全局存储 OTP 验证码
# ==========================================
latest_otp_storage = {}

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True, 
    "approval_keywords": ["同意", "批准", "ok"],
    "schedule": {
        "active": False,
        "start": "09:00",
        "end": "21:00"
    },
    "rules": [
        {
            "id": "deposit_example",
            "name": "代存报备(默认)",
            "enabled": True,
            "groups": [-1002169616907],
            "check_file": False,
            "keywords": ["r:(代|带)存|入[金款]"],
            "enable_approval": False,
            "file_extensions": [],
            "filename_keywords": [],
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                {
                    "type": "amount_logic", 
                    "forward_to": -100123456789, 
                    "text": "2001|⚠️ 金额过大，需领导审批|请稍等ART;;✅ 已报备",
                    "min": 1, 
                    "max": 2
                }
            ],
            "approval_action": {
                "reply_admin": "收到，正在处理",
                "reply_origin": "✅ 领导已批准，代存已报备",
                "forward_to": -100123456789,
                "delay_1_min": 1.0, "delay_1_max": 2.0, 
                "delay_2_min": 1.0, "delay_2_max": 3.0, 
                "delay_3_min": 1.0, "delay_3_max": 2.0  
            }
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
redis_client = None

def init_redis_connection():
    global redis_client
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_URI") or os.environ.get("REDIS_PUBLIC_URL")
    
    if redis and redis_url:
        try:
            redis_url = redis_url.strip()
            # 日志脱敏
            safe_url = re.sub(r':([^@]+)@', ':****@', redis_url)
            logger.info(f"🔗 [Monitor] 尝试连接 Redis: {safe_url}")

            redis_client = redis.from_url(
                redis_url, 
                decode_responses=True, 
                socket_timeout=5, 
                socket_connect_timeout=5
            )
            redis_client.ping()
            logger.info("✅ [Monitor] Redis 数据库连接成功!")
        except Exception as e:
            logger.error(f"❌ [Monitor] Redis 连接失败 (将使用本地模式): {e}")
            redis_client = None
    else:
        logger.warning("⚠️ [Monitor] 未检测到 REDIS_URL 或 REDIS_URI，将仅使用本地文件存储")

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
                    logger.info("📥 [Monitor] 已从 Redis 恢复配置")
        except Exception as e:
            logger.error(f"⚠️ [Monitor] Redis 读取出错: {e}")

    if not loaded and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if "rules" in saved:
                    current_config = saved
                    loaded = True
                    logger.info("📂 [Monitor] 已从本地文件恢复配置")
        except Exception as e:
            logger.error(f"⚠️ [Monitor] 本地文件读取出错: {e}")

    if not loaded: 
        logger.warning("⚠️ [Monitor] 未能加载任何配置，系统使用默认模板启动")
        current_config = DEFAULT_CONFIG.copy()
    
    if "approval_keywords" not in current_config:
        current_config["approval_keywords"] = ["同意", "批准", "ok"]
    if "schedule" not in current_config:
        current_config["schedule"] = DEFAULT_CONFIG["schedule"]

    for rule in current_config["rules"]:
        if "enabled" not in rule: rule["enabled"] = True
        if "check_file" not in rule: rule["check_file"] = False
        if "enable_approval" not in rule: rule["enable_approval"] = False
        if "filename_keywords" not in rule: rule["filename_keywords"] = []
        if "approval_action" not in rule: rule["approval_action"] = {}
        
        aa = rule["approval_action"]
        if "reply_admin" not in aa: aa["reply_admin"] = ""
        if "reply_origin" not in aa: aa["reply_origin"] = ""
        if "forward_to" not in aa: aa["forward_to"] = ""
        if "delay_1_min" not in aa: aa["delay_1_min"] = 1.0
        if "delay_1_max" not in aa: aa["delay_1_max"] = 2.0
        if "delay_2_min" not in aa: aa["delay_2_min"] = 1.0
        if "delay_2_max" not in aa: aa["delay_2_max"] = 3.0
        if "delay_3_min" not in aa: aa["delay_3_min"] = 1.0
        if "delay_3_max" not in aa: aa["delay_3_max"] = 2.0

        if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
            rule["sender_prefixes"] = list(system_cs_prefixes)

def save_config(new_config):
    global current_config
    try:
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式"

        if "schedule" not in new_config:
            new_config["schedule"] = DEFAULT_CONFIG["schedule"]
        else:
            new_config["schedule"]["active"] = bool(new_config["schedule"].get("active", False))
            new_config["schedule"]["start"] = str(new_config["schedule"].get("start", "09:00"))
            new_config["schedule"]["end"] = str(new_config["schedule"].get("end", "21:00"))

        raw_app_kws = new_config.get("approval_keywords", [])
        if isinstance(raw_app_kws, str):
            new_config["approval_keywords"] = [k.strip() for k in re.split(r'[,\n]', raw_app_kws) if k.strip()]
        
        for rule in new_config.get("rules", []):
            rule["enabled"] = bool(rule.get("enabled", True))
            
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
            rule["enable_approval"] = bool(rule.get("enable_approval", False))

            clean_kws = []
            raw_kws = rule.get("keywords", [])
            if isinstance(raw_kws, str): raw_kws = raw_kws.split('\n')
            for k in raw_kws:
                k = str(k).strip()
                if k: clean_kws.append(k)
            rule["keywords"] = clean_kws

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
            
            if "approval_action" not in rule: rule["approval_action"] = {}
            aa = rule["approval_action"]
            aa["reply_admin"] = str(aa.get("reply_admin", "")).strip()
            aa["reply_origin"] = str(aa.get("reply_origin", "")).strip()
            
            for i in range(1, 4):
                try: aa[f"delay_{i}_min"] = float(aa.get(f"delay_{i}_min", 1.0))
                except: aa[f"delay_{i}_min"] = 1.0
                try: aa[f"delay_{i}_max"] = float(aa.get(f"delay_{i}_max", 2.0))
                except: aa[f"delay_{i}_max"] = 2.0
            
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
            try: 
                redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
            except Exception as e:
                logger.error(f"❌ [Monitor] Redis 保存失败: {e}")
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        
        current_config = new_config
        logger.info(f"💾 [Monitor] 配置已更新并保存")
        return True, "保存成功"
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存逻辑错误: {e}")
        return False, str(e)

# --- Web UI (Bento Grid + Global CDN) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-[#F3F4F6]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Monitor Pro v52</title>
    <script src="https://unpkg.com/vue@3.3.4/dist/vue.global.prod.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&family=Plus+Jakarta+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Plus Jakarta Sans', sans-serif; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
        textarea, input, select { font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: -0.01em; }
        .bento-card { background: white; border: 1px solid #E5E7EB; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); transition: all 0.2s ease; }
        .bento-card:hover { border-color: #D1D5DB; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }
        .bento-input { background-color: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 6px; color: #374151; transition: all 0.15s; }
        .bento-input:focus { background-color: white; border-color: #6366F1; ring: 2px solid rgba(99, 102, 241, 0.1); outline: none; }
        .section-label { font-size: 10px; font-weight: 700; color: #6B7280; text-transform: uppercase; letter-spacing: 0.05em; }
        .recovery-panel { background: linear-gradient(135deg, #FFF1F2 0%, #FFF 100%); border: 1px solid #FECDD3; }
        .approval-bg { background-color: #EFF6FF; border-top: 1px solid #DBEAFE; }
    </style>
    <script>
        tailwind.config = { theme: { extend: { fontFamily: { sans: ['"Plus Jakarta Sans"', 'sans-serif'], mono: ['"JetBrains Mono"', 'monospace'], }, colors: { primary: '#6366F1', slate: { 50:'#f9fafb', 100:'#f3f4f6', 200:'#e5e7eb', 800:'#1f2937' } } } } }
    </script>
</head>
<body class="text-slate-800 antialiased min-h-screen pb-20 font-sans">
<div id="app">
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 h-12 flex items-center px-4 justify-between bg-opacity-90 backdrop-blur-sm">
        <div class="flex items-center gap-2">
            <div class="w-6 h-6 bg-primary text-white rounded flex items-center justify-center text-xs"><i class="fa-solid fa-bolt"></i></div>
            <span class="font-bold text-sm tracking-tight text-slate-900">Monitor <span class="text-xs text-primary font-medium bg-primary/10 px-1.5 py-0.5 rounded">Pro v52</span></span>
        </div>
        
        <div class="flex items-center gap-3 bg-slate-50 px-2 py-1 rounded border border-slate-200 mx-2 hidden md:flex">
            <label class="flex items-center gap-1.5 cursor-pointer select-none text-[10px] font-bold text-slate-500 uppercase">
                <input type="checkbox" v-model="config.schedule.active" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0">
                <span><i class="fa-regular fa-clock mr-1"></i>自动排班</span>
            </label>
            <div v-if="config.schedule.active" class="flex items-center gap-1 transition-all">
                <input type="time" v-model="config.schedule.start" class="bg-white border border-slate-300 rounded px-1 h-6 text-[10px] font-mono">
                <span class="text-[9px] text-slate-400">至</span>
                <input type="time" v-model="config.schedule.end" class="bg-white border border-slate-300 rounded px-1 h-6 text-[10px] font-mono">
            </div>
        </div>

        <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 cursor-pointer select-none bg-slate-50 px-2 py-1 rounded border border-slate-200 hover:border-slate-300 transition-colors" title="手动总开关">
                <div class="w-2 h-2 rounded-full" :class="config.enabled ? 'bg-green-500' : 'bg-red-500'"></div>
                <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden">
                <span class="text-[11px] font-semibold text-slate-600">{{ config.enabled ? 'Running' : 'Stopped' }}</span>
            </label>
            <button @click="saveConfig" class="bg-slate-900 hover:bg-black text-white px-3 py-1 rounded text-[11px] font-bold transition-colors flex items-center gap-1.5 shadow-sm"><i class="fa-solid fa-floppy-disk"></i> 保存</button>
        </div>
    </nav>

    <main class="max-w-[1400px] mx-auto px-4 py-6 space-y-6">
        
        <div class="md:hidden flex flex-col gap-2 bg-white p-3 rounded-lg border border-slate-200 shadow-sm">
            <div class="flex items-center justify-between">
                <span class="text-xs font-bold text-slate-700"><i class="fa-regular fa-clock mr-1"></i>自动排班</span>
                <input type="checkbox" v-model="config.schedule.active" class="w-4 h-4 text-primary border-slate-300 rounded focus:ring-0">
            </div>
            <div v-if="config.schedule.active" class="grid grid-cols-2 gap-2">
                <div class="flex items-center gap-2"><span class="text-[10px] text-slate-400">开启:</span><input type="time" v-model="config.schedule.start" class="bento-input w-full px-2 py-1 h-8 text-xs font-mono"></div>
                <div class="flex items-center gap-2"><span class="text-[10px] text-slate-400">关闭:</span><input type="time" v-model="config.schedule.end" class="bento-input w-full px-2 py-1 h-8 text-xs font-mono"></div>
            </div>
        </div>

        <div class="flex items-center gap-2 mb-2">
            <span class="text-[10px] font-bold text-slate-400 uppercase">全局审批触发词:</span>
            <input :value="(config.approval_keywords || []).join(', ')" @input="val => config.approval_keywords = val.target.value.split(/[,，]/).map(s=>s.trim()).filter(s=>s)" class="bento-input px-2 py-1 h-6 text-xs font-mono border-slate-300 w-64" placeholder="同意, 批准, ok">
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="(rule, index) in config.rules" :key="index" class="bento-card flex flex-col overflow-hidden relative group">
                <div class="px-3 py-2 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                    <div class="flex items-center gap-2 flex-1">
                        <span class="text-slate-400 text-[10px] font-mono">#{{index+1}}</span>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-700 focus:ring-0 placeholder-slate-300 w-full font-sans" placeholder="未命名规则">
                    </div>
                    <label class="relative inline-flex items-center cursor-pointer mr-2" :title="rule.enabled ? '规则已开启' : '规则已禁用'">
                        <input type="checkbox" v-model="rule.enabled" @change="saveConfig" class="sr-only peer">
                        <div class="w-7 h-4 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-green-500"></div>
                    </label>
                    <button @click="removeRule(index)" class="text-slate-300 hover:text-red-500 transition-colors px-1" title="删除"><i class="fa-solid fa-trash text-[10px]"></i></button>
                </div>
                <div class="p-3 flex flex-col gap-3" :class="{'opacity-50 grayscale pointer-events-none': !rule.enabled}">
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label"><i class="fa-solid fa-eye mr-1"></i>监听来源</span><label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="rule.check_file" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0"><span class="text-[10px] text-slate-500 font-medium" :class="{'text-primary': rule.check_file}">文件模式</span></label></div>
                        <div class="relative"><textarea :value="listToString(rule.groups)" @change="stringToIntList($event, rule, 'groups')" rows="1" class="bento-input w-full px-2 py-1.5 resize-none h-8 leading-tight font-mono text-[11px]" placeholder="群ID (换行分隔)"></textarea></div>
                        <div v-if="!rule.check_file" class="relative">
                            <textarea :value="listToString(rule.keywords)" @change="stringToList($event, rule, 'keywords')" rows="2" class="bento-input w-full px-2 py-1.5 resize-none h-16 leading-tight font-mono text-[11px] placeholder-slate-400" placeholder="普通: 代存&#10;正则: r:(代|带)存|入[金款]"></textarea>
                            <div class="absolute right-2 bottom-1 text-[9px] text-primary/60 bg-white/80 px-1 rounded pointer-events-none">支持正则 r:...</div>
                        </div>
                        <div v-else class="space-y-2">
                            <div class="grid grid-cols-2 gap-2"><input :value="listToString(rule.file_extensions).replace(/\\n/g, ', ')" @change="stringToList($event, rule, 'file_extensions')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="后缀: xlsx, png"><input :value="listToString(rule.filename_keywords).replace(/\\n/g, ', ')" @change="stringToList($event, rule, 'filename_keywords')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="文件名关键词"></div>
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="section-label"><i class="fa-solid fa-filter mr-1"></i>过滤与冷却</div>
                        <div class="grid grid-cols-5 gap-2">
                            <div class="col-span-2"><select v-model="rule.sender_mode" class="bento-input w-full px-1 py-0 h-7 text-[10px] font-sans font-medium"><option value="exclude">🚫 排除前缀</option><option value="include">✅ 只许前缀</option></select></div>
                            <div class="col-span-3"><input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @change="stringToList($event, rule, 'sender_prefixes')" class="bento-input w-full px-2 py-1.5 h-7 truncate font-mono text-[11px]" placeholder="前缀: YY, AA"></div>
                            <div class="col-span-5 relative flex items-center gap-2 mt-0.5"><span class="text-[10px] text-slate-400 font-medium">冷却CD:</span><input type="number" v-model.number="rule.cooldown" class="bento-input w-16 px-1 py-0 h-6 text-center text-[10px] font-mono font-bold"><span class="text-[10px] text-slate-400 font-medium">秒</span></div>
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label text-primary"><i class="fa-solid fa-bolt mr-1"></i>执行动作流</span><button @click="rule.replies.push({type:'text', text:'', forward_to:'', min:1, max:3})" class="text-[10px] text-primary hover:bg-primary/5 px-1.5 py-0.5 rounded transition-colors border border-transparent hover:border-primary/10 font-bold">+ 添加步骤</button></div>
                        <div v-if="rule.replies.length === 0" class="text-center py-2 text-[10px] text-slate-300 border border-dashed border-slate-200 rounded font-medium">无动作</div>
                        <div class="space-y-1.5">
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="flex gap-1.5 group/item">
                                <div class="flex flex-col justify-center items-center w-8 bg-slate-50 border border-slate-200 rounded h-auto font-mono"><input v-model.number="reply.min" class="w-full text-center bg-transparent text-[9px] text-slate-500 focus:outline-none h-3 p-0" placeholder="min"><div class="w-3 h-px bg-slate-200 my-0.5"></div><input v-model.number="reply.max" class="w-full text-center bg-transparent text-[9px] text-slate-500 focus:outline-none h-3 p-0" placeholder="max"></div>
                                <div class="flex-1 bg-slate-50 border border-slate-200 rounded p-1.5 hover:border-primary/30 hover:bg-white transition-all">
                                    <div class="flex items-center gap-1.5 mb-1">
                                        <select v-model="reply.type" class="text-[10px] bg-transparent border-none p-0 text-slate-600 font-bold focus:ring-0 cursor-pointer w-auto font-sans"><option value="text">💬 发送文本</option><option value="forward">🔀 直接转发</option><option value="copy_file">📂 转发+新文案</option><option value="amount_logic">💰 金额分流</option><option value="preempt_check">⚡ 抢答检测 (自删)</option></select>
                                        <button @click="rule.replies.splice(rIndex, 1)" class="ml-auto text-slate-300 hover:text-red-400"><i class="fa-solid fa-xmark text-[10px]"></i></button>
                                    </div>
                                    <template v-if="reply.type === 'text'"><textarea v-model="reply.text" rows="2" class="bento-input w-full px-1.5 py-1 text-[10px] resize-none border-transparent bg-white focus:border-slate-200 font-mono" placeholder="内容... ({data}插入提取结果)"></textarea></template>
                                    <template v-if="reply.type === 'forward'"><input v-model="reply.forward_to" class="bento-input w-full px-1.5 py-1 h-6 text-[10px] font-mono text-blue-600" placeholder="目标群ID"></template>
                                    <template v-if="reply.type === 'copy_file'"><input v-model="reply.forward_to" class="bento-input w-full px-1.5 py-1 h-6 text-[10px] font-mono text-blue-600 mb-1" placeholder="目标群ID"><textarea v-model="reply.text" rows="2" class="bento-input w-full px-1.5 py-1 text-[10px] resize-none bg-yellow-50 border-yellow-100 focus:border-yellow-300 font-mono" placeholder="新文案... ({time})"></textarea></template>
                                    <template v-if="reply.type === 'amount_logic'"><input v-model="reply.forward_to" class="bento-input w-full px-1.5 py-1 h-6 text-[10px] font-mono text-blue-600 mb-1" placeholder="小额转发目标群ID"><textarea v-model="reply.text" rows="2" class="bento-input w-full px-1.5 py-1 text-[10px] resize-none bg-indigo-50 border-indigo-100 focus:border-indigo-300 font-mono" placeholder="2000|大额语|小额1;;小额2"></textarea></template>
                                    <template v-if="reply.type === 'preempt_check'"><div class="px-1.5 py-1 bg-red-50 text-red-500 rounded text-[10px] font-medium border border-red-100 flex items-center gap-2"><i class="fa-solid fa-user-ninja"></i><span>检测到中间有人插话则删除自己</span></div></template>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <div class="approval-bg p-3 flex flex-col gap-2">
                    <div class="flex items-center justify-between">
                        <label class="flex items-center gap-1.5 cursor-pointer select-none text-[9px] font-bold text-blue-500 uppercase">
                            <input type="checkbox" v-model="rule.enable_approval" class="w-3 h-3 text-blue-500 rounded border-blue-200 focus:ring-0">
                            <i class="fa-solid fa-user-check"></i> 启用审批流 (Approval)
                        </label>
                    </div>
                    <div v-if="rule.enable_approval" class="flex flex-col gap-2 mt-1 transition-all">
                        <div class="flex items-center gap-2">
                            <div class="flex items-center w-14 bg-white border border-blue-200 rounded h-6 px-1 shrink-0" title="同意后延迟"><i class="fa-regular fa-clock text-[9px] text-blue-300 mr-0.5"></i><input v-model.number="rule.approval_action.delay_1_min" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="1"><span class="text-blue-200 text-[9px] mx-0.5">-</span><input v-model.number="rule.approval_action.delay_1_max" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="2"></div>
                            <input v-model="rule.approval_action.reply_admin" class="bento-input flex-1 px-2 py-1.5 h-6 text-[10px] border-blue-200 focus:border-blue-400" placeholder="步骤1: 回复领导 (请稍等ART)">
                        </div>
                        <div class="flex items-center gap-2">
                            <div class="flex items-center w-14 bg-white border border-blue-200 rounded h-6 px-1 shrink-0" title="回复后延迟"><i class="fa-regular fa-clock text-[9px] text-blue-300 mr-0.5"></i><input v-model.number="rule.approval_action.delay_2_min" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="1"><span class="text-blue-200 text-[9px] mx-0.5">-</span><input v-model.number="rule.approval_action.delay_2_max" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="3"></div>
                            <input v-model="rule.approval_action.forward_to" class="bento-input flex-1 px-2 py-1.5 h-6 text-[10px] border-blue-200 focus:border-blue-400 font-mono text-blue-600" placeholder="步骤2: 转发到群ID">
                        </div>
                        <div class="flex items-center gap-2">
                            <div class="flex items-center w-14 bg-white border border-blue-200 rounded h-6 px-1 shrink-0" title="转发后延迟"><i class="fa-regular fa-clock text-[9px] text-blue-300 mr-0.5"></i><input v-model.number="rule.approval_action.delay_3_min" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="1"><span class="text-blue-200 text-[9px] mx-0.5">-</span><input v-model.number="rule.approval_action.delay_3_max" class="w-3.5 text-center bg-transparent text-[9px] font-mono focus:outline-none p-0" placeholder="2"></div>
                            <input v-model="rule.approval_action.reply_origin" class="bento-input flex-1 px-2 py-1.5 h-6 text-[10px] border-blue-200 focus:border-blue-400" placeholder="步骤3: 回复原消息 (✅ 已处理)">
                        </div>
                    </div>
                </div>
            </div>
            <div @click="addRule" class="border border-dashed border-slate-300 rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer hover:border-primary hover:bg-slate-50 transition-all min-h-[200px] text-slate-400 hover:text-primary group"><div class="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center mb-2 group-hover:bg-primary/10 transition-colors"><i class="fa-solid fa-plus text-lg"></i></div><span class="text-xs font-bold">新建规则卡片</span></div>
        </div>

        <div class="bento-card recovery-panel p-4 flex flex-col md:flex-row gap-4 items-center justify-between shadow-sm hover:shadow-md transition-all">
            <div class="flex items-center gap-3 w-full md:w-auto"><div class="w-10 h-10 bg-red-100 text-red-500 rounded-lg flex items-center justify-center text-xl shrink-0"><i class="fa-solid fa-truck-medical"></i></div><div><h3 class="text-sm font-bold text-slate-800">突发事件批量回复 (Global Reply)</h3><p class="text-[10px] text-slate-500 mt-0.5">自动查找我的反馈消息，并回复给<strong class="text-red-500">原提问者</strong> (Original Sender)</p></div></div>
            <div class="flex flex-col md:flex-row gap-3 w-full md:w-auto flex-1 justify-end">
                <div class="flex flex-col gap-1 w-full md:w-48"><label class="text-[9px] font-bold text-slate-500 uppercase">查找我的反馈话术</label><input v-model="recovery.search" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-red-200 focus:border-red-400" placeholder="例如: 场馆技术核实中..."></div>
                <div class="flex flex-col gap-1 w-full md:w-48"><label class="text-[9px] font-bold text-slate-500 uppercase">回复给原提问者</label><input v-model="recovery.reply" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-green-200 focus:border-green-400" placeholder="例如: 已恢复，请刷新重试"></div>
                <div class="flex flex-col gap-1 w-full md:w-20"><label class="text-[9px] font-bold text-slate-500 uppercase">范围(小时)</label><input type="number" v-model.number="recovery.hours" class="bento-input px-2 py-1.5 h-8 text-xs text-center font-bold" placeholder="5"></div>
                <div class="flex flex-col gap-1 w-full md:w-24"><label class="text-[9px] font-bold text-slate-500 uppercase">间隔(秒)</label><div class="flex gap-1"><input type="number" v-model.number="recovery.min" class="bento-input px-1 py-1.5 h-8 text-xs text-center font-bold w-1/2" placeholder="2"><input type="number" v-model.number="recovery.max" class="bento-input px-1 py-1.5 h-8 text-xs text-center font-bold w-1/2" placeholder="5"></div></div>
                <div class="flex items-end"><button @click="runRecovery" :disabled="!recovery.search || !recovery.reply" class="h-8 bg-red-500 hover:bg-red-600 disabled:bg-slate-300 text-white px-4 rounded text-xs font-bold transition-colors flex items-center gap-2 shadow-sm whitespace-nowrap"><i class="fa-solid fa-paper-plane"></i> 执行回复</button></div>
            </div>
        </div>
    </main>

    <div class="fixed bottom-4 right-4 z-50 transition-all duration-300" :class="{'translate-y-20 opacity-0': !toast.show, 'translate-y-0 opacity-100': toast.show}">
        <div class="bg-slate-800 text-white px-3 py-2 rounded shadow-lg flex items-center gap-2 text-xs font-medium"><i v-if="toast.type==='success'" class="fa-solid fa-check text-green-400"></i><i v-else class="fa-solid fa-triangle-exclamation text-red-400"></i><span>{{ toast.msg }}</span></div>
    </div>
</div>

<script>
    const { createApp, reactive } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: false, approval_keywords: [], schedule: {active: false, start: '09:00', end: '21:00'}, rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });
            const recovery = reactive({ search: '', reply: '', hours: 5, min: 2, max: 5 });

            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { 
                    config.enabled = data.enabled; 
                    if(data.approval_keywords) config.approval_keywords = data.approval_keywords;
                    else config.approval_keywords = ['同意', '批准', 'ok'];
                    
                    if(data.schedule) config.schedule = data.schedule;
                    else config.schedule = {active: false, start: '09:00', end: '21:00'};

                    config.rules = (data.rules || []).map(r => {
                        if(r.replies) r.replies = r.replies.map(rep => ({...rep, type: rep.type || 'text'}));
                        if(r.check_file === undefined) r.check_file = false;
                        if(r.enable_approval === undefined) r.enable_approval = false;
                        if(r.enabled === undefined) r.enabled = true;
                        if(!r.file_extensions) r.file_extensions = [];
                        if(!r.filename_keywords) r.filename_keywords = [];
                        if(!r.sender_prefixes) r.sender_prefixes = [];
                        if(!r.keywords) r.keywords = [];
                        if(!r.approval_action) r.approval_action = {reply_admin:'', reply_origin:'', forward_to:'', delay_1_min:1, delay_1_max:2, delay_2_min:1, delay_2_max:3, delay_3_min:1, delay_3_max:2};
                        return r;
                    });
                });

            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { 
                let val = e.target.value;
                // [Fix] Support Chinese comma
                val = val.replace(/，/g, ',');
                
                if (val.includes(',')) {
                    rule[key] = val.split(',').map(x=>x.trim()).filter(x=>x);
                } else {
                    // Split by newline
                    rule[key] = val.split(/[\\r\\n]+/).map(x=>x.trim()).filter(x=>x);
                }
            };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    name: '新规则 #' + (config.rules.length + 1),
                    enabled: true,
                    groups: [], check_file: false, keywords: [], file_extensions: [], filename_keywords: [],
                    enable_approval: false,
                    approval_action: {reply_admin:'', reply_origin:'', forward_to:'', delay_1_min:1, delay_1_max:2, delay_2_min:1, delay_2_max:3, delay_3_min:1, delay_3_max:2},
                    sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [{type:'text', text: '', min: 1, max: 2}]
                });
            };
            
            const removeRule = (index) => { if(confirm('确定删除此规则？')) config.rules.splice(index, 1); };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) showToast('配置已保存', 'success');
                    else showToast('保存失败: ' + json.msg, 'error');
                } catch(e) { showToast('网络错误', 'error'); }
            };
            
            const runRecovery = async () => {
                const min = recovery.min || 1;
                const max = recovery.max || 3;
                if(!confirm(`⚠️ 确定要执行批量回复吗？\\n\\n范围: 过去 ${recovery.hours} 小时\\n目标: 我发送的 "${recovery.search}" \\n动作: 追溯回复给【原消息发送者】\\n间隔: ${min}-${max} 秒`)) return;
                try {
                    const res = await fetch('/api/batch_recovery', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(recovery) });
                    const json = await res.json();
                    if (json.success) showToast(json.msg, 'success');
                    else showToast('执行失败: ' + json.msg, 'error');
                } catch(e) { showToast('网络请求错误', 'error'); }
            };

            const showToast = (msg, type) => { toast.msg = msg; toast.type = type; toast.show = true; setTimeout(() => toast.show = false, 3000); };

            return { config, toast, recovery, listToString, stringToList, stringToIntList, addRule, removeRule, saveConfig, runRecovery };
        }
    }).mount('#app');
</script>
</body>
</html>
"""

OTP_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>验证码监控</title>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        :root { --bg-color: #f3f4f6; --text-color: #1f2937; --card-bg: #ffffff; }
        body { font-family: -apple-system, system-ui, "Microsoft YaHei", sans-serif; background-color: var(--bg-color); color: var(--text-color); margin: 0; padding: 20px; display: flex; flex-direction: column; align-items: center; min-height: 100vh; }
        
        .header { text-align: center; margin-bottom: 30px; }
        .header h1 { font-size: 24px; font-weight: 800; margin: 0; color: #374151; letter-spacing: -0.5px; }
        .header span { font-size: 13px; color: #9ca3af; font-weight: 500; background: #e5e7eb; padding: 2px 8px; border-radius: 99px; margin-left: 8px; vertical-align: middle; }

        .grid-container { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 20px; width: 100%; max-width: 1200px; margin-bottom: 40px; }
        
        .card { background: var(--card-bg); border-radius: 16px; padding: 20px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -1px rgba(0, 0, 0, 0.03); border: 1px solid #f3f4f6; transition: transform 0.2s; position: relative; overflow: hidden; }
        .card:hover { transform: translateY(-2px); box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.05); }
        
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
        .platform-icon { font-size: 20px; margin-right: 8px; }
        .account-name { font-weight: 700; font-size: 15px; color: #111827; }
        .status-badge { font-size: 11px; padding: 2px 8px; border-radius: 6px; font-weight: 600; text-transform: uppercase; }
        
        /* Telegram Style */
        .tg-style .platform-icon { color: #24A1DE; }
        .tg-style .status-badge { background: #e0f2fe; color: #0284c7; }
        .tg-style .code-box { background: #f0f9ff; color: #0369a1; border: 1px dashed #bae6fd; }
        
        /* Google Style */
        .ga-style .platform-icon { color: #EA4335; }
        .ga-style .status-badge { background: #fff1f2; color: #e11d48; }
        .ga-style .code-box { background: #fff5f5; color: #be123c; border: 1px dashed #fecdd3; }

        .code-box { font-family: 'SF Mono', 'Menlo', monospace; font-size: 32px; font-weight: 700; letter-spacing: 4px; text-align: center; padding: 16px; border-radius: 12px; margin: 12px 0; cursor: pointer; user-select: all; transition: all 0.2s; }
        .code-box:active { transform: scale(0.98); background-color: #e5e7eb; }
        
        .meta-info { font-size: 12px; color: #6b7280; display: flex; justify-content: space-between; align-items: center; margin-top: 8px; font-weight: 500; }
        
        .progress-track { height: 6px; background: #f3f4f6; border-radius: 3px; overflow: hidden; margin-top: 15px; }
        .progress-fill { height: 100%; border-radius: 3px; transition: width 0.1s linear; }
        .ga-style .progress-fill { background: linear-gradient(90deg, #f43f5e, #e11d48); }

        .empty-state { text-align: center; padding: 40px; color: #9ca3af; font-size: 14px; background: white; border-radius: 16px; border: 2px dashed #e5e7eb; width: 100%; max-width: 600px; }
        
        .section-label { font-size: 12px; font-weight: 700; color: #9ca3af; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 12px; width: 100%; max-width: 1200px; }
        
        .toast { position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%); background: #1f2937; color: white; padding: 8px 16px; border-radius: 20px; font-size: 12px; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
        .toast.show { opacity: 1; }
    </style>
</head>
<body>
    <div class="header">
        <h1>验证码监控 <span>{{ tz_name }}</span></h1>
    </div>

    {% if otp_list %}
    <div class="section-label">Telegram 登录验证码</div>
    <div class="grid-container">
        {% for name, data in otp_list.items() %}
        <div class="card tg-style">
            <div class="card-header">
                <div style="display:flex; align-items:center;">
                    <i class="fa-brands fa-telegram platform-icon"></i>
                    <span class="account-name">{{ name }}</span>
                </div>
                <span class="status-badge">已连接</span>
            </div>
            {% if data.code %}
                <div class="code-box" onclick="copyToClip('{{ data.code }}')">{{ data.code }}</div>
                <div class="meta-info">
                    <span><i class="fa-regular fa-clock"></i> {{ data.time.split(' ')[1] }} 接收</span>
                    <span style="color:#0ea5e9; font-size:10px;">点击复制</span>
                </div>
            {% else %}
                <div style="padding: 24px 0; text-align: center; color: #9ca3af; font-size: 13px; font-style: italic;">
                    等待验证码...
                </div>
            {% endif %}
            <div class="meta-info" style="margin-top:10px; border-top:1px solid #f3f4f6; padding-top:8px;">
                <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 100%;">{{ data.text[:30] }}...</span>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if google_list %}
    <div class="section-label">谷歌验证码 (2FA)</div>
    <div class="grid-container">
        {% for item in google_list %}
        <div class="card ga-style google-item" data-ttl="{{ item.ttl }}">
            <div class="card-header">
                <div style="display:flex; align-items:center;">
                    <i class="fa-brands fa-google platform-icon"></i>
                    <span class="account-name">{{ item.name }}</span>
                </div>
                <span class="status-badge ttl-text">{{ item.ttl }}s</span>
            </div>
            <div class="code-box" onclick="copyToClip('{{ item.code }}')">{{ item.code }}</div>
            <div class="progress-track">
                <div class="progress-fill" style="width: {{ (item.ttl / 30) * 100 }}%"></div>
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if not otp_list and not google_list %}
    <div class="empty-state">
        <i class="fa-solid fa-ghost" style="font-size: 32px; margin-bottom: 10px;"></i><br>
        暂无已配置的账号
    </div>
    {% endif %}

    <div id="toast" class="toast">已复制到剪贴板</div>

    <script>
    function copyToClip(text) {
        if(!text) return;
        const input = document.createElement('input');
        input.setAttribute('value', text);
        document.body.appendChild(input);
        input.select();
        document.execCommand('copy');
        document.body.removeChild(input);
        
        const toast = document.getElementById('toast');
        toast.textContent = text + ' 已复制';
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 2000);
    }

    document.addEventListener("DOMContentLoaded", function() {
        const items = document.querySelectorAll('.google-item');
        
        setInterval(() => {
            let needsReload = false;
            
            items.forEach(item => {
                let ttl = parseFloat(item.getAttribute('data-ttl'));
                // 每次减少 0.1 秒
                ttl -= 0.1;
                
                if (ttl <= 0) {
                    needsReload = true;
                } else {
                    // 更新属性
                    item.setAttribute('data-ttl', ttl.toFixed(1));
                    
                    // 更新右上角文字
                    const badge = item.querySelector('.ttl-text');
                    if(badge) badge.innerText = Math.ceil(ttl) + 's';
                    
                    // 更新进度条
                    const fill = item.querySelector('.progress-fill');
                    if(fill) {
                        const pct = (ttl / 30) * 100;
                        fill.style.width = pct + '%';
                        
                        // 颜色变化提醒
                        if(ttl < 5) fill.style.background = '#ef4444'; // Red
                        else fill.style.background = 'linear-gradient(90deg, #f43f5e, #e11d48)';
                    }
                }
            });

            // 关键修复：如果任何一个验证码过期，等待 1.5 秒后刷新页面
            // 这样可以防止在 0s 时疯狂刷新
            if (needsReload) {
                console.log("Token expired, refreshing in 1.5s...");
                setTimeout(() => location.reload(), 1500);
            }
        }, 100); 
    });
    </script>
</body>
</html>
"""

def match_text(text, rule):
    """通用文本匹配逻辑 (支持 & # 和 r:正则)"""
    keywords = rule.get("keywords", [])
    if not keywords: return True 
    
    for kw_rule in keywords:
        if not kw_rule: continue
        kw_rule_lower = kw_rule.lower()
        text_lower = text.lower()
        
        # 0. Regex Mode
        if kw_rule_lower.startswith('r:'):
            try:
                pattern = kw_rule[2:] # Remove 'r:'
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except: pass
            continue

        # 1. Normal Mode (Inclusion # Exclusion)
        parts = kw_rule_lower.split('#')
        include_part = parts[0]
        exclude_parts = parts[1:] if len(parts) > 1 else []
        
        hit_exclusion = False
        for ex in exclude_parts:
            if ex.strip() and (ex.strip() in text_lower):
                hit_exclusion = True
                break
        if hit_exclusion: continue
        
        and_kws = include_part.split('&')
        all_matched = True
        for ak in and_kws:
            ak = ak.strip()
            if ak and (ak not in text_lower):
                all_matched = False
                break
        
        if all_matched and and_kws:
            return True
    return False

def get_sender_name(sender):
    """统一提取发送者名称 (User/Channel/Chat)"""
    if not sender: return "Unknown"
    # 1. 尝试 Channel/Group 名 (用于匿名管理员或频道身份)
    title = getattr(sender, 'title', '')
    if title: return title
    # 2. 尝试 User 名
    fname = getattr(sender, 'first_name', '')
    lname = getattr(sender, 'last_name', '')
    if fname or lname:
        return f"{fname} {lname}".strip()
    return "Unknown"

def check_sender_allowed(sender_name, rule):
    """检查发送者是否被允许 (支持包含匹配)"""
    if not sender_name: return True
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    
    match_found = False
    for p in prefixes:
        # [修改] 改为包含匹配 (contains)，不仅仅是前缀 (startswith)
        if p and (p in sender_name):
            match_found = True
            break
            
    if sender_mode == "exclude" and match_found: return False
    elif sender_mode == "include" and not match_found: return False
    return True

def format_caption(tpl):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    res = tpl.replace('{time}', now_str)
    return res

async def analyze_message(client, rule, event, other_cs_ids, sender_name):
    # [新增] 检查规则开关状态
    if not rule.get("enabled", True): 
        return False, "规则已关闭", None

    if event.chat_id not in rule.get("groups", []): return False, "群组不符", None
    if event.is_reply: return False, "是回复消息", None
    if event.out: return False, "Bot自己发送", None
    if event.sender_id in other_cs_ids: return False, "ID是客服", None
    
    if not check_sender_allowed(sender_name, rule):
        return False, "发送者被排除", None

    check_file = rule.get("check_file", False)
    text = (event.text or "")
    
    if check_file:
        if not event.message.file: return False, "非文件消息", None
        file_exts = rule.get("file_extensions", [])
        ext = (event.message.file.ext or "").lower().replace('.', '')
        if file_exts:
            if ext not in file_exts: return False, "后缀不符", None
        fn_kws = rule.get("filename_keywords", [])
        filename = ""
        if event.message.file.name: filename = event.message.file.name
        else:
            for attr in event.message.file.attributes:
                if hasattr(attr, 'file_name'):
                    filename = attr.file_name
                    break
        filename_lower = (filename or "").lower()
        if fn_kws:
            if not any(k.lower() in filename_lower for k in fn_kws): return False, "文件名关键词不符", None
    else:
        # [Fix] Call match_text here
        if not match_text(text, rule): return False, "文本关键词不符", None
    
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中", None
    
    return True, "✅ 匹配成功", None

# [新增] 自动排班任务
async def run_schedule_job():
    while True:
        try:
            await asyncio.sleep(60)
            
            schedule = current_config.get("schedule", {})
            if not schedule.get("active", False):
                continue
                
            start_str = schedule.get("start", "09:00")
            end_str = schedule.get("end", "21:00")
            
            now = datetime.now(BJ_TZ)
            current_time = now.strftime("%H:%M")
            
            is_working_hours = False
            if start_str < end_str:
                if start_str <= current_time < end_str:
                    is_working_hours = True
            else:
                if current_time >= start_str or current_time < end_str:
                    is_working_hours = True
            
            if is_working_hours and not current_config["enabled"]:
                current_config["enabled"] = True
                save_config(current_config) 
                logger.info(f"⏰ [Schedule] 上班时间到了 ({start_str})，自动开启监听")
                
            elif not is_working_hours and current_config["enabled"]:
                current_config["enabled"] = False
                save_config(current_config) 
                logger.info(f"💤 [Schedule] 下班时间到了 ({end_str})，自动关闭监听")
                
        except Exception as e:
            logger.error(f"❌ [Schedule] Error: {e}")

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global_main_handler = main_handler
    init_redis_connection()
    load_config(main_cs_prefixes)
    
    try: bot_loop = client.loop
    except:
        try: bot_loop = asyncio.get_event_loop()
        except: bot_loop = asyncio.new_event_loop(); asyncio.set_event_loop(bot_loop)

    # 启动排班任务 (添加保护)
    if bot_loop:
        bot_loop.create_task(run_schedule_job())

    @app.route('/zd')
    def monitor_settings_page(): 
        return Response(SETTINGS_HTML, mimetype='text/html; charset=utf-8')
    
    # [NEW] OTP 页面 - 渲染列表 (包含 Telegram 和 Google Auth)
    @app.route('/otp')
    def view_otp_page():
        # 1. 准备 Telegram OTP (从全局变量读取)
        tg_data = latest_otp_storage
        
        # 2. 准备 Google Auth OTP (实时计算)
        ga_data = []
        if pyotp:
            # 从环境变量读取密钥: "Name1:Secret1;Name2:Secret2"
            raw_secrets = os.environ.get("GA_SECRETS", "")
            if raw_secrets:
                pairs = raw_secrets.split(';')
                for p in pairs:
                    if ':' in p:
                        name, secret = p.split(':', 1)
                        name = name.strip()
                        secret = secret.strip()
                        if not secret: continue
                        try:
                            totp = pyotp.TOTP(secret)
                            code = totp.now()
                            # 计算剩余时间 (30秒周期)
                            time_remaining = totp.interval - datetime.now().timestamp() % totp.interval
                            ga_data.append({
                                "name": name,
                                "code": code,
                                "ttl": int(time_remaining)
                            })
                        except Exception as e:
                            logger.error(f"❌ [GoogleAuth] 计算失败 ({name}): {e}")

        return render_template_string(OTP_HTML, otp_list=tg_data, google_list=ga_data, tz_name=TZ_NAME)
        
    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json(): return jsonify(current_config)
    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.json)
        return jsonify({"success": success, "msg": msg if not success else ""})

    @app.route('/api/batch_recovery', methods=['POST'])
    def trigger_batch_recovery():
        data = request.json
        asyncio.run_coroutine_threadsafe(
            run_batch_recovery_task(client, data.get('search'), data.get('reply'), float(data.get('hours', 5)), float(data.get('min', 2.0)), float(data.get('max', 5.0))),
            bot_loop
        )
        return jsonify({"success": True, "msg": "任务已启动"})

    async def run_batch_recovery_task(cli, search, reply, hours, min_d, max_d):
        limit_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        async for msg in cli.iter_messages(None, search=search):
            if msg.date < limit_time: break
            if not msg.is_group or not msg.out: continue
            try:
                target_id = msg.reply_to_msg_id if (msg.is_reply and msg.reply_to_msg_id) else msg.id
                await cli.send_message(msg.chat_id, format_caption(reply), reply_to=target_id)
                await asyncio.sleep(random.uniform(min_d, max_d))
            except: pass

    # --- OTP 处理器工厂函数 ---
    def create_otp_handler(account_name):
        async def otp_handler(event):
            try:
                text = event.message.text or ""
                # 尝试提取 5位数字 验证码
                code = ""
                match = re.search(r'[\s:](\d{5})[\s.]', text)
                if match:
                    code = match.group(1)
                else:
                    match = re.search(r'\b\d{5}\b', text)
                    if match: code = match.group(0)

                # 存入全局字典，Key 为账号名称
                latest_otp_storage[account_name] = {
                    "code": code,
                    "text": text,
                    "time": datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')
                }
                logger.info(f"🔐 [OTP] {account_name} 收到官方消息, Code: {code}")
            except Exception as e:
                logger.error(f"❌ [OTP] Error ({account_name}): {e}")
        return otp_handler

    # 1. 为【主账号】(SESSION_STRING) 添加监听器
    main_name = os.environ.get("MAIN_SESSION_NAME", "主账号")
    client.add_event_handler(create_otp_handler(main_name), events.NewMessage(chats=777000))

    # 2. 为【其他账号】启动新的客户端并监听 (FIXED: 使用 wrapper 解决 create_task 报错)
    extra_sessions_env = os.environ.get("EXTRA_SESSION_STRINGS", "")
    api_id = int(os.environ.get("API_ID", 0))
    api_hash = os.environ.get("API_HASH", "")

    # 定义一个异步包装函数，确保 create_task 接收到的是 coroutine
    async def _start_extra_client(cli, name):
        try:
            # await cli.start() 在这里可以正常工作，因为它是在 loop 运行时被调用的
            await cli.start()
            logger.info(f"✅ [OTP] {name} 启动成功")
            # 保持连接活跃
            await cli.run_until_disconnected()
        except Exception as e:
            logger.error(f"❌ [OTP] {name} 启动/运行失败: {e}")

    if extra_sessions_env and api_id and api_hash:
        # Split by ; first
        raw_items = [x.strip() for x in extra_sessions_env.split(';') if x.strip()]
        for i, item in enumerate(raw_items):
            try:
                # Support "Name=Session" format
                if '=' in item:
                    c_name, c_sess = item.split('=', 1)
                    acc_name = c_name.strip()
                    sess_str = c_sess.strip()
                else:
                    acc_name = f"副账号 {i+1}"
                    sess_str = item
                
                logger.info(f"🔄 [OTP] 正在准备 {acc_name}...")
                
                # 初始化客户端（注意：此时并未连接）
                extra_client = TelegramClient(StringSession(sess_str), api_id, api_hash, loop=bot_loop)
                extra_client.add_event_handler(create_otp_handler(acc_name), events.NewMessage(chats=777000))
                
                # 将启动任务提交给事件循环 (使用包装函数)
                bot_loop.create_task(_start_extra_client(extra_client, acc_name))
                
            except Exception as e:
                logger.error(f"❌ [OTP] 初始化 {acc_name} 失败: {e}")

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug": await event.reply("Monitor Debug: Alive v52 (Fixed Match Text)"); return
        if not current_config.get("enabled", True): return
        
        if event.is_reply:
            app_kws = current_config.get("approval_keywords", ["同意", "批准", "ok"])
            if any(k in event.text for k in app_kws):
                try:
                    approver = await event.get_sender()
                    approver_name = get_sender_name(approver)
                    
                    original_msg = await event.get_reply_message()
                    if original_msg:
                        orig_sender = await original_msg.get_sender()
                        orig_sender_name = get_sender_name(orig_sender)

                        for rule in current_config.get("rules", []):
                            if not rule.get("enabled", True): continue

                            if not check_sender_allowed(approver_name, rule):
                                continue

                            is_match, _, _ = await analyze_message(client, rule, events.NewMessage.Event(original_msg), other_cs_ids, orig_sender_name)
                            
                            if is_match and rule.get("enable_approval", False):
                                logger.info(f"👮 [Approval] 批准通过! 匹配规则: {rule.get('name')} | 批准人: {approver_name}")
                                action = rule.get("approval_action", {})
                                
                                d1_min = float(action.get("delay_1_min", 1.0))
                                d1_max = float(action.get("delay_1_max", 2.0))
                                await asyncio.sleep(random.uniform(d1_min, d1_max))
                                if action.get("reply_admin"):
                                    await event.reply(format_caption(action["reply_admin"]))
                                
                                d2_min = float(action.get("delay_2_min", 1.0))
                                d2_max = float(action.get("delay_2_max", 3.0))
                                await asyncio.sleep(random.uniform(d2_min, d2_max))
                                fwd_tgt = action.get("forward_to")
                                if fwd_tgt:
                                    try:
                                        await client.forward_messages(int(str(fwd_tgt).strip()), original_msg)
                                    except Exception as e:
                                        logger.error(f"❌ [Approval] 转发失败: {e}")

                                d3_min = float(action.get("delay_3_min", 1.0))
                                d3_max = float(action.get("delay_3_max", 2.0))
                                await asyncio.sleep(random.uniform(d3_min, d3_max))
                                if action.get("reply_origin"):
                                    await original_msg.reply(format_caption(action["reply_origin"]))
                                
                                return
                except Exception as e:
                    logger.error(f"❌ [Approval] 处理出错: {e}")

        # [修正] 增强的名字获取逻辑 (v49/v50通用)
        sender_name = ""
        try:
            event.sender = await event.get_sender()
            sender_name = get_sender_name(event.sender)
            
            # 日志记录，方便调试
            logger.info(f"🔍 [Check] Sender: {sender_name} | ID: {event.sender_id}")
        except: 
            pass

        for rule in current_config.get("rules", []):
            try:
                if not rule.get("enabled", True): continue

                is_match, reason, extracted_data = await analyze_message(client, rule, event, other_cs_ids, sender_name)
                if is_match:
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发!")
                    rule_timers[rule.get("id", str(rule.get("groups")))] = time.time()
                    sent_msgs = []
                    for step in rule.get("replies", []):
                        await asyncio.sleep(random.uniform(step.get("min", 1), step.get("max", 3)))
                        stype = step.get("type", "text")
                        
                        if stype == "forward":
                            tgt = step.get("forward_to")
                            if tgt: sent_msgs.append(await client.forward_messages(int(str(tgt).strip()), event.message))
                        
                        elif stype == "copy_file":
                            tgt = step.get("forward_to")
                            if tgt and event.message.file:
                                sent_msgs.append(await client.send_file(int(str(tgt).strip()), event.message.file.media, caption=format_caption(step.get("text", ""))))
                        
                        elif stype == "amount_logic":
                            cfg = step.get("text", "")
                            tgt = step.get("forward_to")
                            parts = cfg.split('|')
                            if len(parts) >= 3:
                                thresh = float(parts[0])
                                amt_match = re.search(r"(?:金额|额度|存)[:：]?\s*(\d+(?:\.\d+)?)", event.text) 
                                if amt_match:
                                    amt = float(amt_match.group(1))
                                    if amt >= thresh:
                                        sent_msgs.append(await event.reply(format_caption(parts[1])))
                                    else:
                                        for sub_msg in parts[2].split(';;'):
                                            if sub_msg.strip():
                                                sent_msgs.append(await event.reply(format_caption(sub_msg)))
                                                await asyncio.sleep(1)
                                        if tgt: 
                                            fwd_msg = await client.forward_messages(int(str(tgt).strip()), event.message)
                                            sent_msgs.append(fwd_msg)
                                else:
                                    logger.warning(f"⚠️ [Monitor] Amount logic matched text but no specific amount found.")

                        elif stype == "preempt_check":
                            if not sent_msgs: continue
                            me = await client.get_me()
                            hist = await client.get_messages(event.chat_id, limit=10, min_id=event.id)
                            if any(m.sender_id != me.id and m.sender_id != event.sender_id for m in hist):
                                await client.delete_messages(event.chat_id, sent_msgs)
                                sent_msgs = []
                                break

                        else: # text
                            content = step.get("text", "")
                            if content: 
                                sent = await event.reply(format_caption(content))
                                sent_msgs.append(sent)
                                if global_main_handler: asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
                    break
            except Exception as e: logger.error(f"❌ [Monitor] Rule Error: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v52 (Fixed Match Text) 已启动")
