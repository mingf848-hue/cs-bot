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

# 尝试导入 redis
try: 
    import redis
except ImportError: 
    redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"
global_main_handler = None

# 标记配置来源
CONFIG_SOURCE = 'DEFAULT' 

# 北京时区
BJ_TZ = timezone(timedelta(hours=8))

# --- 没有任何预设规则的空壳配置 ---
# 既然您数据库里有数据，这个仅仅作为极其罕见的兜底，防止程序报错
DEFAULT_CONFIG = {
    "enabled": False, 
    "approval_keywords": ["同意", "批准", "ok"],
    "schedule": {
        "active": False,
        "start": "09:00",
        "end": "21:00"
    },
    "rules": []  # <--- 这里彻底空了，不会再有任何干扰
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
redis_client = None

def init_redis_connection():
    global redis_client
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_PUBLIC_URL")
    if redis and redis_url:
        for i in range(3):
            try:
                redis_client = redis.from_url(redis_url, decode_responses=True)
                redis_client.ping()
                logger.info("✅ [Monitor] Redis 数据库连接成功")
                return
            except Exception as e:
                logger.error(f"❌ [Monitor] Redis 连接失败 (第{i+1}次): {e}")
                time.sleep(2)
        redis_client = None

def load_config(system_cs_prefixes):
    global current_config, CONFIG_SOURCE
    loaded = False
    
    # 1. 极力尝试从 Redis 读取
    if redis_client:
        try:
            # 获取原始字符串
            raw_data = redis_client.get(REDIS_KEY)
            
            if raw_data:
                logger.info(f"🔍 [Monitor] 从 Redis 读到了数据 (长度: {len(raw_data)})")
                try:
                    saved = json.loads(raw_data)
                    # 只要是字典，我们就信任它，不搞严格检查
                    if isinstance(saved, dict):
                        current_config = saved
                        loaded = True
                        CONFIG_SOURCE = 'REDIS'
                        logger.info("📥 [Monitor] 成功加载 Redis 配置")
                    else:
                        logger.error("❌ [Monitor] Redis 数据格式不对 (不是字典)")
                except json.JSONDecodeError as je:
                    logger.error(f"❌ [Monitor] Redis 数据 JSON 解析失败: {je}")
            else:
                logger.warning("⚠️ [Monitor] Redis 连接成功，但该 Key 没有数据 (None)")
                
        except Exception as e:
            logger.error(f"⚠️ [Monitor] Redis 读取过程发生未知错误: {e}")

    # 2. 如果 Redis 真的没读到，尝试本地文件
    if not loaded and os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    current_config = saved
                    loaded = True
                    CONFIG_SOURCE = 'FILE'
                    logger.info("📂 [Monitor] 已从本地文件加载配置")
        except Exception as e:
            logger.error(f"⚠️ [Monitor] 本地文件读取出错: {e}")

    # 3. 如果依然没有加载成功，保持空壳
    if not loaded: 
        current_config = DEFAULT_CONFIG.copy()
        CONFIG_SOURCE = 'DEFAULT'
        logger.warning("⚠️ [Monitor] 未能加载任何配置，系统处于空壳状态")
    
    # 数据补全 (只补全最基础的字段，不覆盖 rules)
    if "approval_keywords" not in current_config:
        current_config["approval_keywords"] = ["同意", "批准", "ok"]
    if "schedule" not in current_config:
        current_config["schedule"] = DEFAULT_CONFIG["schedule"]
    if "rules" not in current_config:
        current_config["rules"] = []

    # 简单的格式修正，防止旧数据缺少字段报错
    for rule in current_config.get("rules", []):
        if "check_file" not in rule: rule["check_file"] = False
        if "enable_approval" not in rule: rule["enable_approval"] = False
        if "approval_action" not in rule: rule["approval_action"] = {}
        
        # 补全延迟参数
        aa = rule["approval_action"]
        for k in ["reply_admin", "reply_origin", "forward_to"]:
            if k not in aa: aa[k] = ""
        for i in range(1, 4):
            if f"delay_{i}_min" not in aa: aa[f"delay_{i}_min"] = 1.0
            if f"delay_{i}_max" not in aa: aa[f"delay_{i}_max"] = 2.0

        if rule.get("sender_mode") == "exclude" and not rule.get("sender_prefixes"):
            rule["sender_prefixes"] = list(system_cs_prefixes)

def save_config(new_config, is_auto_save=False):
    global current_config, CONFIG_SOURCE
    try:
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式"

        # [铁律] 如果是自动保存(定时任务)，且当前是空壳模式，绝对禁止写入！
        if is_auto_save and CONFIG_SOURCE == 'DEFAULT':
            logger.warning("🛡️ [Monitor] 空壳模式下禁止自动保存，防止覆盖您的数据库！")
            current_config = new_config
            return True, "内存已更新(未写入DB)"

        # ... (常规数据清洗) ...
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
            # 基础清洗
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

            # 列表清洗
            for list_key in ["keywords", "file_extensions", "filename_keywords", "sender_prefixes"]:
                clean_list = []
                raw_list = rule.get(list_key, [])
                if isinstance(raw_list, str):
                    if ',' in raw_list: raw_list = raw_list.split(',')
                    else: raw_list = raw_list.split('\n')
                for item in raw_list:
                    item_str = str(item).strip()
                    if item_str: clean_list.append(item_str)
                rule[list_key] = clean_list
            
            # 动作参数清洗
            if "approval_action" not in rule: rule["approval_action"] = {}
            aa = rule["approval_action"]
            for k in ["reply_admin", "reply_origin", "forward_to"]:
                aa[k] = str(aa.get(k, "")).strip()
            
            for i in range(1, 4):
                try: aa[f"delay_{i}_min"] = float(aa.get(f"delay_{i}_min", 1.0))
                except: aa[f"delay_{i}_min"] = 1.0
                try: aa[f"delay_{i}_max"] = float(aa.get(f"delay_{i}_max", 2.0))
                except: aa[f"delay_{i}_max"] = 2.0
            
            try: rule["cooldown"] = int(rule.get("cooldown", 60))
            except: rule["cooldown"] = 60
            
            for r in rule.get("replies", []):
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
                if "type" not in r: r["type"] = "text"
        
        # 写入 Redis
        if redis_client:
            try: 
                # ensure_ascii=False 确保中文正常保存
                redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
                CONFIG_SOURCE = 'REDIS' 
                logger.info("💾 [Monitor] 数据成功写入 Redis")
            except Exception as e:
                logger.error(f"❌ [Monitor] Redis 保存失败: {e}")
        
        # 写入本地文件
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        
        current_config = new_config
        return True, "保存成功"
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存逻辑错误: {e}")
        return False, str(e)

# --- Web UI (稳定 CDN) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-[#F3F4F6]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Monitor Pro v39</title>
    <script src="https://lib.baomitu.com/vue/3.3.4/vue.global.prod.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://lib.baomitu.com/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
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
<div id="loading-mask" style="position:fixed;top:0;left:0;width:100%;height:100%;background:#F3F4F6;z-index:9999;display:flex;justify-content:center;align-items:center;flex-direction:column;">
    <div style="font-size:18px;font-weight:bold;margin-bottom:10px;color:#334155;"><i class="fa-solid fa-circle-notch fa-spin"></i> 正在加载资源...</div>
    <div style="font-size:12px;color:#64748B;">国内高速 CDN 加速中</div>
</div>

<div id="app" v-cloak>
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 h-12 flex items-center px-4 justify-between bg-opacity-90 backdrop-blur-sm">
        <div class="flex items-center gap-2">
            <div class="w-6 h-6 bg-primary text-white rounded flex items-center justify-center text-xs"><i class="fa-solid fa-bolt"></i></div>
            <span class="font-bold text-sm tracking-tight text-slate-900">Monitor <span class="text-xs text-primary font-medium bg-primary/10 px-1.5 py-0.5 rounded">Pro v39</span></span>
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

        <div v-if="config.rules.length === 0" class="text-center py-10">
            <div class="inline-flex flex-col items-center justify-center p-6 bg-white rounded-lg border border-dashed border-slate-300 text-slate-400">
                <i class="fa-solid fa-inbox text-4xl mb-2"></i>
                <span class="text-sm font-medium">还没有规则，点击下方添加</span>
            </div>
        </div>

        <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            <div v-for="(rule, index) in config.rules" :key="index" class="bento-card flex flex-col overflow-hidden relative group">
                <div class="px-3 py-2 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                    <div class="flex items-center gap-2 flex-1">
                        <span class="text-slate-400 text-[10px] font-mono">#{{index+1}}</span>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-700 focus:ring-0 placeholder-slate-300 w-full font-sans" placeholder="未命名规则">
                    </div>
                    <button @click="removeRule(index)" class="text-slate-300 hover:text-red-500 transition-colors px-1" title="删除"><i class="fa-solid fa-trash text-[10px]"></i></button>
                </div>
                <div class="p-3 flex flex-col gap-3">
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label"><i class="fa-solid fa-eye mr-1"></i>监听来源</span><label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="rule.check_file" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0"><span class="text-[10px] text-slate-500 font-medium" :class="{'text-primary': rule.check_file}">文件模式</span></label></div>
                        <div class="relative"><textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" rows="1" class="bento-input w-full px-2 py-1.5 resize-none h-8 leading-tight font-mono text-[11px]" placeholder="群ID (换行分隔)"></textarea></div>
                        <div v-if="!rule.check_file" class="relative">
                            <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="2" class="bento-input w-full px-2 py-1.5 resize-none h-16 leading-tight font-mono text-[11px] placeholder-slate-400" placeholder="普通: 代存&#10;正则: r:(代|带)存|入[金款]"></textarea>
                            <div class="absolute right-2 bottom-1 text-[9px] text-primary/60 bg-white/80 px-1 rounded pointer-events-none">支持正则 r:...</div>
                        </div>
                        <div v-else class="space-y-2">
                            <div class="grid grid-cols-2 gap-2"><input :value="listToString(rule.file_extensions).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'file_extensions')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="后缀: xlsx, png"><input :value="listToString(rule.filename_keywords).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'filename_keywords')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="文件名关键词"></div>
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="section-label"><i class="fa-solid fa-filter mr-1"></i>过滤与冷却</div>
                        <div class="grid grid-cols-5 gap-2">
                            <div class="col-span-2"><select v-model="rule.sender_mode" class="bento-input w-full px-1 py-0 h-7 text-[10px] font-sans font-medium"><option value="exclude">🚫 排除前缀</option><option value="include">✅ 只许前缀</option></select></div>
                            <div class="col-span-3"><input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" class="bento-input w-full px-2 py-1.5 h-7 truncate font-mono text-[11px]" placeholder="前缀: YY, AA"></div>
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
    const { createApp, reactive, onMounted } = Vue;
    createApp({
        setup() {
            const config = reactive({ enabled: false, approval_keywords: [], schedule: {active: false, start: '09:00', end: '21:00'}, rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });
            const recovery = reactive({ search: '', reply: '', hours: 5, min: 2, max: 5 });

            onMounted(() => {
                document.getElementById('loading-mask').style.display = 'none';
            });

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

def check_sender_allowed(sender_name, rule):
    if not sender_name: return True
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = False
    for p in prefixes:
        if p and sender_name.startswith(p):
            match_prefix = True
            break
    if sender_mode == "exclude" and match_prefix: return False
    elif sender_mode == "include" and not match_prefix: return False
    return True

def format_caption(tpl):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    res = tpl.replace('{time}', now_str)
    return res

async def analyze_message(client, rule, event, other_cs_ids, sender_name):
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
        if not match_text(text, rule): return False, "文本关键词不符", None
    
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中", None
    
    return True, "✅ 匹配成功", None

# [安全改进] 自动排班任务 - 带数据源检查
async def run_schedule_job():
    while True:
        try:
            await asyncio.sleep(60)
            
            # 如果配置还没加载成功（用了默认模板），绝对不要执行自动保存
            if CONFIG_SOURCE == 'DEFAULT':
                continue

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
            
            # 只有状态真正改变时，才触发保存（减少DB写入）
            if is_working_hours and not current_config["enabled"]:
                current_config["enabled"] = True
                save_config(current_config, is_auto_save=True) # 传入标记
                logger.info(f"⏰ [Schedule] 上班时间到了 ({start_str})，自动开启监听")
                
            elif not is_working_hours and current_config["enabled"]:
                current_config["enabled"] = False
                save_config(current_config, is_auto_save=True) # 传入标记
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

    # [修复] 强制使用 UTF-8 编码的 HTML 响应
    @app.route('/zd')
    def monitor_settings_page(): 
        return Response(SETTINGS_HTML, mimetype='text/html; charset=utf-8')
        
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

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug": await event.reply("Monitor Debug: Alive v39 Zero-Interference (Direct Read)"); return
        if not current_config.get("enabled", True): return
        
        if event.is_reply:
            app_kws = current_config.get("approval_keywords", ["同意", "批准", "ok"])
            if any(k in event.text for k in app_kws):
                try:
                    approver = await event.get_sender()
                    approver_name = getattr(approver, 'first_name', '') or ''
                    
                    original_msg = await event.get_reply_message()
                    if original_msg:
                        orig_sender = await original_msg.get_sender()
                        orig_sender_name = getattr(orig_sender, 'first_name', '') or ''

                        for rule in current_config.get("rules", []):
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

        sender_name = ""
        try:
            event.sender = await event.get_sender()
            sender_name = getattr(event.sender, 'first_name', '') or ''
        except: pass

        for rule in current_config.get("rules", []):
            try:
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

    logger.info("🛠️ [Monitor] Ultimate UI v39 (Zero-Interference) 已启动")
