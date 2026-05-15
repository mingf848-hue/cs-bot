import asyncio
import logging
import time
import random
import json
import os
import re
import unicodedata
import warnings
import urllib.request
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from flask import request, jsonify, Response, render_template_string
from telethon import events, TelegramClient, functions
from telethon.sessions import StringSession

# 忽略 asyncio 的一些无关紧要的警告
warnings.filterwarnings("ignore", category=RuntimeWarning)

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
RUNTIME_STATS_FILE = "monitor_runtime_stats.json"
RUNTIME_STATS_KEY = "monitor_runtime_stats_v1"
MAX_RUNTIME_RECORDS = 800
MAX_DAILY_STATS_DAYS = 60
global_main_handler = None

# ==========================================
# [配置区] 时区设置 - 北京时间 (UTC+8)
# ==========================================
BJ_TZ = timezone(timedelta(hours=8))
TZ_NAME = "北京时间"

# ==========================================
# [全局存储] 
# ==========================================
latest_otp_storage = {}
global_clients = {}  # 存储所有活跃的客户端实例 {name: client}
client_user_ids = {}  # 缓存已知账号的 Telegram user id {name: id}
MAIN_NAME = "主账号" # 全局记录主账号名称
redis_client = None  # 全局 Redis 客户端
system_cs_prefixes = []
service_started_at = datetime.now(BJ_TZ)
runtime_stats_lock = threading.Lock()
runtime_stats = {"records": [], "daily": {}}
ALBUM_REPLY_DEDUP_LIMIT = 5000
album_reply_dedup_queue = deque()
album_reply_dedup_keys = set()

# --- 核心工具函数 ---

def match_text(text, rule):
    """通用文本匹配逻辑 (支持 & # 和 r:正则)"""
    keywords = rule.get("keywords", [])
    if not keywords: return True 
    
    text_lower = text.lower()
    
    for kw_rule in keywords:
        if not kw_rule: continue
        
        # 1. 统一分割排除词 (Separator: #)
        parts = kw_rule.split('#')
        include_part = parts[0].strip()
        exclude_parts = [p.strip().lower() for p in parts[1:] if p.strip()]
        
        # 2. 检查排除词
        hit_exclusion = False
        for ex in exclude_parts:
            if ex in text_lower:
                hit_exclusion = True
                break
        if hit_exclusion: continue
        
        # 3. 执行主匹配
        include_part_lower = include_part.lower()
        
        if include_part_lower.startswith('r:'):
            try:
                pattern = include_part[2:] # 去掉 'r:'
                if re.search(pattern, text, re.IGNORECASE):
                    return True
            except: pass
        else:
            and_kws = include_part_lower.split('&')
            all_matched = True
            for ak in and_kws:
                ak = ak.strip()
                if ak and (ak not in text_lower):
                    all_matched = False
                    break
            if all_matched and and_kws:
                return True
    return False

def should_skip_album_reply(chat_id, grouped_id):
    """同一组图集只触发一次自动回复，避免多图消息按单张图片重复执行。"""
    if not grouped_id:
        return False

    key = (chat_id, grouped_id)
    if key in album_reply_dedup_keys:
        return True

    album_reply_dedup_keys.add(key)
    album_reply_dedup_queue.append(key)
    while len(album_reply_dedup_queue) > ALBUM_REPLY_DEDUP_LIMIT:
        old_key = album_reply_dedup_queue.popleft()
        album_reply_dedup_keys.discard(old_key)
    return False

def get_sender_name(sender):
    """统一提取发送者名称"""
    if not sender: return "Unknown"
    title = getattr(sender, 'title', '')
    if title: return title
    fname = getattr(sender, 'first_name', '') or ""
    lname = getattr(sender, 'last_name', '') or ""
    fullname = f"{fname} {lname}".strip()
    uname = getattr(sender, 'username', '')
    if uname:
        return f"{fullname} (@{uname})".strip()
    return fullname or "Unknown"

def now_bj():
    return datetime.now(BJ_TZ)

def parse_bj_datetime(raw):
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BJ_TZ)
        return dt.astimezone(BJ_TZ)
    except Exception:
        return None

def empty_daily_summary():
    return {
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "action_count": 0,
        "hourly": [
            {"hour": f"{hour:02d}", "total": 0, "success": 0, "failed": 0, "skipped": 0}
            for hour in range(24)
        ],
        "by_rule": {}
    }

def apply_record_to_daily(daily, record):
    ts = parse_bj_datetime(record.get("ts")) or now_bj()
    day = record.get("day") or ts.strftime("%Y-%m-%d")
    day_summary = daily.setdefault(day, empty_daily_summary())
    status = record.get("status")
    action_count = int(record.get("action_count") or 0)

    day_summary["total"] = int(day_summary.get("total") or 0) + 1
    day_summary["action_count"] = int(day_summary.get("action_count") or 0) + action_count
    if status in ("success", "failed", "skipped"):
        day_summary[status] = int(day_summary.get(status) or 0) + 1

    hourly = day_summary.get("hourly")
    if not isinstance(hourly, list) or len(hourly) != 24:
        hourly = empty_daily_summary()["hourly"]
        day_summary["hourly"] = hourly
    bucket = hourly[ts.hour]
    bucket["total"] = int(bucket.get("total") or 0) + 1
    if status in ("success", "failed", "skipped"):
        bucket[status] = int(bucket.get(status) or 0) + 1

    rule_id = str(record.get("rule_id") or "__system__")
    by_rule = day_summary.setdefault("by_rule", {})
    rule_summary = by_rule.setdefault(rule_id, {
        "id": rule_id,
        "name": record.get("rule_name") or "系统任务",
        "total": 0,
        "success": 0,
        "failed": 0,
        "skipped": 0,
        "action_count": 0,
        "last_record": None
    })
    rule_summary["name"] = record.get("rule_name") or rule_summary.get("name") or "系统任务"
    rule_summary["total"] = int(rule_summary.get("total") or 0) + 1
    rule_summary["action_count"] = int(rule_summary.get("action_count") or 0) + action_count
    if status in ("success", "failed", "skipped"):
        rule_summary[status] = int(rule_summary.get(status) or 0) + 1
    if not rule_summary.get("last_record") or str(record.get("ts", "")) > str(rule_summary["last_record"].get("ts", "")):
        rule_summary["last_record"] = record

def normalize_daily_stats(raw_daily, records):
    daily = raw_daily if isinstance(raw_daily, dict) else {}
    if not daily:
        rebuilt = {}
        for record in reversed(records):
            apply_record_to_daily(rebuilt, record)
        daily = rebuilt

    normalized = {}
    for day in sorted(daily.keys(), reverse=True)[:MAX_DAILY_STATS_DAYS]:
        source = daily.get(day) if isinstance(daily.get(day), dict) else {}
        summary = empty_daily_summary()
        for key in ("total", "success", "failed", "skipped", "action_count"):
            try:
                summary[key] = int(source.get(key) or 0)
            except Exception:
                summary[key] = 0

        source_hourly = source.get("hourly")
        if isinstance(source_hourly, list):
            for idx, item in enumerate(source_hourly[:24]):
                if not isinstance(item, dict):
                    continue
                for key in ("total", "success", "failed", "skipped"):
                    try:
                        summary["hourly"][idx][key] = int(item.get(key) or 0)
                    except Exception:
                        summary["hourly"][idx][key] = 0

        source_by_rule = source.get("by_rule")
        if isinstance(source_by_rule, dict):
            for rule_id, item in source_by_rule.items():
                if not isinstance(item, dict):
                    continue
                rule_summary = {
                    "id": str(item.get("id") or rule_id),
                    "name": item.get("name") or "系统任务",
                    "total": int(item.get("total") or 0),
                    "success": int(item.get("success") or 0),
                    "failed": int(item.get("failed") or 0),
                    "skipped": int(item.get("skipped") or 0),
                    "action_count": int(item.get("action_count") or 0),
                    "last_record": item.get("last_record") if isinstance(item.get("last_record"), dict) else None
                }
                summary["by_rule"][str(rule_summary["id"])] = rule_summary
        normalized[day] = summary
    return normalized

def normalize_runtime_stats(data):
    if not isinstance(data, dict):
        return {"records": [], "daily": {}}
    records = data.get("records", [])
    if not isinstance(records, list):
        records = []
    clean_records = []
    for record in records:
        if not isinstance(record, dict):
            continue
        ts = parse_bj_datetime(record.get("ts"))
        if ts:
            record["ts"] = ts.isoformat()
            record["day"] = ts.strftime("%Y-%m-%d")
            record["time"] = ts.strftime("%H:%M:%S")
        clean_records.append(record)
    clean_records.sort(key=lambda item: item.get("ts", ""), reverse=True)
    return {
        "records": clean_records[:MAX_RUNTIME_RECORDS],
        "daily": normalize_daily_stats(data.get("daily"), clean_records)
    }

def load_runtime_stats():
    global runtime_stats
    loaded = False

    if redis_client:
        try:
            data = redis_client.get(RUNTIME_STATS_KEY)
            if data:
                runtime_stats = normalize_runtime_stats(json.loads(data))
                loaded = True
                logger.info("📈 [Monitor] 已从 Redis 恢复运行统计")
        except Exception as e:
            logger.error(f"⚠️ [Monitor] Redis 运行统计读取出错: {e}")

    if not loaded and os.path.exists(RUNTIME_STATS_FILE):
        try:
            with open(RUNTIME_STATS_FILE, "r", encoding="utf-8") as f:
                runtime_stats = normalize_runtime_stats(json.load(f))
                loaded = True
                logger.info("📈 [Monitor] 已从本地文件恢复运行统计")
        except Exception as e:
            logger.error(f"⚠️ [Monitor] 本地运行统计读取出错: {e}")

    if not loaded:
        runtime_stats = {"records": [], "daily": {}}

def persist_runtime_stats():
    with runtime_stats_lock:
        data = normalize_runtime_stats(runtime_stats)

    if redis_client:
        try:
            redis_client.set(RUNTIME_STATS_KEY, json.dumps(data, ensure_ascii=False))
        except Exception as e:
            logger.error(f"⚠️ [Monitor] Redis 运行统计保存失败: {e}")

    try:
        with open(RUNTIME_STATS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"⚠️ [Monitor] 本地运行统计保存失败: {e}")

def event_text_preview(event, limit=120):
    text = ""
    if event is not None:
        text = getattr(event, "raw_text", None) or getattr(event, "text", "") or ""
        if not text and getattr(event, "message", None) is not None:
            text = getattr(event.message, "raw_text", None) or getattr(event.message, "text", "") or ""
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > limit:
        return text[:limit] + "..."
    return text

def record_runtime_event(kind, status, detail="", rule=None, event=None, sender_name="", target_account="", action_count=0, duration_ms=0):
    ts = now_bj()
    rule_id = str((rule or {}).get("id") or "")
    rule_name = str((rule or {}).get("name") or "系统任务")
    message = getattr(event, "message", event)
    record = {
        "id": f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}",
        "kind": str(kind or "monitor"),
        "status": str(status or "success"),
        "detail": str(detail or ""),
        "rule_id": rule_id,
        "rule_name": rule_name,
        "target_account": str(target_account or ""),
        "sender_name": str(sender_name or ""),
        "sender_id": getattr(event, "sender_id", None),
        "chat_id": getattr(event, "chat_id", None) if event is not None else None,
        "message_id": getattr(message, "id", None) or getattr(event, "id", None),
        "message_text": event_text_preview(event),
        "action_count": int(action_count or 0),
        "duration_ms": int(duration_ms or 0),
        "ts": ts.isoformat(),
        "day": ts.strftime("%Y-%m-%d"),
        "time": ts.strftime("%H:%M:%S")
    }

    with runtime_stats_lock:
        runtime_stats.setdefault("records", []).insert(0, record)
        apply_record_to_daily(runtime_stats.setdefault("daily", {}), record)
        normalized = normalize_runtime_stats(runtime_stats)
        runtime_stats["records"] = normalized["records"]
        runtime_stats["daily"] = normalized["daily"]
    persist_runtime_stats()
    return record

def get_account_summaries():
    accounts = []
    for name, cli in global_clients.items():
        connected = True
        try:
            if hasattr(cli, "is_connected"):
                connected = bool(cli.is_connected())
        except Exception:
            connected = False
        accounts.append({
            "name": name,
            "role": "主账号" if name == MAIN_NAME else "副账号",
            "connected": connected,
            "user_id": client_user_ids.get(name)
        })
    return accounts

def build_monitor_runtime_stats(limit=160):
    now = now_bj()
    today = now.strftime("%Y-%m-%d")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    with runtime_stats_lock:
        records = list(runtime_stats.get("records", []))
        daily = normalize_daily_stats(runtime_stats.get("daily", {}), records)

    today_daily = daily.get(today, empty_daily_summary())
    yesterday_daily = daily.get(yesterday, empty_daily_summary())

    def summarize(day_summary):
        success = int(day_summary.get("success") or 0)
        failed = int(day_summary.get("failed") or 0)
        skipped = int(day_summary.get("skipped") or 0)
        actions = int(day_summary.get("action_count") or 0)
        finished = success + failed
        success_rate = round((success / finished * 100), 1) if finished else 0.0
        return {
            "total": int(day_summary.get("total") or 0),
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "action_count": actions,
            "success_rate": success_rate
        }

    today_summary = summarize(today_daily)
    yesterday_summary = summarize(yesterday_daily)

    hourly = today_daily.get("hourly")
    if not isinstance(hourly, list) or len(hourly) != 24:
        hourly = empty_daily_summary()["hourly"]

    by_rule = {}
    for rule in current_config.get("rules", []):
        rule_id = str(rule.get("id") or "")
        if not rule_id:
            continue
        by_rule[rule_id] = {
            "id": rule_id,
            "name": rule.get("name") or "未命名规则",
            "total": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "action_count": 0,
            "success_rate": 0.0,
            "last_record": None
        }

    aggregate_by_rule = today_daily.get("by_rule", {})
    if not isinstance(aggregate_by_rule, dict):
        aggregate_by_rule = {}
    for rule_id, record in aggregate_by_rule.items():
        rule_id = str(rule_id or "__system__")
        if rule_id not in by_rule:
            by_rule[rule_id] = {
                "id": rule_id,
                "name": record.get("name") or "系统任务",
                "total": 0,
                "success": 0,
                "failed": 0,
                "skipped": 0,
                "action_count": 0,
                "success_rate": 0.0,
                "last_record": None
            }
        item = by_rule[rule_id]
        item["name"] = record.get("name") or item["name"]
        item["total"] = int(record.get("total") or 0)
        item["success"] = int(record.get("success") or 0)
        item["failed"] = int(record.get("failed") or 0)
        item["skipped"] = int(record.get("skipped") or 0)
        item["action_count"] = int(record.get("action_count") or 0)
        item["last_record"] = record.get("last_record")

    for item in by_rule.values():
        finished = item["success"] + item["failed"]
        item["success_rate"] = round(item["success"] / finished * 100, 1) if finished else 0.0

    total_rules = len(current_config.get("rules", []))
    running_rules = 0
    disabled_rules = 0
    draft_rules = 0
    for rule in current_config.get("rules", []):
        if not rule.get("groups"):
            draft_rules += 1
        elif not rule.get("enabled", True) or (rule.get("reply_account") and not current_config.get("extra_enabled", True)):
            disabled_rules += 1
        else:
            running_rules += 1

    last_record = records[0] if records else None
    return {
        "server_time": now.isoformat(),
        "timezone": TZ_NAME,
        "service_started_at": service_started_at.isoformat(),
        "main_account": MAIN_NAME,
        "accounts": get_account_summaries(),
        "rules": {
            "total": total_rules,
            "running": running_rules,
            "disabled": disabled_rules,
            "draft": draft_rules,
            "active": running_rules
        },
        "today": today_summary,
        "yesterday": yesterday_summary,
        "hourly": hourly,
        "by_rule": sorted(by_rule.values(), key=lambda item: (item["total"], item["success"]), reverse=True),
        "records": records[:limit],
        "last_record": last_record
    }

def split_config_items(raw, split_commas=False):
    if raw is None:
        return []
    if isinstance(raw, str):
        pattern = r'[\r\n,，]+' if split_commas else r'[\r\n]+'
        items = re.split(pattern, raw)
    elif isinstance(raw, list):
        items = raw
    else:
        items = [raw]
    return [str(x).strip() for x in items if str(x).strip()]

def clean_group_ids(raw):
    result = []
    for item in split_config_items(raw, split_commas=True):
        match = re.search(r'-?\d+', item)
        if match:
            try:
                result.append(int(match.group()))
            except Exception:
                pass
    return result

def ensure_rule_id(rule):
    rule_id = str(rule.get("id", "")).strip()
    if not rule_id:
        rule_id = f"rule_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
        rule["id"] = rule_id
    return rule_id

def normalize_prefix_text(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text)).lower()
    text = re.sub(r'[\s\u200b\u200c\u200d\ufeff]+', '', text)
    return re.sub(r'[_\-/／\\|·•・.。:：]+', '', text)

def sender_matches_prefix(sender_obj, prefixes, include_username=True):
    if not sender_obj or not prefixes:
        return False

    username = (getattr(sender_obj, 'username', None) or "").strip().lower()
    norm_username = normalize_prefix_text(username)
    first_name = getattr(sender_obj, 'first_name', '') or ""
    last_name = getattr(sender_obj, 'last_name', '') or ""
    title = getattr(sender_obj, 'title', '') or ""
    fullname = f"{first_name} {last_name}".strip()
    raw_name_candidates = [str(x).strip().lower() for x in (first_name, fullname, title) if str(x).strip()]
    norm_name_candidates = [normalize_prefix_text(x) for x in raw_name_candidates]

    for p in prefixes:
        clean_p = str(p).strip().lstrip('@').lower()
        norm_p = normalize_prefix_text(clean_p)
        if not clean_p:
            continue

        if include_username and username and (clean_p in username or (norm_p and norm_p in norm_username)):
            return True

        if any(name.startswith(clean_p) for name in raw_name_candidates):
            return True

        if norm_p and any(name.startswith(norm_p) for name in norm_name_candidates):
            return True

        if norm_p and len(norm_p) >= 4 and any(norm_p in name for name in norm_name_candidates):
            return True

    return False

def check_sender_allowed(sender_obj, rule):
    """
    检查发送者是否被允许。
    Username 继续使用包含匹配；显示名使用前缀匹配，避免无 username 的客服被放行。
    """
    sender_mode = rule.get("sender_mode", "exclude")
    prefixes = rule.get("sender_prefixes", [])
    match_found = sender_matches_prefix(sender_obj, prefixes, include_username=True)
            
    if sender_mode == "exclude" and match_found: 
        logger.info(f"🛡️ [Filter] 黑名单拦截: {get_sender_name(sender_obj)}")
        return False
    elif sender_mode == "include" and not match_found: 
        return False # 白名单未命中，拦截
        
    return True

async def is_monitor_sender_cs(client, event, other_cs_ids, sender_obj=None):
    sender_id = event.sender_id
    if event.out:
        return True
    if sender_id in (other_cs_ids or []):
        return True
    if sender_id in client_user_ids.values():
        return True

    for name, cli in list(global_clients.items()):
        if name in client_user_ids:
            continue
        try:
            if hasattr(cli, "is_connected") and not cli.is_connected():
                continue
            me = await cli.get_me()
            if me:
                client_user_ids[name] = me.id
                if sender_id == me.id:
                    return True
        except Exception:
            pass

    if sender_obj is None:
        try:
            sender_obj = await event.get_sender()
        except Exception:
            sender_obj = None

    return sender_matches_prefix(sender_obj, system_cs_prefixes, include_username=False)

def remember_sent_message(sent_records, chat_id, sent):
    if not sent:
        return
    if isinstance(sent, (list, tuple)):
        for item in sent:
            remember_sent_message(sent_records, chat_id, item)
        return
    sent_records.append((chat_id, sent))

def get_sent_ids_for_chat(sent_records, chat_id):
    ids = set()
    for sent_chat_id, sent in sent_records:
        if sent_chat_id != chat_id:
            continue
        msg_id = getattr(sent, "id", sent)
        if msg_id:
            ids.add(msg_id)
    return ids

def get_first_sent_id_for_chat(sent_records, chat_id):
    ids = get_sent_ids_for_chat(sent_records, chat_id)
    if not ids:
        return None
    return min(ids)

def get_last_sent_record(sent_records):
    if not sent_records:
        return None, None
    return sent_records[-1]

def get_ordered_message_reply_target_ids(message):
    ids = []
    seen = set()

    def add_id(val):
        if val and val not in seen:
            seen.add(val)
            ids.append(val)

    for attr in ("reply_to_msg_id",):
        add_id(getattr(message, attr, None))

    reply_to = getattr(message, "reply_to", None)
    if reply_to:
        for attr in ("reply_to_msg_id", "reply_to_top_id"):
            add_id(getattr(reply_to, attr, None))
    return ids

def get_message_reply_target_ids(message):
    return set(get_ordered_message_reply_target_ids(message))

def is_same_reply_flow(message, origin_message):
    origin_id = getattr(origin_message, "id", None)
    if not origin_id:
        return False
    return origin_id in get_message_reply_target_ids(message)

async def delete_sent_messages(client, sent_records):
    ids_by_chat = {}
    for chat_id, sent in sent_records:
        msg_id = getattr(sent, "id", sent)
        if msg_id:
            ids_by_chat.setdefault(chat_id, []).append(msg_id)

    for chat_id, msg_ids in ids_by_chat.items():
        await client.delete_messages(chat_id, msg_ids)

def parse_peer_target(raw):
    target = str(raw or "").strip()
    if not target:
        return None
    if re.fullmatch(r'-?\d+', target):
        try:
            return int(target)
        except Exception:
            return target
    return target

def format_bot_notice(tpl, event=None, rule=None, sender_name=""):
    text = format_caption(tpl)
    if not text:
        return ""
    message_text = ""
    chat_id = ""
    if event is not None:
        message_text = getattr(event, "raw_text", None) or getattr(event, "text", "") or ""
        chat_id = str(getattr(event, "chat_id", "") or "")
    replacements = {
        "{rule}": str((rule or {}).get("name", "")),
        "{sender}": str(sender_name or ""),
        "{group_id}": chat_id,
        "{message}": message_text,
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text

def _send_bot_message_sync(chat_id, text):
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN 未配置，无法发送 Bot 通知")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            status = getattr(resp, "status", resp.getcode())
            resp_text = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        raise RuntimeError(f"Bot API 请求失败: {e}")
    if status != 200:
        raise RuntimeError(f"Bot API 返回 {status}: {resp_text[:200]}")
    data = json.loads(resp_text)
    if not data.get("ok"):
        raise RuntimeError(f"Bot API 发送失败: {str(data)[:200]}")
    return data

async def send_bot_notice(chat_id, text):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _send_bot_message_sync(chat_id, text))

def format_caption(tpl):
    if not tpl: return ""
    now_str = datetime.now(BJ_TZ).strftime('%Y-%-m-%-d %H:%M') 
    res = tpl.replace('{time}', now_str)
    return res

def split_reply_sequence(text):
    raw = str(text or "")
    if not raw.strip():
        return []
    parts = re.split(r'(?:;;|\\n|\r?\n)+', raw)
    return [p.strip() for p in parts if p.strip()]

def random_delay_from_step(step, min_key, max_key, default_min=1.5, default_max=3.0):
    try:
        min_val = float(step.get(min_key, default_min))
    except Exception:
        min_val = default_min
    try:
        max_val = float(step.get(max_key, default_max))
    except Exception:
        max_val = default_max
    if max_val < min_val:
        min_val, max_val = max_val, min_val
    return random.uniform(min_val, max_val)

def approval_keyword_matches(text, keywords):
    normalized_text = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    if not normalized_text:
        return False
    for keyword in keywords or []:
        normalized_keyword = unicodedata.normalize("NFKC", str(keyword or "")).strip().lower()
        if normalized_keyword and normalized_text == normalized_keyword:
            return True
    return False

def parse_smart_amount(text):
    """
    [v76 修复版] 从文本中提取金额，支持 k/w/万 等单位
    修复：排除订单号、超长数字干扰
    """
    if not text:
        return False, 0.0

    def apply_amount_unit(num, unit):
        if not unit:
            return num
        unit = unit.lower()
        if unit in ['w', '万']:
            return num * 10000
        if unit in ['k', '千']:
            return num * 1000
        return num

    # 1. 优先：带单位的数字 (如 3万, 3w, 5k)，避免“万”被关键词分支提前吃掉
    unit_nums = []
    for unit_match in re.finditer(r'(?<![a-zA-Z0-9])(\d+(?:\.\d+)?)\s*([wWkK万千])', text):
        num = apply_amount_unit(float(unit_match.group(1)), unit_match.group(2))
        if num < 100000000:
            unit_nums.append(num)
    if unit_nums:
        return True, max(unit_nums)

    # 2. 次优：[关键词] + [任意非数字间隔] + [数字] + [可选单位]
    # 使用 [^0-9\n]{0,20} 允许中间有最多20个非数字字符
    kv_match = re.search(r'(?:金额|额度|存|款|U)[^0-9\n]{0,20}(\d+(?:\.\d+)?)\s*([wWkK万千])?', text)
    if kv_match:
        num = float(kv_match.group(1))
        num = apply_amount_unit(num, kv_match.group(2))
        
        # 再次校验：过滤掉大于1亿的数字（可能是订单号）
        if num < 100000000:
            return True, num

    # 3. 兜底：提取文中所有纯数字，但必须排除长得像 ID/时间/订单号 的
    simple_nums = re.findall(r'\d+(?:\.\d+)?', text)
    valid_nums = []
    
    for n_str in simple_nums:
        # 过滤逻辑：
        # 1. 如果长度超过 9 位 (例如 2026020616...) -> 视为订单号/时间戳，丢弃
        clean_n = n_str.replace('.', '')
        if len(clean_n) > 9: continue 
        
        val = float(n_str)
        
        # 2. 如果数值大于 1亿 -> 视为异常，丢弃
        if val > 100000000: continue
        
        valid_nums.append(val)

    if valid_nums:
        # 在剩下的合理数字中取最大值（通常金额是最大的合理数字）
        return True, max(valid_nums)

    return False, 0.0

class MessageEventView:
    def __init__(self, message):
        self.message = message
        self.id = getattr(message, "id", None)
        self.chat_id = getattr(message, "chat_id", None)
        self.sender_id = getattr(message, "sender_id", None)
        self.out = bool(getattr(message, "out", False))
        self.text = getattr(message, "text", None) or getattr(message, "raw_text", "") or ""
        self.raw_text = self.text
        self.is_reply = bool(get_message_reply_target_ids(message))

async def collect_approval_candidate_messages(client, replied_msg):
    candidates = []
    seen = set()

    def add_candidate(msg):
        msg_id = getattr(msg, "id", None)
        if not msg or not msg_id or msg_id in seen:
            return
        seen.add(msg_id)
        candidates.append(msg)

    add_candidate(replied_msg)
    chat_id = getattr(replied_msg, "chat_id", None)
    if not chat_id:
        return candidates

    for target_id in get_ordered_message_reply_target_ids(replied_msg):
        if target_id in seen:
            continue
        try:
            parent = await client.get_messages(chat_id, ids=target_id)
            add_candidate(parent)
        except Exception as e:
            logger.warning(f"⚠️ [Approval] 无法追溯被引用消息 Msg={target_id}: {e}")
    return candidates

def approval_amount_gate_passes(rule, message):
    amount_steps = [s for s in rule.get("replies", []) if isinstance(s, dict) and s.get("type") == "amount_logic"]
    if not amount_steps:
        return True

    text = getattr(message, "text", None) or getattr(message, "raw_text", "") or ""
    found, amt = parse_smart_amount(text)
    if not found:
        return False

    for step in amount_steps:
        try:
            thresh = float(str(step.get("text", "")).split("|", 1)[0])
        except Exception:
            continue
        if amt >= thresh:
            return True
    return False

# --- 默认配置 ---
DEFAULT_CONFIG = {
    "enabled": True, 
    "extra_enabled": True, # v71: 副账号总开关
    "approval_keywords": ["同意", "批准", "ok"],
    "schedule": {
        "active": False,
        "start": "09:00",
        "end": "21:00"
    },
    "scheduled_messages": [],
    "rules": [
        {
            "id": "default_rule",
            "name": "默认规则",
            "enabled": True,
            "groups": [],
            "check_file": False,
            "keywords": [],
            "enable_approval": False,
            "reply_account": "", 
            "file_extensions": [],
            "filename_keywords": [],
            "sender_mode": "exclude",
            "sender_prefixes": [],
            "cooldown": 60,
            "replies": [{"type": "text", "text": "收到", "min": 1, "max": 2}],
            "approval_action": {}
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
scheduled_message_runs = {}
VALID_REPLY_TYPES = {"text", "edit_prev", "forward", "copy_file", "amount_logic", "preempt_check", "notify_user"}

def ensure_scheduled_message_id(item):
    if not item.get("id"):
        item["id"] = f"schedule_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    return item["id"]

def merge_scheduled_message_runtime_fields(items):
    existing = {
        str(item.get("id")): item
        for item in current_config.get("scheduled_messages", [])
        if isinstance(item, dict) and item.get("id")
    }
    for item in items:
        old = existing.get(str(item.get("id")))
        if not old:
            continue
        if old.get("last_sent_date") and not item.get("last_sent_date"):
            item["last_sent_date"] = old.get("last_sent_date", "")
        if old.get("last_sent_at") and not item.get("last_sent_at"):
            item["last_sent_at"] = old.get("last_sent_at", "")
    return items

def normalize_scheduled_messages(raw_items):
    if not isinstance(raw_items, list):
        return []
    clean_items = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        ensure_scheduled_message_id(item)
        text = str(item.get("text", "") or "").strip()
        targets = clean_group_ids(item.get("groups", []))
        if not text and not targets and not item.get("enabled", False):
            continue
        frequency = str(item.get("frequency", "daily") or "daily").strip().lower()
        if frequency not in ("daily", "once"):
            frequency = "daily"
        send_time = str(item.get("time", "09:00") or "09:00").strip()
        if not re.fullmatch(r"\d{2}:\d{2}", send_time):
            send_time = "09:00"
        account = str(item.get("account", "") or "").strip()
        clean_items.append({
            "id": str(item["id"]),
            "name": str(item.get("name", "") or "").strip(),
            "enabled": bool(item.get("enabled", False)),
            "groups": targets,
            "text": text,
            "time": send_time,
            "frequency": frequency,
            "account": account,
            "last_sent_date": str(item.get("last_sent_date", "") or "").strip(),
            "last_sent_at": str(item.get("last_sent_at", "") or "").strip(),
        })
    return clean_items

# [v78] 全局 Redis 初始化函数 (Main.py 可见)
def init_redis_connection():
    global redis_client
    redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_URI") or os.environ.get("REDIS_PUBLIC_URL")
    
    if redis and redis_url:
        try:
            redis_url = redis_url.strip()
            safe_url = re.sub(r':([^@]+)@', ':****@', redis_url)
            logger.info(f"🔗 [Monitor] 尝试连接 Redis: {safe_url}")
            redis_client = redis.from_url(redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
            redis_client.ping()
            logger.info("✅ [Monitor] Redis 数据库连接成功!")
        except Exception as e:
            logger.error(f"❌ [Monitor] Redis 连接失败 (将使用本地模式): {e}")
            redis_client = None
    else:
        logger.warning("⚠️ [Monitor] 未检测到 REDIS_URL，将仅使用本地文件存储")

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

    if "enabled" not in current_config:
        current_config["enabled"] = True
    if "extra_enabled" not in current_config:
        current_config["extra_enabled"] = True 

    if "approval_keywords" not in current_config:
        current_config["approval_keywords"] = ["同意", "批准", "ok"]
    else:
        current_config["approval_keywords"] = split_config_items(current_config.get("approval_keywords", []), split_commas=True)
    if not isinstance(current_config.get("schedule"), dict):
        current_config["schedule"] = DEFAULT_CONFIG["schedule"]
    current_config["scheduled_messages"] = normalize_scheduled_messages(current_config.get("scheduled_messages", []))
    if not isinstance(current_config.get("rules"), list):
        current_config["rules"] = []

    clean_rules = []
    for rule in current_config["rules"]:
        if not isinstance(rule, dict):
            continue
        ensure_rule_id(rule)
        if "enabled" not in rule: rule["enabled"] = True
        if "check_file" not in rule: rule["check_file"] = False
        if "enable_approval" not in rule: rule["enable_approval"] = False
        if "reply_account" not in rule: rule["reply_account"] = "" 
        if "groups" not in rule: rule["groups"] = []
        if "keywords" not in rule: rule["keywords"] = []
        if "file_extensions" not in rule: rule["file_extensions"] = []
        if "filename_keywords" not in rule: rule["filename_keywords"] = []
        if "sender_mode" not in rule: rule["sender_mode"] = "exclude"
        if "sender_prefixes" not in rule: rule["sender_prefixes"] = []
        if "cooldown" not in rule: rule["cooldown"] = 60
        if not isinstance(rule.get("replies"), list): rule["replies"] = []
        if not isinstance(rule.get("approval_action"), dict): rule["approval_action"] = {}

        rule["groups"] = clean_group_ids(rule.get("groups", []))
        rule["keywords"] = split_config_items(rule.get("keywords", []))
        rule["file_extensions"] = [x.lower().replace('.', '') for x in split_config_items(rule.get("file_extensions", []), split_commas=True)]
        rule["filename_keywords"] = split_config_items(rule.get("filename_keywords", []), split_commas=True)
        rule["sender_prefixes"] = split_config_items(rule.get("sender_prefixes", []), split_commas=True)
        if rule.get("sender_mode") not in ("exclude", "include"):
            rule["sender_mode"] = "exclude"
        try:
            rule["cooldown"] = int(rule.get("cooldown", 60))
        except Exception:
            rule["cooldown"] = 60
        
        aa = rule["approval_action"]
        if "reply_admin" not in aa: aa["reply_admin"] = ""
        if "reply_origin" not in aa: aa["reply_origin"] = ""
        if "forward_to" not in aa: aa["forward_to"] = ""
        for i in range(1, 4):
            if f"delay_{i}_min" not in aa: aa[f"delay_{i}_min"] = 1.0
            if f"delay_{i}_max" not in aa: aa[f"delay_{i}_max"] = 2.0

        clean_replies = []
        for r in rule.get("replies", []):
            if not isinstance(r, dict):
                continue
            try: r["min"] = float(r.get("min", 1.0))
            except Exception: r["min"] = 1.0
            try: r["max"] = float(r.get("max", 3.0))
            except Exception: r["max"] = 3.0
            if "type" not in r: r["type"] = "text"
            if r.get("type") not in VALID_REPLY_TYPES: r["type"] = "text"
            r["text"] = str(r.get("text", "") or "")
            r["forward_to"] = str(r.get("forward_to", "") or "").strip()
            if r.get("type") == "amount_logic":
                amount_delay_defaults = (
                    ("high_reply_min", r.get("min", 1.0)),
                    ("high_reply_max", r.get("max", 3.0)),
                    ("low_first_min", r.get("min", 1.0)),
                    ("low_first_max", r.get("max", 3.0)),
                    ("low_forward_min", 1.5),
                    ("low_forward_max", 3.0),
                    ("low_reply_min", 1.5),
                    ("low_reply_max", 3.0),
                )
                for key, default in amount_delay_defaults:
                    try: r[key] = float(r.get(key, default))
                    except Exception: r[key] = default
            clean_replies.append(r)
        rule["replies"] = clean_replies

        if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
            rule["sender_prefixes"] = list(system_cs_prefixes)
        clean_rules.append(rule)
    current_config["rules"] = clean_rules

def save_config(new_config):
    global current_config
    try:
        if not isinstance(new_config, dict) or "rules" not in new_config:
            return False, "无效的配置格式"
        if not isinstance(new_config.get("rules"), list):
            return False, "rules 必须是数组"

        if not isinstance(new_config.get("schedule"), dict):
            new_config["schedule"] = DEFAULT_CONFIG["schedule"]
        else:
            new_config["schedule"]["active"] = bool(new_config["schedule"].get("active", False))
            new_config["schedule"]["start"] = str(new_config["schedule"].get("start", "09:00"))
            new_config["schedule"]["end"] = str(new_config["schedule"].get("end", "21:00"))

        new_config["scheduled_messages"] = merge_scheduled_message_runtime_fields(
            normalize_scheduled_messages(new_config.get("scheduled_messages", []))
        )

        if "extra_enabled" in new_config:
            current_config["extra_enabled"] = bool(new_config["extra_enabled"])

        raw_app_kws = new_config.get("approval_keywords", [])
        new_config["approval_keywords"] = split_config_items(raw_app_kws, split_commas=True)
        
        clean_rules = []
        for rule in new_config.get("rules", []):
            if not isinstance(rule, dict):
                continue
            ensure_rule_id(rule)
            rule["enabled"] = bool(rule.get("enabled", True))
            rule["reply_account"] = str(rule.get("reply_account", "")).strip() 
            
            raw_groups = rule.get("groups", [])
            rule["groups"] = clean_group_ids(raw_groups)
            
            rule["check_file"] = bool(rule.get("check_file", False))
            rule["enable_approval"] = bool(rule.get("enable_approval", False))
            if rule.get("sender_mode") not in ("exclude", "include"):
                rule["sender_mode"] = "exclude"

            clean_kws = []
            raw_kws = rule.get("keywords", [])
            if isinstance(raw_kws, str): raw_kws = raw_kws.split('\n')
            for k in raw_kws:
                k = str(k).strip()
                if k: clean_kws.append(k)
            rule["keywords"] = clean_kws

            raw_exts = rule.get("file_extensions", [])
            rule["file_extensions"] = [x.lower().replace('.', '') for x in split_config_items(raw_exts, split_commas=True)]

            raw_fn_kws = rule.get("filename_keywords", [])
            rule["filename_keywords"] = split_config_items(raw_fn_kws, split_commas=True)
            
            if not isinstance(rule.get("approval_action"), dict): rule["approval_action"] = {}
            aa = rule["approval_action"]
            aa["reply_admin"] = str(aa.get("reply_admin", "")).strip()
            aa["reply_origin"] = str(aa.get("reply_origin", "")).strip()
            aa["forward_to"] = str(aa.get("forward_to", "")).strip()
            
            for i in range(1, 4):
                try: aa[f"delay_{i}_min"] = float(aa.get(f"delay_{i}_min", 1.0))
                except: aa[f"delay_{i}_min"] = 1.0
                try: aa[f"delay_{i}_max"] = float(aa.get(f"delay_{i}_max", 2.0))
                except: aa[f"delay_{i}_max"] = 2.0
            
            raw_prefixes = rule.get("sender_prefixes", [])
            rule["sender_prefixes"] = split_config_items(raw_prefixes, split_commas=True)
            
            try: rule["cooldown"] = int(rule.get("cooldown", 60))
            except: rule["cooldown"] = 60
            clean_replies = []
            raw_replies = rule.get("replies", [])
            if not isinstance(raw_replies, list):
                raw_replies = []
            for r in raw_replies:
                if not isinstance(r, dict):
                    continue
                try: r["min"] = float(r.get("min", 1.0))
                except: r["min"] = 1.0
                try: r["max"] = float(r.get("max", 3.0))
                except: r["max"] = 3.0
                if "type" not in r: r["type"] = "text"
                if r.get("type") not in VALID_REPLY_TYPES: r["type"] = "text"
                r["text"] = str(r.get("text", "") or "")
                r["forward_to"] = str(r.get("forward_to", "") or "").strip()
                if r.get("type") == "amount_logic":
                    amount_delay_defaults = (
                        ("high_reply_min", r.get("min", 1.0)),
                        ("high_reply_max", r.get("max", 3.0)),
                        ("low_first_min", r.get("min", 1.0)),
                        ("low_first_max", r.get("max", 3.0)),
                        ("low_forward_min", 1.5),
                        ("low_forward_max", 3.0),
                        ("low_reply_min", 1.5),
                        ("low_reply_max", 3.0),
                    )
                    for key, default in amount_delay_defaults:
                        try: r[key] = float(r.get(key, default))
                        except Exception: r[key] = default
                clean_replies.append(r)
            rule["replies"] = clean_replies
            clean_rules.append(rule)

        new_config["rules"] = clean_rules
        
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

def save_monitor_enabled(enabled):
    global current_config
    try:
        current_config["enabled"] = bool(enabled)
        if redis_client:
            try:
                redis_client.set(REDIS_KEY, json.dumps(current_config, ensure_ascii=False))
            except Exception as e:
                logger.error(f"❌ [Monitor] Redis 保存监听状态失败: {e}")
                return False, str(e)

        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, indent=4, ensure_ascii=False)

        logger.info(f"💾 [Monitor] 监听状态已更新: {'开启' if current_config['enabled'] else '暂停'}")
        return True, ""
    except Exception as e:
        logger.error(f"❌ [Monitor] 监听状态保存失败: {e}")
        return False, str(e)

def persist_current_config():
    try:
        if redis_client:
            redis_client.set(REDIS_KEY, json.dumps(current_config, ensure_ascii=False))
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(current_config, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error(f"❌ [Monitor] 配置持久化失败: {e}")
        return False

# --- Web UI (Bento Grid + Global CDN + Multi-Account Selector) ---
SETTINGS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN" class="bg-[#F3F4F6]">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Monitor Pro v78</title>
    <script>
        tailwind.config = { theme: { extend: { fontFamily: { sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"Microsoft YaHei"', 'sans-serif'], mono: ['ui-monospace', 'Menlo', 'monospace'], }, colors: { primary: '#6366F1', slate: { 50:'#f9fafb', 100:'#f3f4f6', 200:'#e5e7eb', 800:'#1f2937' } } } } }
    </script>
    <script src="https://unpkg.com/vue@3.3.4/dist/vue.global.prod.js"></script>
    <script src="https://cdn.tailwindcss.com"></script>
    <link href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" rel="stylesheet">
    <style>
        * { box-sizing: border-box; }
        html { background: #F3F4F6; font-size: 14px; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 12px; line-height: 1.45; background: #F3F4F6; color: #1F2937; }
        button, input, select, textarea { max-width: 100%; }
        button, label, select, input[type="checkbox"], input[type="file"] { cursor: pointer; }
        button:disabled { cursor: not-allowed; opacity: .65; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 2px; }
        ::-webkit-scrollbar-thumb:hover { background: #94A3B8; }
        textarea, input, select { font-family: ui-monospace, Menlo, monospace; font-size: 11px; letter-spacing: -0.01em; }
        .bento-card { background: white; border: 1px solid #E5E7EB; border-radius: 8px; box-shadow: 0 1px 2px rgba(0,0,0,0.05); transition: all 0.2s ease; }
        .bento-card:hover { border-color: #D1D5DB; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }
        .bento-input { background-color: #F9FAFB; border: 1px solid #E5E7EB; border-radius: 6px; color: #374151; transition: all 0.15s; }
        .bento-input:focus { background-color: white; border-color: #6366F1; ring: 2px solid rgba(99, 102, 241, 0.1); outline: none; }
        .section-label { font-size: 10px; font-weight: 700; color: #6B7280; text-transform: uppercase; letter-spacing: 0.05em; }
        .recovery-panel { background: linear-gradient(135deg, #FFF1F2 0%, #FFF 100%); border: 1px solid #FECDD3; }
        .approval-bg { background-color: #EFF6FF; border-top: 1px solid #DBEAFE; }
        .approval-panel { border: 1px solid #BFDBFE; background: #EFF6FF; border-radius: 8px; padding: 8px; }
        .approval-panel.is-off { border-color: #E5E7EB; background: #F9FAFB; }
        .approval-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
        .approval-delay { display: grid; grid-template-columns: minmax(0, 1fr) 12px minmax(0, 1fr); gap: 4px; align-items: center; }
        .approval-delay input { text-align: center; }
        .flow-step { display: flex; gap: 8px; align-items: stretch; }
        .delay-box { width: 56px; flex: 0 0 56px; border: 1px solid #E5E7EB; border-radius: 8px; background: #FAFAFA; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 3px; color: #9CA3AF; }
        .delay-box input { width: 38px; height: 20px; text-align: center; background: transparent; border: 0; padding: 0; color: #6B7280; font-weight: 700; }
        .step-panel { flex: 1; border: 1px solid #E5E7EB; border-radius: 8px; background: #FFFFFF; padding: 8px; transition: border-color .15s, box-shadow .15s; }
        .step-panel:hover { border-color: #C7D2FE; box-shadow: 0 4px 12px rgba(99,102,241,.08); }
        .visual-label { font-size: 9px; font-weight: 800; color: #9CA3AF; text-transform: uppercase; letter-spacing: .04em; margin-bottom: 3px; display: flex; align-items: center; gap: 4px; }
        .visual-field { display: flex; flex-direction: column; gap: 3px; min-width: 0; }
        .step-type { border: 0; background: transparent; color: #4B5563; font-weight: 800; height: 22px; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }
        .step-help { border: 1px solid #FDE68A; background: #FFFBEB; color: #92400E; border-radius: 6px; padding: 6px 8px; font-size: 10px; font-weight: 600; line-height: 1.4; }
        #app > nav { min-height: 48px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 16px; background: rgba(255,255,255,.94); border-bottom: 1px solid #E5E7EB; position: sticky; top: 0; z-index: 50; backdrop-filter: blur(10px); }
        #app > nav > div { min-width: 0; }
        main { width: 100%; max-width: 1400px; margin: 0 auto; padding: 24px 16px 80px; }
        main > * + * { margin-top: 24px; }
        main > .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; align-items: start; }
        .bento-card { min-width: 0; }
        .bento-card input, .bento-card select, .bento-card textarea { min-width: 0; }
        .fixed.bottom-4.right-4 { position: fixed; right: 16px; bottom: 16px; }
        .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border-width: 0; }
        .hidden { display: none !important; }
        .flex { display: flex; }
        .inline-flex { display: inline-flex; }
        .grid { display: grid; }
        .items-center { align-items: center; }
        .items-end { align-items: flex-end; }
        .items-stretch { align-items: stretch; }
        .justify-between { justify-content: space-between; }
        .justify-center { justify-content: center; }
        .justify-end { justify-content: flex-end; }
        .flex-col { flex-direction: column; }
        .flex-1 { flex: 1 1 0%; }
        .shrink-0 { flex-shrink: 0; }
        .relative { position: relative; }
        .absolute { position: absolute; }
        .w-full { width: 100%; }
        .min-h-screen { min-height: 100vh; }
        .overflow-hidden { overflow: hidden; }
        .resize-none { resize: none; }
        .truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
        .whitespace-nowrap { white-space: nowrap; }
        .text-center { text-align: center; }
        .font-bold { font-weight: 700; }
        .font-semibold { font-weight: 600; }
        .font-medium { font-weight: 500; }
        .font-mono { font-family: ui-monospace, Menlo, monospace; }
        .font-sans { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Microsoft YaHei", sans-serif; }
        .pointer-events-none { pointer-events: none; }
        .cursor-pointer { cursor: pointer; }
        .select-none { user-select: none; }
        .rounded, .rounded-lg { border-radius: 8px; }
        .rounded-full { border-radius: 9999px; }
        .border { border: 1px solid #E5E7EB; }
        .border-dashed { border-style: dashed; }
        .bg-white { background: #FFFFFF; }
        .bg-slate-50 { background: #F9FAFB; }
        .bg-slate-100 { background: #F3F4F6; }
        .text-slate-800 { color: #1F2937; }
        .text-slate-700 { color: #374151; }
        .text-slate-600 { color: #4B5563; }
        .text-slate-500 { color: #6B7280; }
        .text-slate-400 { color: #94A3B8; }
        .text-primary { color: #6366F1; }
        .bg-primary { background: #6366F1; }
        .text-white { color: #FFFFFF; }
        .shadow-sm { box-shadow: 0 1px 2px rgba(0,0,0,.05); }
        .transition-colors, .transition-all { transition-duration: .15s; transition-property: color, background-color, border-color, opacity, transform, box-shadow; }
        .space-y-1\.5 > * + * { margin-top: 6px; }
        .space-y-2 > * + * { margin-top: 8px; }
        .space-y-6 > * + * { margin-top: 24px; }
        .gap-1 { gap: 4px; }
        .gap-1\.5 { gap: 6px; }
        .gap-2 { gap: 8px; }
        .gap-3 { gap: 12px; }
        .gap-4 { gap: 16px; }
        .grid-cols-1 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
        .grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .grid-cols-5 { grid-template-columns: repeat(5, minmax(0, 1fr)); }
        .col-span-2 { grid-column: span 2 / span 2; }
        .col-span-3 { grid-column: span 3 / span 3; }
        .col-span-5 { grid-column: span 5 / span 5; }
        @media (min-width: 768px) {
            .md\:flex { display: flex !important; }
            .md\:hidden { display: none !important; }
            .md\:flex-row { flex-direction: row; }
            .md\:w-auto { width: auto; }
            .md\:w-20 { width: 5rem; }
            .md\:w-24 { width: 6rem; }
            .md\:w-32 { width: 8rem; }
            .md\:w-48 { width: 12rem; }
            .md\:grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (min-width: 1280px) {
            main > .xl\:grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
        }
        @media (max-width: 767px) {
            #app > nav { align-items: stretch; flex-wrap: wrap; padding: 10px 12px; }
            #app > nav > div:first-child { flex: 1 1 auto; }
            #app > nav > div:last-child { margin-left: auto; }
            main { padding: 16px 12px 72px; }
            main > .grid { grid-template-columns: minmax(0, 1fr); }
            .flow-step { gap: 6px; }
            .delay-box { width: 48px; flex-basis: 48px; }
            .recovery-panel { align-items: stretch; }
        }
        [v-cloak] { display: none !important; }
    </style>
</head>
<body class="text-slate-800 antialiased min-h-screen pb-20 font-sans">
<div id="app" v-cloak>
    <nav class="bg-white border-b border-slate-200 sticky top-0 z-50 h-12 flex items-center px-4 justify-between bg-opacity-90 backdrop-blur-sm">
        <div class="flex items-center gap-2">
            <div class="w-6 h-6 bg-primary text-white rounded flex items-center justify-center text-xs"><i class="fa-solid fa-bolt"></i></div>
            <span class="font-bold text-sm tracking-tight text-slate-900">Monitor <span class="text-xs text-primary font-medium bg-primary/10 px-1.5 py-0.5 rounded">Pro v78</span></span>
        </div>
        
        <div class="flex items-center gap-3">
            <div class="hidden md:flex items-center gap-1.5 px-2 py-1 bg-slate-50 rounded border border-slate-200">
                <span class="text-[10px] font-bold text-slate-500 uppercase">分身模式:</span>
                <span class="text-[10px] font-bold" :class="config.extra_enabled ? 'text-green-500' : 'text-slate-400'">{{ config.extra_enabled ? '启用' : '停用' }}</span>
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
            <div v-for="(rule, index) in config.rules" :key="index" 
                 class="bento-card flex flex-col overflow-hidden relative group transition-all duration-300"
                 :class="{'opacity-60 grayscale': (!rule.enabled) || (rule.reply_account && rule.reply_account !== '' && !config.extra_enabled)}">
                
                <div v-if="rule.enabled && rule.reply_account && rule.reply_account !== '' && !config.extra_enabled" 
                     class="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-slate-800 text-white px-3 py-1 rounded shadow-lg z-20 text-xs font-bold pointer-events-none whitespace-nowrap">
                    已停用
                </div>

                <div class="px-3 py-2 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                    <div class="flex items-center gap-2 flex-1">
                        <span class="text-slate-400 text-[10px] font-mono">#{{index+1}}</span>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-700 focus:ring-0 placeholder-slate-300 w-full font-sans" placeholder="未命名规则">
                    </div>
                    
                    <label class="relative inline-flex items-center cursor-pointer mr-2" 
                           :title="(rule.reply_account && !config.extra_enabled) ? '分身模式已关闭，此开关被强制锁定' : '切换规则状态'">
                        <input type="checkbox" 
                               :checked="rule.enabled && (!rule.reply_account || rule.reply_account === '' || config.extra_enabled)" 
                               @change="if(!rule.reply_account || rule.reply_account === '' || config.extra_enabled) { rule.enabled = $event.target.checked; saveConfig(); }"
                               :disabled="!!rule.reply_account && rule.reply_account !== '' && !config.extra_enabled"
                               class="sr-only peer">
                        <div class="w-7 h-4 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-green-500"></div>
                    </label>

                    <button @click="removeRule(index)" class="text-slate-300 hover:text-red-500 transition-colors px-1" title="删除"><i class="fa-solid fa-trash text-[10px]"></i></button>
                </div>
                <div class="p-3 flex flex-col gap-3" :class="{'pointer-events-none': !rule.enabled || (rule.reply_account && rule.reply_account !== '' && !config.extra_enabled)}">
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label"><i class="fa-solid fa-eye mr-1"></i>监听来源</span><label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="rule.check_file" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0"><span class="text-[10px] text-slate-500 font-medium" :class="{'text-primary': rule.check_file}">文件模式</span></label></div>
                        <div class="relative"><textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" rows="3" class="bento-input w-full px-2 py-1.5 resize-y min-h-16 leading-tight font-mono text-[11px]" placeholder="群ID (换行分隔)"></textarea></div>
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
                            <div class="col-span-2"><select v-model="rule.sender_mode" class="bento-input w-full px-1 py-0 h-7 text-[10px] font-sans font-medium"><option value="exclude">排除前缀</option><option value="include">仅允许</option></select></div>
                            <div class="col-span-3"><input :value="listToString(rule.sender_prefixes).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'sender_prefixes')" class="bento-input w-full px-2 py-1.5 h-7 truncate font-mono text-[11px]" placeholder="前缀: YY, AA"></div>
                            <div class="col-span-5 relative flex items-center gap-2 mt-0.5"><span class="text-[10px] text-slate-400 font-medium">冷却CD:</span><input type="number" v-model.number="rule.cooldown" class="bento-input w-16 px-1 py-0 h-6 text-center text-[10px] font-mono font-bold"><span class="text-[10px] text-slate-400 font-medium">秒</span></div>
                        </div>
                    </div>
                    <div class="h-px bg-slate-100"></div>
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label text-primary"><i class="fa-solid fa-bolt mr-1"></i>执行动作流</span><button @click="addStep(rule)" class="text-[10px] text-primary hover:bg-primary/5 px-1.5 py-0.5 rounded transition-colors border border-transparent hover:border-primary/10 font-bold"><i class="fa-solid fa-plus mr-1"></i>添加步骤</button></div>
                        <div class="flex items-center gap-2 mb-2 bg-indigo-50 border border-indigo-100 p-1.5 rounded">
                            <span class="text-[9px] font-bold text-indigo-500 uppercase"><i class="fa-solid fa-user-tag mr-1"></i>选择回复账号:</span>
                            <select v-model="rule.reply_account" class="flex-1 text-[10px] bg-transparent border-none p-0 text-indigo-700 font-bold focus:ring-0 cursor-pointer h-4">
                                <option value="">主账号 (默认)</option>
                                <option v-for="acc in available_accounts" :value="acc">{{ acc }}</option>
                            </select>
                        </div>
                        <div class="approval-panel" :class="{'is-off': !rule.enable_approval}">
                            <div class="flex items-center justify-between gap-2">
                                <span class="text-[10px] font-bold text-blue-700 uppercase"><i class="fa-solid fa-stamp mr-1"></i>同意审批检测</span>
                                <label class="relative inline-flex items-center cursor-pointer" title="监听别人引用报备并发送全局审批触发词">
                                    <input type="checkbox" v-model="rule.enable_approval" @change="ensureApprovalAction(rule)" class="sr-only peer">
                                    <div class="w-7 h-4 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-blue-500"></div>
                                </label>
                            </div>
                            <div v-if="rule.enable_approval && rule.approval_action" class="mt-2 space-y-2">
                                <div class="text-[10px] leading-5 text-blue-700 bg-white border border-blue-100 rounded px-2 py-1">
                                    审批后流程：领导引用同意 → 引用回复领导 → 转发原始报备 → 引用原始报备回复
                                </div>
                                <div class="grid grid-cols-1 gap-2">
                                    <div class="visual-field">
                                        <div class="visual-label"><i class="fa-solid fa-reply"></i>同意后回复领导引用消息</div>
                                        <textarea v-model="rule.approval_action.reply_admin" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-white border-blue-100" placeholder="请稍等ART"></textarea>
                                    </div>
                                    <div class="approval-grid">
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-solid fa-share"></i>同意后转发原始报备到群</div>
                                            <input v-model="rule.approval_action.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-600 bg-white" placeholder="-1001234567890">
                                        </div>
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-solid fa-reply-all"></i>同意后回复原始报备</div>
                                            <textarea v-model="rule.approval_action.reply_origin" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-white border-blue-100" placeholder="请稍等ART"></textarea>
                                        </div>
                                    </div>
                                    <div class="approval-grid">
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-regular fa-clock"></i>先回领导延迟</div>
                                            <div class="approval-delay"><input type="number" v-model.number="rule.approval_action.delay_1_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="rule.approval_action.delay_1_max" class="bento-input h-7 px-1"></div>
                                        </div>
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-regular fa-clock"></i>转发延迟</div>
                                            <div class="approval-delay"><input type="number" v-model.number="rule.approval_action.delay_2_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="rule.approval_action.delay_2_max" class="bento-input h-7 px-1"></div>
                                        </div>
                                        <div class="visual-field col-span-2">
                                            <div class="visual-label"><i class="fa-regular fa-clock"></i>回原始报备延迟</div>
                                            <div class="approval-delay"><input type="number" v-model.number="rule.approval_action.delay_3_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="rule.approval_action.delay_3_max" class="bento-input h-7 px-1"></div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div v-if="rule.replies.length === 0" class="text-center py-2 text-[10px] text-slate-300 border border-dashed border-slate-200 rounded font-medium">无动作</div>
                        <div class="space-y-2">
                            <div v-for="(reply, rIndex) in rule.replies" :key="rIndex" class="flow-step group/item">
                                <div v-if="reply.type !== 'amount_logic'" class="delay-box">
                                    <input v-model.number="reply.min" placeholder="1">
                                    <div class="w-4 h-px bg-slate-200"></div>
                                    <input v-model.number="reply.max" placeholder="3">
                                    <span class="text-[9px] font-bold">秒</span>
                                </div>
                                <div v-else class="delay-box">
                                    <span class="text-[9px] font-bold text-indigo-500">金额</span>
                                    <span class="text-[9px] text-slate-400 text-center leading-4">分支<br>延迟</span>
                                </div>
                                <div class="step-panel">
                                    <div class="flex items-center gap-2 mb-2">
                                        <span class="text-[10px] text-slate-400 font-mono">#{{rIndex + 1}}</span>
                                        <select v-model="reply.type" @change="normalizeStep(reply)" class="step-type">
                                            <option value="text">发送文本</option>
                                            <option value="edit_prev">编辑上一条</option>
                                            <option value="forward">直接转发</option>
                                            <option value="copy_file">转发+新文案</option>
                                            <option value="amount_logic">金额分流</option>
                                            <option value="preempt_check">抢答检测（自删）</option>
                                            <option value="notify_user">Bot私聊通知</option>
                                        </select>
                                        <button @click="rule.replies.splice(rIndex, 1)" class="ml-auto text-slate-300 hover:text-red-400 w-6 h-6 flex items-center justify-center rounded hover:bg-red-50" title="删除步骤"><i class="fa-solid fa-xmark text-[10px]"></i></button>
                                    </div>

                                    <template v-if="reply.type === 'text'">
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-solid fa-message"></i>发送内容</div>
                                            <textarea v-model="reply.text" rows="3" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-white" placeholder="输入要回复到原群的文字"></textarea>
                                        </div>
                                    </template>

                                    <template v-if="reply.type === 'edit_prev'">
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-solid fa-pen-to-square"></i>编辑为</div>
                                            <textarea v-model="reply.text" rows="3" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-amber-50 border-amber-100 focus:border-amber-300" placeholder="把上一条自动发送的消息编辑成这段内容"></textarea>
                                        </div>
                                    </template>

                                    <template v-if="reply.type === 'forward'">
                                        <div class="visual-field">
                                            <div class="visual-label"><i class="fa-solid fa-share"></i>转发目标群</div>
                                            <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-600" placeholder="-1001234567890">
                                        </div>
                                    </template>

                                    <template v-if="reply.type === 'copy_file'">
                                        <div class="grid grid-cols-1 gap-2">
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-share"></i>转发目标群</div>
                                                <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-600" placeholder="-1001234567890">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-pen"></i>新文案</div>
                                                <textarea v-model="reply.text" rows="3" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-yellow-50 border-yellow-100 focus:border-yellow-300" placeholder="发送文件/图片时附带的文案，支持 {time}"></textarea>
                                            </div>
                                        </div>
                                    </template>

                                    <template v-if="reply.type === 'amount_logic'">
                                        <div class="grid grid-cols-2 gap-2">
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-scale-balanced"></i>金额阈值</div>
                                                <input :value="amountPart(reply, 0)" @input="setAmountPart(reply, 0, $event.target.value)" class="bento-input w-full px-2 py-1.5 h-8 text-[11px]" placeholder="2000">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-regular fa-clock"></i>大额回复延迟</div>
                                                <div class="approval-delay"><input type="number" v-model.number="reply.high_reply_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="reply.high_reply_max" class="bento-input h-7 px-1"></div>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-regular fa-clock"></i>小额首句延迟</div>
                                                <div class="approval-delay"><input type="number" v-model.number="reply.low_first_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="reply.low_first_max" class="bento-input h-7 px-1"></div>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-share"></i>小额转发群</div>
                                                <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-600" placeholder="-1001234567890">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-regular fa-clock"></i>小额转发延迟</div>
                                                <div class="approval-delay"><input type="number" v-model.number="reply.low_forward_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="reply.low_forward_max" class="bento-input h-7 px-1"></div>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-regular fa-clock"></i>小额后续回复延迟</div>
                                                <div class="approval-delay"><input type="number" v-model.number="reply.low_reply_min" class="bento-input h-7 px-1"><span class="text-center text-slate-400">-</span><input type="number" v-model.number="reply.low_reply_max" class="bento-input h-7 px-1"></div>
                                            </div>
                                            <div class="visual-field col-span-2">
                                                <div class="visual-label"><i class="fa-solid fa-arrow-trend-up"></i>大额回复</div>
                                                <textarea :value="amountPart(reply, 1)" @input="setAmountPart(reply, 1, $event.target.value)" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-indigo-50 border-indigo-100" placeholder="金额达到阈值时回复的内容"></textarea>
                                            </div>
                                            <div class="visual-field col-span-2">
                                                <div class="visual-label"><i class="fa-solid fa-arrow-trend-down"></i>小额回复顺序</div>
                                                <textarea :value="amountLowLines(reply)" @input="setAmountLowLines(reply, $event.target.value)" rows="3" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-indigo-50 border-indigo-100" placeholder="第一行先回复；转发后再发送第二行起"></textarea>
                                            </div>
                                        </div>
                                    </template>

                                    <template v-if="reply.type === 'preempt_check'">
                                        <div class="step-help"><i class="fa-solid fa-user-shield mr-1"></i>只检测在本规则第一条回复之前、且引用同一条原始消息的他人回复；若有人更快，会删除本规则已发出的消息。</div>
                                    </template>

                                    <template v-if="reply.type === 'notify_user'">
                                        <div class="grid grid-cols-1 gap-2">
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-user"></i>Bot通知对象</div>
                                                <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-emerald-700" placeholder="用户ID，或机器人可访问的 @username">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-bell"></i>通知内容</div>
                                                <textarea v-model="reply.text" rows="3" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-emerald-50 border-emerald-100 focus:border-emerald-300" placeholder="例如：{rule} 已自动回复 {sender}：{message}"></textarea>
                                            </div>
                                        </div>
                                    </template>
                                </div>
                            </div>
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
                <div class="flex flex-col gap-1 w-full md:w-32"><label class="text-[9px] font-bold text-slate-500 uppercase">附带图片(可选)</label><div class="flex items-center gap-1"><input type="file" accept="image/*" @change="onRecoveryImage" ref="recoveryImgInput" class="hidden"><button type="button" @click="$refs.recoveryImgInput.click()" class="h-8 px-2 bg-blue-50 hover:bg-blue-100 text-blue-600 border border-blue-200 rounded text-[10px] font-bold flex items-center gap-1 whitespace-nowrap"><i class="fa-solid fa-image"></i><span v-if="!recovery.image">选择图片</span><span v-else class="truncate max-w-[60px]">{{ recovery.imageName }}</span></button><button v-if="recovery.image" type="button" @click="clearRecoveryImage" class="h-8 w-8 bg-red-50 hover:bg-red-100 text-red-500 border border-red-200 rounded text-[10px] flex items-center justify-center" title="清除"><i class="fa-solid fa-xmark"></i></button></div></div>
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
            const config = reactive({ enabled: false, extra_enabled: true, approval_keywords: [], schedule: {active: false, start: '09:00', end: '21:00'}, scheduled_messages: [], rules: [] });
            const toast = reactive({ show: false, msg: '', type: 'success' });
            const recovery = reactive({ search: '', reply: '', hours: 5, min: 2, max: 5, image: '', imageName: '' });
            const available_accounts = reactive([]);
            const syncAvailableAccounts = (accounts) => {
                if (!Array.isArray(accounts)) return;
                available_accounts.splice(0, available_accounts.length, ...accounts);
            };
            const defaultApprovalAction = () => ({
                reply_admin: '',
                reply_origin: '',
                forward_to: '',
                delay_1_min: 1,
                delay_1_max: 2,
                delay_2_min: 1,
                delay_2_max: 3,
                delay_3_min: 1,
                delay_3_max: 2
            });
            const hydrateApprovalAction = (action = {}) => {
                const base = defaultApprovalAction();
                const merged = { ...base, ...(action || {}) };
                ['reply_admin', 'reply_origin', 'forward_to'].forEach(key => merged[key] = merged[key] || '');
                for (let i = 1; i <= 3; i++) {
                    merged[`delay_${i}_min`] = Number(merged[`delay_${i}_min`] ?? base[`delay_${i}_min`]);
                    merged[`delay_${i}_max`] = Number(merged[`delay_${i}_max`] ?? base[`delay_${i}_max`]);
                }
                return merged;
            };
            const ensureApprovalAction = (rule) => {
                if (!rule.approval_action) rule.approval_action = defaultApprovalAction();
                else rule.approval_action = hydrateApprovalAction(rule.approval_action);
            };
            const hydrateReply = (rep = {}) => ({
                ...rep,
                type: rep.type || 'text',
                text: rep.text || '',
                forward_to: rep.forward_to || '',
                min: rep.min ?? 1,
                max: rep.max ?? 3,
                high_reply_min: rep.high_reply_min ?? rep.min ?? 1,
                high_reply_max: rep.high_reply_max ?? rep.max ?? 3,
                low_first_min: rep.low_first_min ?? rep.min ?? 1,
                low_first_max: rep.low_first_max ?? rep.max ?? 3,
                low_forward_min: rep.low_forward_min ?? 1.5,
                low_forward_max: rep.low_forward_max ?? 3,
                low_reply_min: rep.low_reply_min ?? 1.5,
                low_reply_max: rep.low_reply_max ?? 3
            });
            const splitAmountConfig = (reply) => {
                const parts = String(reply.text || '').split('|');
                while (parts.length < 3) parts.push('');
                return parts.slice(0, 3);
            };
            const splitReplyLines = (value) => String(value || '').split(/(?:;;|\\\\n|\\r?\\n)+/).map(s => s.trim()).filter(Boolean);
            const amountPart = (reply, index) => splitAmountConfig(reply)[index] || '';
            const setAmountPart = (reply, index, value) => {
                const parts = splitAmountConfig(reply);
                parts[index] = value;
                reply.text = parts.join('|');
            };
            const amountLowLines = (reply) => splitReplyLines(amountPart(reply, 2)).join('\\n');
            const setAmountLowLines = (reply, value) => {
                const lines = splitReplyLines(value);
                setAmountPart(reply, 2, lines.join(';;'));
            };
            const normalizeStep = (reply) => {
                if (!reply.text) reply.text = '';
                if (!reply.forward_to) reply.forward_to = '';
                if (reply.min === undefined || reply.min === null || reply.min === '') reply.min = 1;
                if (reply.max === undefined || reply.max === null || reply.max === '') reply.max = 3;
                if (reply.type === 'amount_logic') {
                    reply.text = splitAmountConfig(reply).join('|');
                    reply.high_reply_min = reply.high_reply_min ?? reply.min ?? 1;
                    reply.high_reply_max = reply.high_reply_max ?? reply.max ?? 3;
                    reply.low_first_min = reply.low_first_min ?? reply.min ?? 1;
                    reply.low_first_max = reply.low_first_max ?? reply.max ?? 3;
                    reply.low_forward_min = reply.low_forward_min ?? 1.5;
                    reply.low_forward_max = reply.low_forward_max ?? 3;
                    reply.low_reply_min = reply.low_reply_min ?? 1.5;
                    reply.low_reply_max = reply.low_reply_max ?? 3;
                }
            };
            const addStep = (rule) => {
                if (!Array.isArray(rule.replies)) rule.replies = [];
                rule.replies.push(hydrateReply({type:'text', text:'', forward_to:'', min:1, max:3}));
            };

            // v75: Independent function for refreshing status (Heartbeat)
            const refreshStatus = () => {
                fetch('/tool/monitor_settings_json')
                    .then(r => r.json())
                    .then(data => { 
                        // Only update switches to avoid UI glitches during typing
                        if(data.enabled !== undefined) config.enabled = data.enabled;
                        if(data.extra_enabled !== undefined) config.extra_enabled = data.extra_enabled;
                        syncAvailableAccounts(data.available_accounts);
                    })
                    .catch(e => console.log('Heartbeat skipped'));
            };

            // Initial full load
            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(data => { 
                    config.enabled = data.enabled; 
                    if(data.extra_enabled !== undefined) config.extra_enabled = data.extra_enabled;
                    syncAvailableAccounts(data.available_accounts);
                    
                    if(data.approval_keywords) config.approval_keywords = data.approval_keywords;
                    else config.approval_keywords = ['同意', '批准', 'ok'];
                    
                    if(data.schedule) config.schedule = data.schedule;
                    else config.schedule = {active: false, start: '09:00', end: '21:00'};
                    config.scheduled_messages = Array.isArray(data.scheduled_messages) ? data.scheduled_messages : [];

                    config.rules = (data.rules || []).map(r => {
                        if(r.replies) r.replies = r.replies.map(rep => hydrateReply(rep));
                        else r.replies = [];
                        if(r.check_file === undefined) r.check_file = false;
                        if(r.enable_approval === undefined) r.enable_approval = false;
                        if(r.enabled === undefined) r.enabled = true;
                        if(r.reply_account === undefined) r.reply_account = '';
                        if(!r.file_extensions) r.file_extensions = [];
                        if(!r.filename_keywords) r.filename_keywords = [];
                        if(!r.sender_prefixes) r.sender_prefixes = [];
                        if(!r.keywords) r.keywords = [];
                        r.approval_action = hydrateApprovalAction(r.approval_action);
                        return r;
                    });
                });

            // Start Heartbeat (every 3 seconds)
            setInterval(refreshStatus, 3000);

            const listToString = (list) => (list || []).join('\\n');
            const stringToList = (e, rule, key) => { 
                let val = e.target.value;
                val = val.replace(/，/g, ',');
                if (val.includes(',')) {
                    rule[key] = val.split(',').map(x=>x.trim()).filter(x=>x);
                } else {
                    rule[key] = val.split(/[\\r\\n]+/).map(x=>x.trim()).filter(x=>x);
                }
            };
            const stringToIntList = (e, rule, key) => { rule[key] = e.target.value.split('\\n').map(x=>x.trim()).filter(x=>x); };

            const addRule = () => {
                config.rules.push({
                    id: 'rule_' + Date.now() + '_' + Math.floor(Math.random() * 10000),
                    name: '新规则 #' + (config.rules.length + 1),
                    enabled: true,
                    groups: [], check_file: false, keywords: [], file_extensions: [], filename_keywords: [],
                    enable_approval: false,
                    approval_action: defaultApprovalAction(),
                    sender_mode: 'exclude', sender_prefixes: [], cooldown: 60,
                    replies: [hydrateReply({type:'text', text: '', min: 1, max: 2})],
                    reply_account: ''
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
            
            const onRecoveryImage = (e) => {
                const file = e.target.files && e.target.files[0];
                if (!file) return;
                if (file.size > 10 * 1024 * 1024) { showToast('图片过大(>10MB)', 'error'); e.target.value=''; return; }
                const reader = new FileReader();
                reader.onload = () => { recovery.image = reader.result; recovery.imageName = file.name; };
                reader.onerror = () => showToast('图片读取失败', 'error');
                reader.readAsDataURL(file);
            };

            const clearRecoveryImage = () => { recovery.image = ''; recovery.imageName = ''; };

            const runRecovery = async () => {
                const min = recovery.min || 1;
                const max = recovery.max || 3;
                const imgInfo = recovery.image ? `\\n图片: ${recovery.imageName}` : '';
                if(!confirm(`⚠️ 确定要执行批量回复吗？\\n\\n范围: 过去 ${recovery.hours} 小时\\n目标: 我发送的 "${recovery.search}" \\n动作: 追溯回复给【原消息发送者】${imgInfo}\\n间隔: ${min}-${max} 秒`)) return;
                try {
                    const res = await fetch('/api/batch_recovery', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(recovery) });
                    const json = await res.json();
                    if (json.success) showToast(json.msg, 'success');
                    else showToast('执行失败: ' + json.msg, 'error');
                } catch(e) { showToast('网络请求错误', 'error'); }
            };

            const showToast = (msg, type) => { toast.msg = msg; toast.type = type; toast.show = true; setTimeout(() => toast.show = false, 3000); };

            return { config, toast, recovery, available_accounts, listToString, stringToList, stringToIntList, addRule, addStep, removeRule, saveConfig, runRecovery, onRecoveryImage, clearRecoveryImage, normalizeStep, ensureApprovalAction, amountPart, setAmountPart, amountLowLines, setAmountLowLines };
        }
    }).mount('#app');
</script>
</body>
</html>
"""
OTP_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>验证码监控</title>
    <style>
        *{box-sizing:border-box;margin:0;padding:0}
        :root{--bg:#f9fafb;--card:#fff;--border:#e5e7eb;--text:#111827;--muted:#6b7280}
        body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text);min-height:100vh;padding:20px 16px}
        .topbar{display:flex;align-items:center;justify-content:space-between;max-width:1200px;margin:0 auto 24px}
        .topbar h1{font-size:17px;font-weight:700;display:flex;align-items:center;gap:8px}
        .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block;animation:pulse 2s infinite}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
        .nav{display:flex;gap:6px}
        .nav a{font-size:12px;color:var(--muted);text-decoration:none;padding:4px 10px;border:1px solid var(--border);border-radius:6px}
        .nav a:hover{color:var(--text);border-color:#d1d5db}
        .sec-title{font-size:11px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1px;max-width:1200px;margin:0 auto 10px}
        .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:14px;max-width:1200px;margin:0 auto 28px}
        .card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;cursor:pointer;transition:box-shadow .15s,transform .15s;user-select:none}
        .card:hover{box-shadow:0 4px 12px rgba(0,0,0,.06);transform:translateY(-1px)}
        .card:active{transform:scale(.98)}
        .card-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
        .acc{font-size:13px;font-weight:700}
        .badge{font-size:10px;font-weight:600;padding:2px 7px;border-radius:5px}
        .btg{background:#e0f2fe;color:#0284c7}
        .bga{background:#fff1f2;color:#e11d48}
        .code{font-family:ui-monospace,Menlo,monospace;font-size:28px;font-weight:700;letter-spacing:5px;text-align:center;padding:12px;border-radius:8px;margin-bottom:8px}
        .ctg{background:#f0f9ff;color:#0369a1}
        .cga{background:#fff5f5;color:#be123c;margin:0}
        .empty-code{font-size:13px;letter-spacing:0;color:var(--muted);font-style:italic}
        .meta{font-size:11px;color:var(--muted);display:flex;justify-content:space-between}
        .hint{font-size:10px;color:#0ea5e9}
        .ring-wrap{display:flex;align-items:center;gap:10px;margin-bottom:8px}
        .rbg{fill:none;stroke:#fecdd3;stroke-width:4}
        .rarc{fill:none;stroke:#e11d48;stroke-width:4;stroke-linecap:round}
        .rtxt{font-size:11px;fill:var(--muted);text-anchor:middle;dominant-baseline:middle;font-family:ui-monospace,Menlo,monospace}
        .empty{text-align:center;padding:48px;color:var(--muted);font-size:13px;border:2px dashed var(--border);border-radius:12px;max-width:500px;margin:0 auto}
        .toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#1f2937;color:#fff;padding:7px 16px;border-radius:20px;font-size:12px;opacity:0;transition:opacity .3s;pointer-events:none;white-space:nowrap}
        .toast.show{opacity:1}
    </style>
</head>
<body>
<div class="topbar">
    <h1><span class="dot" id="dot"></span>验证码监控</h1>
    <div class="nav"><a href="/">仪表板</a><a href="/zd">设置</a></div>
</div>
<div id="tg-sec" style="display:none"><div class="sec-title">Telegram 登录验证码</div><div class="grid" id="tg-grid"></div></div>
<div id="ga-sec" style="display:none"><div class="sec-title">谷歌验证码 (2FA)</div><div class="grid" id="ga-grid"></div></div>
<div id="empty" class="empty" style="display:none">暂无已配置的账号</div>
<div id="toast" class="toast"></div>
<script>
const R=24,C=R+4,CIRC=2*Math.PI*R;
let gaStates={};

function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function copyCode(code){
    if(!code)return;
    (navigator.clipboard?navigator.clipboard.writeText(code):Promise.reject()).catch(()=>{
        const el=document.createElement('input');el.value=code;document.body.appendChild(el);el.select();document.execCommand('copy');document.body.removeChild(el);
    });
    const t=document.getElementById('toast');t.textContent=code+' 已复制';t.classList.add('show');clearTimeout(t._t);t._t=setTimeout(()=>t.classList.remove('show'),2000);
}
function buildTG(item){
    const d=document.createElement('div');d.className='card';d.onclick=()=>copyCode(item.code);
    const timeStr=(item.time||'').split(' ')[1]||'';
    d.innerHTML=item.code
        ?`<div class="card-head"><span class="acc">${esc(item.name)}</span><span class="badge btg">TG</span></div><div class="code ctg">${esc(item.code)}</div><div class="meta"><span>${esc(timeStr)} 接收</span><span class="hint">点击复制</span></div>${item.text?`<div class="meta" style="margin-top:8px;border-top:1px solid #f3f4f6;padding-top:8px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%">${esc(item.text)}</span></div>`:''}`
        :`<div class="card-head"><span class="acc">${esc(item.name)}</span><span class="badge btg">TG</span></div><div class="code ctg empty-code">等待验证码...</div>`;
    return d;
}
function buildGA(item){
    const d=document.createElement('div');d.className='card';d.setAttribute('data-ga',item.name);d.onclick=()=>copyCode(item.code);
    d.innerHTML=`<div class="card-head"><span class="acc">${esc(item.name)}</span><span class="badge bga">2FA</span></div><div class="ring-wrap"><svg width="${C*2}" height="${C*2}" viewBox="0 0 ${C*2} ${C*2}"><circle class="rbg" cx="${C}" cy="${C}" r="${R}"/><circle class="rarc" cx="${C}" cy="${C}" r="${R}" stroke-dasharray="${CIRC}" stroke-dashoffset="0" transform="rotate(-90 ${C} ${C})"/><text class="rtxt" x="${C}" y="${C}">30</text></svg><div class="code cga" style="flex:1">${esc(item.code)}</div></div><div class="meta"><span style="opacity:.5">点击复制</span></div>`;
    return d;
}
function updateRing(name){
    const s=gaStates[name];if(!s)return;
    const card=document.querySelector('[data-ga="'+name+'"]');if(!card)return;
    const elapsed=Date.now()/1000-s.fetchedAt;
    const ttl=Math.max(0,s.ttl_exact-elapsed);
    const arc=card.querySelector('.rarc'),txt=card.querySelector('.rtxt'),codeEl=card.querySelector('.cga');
    if(arc){arc.setAttribute('stroke-dashoffset',(1-ttl/s.interval)*CIRC);arc.setAttribute('stroke',ttl<5?'#ef4444':'#e11d48');}
    if(txt)txt.textContent=Math.ceil(ttl)+'s';
    if(codeEl)codeEl.textContent=s.code;
}
(function animLoop(){requestAnimationFrame(animLoop);for(const n in gaStates)updateRing(n);})();

async function poll(){
    try{
        const res=await fetch('/api/otp');
        if(!res.ok)throw new Error(res.status);
        const data=await res.json();
        const recvAt=Date.now()/1000;
        // TG
        const tgGrid=document.getElementById('tg-grid'),tgSec=document.getElementById('tg-sec');
        if(data.tg_codes&&data.tg_codes.length){
            tgSec.style.display='';
            const cur=tgGrid.querySelectorAll('.card');
            if(cur.length!==data.tg_codes.length){tgGrid.innerHTML='';data.tg_codes.forEach(i=>tgGrid.appendChild(buildTG(i)));}
            else data.tg_codes.forEach((item,i)=>{const c=cur[i].querySelector('.code');if(c)c.textContent=item.code||'等待验证码...';});
        }else tgSec.style.display='none';
        // GA
        const gaGrid=document.getElementById('ga-grid'),gaSec=document.getElementById('ga-sec');
        if(data.ga_codes&&data.ga_codes.length){
            gaSec.style.display='';
            const existing=new Set([...gaGrid.querySelectorAll('.card')].map(c=>c.getAttribute('data-ga')));
            const incoming=new Set(data.ga_codes.map(x=>x.name));
            existing.forEach(n=>{if(!incoming.has(n)){const c=gaGrid.querySelector('[data-ga="'+n+'"]');if(c)c.remove();delete gaStates[n];}});
            data.ga_codes.forEach(item=>{
                gaStates[item.name]={code:item.code,ttl_exact:item.ttl_exact,interval:item.interval||30,fetchedAt:recvAt};
                if(!gaGrid.querySelector('[data-ga="'+item.name+'"]'))gaGrid.appendChild(buildGA(item));
            });
        }else{gaSec.style.display='none';}
        const hasAny=(data.tg_codes&&data.tg_codes.length)||(data.ga_codes&&data.ga_codes.length);
        document.getElementById('empty').style.display=hasAny?'none':'';
        document.getElementById('dot').style.background='#22c55e';
    }catch(e){document.getElementById('dot').style.background='#ef4444';}
}
poll();setInterval(poll,2000);
</script>
</body>
</html>
"""

def get_monitor_settings_html():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "monitor_settings.html")
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
        required_markers = ("</html>", "createApp", 'id="app"', "monitor_settings_json")
        if all(marker in html for marker in required_markers):
            return html
        logger.error(f"❌ [Monitor UI] 新版控制面板模板不完整，使用内置旧版: {template_path}, size={len(html)}")
    except Exception as e:
        logger.error(f"❌ [Monitor UI] 无法加载新版控制面板模板，使用内置旧版: {e}")
    return SETTINGS_HTML

async def analyze_message(client, rule, event, other_cs_ids, sender_obj, check_cooldown=True):
    if not rule.get("enabled", True): 
        return False, "规则已关闭", None

    if event.chat_id not in rule.get("groups", []): return False, "群组不符", None
    if event.is_reply: return False, "是回复消息", None
    if await is_monitor_sender_cs(client, event, other_cs_ids, sender_obj):
        return False, "发送者是客服", None
    
    # v69: Pass sender object, not name string
    if not check_sender_allowed(sender_obj, rule):
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
    
    if check_cooldown:
        rule_id = ensure_rule_id(rule)
        last_time = rule_timers.get(rule_id, 0)
        now = time.time()
        if now - last_time < rule.get("cooldown", 60): return False, "冷却中", None
    
    return True, "✅ 匹配成功", None

async def run_schedule_job():
    while True:
        try:
            await asyncio.sleep(60)
            schedule = current_config.get("schedule", {})
            if not schedule.get("active", False): continue
            start_str = schedule.get("start", "09:00")
            end_str = schedule.get("end", "21:00")
            now = datetime.now(BJ_TZ)
            current_time = now.strftime("%H:%M")
            is_working_hours = False
            if start_str < end_str:
                if start_str <= current_time < end_str: is_working_hours = True
            else:
                if current_time >= start_str or current_time < end_str: is_working_hours = True
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

async def send_scheduled_message_job(item):
    job_id = ensure_scheduled_message_id(item)
    job_name = item.get("name") or job_id
    target_name = item.get("account") or MAIN_NAME
    started = time.time()

    if target_name != MAIN_NAME and not current_config.get("extra_enabled", True):
        raise RuntimeError(f"副账号分身模式已关闭：{target_name}")
    if target_name not in global_clients:
        raise RuntimeError(f"发送账号不存在或未注册：{target_name}")

    target_client = global_clients[target_name]
    if target_name != MAIN_NAME and hasattr(target_client, "is_connected") and not target_client.is_connected():
        raise RuntimeError(f"发送账号未连接：{target_name}")

    targets = [parse_peer_target(group_id) for group_id in item.get("groups", [])]
    targets = [target for target in targets if target is not None]
    if not targets:
        raise RuntimeError("未配置发送群组")
    text = format_caption(item.get("text", ""))
    if not text:
        raise RuntimeError("发送消息不能为空")

    sent_count = 0
    errors = []
    for target in targets:
        try:
            await target_client.send_message(target, text)
            sent_count += 1
            await asyncio.sleep(0.4)
        except Exception as e:
            errors.append(f"{target}: {e}")

    status = "success" if sent_count and not errors else "failed"
    detail = f"定时发送 {sent_count}/{len(targets)} 个群"
    if errors:
        detail += f"，失败: {'; '.join(errors[:3])}"
    record_runtime_event(
        "scheduled_message",
        status,
        detail,
        rule={"id": job_id, "name": job_name},
        target_account=target_name,
        action_count=sent_count,
        duration_ms=(time.time() - started) * 1000
    )
    if errors:
        raise RuntimeError(detail)
    return sent_count

async def run_scheduled_messages_job():
    while True:
        try:
            await asyncio.sleep(20)
            now = datetime.now(BJ_TZ)
            today = now.strftime("%Y-%m-%d")
            current_time = now.strftime("%H:%M")
            changed = False
            for item in list(current_config.get("scheduled_messages", [])):
                try:
                    job_id = ensure_scheduled_message_id(item)
                    if not item.get("enabled", False):
                        continue
                    if item.get("time") != current_time:
                        continue
                    if scheduled_message_runs.get(job_id) == today or item.get("last_sent_date") == today:
                        continue

                    logger.info(f"⏰ [ScheduledMessage] 开始执行: {item.get('name') or job_id} -> {item.get('groups')}")
                    await send_scheduled_message_job(item)
                    scheduled_message_runs[job_id] = today
                    item["last_sent_date"] = today
                    item["last_sent_at"] = now.isoformat()
                    if item.get("frequency") == "once":
                        item["enabled"] = False
                    changed = True
                    logger.info(f"✅ [ScheduledMessage] 执行完成: {item.get('name') or job_id}")
                except Exception as e:
                    scheduled_message_runs[item.get("id", "")] = today
                    record_runtime_event(
                        "scheduled_message",
                        "failed",
                        f"定时发送失败：{e}",
                        rule={"id": item.get("id") or "__scheduled__", "name": item.get("name") or "定时发送"},
                        target_account=item.get("account") or MAIN_NAME,
                    )
                    logger.error(f"❌ [ScheduledMessage] 执行失败: {e}")
            if changed:
                persist_current_config()
        except Exception as e:
            logger.error(f"❌ [ScheduledMessage] Error: {e}")

def init_monitor(client, app, other_cs_ids, main_cs_prefixes, main_handler=None):
    global global_main_handler
    global system_cs_prefixes
    global_main_handler = main_handler
    system_cs_prefixes = list(main_cs_prefixes or [])
    init_redis_connection() # [v78] 确保调用全局函数
    load_config(main_cs_prefixes)
    load_runtime_stats()
    
    try: bot_loop = client.loop
    except:
        try: bot_loop = asyncio.get_event_loop()
        except: bot_loop = asyncio.new_event_loop(); asyncio.set_event_loop(bot_loop)

    if bot_loop:
        bot_loop.create_task(run_schedule_job())
        bot_loop.create_task(run_scheduled_messages_job())

    @app.route('/zd')
    def monitor_settings_page(): 
        return Response(get_monitor_settings_html(), mimetype='text/html; charset=utf-8')
        
    @app.route('/otp')
    def view_otp_page():
        return Response(OTP_HTML, mimetype='text/html; charset=utf-8')

    @app.route('/api/otp')
    def api_otp_data():
        import time as _t
        now = _t.time()
        result = {"server_time": now, "tg_codes": [], "ga_codes": []}
        for name, data in latest_otp_storage.items():
            result["tg_codes"].append({"name": name, "code": data.get("code", ""), "text": (data.get("text") or "")[:40], "time": data.get("time", "")})
        if pyotp:
            raw_secrets = os.environ.get("GA_SECRETS", "")
            for p in (raw_secrets or "").split(';'):
                if ':' not in p: continue
                name, secret = p.split(':', 1)
                name, secret = name.strip(), secret.strip()
                if not secret: continue
                try:
                    totp = pyotp.TOTP(secret)
                    result["ga_codes"].append({"name": name, "code": totp.now(), "ttl_exact": round(totp.interval - (now % totp.interval), 3), "interval": totp.interval})
                except Exception as e:
                    logger.error(f"[OTP] {name}: {e}")
        return jsonify(result)

    @app.route('/tool/monitor_settings_json')
    def monitor_settings_json():
        data = current_config.copy()
        data["available_accounts"] = [k for k in global_clients.keys() if k != MAIN_NAME]
        data["main_account"] = MAIN_NAME
        data["accounts"] = get_account_summaries()
        return jsonify(data)

    @app.route('/api/monitor_runtime_stats')
    def monitor_runtime_stats_api():
        try:
            limit = max(1, min(500, int(request.args.get("limit", 160))))
        except Exception:
            limit = 160
        return jsonify(build_monitor_runtime_stats(limit=limit))

    @app.route('/api/monitor_settings', methods=['POST'])
    def update_monitor_settings():
        success, msg = save_config(request.get_json(silent=True) or {})
        return jsonify({"success": success, "msg": msg if not success else ""})

    @app.route('/api/monitor_toggle', methods=['POST'])
    def update_monitor_toggle():
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled")
        if enabled is None:
            enabled = not current_config.get("enabled", True)
        success, msg = save_monitor_enabled(bool(enabled))
        return jsonify({
            "success": success,
            "enabled": current_config.get("enabled", True),
            "msg": msg if not success else ""
        })

    @app.route('/api/batch_recovery', methods=['POST'])
    def trigger_batch_recovery():
        data = request.get_json(silent=True) or {}
        search = str(data.get('search') or '').strip()
        reply = str(data.get('reply') or '').strip()
        if not search or not reply:
            return jsonify({"success": False, "msg": "查找话术和回复内容不能为空"})

        start_time_raw = str(data.get('start_time') or '').strip()
        start_time = None
        if start_time_raw:
            try:
                start_time = datetime.fromisoformat(start_time_raw)
                if start_time.tzinfo is None:
                    start_time = start_time.replace(tzinfo=BJ_TZ)
                start_time = start_time.astimezone(timezone.utc)
            except Exception:
                return jsonify({"success": False, "msg": "起始时间无效"})
        else:
            try:
                hours = max(0.1, float(data.get('hours', 5)))
                start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            except Exception:
                return jsonify({"success": False, "msg": "起始时间无效"})

        try:
            min_d = max(0.0, float(data.get('min', 2.0)))
            max_d = max(0.0, float(data.get('max', 5.0)))
        except Exception:
            return jsonify({"success": False, "msg": "间隔秒数无效"})

        if min_d > max_d:
            min_d, max_d = max_d, min_d

        image_data = data.get('image') or ''
        image_bytes = None
        image_name = data.get('imageName') or 'image.jpg'
        if image_data and ',' in image_data:
            try:
                import base64 as _b64
                image_bytes = _b64.b64decode(image_data.split(',', 1)[1])
            except Exception:
                image_bytes = None
        asyncio.run_coroutine_threadsafe(
            run_batch_recovery_task(client, search, reply, start_time, min_d, max_d, image_bytes, image_name),
            bot_loop
        )
        return jsonify({"success": True, "msg": "任务已启动" + ("（含图片）" if image_bytes else "")})

    async def run_batch_recovery_task(cli, search, reply, start_time, min_d, max_d, image_bytes=None, image_name='image.jpg'):
        limit_time = start_time.astimezone(timezone.utc)
        async for msg in cli.iter_messages(None, search=search):
            if msg.date < limit_time: break
            if not msg.is_group or not msg.out: continue
            try:
                target_id = msg.reply_to_msg_id if (msg.is_reply and msg.reply_to_msg_id) else msg.id
                caption = format_caption(reply)
                if image_bytes:
                    # 图片+文字 一条消息发送
                    import io as _io
                    buf = _io.BytesIO(image_bytes)
                    buf.name = image_name
                    await cli.send_file(msg.chat_id, buf, caption=caption, reply_to=target_id)
                else:
                    await cli.send_message(msg.chat_id, caption, reply_to=target_id)
                record_runtime_event(
                    "recovery",
                    "success",
                    f"批量恢复回复：{search}",
                    rule={"id": "__recovery__", "name": "突发事件批量回复"},
                    event=msg,
                    target_account=MAIN_NAME,
                    action_count=1
                )
                await asyncio.sleep(random.uniform(min_d, max_d))
            except Exception as e:
                record_runtime_event(
                    "recovery",
                    "failed",
                    f"批量恢复失败：{e}",
                    rule={"id": "__recovery__", "name": "突发事件批量回复"},
                    event=msg,
                    target_account=MAIN_NAME,
                    action_count=0
                )
                logger.error(f"❌ [Recovery] 批量回复失败 Msg={getattr(msg, 'id', 'unknown')}: {e}")

    def create_otp_handler(account_name):
        async def otp_handler(event):
            try:
                text = event.message.text or ""
                code = ""
                match = re.search(r'[\s:](\d{5})[\s.]', text)
                if match: code = match.group(1)
                else:
                    match = re.search(r'\b\d{5}\b', text)
                    if match: code = match.group(0)
                latest_otp_storage[account_name] = {"code": code, "text": text, "time": datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')}
                logger.info(f"🔐 [OTP] {account_name} 收到官方消息, Code: {code}")
            except Exception as e:
                logger.error(f"❌ [OTP] Error ({account_name}): {e}")
        return otp_handler

    # Main Account
    main_name = os.environ.get("MAIN_SESSION_NAME", "主账号")
    global MAIN_NAME
    MAIN_NAME = main_name # Set global main name
    
    client.add_event_handler(create_otp_handler(main_name), events.NewMessage(chats=777000))
    global_clients[main_name] = client # v65: Register main client

    # Extra Accounts
    extra_sessions_env = os.environ.get("EXTRA_SESSION_STRINGS", "")
    api_id = int(os.environ.get("API_ID", 0))
    api_hash = os.environ.get("API_HASH", "")

    async def _start_extra_client(cli, name):
        try:
            await cli.connect()
            if not await cli.is_user_authorized():
                logger.error(f"❌ [OTP] {name} 身份验证失败: Session String 无效或已过期")
                await cli.disconnect()
                return
            
            me = await cli.get_me()
            client_user_ids[name] = me.id
            logger.info(f"✅ [OTP] {name} 启动成功 | 登录身份: {me.first_name} ({me.id})")
            
            try:
                history = await cli.get_messages(777000, limit=1)
                if history:
                    await create_otp_handler(name)(events.NewMessage.Event(history[0]))
                    logger.info(f"📥 [OTP] {name} 已自动加载最新一条验证码")
            except Exception as e:
                logger.warning(f"⚠️ [OTP] {name} 无法获取历史消息: {e}")

            asyncio.create_task(keep_alive_loop(cli, name))
            await cli.run_until_disconnected()
        except Exception as e:
            logger.error(f"❌ [OTP] {name} 启动/运行失败: {e}")

    async def keep_alive_loop(cli, name):
        while cli.is_connected():
            try:
                now = datetime.now(BJ_TZ)
                target = now.replace(hour=12, minute=13, second=47, microsecond=0)
                if now >= target: target += timedelta(days=6)
                wait_seconds = (target - now).total_seconds()
                logger.info(f"⏳ [OTP] {name} 下次保活时间: {target.strftime('%Y-%m-%d %H:%M:%S')} (等待 {int(wait_seconds)}秒)")
                await asyncio.sleep(wait_seconds)
                if not cli.is_connected(): break
                await cli(functions.account.UpdateStatusRequest(offline=False))
                msg = await cli.send_message('me', f"💓 6-Day Keep-Alive: {datetime.now(BJ_TZ).strftime('%Y-%m-%d %H:%M:%S')}")
                await asyncio.sleep(5)
                await msg.delete()
                logger.info(f"💓 [OTP] {name} 每6日保活执行成功")
                await asyncio.sleep(60)
            except Exception as e:
                logger.warning(f"⚠️ [OTP] {name} 保活失败: {e}")
                await asyncio.sleep(300)

    if extra_sessions_env and api_id and api_hash:
        raw_items = [x.strip() for x in extra_sessions_env.split(';') if x.strip()]
        for i, item in enumerate(raw_items):
            try:
                if '=' in item:
                    parts = item.split('=', 1)
                    if len(parts[0]) > 30: 
                        acc_name = f"副账号 {i+1}"
                        sess_str = item
                    else:
                        acc_name = parts[0].strip()
                        sess_str = parts[1].strip()
                else:
                    acc_name = f"副账号 {i+1}"
                    sess_str = item
                
                logger.info(f"🔄 [OTP] 正在准备 {acc_name}...")
                
                # 在这里添加设备伪装参数
                extra_client = TelegramClient(
                    StringSession(sess_str), 
                    api_id, 
                    api_hash, 
                    loop=bot_loop,
                    device_model="VMware20,1",
                    system_version="Windows 10 x64",
                    app_version="6.6.3 x64",
                    lang_code="zh-hans",
                    system_lang_code="zh-hans"
                )
                
                global_clients[acc_name] = extra_client
                extra_client.add_event_handler(create_otp_handler(acc_name), events.NewMessage(chats=777000))
                bot_loop.create_task(_start_extra_client(extra_client, acc_name))
            except Exception as e:
                logger.error(f"❌ [OTP] 初始化 {acc_name} 失败: {e}")

    @client.on(events.NewMessage())
    async def multi_rule_handler(event):
        if event.text == "/debug": await event.reply("Monitor Debug: Alive v78 (Full Source)"); return
        if not current_config.get("enabled", True): return
        
        # Approval Logic
        if event.is_reply:
            app_kws = current_config.get("approval_keywords", ["同意", "批准", "ok"])
            event_text = event.text or ""
            if approval_keyword_matches(event_text, app_kws):
                try:
                    approver = await event.get_sender()
                    if await is_monitor_sender_cs(client, event, other_cs_ids, approver):
                        logger.info("🛡️ [Approval] 忽略自己/客服账号发出的审批触发词")
                        return

                    replied_msg = await event.get_reply_message()
                    if replied_msg:
                        for original_msg in await collect_approval_candidate_messages(client, replied_msg):
                            try:
                                orig_sender = await original_msg.get_sender()
                            except Exception:
                                orig_sender = None

                            for rule in current_config.get("rules", []):
                                if not rule.get("enabled", True): continue
                                if not rule.get("enable_approval", False): continue

                                # 1. Match the real report. If the leader replied to our prompt,
                                #    the parent message is also tested as the original report.
                                is_match, _, _ = await analyze_message(client, rule, MessageEventView(original_msg), other_cs_ids, orig_sender, check_cooldown=False)
                                if not is_match:
                                    continue

                                if not approval_amount_gate_passes(rule, original_msg):
                                    logger.info(f"💰 [Approval] 规则 '{rule.get('name')}' 原消息金额未达到审批阈值，跳过")
                                    continue

                                # 2. [Fixed] Check if APPROVER is allowed for THIS rule
                                if not check_sender_allowed(approver, rule):
                                    continue

                                logger.info(f"👮 [Approval] 批准通过! 匹配规则: {rule.get('name')}")
                                approval_started = time.time()
                                approval_actions = 0
                                approval_errors = []
                                action = rule.get("approval_action", {})
                                
                                target_name = rule.get("reply_account")
                                
                                # v72: Strict Pause Logic
                                extra_on = current_config.get("extra_enabled", True)
                                if not target_name: target_name = MAIN_NAME 
                                
                                if target_name != MAIN_NAME and not extra_on:
                                    logger.info(f"⏸️ [Approval] 副号开关已关，规则已暂停")
                                    record_runtime_event(
                                        "approval",
                                        "skipped",
                                        "副号总开关已关闭，审批动作暂停",
                                        rule=rule,
                                        event=event,
                                        sender_name=get_sender_name(approver),
                                        target_account=target_name,
                                        duration_ms=(time.time() - approval_started) * 1000
                                    )
                                    return

                                if target_name not in global_clients:
                                    logger.error(f"❌ [Approval] 指定回复账号不存在或未注册: {target_name}，已取消执行")
                                    record_runtime_event(
                                        "approval",
                                        "failed",
                                        f"指定回复账号不存在或未注册：{target_name}",
                                        rule=rule,
                                        event=event,
                                        sender_name=get_sender_name(approver),
                                        target_account=target_name,
                                        duration_ms=(time.time() - approval_started) * 1000
                                    )
                                    return

                                replier_client = global_clients[target_name]
                                if target_name != MAIN_NAME and hasattr(replier_client, "is_connected") and not replier_client.is_connected():
                                    logger.error(f"❌ [Approval] 指定回复账号未连接: {target_name}，已取消执行")
                                    record_runtime_event(
                                        "approval",
                                        "failed",
                                        f"指定回复账号未连接：{target_name}",
                                        rule=rule,
                                        event=event,
                                        sender_name=get_sender_name(approver),
                                        target_account=target_name,
                                        duration_ms=(time.time() - approval_started) * 1000
                                    )
                                    return

                                await asyncio.sleep(random.uniform(float(action.get("delay_1_min", 1.0)), float(action.get("delay_1_max", 2.0))))
                                if action.get("reply_admin"):
                                    await replier_client.send_message(event.chat_id, format_caption(action["reply_admin"]), reply_to=event.id)
                                    approval_actions += 1
                                
                                await asyncio.sleep(random.uniform(float(action.get("delay_2_min", 1.0)), float(action.get("delay_2_max", 3.0))))
                                fwd_tgt = action.get("forward_to")
                                if fwd_tgt:
                                    try:
                                        await replier_client.forward_messages(parse_peer_target(fwd_tgt), original_msg)
                                        approval_actions += 1
                                    except Exception as e:
                                        approval_errors.append(f"转发失败：{e}")
                                        logger.error(f"❌ [Approval] 转发失败: {e}")

                                await asyncio.sleep(random.uniform(float(action.get("delay_3_min", 1.0)), float(action.get("delay_3_max", 2.0))))
                                if action.get("reply_origin"):
                                    try:
                                        await replier_client.send_message(original_msg.chat_id, format_caption(action["reply_origin"]), reply_to=original_msg.id)
                                        approval_actions += 1
                                    except Exception as e:
                                        approval_errors.append(f"回复原消息失败：{e}")
                                        logger.error(f"❌ [Approval] 回复原消息失败: {e}")
                                record_runtime_event(
                                    "approval",
                                    "failed" if approval_errors else "success",
                                    "；".join(approval_errors) if approval_errors else "审批动作执行完成",
                                    rule=rule,
                                    event=event,
                                    sender_name=get_sender_name(approver),
                                    target_account=target_name,
                                    action_count=approval_actions,
                                    duration_ms=(time.time() - approval_started) * 1000
                                )
                                return
                except Exception as e: logger.error(f"❌ [Approval] 处理出错: {e}")

        # Monitor Logic
        sender_name = ""
        try:
            event.sender = await event.get_sender()
            sender_name = get_sender_name(event.sender)
            logger.info(f"🔍 [Check] Sender: {sender_name} | ID: {event.sender_id}")
        except: pass

        for rule in current_config.get("rules", []):
            try:
                if not rule.get("enabled", True): continue
                # v69: Pass event.sender object
                is_match, reason, extracted_data = await analyze_message(client, rule, event, other_cs_ids, event.sender)
                if is_match:
                    grouped_id = getattr(event.message, "grouped_id", None)
                    if should_skip_album_reply(event.chat_id, grouped_id):
                        logger.info(f"🛡️ [Monitor] 图集去重 | Chat={event.chat_id} | GroupID={grouped_id} | Msg={event.id} 已跳过重复自动回复")
                        break

                    rule_id = ensure_rule_id(rule)
                    match_started = time.time()
                    logger.info(f"✅ [Monitor] 规则 '{rule.get('name')}' 触发!")
                    
                    # v72: Strict Routing (No Fallback)
                    target_name = rule.get("reply_account")
                    extra_on = current_config.get("extra_enabled", True)

                    # 1. Determine Target
                    if not target_name: target_name = MAIN_NAME

                    # 2. Check Permission (Strict Pause)
                    if target_name != MAIN_NAME and not extra_on:
                        logger.info(f"⏸️ [Routing] 副号开关已关，规则 '{rule.get('name')}' 已暂停 (不转交给主号)")
                        record_runtime_event(
                            "monitor",
                            "skipped",
                            "副号总开关已关闭，规则暂停执行",
                            rule=rule,
                            event=event,
                            sender_name=sender_name,
                            target_account=target_name,
                            duration_ms=(time.time() - match_started) * 1000
                        )
                        break # Stop checking other rules

                    # 3. Assign Client
                    if target_name not in global_clients:
                        logger.error(f"❌ [Routing] 指定回复账号不存在或未注册: {target_name}，规则 '{rule.get('name')}' 已取消")
                        record_runtime_event(
                            "monitor",
                            "failed",
                            f"指定回复账号不存在或未注册：{target_name}",
                            rule=rule,
                            event=event,
                            sender_name=sender_name,
                            target_account=target_name,
                            duration_ms=(time.time() - match_started) * 1000
                        )
                        break

                    target_client = global_clients[target_name]
                    if target_name != MAIN_NAME and hasattr(target_client, "is_connected") and not target_client.is_connected():
                        logger.error(f"❌ [Routing] 指定回复账号未连接: {target_name}，规则 '{rule.get('name')}' 已取消")
                        record_runtime_event(
                            "monitor",
                            "failed",
                            f"指定回复账号未连接：{target_name}",
                            rule=rule,
                            event=event,
                            sender_name=sender_name,
                            target_account=target_name,
                            duration_ms=(time.time() - match_started) * 1000
                        )
                        break
                    if target_name != MAIN_NAME: logger.info(f"🔀 [Routing] 使用指定账号回复: {target_name}")

                    sent_msgs = []
                    notify_sent = False
                    action_failed = False
                    preempted = False
                    try:
                        for step in rule.get("replies", []):
                            stype = step.get("type", "text")
                            if stype != "amount_logic":
                                await asyncio.sleep(random.uniform(step.get("min", 1), step.get("max", 3)))

                            if stype == "forward":
                                tgt = step.get("forward_to")
                                if tgt:
                                    tgt_chat_id = parse_peer_target(tgt)
                                    sent = await target_client.forward_messages(tgt_chat_id, event.message)
                                    remember_sent_message(sent_msgs, tgt_chat_id, sent)

                            elif stype == "edit_prev":
                                content = format_caption(step.get("text", ""))
                                if not content:
                                    continue
                                last_chat_id, last_sent = get_last_sent_record(sent_msgs)
                                last_msg_id = getattr(last_sent, "id", last_sent)
                                if not last_chat_id or not last_msg_id:
                                    logger.info(f"📝 [EditPrev] 规则 '{rule.get('name')}' 暂无可编辑的上一条消息，已跳过")
                                    continue
                                edited = await target_client.edit_message(last_chat_id, last_msg_id, content)
                                if edited:
                                    sent_msgs[-1] = (last_chat_id, edited)

                            elif stype == "copy_file":
                                tgt = step.get("forward_to")
                                if tgt and event.message.file:
                                    tgt_chat_id = parse_peer_target(tgt)
                                    sent = await target_client.send_file(tgt_chat_id, event.message.file.media, caption=format_caption(step.get("text", "")))
                                    remember_sent_message(sent_msgs, tgt_chat_id, sent)

                            elif stype == "amount_logic":
                                cfg = step.get("text", "")
                                tgt = step.get("forward_to")
                                parts = cfg.split('|')
                                if len(parts) >= 3:
                                    thresh = float(parts[0])
                                    # v76: Smart amount (Fixed long ID bug)
                                    found, amt = parse_smart_amount(event.text)

                                    if found:
                                        logger.info(f"💰 [Amount] 识别到金额: {amt}")
                                        if amt >= thresh:
                                            await asyncio.sleep(random_delay_from_step(step, "high_reply_min", "high_reply_max", step.get("min", 1), step.get("max", 3)))
                                            sent = await target_client.send_message(event.chat_id, format_caption(parts[1]), reply_to=event.id)
                                            remember_sent_message(sent_msgs, event.chat_id, sent)
                                        else:
                                            low_replies = split_reply_sequence(parts[2])
                                            if low_replies:
                                                await asyncio.sleep(random_delay_from_step(step, "low_first_min", "low_first_max", step.get("min", 1), step.get("max", 3)))
                                                sent = await target_client.send_message(event.chat_id, format_caption(low_replies[0]), reply_to=event.id)
                                                remember_sent_message(sent_msgs, event.chat_id, sent)
                                            if tgt:
                                                tgt_chat_id = parse_peer_target(tgt)
                                                if low_replies:
                                                    await asyncio.sleep(random_delay_from_step(step, "low_forward_min", "low_forward_max"))
                                                fwd_msg = await target_client.forward_messages(tgt_chat_id, event.message)
                                                remember_sent_message(sent_msgs, tgt_chat_id, fwd_msg)
                                            for sub_msg in low_replies[1:]:
                                                await asyncio.sleep(random_delay_from_step(step, "low_reply_min", "low_reply_max"))
                                                sent = await target_client.send_message(event.chat_id, format_caption(sub_msg), reply_to=event.id)
                                                remember_sent_message(sent_msgs, event.chat_id, sent)
                                    else:
                                        logger.warning(f"⚠️ [Monitor] Amount logic matched text but no specific amount found.")

                            elif stype == "preempt_check":
                                if not sent_msgs: continue
                                me = await target_client.get_me()
                                source_sent_ids = get_sent_ids_for_chat(sent_msgs, event.chat_id)
                                first_source_sent_id = get_first_sent_id_for_chat(sent_msgs, event.chat_id)
                                if not first_source_sent_id:
                                    logger.info(f"🛡️ [Preempt] 原群尚无本规则发送消息，跳过抢答检测")
                                    continue
                                hist_limit = max(20, len(source_sent_ids) + 10)
                                hist = await target_client.get_messages(event.chat_id, limit=hist_limit, min_id=event.id)
                                has_preempt = False
                                for m in hist:
                                    if not getattr(m, "id", None) or m.id >= first_source_sent_id:
                                        continue
                                    if m.id in source_sent_ids:
                                        continue
                                    if not getattr(m, "sender_id", None) or m.sender_id in (me.id, event.sender_id):
                                        continue
                                    if not is_same_reply_flow(m, event.message):
                                        continue
                                    has_preempt = True
                                    break
                                if has_preempt:
                                    await delete_sent_messages(target_client, sent_msgs)
                                    sent_msgs = []
                                    preempted = True
                                    logger.info(f"🧹 [Preempt] 检测到抢答，已删除规则 '{rule.get('name')}' 的已发送消息")
                                    break

                            elif stype == "notify_user":
                                notify_target = parse_peer_target(step.get("forward_to"))
                                notify_text = format_bot_notice(step.get("text", ""), event, rule, sender_name)
                                if notify_target and notify_text:
                                    await send_bot_notice(notify_target, notify_text)
                                    notify_sent = True
                                    logger.info(f"🔔 [Notify] 规则 '{rule.get('name')}' 已通过 Telegram Bot 通知: {notify_target}")

                            else: # text
                                content = step.get("text", "")
                                if content:
                                    sent = await target_client.send_message(event.chat_id, format_caption(content), reply_to=event.id)
                                    remember_sent_message(sent_msgs, event.chat_id, sent)
                                    if global_main_handler: asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
                    except Exception as e:
                        action_failed = True
                        if sent_msgs or notify_sent:
                            rule_timers[rule_id] = time.time()
                            logger.warning(f"⚠️ [Monitor] 规则 '{rule.get('name')}' 已部分发送，已进入冷却")
                        logger.error(f"❌ [Monitor] 规则 '{rule.get('name')}' 执行动作失败: {e}")
                        record_runtime_event(
                            "monitor",
                            "failed",
                            f"执行动作失败：{e}",
                            rule=rule,
                            event=event,
                            sender_name=sender_name,
                            target_account=target_name,
                            action_count=len(sent_msgs) + (1 if notify_sent else 0),
                            duration_ms=(time.time() - match_started) * 1000
                        )

                    if not action_failed:
                        action_count = len(sent_msgs) + (1 if notify_sent else 0)
                        if preempted:
                            record_runtime_event(
                                "monitor",
                                "skipped",
                                "抢答检测命中，已删除本规则已发送消息",
                                rule=rule,
                                event=event,
                                sender_name=sender_name,
                                target_account=target_name,
                                action_count=action_count,
                                duration_ms=(time.time() - match_started) * 1000
                            )
                        elif sent_msgs or notify_sent:
                            rule_timers[rule_id] = time.time()
                            record_runtime_event(
                                "monitor",
                                "success",
                                "规则动作执行完成",
                                rule=rule,
                                event=event,
                                sender_name=sender_name,
                                target_account=target_name,
                                action_count=action_count,
                                duration_ms=(time.time() - match_started) * 1000
                            )
                        else:
                            logger.warning(f"⚠️ [Monitor] 规则 '{rule.get('name')}' 未完成任何发送动作，不进入冷却")
                            record_runtime_event(
                                "monitor",
                                "skipped",
                                "规则触发但没有完成任何发送动作",
                                rule=rule,
                                event=event,
                                sender_name=sender_name,
                                target_account=target_name,
                                action_count=0,
                                duration_ms=(time.time() - match_started) * 1000
                            )
                    break
            except Exception as e: logger.error(f"❌ [Monitor] Rule Error: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v78 (Full Source) 已启动")
