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
import urllib.parse
import threading
import io
import zipfile
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

DATA_DIR = (os.environ.get("DATA_DIR") or os.environ.get("PERSISTENT_DATA_DIR") or "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)

def data_path(filename):
    return os.path.join(DATA_DIR, filename) if DATA_DIR else filename

CONFIG_FILE = data_path("monitor_config_v2.json")
REDIS_KEY = "monitor_config"
RUNTIME_STATS_FILE = data_path("monitor_runtime_stats.json")
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
try:
    SETTLEMENT_TG_REPLY_TTL_SECONDS = max(3600, int(os.environ.get("SETTLEMENT_TG_REPLY_TTL_SECONDS", "172800")))
except Exception:
    SETTLEMENT_TG_REPLY_TTL_SECONDS = 172800
SETTLEMENT_TG_FORWARD_DEDUP_LIMIT = 5000
settlement_tg_bridge_lock = threading.RLock()
settlement_tg_bridge_memory = {}
settlement_tg_forwarded_queue = deque()
settlement_tg_forwarded_keys = set()
ai_private_reply_latest = {}
ai_private_reply_locks = {}

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

def rule_has_backend_action(rule, action):
    action = normalize_backend_action(action)
    return any(
        isinstance(step, dict)
        and step.get("type") == "backend_unlock"
        and normalize_backend_action(step.get("backend_action", "")) == action
        for step in (rule or {}).get("replies", [])
    )

def looks_like_ticket_reason_request(text):
    compact = str(text or "").replace(" ", "")
    if re.search(r"自动处理失败|后台.*失败|催结算失败", compact):
        return False
    return bool(re.search(r"无效|無效|取消原因|无效原因|無效原因|失败原因|失敗原因|投注失败|投注失敗|注单取消|注單取消|取消|失败|失敗", compact))

def looks_like_short_urge_settlement(text):
    compact = str(text or "").replace(" ", "")
    if not re.search(r"(?<!\d)533\d{13}(?!\d)", compact):
        return False
    if re.search(r"自动处理失败|后台.*失败|催结算失败|人工核查|状态[:：]", compact):
        return False
    if looks_like_ticket_reason_request(compact):
        return False
    if not re.search(r"催|未结算|未結算|不结算|不結算|结算回滚|結算回滾|回滚|回滾", compact):
        return False
    if re.search(r"催(?:提款|取款|提现|出款)|(?:提款|取款|提现|出款).*催", compact):
        return False
    return True

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

BACKEND_UNLOCK_ACCOUNT_PATTERN = r"([A-Za-z0-9][A-Za-z0-9._-]{1,63})"
BACKEND_ORDER_NO_PATTERN = r"(?<!\d)(\d{12,24})(?!\d)"
BACKEND_PROXY_IP_PATTERN = r"\b((?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d))\b"
def int_env(name, default, min_value=None, max_value=None):
    try:
        value = int(os.environ.get(name, default) or default)
    except Exception:
        value = int(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value

def float_env(name, default, min_value=None, max_value=None):
    try:
        value = float(os.environ.get(name, default) or default)
    except Exception:
        value = float(default)
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value

ZD_AI_PARSE_ENABLED = os.environ.get("ZD_AI_PARSE_ENABLED", "1").strip().lower() not in ("0", "false", "off", "no")
ZD_AI_PARSE_RETRIES = int_env("ZD_AI_PARSE_RETRIES", 1, 0, 2)
ZD_AI_PARSE_TIMEOUT = float_env("ZD_AI_PARSE_TIMEOUT", 20, 3.0, 60.0)
ZD_AI_INPUT_MAX_CHARS = int_env("ZD_AI_INPUT_MAX_CHARS", 1600, 300, 5000)
ZD_AI_MAX_OUTPUT_TOKENS = int_env("ZD_AI_MAX_OUTPUT_TOKENS", 384, 128, 2048)
ZD_AGENT_MAX_OUTPUT_TOKENS = int_env("ZD_AGENT_MAX_OUTPUT_TOKENS", 768, 256, 4096)
AI_PRIVATE_REPLY_INPUT_MAX_CHARS = int_env("AI_PRIVATE_REPLY_INPUT_MAX_CHARS", 1200, 200, 5000)
AI_PRIVATE_REPLY_MAX_OUTPUT_TOKENS = int_env("AI_PRIVATE_REPLY_MAX_OUTPUT_TOKENS", 256, 64, 1024)
AI_PRIVATE_REPLY_TIMEOUT = float_env("AI_PRIVATE_REPLY_TIMEOUT", 20, 3.0, 60.0)
AI_PRIVATE_REPLY_TEMPERATURE = float_env("AI_PRIVATE_REPLY_TEMPERATURE", 0.35, 0.0, 1.0)
AI_PRIVATE_REPLY_CONTEXT_MESSAGES = int_env("AI_PRIVATE_REPLY_CONTEXT_MESSAGES", 12, 2, 30)
AI_PRIVATE_REPLY_CONTEXT_MINUTES = int_env("AI_PRIVATE_REPLY_CONTEXT_MINUTES", 20, 1, 1440)
AI_PRIVATE_REPLY_DEBOUNCE_SECONDS = float_env("AI_PRIVATE_REPLY_DEBOUNCE_SECONDS", 1.2, 0.0, 10.0)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash").strip() or "gemini-3.5-flash"
ZD_RULE_MAX_CONCURRENT = int_env("ZD_RULE_MAX_CONCURRENT", 12, 1, 100)
BACKEND_COMMAND_TIMEOUT = float_env("BACKEND_COMMAND_TIMEOUT", 240, 30.0, 900.0)

GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_AI_PRIVATE_REPLY_PROMPT = """
你是当前 Telegram 账号本人，正在一对一私聊里自然聊天。
根据最近私聊上下文，回复最后一条对方消息。
旧消息只用于理解连续对话，回复围绕最后一条对方消息。
语气自然、简短，像真人日常私聊，中文为主。
""".strip()

def gemini_generate_content_url():
    return f"{GEMINI_API_ROOT}/models/{GEMINI_MODEL}:generateContent"

def zd_ai_parse_available():
    return bool(ZD_AI_PARSE_ENABLED and GEMINI_API_KEY)

def trim_zd_ai_text(text):
    raw = str(text or "")
    if len(raw) <= ZD_AI_INPUT_MAX_CHARS:
        return raw
    return raw[:ZD_AI_INPUT_MAX_CHARS] + "\n...[已截断]"

def extract_json_object(text):
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("AI返回为空")
    try:
        return json.loads(raw)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, flags=re.IGNORECASE)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except Exception:
            pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        return json.loads(raw[start:end + 1])
    raise ValueError("AI返回不是JSON对象")

def ai_backend_action_schema(action):
    if action in {"urge_settlement", "query_ticket_cancel_reason"}:
        return "target/order_no 必须是12到24位注单号。"
    if action == "add_proxy_whitelist":
        return "target/ip 必须是IPv4地址。"
    if action == "member_data_overview":
        return "target/member 是会员账号；data_fields 只能从 总输赢、总流水、总存款、总提款、总红利、总返水 中选择；如出现时间范围，startAt/endAt 用 YYYY-MM-DD。"
    if action in {"query_member_line", "query_login_device_ip", "query_same_ip_device"}:
        return "target/member 是主会员账号；members 可放多个会员账号；agent_codes 放消息里的5到12位合营代码。"
    if action == "query_venue_turnover":
        return "target/member 是会员账号；venue 填场馆名，例如 米兰体育。"
    if action == "configure_rebate":
        return "target/game 填游戏名；venue 填场馆名；site 只在明确 6站/JN 或 9站/ML 时填写 6001/9001。"
    return "target/member 是会员账号。"

def build_ai_backend_parse_prompt(text, action, rule_name="", previous_error=""):
    today = now_bj().strftime("%Y-%m-%d")
    return f"""
你是 ZD 后台动作消息解析器，只能输出一个 JSON 对象，不要解释。

当前日期：{today}
规则名称：{rule_name or "-"}
固定动作：{action}
动作字段要求：{ai_backend_action_schema(action)}

原始消息：
{trim_zd_ai_text(text)}

请从原始消息中提取后台动作参数。不要发明不存在的信息。
输出 JSON schema：
{{
  "ok": true,
  "target": "",
  "members": [],
  "member": "",
  "order_no": "",
  "ip": "",
  "agent_codes": [],
  "data_fields": [],
  "startAt": "",
  "endAt": "",
  "private_reply": false,
  "telegram_account": "",
  "agent_code": "",
  "venue": "",
  "game": "",
  "site": "",
  "line_mode": "",
  "reason": ""
}}

字段规则：
- target 是该动作最终目标；催结算/取消失败原因填注单号，代理加白填IP，其它账号类填会员账号。
- member 仅账号类填写；order_no 仅催结算/取消失败原因填写；ip 仅代理加白填写。
- 查线/登录设备/查同IP设备支持多个会员，放 members；消息里的合营代码放 agent_codes。
- 查询场馆流水要提取 venue；配置返水要提取 venue 和 game。
- data_fields 只在查数据时填写，并按原消息要求出现顺序返回，例如 ["总存款","总提款","总流水"]；没明确要求字段时返回空数组。
- private_reply 只有明确要求私发/私聊/发我时才是 true。
- telegram_account 只有明确指定发送账号时填写账号名，否则空。
- agent_code 只有消息里明确提供上级代理编号时填写，通常是账号后面的5到12位数字。
- 解析失败时 ok=false，并在 reason 写明原因。
{f"上次解析失败原因：{previous_error}" if previous_error else ""}
""".strip()

def call_zd_ai_parse_once(text, action, rule_name="", previous_error=""):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 未配置")
    prompt = build_ai_backend_parse_prompt(text, action, rule_name=rule_name, previous_error=previous_error)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": ZD_AI_MAX_OUTPUT_TOKENS,
            "temperature": 0
        }
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        gemini_generate_content_url(),
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=ZD_AI_PARSE_TIMEOUT) as resp:
        status = getattr(resp, "status", resp.getcode())
        resp_text = resp.read().decode("utf-8", errors="replace")
    if status < 200 or status >= 300:
        raise RuntimeError(f"AI HTTP {status}: {resp_text[:200]}")
    data = json.loads(resp_text)
    raw_content = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
    return extract_json_object(raw_content)

def clean_ai_data_fields(fields):
    allowed = {"总输赢", "总流水", "总存款", "总提款", "总红利", "总返水"}
    aliases = {
        "总反水": "总返水", "反水": "总返水", "返水": "总返水",
        "输赢": "总输赢", "盈亏": "总输赢",
        "红利": "总红利", "优惠": "总红利",
        "流水": "总流水", "有效流水": "总流水", "有效投注": "总流水",
        "存款": "总存款", "充值": "总存款",
        "提款": "总提款", "取款": "总提款",
    }
    out = []
    for item in fields if isinstance(fields, list) else []:
        label = aliases.get(str(item or "").strip(), str(item or "").strip())
        if label in allowed and label not in out:
            out.append(label)
    return out

def data_overview_fields_from_text(text):
    aliases = [
        ("总输赢", "总输赢"), ("输赢", "总输赢"),
        ("总流水", "总流水"), ("有效流水", "总流水"), ("有效投注", "总流水"), ("流水", "总流水"),
        ("总存款", "总存款"), ("存款", "总存款"), ("充值", "总存款"),
        ("总提款", "总提款"), ("提款", "总提款"), ("取款", "总提款"),
        ("总红利", "总红利"), ("红利", "总红利"), ("优惠", "总红利"),
        ("总返水", "总返水"), ("总反水", "总返水"), ("返水", "总返水"), ("反水", "总返水"),
    ]
    source = str(text or "")
    matches = []
    for alias, label in aliases:
        start = 0
        while True:
            idx = source.find(alias, start)
            if idx < 0:
                break
            matches.append((idx, -len(alias), label))
            start = idx + max(1, len(alias))
    out = []
    seen = set()
    for _idx, _neg_len, label in sorted(matches):
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out

def is_likely_agent_member(value):
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", text):
        return False
    if re.fullmatch(r"\d{5,24}", text):
        return False
    if not re.search(r"[a-z]", text) or not re.search(r"\d", text):
        return False
    if re.fullmatch(r"(vip\d*|tg\d*|bot\d*|http|https|www|ip|ios|android|web|h5|jn|ml|art|ok|yes|no)", text, flags=re.I):
        return False
    return True

def clean_agent_members(values, source_text=""):
    members = []
    seen = set()
    def add(value):
        member = str(value or "").strip().lower()
        if not member or member in seen or not is_likely_agent_member(member):
            return
        seen.add(member)
        members.append(member)
    for item in values if isinstance(values, list) else []:
        add(item)
    for match in re.finditer(r"\b([A-Za-z][A-Za-z0-9._-]{1,63})\b", str(source_text or "")):
        add(match.group(1))
    return members[:12]

def clean_agent_codes(values, source_text=""):
    codes = []
    seen = set()
    def add(value):
        code = str(value or "").strip()
        if not re.fullmatch(r"\d{5,12}", code) or code in seen:
            return
        seen.add(code)
        codes.append(code)
    for item in values if isinstance(values, list) else []:
        add(item)
    for match in re.finditer(r"\b(\d{5,12})\b", str(source_text or "")):
        add(match.group(1))
    return codes[:30]

def agent_site_hint_from_text(text):
    compact = re.sub(r"\s+", "", str(text or "")).lower()
    if "6站" in compact or "jn站" in compact or re.search(r"\bjn\b", compact):
        return "6001"
    if "9站" in compact or "ml站" in compact or re.search(r"\bml\b", compact):
        return "9001"
    return ""

def agent_venue_hint_from_text(text):
    source = str(text or "")
    for name in ["米兰体育", "熊猫体育", "米兰电竞", "米兰棋牌", "米兰彩票", "米兰真人", "米兰电子", "米兰捕鱼"]:
        if name in source:
            return name
    match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]{2,20})场馆", source)
    return match.group(1) if match else ""

def agent_game_hint_from_text(text):
    source = re.sub(r"配置返水|返水配置|配置|返水|场馆|游戏|6站|9站|JN站|ML站|JN|ML", " ", str(text or ""), flags=re.I)
    for venue in ["米兰体育", "熊猫体育", "米兰电竞", "米兰棋牌", "米兰彩票", "米兰真人", "米兰电子", "米兰捕鱼"]:
        source = source.replace(venue, " ")
    tokens = [item.strip() for item in re.split(r"[\s,，。；;、]+", source) if item.strip()]
    return tokens[0][:40] if tokens else ""

def valid_iso_date(value):
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return ""
    try:
        datetime.strptime(text, "%Y-%m-%d")
        return text
    except Exception:
        return ""

def validate_ai_backend_parse(raw, action):
    if not isinstance(raw, dict):
        raise ValueError("AI返回不是对象")
    if raw.get("ok") is False:
        raise ValueError(str(raw.get("reason") or "AI表示无法解析"))
    target = str(raw.get("target") or "").strip()
    member = str(raw.get("member") or "").strip().lower()
    order_no = str(raw.get("order_no") or "").strip()
    ip = str(raw.get("ip") or "").strip()
    members = clean_agent_members(raw.get("members"), "")
    if member and member not in members and is_likely_agent_member(member):
        members.insert(0, member)
    elif target and target not in members and is_likely_agent_member(target):
        members.insert(0, target.lower())

    if action in {"urge_settlement", "query_ticket_cancel_reason"}:
        target = order_no or target
        if not re.fullmatch(r"\d{12,24}", target):
            raise ValueError("AI未提取到有效注单号")
    elif action == "add_proxy_whitelist":
        target = ip or target
        if not re.fullmatch(BACKEND_PROXY_IP_PATTERN, target):
            raise ValueError("AI未提取到有效IPv4")
    elif action == "configure_rebate":
        target = str(raw.get("game") or target or "").strip()
        if not target:
            raise ValueError("AI未提取到返水游戏")
    elif action in {"query_member_line", "query_login_device_ip", "query_same_ip_device", "query_venue_turnover"}:
        target = members[0] if members else (member or target.lower())
        if not re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, target):
            raise ValueError("AI未提取到有效会员账号")
    else:
        target = member or target.lower()
        if not re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, target):
            raise ValueError("AI未提取到有效会员账号")
    site = str(raw.get("site") or "").strip().lower()
    if site in {"6", "6站", "jn", "6001"}:
        site = "6001"
    elif site in {"9", "9站", "ml", "9001"}:
        site = "9001"
    else:
        site = ""

    parsed = {
        "target": target,
        "member": members[0] if members else member,
        "members": members,
        "order_no": order_no,
        "ip": ip,
        "agent_codes": clean_agent_codes(raw.get("agent_codes")),
        "data_fields": clean_ai_data_fields(raw.get("data_fields")),
        "startAt": valid_iso_date(raw.get("startAt")),
        "endAt": valid_iso_date(raw.get("endAt")),
        "private_reply": bool(raw.get("private_reply")),
        "telegram_account": str(raw.get("telegram_account") or "").strip(),
        "agent_code": str(raw.get("agent_code") or "").strip(),
        "venue": str(raw.get("venue") or raw.get("venue_name") or "").strip(),
        "game": str(raw.get("game") or raw.get("game_name") or "").strip(),
        "site": site,
        "line_mode": str(raw.get("line_mode") or "").strip(),
        "reason": str(raw.get("reason") or "").strip(),
    }
    if parsed["agent_code"] and not re.fullmatch(r"\d{5,12}", parsed["agent_code"]):
        parsed["agent_code"] = ""
    return parsed

def parse_backend_message_with_ai_sync(text, action, rule_name=""):
    if not zd_ai_parse_available():
        return {}
    last_error = ""
    for attempt in range(ZD_AI_PARSE_RETRIES + 1):
        try:
            raw = call_zd_ai_parse_once(text, action, rule_name=rule_name, previous_error=last_error)
            parsed = validate_ai_backend_parse(raw, action)
            logger.info(f"🤖 [ZD-AI] 解析成功 action={action} target={parsed.get('target')} attempt={attempt + 1}")
            return parsed
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ [ZD-AI] 解析失败 action={action} attempt={attempt + 1}/{ZD_AI_PARSE_RETRIES + 1}: {last_error}")
            if attempt < ZD_AI_PARSE_RETRIES:
                time.sleep(min(2.0, 0.4 * (attempt + 1)))
    logger.warning(f"⚠️ [ZD-AI] 多次解析失败，回退正则 action={action}: {last_error}")
    return {}

async def parse_backend_message_with_ai(text, action, rule_name=""):
    if not zd_ai_parse_available():
        return {}
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: parse_backend_message_with_ai_sync(text, action, rule_name))

def normalize_ai_private_reply_config(raw):
    if not isinstance(raw, dict):
        raw = {}
    accounts_raw = raw.get("accounts") if isinstance(raw.get("accounts"), dict) else raw
    accounts = {}
    for name, item in accounts_raw.items():
        account_name = str(name or "").strip()
        if not account_name or account_name == "accounts":
            continue
        if isinstance(item, bool):
            enabled = item
            prompt = ""
        elif isinstance(item, dict):
            enabled = bool(item.get("enabled", False))
            prompt = str(item.get("prompt", "") or "")
        else:
            continue
        accounts[account_name] = {
            "enabled": enabled,
            "prompt": prompt[:8000],
        }
    return {"accounts": accounts}

def get_ai_private_reply_account_config(account_name):
    config = normalize_ai_private_reply_config(current_config.get("ai_private_reply", {}))
    wanted = str(account_name or MAIN_NAME).strip() or MAIN_NAME
    accounts = config.get("accounts", {})
    if wanted in accounts:
        return accounts[wanted]
    wanted_lower = wanted.lower()
    for name, item in accounts.items():
        if str(name).lower() == wanted_lower:
            return item
    return {"enabled": False, "prompt": ""}

def trim_ai_private_context(text):
    raw = str(text or "").strip()
    if len(raw) <= AI_PRIVATE_REPLY_INPUT_MAX_CHARS:
        return raw
    return "...[前文已截断]\n" + raw[-AI_PRIVATE_REPLY_INPUT_MAX_CHARS:]

def build_ai_private_reply_prompt(account_name, account_prompt, context_text):
    custom_prompt = str(account_prompt or "").strip() or DEFAULT_AI_PRIVATE_REPLY_PROMPT
    return f"""
你要代 Telegram 账号“{account_name}”在私聊中回复。

账号提示词：
{custom_prompt}

最近私聊上下文，按时间从旧到新：
{trim_ai_private_context(context_text)}

直接给出要发送的回复文本。
""".strip()

def call_ai_private_reply_sync(account_name, account_prompt, context_text):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 未配置")
    prompt = build_ai_private_reply_prompt(account_name, account_prompt, context_text)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": AI_PRIVATE_REPLY_MAX_OUTPUT_TOKENS,
            "temperature": AI_PRIVATE_REPLY_TEMPERATURE,
        }
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        gemini_generate_content_url(),
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=AI_PRIVATE_REPLY_TIMEOUT) as resp:
        status = getattr(resp, "status", resp.getcode())
        resp_text = resp.read().decode("utf-8", errors="replace")
    if status < 200 or status >= 300:
        raise RuntimeError(f"AI HTTP {status}: {resp_text[:200]}")
    data = json.loads(resp_text)
    text = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
    reply = re.sub(r"^\s*[\"“”]+|[\"“”]+\s*$", "", str(text or "").strip())
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
    if not reply:
        raise ValueError("AI回复为空")
    return reply[:1200]

async def collect_ai_private_context(client, chat_id, limit=None):
    limit = limit or AI_PRIVATE_REPLY_CONTEXT_MESSAGES
    rows = []
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=AI_PRIVATE_REPLY_CONTEXT_MINUTES)
    async for message in client.iter_messages(chat_id, limit=limit):
        msg_date = getattr(message, "date", None)
        if msg_date:
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)
            if msg_date < cutoff:
                continue
        text = getattr(message, "raw_text", None) or getattr(message, "text", "") or ""
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        if not text:
            continue
        role = "我" if getattr(message, "out", False) else "对方"
        time_text = ""
        if msg_date:
            time_text = msg_date.astimezone(BJ_TZ).strftime("%H:%M")
        rows.append(f"{time_text} {role}: {text[:500]}".strip())
    rows.reverse()
    return "\n".join(rows)

async def generate_ai_private_reply(account_name, account_prompt, context_text):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: call_ai_private_reply_sync(account_name, account_prompt, context_text))

def ai_private_reply_key(account_name, chat_id):
    return f"{account_name}:{chat_id}"

def simple_ai_private_chat_reply(text):
    compact = re.sub(r"[\s，,。.!！?？~～、]+", "", str(text or "").strip().lower())
    if compact in {"你好", "您好", "哈喽", "哈啰", "hello", "hi", "嗨"}:
        return "你好"
    if compact in {"在吗", "在不", "在不在", "在"}:
        return "在"
    return ""

async def handle_ai_private_reply(event, account_name):
    if not getattr(event, "is_private", False):
        return
    message = getattr(event, "message", event)
    if getattr(event, "out", False) or getattr(message, "out", False):
        return
    if str(getattr(event, "chat_id", "") or "") == "777000":
        return
    text = getattr(event, "raw_text", None) or getattr(event, "text", "") or ""
    if not str(text or "").strip() or str(text).strip().startswith("/"):
        return
    if getattr(event, "sender_id", None) in set(client_user_ids.values()):
        return

    account_config = get_ai_private_reply_account_config(account_name)
    if not account_config.get("enabled"):
        return
    if not GEMINI_API_KEY:
        logger.warning("⚠️ [AI-Private] GEMINI_API_KEY 未配置，跳过私聊AI回复")
        return

    key = ai_private_reply_key(account_name, getattr(event, "chat_id", ""))
    ai_private_reply_latest[key] = getattr(event, "id", None)
    if AI_PRIVATE_REPLY_DEBOUNCE_SECONDS > 0:
        await asyncio.sleep(AI_PRIVATE_REPLY_DEBOUNCE_SECONDS)
    if ai_private_reply_latest.get(key) != getattr(event, "id", None):
        return

    lock = ai_private_reply_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        ai_private_reply_locks[key] = lock

    async with lock:
        if ai_private_reply_latest.get(key) != getattr(event, "id", None):
            return
        reply = simple_ai_private_chat_reply(text)
        if not reply:
            context_text = await collect_ai_private_context(event.client, event.chat_id)
            reply = await generate_ai_private_reply(account_name, account_config.get("prompt", ""), context_text)
        if ai_private_reply_latest.get(key) != getattr(event, "id", None):
            return
        await event.client.send_message(event.chat_id, reply)
        record_runtime_event(
            "ai_private_reply",
            "success",
            "AI私聊回复已发送",
            rule={"id": "__ai_private_reply__", "name": "AI私聊回复"},
            event=event,
            target_account=account_name,
            action_count=1,
        )

def create_ai_private_reply_handler(account_name):
    async def ai_private_reply_handler(event):
        try:
            await handle_ai_private_reply(event, account_name)
        except Exception as e:
            logger.error(f"❌ [AI-Private] 私聊AI回复失败 ({account_name}): {e}")
            record_runtime_event(
                "ai_private_reply",
                "failed",
                str(e),
                rule={"id": "__ai_private_reply__", "name": "AI私聊回复"},
                event=event,
                target_account=account_name,
            )
    return ai_private_reply_handler

def resolve_client_name(name):
    wanted = str(name or "").strip()
    if not wanted:
        return ""
    if wanted in global_clients:
        return wanted
    wanted_lower = wanted.lower()
    for item in global_clients.keys():
        if str(item).lower() == wanted_lower:
            return item
    return ""

def get_rule_task_semaphore():
    global rule_task_semaphore
    if rule_task_semaphore is None:
        rule_task_semaphore = asyncio.Semaphore(ZD_RULE_MAX_CONCURRENT)
    return rule_task_semaphore

def schedule_zd_rule_task(coro, label="规则任务"):
    semaphore = get_rule_task_semaphore()

    async def runner():
        async with semaphore:
            await coro

    task = asyncio.create_task(runner())
    active_rule_tasks.add(task)
    logger.info(f"🧵 [ZD-Concurrency] 已投递 {label} | active={len(active_rule_tasks)} limit={ZD_RULE_MAX_CONCURRENT}")

    def done_callback(done_task):
        active_rule_tasks.discard(done_task)
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            logger.warning(f"⚠️ [ZD-Concurrency] {label} 已取消 | active={len(active_rule_tasks)}")
            return
        if exc:
            logger.error(f"❌ [ZD-Concurrency] {label} 异常: {exc}")

    task.add_done_callback(done_callback)
    return task

def rule_has_backend_unlock(rule):
    return any(
        isinstance(step, dict) and step.get("type") == "backend_unlock"
        for step in (rule or {}).get("replies", [])
    )

def rule_has_agent_orchestrator(rule):
    return any(
        isinstance(step, dict)
        and (
            step.get("type") == "agent_orchestrator"
            or (step.get("type") == "backend_unlock" and normalize_backend_action(step.get("backend_action", "")) == "agent_existing")
        )
        for step in (rule or {}).get("replies", [])
    )

def rule_has_backend_or_agent(rule):
    return rule_has_backend_unlock(rule) or rule_has_agent_orchestrator(rule)

def rule_backend_actions(rule):
    return {
        normalize_backend_action(step.get("backend_action", "unlock_sms"))
        for step in (rule or {}).get("replies", [])
        if isinstance(step, dict) and step.get("type") == "backend_unlock"
    }

def text_mentions_secondary_password(text):
    normalized = unicodedata.normalize("NFKC", str(text or "")).lower()
    normalized = re.sub(r"\s+", "", normalized)
    return bool(re.search(r"(二级密码|二级.*密码|密码.*二级|2级.*密码|密码.*2级)", normalized))

def extract_backend_unlock_member(text, pattern="", use_default=True):
    msg_text = str(text or "").strip()
    if not msg_text:
        return None
    patterns = [pattern.strip()] if pattern and pattern.strip() else []
    if use_default:
        patterns.append(BACKEND_UNLOCK_ACCOUNT_PATTERN)
    for pat in patterns:
        try:
            m = re.search(pat, msg_text, flags=re.IGNORECASE)
        except re.error as e:
            logger.warning(f"⚠️ [BackendUnlock] 账号正则无效: {e}")
            continue
        if m and m.lastindex:
            member_name = str(m.group(1) or "").strip()
            if member_name:
                return member_name.lower()
    return None

def extract_backend_unlock_member_for_rule(rule, text, use_default=True):
    for step in (rule or {}).get("replies", []):
        if isinstance(step, dict) and step.get("type") == "backend_unlock":
            target_value = extract_backend_target(text, step, use_default=use_default)
            if target_value:
                return target_value
    return None

def extract_backend_proxy_ip(text, pattern="", use_default=True):
    msg_text = str(text or "").strip()
    if not msg_text:
        return None
    patterns = [pattern.strip()] if pattern and pattern.strip() else []
    if use_default:
        patterns.append(BACKEND_PROXY_IP_PATTERN)
    for pat in patterns:
        try:
            m = re.search(pat, msg_text, flags=re.IGNORECASE)
        except re.error as e:
            logger.warning(f"⚠️ [BackendUnlock] IP 正则无效: {e}")
            continue
        if m and m.lastindex:
            ip = str(m.group(1) or "").strip()
            if ip:
                return ip
    return None

def extract_backend_order_no(text, pattern="", use_default=True):
    msg_text = str(text or "").strip()
    if not msg_text:
        return None
    patterns = [pattern.strip()] if pattern and pattern.strip() else []
    if use_default:
        patterns.append(BACKEND_ORDER_NO_PATTERN)
    for pat in patterns:
        try:
            m = re.search(pat, msg_text, flags=re.IGNORECASE)
        except re.error as e:
            logger.warning(f"⚠️ [BackendUnlock] 注单号正则无效: {e}")
            continue
        if m and m.lastindex:
            order_no = str(m.group(1) or "").strip()
            if order_no:
                return order_no
    return None

def extract_backend_order_nos(text, pattern="", use_default=True):
    msg_text = str(text or "").strip()
    if not msg_text:
        return []
    patterns = [pattern.strip()] if pattern and pattern.strip() else []
    if use_default:
        patterns.append(BACKEND_ORDER_NO_PATTERN)
    result = []
    seen = set()
    for pat in patterns:
        try:
            matches = list(re.finditer(pat, msg_text, flags=re.IGNORECASE))
        except re.error as e:
            logger.warning(f"⚠️ [BackendUnlock] 注单号正则无效: {e}")
            continue
        for m in matches:
            if not m.lastindex:
                continue
            order_no = str(m.group(1) or "").strip()
            if order_no and order_no not in seen:
                seen.add(order_no)
                result.append(order_no)
    return result

def extract_backend_target(text, step, use_default=True):
    action = normalize_backend_action((step or {}).get("backend_action", "unlock_sms"))
    if action == "add_proxy_whitelist":
        return extract_backend_proxy_ip(text, (step or {}).get("ip_pattern", ""), use_default=use_default)
    if action in {"urge_settlement", "query_ticket_cancel_reason"}:
        return extract_backend_order_no(text, (step or {}).get("member_pattern", ""), use_default=use_default)
    return extract_backend_unlock_member(text, (step or {}).get("member_pattern", ""), use_default=use_default)

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

def settlement_tg_key_part(value):
    return str(value or "").strip()

def settlement_tg_bridge_keys(chat_id, message_id, account_name=""):
    chat_key = settlement_tg_key_part(chat_id)
    msg_key = settlement_tg_key_part(message_id)
    if not chat_key or not msg_key:
        return []
    keys = [f"settlement_tg_bridge:{chat_key}:{msg_key}"]
    account_key = settlement_tg_key_part(account_name)
    if account_key:
        keys.insert(0, f"settlement_tg_bridge:{account_key}:{chat_key}:{msg_key}")
    return keys

def telegram_peer_value(value):
    text = str(value or "").strip()
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except Exception:
            return text
    return text

def get_leased_command_snapshot(cmd_id):
    if not cmd_id:
        return {}
    try:
        with backend_command_lock:
            leased = pending_command_leases.get(str(cmd_id))
            if leased and isinstance(leased.get("cmd"), dict):
                return dict(leased["cmd"])
    except Exception:
        return {}
    return {}

def merge_command_context(post_data):
    post_data = dict(post_data or {})
    leased = get_leased_command_snapshot(post_data.get("id"))
    merged = dict(leased)
    for key, value in post_data.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged

def save_settlement_tg_bridge(account_name, sent_message, post_data):
    cmd = merge_command_context(post_data)
    action = normalize_backend_action(cmd.get("action") or cmd.get("backend_action") or "")
    if action and action != "urge_settlement":
        return

    source_chat_id = cmd.get("chat_id") or cmd.get("source_chat_id")
    source_message_id = cmd.get("message_id") or cmd.get("source_message_id")
    sent_chat_id = getattr(sent_message, "chat_id", None) or cmd.get("telegram_chat_id")
    sent_message_id = getattr(sent_message, "id", None)
    if not source_chat_id or not source_message_id or not sent_chat_id or not sent_message_id:
        logger.info(
            "ℹ️ [SettlementBridge] 跳过关联保存，缺少来源或群消息信息: "
            f"cmd={cmd.get('id') or '-'} source={source_chat_id}/{source_message_id} "
            f"sent={sent_chat_id}/{sent_message_id}"
        )
        return

    order_no = (
        cmd.get("orderNo")
        or cmd.get("order_no")
        or cmd.get("order_id")
        or cmd.get("target_value")
        or cmd.get("member_name")
        or ""
    )
    payload = {
        "cmd_id": str(cmd.get("id") or ""),
        "rule": str(cmd.get("rule") or "催结算"),
        "account": str(account_name or MAIN_NAME),
        "telegram_chat_id": str(sent_chat_id),
        "telegram_message_id": str(sent_message_id),
        "source_chat_id": str(source_chat_id),
        "source_message_id": str(source_message_id),
        "order_no": str(order_no or ""),
        "source_text": str(cmd.get("source_text") or "")[:1000],
        "telegram_text": str(cmd.get("text") or "")[:1000],
        "created_at": now_bj().isoformat(),
    }
    keys = settlement_tg_bridge_keys(sent_chat_id, sent_message_id, account_name)
    if not keys:
        return
    expires_at = time.time() + SETTLEMENT_TG_REPLY_TTL_SECONDS
    with settlement_tg_bridge_lock:
        for key in keys:
            settlement_tg_bridge_memory[key] = {"payload": payload, "expires_at": expires_at}
    if redis_client:
        raw = json.dumps(payload, ensure_ascii=False)
        for key in keys:
            try:
                redis_client.set(key, raw, ex=SETTLEMENT_TG_REPLY_TTL_SECONDS)
            except Exception as e:
                logger.warning(f"⚠️ [SettlementBridge] Redis 保存关联失败: {e}")
                break
    logger.info(
        f"🔗 [SettlementBridge] 已关联 TG 催结算消息: tg={sent_chat_id}/{sent_message_id} "
        f"origin={source_chat_id}/{source_message_id} order={payload['order_no'] or '-'}"
    )

def load_settlement_tg_bridge(chat_id, message_id, account_name=""):
    now = time.time()
    for key in settlement_tg_bridge_keys(chat_id, message_id, account_name):
        with settlement_tg_bridge_lock:
            item = settlement_tg_bridge_memory.get(key)
            if item:
                if float(item.get("expires_at") or 0) > now:
                    return dict(item.get("payload") or {})
                settlement_tg_bridge_memory.pop(key, None)
        if redis_client:
            try:
                raw = redis_client.get(key)
                if raw:
                    payload = json.loads(raw)
                    with settlement_tg_bridge_lock:
                        settlement_tg_bridge_memory[key] = {
                            "payload": payload,
                            "expires_at": now + SETTLEMENT_TG_REPLY_TTL_SECONDS,
                        }
                    return payload
            except Exception as e:
                logger.warning(f"⚠️ [SettlementBridge] Redis 读取关联失败: {e}")
    return {}

def mark_settlement_tg_reply_forwarded(chat_id, reply_message_id):
    key = f"settlement_tg_forwarded:{settlement_tg_key_part(chat_id)}:{settlement_tg_key_part(reply_message_id)}"
    if redis_client:
        try:
            return bool(redis_client.set(key, "1", nx=True, ex=SETTLEMENT_TG_REPLY_TTL_SECONDS))
        except Exception as e:
            logger.warning(f"⚠️ [SettlementBridge] Redis 去重失败，回退内存: {e}")
    with settlement_tg_bridge_lock:
        if key in settlement_tg_forwarded_keys:
            return False
        settlement_tg_forwarded_keys.add(key)
        settlement_tg_forwarded_queue.append(key)
        while len(settlement_tg_forwarded_queue) > SETTLEMENT_TG_FORWARD_DEDUP_LIMIT:
            old_key = settlement_tg_forwarded_queue.popleft()
            settlement_tg_forwarded_keys.discard(old_key)
    return True

def clear_settlement_tg_reply_forwarded_mark(chat_id, reply_message_id):
    key = f"settlement_tg_forwarded:{settlement_tg_key_part(chat_id)}:{settlement_tg_key_part(reply_message_id)}"
    if redis_client:
        try:
            redis_client.delete(key)
        except Exception:
            pass
    with settlement_tg_bridge_lock:
        settlement_tg_forwarded_keys.discard(key)

def get_message_reply_to_id(message):
    reply_to_id = getattr(message, "reply_to_msg_id", None)
    if reply_to_id:
        return reply_to_id
    reply_to = getattr(message, "reply_to", None)
    return getattr(reply_to, "reply_to_msg_id", None) or getattr(reply_to, "reply_to_top_id", None)

def clean_settlement_group_reply(text):
    raw = str(text or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"\r\n?", "\n", raw)
    suffix = r"(?:\s*[~～\-—–_#]*\s*[A-Za-z0-9][A-Za-z0-9_-]{0,15})\s*[。.!！]*"
    raw = re.sub(
        rf"(谢谢[！!。.]?){suffix}\s*$",
        r"\1",
        raw,
    )
    raw = re.sub(
        rf"(感谢[！!。.]?){suffix}\s*$",
        r"\1",
        raw,
    )
    return raw.strip()

def looks_like_settlement_group_result(text):
    compact = re.sub(r"\s+", "", str(text or ""))
    if len(compact) < 4:
        return False
    return bool(re.search(r"结算|刷新|赛果|核实|已处理|未处理|无效|取消|退回|本金|注单|盘口|比分", compact))

async def forward_settlement_tg_group_reply(event, account_name):
    message = getattr(event, "message", event)
    if getattr(event, "out", False) or getattr(message, "out", False):
        return
    reply_to_id = get_message_reply_to_id(message)
    if not reply_to_id:
        return
    payload = load_settlement_tg_bridge(getattr(event, "chat_id", None), reply_to_id, account_name)
    if not payload:
        return
    raw_text = getattr(event, "raw_text", None) or getattr(event, "text", "") or getattr(message, "message", "") or ""
    reply_text = clean_settlement_group_reply(raw_text)
    if not looks_like_settlement_group_result(reply_text):
        logger.info(f"↩️ [SettlementBridge] 忽略非结果类客服回复: tg={event.chat_id}/{getattr(message, 'id', '-')}")
        return
    reply_msg_id = getattr(message, "id", None)
    if not mark_settlement_tg_reply_forwarded(getattr(event, "chat_id", None), reply_msg_id):
        return

    source_chat_id = payload.get("source_chat_id")
    source_message_id = payload.get("source_message_id")
    target_client = global_clients.get(MAIN_NAME) or global_clients.get(account_name)
    if not target_client:
        clear_settlement_tg_reply_forwarded_mark(getattr(event, "chat_id", None), reply_msg_id)
        raise RuntimeError("没有可用于回传原消息的 Telegram 账号")
    try:
        await target_client.send_message(
            telegram_peer_value(source_chat_id),
            reply_text,
            reply_to=int(source_message_id),
        )
    except Exception:
        clear_settlement_tg_reply_forwarded_mark(getattr(event, "chat_id", None), reply_msg_id)
        raise

    record_runtime_event(
        "settlement_group_reply",
        "success",
        f"TG催结算群回复已回传：{payload.get('order_no') or '-'}",
        rule={"id": payload.get("cmd_id") or "__settlement_group_reply__", "name": payload.get("rule") or "催结算"},
        target_account=MAIN_NAME,
        action_count=1,
    )
    logger.info(
        f"✅ [SettlementBridge] 已回传 TG 催结算群回复: "
        f"tg={event.chat_id}/{reply_msg_id} origin={source_chat_id}/{source_message_id}"
    )

def create_settlement_tg_reply_handler(account_name):
    async def settlement_tg_reply_handler(event):
        try:
            await forward_settlement_tg_group_reply(event, account_name)
        except Exception as e:
            logger.error(f"❌ [SettlementBridge] 回传 TG 催结算群回复失败 ({account_name}): {e}")
    return settlement_tg_reply_handler

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
        elif not rule.get("enabled", True):
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

def split_sender_prefix_items(raw):
    items = split_config_items(raw, split_commas=True)
    result = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        parts = re.findall(r'@[A-Za-z0-9_]{2,}', text)
        compact = re.sub(r'\s+', '', text)
        if len(parts) > 1 and ''.join(parts) == compact:
            result.extend(parts)
        elif len(parts) > 1 and compact.count('@') == len(parts):
            result.extend(parts)
        else:
            result.append(text)
    return result

def clean_group_ids(raw):
    result = []
    for item in split_config_items(raw, split_commas=True):
        match = re.search(r'-?\d+', item)
        if match:
            try:
                result.append(canonical_group_id(match.group()))
            except Exception:
                pass
    return result

def canonical_group_id(value):
    group_id = int(value)
    if group_id > 0:
        raw = str(group_id)
        if raw.startswith("100") and len(raw) >= 12:
            return -group_id
        if len(raw) >= 9:
            return int(f"-100{raw}")
    return group_id

def rule_matches_group(chat_id, groups):
    try:
        cid = canonical_group_id(chat_id)
    except Exception:
        return False
    return cid in set(clean_group_ids(groups))

def normalize_resource_label(value, fallback="未命名"):
    text = str(value or "").strip()
    return text or fallback

def normalize_monitor_resources(raw_resources=None, rules=None, default_prefixes=None):
    raw_resources = raw_resources if isinstance(raw_resources, dict) else {}
    rules = rules or []
    default_prefixes = default_prefixes or []

    groups = []
    seen_group_ids = set()
    for item in raw_resources.get("groups", []):
        if isinstance(item, dict):
            raw_id = item.get("id", item.get("value", item.get("group_id", "")))
            name = item.get("name", item.get("label", item.get("title", "")))
        else:
            raw_id = item
            name = ""
        ids = clean_group_ids([raw_id])
        if not ids:
            continue
        group_id = ids[0]
        if group_id in seen_group_ids:
            continue
        seen_group_ids.add(group_id)
        groups.append({
            "id": group_id,
            "name": normalize_resource_label(name, "未命名群组")
        })

    for rule in rules:
        if not isinstance(rule, dict):
            continue
        for group_id in clean_group_ids(rule.get("groups", [])):
            if group_id in seen_group_ids:
                continue
            seen_group_ids.add(group_id)
            groups.append({"id": group_id, "name": "未命名群组"})

    sender_prefixes = []
    seen_prefixes = set()
    raw_prefix_resources = raw_resources.get("sender_prefixes", [])
    if isinstance(raw_prefix_resources, str):
        raw_prefix_resources = split_config_items(raw_prefix_resources, split_commas=True)
    for item in raw_prefix_resources:
        if isinstance(item, dict):
            raw_value = str(item.get("value", item.get("name", item.get("label", ""))) or "").strip()
            raw_label = item.get("label", item.get("name", raw_value))
        else:
            raw_value = str(item or "").strip()
            raw_label = raw_value
        for value in split_sender_prefix_items(raw_value):
            if not value:
                continue
            key = value.lower()
            if key in seen_prefixes:
                continue
            seen_prefixes.add(key)
            sender_prefixes.append({
                "value": value,
                "label": normalize_resource_label(raw_label if raw_value == value else value, value)
            })

    for prefix in list(default_prefixes) + [p for rule in rules if isinstance(rule, dict) for p in split_sender_prefix_items(rule.get("sender_prefixes", []))]:
        value = str(prefix or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen_prefixes:
            continue
        seen_prefixes.add(key)
        sender_prefixes.append({"value": value, "label": value})

    return {
        "groups": groups,
        "sender_prefixes": sender_prefixes
    }

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

async def is_monitor_own_account(client, event, other_cs_ids):
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

    return False

async def is_monitor_sender_cs(client, event, other_cs_ids, sender_obj=None):
    if await is_monitor_own_account(client, event, other_cs_ids):
        return True

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

async def find_preempting_reply(client, chat_id, origin_message, after_msg_id, before_msg_id=None, own_sent_ids=None, ignored_sender_ids=None, extra_target_ids=None, target_ids=None):
    if target_ids is None:
        target_ids = {getattr(origin_message, "id", None), *(extra_target_ids or [])}
    else:
        target_ids = set(target_ids)
    target_ids = {msg_id for msg_id in target_ids if msg_id}
    own_sent_ids = set(own_sent_ids or [])
    ignored_sender_ids = {sender_id for sender_id in (ignored_sender_ids or []) if sender_id}
    kwargs = {"limit": 80}
    if after_msg_id:
        kwargs["min_id"] = after_msg_id
    if before_msg_id:
        kwargs["max_id"] = before_msg_id
    hist = await client.get_messages(chat_id, **kwargs)
    for m in hist:
        msg_id = getattr(m, "id", None)
        if not msg_id:
            continue
        if msg_id in own_sent_ids:
            continue
        sender_id = getattr(m, "sender_id", None)
        if not sender_id or sender_id in ignored_sender_ids:
            continue
        if target_ids and target_ids.intersection(get_message_reply_target_ids(m)):
            return m
    return None

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

def _send_bot_message_sync(chat_id, text, reply_markup=None):
    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    if not bot_token:
        raise RuntimeError("BOT_TOKEN 未配置，无法发送 Bot 通知")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
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

async def send_bot_notice(chat_id, text, delete_button=True):
    loop = asyncio.get_event_loop()
    reply_markup = None
    if delete_button:
        reply_markup = {"inline_keyboard": [[{"text": "已读删除", "callback_data": "delete_notice"}]]}
    return await loop.run_in_executor(None, lambda: _send_bot_message_sync(chat_id, text, reply_markup))

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
    "extra_enabled": True, # retained for old configs;副账号发送不再受总开关限制
    "approval_keywords": ["同意", "批准", "ok"],
    "schedule": {
        "active": False,
        "start": "09:00",
        "end": "21:00"
    },
    "resources": {
        "groups": [],
        "sender_prefixes": []
    },
    "ai_private_reply": {
        "accounts": {}
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
            "cooldown": 1,
            "replies": [{"type": "text", "text": "收到", "min": 1, "max": 2}],
            "approval_action": {}
        }
    ]
}

current_config = DEFAULT_CONFIG.copy()
rule_timers = {}
scheduled_message_runs = {}
active_rule_tasks = set()
rule_task_semaphore = None
VALID_REPLY_TYPES = {"text", "edit_prev", "forward", "copy_file", "amount_logic", "preempt_check", "notify_user", "backend_unlock", "agent_orchestrator"}
BACKEND_UNLOCK_ACTIONS = {
    "unlock_sms", "clear_login_error", "add_proxy_whitelist", "migrate_milan",
        "send_site_inner_msg", "member_data_overview", "query_member_line",
        "query_login_device_ip", "query_same_ip_device", "query_venue_turnover",
        "configure_rebate", "urge_settlement", "query_ticket_cancel_reason", "agent_existing"
}
AGENT_EXECUTABLE_ACTIONS = {
    "unlock_sms", "clear_login_error", "add_proxy_whitelist", "migrate_milan",
    "member_data_overview", "query_member_line", "query_login_device_ip",
    "query_same_ip_device", "query_venue_turnover", "configure_rebate",
    "urge_settlement", "query_ticket_cancel_reason"
}
AGENT_CAPABILITIES = [
    {
        "action": "member_data_overview",
        "name": "查数据",
        "input": "会员账号",
        "fields": ["总输赢", "总流水", "总存款", "总提款", "总红利", "总返水"],
        "notes": "只有原消息明确要求总输赢/总流水/总存款/总提款/总红利/总返水等字段时才使用；会员账号单独出现不是查数据。",
    },
    {
        "action": "query_member_line",
        "name": "查线/查代理线",
        "input": "会员账号，可带一个或多个合营代码",
        "reply": "查线回复 在线下/官网/其他线下；查代理线命中时回复具体合营代码",
    },
    {
        "action": "query_login_device_ip",
        "name": "登录设备 IP 在哪里",
        "input": "会员账号，可带合营代码校验",
        "reply": "会员 设备 地区；不在线下则回复 xxx不在线下。",
    },
    {
        "action": "query_same_ip_device",
        "name": "查同IP/查同设备",
        "input": "会员账号，可带合营代码校验",
        "reply": "设备无关联/设备关联 N，IP无关联/IP关联 N/IP关联多个",
    },
    {
        "action": "query_venue_turnover",
        "name": "查询场馆流水还差多少",
        "input": "会员账号 + 场馆名，例如 米兰体育",
        "reply": "有锁定直接回复后台提示；无锁定则自动转回1元并回复缓存刷新话术",
    },
    {
        "action": "configure_rebate",
        "name": "配置返水",
        "input": "场馆名 + 游戏名",
        "notes": "按后台返水等级逐级保存指定场馆游戏配置",
    },
    {
        "action": "urge_settlement",
        "name": "催结算",
        "input": "533开头16位注单号，可多个",
        "notes": "只用于未结算、催促结算、一直不结算、结算回滚后重新催结算等诉求；不要用于取消/失败/无效原因。",
    },
    {
        "action": "query_ticket_cancel_reason",
        "name": "注单取消/失败原因",
        "input": "533开头16位注单号，可多个",
        "notes": "用于取消原因、失败原因、无效原因、投注失败、注单取消；优先按注单明细盘口/取消原因匹配公告，没有公告再回后台原因。",
    },
    {"action": "unlock_sms", "name": "短信/验证码限制", "input": "会员账号"},
    {"action": "clear_login_error", "name": "登录密码试错限制", "input": "会员账号"},
    {"action": "add_proxy_whitelist", "name": "代理 IP 加白", "input": "IPv4地址"},
    {"action": "migrate_milan", "name": "迁移米兰", "input": "会员账号"},
]

# 后台操作指令队列（Chrome 扩展轮询取指令）
CMD_SECRET = "J7kN3mQxR9vTsW2pYzBf"
pending_commands = deque(maxlen=200)
pending_command_leases = {}
backend_command_results = {}
backend_command_progress = {}
backend_command_lock = threading.RLock()
pending_command_condition = threading.Condition(backend_command_lock)
BACKEND_COMMAND_LEASE_SECONDS = 300

SITE_MESSAGE_PROFILES = {
    "9zc": {
        "site": "9",
        "name": "9站新注册",
        "steps": [
            {"template_id": 259, "msg_type": 1, "icon_url": "17", "clients": "0,1,2,3,8,9"},
            {"template_id": 260, "msg_type": 2, "icon_url": "18", "clients": "0,1,2,3,8,9"},
            {"template_id": 234, "msg_type": 2, "icon_url": "18", "clients": "0,1,2,3,8,9"},
            {"template_id": 233, "msg_type": 2, "icon_url": "18", "clients": "0,1,2,3,8,9"},
            {"template_id": 232, "msg_type": 2, "icon_url": "18", "clients": "0,1,2,3,8,9"},
            {"template_id": 229, "msg_type": 3, "icon_url": "13", "clients": "0,1,2,3,8,9"},
        ],
    },
    "6zc": {
        "site": "6",
        "name": "6站新注册",
        "steps": [
            {"template_id": 262, "msg_type": 1, "icon_url": "12", "clients": "0,1,2,3,8"},
            {"template_id": 232, "msg_type": 2, "icon_url": "14", "clients": "0,1,2,3,8"},
            {"template_id": 229, "msg_type": 3, "icon_url": "9", "clients": "0,1,2,3,8"},
        ],
    },
}

def _prune_backend_command_maps_locked():
    while len(backend_command_results) > 500:
        backend_command_results.pop(next(iter(backend_command_results)), None)
    while len(backend_command_progress) > 500:
        backend_command_progress.pop(next(iter(backend_command_progress)), None)

def enqueue_pending_command(command, left=False):
    with pending_command_condition:
        if left:
            pending_commands.appendleft(command)
        else:
            pending_commands.append(command)
        pending_command_condition.notify()

def normalize_backend_action(action):
    action = str(action or "unlock_sms").strip()
    aliases = {
        "agent": "agent_existing",
        "agent_existing_capabilities": "agent_existing",
        "existing_agent": "agent_existing",
        "智能体": "agent_existing",
        "Agent编排": "agent_existing",
        "data_overview": "member_data_overview",
        "query_member_data": "member_data_overview",
        "member_data_query": "member_data_overview",
        "查数据": "member_data_overview",
        "数据概览": "member_data_overview",
        "line_query": "query_member_line",
        "query_line": "query_member_line",
        "agent_line_query": "query_member_line",
        "query_agent_line": "query_member_line",
        "查线": "query_member_line",
        "查代理线": "query_member_line",
        "login_device_ip": "query_login_device_ip",
        "query_device_ip": "query_login_device_ip",
        "登录设备": "query_login_device_ip",
        "查询登录设备": "query_login_device_ip",
        "same_ip_device": "query_same_ip_device",
        "query_same_device_ip": "query_same_ip_device",
        "查同IP": "query_same_ip_device",
        "查同ip": "query_same_ip_device",
        "查同设备": "query_same_ip_device",
        "venue_turnover": "query_venue_turnover",
        "venue_turnover_lock": "query_venue_turnover",
        "query_venue_turnover_lock": "query_venue_turnover",
        "查场馆流水": "query_venue_turnover",
        "流水锁定": "query_venue_turnover",
        "rebate_config": "configure_rebate",
        "configure_rebate_rate": "configure_rebate",
        "配置返水": "configure_rebate",
        "ticket_cancel_reason": "query_ticket_cancel_reason",
        "ticket_failure_reason": "query_ticket_cancel_reason",
        "query_ticket_failure_reason": "query_ticket_cancel_reason",
        "invalid_ticket_reason": "query_ticket_cancel_reason",
        "query_invalid_ticket_reason": "query_ticket_cancel_reason",
        "注单取消原因": "query_ticket_cancel_reason",
        "取消原因": "query_ticket_cancel_reason",
        "失败原因": "query_ticket_cancel_reason",
        "无效原因": "query_ticket_cancel_reason",
        "投注失败": "query_ticket_cancel_reason",
    }
    action = aliases.get(action, action)
    return action if action in BACKEND_UNLOCK_ACTIONS else "unlock_sms"

def queue_site_inner_message_command(members, title=None, content=None, source="bot_private", strategy="sb"):
    clean_members = []
    seen = set()
    for member in members or []:
        name = str(member or "").strip().lower()
        if not name or name in seen:
            continue
        seen.add(name)
        clean_members.append(name)
    if not clean_members:
        raise ValueError("账号列表为空")
    strategy = str(strategy or "sb").strip().lower()
    profile = SITE_MESSAGE_PROFILES.get(strategy)
    backend_site = (profile or {}).get("site") or "9"
    cmd_id = f"site_msg_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    command = {
        "id": cmd_id,
        "action": "send_site_inner_msg",
        "member_name": ",".join(clean_members[:5]) + ("..." if len(clean_members) > 5 else ""),
        "target_value": ",".join(clean_members),
        "members": clean_members,
        "site_message_strategy": strategy,
        "backend_site": backend_site,
        "template_id": 243,
        "title": title or "【存款温馨提示】",
        "content": content or "系统检测到您的存款订单已取消，为了让您的存款更加通畅，请您使用银联支付的方式存款，联系私人专属经理，申请更高彩金活动加赠！ 👉如无私人专属经理，截图此条消息，联系在线客服发送：“申请专属经理”，享更多优惠～",
        "source": source,
        "queued_at": now_bj().isoformat(),
    }
    if profile:
        command.update({
            "site_message_strategy_name": profile["name"],
            "site_message_steps": profile["steps"],
            "step_delay_min": 5,
            "step_delay_max": 8,
        })
    enqueue_pending_command(command)
    return cmd_id, clean_members

def normalize_reply_steps(raw_replies):
    clean_replies = []
    if not isinstance(raw_replies, list):
        return clean_replies
    for r in raw_replies:
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
        r["member_pattern"] = str(r.get("member_pattern", "") or "").strip()
        r["ip_pattern"] = str(r.get("ip_pattern", "") or "").strip()
        r["backend_action"] = normalize_backend_action(r.get("backend_action", "unlock_sms"))
        if r.get("type") == "backend_unlock" and r["backend_action"] == "agent_existing":
            r["type"] = "agent_orchestrator"
        r["telegram_account"] = str(r.get("telegram_account", "") or "").strip()
        r["fail_notify_to"] = str(r.get("fail_notify_to", "") or "").strip()
        r["fail_notify_text"] = str(r.get("fail_notify_text", "") or "").strip()
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
    return clean_replies

def queue_backend_unlock_command(target_value, rule, event, action="unlock_sms", step=None, ai_parse=None):
    cmd_id = f"unlock_{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
    action = normalize_backend_action(action)
    ai_parse = ai_parse if isinstance(ai_parse, dict) else {}
    command = {
        "id": cmd_id,
        "action": action,
        "member_name": target_value,
        "target_value": target_value,
        "source": "monitor",
        "rule": rule.get("name") or rule.get("id") or "",
        "chat_id": event.chat_id,
        "message_id": event.id,
        "source_text": getattr(event, "text", None) or getattr(event, "raw_text", "") or "",
        "queued_at": now_bj().isoformat(),
    }
    if ai_parse:
        command["ai_parse"] = {
            key: ai_parse.get(key)
            for key in (
                "target", "members", "agent_codes", "data_fields", "startAt", "endAt",
                "private_reply", "telegram_account", "agent_code", "venue", "game",
                "site", "line_mode", "order_nos", "urge_batch_id", "urge_batch_total"
            )
            if ai_parse.get(key) not in (None, "", [])
        }
    for list_key in ("members", "agent_codes"):
        if ai_parse.get(list_key):
            command[list_key] = ai_parse[list_key]
    for text_key in ("venue", "game", "site", "line_mode"):
        if ai_parse.get(text_key):
            command[text_key] = ai_parse[text_key]
    if ai_parse.get("site"):
        command["backend_site"] = ai_parse["site"]
    if action == "member_data_overview":
        if ai_parse.get("data_fields"):
            command["data_fields"] = ai_parse["data_fields"]
        if ai_parse.get("startAt"):
            command["startAt"] = ai_parse["startAt"]
        if ai_parse.get("endAt"):
            command["endAt"] = ai_parse["endAt"]
        if ai_parse.get("agent_code"):
            command["agent_code"] = ai_parse["agent_code"]
    if action in {"urge_settlement", "query_ticket_cancel_reason"}:
        telegram_account = str((step or {}).get("telegram_account", "") or "").strip()
        parsed_account = str(ai_parse.get("telegram_account") or "").strip()
        resolved_account = resolve_client_name(parsed_account)
        if resolved_account:
            telegram_account = resolved_account
        command.update({
            "backend_site": "merchant",
            "orderNo": target_value,
        })
        if action == "urge_settlement":
            command.update({
                "telegram_target": str((step or {}).get("forward_to", "") or "").strip(),
                "telegram_account": telegram_account,
                "telegram_template": str((step or {}).get("text", "") or "").strip(),
            })
            if ai_parse.get("order_nos"):
                command["order_nos"] = ai_parse["order_nos"]
            if ai_parse.get("urge_batch_id"):
                command["urge_batch_id"] = ai_parse["urge_batch_id"]
            if ai_parse.get("urge_batch_total"):
                command["urge_batch_total"] = ai_parse["urge_batch_total"]
    if action == "unlock_sms":
        unlock_value = os.environ.get("ZD_SMS_UNLOCK_VALUE", "").strip()
        if unlock_value:
            command["value"] = unlock_value
    enqueue_pending_command(command)
    return cmd_id

def _lease_next_pending_command_locked():
    now = time.time()
    for cmd_id, leased in list(pending_command_leases.items()):
        if now - float(leased.get("leased_at", 0)) > BACKEND_COMMAND_LEASE_SECONDS:
            pending_command_leases.pop(cmd_id, None)
            pending_commands.appendleft(leased["cmd"])
    if not pending_commands:
        return None
    cmd = pending_commands.popleft()
    cmd_id = cmd.get("id") or f"cmd_{int(now * 1000)}"
    cmd["id"] = cmd_id
    pending_command_leases[cmd_id] = {"cmd": cmd, "leased_at": now}
    return cmd

def lease_next_pending_command():
    with pending_command_condition:
        return _lease_next_pending_command_locked()

def wait_for_pending_command(wait_seconds=0.0):
    deadline = time.monotonic() + max(0.0, float(wait_seconds or 0.0))
    with pending_command_condition:
        while True:
            cmd = _lease_next_pending_command_locked()
            if cmd:
                return cmd
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            pending_command_condition.wait(remaining)

async def wait_backend_command_result(cmd_id, timeout=90.0):
    deadline = time.time() + max(1.0, float(timeout or 90.0))
    while time.time() < deadline:
        with backend_command_lock:
            result = backend_command_results.pop(cmd_id, None)
        if result:
            return result
        await asyncio.sleep(0.2)
    with backend_command_lock:
        pending_command_leases.pop(cmd_id, None)
    return {"status": "timeout", "detail": "等待后台回执超时"}

def get_backend_command_progress(cmd_id):
    with backend_command_lock:
        return dict(backend_command_progress.get(str(cmd_id or ""), {}) or {})

def backend_result_ok(result):
    return str((result or {}).get("status") or "") in {"success", "reply_origin"}

def normalize_urge_settlement_reply(text):
    raw = str(text or "").strip()
    if not raw:
        return raw
    if "当前注单仍有未结算场次" in raw or "仍有未结算场次" in raw:
        return "赛果核实中，已催促，核实完毕后会进行结算，请耐心等待。"
    return raw

def format_backend_reply_items(reply_items, backend_action=""):
    clean = [(str(target or "").strip(), str(text or "").strip()) for target, text in reply_items]
    clean = [(target, text) for target, text in clean if text]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0][1]
    if backend_action in {"query_member_line", "query_login_device_ip", "query_same_ip_device", "query_venue_turnover", "configure_rebate"}:
        return "\n".join(text for _target, text in clean)
    if backend_action != "urge_settlement":
        return "\n".join(f"{target}：{text}" for target, text in clean)

    groups = []
    by_text = {}
    for target, text in clean:
        if text not in by_text:
            by_text[text] = []
            groups.append((text, by_text[text]))
        if target and target not in by_text[text]:
            by_text[text].append(target)

    blocks = []
    for text, targets in groups:
        if targets:
            blocks.append(f"{chr(10).join(targets)}\n以上注单{text}")
        else:
            blocks.append(text)
    return "\n\n".join(blocks)

def humanize_backend_failure_detail(detail, action=""):
    text = str(detail or "").strip()
    compact = re.sub(r"\s+", " ", text)
    if not compact:
        return "后台处理失败"
    if "代理编码不正确" in compact or "上级代理编号不匹配" in compact:
        return "代理编码不正确，请核实"
    if "未提取到会员账号" in compact or "会员账号为空" in compact:
        return "未提取到会员账号"
    if "未找到会员" in compact or "会员不存在" in compact:
        return "会员不存在"
    if "所有场馆账号均未找到注单" in compact or "未找到注单" in compact:
        return "未找到注单"
    if "未到开赛时间" in compact or "注单未开赛" in compact:
        return "注单未开赛"
    if "所有场馆账号登录失效" in compact or "场馆登录失效" in compact:
        return "场馆登录失效"
    if "9站未登录" in compact:
        return "9站未登录"
    if "6站未登录" in compact:
        return "6站未登录"
    if "未登录" in compact:
        if action in {"urge_settlement", "merchant_order_statistics", "query_ticket_cancel_reason"}:
            return "场馆登录失效"
        return "后台未登录"
    if "扩展已重新加载" in compact or "拓展已重新加载" in compact or "刷新对应后台页面" in compact or "登录态同步" in compact:
        return "拓展未同步登录态"
    if "Failed to fetch" in compact or "Load failed" in compact or "请求失败" in compact or re.search(r"HTTP\s*[45]\d\d", compact):
        label = "场馆接口请求失败" if action in {"urge_settlement", "merchant_order_statistics", "query_ticket_cancel_reason"} or "api-merchant-backstage" in compact else "后台接口请求失败"
        host_label = ""
        url_match = re.search(r"https?://([^\s/:。；]+)(/[^\s。；]*)?", compact)
        if url_match:
            host = url_match.group(1)
            path = (url_match.group(2) or "").split("?")[0]
            parts = [item for item in path.split("/") if item]
            endpoint = "/".join(parts[-2:]) if parts else host
            if "9sitebg" in host:
                label = "9站接口请求失败"
            elif "6sitebg" in host:
                label = "6站接口请求失败"
            elif "api-merchant-backstage" in host:
                label = "场馆接口请求失败"
            host_label = endpoint or host
        transport = ""
        if "Failed to fetch" in compact:
            transport = "Failed to fetch"
        elif "Load failed" in compact:
            transport = "Load failed"
        else:
            http_match = re.search(r"HTTP\s*[45]\d\d", compact)
            transport = http_match.group(0) if http_match else ""
        suffix = "，".join([item for item in (host_label, transport) if item])
        return f"{label}：{suffix}"[:120] if suffix else label
    if "TG发送失败" in compact or "send_telegram" in compact or "未配置催结算TG群" in compact:
        return "TG发送失败"
    if "超时" in compact or "timeout" in compact.lower():
        return "后台处理超时"
    return compact.splitlines()[0][:120]

async def notify_backend_failure(step, rule, event, target_value, action, result):
    notify_target = parse_peer_target((step or {}).get("fail_notify_to"))
    if not notify_target:
        return False
    status = str((result or {}).get("status") or "failed")
    detail = humanize_backend_failure_detail((result or {}).get("detail") or "", action)
    default_text = "后台自动处理失败，请人工核查。\n规则：{rule}\n动作：{action}\n目标：{target}\n状态：{status}\n原因：{detail}"
    tpl = (step or {}).get("fail_notify_text") or default_text
    text = format_bot_notice(tpl, event, rule, "")
    replacements = {
        "{target}": str(target_value or ""),
        "{action}": command_action_label(action),
        "{status}": status,
        "{detail}": detail[:500],
        "{reason}": detail[:500],
    }
    for key, value in replacements.items():
        text = text.replace(key, value)
    await send_bot_notice(notify_target, text)
    return True

async def execute_backend_unlock_step(step, rule, event, source_text, target_client=None):
    backend_action = normalize_backend_action((step or {}).get("backend_action", "unlock_sms"))
    if backend_action == "agent_existing":
        return await execute_agent_existing_step(step, rule, event, source_text, target_client=target_client)
    ai_parse = {}
    if backend_action in {"urge_settlement", "query_ticket_cancel_reason"}:
        target_values = extract_backend_order_nos(source_text or "", (step or {}).get("member_pattern", ""))
        target_value = target_values[0] if target_values else ""
        if not target_values:
            ai_parse = await parse_backend_message_with_ai(source_text or "", backend_action, str((rule or {}).get("name") or ""))
            target_value = str((ai_parse or {}).get("target") or "").strip() or extract_backend_target(source_text or "", step)
            target_values = [target_value] if target_value else []
    else:
        ai_parse = await parse_backend_message_with_ai(source_text or "", backend_action, str((rule or {}).get("name") or ""))
        target_value = str((ai_parse or {}).get("target") or "").strip() or extract_backend_target(source_text or "", step)
        target_values = [target_value] if target_value else []
    if backend_action in {"urge_settlement", "query_ticket_cancel_reason"}:
        seen_targets = set()
        clean_targets = []
        for value in [*target_values, str((ai_parse or {}).get("target") or "").strip()]:
            if value and value not in seen_targets:
                seen_targets.add(value)
                clean_targets.append(value)
        target_values = clean_targets
        if not target_values and target_value:
            target_values = [target_value]

    if not target_values:
        result = {"status": "no_target", "detail": "未提取到目标值"}
        await notify_backend_failure(step, rule, event, "", backend_action, result)
        raise RuntimeError(f"后台动作未提取到目标值：{command_action_label(backend_action)}")

    urge_batch_id = ""
    if backend_action == "urge_settlement" and len(target_values) > 1:
        urge_batch_id = f"{getattr(event, 'chat_id', '')}:{getattr(event, 'id', '')}:{','.join(target_values)}"

    async def run_one_backend_target(target_value):
        target_ai_parse = dict(ai_parse or {})
        if urge_batch_id:
            target_ai_parse.update({
                "order_nos": target_values,
                "urge_batch_id": urge_batch_id,
                "urge_batch_total": len(target_values),
            })
        cmd_id = queue_backend_unlock_command(target_value, rule, event, backend_action, step=step, ai_parse=target_ai_parse)
        logger.info(f"🔓 [BackendUnlock] 规则 '{rule.get('name')}' 已下发后台指令: {backend_action} {target_value} | id={cmd_id}")
        result = await wait_backend_command_result(cmd_id, timeout=BACKEND_COMMAND_TIMEOUT)
        if not backend_result_ok(result):
            notified = await notify_backend_failure(step, rule, event, target_value, backend_action, result)
            return "failure", (target_value, result, notified)
        return "success", (target_value, cmd_id, result)

    if len(target_values) > 1 and backend_action in {"urge_settlement", "query_ticket_cancel_reason"}:
        results = await asyncio.gather(*(run_one_backend_target(target_value) for target_value in target_values))
    else:
        results = [await run_one_backend_target(target_value) for target_value in target_values]

    successes = [item for kind, item in results if kind == "success"]
    failures = [item for kind, item in results if kind == "failure"]

    if not successes:
        target_text = "、".join(target_values)
        notified = any(item[2] for item in failures)
        status = (failures[0][1] or {}).get("status") if failures else "failed"
        suffix = "，已通知人工核查" if notified else "，未配置失败通知对象"
        raise RuntimeError(f"后台动作失败：{command_action_label(backend_action)} {target_text} status={status}{suffix}")

    reply_items = []
    for target, _cmd_id, result in successes:
        text = str((result or {}).get("reply_text") or "").strip()
        if backend_action == "add_proxy_whitelist":
            text = agent_success_reply(backend_action, target)
        reply_items.append((target, text))
    if backend_action == "urge_settlement":
        reply_items = [(target, normalize_urge_settlement_reply(text)) for target, text in reply_items]
    reply_items = [(target, text) for target, text in reply_items if text]
    if reply_items and target_client:
        await asyncio.sleep(random_delay_from_step(step or {}, "result_reply_min", "result_reply_max", 1.8, 3.8))
        private_reply = member_data_private_requested(source_text) or bool((ai_parse or {}).get("private_reply"))
        if backend_action == "member_data_overview" and private_reply and not reply_items[0][1].startswith("代理编码不正确"):
            sender_id = getattr(event, "sender_id", None)
            if not sender_id:
                raise RuntimeError("无法识别原消息发送者，不能私发查数据结果")
            await target_client.send_message(sender_id, reply_items[0][1])
            await target_client.send_message(event.chat_id, "已发", reply_to=event.id)
        else:
            reply_text = format_backend_reply_items(reply_items, backend_action)
            sent = await target_client.send_message(event.chat_id, reply_text, reply_to=event.id)
            if global_main_handler:
                asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
    if failures:
        logger.warning(f"⚠️ [BackendUnlock] 规则 '{rule.get('name')}' 部分后台动作失败: {backend_action} failures={len(failures)} success={len(successes)}")
    logger.info(f"✅ [BackendUnlock] 规则 '{rule.get('name')}' 后台动作成功: {backend_action} targets={','.join(t for t, _id, _r in successes)}")
    return {
        "id": successes[-1][1],
        "stop_actions": any(bool((result or {}).get("stop_actions")) for _target, _cmd_id, result in successes)
    }

def command_action_label(action):
    if action == "agent_existing":
        return "Agent 编排"
    if action == "add_proxy_whitelist":
        return "代理 IP 加白"
    if action == "clear_login_error":
        return "登录密码试错限制"
    if action == "migrate_milan":
        return "迁移米兰"
    if action == "member_data_overview":
        return "查数据"
    if action == "query_member_line":
        return "查线"
    if action == "query_login_device_ip":
        return "查登录设备/IP"
    if action == "query_same_ip_device":
        return "查同IP/设备"
    if action == "query_venue_turnover":
        return "查场馆流水锁定"
    if action == "configure_rebate":
        return "配置返水"
    if action == "urge_settlement":
        return "催结算"
    if action == "query_ticket_cancel_reason":
        return "注单取消/失败原因"
    return "短信/验证码限制"

def member_data_private_requested(text):
    return "私发" in str(text or "").replace(" ", "")

def agent_capabilities_prompt():
    return json.dumps(AGENT_CAPABILITIES, ensure_ascii=False, indent=2)

def build_ai_agent_plan_prompt(text, rule_name="", previous_error=""):
    today = now_bj().strftime("%Y-%m-%d")
    return f"""
你是 ZD 回群 Agent 的任务规划器，只能输出一个 JSON 对象，不要解释。

当前日期：{today}
规则名称：{rule_name or "-"}

你只能使用下面已注册能力，不能创造新能力：
{agent_capabilities_prompt()}

原始群消息：
{trim_zd_ai_text(text)}

输出 JSON schema：
{{
  "ok": true,
  "tasks": [
    {{
      "action": "member_data_overview",
      "target": "",
      "members": [],
      "member": "",
      "order_no": "",
      "ip": "",
      "agent_codes": [],
      "data_fields": [],
      "startAt": "",
      "endAt": "",
      "private_reply": false,
      "telegram_account": "",
      "agent_code": "",
      "venue": "",
      "game": "",
      "site": "",
      "line_mode": "",
      "reason": ""
    }}
  ],
  "unsupported": [
    {{"label": "", "member": "", "reason": ""}}
  ],
  "private_reply": false,
  "reason": ""
}}

规则：
- tasks 只能放已注册能力的 action。
- 用户一句话有多个诉求时，拆成多个 task。
- 用户要求的事项没有对应能力时，放到 unsupported，不能放进 tasks。
- 不要因为讨好用户而把不支持事项说成已处理。
- 查数据 data_fields 只能从 总输赢、总流水、总存款、总提款、总红利、总返水 中选择，顺序必须跟原消息要求一致；没明确字段时返回空数组。
- 只有原文明确出现“查总输赢/查总流水/总存款/总提款/总红利/总返水”等查数据字段，才允许创建 member_data_overview；会员账号只是辅助信息时不能查数据。
- 催结算 order_no/target 必须是12到24位注单号；代理加白 ip/target 必须是IPv4；账号类 member/target 填会员账号。
- 催结算只处理“未结算/一直不结算/催促结算/催结算/结算回滚”等要求推进结算的消息；结算回滚不是已结算。
- 注单取消/失败/无效原因必须使用 query_ticket_cancel_reason，不要使用 urge_settlement。
- 只要消息包含“取消原因、无效原因、失败原因、投注失败、注单取消、无效、取消、失败”，即使同时出现“催”，也使用 query_ticket_cancel_reason。
- 查线/登录设备/查同IP设备可以把多个会员放 members，把消息里的合营代码放 agent_codes；查代理线 line_mode 填 agent。
- 查询场馆流水 target/member 填会员账号，venue 填场馆名，例如 米兰体育。
- 配置返水 venue 填场馆名，game 填游戏名；site 只在明确 6站/JN 或 9站/ML 时填写 6001/9001。
- private_reply 只有明确要求私发/私聊/发我时才为 true。
- agent_code 只有消息里明确提供上级代理编号时填写。
{f"上次规划失败原因：{previous_error}" if previous_error else ""}
""".strip()

def call_zd_agent_plan_once(text, rule_name="", previous_error=""):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 未配置")
    prompt = build_ai_agent_plan_prompt(text, rule_name=rule_name, previous_error=previous_error)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json",
            "maxOutputTokens": ZD_AGENT_MAX_OUTPUT_TOKENS,
            "temperature": 0
        }
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        gemini_generate_content_url(),
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": GEMINI_API_KEY,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=ZD_AI_PARSE_TIMEOUT) as resp:
        status = getattr(resp, "status", resp.getcode())
        resp_text = resp.read().decode("utf-8", errors="replace")
    if status < 200 or status >= 300:
        raise RuntimeError(f"AI HTTP {status}: {resp_text[:200]}")
    data = json.loads(resp_text)
    raw_content = (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text", "")
    return extract_json_object(raw_content)

def normalize_agent_task_action(action):
    raw = str(action or "").strip()
    aliases = {
        "data_overview": "member_data_overview",
        "query_member_data": "member_data_overview",
        "member_data_query": "member_data_overview",
        "查数据": "member_data_overview",
        "数据概览": "member_data_overview",
        "line_query": "query_member_line",
        "query_line": "query_member_line",
        "agent_line_query": "query_member_line",
        "query_agent_line": "query_member_line",
        "查线": "query_member_line",
        "查代理线": "query_member_line",
        "login_device_ip": "query_login_device_ip",
        "query_device_ip": "query_login_device_ip",
        "登录设备": "query_login_device_ip",
        "查询登录设备": "query_login_device_ip",
        "same_ip_device": "query_same_ip_device",
        "query_same_device_ip": "query_same_ip_device",
        "查同IP": "query_same_ip_device",
        "查同ip": "query_same_ip_device",
        "查同设备": "query_same_ip_device",
        "venue_turnover": "query_venue_turnover",
        "venue_turnover_lock": "query_venue_turnover",
        "query_venue_turnover_lock": "query_venue_turnover",
        "查场馆流水": "query_venue_turnover",
        "流水锁定": "query_venue_turnover",
        "rebate_config": "configure_rebate",
        "configure_rebate_rate": "configure_rebate",
        "配置返水": "configure_rebate",
        "settlement_urge": "urge_settlement",
        "urge_settle": "urge_settlement",
        "urge_settlement_order": "urge_settlement",
        "催结算": "urge_settlement",
        "ticket_cancel_reason": "query_ticket_cancel_reason",
        "ticket_failure_reason": "query_ticket_cancel_reason",
        "query_ticket_failure_reason": "query_ticket_cancel_reason",
        "invalid_ticket_reason": "query_ticket_cancel_reason",
        "query_invalid_ticket_reason": "query_ticket_cancel_reason",
        "注单取消原因": "query_ticket_cancel_reason",
        "取消原因": "query_ticket_cancel_reason",
        "失败原因": "query_ticket_cancel_reason",
        "无效原因": "query_ticket_cancel_reason",
        "投注失败": "query_ticket_cancel_reason",
        "代理加白": "add_proxy_whitelist",
        "代理IP加白": "add_proxy_whitelist",
        "短信解锁": "unlock_sms",
        "验证码解锁": "unlock_sms",
        "登录限制": "clear_login_error",
        "迁移米兰": "migrate_milan",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in AGENT_EXECUTABLE_ACTIONS else ""

def sanitize_agent_task(raw_task):
    if not isinstance(raw_task, dict):
        return None
    action = normalize_agent_task_action(raw_task.get("action") or "")
    if not action:
        return None
    member = str(raw_task.get("member") or "").strip().lower()
    target = str(raw_task.get("target") or "").strip()
    order_no = str(raw_task.get("order_no") or "").strip()
    ip = str(raw_task.get("ip") or "").strip()
    source_text = str(raw_task.get("source_text") or raw_task.get("text") or "")
    members = clean_agent_members(raw_task.get("members"), source_text)
    if member and member not in members and is_likely_agent_member(member):
        members.insert(0, member)
    elif target and target not in members and is_likely_agent_member(target):
        members.insert(0, target.lower())
    agent_codes = clean_agent_codes(raw_task.get("agent_codes"), source_text)
    if raw_task.get("agent_code"):
        agent_codes = clean_agent_codes([raw_task.get("agent_code"), *agent_codes])
    if action in {"urge_settlement", "query_ticket_cancel_reason"}:
        target = order_no or target
        if not re.fullmatch(r"\d{12,24}", target):
            return None
    elif action == "add_proxy_whitelist":
        target = ip or target
        if not re.fullmatch(BACKEND_PROXY_IP_PATTERN, target):
            return None
    elif action == "configure_rebate":
        target = str(raw_task.get("game") or target or "配置返水").strip()
        if not target:
            return None
    elif action in {"query_member_line", "query_login_device_ip", "query_same_ip_device", "query_venue_turnover"}:
        if not members and member and re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, member):
            members = [member]
        target = (members[0] if members else (member or target).lower())
        if not target or not re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, target):
            return None
    else:
        target = (member or target).lower()
        if not re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, target):
            return None
    site = str(raw_task.get("site") or "").strip().lower()
    if site in {"6", "6站", "jn", "6001"}:
        site = "6001"
    elif site in {"9", "9站", "ml", "9001"}:
        site = "9001"
    else:
        site = ""
    return {
        "action": action,
        "target": target,
        "member": members[0] if members else member,
        "members": members,
        "order_no": order_no,
        "ip": ip,
        "agent_codes": agent_codes,
        "data_fields": clean_ai_data_fields(raw_task.get("data_fields")),
        "startAt": valid_iso_date(raw_task.get("startAt")),
        "endAt": valid_iso_date(raw_task.get("endAt")),
        "private_reply": bool(raw_task.get("private_reply")),
        "telegram_account": str(raw_task.get("telegram_account") or "").strip(),
        "agent_code": str(raw_task.get("agent_code") or "").strip() if re.fullmatch(r"\d{5,12}", str(raw_task.get("agent_code") or "").strip()) else "",
        "venue": str(raw_task.get("venue") or raw_task.get("venue_name") or "").strip(),
        "game": str(raw_task.get("game") or raw_task.get("game_name") or "").strip(),
        "site": site,
        "line_mode": str(raw_task.get("line_mode") or "").strip(),
        "reason": str(raw_task.get("reason") or "").strip(),
    }

def agent_plan_fallback(text):
    source_text = str(text or "")
    tasks = []
    seen = set()
    def add_task(task):
        if not task:
            return
        key = (task["action"], task["target"], tuple(task.get("data_fields") or []))
        if key in seen:
            return
        seen.add(key)
        tasks.append(task)

    for order_no in extract_backend_order_nos(source_text, ""):
        if looks_like_ticket_reason_request(source_text):
            add_task(sanitize_agent_task({"action": "query_ticket_cancel_reason", "order_no": order_no, "target": order_no}))
        elif looks_like_short_urge_settlement(source_text):
            add_task(sanitize_agent_task({"action": "urge_settlement", "order_no": order_no, "target": order_no}))

    if re.search(r"加白|白名单|代理IP|代理\s*IP", source_text, flags=re.IGNORECASE):
        ip = extract_backend_proxy_ip(source_text, "")
        add_task(sanitize_agent_task({"action": "add_proxy_whitelist", "ip": ip, "target": ip}))

    members = clean_agent_members([], source_text)
    agent_codes = clean_agent_codes([], source_text)
    site = agent_site_hint_from_text(source_text)
    venue = agent_venue_hint_from_text(source_text)

    if re.search(r"查同\s*ip|查同IP|查同设备|同设备", source_text, flags=re.IGNORECASE) and members:
        add_task(sanitize_agent_task({
            "action": "query_same_ip_device",
            "member": members[0],
            "target": members[0],
            "members": members,
            "agent_codes": agent_codes,
            "source_text": source_text,
            "site": site,
        }))

    if re.search(r"登录设备|设备\s*ip|ip\s*在哪|IP\s*在哪", source_text, flags=re.IGNORECASE) and members:
        add_task(sanitize_agent_task({
            "action": "query_login_device_ip",
            "member": members[0],
            "target": members[0],
            "members": members,
            "agent_codes": agent_codes,
            "source_text": source_text,
            "site": site,
        }))

    if re.search(r"查代理线|查线", source_text) and members:
        add_task(sanitize_agent_task({
            "action": "query_member_line",
            "member": members[0],
            "target": members[0],
            "members": members,
            "agent_codes": agent_codes,
            "source_text": source_text,
            "line_mode": "agent" if "查代理线" in source_text else "",
            "site": site,
        }))

    if re.search(r"流水.*还差多少|场馆.*流水|流水锁定", source_text) and members:
        add_task(sanitize_agent_task({
            "action": "query_venue_turnover",
            "member": members[0],
            "target": members[0],
            "members": [members[0]],
            "venue": venue,
            "source_text": source_text,
            "site": site,
        }))

    if "配置返水" in source_text or "返水配置" in source_text:
        game = agent_game_hint_from_text(source_text)
        add_task(sanitize_agent_task({
            "action": "configure_rebate",
            "target": game or "配置返水",
            "venue": venue,
            "game": game,
            "source_text": source_text,
            "site": site,
        }))

    fields = data_overview_fields_from_text(source_text)
    if fields:
        member = extract_backend_unlock_member(source_text, "")
        add_task(sanitize_agent_task({"action": "member_data_overview", "member": member, "target": member, "data_fields": fields}))

    return {"ok": bool(tasks), "tasks": tasks, "unsupported": [], "private_reply": member_data_private_requested(source_text), "reason": ""}

def sanitize_agent_plan(raw, source_text=""):
    if not isinstance(raw, dict):
        raise ValueError("AI返回不是对象")
    if raw.get("ok") is False:
        raise ValueError(str(raw.get("reason") or "AI表示无法规划"))
    tasks = []
    seen = set()
    data_task_index = {}
    for item in raw.get("tasks") if isinstance(raw.get("tasks"), list) else []:
        task = sanitize_agent_task(item)
        if not task:
            continue
        if task["action"] == "member_data_overview":
            source_fields = data_overview_fields_from_text(source_text)
            if not source_fields:
                logger.info(f"🛡️ [ZD-Agent] 丢弃无字段查数据任务: target={task.get('target')}")
                continue
            task["data_fields"] = source_fields
            data_key = (task["action"], task["target"], task.get("startAt"), task.get("endAt"))
            existing_index = data_task_index.get(data_key)
            if existing_index is not None:
                merged_fields = clean_ai_data_fields([
                    *(tasks[existing_index].get("data_fields") or []),
                    *(task.get("data_fields") or []),
                ])
                tasks[existing_index]["data_fields"] = merged_fields
                tasks[existing_index]["private_reply"] = tasks[existing_index].get("private_reply") or task.get("private_reply")
                continue
            data_task_index[data_key] = len(tasks)
        key = (task["action"], task["target"], tuple(task.get("data_fields") or []), task.get("startAt"), task.get("endAt"))
        if key in seen:
            continue
        seen.add(key)
        tasks.append(task)
        if len(tasks) >= 8:
            break

    unsupported = []
    for item in raw.get("unsupported") if isinstance(raw.get("unsupported"), list) else []:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()[:40]
        reason = str(item.get("reason") or "").strip()[:120]
        member = str(item.get("member") or "").strip().lower()
        if label or reason:
            unsupported.append({"label": label or "该项", "member": member, "reason": reason})
        if len(unsupported) >= 4:
            break

    if not tasks and not unsupported:
        fallback = agent_plan_fallback(source_text)
        tasks = fallback["tasks"]
    return {
        "ok": bool(tasks or unsupported),
        "tasks": tasks,
        "unsupported": unsupported,
        "private_reply": bool(raw.get("private_reply")) or member_data_private_requested(source_text),
        "reason": str(raw.get("reason") or "").strip(),
    }

def parse_agent_plan_sync(text, rule_name=""):
    if not zd_ai_parse_available():
        return agent_plan_fallback(text)
    last_error = ""
    for attempt in range(ZD_AI_PARSE_RETRIES + 1):
        try:
            raw = call_zd_agent_plan_once(text, rule_name=rule_name, previous_error=last_error)
            plan = sanitize_agent_plan(raw, text)
            logger.info(f"🤖 [ZD-Agent] 规划成功 tasks={len(plan.get('tasks') or [])} unsupported={len(plan.get('unsupported') or [])} attempt={attempt + 1}")
            return plan
        except Exception as e:
            last_error = str(e)
            logger.warning(f"⚠️ [ZD-Agent] 规划失败 attempt={attempt + 1}/{ZD_AI_PARSE_RETRIES + 1}: {last_error}")
            if attempt < ZD_AI_PARSE_RETRIES:
                time.sleep(min(2.0, 0.4 * (attempt + 1)))
    logger.warning(f"⚠️ [ZD-Agent] 多次规划失败，回退规则: {last_error}")
    return agent_plan_fallback(text)

async def parse_agent_plan(text, rule_name=""):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: parse_agent_plan_sync(text, rule_name))

def agent_success_reply(action, target):
    if action == "add_proxy_whitelist":
        return f"{target} 已加白。"
    if action == "migrate_milan":
        return f"{target} 已提交迁移。"
    if action in {"unlock_sms", "clear_login_error"}:
        return f"{target} 已处理。"
    return ""

def merge_agent_reply_blocks(blocks):
    clean = [str(block or "").strip() for block in blocks if str(block or "").strip()]
    if not clean:
        return ""
    if len(clean) == 1:
        return clean[0]
    merged = []
    used = [False] * len(clean)
    for idx, block in enumerate(clean):
        if used[idx]:
            continue
        lines = block.splitlines()
        if len(lines) >= 1 and re.fullmatch(BACKEND_UNLOCK_ACCOUNT_PATTERN, lines[0].strip(), flags=re.IGNORECASE):
            member = lines[0].strip().lower()
            merged_lines = list(lines)
            seen_lines = {line.strip() for line in merged_lines if line.strip()}
            extra_lines = []
            for jdx in range(idx + 1, len(clean)):
                other_lines = clean[jdx].splitlines()
                if other_lines and other_lines[0].strip().lower() == member:
                    for line in (other_lines[1:] or other_lines):
                        compact = line.strip()
                        if not compact or compact in seen_lines:
                            continue
                        seen_lines.add(compact)
                        extra_lines.append(line)
                    used[jdx] = True
            merged.append("\n".join([*merged_lines, *extra_lines]).strip())
        else:
            merged.append(block)
        used[idx] = True
    return "\n\n".join(merged)

def agent_handoff_target(step):
    return parse_peer_target(
        (step or {}).get("fail_notify_to")
        or os.environ.get("AGENT_HANDOFF_TO")
        or os.environ.get("AGENT_NOTIFY_TO")
        or os.environ.get("AGENT_REVIEW_TO")
        or ""
    )

def format_agent_task_summary(task):
    action = str((task or {}).get("action") or "")
    target = str((task or {}).get("target") or "")
    members = (task or {}).get("members") or []
    if members and action in {"query_member_line", "query_login_device_ip", "query_same_ip_device"}:
        target = "、".join(members[:6]) + ("..." if len(members) > 6 else "")
    fields = (task or {}).get("data_fields") or []
    extra = f" ({'、'.join(fields)})" if fields else ""
    return f"{command_action_label(action)}：{target}{extra}".strip("：")

def format_agent_unsupported_summary(item):
    label = str((item or {}).get("label") or "未实现能力").strip()
    member = str((item or {}).get("member") or "").strip()
    reason = str((item or {}).get("reason") or "").strip()
    parts = [label]
    if member:
        parts.append(f"会员：{member}")
    if reason:
        parts.append(reason)
    return "，".join(parts)

def format_agent_handoff_notice(rule, event, source_text, tasks, successes, failures, unsupported):
    lines = [
        "Agent需要人工接管。",
        f"规则：{(rule or {}).get('name') or (rule or {}).get('id') or '-'}",
        f"消息ID：{getattr(event, 'id', '-')}",
        f"群ID：{getattr(event, 'chat_id', '-')}",
        "",
        "原消息：",
        str(source_text or "").strip()[:1200] or "-",
    ]
    if tasks:
        lines.extend(["", "已识别能力："])
        lines.extend([f"- {format_agent_task_summary(task)}" for task in tasks])
    if successes:
        lines.extend(["", "已查到结果："])
        for item in successes:
            reply_text = str((item or {}).get("reply_text") or "").strip()
            if reply_text:
                lines.append(reply_text[:1200])
    if failures:
        lines.extend(["", "已识别但查询失败："])
        for item in failures:
            action = (item or {}).get("action")
            target = (item or {}).get("target")
            reason = humanize_backend_failure_detail(((item or {}).get("result") or {}).get("detail") or "", action)
            lines.append(f"- {command_action_label(action)} {target}：{reason}")
    if unsupported:
        lines.extend(["", "未实现能力："])
        lines.extend([f"- {format_agent_unsupported_summary(item)}" for item in unsupported])
    lines.extend(["", "处理方式：先人工回群；需要自动化的话，录制对应后台接口后补能力。"])
    return "\n".join(lines)

async def execute_agent_existing_step(step, rule, event, source_text, target_client=None):
    plan = await parse_agent_plan(source_text or "", str((rule or {}).get("name") or ""))
    tasks = list((plan or {}).get("tasks") or [])
    unsupported = list((plan or {}).get("unsupported") or [])
    if not tasks and not unsupported:
        result = {"status": "no_target", "detail": "Agent未识别到现有能力"}
        await notify_backend_failure(step, rule, event, "", "agent_existing", result)
        raise RuntimeError("Agent未识别到现有能力")

    urge_tasks = [task for task in tasks if task.get("action") == "urge_settlement"]
    if len(urge_tasks) > 1:
        order_nos = [str(task.get("target") or "").strip() for task in urge_tasks if str(task.get("target") or "").strip()]
        batch_id = f"agent:{getattr(event, 'chat_id', '')}:{getattr(event, 'id', '')}:{','.join(order_nos)}"
        for task in urge_tasks:
            task["order_nos"] = order_nos
            task["urge_batch_id"] = batch_id
            task["urge_batch_total"] = len(order_nos)

    async def run_one_agent_task(task):
        action = task["action"]
        target = task["target"]
        cmd_id = queue_backend_unlock_command(target, rule, event, action, step=step, ai_parse=task)
        logger.info(f"🤖 [ZD-Agent] 下发能力: {action} {target} | id={cmd_id}")
        result = await wait_backend_command_result(cmd_id, timeout=BACKEND_COMMAND_TIMEOUT)
        if not backend_result_ok(result):
            await notify_backend_failure(step, rule, event, target, action, result)
            return {"ok": False, "action": action, "target": target, "result": result}
        reply_text = str((result or {}).get("reply_text") or "").strip()
        if action == "urge_settlement":
            reply_text = normalize_urge_settlement_reply(reply_text)
        if not reply_text:
            reply_text = agent_success_reply(action, target)
        return {"ok": True, "action": action, "target": target, "cmd_id": cmd_id, "result": result, "reply_text": reply_text}

    results = await asyncio.gather(*(run_one_agent_task(task) for task in tasks)) if tasks else []
    successes = [item for item in results if item.get("ok")]
    failures = [item for item in results if not item.get("ok")]
    if not successes and tasks and not unsupported:
        status = ((failures[0] or {}).get("result") or {}).get("status") if failures else "failed"
        raise RuntimeError(f"Agent能力执行失败 status={status}")

    blocks = [item.get("reply_text") for item in successes if item.get("reply_text")]
    primary_member = next((task.get("target") for task in tasks if task.get("action") != "add_proxy_whitelist"), "")
    if unsupported:
        notify_target = agent_handoff_target(step)
        if not notify_target:
            result = {"status": "agent_handoff_missing", "detail": "Agent发现未实现能力，但未配置失败通知对象/AGENT_HANDOFF_TO"}
            await notify_backend_failure(step, rule, event, primary_member, "agent_existing", result)
            raise RuntimeError("Agent发现未实现能力，但未配置人工接管通知对象")
        handoff_text = format_agent_handoff_notice(rule, event, source_text, tasks, successes, failures, unsupported)
        await send_bot_notice(notify_target, handoff_text)
        logger.warning(f"🤖 [ZD-Agent] 发现未实现能力，已中断群回复并私发人工接管通知: {notify_target}")
        return {
            "id": successes[-1]["cmd_id"] if successes else f"agent_handoff_{int(time.time() * 1000)}",
            "stop_actions": True,
            "handoff": True,
        }
    reply_text = merge_agent_reply_blocks(blocks)

    if reply_text and target_client:
        await asyncio.sleep(random_delay_from_step(step or {}, "result_reply_min", "result_reply_max", 1.8, 3.8))
        if (plan or {}).get("private_reply") and getattr(event, "sender_id", None):
            await target_client.send_message(event.sender_id, reply_text)
            sent = await target_client.send_message(event.chat_id, "已发", reply_to=event.id)
        else:
            sent = await target_client.send_message(event.chat_id, reply_text, reply_to=event.id)
        if global_main_handler:
            asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))

    if failures:
        logger.warning(f"⚠️ [ZD-Agent] 部分能力失败: success={len(successes)} failed={len(failures)}")
    return {
        "id": successes[-1]["cmd_id"] if successes else f"agent_{int(time.time() * 1000)}",
        "stop_actions": any(bool((item.get("result") or {}).get("stop_actions")) for item in successes)
    }

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
    current_config["extra_enabled"] = True

    if "approval_keywords" not in current_config:
        current_config["approval_keywords"] = ["同意", "批准", "ok"]
    else:
        current_config["approval_keywords"] = split_config_items(current_config.get("approval_keywords", []), split_commas=True)
    if not isinstance(current_config.get("schedule"), dict):
        current_config["schedule"] = DEFAULT_CONFIG["schedule"]
    current_config["ai_private_reply"] = normalize_ai_private_reply_config(current_config.get("ai_private_reply", {}))
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
        if "cooldown" not in rule: rule["cooldown"] = 1
        if not isinstance(rule.get("replies"), list): rule["replies"] = []
        if not isinstance(rule.get("approval_action"), dict): rule["approval_action"] = {}

        rule["groups"] = clean_group_ids(rule.get("groups", []))
        rule["keywords"] = split_config_items(rule.get("keywords", []))
        rule["file_extensions"] = [x.lower().replace('.', '') for x in split_config_items(rule.get("file_extensions", []), split_commas=True)]
        rule["filename_keywords"] = split_config_items(rule.get("filename_keywords", []), split_commas=True)
        rule["sender_prefixes"] = split_sender_prefix_items(rule.get("sender_prefixes", []))
        if rule.get("sender_mode") not in ("exclude", "include"):
            rule["sender_mode"] = "exclude"
        try:
            rule["cooldown"] = int(rule.get("cooldown", 1))
        except Exception:
            rule["cooldown"] = 1
        
        aa = rule["approval_action"]
        if "reply_admin" not in aa: aa["reply_admin"] = ""
        if "reply_origin" not in aa: aa["reply_origin"] = ""
        if "forward_to" not in aa: aa["forward_to"] = ""
        aa["replies"] = normalize_reply_steps(aa.get("replies", []))
        for i in range(1, 4):
            if f"delay_{i}_min" not in aa: aa[f"delay_{i}_min"] = 1.0
            if f"delay_{i}_max" not in aa: aa[f"delay_{i}_max"] = 2.0

        rule["replies"] = normalize_reply_steps(rule.get("replies", []))

        if rule["sender_mode"] == "exclude" and not rule["sender_prefixes"]:
            rule["sender_prefixes"] = list(system_cs_prefixes)
        clean_rules.append(rule)
    current_config["rules"] = clean_rules
    current_config["resources"] = normalize_monitor_resources(current_config.get("resources", {}), clean_rules, system_cs_prefixes)

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

        new_config["extra_enabled"] = True
        new_config["ai_private_reply"] = normalize_ai_private_reply_config(new_config.get("ai_private_reply", {}))

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
            aa["replies"] = normalize_reply_steps(aa.get("replies", []))
            
            for i in range(1, 4):
                try: aa[f"delay_{i}_min"] = float(aa.get(f"delay_{i}_min", 1.0))
                except: aa[f"delay_{i}_min"] = 1.0
                try: aa[f"delay_{i}_max"] = float(aa.get(f"delay_{i}_max", 2.0))
                except: aa[f"delay_{i}_max"] = 2.0
            
            raw_prefixes = rule.get("sender_prefixes", [])
            rule["sender_prefixes"] = split_sender_prefix_items(raw_prefixes)
            
            try: rule["cooldown"] = int(rule.get("cooldown", 1))
            except: rule["cooldown"] = 1
            rule["replies"] = normalize_reply_steps(rule.get("replies", []))
            clean_rules.append(rule)

        new_config["rules"] = clean_rules
        new_config["resources"] = normalize_monitor_resources(new_config.get("resources", {}), clean_rules, system_cs_prefixes)
        
        serialized_config = json.dumps(new_config, ensure_ascii=False)
        if redis_client:
            try: 
                if not redis_client.set(REDIS_KEY, serialized_config):
                    return False, "Redis 保存失败"
                saved_raw = redis_client.get(REDIS_KEY)
                if saved_raw != serialized_config:
                    return False, "Redis 保存校验失败"
            except Exception as e:
                logger.error(f"❌ [Monitor] Redis 保存失败: {e}")
                return False, f"Redis 保存失败: {e}"
        
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_config, f, indent=4, ensure_ascii=False)
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                saved_file = json.load(f)
            if saved_file.get("rules") != new_config.get("rules"):
                return False, "本地配置保存校验失败"
        except Exception as e:
            logger.error(f"❌ [Monitor] 本地配置保存校验失败: {e}")
            return False, f"本地配置保存校验失败: {e}"
        
        current_config = new_config
        logger.info(f"💾 [Monitor] 配置已更新并保存")
        return True, "保存成功"
    except Exception as e:
        logger.error(f"❌ [Monitor] 保存逻辑错误: {e}")
        return False, str(e)

async def send_command_telegram_message(account_name, target, text, cmd=None):
    target_name = str(account_name or "").strip() or MAIN_NAME
    if target_name not in global_clients:
        raise RuntimeError(f"发送账号不存在或未注册：{target_name}")
    target_client = global_clients[target_name]
    if target_name != MAIN_NAME and hasattr(target_client, "is_connected") and not target_client.is_connected():
        raise RuntimeError(f"发送账号未连接：{target_name}")
    peer = parse_peer_target(target)
    if peer is None:
        raise RuntimeError("未配置 Telegram 目标群")
    body = format_caption(text)
    if not body:
        raise RuntimeError("Telegram 消息为空")
    sent = await target_client.send_message(peer, body)
    save_settlement_tg_bridge(target_name, sent, {**(cmd or {}), "text": body})
    record_runtime_event(
        "settlement_urge",
        "success",
        f"催结算消息已发送：{peer}",
        rule={"id": str((cmd or {}).get("id") or "__settlement_urge__"), "name": str((cmd or {}).get("rule") or "催结算")},
        target_account=target_name,
        action_count=1,
    )
    return sent

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
        .script-link { height: 28px; display: inline-flex; align-items: center; gap: 6px; padding: 0 10px; border: 1px solid #C7D2FE; border-radius: 6px; background: #EEF2FF; color: #4F46E5; font-size: 11px; font-weight: 800; text-decoration: none; white-space: nowrap; }
        .script-link:hover { background: #E0E7FF; border-color: #A5B4FC; }
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
            <a href="/tool/zd_unlock_extension.zip" class="script-link"><i class="fa-solid fa-puzzle-piece"></i>Chrome扩展ZIP</a>
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
                 :class="{'opacity-60 grayscale': !rule.enabled}">

                <div class="px-3 py-2 border-b border-slate-100 flex justify-between items-center bg-slate-50/50">
                    <div class="flex items-center gap-2 flex-1">
                        <span class="text-slate-400 text-[10px] font-mono">#{{index+1}}</span>
                        <input v-model="rule.name" class="bg-transparent border-none p-0 text-xs font-bold text-slate-700 focus:ring-0 placeholder-slate-300 w-full font-sans" placeholder="未命名规则">
                    </div>
                    
                    <label class="relative inline-flex items-center cursor-pointer mr-2" title="切换规则状态">
                        <input type="checkbox"
                               :checked="rule.enabled"
                               @change="rule.enabled = $event.target.checked; saveConfig();"
                               class="sr-only peer">
                        <div class="w-7 h-4 bg-slate-200 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-3 after:w-3 after:transition-all peer-checked:bg-green-500"></div>
                    </label>

                    <button @click="removeRule(index)" class="text-slate-300 hover:text-red-500 transition-colors px-1" title="删除"><i class="fa-solid fa-trash text-[10px]"></i></button>
                </div>
                <div class="p-3 flex flex-col gap-3" :class="{'pointer-events-none': !rule.enabled}">
                    <div class="space-y-1.5">
                        <div class="flex items-center justify-between"><span class="section-label"><i class="fa-solid fa-eye mr-1"></i>监听来源</span><label class="flex items-center gap-1 cursor-pointer select-none"><input type="checkbox" v-model="rule.check_file" class="w-3 h-3 text-primary border-slate-300 rounded focus:ring-0"><span class="text-[10px] text-slate-500 font-medium" :class="{'text-primary': rule.check_file}">文件模式</span></label></div>
                        <div class="relative"><textarea :value="listToString(rule.groups)" @input="stringToIntList($event, rule, 'groups')" rows="3" class="bento-input w-full px-2 py-1.5 resize-y min-h-16 leading-tight font-mono text-[11px]" placeholder="群ID (换行分隔)"></textarea></div>
                        <div v-if="!rule.check_file" class="relative">
                            <textarea :value="listToString(rule.keywords)" @input="stringToList($event, rule, 'keywords')" rows="2" class="bento-input w-full px-2 py-1.5 resize-none h-16 leading-tight font-mono text-[11px] placeholder-slate-400" placeholder="普通: 代存&#10;正则: r:(代|带)存|入[金款]"></textarea>
                            <div class="absolute right-2 bottom-1 text-[9px] text-primary/60 bg-white/80 px-1 rounded pointer-events-none">支持正则 r:...</div>
                        </div>
                        <div v-else class="space-y-2">
                            <div class="grid grid-cols-2 gap-2"><input :value="listToString(rule.file_extensions).replace(/\\n/g, ', ')" @input="stringToList($event, rule, 'file_extensions')" class="bento-input w-full px-2 py-1.5 h-7 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="后缀: xlsx, png"><textarea :value="listToString(rule.filename_keywords)" @input="stringToList($event, rule, 'filename_keywords')" rows="3" class="bento-input w-full px-2 py-1.5 resize-y min-h-16 bg-yellow-50/50 border-yellow-200 focus:border-yellow-400 font-mono text-[11px]" placeholder="文件名关键词，一行一个"></textarea></div>
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
                                            <option value="backend_unlock">触发后台解锁</option>
                                            <option value="agent_orchestrator">Agent编排</option>
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
                                        <div class="step-help"><i class="fa-solid fa-user-shield mr-1"></i>普通规则检测引用原始消息的抢答；同意审批动作流检测引用领导同意消息的抢答。命中后删除已发消息并停止后续动作。</div>
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

                                    <template v-if="reply.type === 'backend_unlock'">
                                        <div class="grid grid-cols-1 gap-2">
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-key"></i>解锁类型</div>
                                                <select v-model="reply.backend_action" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-orange-700 bg-orange-50 border-orange-200">
                                                    <option value="unlock_sms">短信/验证码限制</option>
                                                    <option value="clear_login_error">登录密码试错限制</option>
                                                    <option value="add_proxy_whitelist">代理 IP 加白</option>
                                                    <option value="member_data_overview">查数据</option>
                                                    <option value="query_member_line">查线</option>
                                                    <option value="query_login_device_ip">查登录设备/IP</option>
                                                    <option value="query_same_ip_device">查同 IP/设备</option>
                                                    <option value="query_venue_turnover">查场馆流水锁定</option>
                                                    <option value="configure_rebate">配置返水</option>
                                                    <option value="urge_settlement">催结算</option>
                                                    <option value="query_ticket_cancel_reason">注单取消/失败原因</option>
                                                </select>
                                            </div>
                                            <div class="visual-field" v-if="reply.backend_action === 'urge_settlement' || reply.backend_action === 'query_ticket_cancel_reason'">
                                                <div class="visual-label"><i class="fa-solid fa-receipt"></i>提取注单号的正则</div>
                                                <input v-model="reply.member_pattern" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] font-mono text-orange-700 bg-orange-50 border-orange-200" placeholder="留空：从已匹配消息里提取 12-24 位注单号">
                                            </div>
                                            <div class="visual-field" v-else-if="reply.backend_action !== 'add_proxy_whitelist'">
                                                <div class="visual-label"><i class="fa-solid fa-unlock"></i>提取账号名的正则</div>
                                                <input v-model="reply.member_pattern" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] font-mono text-orange-700 bg-orange-50 border-orange-200" placeholder="留空：从已匹配消息里提取账号">
                                                <div class="text-[9px] text-slate-400 mt-0.5">触发词在监听关键词里维护；这里仅负责提取账号。留空会取消息中的第一个账号样式文本。</div>
                                            </div>
                                            <div class="visual-field" v-else>
                                                <div class="visual-label"><i class="fa-solid fa-network"></i>提取 IP 的正则</div>
                                                <input v-model="reply.ip_pattern" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] font-mono text-orange-700 bg-orange-50 border-orange-200" placeholder="留空：从已匹配消息里提取 IPv4">
                                                <div class="text-[9px] text-slate-400 mt-0.5">触发词在监听关键词里维护；这里仅负责提取 IPv4。</div>
                                            </div>
                                            <template v-if="reply.backend_action === 'urge_settlement'">
                                                <div class="visual-field">
                                                    <div class="visual-label"><i class="fa-brands fa-telegram"></i>TG 催结算群</div>
                                                    <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-700 bg-blue-50 border-blue-100" placeholder="-1001234567890 或 @username">
                                                </div>
                                                <div class="visual-field">
                                                    <div class="visual-label"><i class="fa-solid fa-user"></i>催结算发送账号</div>
                                                    <select v-model="reply.telegram_account" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-700 bg-blue-50 border-blue-100">
                                                        <option value="">主账号（默认）</option>
                                                        <option v-for="acc in available_accounts" :key="acc" :value="acc">{{ acc }}</option>
                                                    </select>
                                                </div>
                                                <div class="visual-field">
                                                    <div class="visual-label"><i class="fa-solid fa-message"></i>TG 消息模板</div>
                                                    <textarea v-model="reply.text" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-blue-50 border-blue-100" placeholder="留空默认：{order_no}注单催结算&#10;赛事ID：{match_id}"></textarea>
                                                </div>
                                            </template>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-bell"></i>失败通知对象</div>
                                                <input v-model="reply.fail_notify_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-red-700 bg-red-50 border-red-100" placeholder="用户ID 或 @username">
                                                <div class="text-[9px] text-slate-400 mt-0.5">未实现能力会中断群回复，并把已查/未查内容私发这里。</div>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-message"></i>失败通知内容</div>
                                                <textarea v-model="reply.fail_notify_text" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-red-50 border-red-100" placeholder="留空使用默认：后台自动处理失败，请人工核查"></textarea>
                                            </div>
                                        </div>
                                    </template>
                                    <template v-if="reply.type === 'agent_orchestrator'">
                                        <div class="grid grid-cols-1 gap-2">
                                            <div class="step-help"><i class="fa-solid fa-wand-magic-sparkles mr-1"></i>自动拆解消息，只调用现有能力；催结算和取消/失败原因分开处理，不支持事项不会编结果。</div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-brands fa-telegram"></i>TG 催结算群</div>
                                                <input v-model="reply.forward_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-700 bg-blue-50 border-blue-100" placeholder="-1001234567890 或 @username">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-user"></i>催结算发送账号</div>
                                                <select v-model="reply.telegram_account" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-blue-700 bg-blue-50 border-blue-100">
                                                    <option value="">主账号（默认）</option>
                                                    <option v-for="acc in available_accounts" :key="acc" :value="acc">{{ acc }}</option>
                                                </select>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-message"></i>TG 消息模板</div>
                                                <textarea v-model="reply.text" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-blue-50 border-blue-100" placeholder="留空默认：{order_no}注单催结算&#10;赛事ID：{match_id}"></textarea>
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-bell"></i>失败通知对象</div>
                                                <input v-model="reply.fail_notify_to" class="bento-input w-full px-2 py-1.5 h-8 text-[11px] text-red-700 bg-red-50 border-red-100" placeholder="用户ID 或 @username">
                                            </div>
                                            <div class="visual-field">
                                                <div class="visual-label"><i class="fa-solid fa-message"></i>失败通知内容</div>
                                                <textarea v-model="reply.fail_notify_text" rows="2" class="bento-input w-full px-2 py-1.5 text-[11px] resize-none bg-red-50 border-red-100" placeholder="留空使用默认：后台自动处理失败，请人工核查"></textarea>
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
            const config = reactive({ enabled: false, approval_keywords: [], schedule: {active: false, start: '09:00', end: '21:00'}, scheduled_messages: [], rules: [] });
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
                type: (rep.type === 'backend_unlock' && rep.backend_action === 'agent_existing') ? 'agent_orchestrator' : (rep.type || 'text'),
                text: rep.text || '',
                forward_to: rep.forward_to || '',
                member_pattern: rep.member_pattern || '',
                ip_pattern: rep.ip_pattern || '',
                backend_action: rep.backend_action || 'unlock_sms',
                telegram_account: rep.telegram_account || '',
                fail_notify_to: rep.fail_notify_to || '',
                fail_notify_text: rep.fail_notify_text || '',
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
                if (!reply.member_pattern) reply.member_pattern = '';
                if (!reply.ip_pattern) reply.ip_pattern = '';
                if (!reply.backend_action) reply.backend_action = 'unlock_sms';
                if (!reply.telegram_account) reply.telegram_account = '';
                if (!reply.fail_notify_to) reply.fail_notify_to = '';
                if (!reply.fail_notify_text) reply.fail_notify_text = '';
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
                        syncAvailableAccounts(data.available_accounts);
                    })
                    .catch(e => console.log('Heartbeat skipped'));
            };

            const applyConfigData = (data = {}) => {
                    config.enabled = data.enabled; 
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
            };

            // Initial full load
            fetch('/tool/monitor_settings_json')
                .then(r => r.json())
                .then(applyConfigData);

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
                    sender_mode: 'exclude', sender_prefixes: [], cooldown: 1,
                    replies: [hydrateReply({type:'text', text: '', min: 1, max: 2})],
                    reply_account: ''
                });
            };
            
            const removeRule = (index) => { if(confirm('确定删除此规则？')) config.rules.splice(index, 1); };

            const saveConfig = async () => {
                try {
                    const res = await fetch('/api/monitor_settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
                    const json = await res.json();
                    if (json.success) {
                        if (json.config) applyConfigData(json.config);
                        showToast('配置已保存', 'success');
                    }
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

    if not rule_matches_group(event.chat_id, rule.get("groups", [])): return False, "群组不符", None
    is_backend_unlock_rule = rule_has_backend_unlock(rule)
    is_backend_or_agent_rule = is_backend_unlock_rule or rule_has_agent_orchestrator(rule)
    text = (event.text or "")
    backend_actions = rule_backend_actions(rule) if is_backend_unlock_rule else set()
    if "clear_login_error" in backend_actions and text_mentions_secondary_password(text):
        return False, "二级密码消息不触发登录密码解锁", None
    if not is_backend_or_agent_rule and await is_monitor_own_account(client, event, other_cs_ids):
        return False, "发送者是已登录账号", None
    
    # v69: Pass sender object, not name string
    if not is_backend_or_agent_rule and not check_sender_allowed(sender_obj, rule):
        return False, "发送者被排除", None

    check_file = rule.get("check_file", False)
    
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
        if not match_text(text, rule):
            if not ((rule_has_backend_action(rule, "urge_settlement") or rule_has_agent_orchestrator(rule)) and looks_like_short_urge_settlement(text)):
                return False, "文本关键词不符", None
            logger.info(f"✅ [Monitor] 催结算短句兜底命中 | Msg={event.id}")
    if event.is_reply: return False, "是回复消息", None

    if check_cooldown and not is_backend_or_agent_rule:
        rule_id = ensure_rule_id(rule)
        last_time = rule_timers.get(rule_id, 0)
        now = time.time()
        if now - last_time < rule.get("cooldown", 1): return False, "冷却中", None
    
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

def resolve_extension_bot_base():
    default_url = "https://cshelp.zeabur.app"
    legacy_urls = {"https://arcshelp.zeabur.app", "http://arcshelp.zeabur.app"}
    for name in (
        "EXTENSION_BOT_BASE",
        "BOT_BASE_URL",
        "BOT_MENU_URL",
        "WEBAPP_URL",
        "WEB_APP_URL",
        "PUBLIC_URL",
        "ZEABUR_WEB_URL",
    ):
        value = str(os.environ.get(name) or "").strip().rstrip("/")
        if not value:
            continue
        if value in legacy_urls:
            return default_url
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            return value
    return default_url

def extension_host_permission(bot_base):
    parsed = urllib.parse.urlparse(str(bot_base or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}/*"

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
        data = current_config.copy() if success else {}
        if success:
            data["available_accounts"] = [k for k in global_clients.keys() if k != MAIN_NAME]
            data["main_account"] = MAIN_NAME
            data["accounts"] = get_account_summaries()
        return jsonify({"success": success, "msg": msg if not success else "", "config": data})

    @app.route('/api/monitor_settings/export')
    def export_monitor_settings():
        exported_at = datetime.now(BJ_TZ)
        payload = {
            "type": "cs-bot.monitor_config",
            "version": 1,
            "exported_at": exported_at.isoformat(),
            "config": current_config,
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        filename = f"monitor_config_{exported_at.strftime('%Y%m%d_%H%M%S')}.json"
        response = Response(body, mimetype='application/json; charset=utf-8')
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @app.route('/api/monitor_settings/import', methods=['POST'])
    def import_monitor_settings():
        raw = request.get_json(silent=True) or {}
        candidate = raw.get("config") if isinstance(raw.get("config"), dict) else raw
        if not isinstance(candidate, dict) or "rules" not in candidate:
            return jsonify({"success": False, "msg": "导入文件格式不正确，缺少 rules"})
        success, msg = save_config(candidate)
        data = current_config.copy() if success else {}
        if success:
            data["available_accounts"] = [k for k in global_clients.keys() if k != MAIN_NAME]
            data["main_account"] = MAIN_NAME
            data["accounts"] = get_account_summaries()
        return jsonify({"success": success, "msg": msg if not success else "", "config": data})

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

    @app.route('/api/cmd/poll')
    def cmd_poll():
        if request.args.get("secret") != CMD_SECRET:
            return jsonify({"ok": False}), 403
        try:
            wait_seconds = max(0.0, min(5.0, float(request.args.get("wait", 0) or 0)))
        except Exception:
            wait_seconds = 0.0
        cmd = wait_for_pending_command(wait_seconds)
        return jsonify({"ok": True, "cmd": cmd})

    @app.route('/api/cmd/ack', methods=['POST'])
    def cmd_ack():
        if request.args.get("secret") != CMD_SECRET:
            return jsonify({"ok": False}), 403
        data = request.get_json(silent=True) or {}
        cmd_id = str(data.get("id") or "")
        status = str(data.get("status") or "")
        member_name = str(data.get("member_name") or "")
        detail = str(data.get("detail") or "")
        reply_text = str(data.get("reply_text") or "")
        stop_actions = bool(data.get("stop_actions"))
        if cmd_id:
            with backend_command_lock:
                pending_command_leases.pop(cmd_id, None)
                previous_progress = backend_command_progress.get(cmd_id, {})
                backend_command_results[cmd_id] = {
                    "status": status,
                    "member_name": member_name,
                    "detail": detail,
                    "reply_text": reply_text,
                    "stop_actions": stop_actions,
                    "acked_at": now_bj().isoformat(),
                }
                backend_command_progress[cmd_id] = {
                    **previous_progress,
                    "status": status,
                    "detail": detail,
                    "percent": 100 if status == "success" else previous_progress.get("percent", 0),
                    "updated_at": now_bj().isoformat(),
                }
                _prune_backend_command_maps_locked()
        logger.info(
            f"🔓 [BackendUnlock] 扩展回执: id={cmd_id or '-'} "
            f"member={member_name or '-'} status={status or '-'} detail={detail[:500] or '-'}"
        )
        return jsonify({"ok": True})

    @app.route('/api/cmd/progress', methods=['POST'])
    def cmd_progress():
        if request.args.get("secret") != CMD_SECRET:
            return jsonify({"ok": False}), 403
        data = request.get_json(silent=True) or {}
        cmd_id = str(data.get("id") or "")
        if cmd_id:
            progress = {
                "status": str(data.get("status") or "running"),
                "message": str(data.get("message") or ""),
                "title": str(data.get("title") or ""),
                "step": int(data.get("step") or 0),
                "total": int(data.get("total") or 0),
                "success": int(data.get("success") or 0),
                "percent": max(0, min(100, int(data.get("percent") or 0))),
                "updated_at": now_bj().isoformat(),
            }
            with backend_command_lock:
                backend_command_progress[cmd_id] = progress
                if cmd_id in pending_command_leases:
                    pending_command_leases[cmd_id]["leased_at"] = time.time()
                _prune_backend_command_maps_locked()
        return jsonify({"ok": True})

    @app.route('/api/cmd/send_telegram', methods=['POST'])
    def cmd_send_telegram():
        if request.args.get("secret") != CMD_SECRET:
            return jsonify({"ok": False}), 403
        data = request.get_json(silent=True) or {}
        cmd_id = str(data.get("id") or "")
        target = str(data.get("target") or "")
        account = str(data.get("account") or "")
        text = str(data.get("text") or "")
        future = asyncio.run_coroutine_threadsafe(
            send_command_telegram_message(account, target, text, cmd=data),
            bot_loop
        )
        try:
            sent = future.result(timeout=30)
            return jsonify({"ok": True, "message_id": getattr(sent, "id", None)})
        except Exception as e:
            logger.error(f"❌ [BackendUnlock] 催结算 TG 发送失败: id={cmd_id or '-'} account={account or MAIN_NAME} target={target or '-'} err={e}")
            record_runtime_event(
                "settlement_urge",
                "failed",
                f"催结算消息发送失败：{e}",
                rule={"id": cmd_id or "__settlement_urge__", "name": str(data.get("rule") or "催结算")},
                target_account=account or MAIN_NAME,
            )
            return jsonify({"ok": False, "error": str(e)}), 500

    @app.route('/tool/zd_unlock_extension.zip')
    def zd_unlock_extension_zip():
        extension_dir = os.path.join(os.path.dirname(__file__), "extensions", "zd-unlock-extension")
        bot_base = resolve_extension_bot_base()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, files in os.walk(extension_dir):
                for filename in files:
                    path = os.path.join(root, filename)
                    rel = os.path.relpath(path, extension_dir)
                    if rel == "service_worker.js":
                        with open(path, "r", encoding="utf-8") as f:
                            source = f.read()
                        source = re.sub(
                            r"const\s+DEFAULT_BOT_BASE\s*=\s*'[^']*';",
                            f"const DEFAULT_BOT_BASE = {json.dumps(bot_base)};",
                            source,
                            count=1,
                        )
                        zf.writestr(rel, source)
                    elif rel == "manifest.json":
                        with open(path, "r", encoding="utf-8") as f:
                            manifest = json.load(f)
                        host_permission = extension_host_permission(bot_base)
                        if host_permission:
                            permissions = manifest.setdefault("host_permissions", [])
                            if host_permission not in permissions:
                                permissions.insert(0, host_permission)
                        zf.writestr(rel, json.dumps(manifest, ensure_ascii=False, indent=2))
                    else:
                        zf.write(path, rel)
        buf.seek(0)
        return Response(
            buf.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": "attachment; filename=zd-unlock-extension.zip"}
        )

    @app.route('/tool/9site_storage_scan.js')
    def nine_site_storage_scan_script():
        script_path = os.path.join(os.path.dirname(__file__), "tools", "9site_storage_scan.js")
        with open(script_path, "r", encoding="utf-8") as f:
            return Response(f.read(), mimetype="application/javascript; charset=utf-8")

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
    client.add_event_handler(create_settlement_tg_reply_handler(main_name), events.NewMessage())
    client.add_event_handler(create_ai_private_reply_handler(main_name), events.NewMessage(incoming=True))
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
                extra_client.add_event_handler(create_settlement_tg_reply_handler(acc_name), events.NewMessage())
                extra_client.add_event_handler(create_ai_private_reply_handler(acc_name), events.NewMessage(incoming=True))
                bot_loop.create_task(_start_extra_client(extra_client, acc_name))
            except Exception as e:
                logger.error(f"❌ [OTP] 初始化 {acc_name} 失败: {e}")

    async def execute_rule_steps(target_client, rule, source_event, source_message, sender_name, steps=None, initial_sent_msgs=None, preempt_after_id=None, preempt_extra_target_ids=None, preempt_target_ids=None):
        sent_msgs = list(initial_sent_msgs or [])
        initial_sent_count = len(sent_msgs)
        notify_sent = False
        backend_actions = 0
        preempted = False
        action_failed = False
        steps = normalize_reply_steps(steps if steps is not None else rule.get("replies", []))
        source_chat_id = getattr(source_event, "chat_id", None)
        source_msg_id = getattr(source_event, "id", None)
        source_text = getattr(source_event, "text", None) or getattr(source_event, "raw_text", "") or ""
        source_file = getattr(source_message, "file", None)
        source_media = getattr(source_message, "media", None) or getattr(source_file, "media", None)

        try:
            for step in steps:
                stype = step.get("type", "text")
                if stype != "amount_logic":
                    await asyncio.sleep(random.uniform(step.get("min", 1), step.get("max", 3)))

                if stype == "forward":
                    tgt = step.get("forward_to")
                    if tgt:
                        tgt_chat_id = parse_peer_target(tgt)
                        sent = await target_client.forward_messages(tgt_chat_id, source_message)
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
                    if tgt and source_media:
                        tgt_chat_id = parse_peer_target(tgt)
                        sent = await target_client.send_file(tgt_chat_id, source_media, caption=format_caption(step.get("text", "")))
                        remember_sent_message(sent_msgs, tgt_chat_id, sent)

                elif stype == "amount_logic":
                    cfg = step.get("text", "")
                    tgt = step.get("forward_to")
                    parts = cfg.split('|')
                    if len(parts) >= 3:
                        thresh = float(parts[0])
                        found, amt = parse_smart_amount(source_text)
                        if found:
                            logger.info(f"💰 [Amount] 识别到金额: {amt}")
                            if amt >= thresh:
                                await asyncio.sleep(random_delay_from_step(step, "high_reply_min", "high_reply_max", step.get("min", 1), step.get("max", 3)))
                                sent = await target_client.send_message(source_chat_id, format_caption(parts[1]), reply_to=source_msg_id)
                                remember_sent_message(sent_msgs, source_chat_id, sent)
                            else:
                                low_replies = split_reply_sequence(parts[2])
                                if low_replies:
                                    await asyncio.sleep(random_delay_from_step(step, "low_first_min", "low_first_max", step.get("min", 1), step.get("max", 3)))
                                    sent = await target_client.send_message(source_chat_id, format_caption(low_replies[0]), reply_to=source_msg_id)
                                    remember_sent_message(sent_msgs, source_chat_id, sent)
                                if tgt:
                                    tgt_chat_id = parse_peer_target(tgt)
                                    if low_replies:
                                        await asyncio.sleep(random_delay_from_step(step, "low_forward_min", "low_forward_max"))
                                    fwd_msg = await target_client.forward_messages(tgt_chat_id, source_message)
                                    remember_sent_message(sent_msgs, tgt_chat_id, fwd_msg)
                                for sub_msg in low_replies[1:]:
                                    await asyncio.sleep(random_delay_from_step(step, "low_reply_min", "low_reply_max"))
                                    sent = await target_client.send_message(source_chat_id, format_caption(sub_msg), reply_to=source_msg_id)
                                    remember_sent_message(sent_msgs, source_chat_id, sent)
                        else:
                            logger.warning(f"⚠️ [Monitor] Amount logic matched text but no specific amount found.")

                elif stype == "preempt_check":
                    me = await target_client.get_me()
                    source_sent_ids = get_sent_ids_for_chat(sent_msgs, source_chat_id)
                    first_source_sent_id = get_first_sent_id_for_chat(sent_msgs, source_chat_id)
                    min_preempt_id = preempt_after_id or source_msg_id
                    preempt_msg = await find_preempting_reply(
                        target_client,
                        source_chat_id,
                        source_message,
                        min_preempt_id,
                        before_msg_id=first_source_sent_id,
                        own_sent_ids=source_sent_ids,
                        ignored_sender_ids={me.id, getattr(source_event, "sender_id", None)},
                        extra_target_ids=preempt_extra_target_ids,
                        target_ids=preempt_target_ids,
                    )
                    if preempt_msg:
                        if sent_msgs:
                            await delete_sent_messages(target_client, sent_msgs)
                        sent_msgs = []
                        preempted = True
                        logger.info(f"🧹 [Preempt] 检测到抢答 Msg={getattr(preempt_msg, 'id', '-')}, 已停止规则 '{rule.get('name')}' 后续动作")
                        break

                elif stype == "notify_user":
                    notify_target = parse_peer_target(step.get("forward_to"))
                    notify_text = format_bot_notice(step.get("text", ""), source_event, rule, sender_name)
                    if notify_target and notify_text:
                        await send_bot_notice(notify_target, notify_text)
                        notify_sent = True
                        logger.info(f"🔔 [Notify] 规则 '{rule.get('name')}' 已通过 Telegram Bot 通知: {notify_target}")

                elif stype == "backend_unlock":
                    backend_result = await execute_backend_unlock_step(step, rule, source_event, source_text, target_client=target_client)
                    backend_actions += 1
                    if backend_result and backend_result.get("stop_actions"):
                        break

                elif stype == "agent_orchestrator":
                    agent_result = await execute_agent_existing_step(step, rule, source_event, source_text, target_client=target_client)
                    backend_actions += 1
                    if agent_result and agent_result.get("stop_actions"):
                        break

                else:
                    content = step.get("text", "")
                    if content:
                        sent = await target_client.send_message(source_chat_id, format_caption(content), reply_to=source_msg_id)
                        remember_sent_message(sent_msgs, source_chat_id, sent)
                        if global_main_handler:
                            asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
        except Exception as e:
            action_failed = True
            logger.error(f"❌ [Monitor] 规则 '{rule.get('name')}' 执行动作失败: {e}")
        return {
            "sent_msgs": sent_msgs,
            "notify_sent": notify_sent,
            "backend_actions": backend_actions,
            "preempted": preempted,
            "action_failed": action_failed,
            "action_count": max(0, len(sent_msgs) - initial_sent_count) + (1 if notify_sent else 0) + backend_actions,
        }

    async def run_monitor_rule_actions(target_client, target_name, rule, event, sender_name, match_started):
        rule_id = ensure_rule_id(rule)
        result = await execute_rule_steps(target_client, rule, event, event.message, sender_name)
        action_count = result["action_count"]
        if result["action_failed"]:
            if action_count:
                rule_timers[rule_id] = time.time()
                logger.warning(f"⚠️ [Monitor] 规则 '{rule.get('name')}' 已部分执行，已进入冷却")
            else:
                rule_timers.pop(rule_id, None)
            record_runtime_event(
                "monitor",
                "failed",
                "执行动作失败",
                rule=rule,
                event=event,
                sender_name=sender_name,
                target_account=target_name,
                action_count=action_count,
                duration_ms=(time.time() - match_started) * 1000
            )
            return

        if result["preempted"]:
            rule_timers.pop(rule_id, None)
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
            return

        if action_count:
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
            rule_timers.pop(rule_id, None)
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

    async def run_approval_rule_actions(replier_client, target_name, rule, event, original_msg, approver, orig_sender, approval_started):
        approval_actions = 0
        approval_errors = []
        action = rule.get("approval_action", {})
        approval_sent_msgs = []
        try:
            await asyncio.sleep(random.uniform(float(action.get("delay_1_min", 1.0)), float(action.get("delay_1_max", 2.0))))
            if action.get("reply_admin"):
                sent_admin_reply = await replier_client.send_message(event.chat_id, format_caption(action["reply_admin"]), reply_to=event.id)
                remember_sent_message(approval_sent_msgs, event.chat_id, sent_admin_reply)
                approval_actions += 1
        except Exception as e:
            approval_errors.append(f"回复领导失败：{e}")
            logger.error(f"❌ [Approval] 回复领导失败: {e}")

        approval_steps = normalize_reply_steps(action.get("replies", []))
        if not approval_steps:
            legacy_forward = str(action.get("forward_to") or "").strip()
            legacy_reply = str(action.get("reply_origin") or "").strip()
            if legacy_forward:
                approval_steps.append({
                    "type": "forward",
                    "forward_to": legacy_forward,
                    "min": action.get("delay_2_min", 1.0),
                    "max": action.get("delay_2_max", 3.0),
                })
            if legacy_reply:
                approval_steps.append({
                    "type": "text",
                    "text": legacy_reply,
                    "min": action.get("delay_3_min", 1.0),
                    "max": action.get("delay_3_max", 2.0),
                })

        step_result = await execute_rule_steps(
            replier_client,
            rule,
            MessageEventView(original_msg),
            original_msg,
            get_sender_name(orig_sender),
            steps=approval_steps,
            initial_sent_msgs=approval_sent_msgs,
            preempt_after_id=event.id,
            preempt_target_ids=[event.id],
        )
        approval_actions += step_result["action_count"]
        if step_result["action_failed"]:
            approval_errors.append("同意后动作流执行失败")
        if step_result["preempted"]:
            approval_errors.append("抢答检测命中，已删除同意后动作流消息")
        record_runtime_event(
            "approval",
            "skipped" if step_result["preempted"] else ("failed" if approval_errors else "success"),
            "；".join(approval_errors) if approval_errors else "审批动作流执行完成",
            rule=rule,
            event=event,
            sender_name=get_sender_name(approver),
            target_account=target_name,
            action_count=approval_actions,
            duration_ms=(time.time() - approval_started) * 1000
        )

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
                    if await is_monitor_own_account(client, event, other_cs_ids):
                        logger.info("🛡️ [Approval] 忽略已登录账号发出的审批触发词")
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
                                
                                if not target_name: target_name = MAIN_NAME 

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

                                schedule_zd_rule_task(
                                    run_approval_rule_actions(
                                        replier_client,
                                        target_name,
                                        rule,
                                        event,
                                        original_msg,
                                        approver,
                                        orig_sender,
                                        approval_started,
                                    ),
                                    f"审批:{rule.get('name')} Msg={event.id}"
                                )
                                return

                                approval_sent_msgs = []
                                try:
                                    await asyncio.sleep(random.uniform(float(action.get("delay_1_min", 1.0)), float(action.get("delay_1_max", 2.0))))
                                    if action.get("reply_admin"):
                                        sent_admin_reply = await replier_client.send_message(event.chat_id, format_caption(action["reply_admin"]), reply_to=event.id)
                                        remember_sent_message(approval_sent_msgs, event.chat_id, sent_admin_reply)
                                        approval_actions += 1
                                except Exception as e:
                                    approval_errors.append(f"回复领导失败：{e}")
                                    logger.error(f"❌ [Approval] 回复领导失败: {e}")

                                approval_steps = normalize_reply_steps(action.get("replies", []))
                                if not approval_steps:
                                    legacy_forward = str(action.get("forward_to") or "").strip()
                                    legacy_reply = str(action.get("reply_origin") or "").strip()
                                    if legacy_forward:
                                        approval_steps.append({
                                            "type": "forward",
                                            "forward_to": legacy_forward,
                                            "min": action.get("delay_2_min", 1.0),
                                            "max": action.get("delay_2_max", 3.0),
                                        })
                                    if legacy_reply:
                                        approval_steps.append({
                                            "type": "text",
                                            "text": legacy_reply,
                                            "min": action.get("delay_3_min", 1.0),
                                            "max": action.get("delay_3_max", 2.0),
                                        })

                                step_result = await execute_rule_steps(
                                    replier_client,
                                    rule,
                                    MessageEventView(original_msg),
                                    original_msg,
                                    get_sender_name(orig_sender),
                                    steps=approval_steps,
                                    initial_sent_msgs=approval_sent_msgs,
                                    preempt_after_id=event.id,
                                    preempt_target_ids=[event.id],
                                )
                                approval_actions += step_result["action_count"]
                                if step_result["action_failed"]:
                                    approval_errors.append("同意后动作流执行失败")
                                if step_result["preempted"]:
                                    approval_errors.append("抢答检测命中，已删除同意后动作流消息")
                                record_runtime_event(
                                    "approval",
                                    "skipped" if step_result["preempted"] else ("failed" if approval_errors else "success"),
                                    "；".join(approval_errors) if approval_errors else "审批动作流执行完成",
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
                    if not target_name: target_name = MAIN_NAME

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

                    if not rule_has_backend_or_agent(rule):
                        rule_timers[rule_id] = time.time()
                    schedule_zd_rule_task(
                        run_monitor_rule_actions(target_client, target_name, rule, event, sender_name, match_started),
                        f"规则:{rule.get('name')} Msg={event.id}"
                    )
                    break

                    sent_msgs = []
                    notify_sent = False
                    backend_actions = 0
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
                                me = await target_client.get_me()
                                source_sent_ids = get_sent_ids_for_chat(sent_msgs, event.chat_id)
                                first_source_sent_id = get_first_sent_id_for_chat(sent_msgs, event.chat_id)
                                preempt_msg = await find_preempting_reply(
                                    target_client,
                                    event.chat_id,
                                    event.message,
                                    event.id,
                                    before_msg_id=first_source_sent_id,
                                    own_sent_ids=source_sent_ids,
                                    ignored_sender_ids={me.id, event.sender_id},
                                )
                                if preempt_msg:
                                    if sent_msgs:
                                        await delete_sent_messages(target_client, sent_msgs)
                                    sent_msgs = []
                                    preempted = True
                                    logger.info(f"🧹 [Preempt] 检测到抢答 Msg={getattr(preempt_msg, 'id', '-')}, 已停止规则 '{rule.get('name')}' 后续动作")
                                    break

                            elif stype == "notify_user":
                                notify_target = parse_peer_target(step.get("forward_to"))
                                notify_text = format_bot_notice(step.get("text", ""), event, rule, sender_name)
                                if notify_target and notify_text:
                                    await send_bot_notice(notify_target, notify_text)
                                    notify_sent = True
                                    logger.info(f"🔔 [Notify] 规则 '{rule.get('name')}' 已通过 Telegram Bot 通知: {notify_target}")

                            elif stype == "backend_unlock":
                                backend_result = await execute_backend_unlock_step(step, rule, event, event.text or "", target_client=target_client)
                                backend_actions += 1
                                if backend_result and backend_result.get("stop_actions"):
                                    break

                            elif stype == "agent_orchestrator":
                                agent_result = await execute_agent_existing_step(step, rule, event, event.text or "", target_client=target_client)
                                backend_actions += 1
                                if agent_result and agent_result.get("stop_actions"):
                                    break

                            else: # text
                                content = step.get("text", "")
                                if content:
                                    sent = await target_client.send_message(event.chat_id, format_caption(content), reply_to=event.id)
                                    remember_sent_message(sent_msgs, event.chat_id, sent)
                                    if global_main_handler: asyncio.create_task(global_main_handler(events.NewMessage.Event(sent)))
                    except Exception as e:
                        action_failed = True
                        if sent_msgs or notify_sent or backend_actions:
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
                            action_count=len(sent_msgs) + (1 if notify_sent else 0) + backend_actions,
                            duration_ms=(time.time() - match_started) * 1000
                        )

                    if not action_failed:
                        action_count = len(sent_msgs) + (1 if notify_sent else 0) + backend_actions
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
                        elif sent_msgs or notify_sent or backend_actions:
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
                elif rule_matches_group(event.chat_id, rule.get("groups", [])) and reason in ("发送者是已登录账号", "发送者被排除", "冷却中", "二级密码消息不触发登录密码解锁"):
                    logger.info(
                        f"↪️ [MonitorSkip] 规则 '{rule.get('name')}' 未执行: {reason} | "
                        f"Chat={event.chat_id} Msg={event.id} Sender={sender_name}"
                    )
            except Exception as e: logger.error(f"❌ [Monitor] Rule Error: {e}")

    logger.info("🛠️ [Monitor] Ultimate UI v78 (Full Source) 已启动")
