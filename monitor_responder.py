import asyncio
import logging
import time
import random
import json
import os
import re
import io
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, Response
from telethon import events

# 导入 Excel 处理与解密库
try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    pd = None
    HAS_PANDAS = False

try:
    import msoffcrypto
    HAS_CRYPTO = True
except ImportError:
    msoffcrypto = None
    HAS_CRYPTO = False

try: import redis
except ImportError: redis = None

logger = logging.getLogger("BotLogger")

CONFIG_FILE = "monitor_config_v2.json"
REDIS_KEY = "monitor_config"
global_main_handler = None

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True,
    # 新增: 审批配置
    "approval": {
        "keywords": ["同意", "批准", "ok"],
        "reply_admin": "请稍等ART",
        "reply_origin": "✅ 领导已批准，已报备"
    },
    "rules": [
        {
            "id": "default_rule",
            "name": "示例规则",
            "groups": [-1002169616907],
            "check_file": False,
            "keywords": ["报备"], 
            "file_extensions": [],
            "filename_keywords": [],
            "excel_password": "",
            "excel_sheet_name": "",
            "excel_target_column": "",
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                {
                    "type": "amount_logic", 
                    "forward_to": -100123456789, 
                    "text": "2000|⚠️ 需领导同意|请稍等ART;;✅ 已报备",
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
    
    # 补全配置
    if "approval" not in current_config:
        current_config["approval"] = DEFAULT_CONFIG["approval"]

    for rule in current_config["rules"]:
        if "check_file" not in rule: rule["check_file"] = False
        if "filename_keywords" not in rule: rule["filename_keywords"] = []
        if "excel_password" not in rule: rule["excel_password"] = ""
        if "excel_sheet_name" not in rule: rule["excel_sheet_name"] = ""
        if "excel_target_column" not in rule: rule["excel_target_column"] = ""
        if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
            rule["sender_prefixes"] = list(system_cs_prefixes)

def save_config(new_config):
    global current_config
    try:
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式"

        # 保存 Approval 配置
        if "approval" not in new_config:
            new_config["approval"] = DEFAULT_CONFIG["approval"]
        
        # 清洗 Keywords
        raw_app_kws = new_config["approval"].get("keywords", [])
        if isinstance(raw_app_kws, str):
            # 支持逗号或换行
            new_config["approval"]["keywords"] = [k.strip() for k in re.split(r'[,\n]', raw_app_kws) if k.strip()]

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
            
            # Excel Config
            rule["excel_password"] = str(rule.get("excel_password", "")).strip()
            rule["excel_sheet_name"] = str(rule.get("excel_sheet_name", "")).strip()
            rule["excel_target_column"] = str(rule.get("excel_target_column", "")).strip()
            
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

# --- Web UI (Bento Grid) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-[#F3F4F6]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Monitor Pro v21</title>
    <script src="https://cdn.staticfile.net/vue/3.3.4/vue.global.prod.min.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdn.staticfile.net/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
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
        .approval-panel { background: linear-gradient(135deg, #F0F9FF 0%, #FFF 100%); border: 1px solid #BAE6FD; }
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
            <span class="font-bold text-sm tracking-tight text-slate-900">Monitor <span class="text-xs text-primary font-medium bg-primary/10 px-1.5 py-0.5 rounded">Pro v21</span></span>
        </div>
        <div class="flex items-center gap-3">
            <label class="flex items-center gap-1.5 cursor-pointer select-none bg-slate-50 px-2 py-1 rounded border border-slate-200 hover:border-slate-300 transition-colors">
                <div class="w-2 h-2 rounded-full" :class="config.enabled ? 'bg-green-500' : 'bg-slate-300'"></div>
                <input type="checkbox" v-model="config.enabled" @change="saveConfig" class="hidden">
                <span class="text-[11px] font-semibold text-slate-600">{{ config.enabled ? 'Active' : 'Paused' }}</span>
            </label>
            <button @click="saveConfig" class="bg-slate-900 hover:bg-black text-white px-3 py-1 rounded text-[11px] font-bold transition-colors flex items-center gap-1.5 shadow-sm"><i class="fa-solid fa-floppy-disk"></i> 保存</button>
        </div>
    </nav>

    <main class="max-w-[1400px] mx-auto px-4 py-6 space-y-6">
        
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
                        <div v-if="!rule.check_file" class="relative"><textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="3" class="bento-input w-full px-2 py-1.5 resize-none h-20 leading-tight font-mono text-[11px] placeholder-slate-400" placeholder="红包雨 (普通)&#10;红包雨#流水 (包含前者，排除#后)&#10;提款&催促 (必须同时包含)"></textarea><div class="absolute right-2 bottom-2 text-[9px] text-primary/60 bg-white/80 px-1 rounded pointer-events-none">支持 #排除 &且</div></div>
                        <div v-else class="space-y-2">
                            <div class="grid grid-cols-2 gap-2"><input :value="listToString(rule.file_extensions).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'file_extensions')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="后缀: xlsx, png"><input :value="listToString(rule.filename_keywords).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'filename_keywords')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="文件名关键词"></div>
                            <div class="grid grid-cols-2 gap-2"><input v-model="rule.excel_password" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="密码 (可选)"><input v-model="rule.excel_sheet_name" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="Sheet名 (可选)"></div>
                            <div class="relative"><input v-model="rule.excel_target_column" class="bento-input w-full px-2 py-1.5 h-7 border-green-200 bg-green-50/30 focus:border-green-400 font-mono text-[11px]" placeholder="提取特定列名 (可选,如: 会员账号)"><div class="absolute right-2 top-1.5 text-[9px] text-green-600/60 pointer-events-none">提取列</div></div>
                            <div class="relative"><textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="1" class="bento-input w-full px-2 py-1.5 resize-none h-8 leading-tight font-mono text-[11px] border-yellow-200 focus:border-yellow-400" placeholder="Excel 内容匹配 (支持 & # 语法)"></textarea></div>
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
            </div>
            <div @click="addRule" class="border border-dashed border-slate-300 rounded-lg flex flex-col items-center justify-center p-4 cursor-pointer hover:border-primary hover:bg-slate-50 transition-all min-h-[200px] text-slate-400 hover:text-primary group"><div class="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center mb-2 group-hover:bg-primary/10 transition-colors"><i class="fa-solid fa-plus text-lg"></i></div><span class="text-xs font-bold">新建规则卡片</span></div>
        </div>

        <div class="bento-card approval-panel p-4 flex flex-col gap-3 shadow-sm hover:shadow-md transition-all">
            <div class="flex items-center gap-3">
                <div class="w-10 h-10 bg-blue-100 text-blue-500 rounded-lg flex items-center justify-center text-xl shrink-0"><i class="fa-solid fa-user-check"></i></div>
                <div><h3 class="text-sm font-bold text-slate-800">审批指令配置 (Approval Listener)</h3><p class="text-[10px] text-slate-500 mt-0.5">当收到"同意"指令时，自动处理之前卡住的"大额申请"</p></div>
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                <div class="flex flex-col gap-1"><label class="text-[9px] font-bold text-slate-500 uppercase">触发关键词 (逗号分隔)</label><input :value="(config.approval?.keywords || []).join(', ')" @input="val => config.approval.keywords = val.target.value.split(/[,，]/).map(s=>s.trim()).filter(s=>s)" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-blue-200 focus:border-blue-400" placeholder="同意, 批准, ok"></div>
                <div class="flex flex-col gap-1"><label class="text-[9px] font-bold text-slate-500 uppercase">回复领导 (操作人)</label><input v-model="config.approval.reply_admin" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-blue-200 focus:border-blue-400" placeholder="请稍等ART"></div>
                <div class="flex flex-col gap-1"><label class="text-[9px] font-bold text-slate-500 uppercase">回复申请人 (原消息)</label><input v-model="config.approval.reply_origin" class="bento-input px-2 py-1.5 h-8 text-xs font-mono border-blue-200 focus:border-blue-400" placeholder="✅ 领导已批准，已报备"></div>
            </div>
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
            const config = reactive({ enabled: true, rules: [], approval: {keywords:[], reply_admin:'', reply_origin:''} });
            const toast = reactive({ show: false, msg: '', type: 'success' });
            const recovery = reactive({ search: '', reply: '', hours: 5, min: 2, max: 5 });

            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { 
                    config.enabled = data.enabled; 
                    config.rules = (data.rules || []).map(r => {
                        if(r.replies) r.replies = r.replies.map(rep => ({...rep, type: rep.type || 'text'}));
                        if(r.check_file === undefined) r.check_file = false;
                        if(!r.file_extensions) r.file_extensions = [];
                        if(!r.filename_keywords) r.filename_keywords = [];
                        if(!r.sender_prefixes) r.sender_prefixes = [];
                        if(!r.keywords) r.keywords = [];
                        if(!r.excel_password) r.excel_password = "";
                        if(!r.excel_sheet_name) r.excel_sheet_name = "";
                        if(!r.excel_target_column) r.excel_target_column = "";
                        return r;
                    });
                    if(data.approval) config.approval = data.approval;
                    else config.approval = {keywords:['同意'], reply_admin:'请稍等ART', reply_origin:'✅ 领导已批准，已报备'};
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
                    excel_password: '', excel_sheet_name: '', excel_target_column: '',
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
    keywords = rule.get("keywords", [])
    if not keywords: return True 
    for kw_rule in keywords:
        if not kw_rule: continue
        kw_rule = kw_rule.lower()
        text_lower = text.lower()
        parts = kw_rule.split('#')
        include_part = parts[0]
        exclude_part = parts[1] if len(parts) > 1 else None
        if exclude_part and exclude_part.strip() and (exclude_part.strip() in text_lower): continue 
        and_kws = include_part.split('&')
        all_matched = True
        for ak in and_kws:
            ak = ak.strip()
            if ak and (ak not in text_lower):
                all_matched = False
                break
        if all_matched and and_kws: return True
    return False

def format_caption(tpl, extracted_data=None):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    res = tpl.replace('{time}', now_str)
    if extracted_data: res = res.replace('{data}', extracted_data)
    return res

async def analyze_message(client, rule, event, other_cs_ids, sender_name):
    if event.chat_id not in rule.get("groups", []): return False, "群组不符", None
    if event.is_reply: return False, "是回复消息", None
    if event.out: return False, "Bot自己发送", None
    if event.sender_id in other_cs_ids: return False, "ID是客服", None
    
    check_file = rule.get("check_file", False)
    text = (event.text or "")
    extracted_data = "" 
    
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
        
        content_kws = rule.get("keywords", [])
        target_col = rule.get("excel_target_column", "")
        
        if (content_kws or target_col) and ext in ['xlsx', 'xls'] and HAS_PANDAS:
            if event.message.file.size > 10 * 1024 * 1024: 
                logger.warning(f"⚠️ [Monitor] Excel too large, skip.")
            else:
                try:
                    blob = await client.download_media(event.message, file=bytes)
                    excel_pass = rule.get("excel_password", "")
                    source = None
                    if excel_pass and HAS_CRYPTO:
                        try:
                            decrypted = io.BytesIO()
                            file = msoffcrypto.OfficeFile(io.BytesIO(blob))
                            file.load_key(password=excel_pass)
                            file.decrypt(decrypted)
                            decrypted.seek(0)
                            source = decrypted
                        except Exception as e: return False, "解密失败", None
                    else: source = io.BytesIO(blob)
                    
                    target_sheet = rule.get("excel_sheet_name")
                    sheet_arg = 0
                    if target_sheet: sheet_arg = target_sheet
                    with source as f:
                        df = pd.read_excel(f, sheet_name=sheet_arg)
                        if target_col:
                            df.columns = df.columns.astype(str).str.strip()
                            if target_col in df.columns:
                                col_data = df[target_col].dropna().astype(str).tolist()
                                extracted_data = ", ".join(col_data)
                        excel_text = " ".join(df.astype(str).values.flatten())
                        if not match_text(excel_text, rule): return False, "Excel内容不匹配", None
                except Exception as e: return False, "读取Excel失败", None
    else:
        if not match_text(text, rule): return False, "文本关键词不符", None

    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_prefix = any(sender_name.startswith(p) for p in prefixes)
    if sender_mode == "exclude" and match_prefix: return False, "前缀被排除", None
    elif sender_mode == "include" and not match_prefix: return False, "前缀不在白名单", None
    
    rule_id = rule.get("id", str(rule.get("groups")))
    last_time = rule_timers.get(rule_id, 0)
    now = time.time()
    if now - last_time < rule.get("cooldown", 60): return False, "冷却中", None
    
    return True, "✅ 匹配成功", extracted_data

def format_caption(tpl, extracted_data=None):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    res = tpl.replace('{time}', now_str)
    if extracted_data: res = res.replace('{data}', extracted_data)
    return res

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global_main_handler = main_handler
    init_redis_connection()
    load_config(main_cs_prefixes)
    
    try: bot_loop = client.loop
    except:
        try: bot_loop = asyncio.get_event_loop()
        except: bot_loop = asyncio.new_event_loop(); asyncio.set_event_loop(bot_loop)

    @app.route('/zd')
    def monitor_settings_page(): return Response(SETTINGS_HTML, mimetype='text/html')
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
        if event.text == "/debug": await event.reply("Monitor Debug: Alive v21 Approval Listener"); return
        if not current_config.get("enabled", True): return
        
        # --- 1. 优先检测：是否为领导审批指令 (同意/批准) ---
        if event.is_reply:
            app_cfg = current_config.get("approval", {})
            app_kws = app_cfg.get("keywords", [])
            if app_kws and any(k in event.text for k in app_kws):
                # 领导发了“同意”，现在去查原消息
                try:
                    original_msg = await event.get_reply_message()
                    if original_msg:
                        # 重新用规则扫描一遍原消息，看它本来要去哪
                        # 为了复用 analyze_message，我们需要模拟 original_msg 的 event
                        # 但这里我们简化逻辑：直接遍历规则，看谁匹配
                        sender_name = "" # 原消息发送者名字，暂空
                        for rule in current_config.get("rules", []):
                            # 这里需要 modify analyze_message 接受 message 对象，或者复用逻辑
                            # 简单起见，我们假设 analyze_message 逻辑够通用
                            # 注意：我们这里手动模拟一下 check
                            is_match, _, _ = await analyze_message(client, rule, events.NewMessage.Event(original_msg), other_cs_ids, sender_name)
                            
                            if is_match:
                                # 找到了对应的规则，提取 forward_to
                                # 只要规则里有任意一个 forward 动作或者 amount_logic 动作，就提取它的 target
                                target_id = None
                                for step in rule.get("replies", []):
                                    if step.get("forward_to"):
                                        target_id = step.get("forward_to")
                                        break
                                
                                if target_id:
                                    logger.info(f"👮 [Approval] 批准通过! 原消息ID: {original_msg.id} -> 转发至: {target_id}")
                                    
                                    # 1. 回复领导
                                    if app_cfg.get("reply_admin"):
                                        await event.reply(format_caption(app_cfg["reply_admin"]))
                                    
                                    # 2. 转发原消息到目标群
                                    try:
                                        await client.forward_messages(int(str(target_id).strip()), original_msg)
                                    except Exception as e:
                                        logger.error(f"❌ [Approval] 转发失败: {e}")

                                    # 3. 回复原消息
                                    if app_cfg.get("reply_origin"):
                                        await original_msg.reply(format_caption(app_cfg["reply_origin"]))
                                    
                                    # 处理一条规则即可，防止重复
                                    return 
                except Exception as e:
                    logger.error(f"❌ [Approval] 处理出错: {e}")

        # --- 2. 常规消息监听 ---
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
                                sent_msgs.append(await client.send_file(int(str(tgt).strip()), event.message.file.media, caption=format_caption(step.get("text", ""), extracted_data)))
                        
                        elif stype == "amount_logic":
                            cfg = step.get("text", "")
                            tgt = step.get("forward_to")
                            parts = cfg.split('|')
                            if len(parts) >= 3:
                                thresh = float(parts[0])
                                amt_match = re.search(r"[:：]?\s*(\d+)", event.text) # 稍微放宽正则
                                if amt_match:
                                    amt = float(amt_match.group(1))
                                    if amt >= thresh:
                                        sent_msgs.append(await event.reply(format_caption(parts[1], extracted_data)))
                                    else:
                                        for sub_msg in parts[2].split(';;'):
                                            if sub_msg.strip():
                                                sent_msgs.append(await event.reply(format_caption(sub_msg, extracted_data)))
                                                await asyncio.sleep(1)
                                        if tgt: await client.forward_messages(int(str(tgt).strip()), event.message)

                        elif stype == "preempt_check":
                            if not sent_msgs: continue
                            me = await client.get_me()
                            # 简单防撞车：查最近10条，如果有除了我和发送者之外的人说话，就撤回
                            hist = await client.get_messages(event.chat_id, limit=10, min_id=event.id)
                            if any(m.sender_id != me.id and m.sender_id != event.sender_id for m in hist):
                                await client.delete_messages(event.chat_id, sent_msgs)
                                sent_msgs = []
                                break

                        else: # text
                            content = step.get("text", "")
                            if content: 
                                sent = await event.reply(format_caption(content, extracted_data))
                                sent_msgs.append(sent)
                                if global_main_handler: asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
                    break
            except Exception as e: logger.error(f"❌ [Monitor] Rule Error: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v21 (Approval Listener) 已启动")
