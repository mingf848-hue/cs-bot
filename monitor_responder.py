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

# === 💀 核心配置（已写死） ===
FIXED_NTFY_URL = "https://ntfy.sh/cs_help_bridge_2026_fixed"
FIXED_REGEX = r"(?=.*催)(?=.*5\d{15})"  # 必须包含“催”且包含“5开头16位数字”

# --- 默认配置结构 ---
DEFAULT_CONFIG = {
    "enabled": True,
    "rules": [
        {
            "id": "urgent_rule",
            "name": "催单固定规则",
            "groups": [-1002169616907], # 如果你的群ID变了，请在网页设置里改，或者这里改
            "keywords": [],
            "regex": FIXED_REGEX,
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [
                # 1. 先在 TG 回复
                {"type": "text", "text": "请稍等ART", "min": 1, "max": 2},
                # 2. 发送到 Teams (通过 Ntfy)
                {"type": "teams_webhook", "webhook_url": FIXED_NTFY_URL, "min": 0, "max": 1}
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
    
    # 强制覆盖为固定逻辑，防止配置乱掉
    for rule in current_config["rules"]:
        rule["regex"] = FIXED_REGEX
        rule["replies"] = [
            {"type": "text", "text": "请稍等ART", "min": 1, "max": 2},
            {"type": "teams_webhook", "webhook_url": FIXED_NTFY_URL, "min": 0, "max": 1}
        ]

def save_config(new_config):
    global current_config
    try:
        # 保存逻辑...
        if redis_client:
            try: redis_client.set(REDIS_KEY, json.dumps(new_config, ensure_ascii=False))
            except: pass
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        current_config = new_config
        return True, "保存成功"
    except Exception as e:
        return False, str(e)

# --- Web UI (省略部分HTML，保持原样即可，重点是API) ---
SETTINGS_HTML = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>Bot Settings</title></head><body><h1>Bot Running...</h1></body></html>"""

def analyze_message(rule, event, other_cs_ids, sender_name):
    # 群组检查
    if event.chat_id not in rule.get("groups", []): return False, "群组不符"
    if event.out: return False, "Bot自己发送"
    
    text = event.text or ""
    
    # === 正则匹配 (写死逻辑) ===
    # 必须同时匹配：催 + 5开头16位数字
    if not re.search(FIXED_REGEX, text, re.DOTALL):
        return False, "正则不匹配"
    
    # 冷却时间
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

    # === 接收 Teams 回复 ===
    @app.route('/api/teams_reply', methods=['POST'])
    def receive_teams_reply():
        try:
            data = request.json
            reply_text = data.get('text', '')
            
            if reply_text and global_client:
                # 这里的逻辑是：把 Automa 抓回来的 Teams 回复，发到 TG 群里
                # 因为没法精准定位到原来的那条消息ID去 reply，我们直接发到群里，带上引用的格式
                target_group = None
                if current_config['rules'] and current_config['rules'][0]['groups']:
                    target_group = current_config['rules'][0]['groups'][0]
                
                if target_group:
                    logger.info(f"📨 [Teams回复] 转发: {reply_text}")
                    async def send_back():
                        # 回复内容
                        await global_client.send_message(target_group, f"**[Teams 客服回复]**\n{reply_text}")
                    global_client.loop.create_task(send_back())
                    return jsonify({"status": "sent"}), 200
            
            return jsonify({"status": "ignored"}), 200
        except Exception as e:
            logger.error(f"Teams Reply Error: {e}")
            return jsonify({"status": "error"}), 500

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
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
                    logger.info(f"✅ [Monitor] 触发! {event.text[:10]}...")
                    rule_id = rule.get("id", str(rule.get("groups")))
                    rule_timers[rule_id] = time.time()
                    
                    for step in rule.get("replies", []):
                        # 随机延迟
                        await asyncio.sleep(random.uniform(step.get("min", 0), step.get("max", 1)))
                        
                        step_type = step.get("type", "text")

                        if step_type == "text":
                            # 1. 在 TG 回复 "请稍等ART"
                            await event.reply(step.get("text", "请稍等ART"))
                            
                        elif step_type == "teams_webhook":
                            # 2. 发送到 Ntfy (Teams)
                            url = step.get("webhook_url")
                            try:
                                msg_text = event.text or ""
                                # === 核心：拼接发送给 Teams 的内容 ===
                                # 格式：用户原话 + 空格 + ART
                                # 这样 Automa 发出去后，消息末尾就有 ART，方便识别
                                content_to_teams = f"From {sender_name}:\n{msg_text} ART"
                                
                                # 发送纯文本到 Ntfy
                                requests.post(url, data=content_str_teams.encode('utf-8'), timeout=5)
                                logger.info(f"📢 已推送到 Teams/Ntfy")
                            except Exception as e:
                                logger.error(f"❌ Webhook 异常: {e}")
                    break
            except Exception as e:
                logger.error(f"❌ 规则错误: {e}")

    logger.info("🛠️ [Monitor] Ready")
