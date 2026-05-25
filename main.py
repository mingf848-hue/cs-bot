import os
import sys
import asyncio
import logging
import requests
import re
import time
import json
import queue
import sqlite3
import copy
import secrets
from collections import deque, defaultdict
from datetime import datetime, timedelta, timezone
from threading import Thread, Lock
from flask import Flask, render_template, render_template_string, Response, request, stream_with_context, jsonify
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError

try:
    import redis
except ImportError:
    redis = None

# ==========================================
# 模块 0: 北京时间树状日志系统
# ==========================================
logger = logging.getLogger("BotLogger")
logger.setLevel(logging.DEBUG)
DATA_DIR = (os.environ.get("DATA_DIR") or os.environ.get("PERSISTENT_DATA_DIR") or "").strip()
if DATA_DIR:
    os.makedirs(DATA_DIR, exist_ok=True)

def data_path(filename):
    return os.path.join(DATA_DIR, filename) if DATA_DIR else filename

LOG_FILE_PATH = data_path('bot_debug.log')
CHAT_LOG_TO_CONSOLE = os.environ.get("CHAT_LOG_TO_CONSOLE", "0").strip().lower() in ("1", "true", "yes", "on")

class BeijingFormatter(logging.Formatter):
    def converter(self, timestamp):
        return datetime.fromtimestamp(timestamp, timezone.utc).astimezone(timezone(timedelta(hours=8)))
    def formatTime(self, record, datefmt=None):
        return self.converter(record.created).strftime('%Y-%m-%d %H:%M:%S')

file_fmt = BeijingFormatter('%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
from logging.handlers import RotatingFileHandler
file_handler = RotatingFileHandler(LOG_FILE_PATH, mode='a', encoding='utf-8', maxBytes=10*1024*1024, backupCount=3)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(file_fmt)

def _is_raw_chat_console_record(message):
    raw = str(message or "")
    if "[DELETED]" in raw or "[EDITED]" in raw:
        return True
    if "[MSG]" in raw and "Msg=" in raw:
        return True
    return False

def _redact_console_message(message):
    raw = str(message or "")
    raw = re.sub(r"全文:\n.*", "全文: [已隐藏]", raw, flags=re.S)
    raw = re.sub(r"内容: \[[^\]]*\]", "内容: [已隐藏]", raw)
    raw = re.sub(r"Text='[^']*'", "Text=[已隐藏]", raw)
    raw = re.sub(r"(客服编辑|客服回复|客户发言): \[[^\]]*\]", r"\1: [已隐藏]", raw)
    return raw

class PrivacyConsoleHandler(logging.StreamHandler):
    def emit(self, record):
        try:
            message = record.getMessage()
            if not CHAT_LOG_TO_CONSOLE:
                if _is_raw_chat_console_record(message):
                    return
                redacted = _redact_console_message(message)
                if redacted != message:
                    record = copy.copy(record)
                    record.msg = redacted
                    record.args = ()
            super().emit(record)
        except Exception:
            self.handleError(record)

console_handler = PrivacyConsoleHandler(sys.stdout)
_console_level_name = os.environ.get("BOT_CONSOLE_LOG_LEVEL", "DEBUG" if CHAT_LOG_TO_CONSOLE else "INFO").upper()
console_handler.setLevel(getattr(logging, _console_level_name, logging.INFO))
console_handler.setFormatter(file_fmt)

logger.addHandler(file_handler)
logger.addHandler(console_handler)

CHAT_LOG_DB = data_path('chat_logs.db')
CHAT_CONTEXT_RETENTION_DAYS = int(os.environ.get("CHAT_CONTEXT_RETENTION_DAYS", os.environ.get("CHAT_LOG_RETENTION_DAYS", "90")) or "90")
CHAT_AUDIT_RETENTION_DAYS = int(os.environ.get("CHAT_AUDIT_RETENTION_DAYS", "0") or "0")
CHAT_HISTORY_BACKFILL_LIMIT = int(os.environ.get("CHAT_HISTORY_BACKFILL_LIMIT", "500") or "0")
_db_lock = Lock()
_sse_clients = []
_sse_clients_lock = Lock()

def _init_db():
    with sqlite3.connect(CHAT_LOG_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            chat_id INTEGER,
            msg_type TEXT NOT NULL DEFAULT 'sys',
            raw TEXT NOT NULL
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_ts ON chat_logs(chat_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON chat_logs(ts)")
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uid TEXT NOT NULL UNIQUE,
            ts REAL NOT NULL,
            chat_id INTEGER,
            message_id INTEGER,
            event_type TEXT NOT NULL,
            sender_id INTEGER,
            sender_name TEXT,
            text TEXT,
            old_text TEXT,
            sender_role TEXT NOT NULL DEFAULT 'user',
            msg_type TEXT NOT NULL DEFAULT '文本',
            raw TEXT NOT NULL,
            reply_to_msg_id INTEGER,
            grouped_id INTEGER
        )""")
        try:
            conn.execute("ALTER TABLE chat_events ADD COLUMN sender_role TEXT NOT NULL DEFAULT 'user'")
        except sqlite3.OperationalError:
            pass
        conn.execute("""CREATE TABLE IF NOT EXISTS chat_message_snapshots (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            first_ts REAL NOT NULL,
            last_ts REAL NOT NULL,
            sender_id INTEGER,
            sender_name TEXT,
            sender_role TEXT NOT NULL DEFAULT 'user',
            text TEXT,
            msg_type TEXT NOT NULL DEFAULT '文本',
            reply_to_msg_id INTEGER,
            grouped_id INTEGER,
            is_deleted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(chat_id, message_id)
        )""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_events_chat_ts ON chat_events(chat_id, ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_events_ts ON chat_events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_events_message ON chat_events(chat_id, message_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_chat_ts ON chat_message_snapshots(chat_id, first_ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON chat_message_snapshots(first_ts)")
        conn.execute("""INSERT OR IGNORE INTO chat_message_snapshots(
            chat_id, message_id, first_ts, last_ts, sender_id, sender_name, sender_role,
            text, msg_type, reply_to_msg_id, grouped_id, is_deleted
        )
        SELECT chat_id, message_id, ts, ts, sender_id, sender_name, sender_role,
               text, msg_type, reply_to_msg_id, grouped_id, 0
        FROM chat_events
        WHERE event_type IN ('new', 'history') AND chat_id IS NOT NULL AND message_id IS NOT NULL""")
        conn.commit()

_init_db()

def _cleanup_old_logs():
    with _db_lock:
        with sqlite3.connect(CHAT_LOG_DB) as conn:
            if CHAT_CONTEXT_RETENTION_DAYS > 0:
                context_cutoff = time.time() - CHAT_CONTEXT_RETENTION_DAYS * 86400
                conn.execute("DELETE FROM chat_logs WHERE ts < ?", (context_cutoff,))
                conn.execute("DELETE FROM chat_events WHERE event_type IN ('new', 'history') AND ts < ?", (context_cutoff,))
                conn.execute("DELETE FROM chat_message_snapshots WHERE first_ts < ?", (context_cutoff,))
            if CHAT_AUDIT_RETENTION_DAYS > 0:
                audit_cutoff = time.time() - CHAT_AUDIT_RETENTION_DAYS * 86400
                conn.execute("DELETE FROM chat_events WHERE event_type IN ('edit', 'delete') AND ts < ?", (audit_cutoff,))
            conn.commit()
    # reschedule
    t = Thread(target=lambda: (time.sleep(86400), _cleanup_old_logs()), daemon=True)
    t.start()

_cleanup_old_logs()

def _event_uid(event_type, chat_id, message_id, ts=None):
    if event_type in ("new", "history"):
        return f"new:{chat_id}:{message_id}"
    return f"{event_type}:{chat_id}:{message_id}:{time.time_ns()}"

def _normalized_message_text(text):
    return re.sub(r"\s+", " ", (text or "").strip())

def _is_meaningful_edit_text(old_text, new_text):
    old_norm = _normalized_message_text(old_text)
    new_norm = _normalized_message_text(new_text)
    if not old_norm or old_norm == "[非文本/空]":
        return False
    return old_norm != new_norm

def _broadcast_chat_event(row):
    payload = json.dumps(row, ensure_ascii=False)
    dead_clients = []
    with _sse_clients_lock:
        for q in list(_sse_clients):
            try:
                q.put_nowait(payload)
            except Exception:
                dead_clients.append(q)
        for q in dead_clients:
            try:
                _sse_clients.remove(q)
            except ValueError:
                pass

def record_chat_event(event_type, chat_id, message_id, sender_id=None, sender_name=None,
                      text="", old_text="", msg_type="文本", ts=None, raw=None,
                      reply_to_msg_id=None, grouped_id=None, sender_role="user", broadcast=True):
    if not chat_id or not message_id:
        return None
    ts = float(ts or time.time())
    safe_text = text or ""
    safe_old_text = old_text or ""
    if raw is None:
        if event_type == "edit":
            raw = f"[EDITED] Msg={message_id} | [{chat_id}] {sender_name or 'Unknown'}: {safe_old_text} -> {safe_text}"
        elif event_type == "delete":
            raw = f"[DELETED] Msg={message_id} | [{chat_id}] {sender_name or 'Unknown'}: {safe_text or safe_old_text}"
        else:
            raw = f"[MSG] Msg={message_id} | [{chat_id}] {sender_name or 'Unknown'}: {safe_text} [{msg_type}]"
    row = {
        "ts": ts,
        "chat_id": chat_id,
        "message_id": message_id,
        "event_type": event_type,
        "source": "audit" if event_type in ("edit", "delete") else "context",
        "sender_id": sender_id,
        "sender_name": sender_name or "Unknown",
        "text": safe_text,
        "old_text": safe_old_text,
        "sender_role": sender_role or "user",
        "msg_type": msg_type or "文本",
        "raw": raw,
        "reply_to_msg_id": reply_to_msg_id,
        "grouped_id": grouped_id,
    }
    try:
        with _db_lock:
            with sqlite3.connect(CHAT_LOG_DB) as conn:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO chat_events(
                        event_uid, ts, chat_id, message_id, event_type, sender_id, sender_name,
                        text, old_text, sender_role, msg_type, raw, reply_to_msg_id, grouped_id
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        _event_uid(event_type, chat_id, message_id, ts), ts, chat_id, message_id,
                        event_type, sender_id, row["sender_name"], safe_text, safe_old_text,
                        row["sender_role"], row["msg_type"], raw, reply_to_msg_id, grouped_id
                    )
                )
                conn.commit()
                if cur.rowcount == 0:
                    return None
                row["id"] = cur.lastrowid
        if broadcast:
            _broadcast_chat_event(row)
        return row
    except Exception as e:
        logger.error(f"❌ chat_events 写入失败: {e}")
        return None

def upsert_message_snapshot(chat_id, message_id, sender_id=None, sender_name=None, text="",
                            msg_type="文本", ts=None, reply_to_msg_id=None, grouped_id=None,
                            sender_role="user", is_deleted=False, broadcast=True):
    if not chat_id or not message_id:
        return None
    ts = float(ts or time.time())
    row = {
        "id": f"ctx:{chat_id}:{message_id}",
        "source": "context",
        "ts": ts,
        "chat_id": chat_id,
        "message_id": message_id,
        "event_type": "new",
        "sender_id": sender_id,
        "sender_name": sender_name or "Unknown",
        "text": text or "",
        "old_text": "",
        "sender_role": sender_role or "user",
        "msg_type": msg_type or "文本",
        "raw": f"[MSG] Msg={message_id} | [{chat_id}] {sender_name or 'Unknown'}: {text or ''} [{msg_type or '文本'}]",
        "reply_to_msg_id": reply_to_msg_id,
        "grouped_id": grouped_id,
        "is_deleted": 1 if is_deleted else 0,
    }
    try:
        with _db_lock:
            with sqlite3.connect(CHAT_LOG_DB) as conn:
                existing = conn.execute(
                    "SELECT first_ts FROM chat_message_snapshots WHERE chat_id=? AND message_id=?",
                    (chat_id, message_id)
                ).fetchone()
                first_ts = existing[0] if existing else ts
                conn.execute(
                    """INSERT INTO chat_message_snapshots(
                        chat_id, message_id, first_ts, last_ts, sender_id, sender_name, sender_role,
                        text, msg_type, reply_to_msg_id, grouped_id, is_deleted
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(chat_id, message_id) DO UPDATE SET
                        last_ts=excluded.last_ts,
                        sender_id=excluded.sender_id,
                        sender_name=excluded.sender_name,
                        sender_role=excluded.sender_role,
                        text=excluded.text,
                        msg_type=excluded.msg_type,
                        reply_to_msg_id=excluded.reply_to_msg_id,
                        grouped_id=excluded.grouped_id,
                        is_deleted=excluded.is_deleted""",
                    (
                        chat_id, message_id, first_ts, ts, sender_id, row["sender_name"], row["sender_role"],
                        row["text"], row["msg_type"], reply_to_msg_id, grouped_id, row["is_deleted"]
                    )
                )
                conn.commit()
        if broadcast:
            _broadcast_chat_event(row)
        return row
    except Exception as e:
        logger.error(f"❌ chat_message_snapshots 写入失败: {e}")
        return None

def mark_message_snapshot_deleted(chat_id, message_id):
    try:
        with _db_lock:
            with sqlite3.connect(CHAT_LOG_DB) as conn:
                conn.execute(
                    "UPDATE chat_message_snapshots SET is_deleted=1, last_ts=? WHERE chat_id=? AND message_id=?",
                    (time.time(), chat_id, message_id)
                )
                conn.commit()
    except Exception:
        pass

def _snapshot_rows(chat_id=None, limit=600):
    with sqlite3.connect(CHAT_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        if chat_id:
            rows = conn.execute(
                """SELECT chat_id, message_id, first_ts, last_ts, sender_id, sender_name,
                          sender_role, text, msg_type, reply_to_msg_id, grouped_id, is_deleted
                   FROM chat_message_snapshots WHERE chat_id=? ORDER BY first_ts DESC LIMIT ?""",
                (chat_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT chat_id, message_id, first_ts, last_ts, sender_id, sender_name,
                          sender_role, text, msg_type, reply_to_msg_id, grouped_id, is_deleted
                   FROM chat_message_snapshots ORDER BY first_ts DESC LIMIT ?""",
                (limit,)
            ).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        result.append({
            "id": f"ctx:{row['chat_id']}:{row['message_id']}",
            "source": "context",
            "ts": row["first_ts"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "event_type": "new",
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"] or "Unknown",
            "text": row["text"] or "",
            "old_text": "",
            "sender_role": row["sender_role"] or "user",
            "msg_type": row["msg_type"] or "文本",
            "raw": f"[MSG] Msg={row['message_id']} | [{row['chat_id']}] {row['sender_name'] or 'Unknown'}: {row['text'] or ''} [{row['msg_type'] or '文本'}]",
            "reply_to_msg_id": row["reply_to_msg_id"],
            "grouped_id": row["grouped_id"],
            "is_deleted": row["is_deleted"] or 0,
        })
    return list(reversed(result))

def _audit_rows(chat_id=None, limit=600, event_type=None):
    with sqlite3.connect(CHAT_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        where = [
            "event_type IN ('edit', 'delete')",
            """(
                event_type!='edit'
                OR (
                    COALESCE(TRIM(old_text),'')!=''
                    AND COALESCE(TRIM(old_text),'')!='[非文本/空]'
                    AND COALESCE(TRIM(old_text),'')!=COALESCE(TRIM(text),'')
                )
            )"""
        ]
        params = []
        if chat_id:
            where.append("chat_id=?")
            params.append(chat_id)
        if event_type:
            where.append("event_type=?")
            params.append(event_type)
        where_sql = " AND ".join(where)
        params.append(limit)
        rows = conn.execute(
            f"""SELECT id, ts, chat_id, message_id, event_type, sender_id, sender_name,
                      text, old_text, sender_role, msg_type, raw, reply_to_msg_id, grouped_id
               FROM chat_events WHERE {where_sql} ORDER BY ts DESC, id DESC LIMIT ?""",
            tuple(params)
        ).fetchall()
    result = []
    for r in rows:
        item = dict(r)
        if item.get("event_type") == "edit" and not _is_meaningful_edit_text(item.get("old_text"), item.get("text")):
            continue
        item["source"] = "audit"
        result.append(item)
    return list(reversed(result))

def _chat_event_rows(chat_id=None, limit=600, mode="all"):
    if mode == "context":
        return _snapshot_rows(chat_id=chat_id, limit=limit)
    if mode == "audit":
        return _audit_rows(chat_id=chat_id, limit=limit)
    if mode == "edit":
        return _audit_rows(chat_id=chat_id, limit=limit, event_type="edit")
    if mode == "delete":
        return _audit_rows(chat_id=chat_id, limit=limit, event_type="delete")

    audit_limit = max(1, limit // 2)
    context_limit = max(1, limit - audit_limit)
    data = _snapshot_rows(chat_id=chat_id, limit=context_limit) + _audit_rows(chat_id=chat_id, limit=audit_limit)
    data.sort(key=lambda row: (row.get("ts") or 0, str(row.get("id") or "")))
    return data[-limit:]

def _legacy_chat_log_rows(chat_id=None, limit=600):
    with sqlite3.connect(CHAT_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        if chat_id:
            rows = conn.execute(
                "SELECT ts, chat_id, msg_type, raw FROM chat_logs WHERE chat_id=? ORDER BY ts DESC LIMIT ?",
                (chat_id, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT ts, chat_id, msg_type, raw FROM chat_logs ORDER BY ts DESC LIMIT ?",
                (limit,)
            ).fetchall()
    return [{"source": "legacy", "ts": r["ts"], "chat_id": r["chat_id"], "msg_type": r["msg_type"], "raw": r["raw"]} for r in reversed(rows)]

def _flow_rows(chat_id, message_id, window_seconds=0, limit=300):
    if not chat_id or not message_id:
        return []
    message_id = int(message_id)
    target_ts = None
    parent_id = None
    rows = []
    with sqlite3.connect(CHAT_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        target = conn.execute(
            """SELECT first_ts AS ts, reply_to_msg_id FROM chat_message_snapshots WHERE chat_id=? AND message_id=?
               UNION ALL
               SELECT ts, reply_to_msg_id FROM chat_events WHERE chat_id=? AND message_id=?
               ORDER BY ts LIMIT 1""",
            (chat_id, message_id, chat_id, message_id)
        ).fetchone()
        if target:
            target_ts = float(target["ts"])
            parent_id = target["reply_to_msg_id"]

        snapshot_sql = """SELECT chat_id, message_id, first_ts, last_ts, sender_id, sender_name,
                                 sender_role, text, msg_type, reply_to_msg_id, grouped_id, is_deleted
                          FROM chat_message_snapshots
                          WHERE chat_id=? AND (message_id=? OR reply_to_msg_id=?"""
        snapshot_params = [chat_id, message_id, message_id]
        if parent_id:
            snapshot_sql += " OR message_id=?"
            snapshot_params.append(parent_id)
        snapshot_sql += ") ORDER BY first_ts DESC LIMIT ?"
        snapshot_params.append(limit)
        snapshots = conn.execute(snapshot_sql, tuple(snapshot_params)).fetchall()

        audit_sql = """SELECT id, ts, chat_id, message_id, event_type, sender_id, sender_name,
                              text, old_text, sender_role, msg_type, raw, reply_to_msg_id, grouped_id
                       FROM chat_events
                       WHERE chat_id=? AND event_type IN ('edit', 'delete')
                         AND (message_id=? OR reply_to_msg_id=?"""
        audit_params = [chat_id, message_id, message_id]
        if parent_id:
            audit_sql += " OR message_id=?"
            audit_params.append(parent_id)
        audit_sql += ") ORDER BY ts DESC, id DESC LIMIT ?"
        audit_params.append(limit)
        audits = conn.execute(audit_sql, tuple(audit_params)).fetchall()

    for r in snapshots:
        row = dict(r)
        rows.append({
            "id": f"ctx:{row['chat_id']}:{row['message_id']}",
            "source": "context",
            "ts": row["first_ts"],
            "chat_id": row["chat_id"],
            "message_id": row["message_id"],
            "event_type": "new",
            "sender_id": row["sender_id"],
            "sender_name": row["sender_name"] or "Unknown",
            "text": row["text"] or "",
            "old_text": "",
            "sender_role": row["sender_role"] or "user",
            "msg_type": row["msg_type"] or "文本",
            "raw": f"[MSG] Msg={row['message_id']} | [{row['chat_id']}] {row['sender_name'] or 'Unknown'}: {row['text'] or ''} [{row['msg_type'] or '文本'}]",
            "reply_to_msg_id": row["reply_to_msg_id"],
            "grouped_id": row["grouped_id"],
            "is_deleted": row["is_deleted"] or 0,
        })
    for r in audits:
        item = dict(r)
        item["source"] = "audit"
        rows.append(item)
    rows.sort(key=lambda row: (row.get("ts") or 0, str(row.get("id") or "")))
    return rows[-limit:]

def get_last_stored_message_text(chat_id, message_id):
    try:
        with sqlite3.connect(CHAT_LOG_DB) as conn:
            row = conn.execute(
                "SELECT text FROM chat_message_snapshots WHERE chat_id=? AND message_id=? LIMIT 1",
                (chat_id, message_id)
            ).fetchone()
            if row:
                return row[0] or ""
            row = conn.execute(
                """SELECT text FROM chat_events
                   WHERE chat_id=? AND message_id=? AND event_type IN ('new', 'edit')
                   ORDER BY ts DESC, id DESC LIMIT 1""",
                (chat_id, message_id)
            ).fetchone()
        return row[0] if row else ""
    except Exception:
        return ""

_CHAT_ID_RE = re.compile(r'\[(-100\d+)\]')
_MSG_TYPE_MAP = [
    ('[MSG]', 'user'), ('[ALERT]', 'alert'), ('[AUDIT]', 'audit'),
    ('[DELETED]', 'deleted'), ('客服操作', 'cs'),
]

class SQLiteLogHandler(logging.Handler):
    def emit(self, record):
        try:
            raw = self.format(record)
            ts = record.created
            m = _CHAT_ID_RE.search(raw)
            chat_id = int(m.group(1)) if m else None
            msg_type = 'sys'
            for marker, t in _MSG_TYPE_MAP:
                if marker in raw:
                    msg_type = t
                    break
            if msg_type == 'sys':
                return
            with _db_lock:
                with sqlite3.connect(CHAT_LOG_DB) as conn:
                    conn.execute(
                        "INSERT INTO chat_logs(ts, chat_id, msg_type, raw) VALUES(?,?,?,?)",
                        (ts, chat_id, msg_type, raw)
                    )
                    conn.commit()
        except Exception:
            pass

_sqlite_handler = SQLiteLogHandler()
_sqlite_handler.setLevel(logging.DEBUG)
_sqlite_handler.setFormatter(file_fmt)
logger.addHandler(_sqlite_handler)

_group_name_cache = {}

logging.getLogger('werkzeug').setLevel(logging.ERROR)
logging.getLogger('telethon').setLevel(logging.WARNING)

_sys_opt = os.environ.get("OPTIMIZATION_LEVEL", "normal").lower() == "debug"

def log_tree(level, msg):
    prefix = ""
    if level == 0:   prefix = "[MSG] "
    elif level == 1: prefix = "  [+] "
    elif level == 2: prefix = "  [-] "
    elif level == 3: prefix = "[ALERT] "
    elif level == 4: prefix = "[AUDIT] "
    elif level == 9: prefix = "[ERROR] "
    
    full_msg = f"{prefix}{msg}"
    if _sys_opt or level >= 2: logger.info(full_msg)
    else: logger.debug(full_msg)

# ==========================================
# 动态模块加载 (Stats & Responder)
# ==========================================
try:
    from work_stats import init_stats_blueprint
except ImportError as e:
    logger.warning(f"⚠️ 统计模块加载失败: {e}")
    init_stats_blueprint = None

try:
    from monitor_responder import init_monitor, queue_site_inner_message_command, wait_backend_command_result, get_backend_command_progress
    logger.info("✅ 自动回复模块 (monitor_responder) 导入成功")
except ImportError as e:
    logger.error(f"❌ 自动回复模块导入失败: {e}")
    init_monitor = None
    queue_site_inner_message_command = None
    wait_backend_command_result = None
    get_backend_command_progress = None

# ==========================================
# 模块 1: 基础函数 (强力清洗版)
# ==========================================
def normalize(text):
    if not text: return ""
    text = text.lower()
    text = re.sub(r'[^\w=]', '', text) 
    return text

def extract_id_list(env_str):
    if not env_str: return []
    clean_str = env_str.replace("，", ",")
    items = clean_str.split(',')
    result = []
    for item in items:
        match = re.search(r'-?\d+', item)
        if match:
            try: result.append(int(match.group()))
            except: pass
    return result

def build_group_id_lookup(group_ids):
    lookup = set()
    for raw_id in group_ids or []:
        try:
            group_id = int(raw_id)
        except Exception:
            continue
        lookup.add(group_id)
        if group_id > 0:
            try:
                lookup.add(int(f"-100{group_id}"))
            except Exception:
                pass
        else:
            group_text = str(group_id)
            if group_text.startswith("-100") and len(group_text) > 4:
                try:
                    lookup.add(int(group_text[4:]))
                except Exception:
                    pass
    return lookup

def is_configured_cs_group(chat_id):
    try:
        return int(chat_id) in CONFIGURED_CS_GROUP_IDS
    except Exception:
        return False

WAIT_ALERT_REDIS_KEY = "wait_alert_config_v1"
wait_alert_config_lock = Lock()
wait_alert_redis_client = None
wait_alert_redis_checked = False
wait_alert_store_status = "未初始化"

def extract_signature_set(env_str):
    if not env_str: return set()
    if isinstance(env_str, (list, tuple, set)):
        return {str(x).strip() for x in env_str if str(x).strip()}
    clean_str = str(env_str)
    for sep in ("，", "；", ";", "|", "\n", "\r"):
        clean_str = clean_str.replace(sep, ",")
    return {x.strip() for x in clean_str.split(',') if x.strip()}

def extract_target_list(raw):
    if not raw:
        return []
    if isinstance(raw, (list, tuple, set)):
        items = raw
    else:
        items = re.split(r'[,，;；\s]+', str(raw))

    result = []
    seen = set()
    for item in items:
        target = str(item).strip()
        if not target or target in seen:
            continue
        seen.add(target)
        result.append(target)
    return result

def normalize_alert_routes(raw_routes, wait_signatures=None):
    if not isinstance(raw_routes, dict):
        return {}
    allowed = set(wait_signatures or [])
    routes = {}
    for keyword, targets in raw_routes.items():
        keyword = str(keyword).strip()
        if not keyword or (allowed and keyword not in allowed):
            continue
        clean_targets = extract_target_list(targets)
        if clean_targets:
            routes[keyword] = clean_targets
    return routes

def normalize_chat_id(target):
    target = str(target).strip()
    if re.fullmatch(r'-?\d+', target):
        try:
            return int(target)
        except Exception:
            return target
    return target

def get_wait_alert_redis_client():
    global wait_alert_redis_client, wait_alert_redis_checked, wait_alert_store_status
    if wait_alert_redis_checked:
        return wait_alert_redis_client
    wait_alert_redis_checked = True

    redis_url = os.environ.get("REDIS_URL") or os.environ.get("REDIS_URI") or os.environ.get("REDIS_PUBLIC_URL")
    if not redis or not redis_url:
        wait_alert_store_status = "内存模式：未配置 Redis，重启后恢复环境变量默认值"
        return None

    try:
        redis_url = redis_url.strip()
        wait_alert_redis_client = redis.from_url(redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
        wait_alert_redis_client.ping()
        wait_alert_store_status = "Redis：已持久化"
        logger.info("✅ [WaitAlert] Redis 配置存储连接成功")
    except Exception as e:
        wait_alert_redis_client = None
        wait_alert_store_status = f"内存模式：Redis 连接失败 ({e})"
        logger.warning(f"⚠️ [WaitAlert] Redis 连接失败，将仅使用内存配置: {e}")
    return wait_alert_redis_client

def load_wait_alert_config_from_store(wait_signatures):
    client = get_wait_alert_redis_client()
    if not client:
        return None, None, None
    try:
        raw = client.get(WAIT_ALERT_REDIS_KEY)
        if not raw:
            return None, None, None
        data = json.loads(raw)
        signatures = extract_signature_set(data.get("alert_wait_keywords", []))
        routes = normalize_alert_routes(data.get("alert_routes", {}), wait_signatures)
        return signatures, routes, "Redis网页配置"
    except Exception as e:
        logger.warning(f"⚠️ [WaitAlert] Redis 配置读取失败，回退环境变量: {e}")
        return None, None, None

def resolve_wait_alert_config(wait_signatures):
    stored_signatures, stored_routes, stored_source = load_wait_alert_config_from_store(wait_signatures)
    if stored_signatures is not None:
        return stored_signatures, stored_routes, stored_source

    raw = os.environ.get("ALERT_WAIT_KEYWORDS")
    source = "ALERT_WAIT_KEYWORDS"
    if raw is None:
        raw = os.environ.get("WAIT_ALERT_KEYWORDS")
        source = "WAIT_ALERT_KEYWORDS"

    if raw is None:
        return set(wait_signatures), {}, "默认全部"

    raw_text = str(raw).strip()
    raw_lower = raw_text.lower()
    if not raw_text or raw_lower in ("all", "*") or raw_text in ("全部", "全体"):
        return set(wait_signatures), {}, f"{source}=全部"
    if raw_lower in ("none", "off", "false", "0") or raw_text in ("关闭", "不预警"):
        return set(), {}, f"{source}=关闭"
    return extract_signature_set(raw), {}, source

def match_signature(text, signatures):
    if not text or not signatures: return None
    for signature in sorted(signatures, key=len, reverse=True):
        if signature and signature in text:
            return signature
    return None

def get_wait_alert_signatures():
    with wait_alert_config_lock:
        return set(WAIT_ALERT_SIGNATURES)

def get_wait_alert_routes():
    with wait_alert_config_lock:
        return {keyword: list(targets) for keyword, targets in WAIT_ALERT_ROUTES.items()}

def get_alert_targets_for_keyword(keyword):
    if not keyword:
        return None
    return get_wait_alert_routes().get(keyword)

def is_wait_keyword_alert_enabled(keyword):
    if not keyword:
        return True
    return keyword in get_wait_alert_signatures()

def persist_wait_alert_config(signatures, routes):
    global wait_alert_store_status
    payload = {
        "alert_wait_keywords": sorted(list(signatures)),
        "alert_routes": normalize_alert_routes(routes, WAIT_SIGNATURES),
        "updated_at": datetime.now(timezone(timedelta(hours=8))).isoformat()
    }
    client = get_wait_alert_redis_client()
    if not client:
        return False, wait_alert_store_status
    try:
        client.set(WAIT_ALERT_REDIS_KEY, json.dumps(payload, ensure_ascii=False))
        wait_alert_store_status = "Redis：已持久化"
        return True, wait_alert_store_status
    except Exception as e:
        wait_alert_store_status = f"内存模式：Redis 保存失败 ({e})"
        logger.warning(f"⚠️ [WaitAlert] Redis 配置保存失败: {e}")
        return False, wait_alert_store_status

# ==========================================
# 模块 2: 配置加载
# ==========================================
try:
    API_ID = int(os.environ["API_ID"])
    API_HASH = os.environ["API_HASH"]
    SESSION_STRING = os.environ["SESSION_STRING"]
    BOT_TOKEN = os.environ["BOT_TOKEN"]
    cs_groups_env = os.environ["CS_GROUP_IDS"]
    CS_GROUP_IDS = extract_id_list(cs_groups_env)
    CONFIGURED_CS_GROUP_IDS = build_group_id_lookup(CS_GROUP_IDS)
    alert_env = os.environ["ALERT_GROUP_ID"]
    ALERT_GROUP_IDS = extract_id_list(alert_env)
    other_cs_env = os.environ.get("OTHER_CS_IDS", "")
    OTHER_CS_IDS = extract_id_list(other_cs_env)
    
    wait_keywords_env = os.environ["WAIT_KEYWORDS"]
    WAIT_SIGNATURES = extract_signature_set(wait_keywords_env)
    WAIT_ALERT_SIGNATURES, WAIT_ALERT_ROUTES, WAIT_ALERT_SOURCE = resolve_wait_alert_config(WAIT_SIGNATURES)
    unknown_alert_wait = WAIT_ALERT_SIGNATURES - WAIT_SIGNATURES
    if unknown_alert_wait:
        logger.warning(f"⚠️ {WAIT_ALERT_SOURCE} 中有 {len(unknown_alert_wait)} 个词不在 WAIT_KEYWORDS 内，不会触发稍等预警: {sorted(list(unknown_alert_wait))}")
        WAIT_ALERT_SIGNATURES = WAIT_ALERT_SIGNATURES & WAIT_SIGNATURES
    WAIT_ALERT_ROUTES = normalize_alert_routes(WAIT_ALERT_ROUTES, WAIT_SIGNATURES)

    keep_keywords_env = os.environ.get("KEEP_KEYWORDS", "") 
    if '|' in keep_keywords_env:
        keep_list = keep_keywords_env.split('|')
    else:
        keep_clean = keep_keywords_env.replace("，", ",")
        keep_list = keep_clean.split(',')
        
    KEEP_SIGNATURES = {x.strip() for x in keep_list if x.strip()}
    
    log_tree(0, f"🔍 关键词配置: CS_GROUPS={len(CONFIGURED_CS_GROUP_IDS)} | WAIT={len(WAIT_SIGNATURES)} | WAIT_ALERT={len(WAIT_ALERT_SIGNATURES)} ({WAIT_ALERT_SOURCE}) | KEEP={len(KEEP_SIGNATURES)}")

    default_ignore = (
        "好,1,不用了,到了,好的,谢谢,收到,明白,好的谢谢,ok,好滴,"
        "好的呢,嗯,嗯嗯,谢了,okk,k,行,妥,了解,已收,没问题,好的收到,ok了,麻烦了,"
        "好的感谢,哦,知道了,好的知道了,没事了"
    )
    ignore_env = os.environ.get("IGNORE_KEYWORDS", default_ignore)
    clean_ignore = ignore_env.replace("，", ",")
    IGNORE_SIGNATURES = {normalize(x) for x in clean_ignore.split(',') if x.strip()}
    
    CS_NAME_PREFIXES = ["YY_6/9_值班号", "Y_YY"]

    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
    GEMINI_MODEL = (os.environ.get("GEMINI_MODEL") or "gemini-3.5-flash").strip() or "gemini-3.5-flash"

except Exception as e:
    logger.error(f"❌ 配置错误: {e}")
    sys.exit(1)

log_tree(0, f"系统启动 | 稍等词: {len(WAIT_SIGNATURES)} | 稍等预警词: {len(WAIT_ALERT_SIGNATURES)} | 跟进词: {len(KEEP_SIGNATURES)} | 忽略词: {len(IGNORE_SIGNATURES)}")

# ==========================================
# 模块 3: 全局状态
# ==========================================
WAIT_TIMEOUT = 12 * 60
FOLLOWUP_TIMEOUT = 15 * 60
REPLY_TIMEOUT = 5 * 60
SELF_REPLY_TIMEOUT = 3 * 60 

MAX_CACHE_SIZE = 50000 
WAIT_CHECK_ALL_RATE_LIMIT_SECONDS = 60 * 60
WAIT_CHECK_ALL_RATE_LIMIT_REDIS_KEY = "wait_check_all_rate_limit_v1"

wait_tasks = {}
followup_tasks = {} 
reply_tasks = {}
self_reply_tasks = {} 
wait_task_keywords = {}

wait_timers = {}
followup_timers = {}
reply_timers = {}
self_reply_timers = {} 

# [重要] 映射表：CS回复的消息ID -> 客户原始消息ID
wait_msg_map = {}        
followup_msg_map = {} 
deleted_cache = deque(maxlen=10000)
self_reply_dedup = deque(maxlen=1000) 

chat_user_active_msgs = {}
chat_thread_active_msgs = {}

msg_to_user_cache = {} 
msg_content_cache = {}
msg_group_cache = {}
group_to_user_cache = {}
group_to_msg_ids_cache = {}

cs_activity_log = {}

IS_WORKING = True
MY_ID = None
bot_loop = None
stop_work_lock = None
wait_check_all_rate_lock = Lock()
wait_check_all_last_request_ts = 0.0

BEIJING_TZ = timezone(timedelta(hours=8))
ZC_BATCH_STATE = {}
ZC_BATCH_TTL_SECONDS = 6 * 60 * 60
SITE_MESSAGE_CHUNK_STATE = {}
SITE_MESSAGE_CHUNK_TTL_SECONDS = 5 * 60
SITE_MESSAGE_STRATEGIES = {"sb", "9zc", "6zc"}
SITE_MESSAGE_MEMBER_RE = re.compile(r"[a-z0-9_@.\-]{3,32}", re.IGNORECASE)
SB_REPORT_WINDOWS = [
    (0, 0, "00:00-00:59"),
    (1, 1, "01:00-01:59"),
    (2, 2, "02:00-02:59"),
    (3, 3, "03:00-03:59"),
    (4, 6, "04:00-06:59"),
    (7, 9, "07:00-09:59"),
    (10, 12, "10:00-12:59"),
    (13, 15, "13:00-15:59"),
    (16, 17, "16:00-17:59"),
    (18, 18, "18:00-18:59"),
    (19, 19, "19:00-19:59"),
    (20, 20, "20:00-20:59"),
    (21, 21, "21:00-21:59"),
    (22, 22, "22:00-22:59"),
    (23, 23, "23:00-23:59"),
]
WAIT_CHECK_SHIFT_WINDOWS = {
    "早班全体": ("12:30", "21:00"),
    "中班全体": ("20:45", "05:00"),
    "晚班全体": ("04:45", "13:00"),
}
WAIT_CHECK_REPLY_CONTINUATION_MAX_MESSAGES = 3
WAIT_CHECK_REPLY_CONTINUATION_SECONDS = 180
WAIT_CHECK_CONTINUATION_PHRASES = [
    "还是", "也是", "一样", "同样", "上面", "上一个", "刚才", "刚刚", "这个", "这个也是",
    "不是这个", "就是这个", "这个不对", "不对", "错了", "有问题", "p图", "P图", "图片",
    "截图", "看这个", "这个图", "这个图片", "补充", "补充一下", "对", "嗯", "是的"
]
WAIT_CHECK_NEW_QUESTION_HINTS = [
    "另外", "还有", "再问", "顺便", "第二个", "另一个", "新问题", "订单", "充值", "提现",
    "钱包", "冻结", "未到账", "不到账", "金额", "地址", "哈希", "hash", "卡号", "转账",
    "支付", "付款", "收款", "提款", "存款", "为什么", "怎么", "怎么办", "处理", "查询"
]
WAIT_CHECK_NOISE_TEXT_RE = re.compile(r"^[a-zA-Z0-9+._·。…!！?？,，、\\-\\s]{1,16}$")
WAIT_CHECK_ML_MENTION_ALLOWLIST = {
    "@ML_YYZB1", "@ML_YYZB2", "@ML_YYZB3", "@ML_YYZB4", "@ML_YYZB5", "@ML_YYZB6", "@ML_YYZB7"
}

def is_all_wait_check_keyword(keyword):
    return keyword in ["全体", "全体检测"] or keyword in WAIT_CHECK_SHIFT_WINDOWS

def get_wait_check_shift_window(keyword):
    if keyword not in WAIT_CHECK_SHIFT_WINDOWS:
        return None

    start_text, end_text = WAIT_CHECK_SHIFT_WINDOWS[keyword]
    start_hour, start_minute = map(int, start_text.split(":"))
    end_hour, end_minute = map(int, end_text.split(":"))

    now = datetime.now(BEIJING_TZ)
    today_start = now.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    today_end = now.replace(hour=end_hour, minute=end_minute, second=0, microsecond=0)

    if today_end <= today_start:
        if now >= today_start:
            start_time = today_start
            end_time = today_end + timedelta(days=1)
        elif now <= today_end:
            start_time = today_start - timedelta(days=1)
            end_time = today_end
        else:
            start_time = today_start - timedelta(days=1)
            end_time = today_end
    else:
        if now < today_start:
            start_time = today_start - timedelta(days=1)
            end_time = today_end - timedelta(days=1)
        else:
            start_time = today_start
            end_time = today_end

    scan_end_time = min(now, end_time)
    return start_time, scan_end_time, start_text, end_text

def is_obvious_reply_continuation(text):
    clean_text = (text or "").strip()
    if not clean_text:
        return True
    lower_text = clean_text.lower()
    if any(hint.lower() in lower_text for hint in WAIT_CHECK_NEW_QUESTION_HINTS):
        return False
    if len(clean_text) <= 12 and any(phrase.lower() in lower_text for phrase in WAIT_CHECK_CONTINUATION_PHRASES):
        return True
    if clean_text in ["?", "？", "??", "？？", "。。。", "..."]:
        return True
    return False

def is_obvious_new_question(text):
    clean_text = (text or "").strip()
    if not clean_text:
        return False
    lower_text = clean_text.lower()
    return any(hint.lower() in lower_text for hint in WAIT_CHECK_NEW_QUESTION_HINTS)

def get_obvious_noise_reason(text):
    clean_text = (text or "").strip()
    if not clean_text:
        return "空消息或无文本内容"
    if is_obvious_new_question(clean_text):
        return None
    if clean_text in ["?", "？", "??", "？？", "。。。", "...", "·"]:
        return "无意义符号或单独标点"
    if len(clean_text) == 1 and re.fullmatch(r"[a-zA-Z0-9·。.!！?？]", clean_text):
        return "单字符无意义内容"
    if WAIT_CHECK_NOISE_TEXT_RE.fullmatch(clean_text):
        meaningful_chars = re.findall(r"[\u4e00-\u9fff]", clean_text)
        if not meaningful_chars:
            return "乱码、测试字符或无业务诉求内容"
    return None

def get_mention_exempt_reason(text):
    clean_text = text or ""
    mentions = set(re.findall(r"@[A-Za-z0-9_]+", clean_text))
    if not mentions:
        return None
    if mentions & WAIT_CHECK_ML_MENTION_ALLOWLIST:
        return None
    return "消息仅在催促或通知非指定 ML 值班账号，按班次遗漏规则忽略"

def format_wait_seconds(seconds):
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}小时{minutes}分钟"
    if minutes:
        return f"{minutes}分钟{sec}秒"
    return f"{sec}秒"

def try_acquire_wait_check_all_slot():
    global wait_check_all_last_request_ts

    now = time.time()
    with wait_check_all_rate_lock:
        client = get_wait_alert_redis_client()
        if client:
            try:
                acquired = client.set(
                    WAIT_CHECK_ALL_RATE_LIMIT_REDIS_KEY,
                    str(now),
                    nx=True,
                    ex=WAIT_CHECK_ALL_RATE_LIMIT_SECONDS
                )
                if acquired:
                    return True, 0
                ttl = client.ttl(WAIT_CHECK_ALL_RATE_LIMIT_REDIS_KEY)
                if ttl is None or ttl < 0:
                    ttl = WAIT_CHECK_ALL_RATE_LIMIT_SECONDS
                return False, ttl
            except Exception as e:
                log_tree(9, f"全体闭环检测限频 Redis 异常，改用当前进程限频: {e}")

        remaining = wait_check_all_last_request_ts + WAIT_CHECK_ALL_RATE_LIMIT_SECONDS - now
        if remaining > 0:
            return False, remaining

        wait_check_all_last_request_ts = now
        return True, 0

def update_msg_cache(chat_id, msg_id, user_id, grouped_id=None):
    key = (chat_id, msg_id)
    if len(msg_to_user_cache) >= MAX_CACHE_SIZE:
        try: msg_to_user_cache.pop(next(iter(msg_to_user_cache)))
        except StopIteration: pass
    msg_to_user_cache[key] = user_id
    if grouped_id:
        g_key = (chat_id, grouped_id)
        msg_group_cache[key] = grouped_id
        if len(group_to_user_cache) >= 5000:
             if g_key not in group_to_user_cache:
                 try: group_to_user_cache.pop(next(iter(group_to_user_cache)))
                 except StopIteration: pass
        group_to_user_cache[g_key] = user_id
        if len(group_to_msg_ids_cache) >= 5000 and g_key not in group_to_msg_ids_cache:
            try:
                old_group_key = next(iter(group_to_msg_ids_cache))
                del group_to_msg_ids_cache[old_group_key]
            except StopIteration:
                pass
        group_to_msg_ids_cache.setdefault(g_key, set()).add(msg_id)

def get_cached_album_msg_ids(chat_id, msg_id):
    grouped_id = msg_group_cache.get((chat_id, msg_id))
    if not grouped_id:
        return set()
    return set(group_to_msg_ids_cache.get((chat_id, grouped_id), set()))

def get_ordered_reply_target_ids(message):
    ids = []

    def add_id(value):
        try:
            value = int(value)
        except Exception:
            return
        if value and value not in ids:
            ids.append(value)

    add_id(getattr(message, "reply_to_msg_id", None))
    reply_to = getattr(message, "reply_to", None)
    if reply_to:
        add_id(getattr(reply_to, "reply_to_msg_id", None))
        add_id(getattr(reply_to, "reply_to_top_id", None))
    return ids

def get_primary_reply_target_id(message):
    reply_ids = get_ordered_reply_target_ids(message)
    return reply_ids[0] if reply_ids else None

def get_related_album_msg_ids(chat_id, msg_id):
    related_ids = {msg_id} if msg_id else set()
    for album_msg_id in list(related_ids):
        related_ids.update(get_cached_album_msg_ids(chat_id, album_msg_id))
    return related_ids

def update_content_cache(chat_id, msg_id, name, text):
    key = (chat_id, msg_id)
    if len(msg_content_cache) >= MAX_CACHE_SIZE:
        try: msg_content_cache.pop(next(iter(msg_content_cache)))
        except StopIteration: pass
    safe_text = text[:100].replace('\n', ' ') if text else "[非文本/空]"
    msg_content_cache[key] = {'name': name, 'text': safe_text}

def record_cs_activity(chat_id, user_id=None, thread_id=None, timestamp=None):
    if timestamp is None: 
        timestamp = time.time()
    
    if user_id: 
        cs_activity_log[(chat_id, user_id)] = timestamp
        
    if thread_id: 
        cs_activity_log[(chat_id, thread_id)] = timestamp

def get_thread_context(event):
    if not event.message.reply_to: return None, None
    r = event.message.reply_to
    if r.reply_to_top_id: return r.reply_to_top_id, "Topic"
    if r.reply_to_msg_id: return r.reply_to_msg_id, "Reply"
    return None, None

async def is_official_cs(message):
    if not message: return False
    sender_id = message.sender_id
    if (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS): return True
    try:
        sender = await message.get_sender()
        if not sender: return False
        name = getattr(sender, 'first_name', '') or ''
        for prefix in CS_NAME_PREFIXES:
            if name.startswith(prefix): return True
    except: pass
    return False

async def get_cs_message_signature(message, signatures):
    if not message or not getattr(message, "text", None):
        return None

    is_cs = False
    if message.sender_id in ([MY_ID] + OTHER_CS_IDS):
        is_cs = True
    else:
        try:
            sender = await message.get_sender()
            if sender and getattr(sender, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)):
                is_cs = True
        except Exception:
            pass

    if not is_cs:
        return None
    return match_signature(message.text, signatures)

async def find_wait_keyword_in_history(chat_id, thread_id=None, limit=30, signatures=None):
    try:
        target_signatures = WAIT_SIGNATURES if signatures is None else signatures
        if not target_signatures:
            return None

        kwargs = {'limit': limit}
        if thread_id:
            root_msg = await client.get_messages(chat_id, ids=thread_id)
            root_keyword = await get_cs_message_signature(root_msg, target_signatures)
            if root_keyword:
                return root_keyword
            kwargs['reply_to'] = thread_id
             
        async for m in client.iter_messages(chat_id, **kwargs):
            keyword = await get_cs_message_signature(m, target_signatures)
            if keyword:
                return keyword
    except Exception as e:
        logger.error(f"History check failed: {e}")
        return None
    return None

async def check_wait_in_history(chat_id, thread_id=None, limit=30, signatures=None):
    try:
        return bool(await find_wait_keyword_in_history(chat_id, thread_id, limit, signatures))
    except Exception as e:
        logger.error(f"History check failed: {e}")
        return False

async def maintenance_task():
    while True:
        try:
            await asyncio.sleep(600)
            now = time.time()
            expired_keys = [k for k, v in cs_activity_log.items() if now - v > 3600]
            for k in expired_keys: del cs_activity_log[k]
        except Exception as e: logger.error(f"维护任务出错: {e}")

# ==========================================
# 模块 4: Web 控制台
# ==========================================
app = Flask(__name__)

COPY_PAGE_TTL_SECONDS = 8 * 60 * 60
COPY_PAGE_REDIS_PREFIX = "bot_copy_page:"
copy_page_cache = {}
copy_page_cache_lock = Lock()
DEFAULT_PUBLIC_BASE_URL = "https://cshelp.zeabur.app"
LEGACY_PUBLIC_BASE_URLS = {
    "https://arcshelp.zeabur.app",
    "http://arcshelp.zeabur.app",
}
PUBLIC_BASE_URL_ENV_NAMES = (
    "BOT_MENU_URL",
    "EXTENSION_BOT_BASE",
    "BOT_BASE_URL",
    "WEBAPP_URL",
    "WEB_APP_URL",
    "PUBLIC_URL",
    "ZEABUR_WEB_URL",
)

COPY_PAGE_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <style>
        *{box-sizing:border-box}
        body{margin:0;background:#f6f7f9;color:#111827;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:18px}
        main{max-width:860px;margin:0 auto}
        h1{font-size:18px;margin:0 0 6px;font-weight:700}
        .sub{font-size:12px;color:#6b7280;margin-bottom:16px}
        .section{background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin-bottom:12px}
        .section-head{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
        h2{font-size:14px;margin:0;font-weight:700}
        button{border:0;background:#111827;color:#fff;border-radius:6px;padding:8px 12px;font-size:13px;font-weight:700;cursor:pointer;white-space:nowrap}
        button.done{background:#16a34a}
        textarea{width:100%;min-height:130px;border:1px solid #d1d5db;border-radius:6px;padding:10px;font:13px/1.55 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre;overflow:auto;resize:vertical;background:#fafafa;color:#111827}
        .hint{font-size:12px;color:#6b7280;margin-top:8px}
    </style>
</head>
<body>
<main>
    <h1>{{ title }}</h1>
    <div class="sub">有效期 8 小时。表格格式请点这里复制，浏览器会保留制表符。</div>
    {% for section in sections %}
    <div class="section">
        <div class="section-head">
            <h2>{{ section.title }}</h2>
            <button type="button" onclick="copyArea('copy-{{ loop.index0 }}', this)">复制</button>
        </div>
        <textarea id="copy-{{ loop.index0 }}" readonly>{{ section.text }}</textarea>
        <div class="hint">也可以点进文本框后全选复制。</div>
    </div>
    {% endfor %}
</main>
<script>
async function copyArea(id, btn){
    const el = document.getElementById(id);
    const text = el.value;
    try {
        if (navigator.clipboard && window.isSecureContext) {
            await navigator.clipboard.writeText(text);
        } else {
            el.focus();
            el.select();
            document.execCommand('copy');
            window.getSelection().removeAllRanges();
        }
        const oldText = btn.textContent;
        btn.textContent = '已复制';
        btn.classList.add('done');
        setTimeout(() => {
            btn.textContent = oldText;
            btn.classList.remove('done');
        }, 1200);
    } catch (e) {
        el.focus();
        el.select();
    }
}
</script>
</body>
</html>
"""

def get_public_base_url():
    for name in PUBLIC_BASE_URL_ENV_NAMES:
        raw_url = str(os.environ.get(name) or "").strip().rstrip("/")
        if not raw_url:
            continue
        if raw_url in LEGACY_PUBLIC_BASE_URLS:
            return DEFAULT_PUBLIC_BASE_URL
        if raw_url.startswith("https://"):
            return raw_url
    return DEFAULT_PUBLIC_BASE_URL

def store_copy_page(title, sections):
    clean_sections = []
    for section in sections or []:
        section_title = str(section.get("title") or "复制内容").strip()[:60]
        section_text = str(section.get("text") or "")
        if section_text:
            clean_sections.append({"title": section_title, "text": section_text})
    if not clean_sections:
        return None

    base_url = get_public_base_url()
    if not base_url:
        log_tree(9, "网页复制链接未生成：缺少 BOT_MENU_URL/WEBAPP_URL/PUBLIC_URL 等 https 公网地址")
        return None

    token = secrets.token_urlsafe(16)
    payload = {
        "title": str(title or "复制内容").strip()[:80],
        "sections": clean_sections,
        "created_at": time.time(),
    }
    redis_key = COPY_PAGE_REDIS_PREFIX + token
    client = get_wait_alert_redis_client()
    if client:
        try:
            client.setex(redis_key, COPY_PAGE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"⚠️ [CopyPage] Redis 写入失败，回退内存: {e}")
            client = None

    if not client:
        now_ts = time.time()
        with copy_page_cache_lock:
            for key, item in list(copy_page_cache.items())[:200]:
                if not isinstance(item, dict) or item.get("expires_at", 0) <= now_ts:
                    copy_page_cache.pop(key, None)
            copy_page_cache[token] = {
                "payload": payload,
                "expires_at": now_ts + COPY_PAGE_TTL_SECONDS,
            }

    return f"{base_url}/copy/{token}"

def get_copy_page_token_from_url(url):
    match = re.search(r"/copy/([A-Za-z0-9_-]{12,64})(?:$|[?#])", str(url or ""))
    return match.group(1) if match else None

def save_copy_page_payload(token, payload):
    token = str(token or "").strip()
    if not token or not isinstance(payload, dict):
        return False
    redis_key = COPY_PAGE_REDIS_PREFIX + token
    client = get_wait_alert_redis_client()
    if client:
        try:
            client.setex(redis_key, COPY_PAGE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as e:
            logger.warning(f"⚠️ [CopyPage] Redis 更新失败，回退内存: {e}")

    with copy_page_cache_lock:
        copy_page_cache[token] = {
            "payload": payload,
            "expires_at": time.time() + COPY_PAGE_TTL_SECONDS,
        }
    return True

def load_copy_page(token):
    token = str(token or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{12,64}", token):
        return None

    client = get_wait_alert_redis_client()
    if client:
        try:
            raw = client.get(COPY_PAGE_REDIS_PREFIX + token)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning(f"⚠️ [CopyPage] Redis 读取失败，尝试内存: {e}")

    with copy_page_cache_lock:
        item = copy_page_cache.get(token)
        if not item:
            return None
        if item.get("expires_at", 0) <= time.time():
            copy_page_cache.pop(token, None)
            return None
        return item.get("payload")

def attach_copy_page_delete_targets(token, chat_id, message_ids):
    payload = load_copy_page(token)
    if not payload:
        return
    clean_ids = []
    seen = set()
    for raw_id in message_ids or []:
        try:
            message_id = int(raw_id)
        except Exception:
            continue
        if message_id > 0 and message_id not in seen:
            seen.add(message_id)
            clean_ids.append(message_id)
    if not clean_ids:
        return
    payload["delete_chat_id"] = chat_id
    payload["delete_message_ids"] = clean_ids
    save_copy_page_payload(token, payload)

def delete_copy_page_messages(payload):
    if not BOT_TOKEN or not isinstance(payload, dict):
        return
    chat_id = payload.get("delete_chat_id")
    message_ids = payload.get("delete_message_ids") or []
    if not chat_id or not message_ids:
        return
    for message_id in dict.fromkeys(message_ids):
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
                json={"chat_id": chat_id, "message_id": message_id},
                timeout=10
            )
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            if resp.status_code != 200 or not data.get("ok"):
                log_tree(9, f"网页复制后删除消息失败: chat={chat_id} msg={message_id} HTTP {resp.status_code} {str(data)[:120]}")
        except Exception as e:
            log_tree(9, f"网页复制后删除消息异常: chat={chat_id} msg={message_id} {e}")

DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <title>监控面板</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{box-sizing:border-box}
        :root{--bg:#f9fafb;--card:#fff;--border:#e5e7eb;--text:#111827;--muted:#6b7280;--primary:#2563eb;--green:#16a34a;--red:#dc2626}
        body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;padding:16px;max-width:580px;margin:0 auto}
        svg.ic{width:15px;height:15px;vertical-align:middle;stroke:currentColor;stroke-width:2;fill:none;stroke-linecap:round;stroke-linejoin:round;display:inline-block}
        .hd{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--border);padding-bottom:12px;margin-bottom:18px}
        .hd h1{margin:0;font-size:16px;font-weight:700;display:flex;align-items:center;gap:7px;color:var(--text)}
        .hd-right{display:flex;gap:8px;align-items:center}
        .tag{padding:3px 9px;border-radius:5px;color:#fff;font-weight:700;font-size:11px;letter-spacing:.4px}
        .on{background:var(--green)}.off{background:var(--red)}
        .cbtn{padding:4px 10px;border:1px solid var(--border);background:var(--card);cursor:pointer;border-radius:5px;font-size:12px;text-decoration:none;color:var(--muted);font-weight:500;transition:background .15s}
        .cbtn:hover{background:#f3f4f6}
        .abtn{cursor:pointer;display:flex;align-items:center;justify-content:center;width:28px;height:28px;border-radius:5px;background:var(--card);border:1px solid var(--border);color:var(--muted);transition:color .15s}
        .abtn:hover{color:var(--text);background:#f3f4f6}
        .box{margin-bottom:20px}
        .btitle{font-weight:600;font-size:12px;color:var(--muted);margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;text-transform:uppercase;letter-spacing:.04em}
        .dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px}
        .card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:6px;display:flex;justify-content:space-between;align-items:flex-start;gap:8px}
        .countdown{font-family:ui-monospace,Menlo,monospace;font-weight:700;font-size:16px;color:#db2777;white-space:nowrap;align-self:center}
        .late{color:var(--red);animation:flash 1.4s infinite}
        .empty{color:#9ca3af;text-align:center;padding:14px;font-size:12px;background:#f9fafb;border-radius:6px;border:1px dashed var(--border)}
        .cpbtn{font-size:11px;color:var(--primary);text-decoration:none;display:inline-flex;align-items:center;gap:3px;margin-top:4px;cursor:pointer;padding:3px 7px;background:#eff6ff;border-radius:4px;border:1px solid #bfdbfe;transition:background .15s}
        .cpbtn:hover{background:#dbeafe}
        .navbtn{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;padding:11px;background:#1e293b;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;margin-top:10px;font-size:13px;transition:opacity .2s}
        .navbtn:hover{opacity:.88}
        .navfoot{text-align:center;color:#9ca3af;margin-top:18px;font-size:11px}
        @keyframes flash{0%,100%{opacity:1}50%{opacity:.35}}
    </style>
</head>
<body>
    <div class="hd">
        <h1>
            <svg class="ic" style="color:var(--primary)" viewBox="0 0 24 24"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>
            实时监控
        </h1>
        <div class="hd-right">
            <div class="abtn" onclick="toggleAudio()" title="报警音" id="audio-icon"></div>
            <a href="#" onclick="ctrl(1);return false;" class="cbtn">上班</a>
            <a href="#" onclick="ctrl(0);return false;" class="cbtn">下班</a>
            <div id="status-tag-area">
                <div class="tag {{ 'on' if working else 'off' }}">{{ 'ON' if working else 'OFF' }}</div>
            </div>
        </div>
    </div>

    <div id="silent-task-container">
        {% for title, timers, color in [('稍等 (12m)', w, '#f59e0b'), ('跟进 (15m)', f, '#3b82f6'), ('漏回 (5m)', r, '#ef4444'), ('自回 (3m)', s, '#8b5cf6')] %}
        <div class="box">
            <div class="btitle">
                <div><span class="dot" style="background:{{color}}"></span>{{ title }}</div>
                <span>{{ timers|length }}</span>
            </div>
            {% if timers %}
                {% for mid, info in timers.items() %}
                <div class="card">
                    <div>
                        <strong style="font-size:13px">{{ info.user }}</strong>
                        {% if title == '漏回 (5m)' and info.target %}
                            <span style="font-size:12px;color:var(--muted)"> → {{ info.target }}</span>
                        {% endif %}
                        <span class="cpbtn" onclick="copyLink('{{ info.url }}', this)">
                            <svg class="ic" style="width:11px;height:11px" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>复制链接
                        </span>
                    </div>
                    <span class="countdown" data-end="{{ info.ts }}">--:--</span>
                </div>
                {% endfor %}
            {% else %}<div class="empty">暂无任务</div>{% endif %}
        </div>
        {% endfor %}
    </div>

    <div style="margin-top:24px;border-top:1px solid var(--border);padding-top:18px">
        <a href="/tool/wait_check" class="navbtn" style="background:#0f766e">
            <svg class="ic" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 9.36l-7.1 7.1a1 1 0 0 1-1.41 0l-1.42-1.42a1 1 0 0 1 0-1.4l7.1-7.1a6 6 0 0 1 9.36-7.94l-3.76 3.76z"/></svg> 闭环检测
        </a>
        <a href="/tool/wait_alerts" class="navbtn" style="background:#be123c">
            <svg class="ic" viewBox="0 0 24 24"><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 7h18s-3 0-3-7"/><path d="M13.73 21a2 2 0 0 1-3.46 0"/></svg> 稍等预警名单
        </a>
        <a href="/tool/work_stats" class="navbtn" style="background:#6d28d9">
            <svg class="ic" viewBox="0 0 24 24"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg> 工作量统计
        </a>
    </div>
    <div class="navfoot">v45.22</div>

    <script>
        const svgOn = '<svg class="icon" viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M15.54 8.46a5 5 0 0 1 0 7.07"></path><path d="M19.07 4.93a10 10 0 0 1 0 14.14"></path></svg>';
        const svgOff = '<svg class="icon" viewBox="0 0 24 24"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><line x1="23" y1="9" x2="17" y2="15"></line><line x1="17" y1="9" x2="23" y2="15"></line></svg>';
        let savedState = localStorage.getItem('tg_bot_audio_enabled');
        let audioEnabled = savedState === null ? true : (savedState === 'true');
        const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        const audioBtn = document.getElementById('audio-icon');
        if (audioBtn) { audioBtn.innerHTML = audioEnabled ? svgOn : svgOff; }

        function playAlarm() { 
            if (!audioEnabled) return; 
            if (audioCtx.state === 'suspended') audioCtx.resume().catch(e => console.log(e)); 
            const oscillator = audioCtx.createOscillator(); 
            const gainNode = audioCtx.createGain(); 
            oscillator.type = 'square'; 
            oscillator.frequency.setValueAtTime(800, audioCtx.currentTime); 
            oscillator.frequency.exponentialRampToValueAtTime(400, audioCtx.currentTime + 0.1); 
            gainNode.gain.setValueAtTime(0.1, audioCtx.currentTime); 
            gainNode.gain.exponentialRampToValueAtTime(0.01, audioCtx.currentTime + 0.1); 
            oscillator.connect(gainNode); 
            gainNode.connect(audioCtx.destination); 
            oscillator.start(); 
            oscillator.stop(audioCtx.currentTime + 0.2); 
        }

        function toggleAudio() { 
            audioEnabled = !audioEnabled; 
            localStorage.setItem('tg_bot_audio_enabled', audioEnabled); 
            audioBtn.innerHTML = audioEnabled ? svgOn : svgOff; 
            if(audioEnabled) { 
                if (audioCtx.state === 'suspended') audioCtx.resume(); 
                playAlarm(); 
            } 
        }

        function renderStatusTag(s) {
            document.getElementById('status-tag-area').innerHTML = `<div class="tag ${s ? 'on' : 'off'}">${s ? 'ON' : 'OFF'}</div>`;
        }

        function ctrl(s) {
            renderStatusTag(s);
            fetch('/api/ctrl?s=' + s + '&_t=' + new Date().getTime())
                .then(() => silentUpdate())
                .catch(() => silentUpdate());
        }

        function copyLink(link, btnElement) { 
            navigator.clipboard.writeText(link).then(() => { 
                const originalHTML = btnElement.innerHTML; 
                btnElement.innerHTML = `<svg class="icon" style="width:12px;height:12px;" viewBox="0 0 24 24"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>已复制链接`; 
                setTimeout(() => { btnElement.innerHTML = originalHTML; }, 1500); 
            }).catch(err => { console.error('Copy fail', err); }); 
        }

        // --- 新增：静默更新逻辑 ---
        async function silentUpdate() {
            try {
                const response = await fetch(window.location.href + '?_t=' + Date.now());
                const text = await response.text();
                const parser = new DOMParser();
                const doc = parser.parseFromString(text, 'text/html');
                
                document.getElementById('silent-task-container').innerHTML = doc.getElementById('silent-task-container').innerHTML;
                document.getElementById('status-tag-area').innerHTML = doc.getElementById('status-tag-area').innerHTML;
            } catch (e) {
                console.error("静默更新失败:", e);
            }
        }

        // 每 5 秒执行一次静默更新
        setInterval(silentUpdate, 5000);

        // 每 1 秒跑一次倒计时显示
        setInterval(() => { 
            const now = Date.now() / 1000; 
            let hasLate = false; 
            document.querySelectorAll('.countdown').forEach(el => {
                const diff = parseFloat(el.dataset.end) - now; 
                if(diff <= 0) { 
                    el.innerText = "已超时"; 
                    el.classList.add('late'); 
                    hasLate = true; 
                } else { 
                    const m = Math.floor(diff / 60); 
                    const s = Math.floor(diff % 60); 
                    el.innerText = `${m}:${s.toString().padStart(2, '0')}`; 
                } 
            }); 
            if (hasLate && audioEnabled) playAlarm(); 
        }, 1000);
    </script>
</body>
</html>
"""

# Log viewer lives in templates/log_viewer.html to keep the standalone UI page separate from backend code.
WAIT_CHECK_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>闭环检测工具</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #f8fafc; padding: 20px; max-width: 1000px; margin: 0 auto; color: #1e293b; }
        .layout-wrapper { display: flex; gap: 20px; align-items: flex-start; }
        .sidebar { flex: 0 0 240px; position: sticky; top: 20px; }
        .main-content { flex: 1; min-width: 0; }
        @media (max-width: 768px) {
            .layout-wrapper { flex-direction: column; }
            .sidebar { flex: none; width: 100%; position: relative; top: 0; }
            .kw-list { display: flex; flex-wrap: wrap; gap: 8px; }
            .kw-btn { flex: 1 1 auto; white-space: nowrap; justify-content: center; }
        }
        .kw-list { display: flex; flex-direction: column; gap: 8px; margin-top: 16px; }
        .kw-btn { padding: 10px 14px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; cursor: pointer; font-size: 14px; transition: all 0.2s; color: #334155; font-weight: 500; display: flex; align-items: center; gap: 8px; }
        .kw-btn:hover { background: #eff6ff; border-color: #bfdbfe; color: #1e3a8a; box-shadow: 0 1px 2px rgba(0,0,0,0.05); }
        .icon { width: 16px; height: 16px; vertical-align: text-bottom; stroke: currentColor; stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; display: inline-block; }
        .icon-sm { width: 14px; height: 14px; vertical-align: text-bottom; stroke: currentColor; stroke-width: 2; fill: none; stroke-linecap: round; stroke-linejoin: round; display: inline-block; }
        .card { background: white; padding: 24px; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.05); margin-bottom: 20px; border: 1px solid #e2e8f0; }
        h1 { margin-top: 0; color: #0f172a; font-size: 1.4rem; display: flex; align-items: center; gap: 8px; border-bottom: 1px solid #e2e8f0; padding-bottom: 16px; margin-bottom: 20px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; margin-bottom: 6px; font-weight: 600; font-size: 0.9rem; color: #475569; }
        input[type="text"] { width: 100%; padding: 12px 16px; border: 1px solid #cbd5e1; border-radius: 6px; box-sizing: border-box; font-size: 15px; outline: none; transition: border-color 0.2s; }
        input[type="text"]:focus { border-color: #3b82f6; box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.1); }
        button { background: #0f172a; color: white; border: none; padding: 12px 20px; border-radius: 6px; cursor: pointer; font-size: 15px; width: 100%; font-weight: 600; display: flex; align-items: center; justify-content: center; gap: 8px; transition: background 0.2s; }
        button:hover { background: #1e293b; } button:disabled { background: #94a3b8; cursor: not-allowed; }
        
        #progress-container { margin-top: 20px; display: none; background: #f1f5f9; padding: 16px; border-radius: 8px; border: 1px solid #e2e8f0; }
        #progress-bar { width: 100%; height: 6px; background: #cbd5e1; border-radius: 3px; overflow: hidden; margin-bottom: 10px; }
        #progress-fill { height: 100%; background: #3b82f6; width: 0%; transition: width 0.3s ease; }
        #status-text { font-size: 13px; color: #64748b; text-align: center; font-weight: 500; }

        .result-list { margin-top: 20px; }
        .result-item { padding: 16px; border-bottom: 1px solid #e2e8f0; display: flex; align-items: flex-start; gap: 16px; background: #fff; transition: background 0.2s; }
        .result-item:hover { background: #f8fafc; } .result-item:last-child { border-bottom: none; }
        
        .status-badge { padding: 6px 10px; border-radius: 6px; font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 4px; min-width: 85px; justify-content: center; }
        .status-closed { background: #ecfdf5; color: #059669; border: 1px solid #a7f3d0; }
        .status-open { background: #fef2f2; color: #dc2626; border: 1px solid #fecaca; }
        
        .msg-content { flex-grow: 1; min-width: 0; }
        .msg-meta { font-size: 12px; color: #64748b; margin-bottom: 6px; display: flex; gap: 12px; font-weight: 500; }
        .msg-text { font-size: 14px; line-height: 1.5; color: #334155; word-wrap: break-word; background: #f1f5f9; padding: 10px 12px; border-radius: 6px; margin: 6px 0; border-left: 3px solid #cbd5e1; }
        .latest-text { font-size: 12px; color: #b45309; margin-top: 8px; background: #fffbeb; padding: 6px 10px; border-radius: 4px; border: 1px dashed #fcd34d; display: inline-block; }
        .latest-text-success { font-size: 12px; color: #0f766e; margin-top: 8px; background: #f0fdfa; padding: 6px 10px; border-radius: 4px; border: 1px dashed #99f6e4; display: inline-block; }
        .reason-text { color: #dc2626; font-size: 13px; margin-top: 6px; font-weight: 500; }
        .reason-success { color: #059669; font-size: 13px; margin-top: 6px; font-weight: 500; }
        .msg-link { color: #2563eb; font-size: 12px; display: inline-flex; align-items: center; gap: 4px; margin-top: 8px; font-weight: 500; cursor: pointer; padding: 4px 8px; background: #eff6ff; border-radius: 4px; border: 1px solid #bfdbfe; transition: all 0.2s; }
        .msg-link:hover { background: #dbeafe; }
        
        .summary { font-weight: 600; margin-bottom: 24px; padding: 16px; background: #eff6ff; border-radius: 8px; border: 1px solid #bfdbfe; color: #1e3a8a; display: none; font-size: 0.95rem; }
        .filter-btn { cursor: pointer; color: #2563eb; margin: 0 6px; padding: 2px 6px; border-radius: 4px; transition: background 0.2s; display: inline-block; }
        .filter-btn:hover { background: #dbeafe; }
        .filter-active { background: #2563eb; color: #fff; pointer-events: none; }
    </style>
</head>
<body>
    <div class="layout-wrapper">
        <div class="sidebar">
            <div class="card" style="margin-bottom: 0; padding: 20px;">
                <h3 style="margin-top: 0; font-size: 1.1rem; color: #0f172a; border-bottom: 1px solid #e2e8f0; padding-bottom: 12px; margin-bottom: 0;">
                    <svg class="icon" style="color:#2563eb" viewBox="0 0 24 24"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg> 快捷选择
                </h3>
                <div class="kw-list">
                    <div class="kw-btn" onclick="fillKeyword('早班全体')">
                        <svg class="icon-sm" style="color:#0f766e" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 早班全体
                    </div>
                    <div class="kw-btn" onclick="fillKeyword('中班全体')">
                        <svg class="icon-sm" style="color:#0f766e" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 中班全体
                    </div>
                    <div class="kw-btn" onclick="fillKeyword('晚班全体')">
                        <svg class="icon-sm" style="color:#0f766e" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 晚班全体
                    </div>
                    {% for kw in wait_keywords %}
                    <div class="kw-btn" onclick="fillKeyword('{{ kw }}')">
                        <svg class="icon-sm" style="color:#64748b" viewBox="0 0 24 24"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg> {{ kw }}
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>

        <div class="main-content">
            <div class="card">
                <h1>
                    <svg class="icon" style="width:22px;height:22px;color:#0f172a" viewBox="0 0 24 24"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 9.36l-7.1 7.1a1 1 0 0 1-1.41 0l-1.42-1.42a1 1 0 0 1 0-1.4l7.1-7.1a6 6 0 0 1 9.36-7.94l-3.76 3.76z"/></svg> 
                    闭环情况检测
                </h1>
                <div class="form-group">
                    <label>扫描配置 (选择班次全体可按固定时间段排查)</label>
                    <input type="text" id="keyword" placeholder="点击左侧快捷键或输入关键词..." value="早班全体">
                </div>
                <button onclick="startCheck()" id="btn-search">
                    <svg class="icon" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg> 开始排查
                </button>
                
                <div id="progress-container">
                    <div id="progress-bar"><div id="progress-fill"></div></div>
                    <div id="status-text">系统准备就绪...</div>
                </div>
            </div>

            <div class="card" id="result-card" style="display:none; padding: 0;">
                <div class="summary" id="summary-box" style="margin: 20px 20px 0 20px;"></div>
                <div class="result-list" id="result-list"></div>
            </div>
        </div>
    </div>

    <script>
        let allResults = [];
        const iconCheck = `<svg class="icon-sm" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"/></svg>`;
        const iconCross = `<svg class="icon-sm" viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>`;
        const iconLink = `<svg class="icon-sm" viewBox="0 0 24 24"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>`;

        function fillKeyword(kw) {
            document.getElementById('keyword').value = kw;
        }

        async function startCheck() {
            const keyword = document.getElementById('keyword').value.trim();
            if (!keyword) return alert("配置内容不能为空");
            
            const btn = document.getElementById('btn-search');
            const pContainer = document.getElementById('progress-container');
            const pFill = document.getElementById('progress-fill');
            const pText = document.getElementById('status-text');
            const resCard = document.getElementById('result-card');
            const resList = document.getElementById('result-list');
            const summaryBox = document.getElementById('summary-box');

            btn.disabled = true;
            pContainer.style.display = 'block';
            resCard.style.display = 'block';
            resList.innerHTML = '';
            summaryBox.style.display = 'none';
            pFill.style.width = '1%';
            pText.innerText = "建立连接并初始化...";
            
            allResults = [];

            try {
                const response = await fetch(`/api/wait_check_stream?keyword=${encodeURIComponent(keyword)}`);
                if (!response.ok) {
                    const errorText = await response.text();
                    let message = errorText || `请求失败 (${response.status})`;
                    try {
                        const errorData = JSON.parse(errorText);
                        message = errorData.error || message;
                    } catch (e) {}
                    throw new Error(message);
                }
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { value, done } = await reader.read();
                    if (done) break;
                    
                    buffer += decoder.decode(value, {stream: true});
                    const lines = buffer.split('\\n');
                    buffer = lines.pop(); // 保留不完整的最后一行片段
                    
                    for (const line of lines) {
                        if (!line.trim()) continue; // 忽略为了防反向代理缓冲而补充的空白行
                        try {
                            const data = JSON.parse(line);
                            if (data.type === 'progress') {
                                pFill.style.width = data.percent + '%';
                                pText.innerText = data.msg;
                            } else if (data.type === 'result') {
                                allResults.push(data);
                                pText.innerText = `数据拉取中: 已发现 ${allResults.length} 条符合条件的记录...`;
                            } else if (data.type === 'done') {
                                pFill.style.width = '100%';
                                pText.innerText = '拉取完成，正在本地渲染视图...';
                                allResults.sort((a, b) => new Date(b.time) - new Date(a.time));
                                renderResults(allResults); 
                                renderSummary(data.total, data.closed, data.open);
                            }
                        } catch (e) {
                            console.error("Parse error", e, "Line content:", line);
                        }
                    }
                }
            } catch (e) {
                pText.innerText = "运行异常: " + e.message;
            } finally {
                btn.disabled = false;
                setTimeout(() => { if(pFill.style.width === '100%') pContainer.style.display = 'none'; }, 2000);
            }
        }

        function renderSummary(total, closed, open) {
            const summaryBox = document.getElementById('summary-box');
            summaryBox.style.display = 'block';
            summaryBox.innerHTML = `
                扫描报告: 合计 ${total} 记录
                <span style="margin:0 12px; color:#cbd5e1">|</span>
                <span class="filter-btn" onclick="filterResults('closed')">已闭环 (${closed})</span>
                <span class="filter-btn" onclick="filterResults('open')">需跟进 (${open})</span>
                <span class="filter-btn filter-active" onclick="filterResults('all')">全览</span>
            `;
        }

        function filterResults(type) {
            let filtered = [];
            if (type === 'all') filtered = allResults;
            else if (type === 'closed') filtered = allResults.filter(d => d.is_closed);
            else if (type === 'open') filtered = allResults.filter(d => !d.is_closed);
            
            filtered.sort((a, b) => new Date(b.time) - new Date(a.time));
            renderResults(filtered);
            
            document.querySelectorAll('.filter-btn').forEach(btn => {
                 if(btn.innerText.includes(type === 'all' ? '全览' : (type === 'closed' ? '已闭环' : '需跟进'))) {
                     btn.classList.add('filter-active');
                 } else {
                     btn.classList.remove('filter-active');
                 }
            });
        }
        
        function renderResults(list) {
            const resList = document.getElementById('result-list');
            resList.innerHTML = '';
            list.forEach(data => {
                const div = document.createElement('div');
                div.className = 'result-item';
                
                const isAllSearch = (data.latest_text === '无人引用回复' || data.latest_text === '相邻消息被回复');
                const mainDisplay = isAllSearch ? data.found_text : data.latest_text;
                
                let subDisplay = isAllSearch ? data.latest_text : data.found_text;
                if (isAllSearch) {
                    if (data.latest_text === '无人引用回复') {
                        subDisplay = data.is_closed ? '无直接引用，AI 判定豁免' : '无直接引用，标记遗漏';
                    } else if (data.latest_text === '相邻消息被回复') {
                        subDisplay = '连续发言，已被相邻上下文覆盖';
                    }
                }
                
                const subClass = data.is_closed ? 'latest-text-success' : 'latest-text';
                const subLabel = isAllSearch ? '特征' : '触发点';

                div.innerHTML = `
                    <div class="status-badge ${data.is_closed ? 'status-closed' : 'status-open'}">
                        ${data.is_closed ? iconCheck + ' 已闭环' : iconCross + ' 未闭环'}
                    </div>
                    <div class="msg-content">
                        <div class="msg-meta">
                            <span>记录时间: ${data.time}</span>
                            <span>归属群组: ${data.group_name}</span>
                        </div>
                        <div class="msg-text">${mainDisplay}</div>
                        ${data.reason ? `<div class="${data.is_closed ? 'reason-success' : 'reason-text'}">${data.reason}</div>` : ''}
                        <div style="margin-top: 4px;">
                            <div class="${subClass}">
                                <span style="opacity:0.7;margin-right:4px">${subLabel}</span> ${subDisplay}
                            </div>
                        </div>
                        <div>
                            <span class="msg-link copy-btn" onclick="copyLink('${data.link}', this)">
                                ${iconLink} 复制原文定位
                            </span>
                        </div>
                    </div>
                `;
                resList.appendChild(div);
            });
        }
        
        function copyLink(link, btnElement) {
            navigator.clipboard.writeText(link).then(() => {
                const originalHTML = btnElement.innerHTML;
                btnElement.innerHTML = `${iconCheck} 链接已复制`;
                setTimeout(() => { btnElement.innerHTML = originalHTML; }, 1500);
            }).catch(err => {
                console.error('Copy fail', err);
            });
        }
    </script>
</body>
</html>
"""

WAIT_ALERTS_HTML = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>稍等预警名单</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        *{box-sizing:border-box}
        body{margin:0;background:#f8fafc;color:#0f172a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
        main{max-width:760px;margin:0 auto;padding:18px 14px 36px}
        .top{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:14px}
        h1{font-size:18px;margin:0}
        .status{font-size:12px;color:#64748b;text-align:right}
        .panel{background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;margin-bottom:12px}
        .toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
        button{border:1px solid #cbd5e1;background:#fff;color:#334155;border-radius:6px;padding:8px 11px;font-size:13px;cursor:pointer;font-weight:600}
        button.primary{background:#be123c;border-color:#be123c;color:#fff}
        button:disabled{opacity:.55;cursor:not-allowed}
        .hint{font-size:12px;color:#64748b;line-height:1.6;margin:0}
        .grid{display:flex;flex-direction:column;gap:8px}
        .item{display:grid;grid-template-columns:minmax(180px,1fr) minmax(220px,1.2fr);gap:10px;align-items:center;border:1px solid #e2e8f0;border-radius:7px;padding:10px 11px;background:#f8fafc;min-height:42px}
        .item:hover{border-color:#fda4af;background:#fff1f2}
        .kw-line{display:flex;align-items:center;gap:9px;cursor:pointer;min-width:0}
        input[type="checkbox"]{width:16px;height:16px;accent-color:#be123c;flex:0 0 auto}
        .kw{font-size:14px;word-break:break-word}
        .targets{width:100%;border:1px solid #cbd5e1;border-radius:6px;padding:8px 10px;font-size:13px;background:#fff}
        @media(max-width:640px){.item{grid-template-columns:1fr}}
        .toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:#0f172a;color:#fff;padding:9px 14px;border-radius:999px;font-size:13px;opacity:0;transition:.2s;pointer-events:none}
        .toast.show{opacity:1}
        .empty{padding:28px;text-align:center;color:#64748b;border:1px dashed #cbd5e1;border-radius:8px;background:#f8fafc}
    </style>
</head>
<body>
<main>
    <div class="top">
        <h1>稍等预警名单</h1>
        <div class="status" id="store-status">加载中...</div>
    </div>

    <div class="panel">
        <p class="hint">这里控制哪些稍等关键词会启动超时预警，以及每个关键词推送给谁。接收人填 Telegram Chat ID，多个用逗号分隔；留空则发送到默认报警群。</p>
    </div>

    <div class="panel">
        <div class="toolbar">
            <button onclick="selectAll()">全选</button>
            <button onclick="selectNone()">全不选</button>
            <button class="primary" id="save-btn" onclick="saveConfig()">保存名单</button>
        </div>
        <div class="grid" id="kw-grid"></div>
    </div>
</main>
<div class="toast" id="toast"></div>
<script>
    let waitKeywords = [];
    let selectedKeywords = new Set();
    let alertRoutes = {};

    function showToast(text) {
        const toast = document.getElementById('toast');
        toast.textContent = text;
        toast.classList.add('show');
        setTimeout(() => toast.classList.remove('show'), 1800);
    }

    async function loadConfig() {
        const res = await fetch('/api/wait_alerts?_t=' + Date.now());
        const data = await res.json();
        waitKeywords = data.wait_keywords || [];
        selectedKeywords = new Set(data.alert_wait_keywords || []);
        alertRoutes = data.alert_routes || {};
        document.getElementById('store-status').textContent = data.store || '';
        render();
    }

    function render() {
        const grid = document.getElementById('kw-grid');
        if (!waitKeywords.length) {
            grid.innerHTML = '<div class="empty">WAIT_KEYWORDS 为空</div>';
            return;
        }
        grid.innerHTML = waitKeywords.map((kw) => `
            <div class="item">
                <label class="kw-line">
                    <input type="checkbox" data-kw="${escapeAttr(kw)}" ${selectedKeywords.has(kw) ? 'checked' : ''}>
                    <span class="kw">${escapeHtml(kw)}</span>
                </label>
                <input class="targets" data-kw="${escapeAttr(kw)}" value="${escapeAttr((alertRoutes[kw] || []).join(', '))}" placeholder="接收人 Chat ID，例如 123456789, -100123">
            </div>
        `).join('');
    }

    function escapeHtml(text) {
        return String(text).replace(/[&<>"']/g, ch => ({
            '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'
        }[ch]));
    }

    function escapeAttr(text) {
        return escapeHtml(text);
    }

    function selectAll() {
        selectedKeywords = new Set(waitKeywords);
        render();
    }

    function selectNone() {
        selectedKeywords = new Set();
        render();
    }

    async function saveConfig() {
        const btn = document.getElementById('save-btn');
        const checked = Array.from(document.querySelectorAll('input[type="checkbox"]:checked')).map(el => el.dataset.kw);
        const routes = {};
        document.querySelectorAll('.targets').forEach(el => {
            const value = el.value.trim();
            if (value) routes[el.dataset.kw] = value;
        });
        btn.disabled = true;
        try {
            const res = await fetch('/api/wait_alerts', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({alert_wait_keywords: checked, alert_routes: routes})
            });
            const data = await res.json();
            if (!data.ok) throw new Error(data.error || '保存失败');
            selectedKeywords = new Set(data.alert_wait_keywords || []);
            alertRoutes = data.alert_routes || {};
            document.getElementById('store-status').textContent = data.store || '';
            render();
            showToast(data.persisted ? '已保存到 Redis' : '已保存到当前进程内存');
        } catch (e) {
            showToast(e.message);
        } finally {
            btn.disabled = false;
        }
    }

    loadConfig().catch(e => {
        document.getElementById('store-status').textContent = '加载失败';
        showToast(e.message);
    });
</script>
</body>
</html>
"""

# ==========================================
# Web 路由区域
# ==========================================
@app.route('/')
def status_page():
    now = datetime.now(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
    return render_template_string(DASHBOARD_HTML, working=IS_WORKING, w=wait_timers, f=followup_timers, r=reply_timers, s=self_reply_timers, current_time=now)

@app.route('/copy/<token>')
def copy_page(token):
    payload = load_copy_page(token)
    if not payload:
        return Response("复制链接已过期或不存在。请重新发送原始内容给 bot 生成。", status=404, mimetype='text/plain')
    if payload.get("delete_chat_id") and payload.get("delete_message_ids"):
        Thread(target=delete_copy_page_messages, args=(payload,), daemon=True).start()
    return render_template_string(
        COPY_PAGE_HTML,
        title=payload.get("title") or "复制内容",
        sections=payload.get("sections") or [],
    )

@app.route('/log')
def log_ui():
    return render_template('log_viewer.html')

@app.route('/tool/wait_check')
def wait_check_ui(): 
    return render_template_string(WAIT_CHECK_HTML, wait_keywords=sorted(list(WAIT_SIGNATURES)))

@app.route('/tool/wait_alerts')
def wait_alerts_ui():
    return Response(WAIT_ALERTS_HTML, mimetype='text/html')

@app.route('/log_raw')
def log_raw():
    try:
        if not os.path.exists(LOG_FILE_PATH):
            return "Log file not created yet.", 200
        file_size = os.path.getsize(LOG_FILE_PATH)
        read_size = 200 * 1024 
        with open(LOG_FILE_PATH, 'rb') as f:
            if file_size > read_size: f.seek(file_size - read_size)
            content = f.read().decode('utf-8', errors='ignore')
        return Response(content, mimetype='text/plain')
    except Exception as e: return f"Log read error: {e}"

@app.route('/log_db')
def log_db():
    chat_id = request.args.get('chat_id', type=int)
    mode = (request.args.get('mode') or 'all').lower()
    if mode not in {"all", "audit", "edit", "delete", "context"}:
        mode = "all"
    limit = min(request.args.get('limit', 600, type=int), 2000)
    try:
        data = _chat_event_rows(chat_id=chat_id, limit=limit, mode=mode)
        if not data and mode == "all":
            data = _legacy_chat_log_rows(chat_id=chat_id, limit=limit)
        return jsonify(data)
    except Exception as e:
        return jsonify([])

@app.route('/log_stream')
def log_stream():
    chat_id = request.args.get('chat_id', type=int)
    mode = (request.args.get('mode') or 'all').lower()

    def generate():
        q = queue.Queue(maxsize=200)
        with _sse_clients_lock:
            _sse_clients.append(q)
        try:
            yield ": connected\n\n"
            while True:
                try:
                    payload = q.get(timeout=25)
                    row = json.loads(payload)
                    if chat_id and row.get("chat_id") != chat_id:
                        continue
                    if mode == "audit" and row.get("source") != "audit":
                        continue
                    if mode == "edit" and row.get("event_type") != "edit":
                        continue
                    if mode == "delete" and row.get("event_type") != "delete":
                        continue
                    if mode == "context" and row.get("source") == "audit":
                        continue
                    yield f"data: {payload}\n\n"
                except queue.Empty:
                    yield ": ping\n\n"
        finally:
            with _sse_clients_lock:
                try:
                    _sse_clients.remove(q)
                except ValueError:
                    pass

    return Response(stream_with_context(generate()), mimetype='text/event-stream')

@app.route('/log_flow')
def log_flow():
    chat_id = request.args.get('chat_id', type=int)
    message_id = request.args.get('message_id', type=int)
    window = min(max(request.args.get('window', 0, type=int), 0), 3600)
    limit = min(max(request.args.get('limit', 300, type=int), 1), 800)
    try:
        return jsonify(_flow_rows(chat_id, message_id, window_seconds=window, limit=limit))
    except Exception as e:
        logger.error(f"❌ log_flow 查询失败: {e}")
        return jsonify([])

@app.route('/log_groups')
def log_groups():
    try:
        with sqlite3.connect(CHAT_LOG_DB) as conn:
            rows = conn.execute(
                """SELECT chat_id, MAX(last_ts) AS last_ts FROM (
                    SELECT chat_id, MAX(first_ts) AS last_ts FROM chat_message_snapshots WHERE chat_id IS NOT NULL GROUP BY chat_id
                    UNION ALL
                    SELECT chat_id, MAX(ts) AS last_ts FROM chat_events WHERE chat_id IS NOT NULL AND event_type IN ('edit', 'delete') GROUP BY chat_id
                ) GROUP BY chat_id ORDER BY last_ts DESC"""
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    "SELECT chat_id, MAX(ts) as last_ts FROM chat_logs WHERE chat_id IS NOT NULL GROUP BY chat_id ORDER BY last_ts DESC"
                ).fetchall()
        # Try to get group names from Telegram client
        result = []
        for chat_id, _ in rows:
            name = _group_name_cache.get(chat_id, str(chat_id))
            result.append({"chat_id": chat_id, "name": name})
        return jsonify(result)
    except Exception as e:
        return jsonify([])

@app.after_request
def add_header(response):
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/api/ctrl')
def api_ctrl():
    s = request.args.get('s', type=int)
    log_tree(1, f"🌐 Web指令接收: {'上班' if s==1 else '下班'}")
    global bot_loop
    if not bot_loop: return "Error: Loop Not Ready", 500
    coro = perform_start_work() if s == 1 else perform_stop_work()
    try: asyncio.run_coroutine_threadsafe(coro, bot_loop)
    except Exception as e: return str(e), 500
    return "OK"

# 【新增】供独立网页读取关键词配置的接口
@app.route('/api/config')
def api_config():
    import json
    data = json.dumps({
        "wait_keywords": sorted(list(WAIT_SIGNATURES)),
        "alert_wait_keywords": sorted(list(get_wait_alert_signatures())),
        "alert_routes": get_wait_alert_routes(),
        "wait_alert_store": wait_alert_store_status,
    })
    response = Response(data, mimetype='application/json')
    response.headers['Access-Control-Allow-Origin'] = '*' # 允许跨域
    return response

@app.route('/api/wait_alerts', methods=['GET', 'POST'])
def api_wait_alerts():
    global WAIT_ALERT_SIGNATURES, WAIT_ALERT_ROUTES

    if request.method == 'GET':
        return jsonify({
            "ok": True,
            "wait_keywords": sorted(list(WAIT_SIGNATURES)),
            "alert_wait_keywords": sorted(list(get_wait_alert_signatures())),
            "alert_routes": get_wait_alert_routes(),
            "store": wait_alert_store_status,
        })

    data = request.get_json(silent=True) or {}
    selected = extract_signature_set(data.get("alert_wait_keywords", []))
    unknown = selected - WAIT_SIGNATURES
    selected = selected & WAIT_SIGNATURES
    routes = normalize_alert_routes(data.get("alert_routes", {}), WAIT_SIGNATURES)

    with wait_alert_config_lock:
        WAIT_ALERT_SIGNATURES = set(selected)
        WAIT_ALERT_ROUTES = dict(routes)

    persisted, store_status = persist_wait_alert_config(selected, routes)
    cancel_disabled_wait_tasks(selected)

    return jsonify({
        "ok": True,
        "persisted": persisted,
        "store": store_status,
        "wait_keywords": sorted(list(WAIT_SIGNATURES)),
        "alert_wait_keywords": sorted(list(selected)),
        "alert_routes": routes,
        "ignored_keywords": sorted(list(unknown)),
    })

@app.route('/api/wait_check_stream')
def wait_check_stream():
    keyword = request.args.get('keyword', '').strip()
    if not keyword: return "Keyword required", 400
    if not bot_loop:
        return jsonify({"ok": False, "error": "Bot loop not ready"}), 503
    def generate():
        result_queue = queue.Queue()
        asyncio.run_coroutine_threadsafe(check_wait_keyword_logic(keyword, result_queue), bot_loop)
        
        yield (" " * 4096) + "\n"
        
        while True:
            data = result_queue.get()
            if data is None: break
            yield data + "\n" + (" " * 4096) + "\n"
            
    response = Response(stream_with_context(generate()), mimetype='text/plain')
    response.headers['X-Accel-Buffering'] = 'no'  
    response.headers['Access-Control-Allow-Origin'] = '*' # 【新增】允许跨域调用
    return response

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port, threaded=True)


# ==========================================
# 模块 4.5: AI 分析模块 (Ver 45.22)
# ==========================================

GEMINI_API_ROOT = "https://generativelanguage.googleapis.com/v1beta"

def _gemini_generate_json(prompt, timeout=60):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY 未配置")

    url = f"{GEMINI_API_ROOT}/models/{GEMINI_MODEL}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "response_mime_type": "application/json"
        }
    }
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text[:200]}")

    res_json = resp.json()
    raw_content = (((res_json.get('candidates') or [{}])[0].get('content') or {}).get('parts') or [{}])[0].get('text', '')
    if not raw_content:
        raise RuntimeError("Gemini返回为空")
    return json.loads(raw_content)

def _ai_check_reply_needed(text):
    # 彻底移除本地暴力兜底，完全依靠 AI 研判
    prompt = f"判断客户消息是否需要回复。消息: '{text}'\n如果是礼貌结束语(如：好、好的、谢谢、收到、ok等)或无意义，返回false。如果是问题或业务请求，返回true。\nJSON: {{'reason': '...', 'need_reply': true/false}}"
    try:
        decision = _gemini_generate_json(prompt, timeout=60)
        return (decision.get("need_reply", True), decision.get("reason", "AI Decision"))
    except: pass
    return (True, "⚠️ AI出错，请人工核查")

def _ai_check_orphan_context(target_text, context_text_list, target_label="User"):
    """
    [Sync Function] [Ver 45.20/22]
    让 AI 自由思考上下文，移除死板规则。
    """
    if not target_text or len(target_text) < 1: return (True, "忽略空消息") 
    
    context_str = "\n".join(context_text_list)
    log_prefix = f"🤖 [AI-Orphan] Text='{target_text[:15]}...' | "

    # 修复了逻辑反转导致的判断BUG，变量名修改为 is_exempt (是否豁免)
    prompt = f"""
    你是一名经验丰富的客服质检员。
    请根据上下文判断下面的【目标消息】是否需要客服继续回复（即是否属于”客服漏回”的事故）。

    目标发送者: “{target_label}”
    目标消息: “{target_text}”

    最近聊天记录 (包含时间、发送者、内容):
    {context_str}

    【判断范围约束（最重要）】：
    你的任务仅仅是评估标有 “<<< TARGET” 的那条消息本身、在其发出时间点之后是否在合理时间内获得了客服处理。
    上下文中如果在目标消息之后还出现了其他用户消息（尤其是更晚时间的、不同话题的新投诉），请完全忽略这些消息是否被处理——那些消息是独立的扫描条目，不属于你当前的判断范围。
    判断结果只与”目标消息”有关，与其他消息无关。

    【分析逻辑】:
    请像人类一样综合思考。仔细观察上下文的时间流和对话流。
    - 豁免无需回复 (is_exempt=true): 如果这条消息看起来是用户连续发言中的一句（分段发送）、对上一句的补充、无意义的语气词（如：好、好的、收到、谢谢等），或者客服在上下文中已经明显针对该【同一事件/话题】接待了该用户，请认为无需单独回复。
    【特别注意】：聊天记录中如果带有”[使用了引用回复]”标签，代表客服精确绑定回复了某个客户。如果目标消息是单纯的催促（如单独的一个”？”、”在吗”、”处理好了吗”等），请在上下文中寻找客服的实质性回复。注意：如果客服只回复了”稍等”、”核实中”、”处理中”等安抚话语，【不等于】问题已解决，请判定为漏回 (is_exempt=false)！只有当上下文中显示客服已经带有”[使用了引用回复]”标签，并且给出了明确的【最终处理结果】（哪怕客服引用的是用户前面的订单消息，而不是引用的这句催促），才能判定为已处理，予以豁免 (is_exempt=true)！
    - 属于漏回需回复 (is_exempt=false): 只有当这是一条被完全忽视的、独立的业务请求时，才标记为漏回。特别注意：如果客户在短时间内连续发送了两个完全不同的问题（例如一个问充值，一个问其它业务），而客服只回答了其中一个，那么未被回答的那个独立问题应判定为漏回 (is_exempt=false)！

    请输出 JSON 格式: {{“reason”: “用中文简短说明原因...”, “is_exempt”: true/false}}
    """

    try:
        decision = _gemini_generate_json(prompt, timeout=60)
        is_exempt = decision.get("is_exempt", False)
        reason = decision.get("reason", "AI Decision")
        log_tree(2, log_prefix + f"✅ AI判定: 豁免={is_exempt} | {reason}")
        return (is_exempt, reason)
    except Exception as e:
        log_tree(9, log_prefix + f"❌ AI Check Failed: {e}，标记人工核查")
        return (False, f"⚠️ AI出错，请人工核查")

def _ai_check_reply_continuation(anchor_text, followup_texts):
    if not followup_texts:
        return True, "无补充消息"

    followup_str = "\n".join([f"{idx + 1}. {text}" for idx, text in enumerate(followup_texts)])
    prompt = f"""
    你是一名客服聊天质检员。
    客户先发送了一条【带引用】的消息，随后又连续发送若干条【未引用】消息。
    请判断这些未引用消息是否只是上一条带引用消息的补充说明，还是包含新的独立业务问题。

    带引用消息: "{anchor_text}"
    后续未引用消息:
    {followup_str}

    判断标准:
    - 如果后续消息是在补充、纠正、抱怨、催促或说明同一件事，返回 is_continuation=true。
    - 如果后续消息提出了新的订单、充值、提现、钱包、金额、地址、冻结等独立业务问题，返回 is_continuation=false。

    请输出 JSON 格式: {{"reason": "用中文简短说明原因...", "is_continuation": true/false}}
    """

    try:
        decision = _gemini_generate_json(prompt, timeout=30)
        return decision.get("is_continuation", False), decision.get("reason", "AI Decision")
    except Exception as e:
        log_tree(9, f"❌ AI补充判定失败: {e}，按新问题处理")
    return False, "AI补充判定失败，按新问题处理"

async def _check_is_closed_logic(latest_msg):
    is_closed = False
    reason = ""
    last_sender_id = latest_msg.sender_id
    last_sender_is_cs = False
    if last_sender_id in ([MY_ID] + OTHER_CS_IDS): last_sender_is_cs = True
    else:
         try:
             s = await latest_msg.get_sender()
             if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): last_sender_is_cs = True
         except: pass
    
    if not last_sender_is_cs:
        if not latest_msg.text or not latest_msg.text.strip(): is_closed = False; reason = "最后消息非文本实体"
        else:
            need_reply, ai_reason = await asyncio.get_event_loop().run_in_executor(None, lambda: _ai_check_reply_needed(latest_msg.text))
            if not need_reply: is_closed = True; reason = f"系统识别已闭环：{ai_reason}"
            else: is_closed = False; reason = f"待处理：{ai_reason}"
    else:
        last_text = latest_msg.text or ""
        is_wait = any(k in last_text for k in WAIT_SIGNATURES)
        is_keep = last_text.strip() in KEEP_SIGNATURES
        if is_wait or is_keep:
            is_closed = False; reason = f"流程挂起中: 包含{'稍等' if is_wait else '跟进'}指令"
            if latest_msg.reply_to:
                try:
                    replied_obj = await latest_msg.get_reply_message()
                    if not replied_obj: is_closed = True; reason = "原消息已撤回 (自动豁免)"
                except: pass
        else: is_closed = True
    return is_closed, reason

async def check_wait_keyword_logic(keyword, result_queue):
    try:
        cutoff_hours = 10
        limit_count = 3000
        shift_window = get_wait_check_shift_window(keyword)
        if is_all_wait_check_keyword(keyword):
            cutoff_hours = 20
            limit_count = 6000 
            
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=cutoff_hours)
        scan_start_time = cutoff_time
        scan_end_time = datetime.now(timezone.utc)
        if shift_window:
            shift_start, shift_end, shift_start_text, shift_end_text = shift_window
            scan_start_time = shift_start.astimezone(timezone.utc)
            scan_end_time = shift_end.astimezone(timezone.utc)
            result_queue.put(json.dumps({
                "type": "progress",
                "percent": 1,
                "msg": f"{keyword} 扫描窗口: 北京时间 {shift_start.strftime('%Y-%m-%d %H:%M')} - {shift_end.strftime('%Y-%m-%d %H:%M')} ({shift_start_text}-{shift_end_text})"
            }))

        total_groups = len(CS_GROUP_IDS)
        EXCLUDED_GROUPS = [-1002807120955, -1002169616907]
        
        found_count = 0
        closed_count = 0

        for idx, chat_id in enumerate(CS_GROUP_IDS):
            if chat_id in EXCLUDED_GROUPS: continue
            
            percent = int((idx / total_groups) * 100)
            result_queue.put(json.dumps({"type": "progress", "percent": percent, "msg": f"正在同步通信群组 {chat_id} ({idx+1}/{total_groups})..."}))

            try:
                history = []
                async for m in client.iter_messages(chat_id, limit=limit_count):
                    if m.date and m.date > scan_end_time:
                        continue
                    if m.date and m.date < scan_start_time: break
                    if getattr(m, 'action', None): continue # 过滤拉人、置顶等系统服务消息
                    history.append(m)
                
                if is_all_wait_check_keyword(keyword):
                    msg_grouped_map = {}
                    user_msg_map = defaultdict(list)
                    for m in history:
                        if m.grouped_id: msg_grouped_map[m.id] = m.grouped_id
                        if m.sender_id: user_msg_map[m.sender_id].append(m)

                    replied_to_ids = set()
                    for m in history:
                        if m.reply_to and m.reply_to.reply_to_msg_id:
                            replied_to_ids.add(m.reply_to.reply_to_msg_id)
                    
                    replied_grouped_ids = set()
                    for mid in replied_to_ids:
                        if mid in msg_grouped_map:
                            replied_grouped_ids.add(msg_grouped_map[mid])

                    orphan_tasks = []
                    reply_continuation_ai_cache = {}
                    for i, m in enumerate(history):
                        is_cs = False
                        if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs = True
                        else:
                            try:
                                s = m.sender 
                                if s and getattr(s, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): is_cs = True
                            except: pass
                        if is_cs: continue

                        if m.sticker or m.gif:
                            continue

                        if m.reply_to and m.reply_to.reply_to_msg_id: continue

                        code_exempt_reason = get_mention_exempt_reason(m.text or "") or get_obvious_noise_reason(m.text or "")

                        previous_customer_reply = None
                        followup_texts = []
                        scan_idx = i + 1
                        while scan_idx < len(history) and len(followup_texts) < WAIT_CHECK_REPLY_CONTINUATION_MAX_MESSAGES:
                            previous_msg = history[scan_idx]
                            previous_is_cs = False
                            if previous_msg.sender_id in ([MY_ID] + OTHER_CS_IDS):
                                previous_is_cs = True
                            else:
                                try:
                                    previous_sender = previous_msg.sender
                                    if previous_sender and getattr(previous_sender, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)):
                                        previous_is_cs = True
                                except:
                                    pass
                            if previous_is_cs:
                                break
                            if previous_msg.sender_id != m.sender_id:
                                break
                            if previous_msg.reply_to and previous_msg.reply_to.reply_to_msg_id:
                                previous_customer_reply = previous_msg
                                break
                            if previous_msg.text:
                                followup_texts.insert(0, previous_msg.text)
                            scan_idx += 1

                        if (
                            previous_customer_reply
                            and previous_customer_reply.date
                            and m.date
                            and 0 <= (m.date - previous_customer_reply.date).total_seconds() <= WAIT_CHECK_REPLY_CONTINUATION_SECONDS
                        ):
                            grouped_followups = followup_texts + [m.text or "[媒体/图片]"]
                            if all(is_obvious_reply_continuation(text) for text in grouped_followups):
                                continue
                            if not any(is_obvious_new_question(text) for text in grouped_followups):
                                cache_key = (chat_id, previous_customer_reply.id, tuple(grouped_followups))
                                if cache_key not in reply_continuation_ai_cache:
                                    is_continuation, continuation_reason = await asyncio.get_event_loop().run_in_executor(
                                        None,
                                        lambda anchor=previous_customer_reply.text or "[媒体/图片]", texts=list(grouped_followups): _ai_check_reply_continuation(anchor, texts)
                                    )
                                    reply_continuation_ai_cache[cache_key] = (is_continuation, continuation_reason)
                                else:
                                    is_continuation, continuation_reason = reply_continuation_ai_cache[cache_key]
                                if is_continuation:
                                    log_tree(2, f"🧩 引用后补充豁免 Msg={m.id}: {continuation_reason}")
                                    continue

                        is_orphan = True
                        if m.id in replied_to_ids: is_orphan = False
                        elif m.grouped_id and m.grouped_id in replied_grouped_ids: is_orphan = False
                            
                        if is_orphan:
                            orphan_tasks.append((i, m, code_exempt_reason))
                    
                    # 核心修复 3: 如果发现孤立消息，提前发出一个进度提示，避免 AI 耗时导致界面长时间假死
                    if orphan_tasks:
                        result_queue.put(json.dumps({"type": "progress", "percent": percent, "msg": f"群组 {chat_id} 发现 {len(orphan_tasks)} 条潜在漏回消息，正在进行规则豁免和 AI 研判..."}))

                    for orphan_idx, (i, m, preclosed_reason) in enumerate(orphan_tasks):
                        # 核心修复 4: 每完成 5 条 AI 判定推送一次进度
                        if orphan_idx > 0 and orphan_idx % 5 == 0:
                            result_queue.put(json.dumps({"type": "progress", "percent": percent, "msg": f"群组 {chat_id} AI 深度研判中 (进度: {orphan_idx}/{len(orphan_tasks)})..."}))

                        start = max(0, i - 30) 
                        end = min(len(history), i + 15)
                        context_slice = history[start:end]
                        context_slice.sort(key=lambda x: x.date)
                        
                        target_uid = m.sender_id
                        target_label = f"User({str(target_uid)[-4:]})" 

                        context_txts = []
                        for cm in context_slice:
                            if getattr(cm, 'action', None): continue 
                            
                            if cm.sender_id in ([MY_ID] + OTHER_CS_IDS): c_label = "CS"
                            else:
                                is_cm_cs = False
                                try:
                                    if getattr(cm.sender, 'first_name', '').startswith(tuple(CS_NAME_PREFIXES)): is_cm_cs = True
                                except: pass
                                if is_cm_cs: c_label = "CS"
                                else: c_label = f"User({str(cm.sender_id)[-4:]})"

                            # 👇 将表情包明确标注，防止 AI 误以为是客户发的报错截图
                            if cm.sticker:
                                c_txt = "[贴纸/表情包]"
                            elif cm.gif:
                                c_txt = "[GIF动图]"
                            else:
                                c_txt = (cm.text or "[媒体/图片]").replace('\n', ' ')
                                
                            marker = " <<< TARGET" if cm.id == m.id else ""
                            
                            # 👇 新增：判断是否使用了引用回复功能
                            reply_tag = ""
                            if cm.reply_to and cm.reply_to.reply_to_msg_id:
                                reply_tag = " [使用了引用回复]"
                                
                            beijing_time_str = cm.date.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')
                            context_txts.append(f"[{beijing_time_str}] {c_label}{reply_tag}: {c_txt}{marker}")
                        
                        found_count += 1

                        if preclosed_reason:
                            is_result_closed = True
                            closed_count += 1
                            display_reason = f"代码判定(豁免): {preclosed_reason}，无需客服回复。"
                        else:
                            # 返回的变成了 is_exempt(是否豁免), 不再是倒错逻辑的 is_slip_up
                            is_exempt, ai_reason = await asyncio.get_event_loop().run_in_executor(
                                None, lambda: _ai_check_orphan_context(m.text or "[Media]", context_txts, target_label)
                            )
                            
                            # 核心修复：强制透传展示 AI 判定结果，不论真假
                            if is_exempt:
                                is_result_closed = True
                                closed_count += 1
                                display_reason = f"AI判定(豁免): {ai_reason}"
                            else:
                                is_result_closed = False
                                display_reason = f"AI判定(漏回): {ai_reason}"
                        
                        group_name = str(chat_id)
                        try: g = await client.get_entity(chat_id); group_name = g.title
                        except: pass

                        safe_text = (m.text or "[媒体/空]")[:100].replace('\n', ' ')
                        beijing_time = m.date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                        real_chat_id = str(chat_id).replace('-100', '')
                        link = f"https://t.me/c/{real_chat_id}/{m.id}"
                        
                        result_queue.put(json.dumps({
                            "type": "result",
                            "is_closed": is_result_closed,
                            "reason": display_reason,
                            "time": beijing_time,
                            "group_name": group_name,
                            "found_text": safe_text,
                            "latest_text": "无人引用回复",
                            "link": link
                        }))
                            
                    continue 

                thread_latest_msg = {}
                for m in history:
                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id 
                        if not t_id: t_id = m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    if t_id not in thread_latest_msg:
                        thread_latest_msg[t_id] = m

                for m in history:
                    if not m.text: continue
                    if keyword in m.text: 
                        found_count += 1
                        t_id = None
                        if m.reply_to:
                            t_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                        if not t_id: t_id = m.id
                        
                        latest_msg = thread_latest_msg.get(t_id, m)
                        is_closed, reason = await _check_is_closed_logic(latest_msg)
                        if is_closed: closed_count += 1

                        group_name = str(chat_id)
                        try: g = await client.get_entity(chat_id); group_name = g.title
                        except: pass
                        
                        safe_text = (m.text or "")[:100].replace('\n', ' ')
                        beijing_time = m.date.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')
                        
                        link = ""
                        real_chat_id = str(chat_id).replace('-100', '')
                        url_thread_id = None
                        target_msg_for_link = latest_msg if not is_closed else m
                        
                        if "(客户删消息)" not in reason:
                            if target_msg_for_link.reply_to:
                                url_thread_id = target_msg_for_link.reply_to.reply_to_top_id or target_msg_for_link.reply_to.reply_to_msg_id
                        
                        if url_thread_id: link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}?thread={url_thread_id}"
                        else: link = f"https://t.me/c/{real_chat_id}/{target_msg_for_link.id}"
                        
                        latest_content = (latest_msg.text or "[媒体]")[:60].replace('\n', ' ')

                        result_queue.put(json.dumps({
                            "type": "result",
                            "is_closed": is_closed,
                            "reason": reason,
                            "time": beijing_time,
                            "group_name": group_name,
                            "found_text": safe_text,
                            "latest_text": latest_content, 
                            "link": link
                        }))

            except Exception as e:
                logger.error(f"Group {chat_id} check failed: {e}")

        result_queue.put(json.dumps({
            "type": "done", 
            "total": found_count, 
            "closed": closed_count, 
            "open": found_count - closed_count
        }))
        result_queue.put(None) 

    except Exception as e:
        logger.error(f"Check Task Logic Error: {e}")
        result_queue.put(None)

# ==========================================
# 模块 5: 通知与网络
# ==========================================
def _post_request(url, payload):
    try:
        resp = requests.post(url, json=payload, timeout=60)
        if resp.status_code != 200: log_tree(9, f"API推送失败: {resp.status_code}")
    except Exception as e: log_tree(9, f"网络异常: {e}")

def md_escape(value):
    text = str(value or "")
    return text.replace("\\", "\\\\").replace("`", "'").replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")

def md_inline(value, max_len=120):
    text = str(value or "").replace("\n", " ").strip()
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return f"`{md_escape(text)}`"

def normalize_copy_links(raw_link):
    if not raw_link:
        return []
    if isinstance(raw_link, str):
        return [("点击复制消息链接", raw_link)] if raw_link.strip() else []

    items = []
    if isinstance(raw_link, dict):
        raw_link = raw_link.items()
    elif not isinstance(raw_link, (list, tuple, set)):
        return []

    for idx, item in enumerate(raw_link, start=1):
        label = f"复制第{idx}条消息链接"
        link = None
        if isinstance(item, dict):
            label = str(item.get("label") or label)
            link = item.get("link") or item.get("url") or item.get("text")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            label = str(item[0] or label)
            link = item[1]
        else:
            link = item

        link = str(link or "").strip()
        if link:
            items.append((label[:64], link))
    return items

def build_copy_link_markup(raw_link):
    rows = []
    for label, link in normalize_copy_links(raw_link)[:8]:
        if len(link) > 256:
            log_tree(9, f"复制按钮链接超过 Telegram 256 字符限制，已跳过: {link[:80]}...")
            continue
        rows.append([{"text": label, "copy_text": {"text": link}}])
    return {"inline_keyboard": rows} if rows else None

def build_copy_text_markup(text, label="复制整理结果"):
    raw = str(text or "")
    if not raw:
        return None
    rows = []
    if len(raw) <= 256:
        rows.append([{"text": label, "copy_text": {"text": raw}}])
    else:
        blocks = [item.strip() for item in re.split(r"\n\s*\n+", raw) if item.strip()]
        if len(blocks) > 1:
            for index, block in enumerate(blocks[:8], start=1):
                if len(block) <= 256:
                    rows.append([{"text": f"复制第{index}段", "copy_text": {"text": block}}])
        if not rows:
            for index, line in enumerate([item for item in raw.splitlines() if item.strip()][:8], start=1):
                if len(line) <= 256:
                    rows.append([{"text": f"复制第{index}行", "copy_text": {"text": line}}])
    return {"inline_keyboard": rows} if rows else None

def build_single_copy_text_markup(text, label="复制整理结果"):
    raw = str(text or "")
    if not raw:
        return None
    return {"inline_keyboard": [[{"text": label, "copy_text": {"text": raw}}]]}

def build_conversion_reply_markup(text, copy_label="复制整理结果", copy_page_url=None):
    rows = []
    if copy_page_url:
        rows.append([{"text": "网页复制", "url": copy_page_url}])
    return {"inline_keyboard": rows} if rows else None

def format_copy_link(link, index=None):
    if not link:
        return "🔗 消息链接：无"
    if index:
        return f"🔗 消息链接：点击下方「复制第{index}条消息链接」按钮"
    return "🔗 消息链接：点击下方按钮复制"

def format_alert_message(title, rows, content_label=None, content=None, link=None):
    lines = [f"{title}"]
    for label, value in rows:
        if value is None or value == "":
            continue
        lines.append(f"{label}：{md_escape(value)}")
    if content_label is not None:
        lines.append(f"{content_label}：{md_inline(content)}")
    lines.append(format_copy_link(link))
    return "\n".join(lines)

def get_bot_menu_url():
    raw_url = get_public_base_url()

    menu_path = os.environ.get("BOT_MENU_PATH", "/").strip() or "/"
    if not menu_path.startswith("/"):
        menu_path = "/" + menu_path
    return raw_url.rstrip("/") + menu_path

def setup_bot_menu_button():
    if not BOT_TOKEN:
        return

    menu_url = get_bot_menu_url()
    if not menu_url:
        log_tree(1, "Bot 菜单未配置：设置 BOT_MENU_URL=https://你的Zeabur域名 后会自动启用")
        return

    menu_text = (os.environ.get("BOT_MENU_TEXT", "监控面板").strip() or "监控面板")[:20]
    api_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton"
    payload = {
        "menu_button": {
            "type": "web_app",
            "text": menu_text,
            "web_app": {"url": menu_url}
        }
    }

    try:
        resp = requests.post(api_url, json=payload, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("ok"):
            log_tree(1, f"Bot 菜单按钮已设置: {menu_text} -> {menu_url}")
        else:
            log_tree(9, f"Bot 菜单按钮设置失败: HTTP {resp.status_code} {str(data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 菜单按钮设置异常: {e}")

def setup_bot_commands():
    if not BOT_TOKEN:
        return

    get_url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMyCommands"
    set_url = f"https://api.telegram.org/bot{BOT_TOKEN}/setMyCommands"

    try:
        resp = requests.get(get_url, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        commands = data.get("result", []) if resp.status_code == 200 and data.get("ok") else []
        merged = [cmd for cmd in commands if cmd.get("command") not in ("start", "id")]
        merged.append({"command": "start", "description": "查看使用引导"})
        merged.append({"command": "id", "description": "查看当前聊天和用户ID"})

        set_resp = requests.post(set_url, json={"commands": merged}, timeout=20)
        set_data = set_resp.json() if set_resp.headers.get("content-type", "").startswith("application/json") else {}
        if set_resp.status_code == 200 and set_data.get("ok"):
            log_tree(1, "Bot 命令已设置: /start /id")
        else:
            log_tree(9, f"Bot 命令设置失败: HTTP {set_resp.status_code} {str(set_data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 命令设置异常: {e}")

def _bot_user_display(user):
    if not isinstance(user, dict):
        return "未知"
    parts = [user.get("first_name"), user.get("last_name")]
    name = " ".join([str(x).strip() for x in parts if x]).strip()
    username = user.get("username")
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    return name or "未知"

def _bot_forward_identity(message):
    if not isinstance(message, dict):
        return None, None

    if isinstance(message.get("forward_from"), dict):
        user = message["forward_from"]
        return user.get("id"), _bot_user_display(user)

    origin = message.get("forward_origin")
    if isinstance(origin, dict):
        if isinstance(origin.get("sender_user"), dict):
            user = origin["sender_user"]
            return user.get("id"), _bot_user_display(user)
        if isinstance(origin.get("chat"), dict):
            chat = origin["chat"]
            return chat.get("id"), chat.get("title") or chat.get("username") or "转发来源聊天"
        if origin.get("sender_user_name"):
            return None, f"{origin.get('sender_user_name')}（已隐藏ID）"

    return None, None

def format_id_command_reply(message):
    chat = message.get("chat", {}) if isinstance(message, dict) else {}
    sender = message.get("from", {}) if isinstance(message, dict) else {}
    chat_id = chat.get("id", "未知")
    chat_type = chat.get("type", "未知")
    chat_title = chat.get("title") or chat.get("username") or "私聊"
    sender_id = sender.get("id", "未知")

    lines = [
        "ID 信息",
        f"当前聊天ID：{chat_id}",
        f"聊天类型：{chat_type}",
        f"聊天名称：{chat_title}",
        f"发命令的人ID：{sender_id}",
        f"发命令的人：{_bot_user_display(sender)}",
    ]

    reply_msg = message.get("reply_to_message") if isinstance(message, dict) else None
    if isinstance(reply_msg, dict):
        reply_sender = reply_msg.get("from")
        if isinstance(reply_sender, dict):
            lines.append(f"被回复的人ID：{reply_sender.get('id', '未知')}")
            lines.append(f"被回复的人：{_bot_user_display(reply_sender)}")

        sender_chat = reply_msg.get("sender_chat")
        if isinstance(sender_chat, dict):
            lines.append(f"被回复的频道/群ID：{sender_chat.get('id', '未知')}")
            lines.append(f"被回复的频道/群：{sender_chat.get('title') or sender_chat.get('username') or '未知'}")

        forward_id, forward_name = _bot_forward_identity(reply_msg)
        if forward_name:
            lines.append(f"被回复转发来源：{forward_name}")
            lines.append(f"被回复转发来源ID：{forward_id if forward_id else '隐藏，Bot拿不到'}")

    if chat_type == "private":
        lines.append("获取别人ID：让对方私聊这个Bot发送 /id，或在共同群里回复他的消息发送 /id。")
    else:
        lines.append("获取某个人ID：回复他的任意消息发送 /id。")

    return "\n".join(lines)

def format_start_command_reply(message):
    sender = message.get("from", {}) if isinstance(message, dict) else {}
    sender_id = sender.get("id", "未知")
    menu_url = get_bot_menu_url()

    lines = [
        "已开启 Bot 通知。",
        "",
        f"你的 Telegram ID：{sender_id}",
        "",
        "常用操作：",
        "1. 发送 /id 可以再次查看自己的 ID。",
        "2. 在群里回复某个人的消息发送 /id，可以获取那个人的 ID。",
        "3. 打开网页面板的「稍等预警名单」，勾选要预警的稍等关键词。",
        "4. 把要接收预警的人的 ID 填到对应关键词的接收人 Chat ID，保存后生效。",
        "",
        "注意：要私聊接收预警的人，必须先点过这个 Bot 的 Start。"
    ]
    if menu_url:
        lines.insert(9, f"网页面板：{menu_url}")
    else:
        lines.append("网页面板入口未配置：需要设置 BOT_MENU_URL 为 Zeabur 的 https 域名。")

    return "\n".join(lines)

def parse_order_table_block(raw_lines):
    if len(raw_lines) < 4:
        return None

    member = raw_lines[0]
    real_name = raw_lines[1]
    detail_line = next((
        line for line in raw_lines[2:]
        if ("取款订单" in line or "存款订单" in line)
        and re.search(r"\b[A-Z0-9]{12,}\b", line)
    ), "")
    card_no = next((line for line in reversed(raw_lines) if re.fullmatch(r"\d{12,25}", line)), "")
    if not detail_line:
        return None

    parts = re.split(r"\s+", detail_line.strip())
    order_index = next((idx for idx, part in enumerate(parts) if re.fullmatch(r"[A-Z0-9]{12,}", part) and re.search(r"[A-Z]", part) and re.search(r"\d", part)), -1)
    if order_index < 6:
        return None

    site = parts[0]
    vip = parts[1]
    account = parts[2]
    order_no = parts[order_index]
    amount = parts[order_index + 1] if order_index + 1 < len(parts) else ""
    order_kind = parts[order_index + 2] if order_index + 2 < len(parts) else ""
    method = next((line for line in raw_lines[raw_lines.index(detail_line) + 1:] if not re.fullmatch(r"\d{12,25}", line)), "")
    if not all([member, real_name, site, vip, account, order_no, amount]):
        return None

    if "取款" in order_kind:
        if not card_no:
            return None
        order_label = "银行卡提款"
        tail_columns = [card_no, card_no]
    elif "存款" in order_kind:
        order_label = method or "存款订单"
        tail_columns = []
    else:
        return None

    today = datetime.now(BEIJING_TZ)
    date_text = f"{today.year}-{today.month}-{today.day}"
    label_columns = ["", order_label] if order_label in {"银行卡存款", "银行卡提款"} else [order_label]
    columns = [date_text, account, *label_columns, member, real_name, vip, order_no, amount, *tail_columns]
    return "\t".join(columns)

def parse_order_table_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return None
    blocks = re.split(r"\n\s*\n+", raw_text)
    rows = []
    for block in blocks:
        raw_lines = [line.strip() for line in block.splitlines() if line.strip()]
        row = parse_order_table_block(raw_lines)
        if row:
            rows.append(row)
    if rows:
        return "\n".join(rows)

    raw_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    return parse_order_table_block(raw_lines)

def parse_withdrawal_table_text(text):
    return parse_order_table_text(text)

LARGE_WITHDRAW_TIMEOUT_CACHE_TTL = 8 * 60 * 60
large_withdraw_timeout_cache = {}
large_withdraw_timeout_cache_lock = Lock()

def parse_large_timeout_datetime(raw_time):
    value = str(raw_time or "").strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}", value):
        value += ":00"
    for fmt in ("%Y-%m-%d %H:%M:%S",):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=BEIJING_TZ)
        except Exception:
            pass
    return None

def format_large_timeout_time(raw_time):
    value = str(raw_time or "").strip().replace("/", "-")
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{1,2}", value):
        value += ":00"
    return value

def format_large_timeout_duration(completion_time, now):
    completion_dt = parse_large_timeout_datetime(completion_time)
    if not completion_dt:
        return None
    diff_seconds = int((now - completion_dt).total_seconds())
    if diff_seconds <= 0:
        return "0:00:00"
    hours = diff_seconds // 3600
    minutes = (diff_seconds % 3600) // 60
    seconds = diff_seconds % 60
    if hours >= 24:
        hours -= 24
    return f"{hours}:{minutes:02d}:{seconds:02d}"

def chinese_number(num):
    numbers = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十"]
    try:
        value = int(num)
    except Exception:
        return str(num)
    if 0 <= value <= 10:
        return numbers[value]
    if 10 < value < 20:
        return "十" + ("" if value % 10 == 0 else numbers[value % 10])
    if 20 <= value < 100:
        return numbers[value // 10] + "十" + ("" if value % 10 == 0 else numbers[value % 10])
    return str(value)

def next_large_timeout_order_count(order_no):
    order_no = str(order_no or "").strip()
    if not order_no:
        return 1

    redis_key = f"large_withdraw_timeout_count:{order_no}"
    client = get_wait_alert_redis_client()
    if client:
        try:
            count = int(client.get(redis_key) or 0) + 1
            client.setex(redis_key, LARGE_WITHDRAW_TIMEOUT_CACHE_TTL, count)
            return count
        except Exception as e:
            logger.warning(f"⚠️ [LargeTimeout] Redis 催促次数缓存失败，回退内存: {e}")

    now_ts = time.time()
    with large_withdraw_timeout_cache_lock:
        expired_keys = [
            key for key, item in large_withdraw_timeout_cache.items()
            if not isinstance(item, dict) or item.get("expires_at", 0) <= now_ts
        ]
        for key in expired_keys[:100]:
            large_withdraw_timeout_cache.pop(key, None)

        item = large_withdraw_timeout_cache.get(order_no) or {}
        count = int(item.get("count") or 0) + 1 if item.get("expires_at", 0) > now_ts else 1
        large_withdraw_timeout_cache[order_no] = {
            "count": count,
            "expires_at": now_ts + LARGE_WITHDRAW_TIMEOUT_CACHE_TTL,
        }
        return count

def parse_large_timeout_line(line, fixed_now):
    raw = str(line or "").strip()
    if not raw:
        return None

    account = None
    for item in re.findall(r"[A-Za-z0-9_]+", raw):
        if (
            not re.fullmatch(r"\d+", item)
            and not re.fullmatch(r"(WD|MW|HWD)[A-Za-z0-9]+", item)
            and not re.fullmatch(r"VIP\d+", item, flags=re.I)
            and not re.fullmatch(r"\d", item)
            and not re.fullmatch(r"[\d,]+\.\d+", item)
            and not re.fullmatch(r"\d{5,6}", item)
            and not re.fullmatch(r"区间段\d+", item)
            and len(item) >= 3
        ):
            account = item
            break

    order_match = re.search(r"\b(?:WD|MW|HWD)[A-Za-z0-9]+\b", raw)
    vip_match = re.search(r"\bVIP(\d+)\b", raw, flags=re.I)
    single_level_match = re.search(r"\b(\d)\b", raw)
    amount_match = re.search(r"\b[\d,]+\.\d{3}\b", raw) or re.search(r"\b\d{5,6}\b", raw)
    times = re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}:\d{1,2}", raw)
    if not times:
        times = [
            item + ":00"
            for item in re.findall(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}\s+\d{1,2}:\d{1,2}(?!:\d)", raw)
        ]

    order = order_match.group(0) if order_match else None
    level = vip_match.group(1) if vip_match else (single_level_match.group(1) if single_level_match else None)
    amount = amount_match.group(0).replace(",", "").split(".")[0] if amount_match else None
    first_time = times[0] if len(times) >= 1 else None
    completion_time = times[1] if len(times) >= 2 else None
    duration = format_large_timeout_duration(completion_time, fixed_now) if completion_time else None

    if not all([account, order, level, amount, first_time, completion_time, duration]):
        return None
    return {
        "account": account,
        "order": order,
        "level": level,
        "amount": amount,
        "time": format_large_timeout_time(first_time),
        "duration": duration,
    }

def format_large_timeout_template(record):
    count = next_large_timeout_order_count(record.get("order"))
    return "\n".join([
        "站点 ：ML",
        f"等级：VIP{record.get('level')}",
        f"会员账号：{record.get('account')}",
        f"提款订单：{record.get('order')}",
        f"提款金额 : {record.get('amount')}",
        f"提款时间：{record.get('time')}",
        f"问题反馈：大额提款超时{record.get('duration')}",
        f"订单催促次数：第{chinese_number(count)}次",
    ])

def format_large_timeout_sheet_row(record, fixed_now):
    system_time = fixed_now.strftime("%H:%M")
    return "\t".join([
        system_time,
        "提款超时",
        "PTYY1B",
        "ARATAKITO",
        "ML",
        f"VIP{record.get('level')}",
        record.get("account") or "",
        record.get("order") or "",
        record.get("time") or "",
        record.get("amount") or "",
        record.get("duration") or "",
    ])

def parse_large_timeout_text(text):
    raw_text = str(text or "").strip()
    if not raw_text:
        return None

    fixed_now = datetime.now(BEIJING_TZ)
    records = []
    for line in raw_text.splitlines():
        record = parse_large_timeout_line(line, fixed_now)
        if record:
            records.append(record)

    if not records:
        compact = " ".join(line.strip() for line in raw_text.splitlines() if line.strip())
        record = parse_large_timeout_line(compact, fixed_now)
        if record:
            records.append(record)

    if not records:
        return None

    output1 = "\n\n".join(format_large_timeout_template(record) for record in records)
    output2 = "\n".join(format_large_timeout_sheet_row(record, fixed_now) for record in records)
    copy_page_url = store_copy_page("大额提款超时转换结果", [
        {"title": "格式1：标准模板", "text": output1},
        {"title": "格式2：表格分列", "text": output2},
    ])
    return {
        "text": output1,
        "reply_markup": build_conversion_reply_markup(output1, "复制格式1", copy_page_url),
        "copy_page_token": get_copy_page_token_from_url(copy_page_url),
        "extra_replies": [
            {
                "text": output2,
                "reply_markup": build_conversion_reply_markup(output2, "复制格式2", copy_page_url),
            }
        ],
    }

def _bot_get_updates(offset=None, timeout=50):
    if not BOT_TOKEN:
        return None

    params = {
        "timeout": timeout,
        "allowed_updates": json.dumps(["message", "callback_query"]),
    }
    if offset is not None:
        params["offset"] = offset

    try:
        resp = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params=params, timeout=timeout + 10)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("ok"):
            return data
        log_tree(9, f"Bot 命令轮询失败: HTTP {resp.status_code} {str(data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 命令轮询异常: {e}")
    return None

def _bot_send_reply(chat_id, text, reply_to_message_id=None, message_thread_id=None, reply_markup=None):
    if not BOT_TOKEN:
        return None

    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
        payload["allow_sending_without_reply"] = True
    if message_thread_id:
        payload["message_thread_id"] = message_thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code != 200 or not data.get("ok"):
            log_tree(9, f"Bot 命令回复失败: HTTP {resp.status_code} {str(data)[:200]}")
            return None
        return ((data.get("result") or {}).get("message_id"))
    except Exception as e:
        log_tree(9, f"Bot 命令回复异常: {e}")
        return None

def _bot_delete_message(chat_id, message_id):
    if not BOT_TOKEN or chat_id is None or message_id is None:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage",
            json={"chat_id": chat_id, "message_id": message_id},
            timeout=20,
        )
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("ok"):
            return True
        log_tree(9, f"Bot 删除账号消息失败: HTTP {resp.status_code} {str(data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 删除账号消息异常: {e}")
    return False

def _bot_edit_message_text(chat_id, message_id, text, reply_markup=None):
    if not BOT_TOKEN or chat_id is None or message_id is None:
        return False
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText", json=payload, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("ok"):
            return True
        description = str(data.get("description") or "")
        if "message is not modified" not in description.lower():
            log_tree(9, f"Bot 编辑消息失败: HTTP {resp.status_code} {str(data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 编辑消息异常: {e}")
    return False

def _bot_answer_callback_query(callback_query_id, text=""):
    if not BOT_TOKEN or not callback_query_id:
        return False
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery", json=payload, timeout=20)
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if resp.status_code == 200 and data.get("ok"):
            return True
        log_tree(9, f"Bot 回调确认失败: HTTP {resp.status_code} {str(data)[:200]}")
    except Exception as e:
        log_tree(9, f"Bot 回调确认异常: {e}")
    return False

def _handle_bot_callback_query(callback):
    data = str(callback.get("data") or "")
    callback_id = callback.get("id")
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if data == "delete_notice":
        deleted = _bot_delete_message(chat_id, message_id)
        _bot_answer_callback_query(callback_id, "已删除" if deleted else "删除失败")
        return True
    return False

def is_bot_command(text, command):
    return bool(re.match(rf"^/{re.escape(command)}(?:@[A-Za-z0-9_]+)?(?:\s|$)", str(text or "").strip(), re.IGNORECASE))

def parse_site_message_members(raw):
    members = []
    seen = set()
    for item in re.split(r"[,;\s]+", str(raw or "").replace("，", ",")):
        name = item.strip().lower()
        if not name or name in seen:
            continue
        if not SITE_MESSAGE_MEMBER_RE.fullmatch(name):
            continue
        seen.add(name)
        members.append(name)
    return members

def cleanup_site_message_chunks(chat_id):
    key = str(chat_id)
    state = SITE_MESSAGE_CHUNK_STATE.get(key)
    if not state:
        return None
    if time.time() - float(state.get("updated_at", 0)) > SITE_MESSAGE_CHUNK_TTL_SECONDS:
        SITE_MESSAGE_CHUNK_STATE.pop(key, None)
        return None
    return state

def cache_site_message_chunk(message, text):
    chat_id = ((message.get("chat") or {}).get("id"))
    if chat_id is None:
        return False
    members = parse_site_message_members(text)
    if len(members) < 10:
        return False
    key = str(chat_id)
    state = cleanup_site_message_chunks(chat_id) or {"chunks": [], "updated_at": time.time()}
    message_id = message.get("message_id")
    if not any(item.get("message_id") == message_id for item in state.get("chunks", [])):
        state.setdefault("chunks", []).append({
            "message_id": message_id,
            "text": str(text or ""),
            "member_count": len(members),
            "created_at": time.time(),
        })
    state["updated_at"] = time.time()
    SITE_MESSAGE_CHUNK_STATE[key] = state
    return True

def collect_site_message_text(message):
    text = str(message.get("text") or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    strategy = lines[-1].lower() if lines else ""
    chat_id = ((message.get("chat") or {}).get("id"))
    if strategy in SITE_MESSAGE_STRATEGIES:
        state = cleanup_site_message_chunks(chat_id) if chat_id is not None else None
        chunks = list((state or {}).get("chunks") or [])
        if chat_id is not None:
            SITE_MESSAGE_CHUNK_STATE.pop(str(chat_id), None)
        previous = [item for item in chunks if item.get("message_id") != message.get("message_id")]
        previous_text = "\n".join(str(item.get("text") or "") for item in previous if item.get("text"))
        previous_ids = [item.get("message_id") for item in previous if item.get("message_id")]
        return "\n".join([part for part in [previous_text, text] if part]).strip(), previous_ids, False
    cached = cache_site_message_chunk(message, text)
    return text, [], cached

def parse_site_message_request(text):
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return [], ""
    strategy = lines[-1].lower()
    if strategy not in SITE_MESSAGE_STRATEGIES:
        return [], ""
    raw = "\n".join(lines[:-1]).replace("，", ",")
    return parse_site_message_members(raw), strategy

def zc_time_window_text(now=None):
    now = now or datetime.now(BEIJING_TZ)
    start_hour = (now.hour - 3) % 24
    end_hour = (now.hour - 1) % 24
    return f"{start_hour:02d}:00-{end_hour:02d}:59"

def format_zc_summary(counts):
    jn_count = counts.get("jn", "")
    ml_count = counts.get("ml", "")
    return "\n".join([
        "JN/ML站",
        zc_time_window_text(),
        "🎁💰邀友一起狂欢💰🎁",
        "🎁🎁体育包赔赛事🎁🎁",
        "🎁💰新人注册五重礼💰🎁",
        "⏰💰虚拟币存款三重礼💰⏰",
        "💰EBpay💰",
        "🌟添加一对一专属经理🌟",
        f"（JN站 {jn_count}人）（ML站 {ml_count}  人）",
        "",
        "EBpay优惠卷发放通知",
        "（ML站/  人）",
    ])

def sb_time_window_text(now=None):
    now = now or datetime.now(BEIJING_TZ)
    target_hour = (now.hour - 1) % 24
    for start_hour, end_hour, label in SB_REPORT_WINDOWS:
        if start_hour <= target_hour <= end_hour:
            return label
    return f"{target_hour:02d}:00-{target_hour:02d}:59"

def format_sb_summary(ml_count, jn_count=0):
    return "\n".join([
        "JN/ML站",
        sb_time_window_text(),
        "",
        "v2-v10存款方式存款失败引导",
        f"（JN站  {jn_count} 人）（ML站 {ml_count}人）",
        "已发送",
    ])

def progress_bar(percent, width=18):
    value = max(0, min(100, int(percent or 0)))
    filled = int(round(width * value / 100))
    return "▰" * filled + "▱" * (width - filled)

def site_message_type_label(strategy):
    return {
        "sb": "存款温馨提示",
        "9zc": "9站新注册连续6条",
        "6zc": "6站新注册连续3条",
    }.get(strategy, strategy)

def site_message_step_total(strategy):
    return {
        "9zc": 6,
        "6zc": 3,
        "sb": 1,
    }.get(strategy, 1)

def format_site_message_progress(strategy, member_count, progress=None, force_percent=None):
    progress = progress or {}
    percent = force_percent if force_percent is not None else progress.get("percent", 0)
    step = int(progress.get("step") or 0)
    total = int(progress.get("total") or site_message_step_total(strategy))
    message = str(progress.get("message") or "等待扩展接收任务")
    title = str(progress.get("title") or "")
    lines = [
        "站内信发送中",
        f"类型：{site_message_type_label(strategy)}",
        f"人数：{member_count}",
        f"进度：{progress_bar(percent)} {max(0, min(100, int(percent or 0)))}%",
        f"步骤：{step}/{total}",
        f"状态：{message}",
    ]
    if title:
        lines.append(f"当前：{title}")
    return "\n".join(lines)

def cleanup_zc_state(chat_id):
    key = str(chat_id)
    state = ZC_BATCH_STATE.get(key)
    if not state:
        return None
    if time.time() - float(state.get("updated_at", 0)) > ZC_BATCH_TTL_SECONDS:
        ZC_BATCH_STATE.pop(key, None)
        return None
    return state

async def delete_bot_message_later(chat_id, message_id, delay_seconds=30):
    if chat_id is None or message_id is None:
        return
    await asyncio.sleep(max(0, float(delay_seconds or 30)))
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, lambda: _bot_delete_message(chat_id, message_id))

async def wait_backend_result_with_progress(cmd_id, chat_id, strategy, member_count):
    loop = asyncio.get_event_loop()
    progress_message_id = await loop.run_in_executor(
        None,
        lambda: _bot_send_reply(chat_id, format_site_message_progress(strategy, member_count, {"percent": 0}))
    )
    wait_task = asyncio.create_task(wait_backend_command_result(cmd_id, timeout=180.0))
    last_text = ""
    last_edit = 0.0
    while not wait_task.done():
        progress = get_backend_command_progress(cmd_id) if get_backend_command_progress else {}
        text = format_site_message_progress(strategy, member_count, progress)
        now_ts = time.time()
        if progress_message_id and text != last_text and now_ts - last_edit >= 0.8:
            await loop.run_in_executor(
                None,
                lambda cid=chat_id, mid=progress_message_id, txt=text: _bot_edit_message_text(cid, mid, txt)
            )
            last_text = text
            last_edit = now_ts
        await asyncio.sleep(0.7)
    result = await wait_task
    final_progress = get_backend_command_progress(cmd_id) if get_backend_command_progress else {}
    if str((result or {}).get("status") or "") == "success":
        final_progress = {**final_progress, "percent": 100, "status": "success", "message": "发送完成"}
    elif result:
        final_progress = {**final_progress, "status": result.get("status") or "failed", "message": result.get("detail") or "发送失败"}
    final_text = format_site_message_progress(strategy, member_count, final_progress)
    if progress_message_id:
        await loop.run_in_executor(
            None,
            lambda cid=chat_id, mid=progress_message_id, txt=final_text: _bot_edit_message_text(cid, mid, txt)
        )
        if str((result or {}).get("status") or "") == "success":
            await asyncio.sleep(0.8)
    return result, progress_message_id

async def handle_site_message_bot_request(message):
    if not queue_site_inner_message_command or not wait_backend_command_result:
        return "站内信模块未加载，无法执行。"
    combined_text, split_source_ids, waiting_for_tail = collect_site_message_text(message)
    if waiting_for_tail:
        return None
    members, strategy = parse_site_message_request(combined_text)
    if not members:
        return None
    cmd_id, clean_members = queue_site_inner_message_command(members, source=f"telegram_bot_{strategy}", strategy=strategy)
    chat_id = ((message.get("chat") or {}).get("id"))
    result, progress_message_id = await wait_backend_result_with_progress(cmd_id, chat_id, strategy, len(clean_members))
    status = str((result or {}).get("status") or "timeout")
    detail = str((result or {}).get("detail") or "无回执")
    ok = status == "success"
    message_id = message.get("message_id")
    if strategy == "sb" and ok:
        return {
            "text": format_sb_summary(len(clean_members), 0),
            "delete_source": True,
            "reply_to_source": False,
            "skip_send": True,
            "sent_message_id": progress_message_id,
            "edit_message_id": progress_message_id,
            "auto_delete_after": 30,
            "delete_after_send_ids": split_source_ids,
        }
    if strategy in {"6zc", "9zc"} and ok:
        site_key = "jn" if strategy == "6zc" else "ml"
        state = cleanup_zc_state(chat_id) or {"counts": {}, "marker_ids": [], "source_ids": [], "updated_at": time.time()}
        state["counts"][site_key] = len(clean_members)
        state.setdefault("source_ids", []).extend([*split_source_ids, message_id])
        state["updated_at"] = time.time()
        ZC_BATCH_STATE[str(chat_id)] = state
        if "jn" in state.get("counts", {}) and "ml" in state.get("counts", {}):
            final_text = format_zc_summary(state["counts"])
            cleanup_ids = [mid for mid in (state.get("marker_ids", []) + state.get("source_ids", [])) if mid]
            ZC_BATCH_STATE.pop(str(chat_id), None)
            return {
                "text": final_text,
                "delete_source": False,
                "reply_to_source": False,
                "skip_send": True,
                "sent_message_id": progress_message_id,
                "edit_message_id": progress_message_id,
                "auto_delete_after": 30,
                "delete_after_send_ids": cleanup_ids,
            }
        return {
            "text": "1",
            "delete_source": True,
            "reply_to_source": False,
            "skip_send": True,
            "sent_message_id": progress_message_id,
            "edit_message_id": progress_message_id,
            "auto_delete_after": 30,
            "remember_zc_marker": True,
            "zc_chat_id": chat_id,
        }
    reply_text = "\n".join([
        "站内信发送结果",
        f"类型：{site_message_type_label(strategy)}",
        f"人数：{len(clean_members)}",
        f"状态：{'成功' if ok else '失败'}",
        f"详情：{detail}",
    ])
    return {
        "text": reply_text,
        "delete_source": ok,
        "reply_to_source": not ok,
        "skip_send": True,
        "sent_message_id": progress_message_id,
        "edit_message_id": progress_message_id,
        "auto_delete_after": 30,
    }

async def bot_command_polling_task():
    if not BOT_TOKEN:
        return

    loop = asyncio.get_event_loop()
    offset = None
    first_batch = await loop.run_in_executor(None, lambda: _bot_get_updates(timeout=0))
    if first_batch and first_batch.get("result"):
        offset = max(item.get("update_id", 0) for item in first_batch["result"]) + 1
    log_tree(1, "Bot /start /id 命令轮询已启动")

    while True:
        try:
            data = await loop.run_in_executor(None, lambda current_offset=offset: _bot_get_updates(offset=current_offset, timeout=50))
            if not data:
                await asyncio.sleep(5)
                continue

            for update in data.get("result", []):
                update_id = update.get("update_id")
                if update_id is not None:
                    offset = update_id + 1

                callback = update.get("callback_query")
                if callback:
                    await loop.run_in_executor(None, lambda cb=callback: _handle_bot_callback_query(cb))
                    continue

                message = update.get("message") or {}
                text = message.get("text")
                delete_source = False
                reply_to_source = True
                remember_zc_marker = False
                zc_chat_id = None
                delete_after_send_ids = []
                skip_send = False
                edit_message_id = None
                auto_delete_after = None
                reply_markup = None
                extra_replies = []
                copy_page_token = None
                if is_bot_command(text, "start"):
                    reply_text = format_start_command_reply(message)
                elif is_bot_command(text, "id"):
                    reply_text = format_id_command_reply(message)
                else:
                    chat = message.get("chat", {})
                    if chat.get("type") != "private":
                        continue
                    withdrawal_text = parse_withdrawal_table_text(text)
                    if withdrawal_text:
                        copy_page_url = store_copy_page("存款/提款表格转换结果", [
                            {"title": "表格分列结果", "text": withdrawal_text}
                        ])
                        reply_result = {
                            "text": withdrawal_text,
                            "reply_markup": build_conversion_reply_markup(withdrawal_text, "复制整理结果", copy_page_url),
                            "copy_page_token": get_copy_page_token_from_url(copy_page_url),
                        }
                    else:
                        reply_result = parse_large_timeout_text(text)
                        if not reply_result:
                            reply_result = await handle_site_message_bot_request(message)
                    if not reply_result:
                        continue
                    if isinstance(reply_result, dict):
                        reply_text = reply_result.get("text") or ""
                        delete_source = bool(reply_result.get("delete_source"))
                        reply_to_source = reply_result.get("reply_to_source") is not False
                        remember_zc_marker = bool(reply_result.get("remember_zc_marker"))
                        zc_chat_id = reply_result.get("zc_chat_id")
                        delete_after_send_ids = list(reply_result.get("delete_after_send_ids") or [])
                        skip_send = bool(reply_result.get("skip_send"))
                        edit_message_id = reply_result.get("edit_message_id")
                        sent_message_id = reply_result.get("sent_message_id")
                        auto_delete_after = reply_result.get("auto_delete_after")
                        reply_markup = reply_result.get("reply_markup")
                        extra_replies = list(reply_result.get("extra_replies") or [])
                        copy_page_token = reply_result.get("copy_page_token")
                    else:
                        reply_text = str(reply_result)
                        delete_source = False
                        reply_to_source = True

                chat = message.get("chat", {})
                chat_id = chat.get("id")
                if chat_id is None:
                    continue

                if skip_send and edit_message_id:
                    await loop.run_in_executor(
                        None,
                        lambda cid=chat_id, mid=edit_message_id, txt=reply_text: _bot_edit_message_text(cid, mid, txt)
                    )
                else:
                    sent_message_id = await loop.run_in_executor(
                        None,
                        lambda cid=chat_id, txt=reply_text, mid=(message.get("message_id") if reply_to_source else None), thread=message.get("message_thread_id"), markup=reply_markup: _bot_send_reply(cid, txt, mid, thread, markup)
                    )
                    conversion_sent_ids = [sent_message_id] if sent_message_id else []
                    for extra_reply in extra_replies:
                        if isinstance(extra_reply, dict):
                            extra_text = extra_reply.get("text") or ""
                            extra_markup = extra_reply.get("reply_markup")
                        else:
                            extra_text = str(extra_reply or "")
                            extra_markup = None
                        if not extra_text:
                            continue
                        extra_sent_message_id = await loop.run_in_executor(
                            None,
                            lambda cid=chat_id, txt=extra_text, mid=(message.get("message_id") if reply_to_source else None), thread=message.get("message_thread_id"), markup=extra_markup: _bot_send_reply(cid, txt, mid, thread, markup)
                        )
                        if extra_sent_message_id:
                            conversion_sent_ids.append(extra_sent_message_id)
                    if copy_page_token:
                        attach_copy_page_delete_targets(
                            copy_page_token,
                            chat_id,
                            [message.get("message_id"), *conversion_sent_ids]
                        )
                if auto_delete_after and sent_message_id:
                    asyncio.create_task(delete_bot_message_later(chat_id, sent_message_id, auto_delete_after))
                if remember_zc_marker and sent_message_id:
                    state = ZC_BATCH_STATE.get(str(zc_chat_id))
                    if state is not None:
                        state.setdefault("marker_ids", []).append(sent_message_id)
                        state["updated_at"] = time.time()
                if delete_source:
                    await loop.run_in_executor(
                        None,
                        lambda cid=chat_id, mid=message.get("message_id"): _bot_delete_message(cid, mid)
                    )
                if delete_after_send_ids:
                    for mid in dict.fromkeys(delete_after_send_ids):
                        await loop.run_in_executor(
                            None,
                            lambda cid=chat_id, delete_mid=mid: _bot_delete_message(cid, delete_mid)
                        )
        except Exception as e:
            log_tree(9, f"Bot /start /id 命令处理异常: {e}")
            await asyncio.sleep(5)

async def send_alert(text, link, extra_log="", target_ids=None):
    if not BOT_TOKEN: return
    summary = text.splitlines()[1] if len(text.splitlines()) > 1 else '通知'
    log_tree(3, f"{extra_log} [ALERT] 发送报警 -> {summary}")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    loop = asyncio.get_event_loop()
    tasks = []
    raw_targets = extract_target_list(target_ids) if target_ids else [str(x) for x in ALERT_GROUP_IDS]
    if not raw_targets:
        raw_targets = [str(x) for x in ALERT_GROUP_IDS]

    for chat_id in raw_targets:
        chat_id = normalize_chat_id(chat_id)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}
        reply_markup = build_copy_link_markup(link)
        if reply_markup:
            payload["reply_markup"] = reply_markup
        tasks.append(loop.run_in_executor(None, lambda p=payload: _post_request(url, p)))
    if tasks: await asyncio.gather(*tasks)

async def check_msg_exists(channel_id, msg_id):
    try:
        msg = await client.get_messages(channel_id, ids=msg_id)
        if not msg: 
            log_tree(2, f"❌ 检查发现消息 {msg_id} 已物理删除")
            return False 
        return True
    except Exception as e:
        log_tree(2, f"⚠️ 网络检测失败 ({e}) -> 强制防漏报")
        return True 

# ==========================================
# 模块 6: 任务管理与核心逻辑
# ==========================================
async def audit_pending_tasks():
    log_tree(4, "开始执行【下班巡检】(扫描全部稍等关键词)...")
    
    all_keywords = sorted(list(WAIT_SIGNATURES))
    all_keywords = sorted(list(set(all_keywords)), key=lambda x: (len(x), x), reverse=True) 
    if not all_keywords:
        log_tree(4, "下班巡检跳过：WAIT_KEYWORDS 为空")
        return
    
    history_cache = {}
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=10) 
    
    EXCLUDED_GROUPS = [-1002807120955, -1002169616907]

    log_tree(4, "正在预读取消息历史 (最近10小时)...")
    for chat_id in CS_GROUP_IDS:
        if chat_id in EXCLUDED_GROUPS: continue
        try:
            msgs = []
            async for m in client.iter_messages(chat_id, limit=3000):
                if m.date and m.date < cutoff_time: break
                if getattr(m, 'action', None): continue # 过滤系统服务消息
                msgs.append(m)
            history_cache[chat_id] = msgs
        except Exception as e:
            logger.error(f"Group {chat_id} fetch failed: {e}")

    total_issues = 0
    notified_targets = set()

    for keyword in all_keywords:
        if not keyword.strip():
            continue
        keyword_targets = get_alert_targets_for_keyword(keyword)
        kw_issues = []
        found_count = 0
        closed_count = 0
        
        for chat_id, history in history_cache.items():
            thread_latest_msg = {}
            for m in history:
                t_id = None
                if m.reply_to:
                    t_id = m.reply_to.reply_to_top_id 
                    if not t_id: t_id = m.reply_to.reply_to_msg_id
                if not t_id: t_id = m.id
                if t_id not in thread_latest_msg:
                    thread_latest_msg[t_id] = m
            
            for m in history:
                if not m.text: continue
                
                if keyword in m.text:
                    is_cs_sender = False
                    if m.sender_id in ([MY_ID] + OTHER_CS_IDS): is_cs_sender = True
                    else:
                        sender = await m.get_sender()
                        name = getattr(sender, 'first_name', '') or ''
                        if name.startswith(tuple(CS_NAME_PREFIXES)): is_cs_sender = True
                    
                    if not is_cs_sender: continue

                    found_count += 1

                    t_id = None
                    if m.reply_to:
                        t_id = m.reply_to.reply_to_top_id or m.reply_to.reply_to_msg_id
                    if not t_id: t_id = m.id
                    
                    latest_msg = thread_latest_msg.get(t_id, m)
                    is_closed, reason = await _check_is_closed_logic(latest_msg)
                    
                    if is_closed:
                        closed_count += 1
                    else:
                        cs_name_display = "未知客服"
                        try:
                            s = await m.get_sender()
                            if s: cs_name_display = getattr(s, 'first_name', 'Unknown')
                        except: pass

                        customer_text = "[无法获取原问题]"
                        if m.reply_to:
                            try:
                                r_msg = await m.get_reply_message()
                                if r_msg: customer_text = (r_msg.text or "[媒体]")[:20] + "..."
                            except: pass

                        link = ""
                        real_chat_id = str(chat_id).replace('-100', '')
                        url_thread_id = None
                        if "(客户删消息)" not in reason:
                             if latest_msg.reply_to:
                                 url_thread_id = latest_msg.reply_to.reply_to_top_id or latest_msg.reply_to.reply_to_msg_id
                        
                        if url_thread_id:
                             link = f"https://t.me/c/{real_chat_id}/{latest_msg.id}?thread={url_thread_id}"
                        else:
                             link = f"https://t.me/c/{real_chat_id}/{latest_msg.id}"
                        
                        cs_reply_text = (latest_msg.text or "[媒体]")[:15]

                        kw_issues.append({
                            'cs_name': cs_name_display,
                            'customer_text': customer_text,
                            'cs_reply': cs_reply_text,
                            'reason': reason,
                            'link': link
                        })

        if kw_issues:
            total_issues += len(kw_issues)
            open_count = found_count - closed_count
            audit_copy_links = []
            report_text = (
                f"👮 **下班巡检报告**\n"
                f"🔑 关键词: `{keyword}`\n"
                f"📊 命中: {found_count} | ✅ 闭环: {closed_count} | ❌ 未闭环: {open_count}\n\n"
            )
            
            for i, iss in enumerate(kw_issues[:8]): 
                item_index = i + 1
                audit_copy_links.append((f"复制第{item_index}条消息链接", iss['link']))
                report_text += (
                    f"{item_index}. 👤 {md_escape(iss['cs_name'])}\n"
                    f"   💬 客户: {md_inline(iss['customer_text'], 40)}\n"
                    f"   👉 结果: {md_inline(iss['cs_reply'], 40)} ({md_escape(iss['reason'])})\n"
                    f"   {format_copy_link(iss['link'], item_index)}\n\n"
                )
            
            if len(kw_issues) > 8:
                report_text += f"... (还有 {len(kw_issues)-8} 条未显示)"
            
            await send_alert(report_text, audit_copy_links, f"Audit-{keyword}", target_ids=keyword_targets)
            for target in extract_target_list(keyword_targets or ALERT_GROUP_IDS):
                notified_targets.add(str(target))
            await asyncio.sleep(2) 
        else:
            log_tree(4, f"关键词 '{keyword}' 巡检完成，无异常 (总数: {found_count})")

    if total_issues:
        log_tree(4, f"下班巡检结束：总计发现 {total_issues} 个未闭环问题，已推送到 {len(notified_targets)} 个接收人")
    else:
        log_tree(4, "下班巡检结束：全部稍等关键词无未闭环问题")

async def perform_stop_work():
    global IS_WORKING, stop_work_lock
    if stop_work_lock is None:
        stop_work_lock = asyncio.Lock()

    if stop_work_lock.locked():
        log_tree(1, "下班流程已在执行，忽略重复下班请求")
        return

    async with stop_work_lock:
        if not IS_WORKING:
            log_tree(1, "当前已是下班模式，忽略重复下班请求")
            return

        IS_WORKING = False
        await audit_pending_tasks()
        for t in list(wait_tasks.values()) + list(followup_tasks.values()) + list(reply_tasks.values()) + list(self_reply_tasks.values()): t.cancel()
        wait_tasks.clear(); followup_tasks.clear(); reply_tasks.clear(); self_reply_tasks.clear()
        wait_task_keywords.clear()
        wait_timers.clear(); followup_timers.clear(); reply_timers.clear(); self_reply_timers.clear()
        wait_msg_map.clear(); followup_msg_map.clear()
        chat_user_active_msgs.clear()
        chat_thread_active_msgs.clear()
        msg_to_user_cache.clear()
        msg_content_cache.clear()
        group_to_user_cache.clear()
        cs_activity_log.clear()
        log_tree(2, "🔴 已切换为：下班模式 (网页/指令)")

async def perform_start_work():
    global IS_WORKING
    if stop_work_lock and stop_work_lock.locked():
        log_tree(1, "下班巡检正在执行，忽略上班请求")
        return
    if IS_WORKING:
        log_tree(1, "当前已是工作模式，忽略重复上班请求")
        return

    IS_WORKING = True
    log_tree(2, "🟢 已切换为：工作模式 (网页/指令)")

def register_task(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key not in chat_user_active_msgs: chat_user_active_msgs[u_key] = set()
        chat_user_active_msgs[u_key].add(msg_id)
        update_msg_cache(chat_id, msg_id, user_id)
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key not in chat_thread_active_msgs: chat_thread_active_msgs[t_key] = set()
        chat_thread_active_msgs[t_key].add(msg_id)

def remove_task_record(chat_id, user_id, msg_id, thread_id=None):
    if user_id:
        u_key = (chat_id, user_id)
        if u_key in chat_user_active_msgs:
            chat_user_active_msgs[u_key].discard(msg_id)
            if not chat_user_active_msgs[u_key]: del chat_user_active_msgs[u_key]
    if thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            chat_thread_active_msgs[t_key].discard(msg_id)
            if not chat_thread_active_msgs[t_key]: del chat_thread_active_msgs[t_key]

def cancel_tasks(chat_id, user_id, thread_id=None, target_msg_id=None, reason="未知", types=None):
    if types is None: types = ['wait', 'followup', 'reply', 'self_reply'] 
    
    targets = set()
    hit_specific = False
    
    if target_msg_id:
        if target_msg_id in wait_tasks or target_msg_id in followup_tasks or target_msg_id in reply_tasks or target_msg_id in self_reply_tasks:
            targets.add(target_msg_id)
            hit_specific = True
            
        if target_msg_id in wait_msg_map:
            targets.add(wait_msg_map[target_msg_id])
            hit_specific = True
            
        if target_msg_id in followup_msg_map:
            targets.add(followup_msg_map[target_msg_id])
            hit_specific = True

        if targets:
            hit_specific = True

    if not hit_specific and thread_id:
        t_key = (chat_id, thread_id)
        if t_key in chat_thread_active_msgs:
            targets.update(chat_thread_active_msgs[t_key])

    if not targets: return

    log_tree(1, f" ┣━━ 尝试销单 | 用户: {user_id} | 目标: {target_msg_id} | 命中: {hit_specific} | 任务池: {list(targets)}")
    count = 0
    cleared_ids = []
    for mid in targets:
        if 'wait' in types and mid in wait_tasks: wait_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'followup' in types and mid in followup_tasks: followup_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'reply' in types and mid in reply_tasks: reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
        if 'self_reply' in types and mid in self_reply_tasks: self_reply_tasks[mid].cancel(); count += 1; cleared_ids.append(mid)
    
    if count > 0:
        log_tree(2, f"销单成功 | {reason} | 流: {thread_id} | 任务: {cleared_ids}")

def cancel_disabled_wait_tasks(enabled_signatures):
    cancelled = []
    for key_id, keyword in list(wait_task_keywords.items()):
        if keyword in enabled_signatures:
            continue
        task = wait_tasks.get(key_id)
        if not task:
            continue
        cancelled.append((key_id, keyword))
        if bot_loop:
            bot_loop.call_soon_threadsafe(task.cancel)
        else:
            task.cancel()

    if cancelled:
        log_tree(2, f"网页预警名单更新，已取消 {len(cancelled)} 个未选中稍等倒计时任务: {cancelled}")
    return cancelled

def remove_map_entries_by_value(mapping, value):
    for key, mapped_value in list(mapping.items()):
        if mapped_value == value:
            del mapping[key]

def check_recent_activity_safe(chat_id, task_start_time, user_ids=None, thread_id=None, buffer_seconds=10):
    if user_ids:
        for uid in user_ids:
            last_act = cs_activity_log.get((chat_id, uid), 0)
            if last_act > task_start_time + buffer_seconds:
                return True, f"用户 {uid} 下有新回复"
    if thread_id:
        last_act = cs_activity_log.get((chat_id, thread_id), 0)
        if last_act > task_start_time + buffer_seconds:
            return True, f"消息流 {thread_id} 下有新回复"
    return False, None

async def has_cs_reply_after(chat_id, target_msg_id, trigger_timestamp, thread_id=None, limit=80):
    target_ids = get_related_album_msg_ids(chat_id, target_msg_id)
    if not target_ids:
        return False, None

    try:
        async for m in client.iter_messages(chat_id, limit=limit):
            if getattr(m, 'action', None):
                continue
            if m.date and m.date.timestamp() <= trigger_timestamp:
                break
            if not await is_official_cs(m):
                continue

            reply_ids = get_ordered_reply_target_ids(m)
            if target_ids.intersection(reply_ids):
                return True, f"客服 Msg={m.id} 已引用回复 Msg={target_msg_id}"

            if thread_id and thread_id in reply_ids:
                text = m.text or ""
                if not match_signature(text, WAIT_SIGNATURES) and text.strip() not in KEEP_SIGNATURES:
                    return True, f"客服 Msg={m.id} 已在同一消息流回复"
    except Exception as e:
        log_tree(9, f"漏回二次校验失败 Msg={target_msg_id}: {e}")
    return False, None

# ==========================================
# 模块 7: 倒计时任务
# ==========================================
async def task_wait_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None, wait_keyword=None):
    try:
        current_task = asyncio.current_task() 
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])
        
        log_tree(1, f"启动 [稍等] 倒计时 (12m) {ids_str} | Thread={thread_id}")
        
        end_time = trigger_timestamp + WAIT_TIMEOUT
        wait_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link, 'keyword': wait_keyword or ''}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(WAIT_TIMEOUT)
        
        if not IS_WORKING: return
        if not is_wait_keyword_alert_enabled(wait_keyword):
            log_tree(1, f"🛡️ 拦截 [稍等] 超时预警 Msg={key_id} | 关键词={wait_keyword} 已从网页预警名单取消")
            return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [稍等] {ids_str} | 原因: {safe_reason} (客服已处理)")
            return

        log_tree(2, f"触发 [稍等] 超时 Msg={key_id}")
        await send_alert(format_alert_message(
            "🚨 **稍等超时预警**",
            [
                ("客服", agent_name),
                ("关键词", wait_keyword),
                ("状态", f"已过 {WAIT_TIMEOUT // 60} 分钟，无后续客服回复"),
            ],
            "消息", original_text,
            link
        ), link, ids_str, target_ids=get_alert_targets_for_keyword(wait_keyword))

        CRITICAL_TIMEOUT = 10 * 60
        await asyncio.sleep(CRITICAL_TIMEOUT)
        
        if not IS_WORKING: return
        if not is_wait_keyword_alert_enabled(wait_keyword):
            log_tree(1, f"🛡️ 拦截 [稍等] 严重超时 Msg={key_id} | 关键词={wait_keyword} 已从网页预警名单取消")
            return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        is_safe_2, safe_reason_2 = check_recent_activity_safe(chat_id, trigger_timestamp, user_ids_list, thread_id)
        if is_safe_2:
             log_tree(2, f"🛡️ 拦截严重误报 [稍等] {ids_str} | 原因: {safe_reason_2}")
             return

        log_tree(3, f"🔥 触发 [稍等] 严重超时 Msg={key_id}")
        await send_alert(format_alert_message(
            f"🔥 **稍等严重超时（{int((WAIT_TIMEOUT+CRITICAL_TIMEOUT)/60)}分钟）**",
            [
                ("客服", agent_name),
                ("关键词", wait_keyword),
                ("状态", "第一次预警后 10 分钟仍无后续客服回复"),
            ],
            "原消息", original_text,
            link
        ), link, ids_str, target_ids=get_alert_targets_for_keyword(wait_keyword))

    except asyncio.CancelledError: pass 
    finally:
        if key_id in wait_tasks and wait_tasks[key_id] == current_task:
            del wait_tasks[key_id]
            if key_id in wait_timers: del wait_timers[key_id]
            if key_id in wait_task_keywords: del wait_task_keywords[key_id]
            if my_msg_id in wait_msg_map: del wait_msg_map[my_msg_id]
            for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_followup_timeout(key_id, agent_name, original_text, link, my_msg_id, chat_id, user_ids_list, trigger_timestamp, thread_id=None, wait_keyword=None):
    try:
        current_task = asyncio.current_task()
        ids_str = f"Msg={key_id}"
        if user_ids_list: ids_str += " " + " ".join([f"User={u}" for u in user_ids_list])

        log_tree(1, f"启动 [跟进] 倒计时 (15m) {ids_str} | Thread={thread_id}")
        end_time = trigger_timestamp + FOLLOWUP_TIMEOUT
        followup_timers[key_id] = {'ts': end_time, 'user': agent_name, 'url': link}
        for uid in user_ids_list: register_task(chat_id, uid, key_id, thread_id)

        await asyncio.sleep(FOLLOWUP_TIMEOUT)
        if not IS_WORKING: return
        if my_msg_id and not await check_msg_exists(chat_id, my_msg_id): return

        # 跟进只检查 thread 级别活动，不检查 user 级别：
        # 同一用户可能同时有多个不相关投诉，对其他 thread 的回复不能消除当前 thread 的跟进警告
        is_safe, safe_reason = check_recent_activity_safe(chat_id, trigger_timestamp, thread_id=thread_id)
        if is_safe:
            log_tree(2, f"🛡️ 拦截误报 [跟进] {ids_str} | 原因: {safe_reason}")
            return

        log_tree(2, f"触发 [跟进] 超时 Msg={key_id}")
        await send_alert(format_alert_message(
            "🚨 **跟进超时预警**",
            [
                ("客服", agent_name),
                ("关键词", wait_keyword),
                ("状态", f"反馈核实内容 {FOLLOWUP_TIMEOUT // 60} 分钟未跟进回复"),
            ],
            "消息", original_text,
            link
        ), link, ids_str, target_ids=get_alert_targets_for_keyword(wait_keyword))
    except asyncio.CancelledError: pass
    finally:
        if key_id in followup_tasks and followup_tasks[key_id] == current_task:
            del followup_tasks[key_id]
            if key_id in followup_timers: del followup_timers[key_id]
            if my_msg_id in followup_msg_map: del followup_msg_map[my_msg_id]
            for uid in user_ids_list: remove_task_record(chat_id, uid, key_id, thread_id)

async def task_reply_timeout(trigger_msg_id, sender_name, content, link, chat_id, user_id, target_name, trigger_timestamp, thread_id=None, wait_keyword=None):
    try:
        current_task = asyncio.current_task()
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [漏回] 监控 (5m) {ids_str} | Target={target_name} | Thread={thread_id}")
        
        end_time = trigger_timestamp + REPLY_TIMEOUT
        reply_timers[trigger_msg_id] = {'ts': end_time, 'user': sender_name, 'url': link, 'target': target_name}
        register_task(chat_id, user_id, trigger_msg_id, thread_id)
        
        await asyncio.sleep(REPLY_TIMEOUT)
        if not IS_WORKING: return
        if not is_configured_cs_group(chat_id):
            log_tree(1, f"🛡️ 拦截 [漏回] 非CS_GROUP_IDS群组 Msg={trigger_msg_id} | Chat={chat_id}")
            return

        replied, replied_reason = await has_cs_reply_after(chat_id, trigger_msg_id, trigger_timestamp, thread_id)
        if replied:
            log_tree(2, f"🛡️ 拦截误报 [漏回] Msg={trigger_msg_id} | 原因: {replied_reason}")
            return
        
        log_tree(2, f"触发 [漏回] 报警 Msg={trigger_msg_id}")
        await send_alert(format_alert_message(
            "🔔 **漏回消息提醒**",
            [
                ("用户", sender_name),
                ("回复客服", target_name),
                ("关键词", wait_keyword),
                ("状态", f"已 {REPLY_TIMEOUT // 60} 分钟未回复"),
            ],
            "内容", content,
            link
        ), link, ids_str, target_ids=get_alert_targets_for_keyword(wait_keyword))
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in reply_tasks and reply_tasks[trigger_msg_id] == current_task:
            del reply_tasks[trigger_msg_id]
            if trigger_msg_id in reply_timers: del reply_timers[trigger_msg_id]
            remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

async def task_self_reply_timeout(trigger_msg_id, user_name, content, link, chat_id, user_id, trigger_timestamp, thread_id=None, wait_keyword=None):
    try:
        current_task = asyncio.current_task()
        ids_str = f"Msg={trigger_msg_id} User={user_id}"
        log_tree(1, f"启动 [自回] 监控 (3m) {ids_str} | Thread={thread_id}")
        
        end_time = trigger_timestamp + SELF_REPLY_TIMEOUT
        self_reply_timers[trigger_msg_id] = {'ts': end_time, 'user': user_name, 'url': link}
        
        await asyncio.sleep(SELF_REPLY_TIMEOUT)
        if not IS_WORKING: return
        
        log_tree(2, f"触发 [自回] 报警 Msg={trigger_msg_id}")
        await send_alert(format_alert_message(
            "🔔 **自回防漏监测**",
            [
                ("用户", user_name),
                ("关键词", wait_keyword),
                ("状态", f"自行追加消息后 {SELF_REPLY_TIMEOUT // 60} 分钟未处理"),
            ],
            "内容", content,
            link
        ), link, ids_str, target_ids=get_alert_targets_for_keyword(wait_keyword))
    except asyncio.CancelledError: pass 
    finally:
        if trigger_msg_id in self_reply_tasks and self_reply_tasks[trigger_msg_id] == current_task:
             del self_reply_tasks[trigger_msg_id]
             if trigger_msg_id in self_reply_timers: del self_reply_timers[trigger_msg_id]
             remove_task_record(chat_id, user_id, trigger_msg_id, thread_id)

# ==========================================
# 模块 8: 客户端与逻辑增强
# ==========================================
client = TelegramClient(
    StringSession(SESSION_STRING), 
    API_ID, 
    API_HASH,
    device_model="VMware20,1", 
    app_version="6.7.5 x64",      
    system_version="Windows 10 x64",
    lang_code="zh-hans",
    system_lang_code="zh-hans"
)

@client.on(events.NewMessage(chats='me', pattern=r'^\s*(上班|下班|状态)\s*$'))
async def command_handler(event):
    cmd = event.text.strip()
    log_tree(0, f"收到指令: {cmd}")
    if cmd == '下班':
        await perform_stop_work()
    elif cmd == '上班':
        await perform_start_work()
    elif cmd == '状态':
        await send_alert(f"🟢 **当前状态**: {'工作中' if IS_WORKING else '已下班'}\n⏳ 稍等: {len(wait_tasks)}\n🕵️ 跟进: {len(followup_tasks)}\n🔔 漏回: {len(reply_tasks)}\n🔄 自回: {len(self_reply_tasks)}", "")

@client.on(events.MessageDeleted)
async def handler_deleted(event):
    if not event.chat_id or not is_configured_cs_group(event.chat_id):
        return
    for msg_id in event.deleted_ids:
        deleted_info = {'name': '未知', 'text': '未知'}
        deleted_info = msg_content_cache.get((event.chat_id, msg_id), deleted_info)
        if deleted_info['text'] == '未知':
            stored_text = get_last_stored_message_text(event.chat_id, msg_id)
            if stored_text:
                deleted_info['text'] = stored_text

        mark_message_snapshot_deleted(event.chat_id, msg_id)
        record_chat_event(
            "delete",
            event.chat_id,
            msg_id,
            sender_name=deleted_info['name'],
            text=deleted_info['text'],
            old_text=deleted_info['text'],
            msg_type="删除",
            raw=f"[DELETED] Msg={msg_id} | [{event.chat_id}] {deleted_info['name']}: {deleted_info['text']}"
        )

        if not IS_WORKING:
            continue

        deleted_cache.append(msg_id)
        sender_info_str = f"发送者: {deleted_info['name']} | 内容: [已隐藏]"

        if msg_id in wait_tasks: 
            wait_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务")

        if msg_id in wait_msg_map:
            target_id = wait_msg_map[msg_id]
            if target_id in wait_tasks:
                wait_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [稍等] 任务(Target={target_id})")
            del wait_msg_map[msg_id]

        if msg_id in followup_tasks: 
            followup_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测(任务本体) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务")

        if msg_id in followup_msg_map:
            target_id = followup_msg_map[msg_id]
            if target_id in followup_tasks:
                followup_tasks[target_id].cancel()
                log_tree(2, f"🗑️ 物理删除侦测(触发指令) Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [跟进] 任务(Target={target_id})")
            del followup_msg_map[msg_id]

        if msg_id in reply_tasks: 
            reply_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测 Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [漏回] 监控")
            
        if msg_id in self_reply_tasks:
            self_reply_tasks[msg_id].cancel()
            log_tree(2, f"🗑️ 物理删除侦测 Msg={msg_id} | {sender_info_str} -> 🛑 撤销 [自回] 监控")

async def get_traceable_sender(chat_id, reply_to_msg_id, current_recursion=0):
    if (chat_id, reply_to_msg_id) in msg_to_user_cache:
        return msg_to_user_cache[(chat_id, reply_to_msg_id)]

    if current_recursion > 3: return None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs: return None
        target_msg = msgs[0]
        if not target_msg: return None
        
        sender_id = target_msg.sender_id
        if sender_id:
            cs_ids = [MY_ID] + OTHER_CS_IDS
            if sender_id not in cs_ids:
                update_msg_cache(chat_id, reply_to_msg_id, sender_id, target_msg.grouped_id)

            return sender_id
        return None
    except Exception: return None

async def get_context_users(chat_id, msg_id):
    users = set()
    try:
        msgs = await client.get_messages(chat_id, ids=[msg_id])
        if not msgs or not msgs[0]: return []
        msg = msgs[0]
        
        if msg.sender_id: 
            users.add(msg.sender_id)
            if msg.sender_id not in ([MY_ID] + OTHER_CS_IDS):
                update_msg_cache(chat_id, msg_id, msg.sender_id, msg.grouped_id)
        
        if msg.reply_to_msg_id:
            parent_user_id = await get_traceable_sender(chat_id, msg.reply_to_msg_id)
            if parent_user_id:
                users.add(parent_user_id)
                
    except Exception as e:
        log_tree(9, f"上下文获取失败: {e}")
        
    cs_ids = [MY_ID] + OTHER_CS_IDS
    return [u for u in users if u not in cs_ids]

async def get_replied_message_info(chat_id, reply_to_msg_id):
    if not reply_to_msg_id:
        return None, None, None
    try:
        msgs = await client.get_messages(chat_id, ids=[reply_to_msg_id])
        if not msgs or not msgs[0]:
            return None, None, None
        replied_msg = msgs[0]
        target_id = replied_msg.sender_id
        target_name = "未知客服"
        sender_obj = await replied_msg.get_sender()
        if sender_obj:
            target_name = getattr(sender_obj, 'first_name', 'Unknown')
        return replied_msg, target_id, target_name
    except Exception as e:
        log_tree(9, f"获取引用消息失败 Msg={reply_to_msg_id}: {e}")
        return None, None, None

def is_message_edited_event(event):
    return isinstance(event, events.MessageEdited.Event)

def is_new_message_event(event):
    return isinstance(event, events.NewMessage.Event)

def infer_sender_role(sender_id, sender_name):
    try:
        if sender_id == MY_ID or sender_id in OTHER_CS_IDS:
            return "cs"
    except Exception:
        pass
    if sender_name:
        for prefix in CS_NAME_PREFIXES:
            if sender_name.startswith(prefix):
                return "cs"
    return "user"

async def record_telegram_message_event(event, sender_name=None, msg_type=None, text=None):
    chat_id = event.chat_id
    if not chat_id or not is_configured_cs_group(chat_id):
        return
    sender_name = sender_name or "Unknown"
    if sender_name == "Unknown":
        try:
            sender = await event.get_sender()
            sender_name = getattr(sender, 'first_name', None) or getattr(sender, 'title', None) or "Unknown"
        except Exception:
            pass
    if text is None:
        text = event.text or ""
    if msg_type is None:
        msg_type = "文本"
        if event.message.file:
            msg_type = "文件/图片"
            if not text:
                text = "[媒体文件]"
        if event.message.sticker:
            msg_type = "贴纸"
            if not text:
                text = "[贴纸]"

    old_info = msg_content_cache.get((chat_id, event.id), {})
    old_text = old_info.get("text", "")
    event_type = "edit" if is_message_edited_event(event) else "new"
    if event_type == "edit" and not old_text:
        old_text = get_last_stored_message_text(chat_id, event.id)
    event_ts = event.date.timestamp()
    if is_message_edited_event(event) and getattr(event.message, "edit_date", None):
        event_ts = event.message.edit_date.timestamp()
    sender_role = infer_sender_role(event.sender_id, sender_name)
    reply_to_msg_id = get_primary_reply_target_id(event.message)
    if event_type == "new":
        upsert_message_snapshot(
            chat_id,
            event.id,
            sender_id=event.sender_id,
            sender_name=sender_name,
            text=text,
            msg_type=msg_type,
            ts=event_ts,
            reply_to_msg_id=reply_to_msg_id,
            grouped_id=event.message.grouped_id,
            sender_role=sender_role,
        )
        return

    if not _is_meaningful_edit_text(old_text, text):
        upsert_message_snapshot(
            chat_id,
            event.id,
            sender_id=event.sender_id,
            sender_name=sender_name,
            text=text,
            msg_type=msg_type,
            ts=event_ts,
            reply_to_msg_id=reply_to_msg_id,
            grouped_id=event.message.grouped_id,
            sender_role=sender_role,
            broadcast=False,
        )
        update_content_cache(chat_id, event.id, sender_name, text)
        return

    record_chat_event(
        "edit",
        chat_id,
        event.id,
        sender_id=event.sender_id,
        sender_name=sender_name,
        text=text,
        old_text=old_text,
        msg_type=msg_type,
        ts=event_ts,
        reply_to_msg_id=reply_to_msg_id,
        grouped_id=event.message.grouped_id,
        sender_role=sender_role,
    )
    upsert_message_snapshot(
        chat_id,
        event.id,
        sender_id=event.sender_id,
        sender_name=sender_name,
        text=text,
        msg_type=msg_type,
        ts=event_ts,
        reply_to_msg_id=reply_to_msg_id,
        grouped_id=event.message.grouped_id,
        sender_role=sender_role,
        broadcast=False,
    )

async def backfill_chat_history():
    if CHAT_HISTORY_BACKFILL_LIMIT <= 0:
        return
    try:
        total = 0
        for chat_id in CS_GROUP_IDS:
            try:
                entity = await client.get_entity(chat_id)
                entity_id = getattr(entity, 'id', chat_id)
                entity_title = getattr(entity, 'title', str(chat_id))
                _group_name_cache[entity_id] = entity_title
                if isinstance(entity_id, int) and entity_id > 0:
                    _group_name_cache[int(f"-100{entity_id}")] = entity_title
                async for msg in client.iter_messages(entity, limit=CHAT_HISTORY_BACKFILL_LIMIT):
                    if not msg or getattr(msg, "action", None):
                        continue
                    text = msg.text or ""
                    msg_type = "文本"
                    if msg.file:
                        msg_type = "文件/图片"
                        if not text:
                            text = "[媒体文件]"
                    if msg.sticker:
                        msg_type = "贴纸"
                        if not text:
                            text = "[贴纸]"
                    sender_name = "Unknown"
                    try:
                        sender = await msg.get_sender()
                        sender_name = getattr(sender, 'first_name', None) or getattr(sender, 'title', None) or "Unknown"
                    except Exception:
                        pass
                    chat_event_id = msg.chat_id or getattr(entity, 'id', chat_id)
                    if isinstance(chat_event_id, int) and chat_event_id > 0:
                        chat_event_id = int(f"-100{chat_event_id}")
                    row = upsert_message_snapshot(
                        chat_event_id,
                        msg.id,
                        sender_id=msg.sender_id,
                        sender_name=sender_name,
                        text=text,
                        msg_type=msg_type,
                        ts=msg.date.timestamp(),
                        reply_to_msg_id=get_primary_reply_target_id(msg),
                        grouped_id=msg.grouped_id,
                        sender_role=infer_sender_role(msg.sender_id, sender_name),
                        broadcast=False
                    )
                    if row:
                        total += 1
            except Exception as e:
                log_tree(9, f"历史消息回补失败 Chat={chat_id}: {e}")
        if total:
            log_tree(1, f"📥 历史消息回补完成: 新增 {total} 条")
    except Exception as e:
        log_tree(9, f"历史消息回补任务失败: {e}")

@client.on(events.NewMessage(chats=list(CONFIGURED_CS_GROUP_IDS)))
@client.on(events.MessageEdited(chats=list(CONFIGURED_CS_GROUP_IDS)))
async def handler(event):
    try:
        global MY_ID
        if not MY_ID: MY_ID = (await client.get_me()).id
        chat_id = event.chat_id
        if not is_configured_cs_group(chat_id):
            if IS_WORKING:
                log_tree(1, f"🛡️ 忽略非CS_GROUP_IDS群组消息 | Chat={chat_id} | Msg={event.id}")
            return

        # 过滤服务消息
        if event.message.action:
            return

        msg_timestamp = event.date.timestamp()
        msg_time_str = event.date.astimezone(timezone(timedelta(hours=8))).strftime('%H:%M:%S')

        text = event.text or ""
        msg_type = "文本"
        if event.message.file:
            msg_type = "文件/图片"
            if not text: text = "[媒体文件]"
        if event.message.sticker:
            msg_type = "贴纸"
            if not text: text = "[贴纸]"

        sender_id = event.sender_id
        sender = await event.get_sender()
        sender_name = getattr(sender, 'first_name', 'Unknown')
        await record_telegram_message_event(event, sender_name=sender_name, msg_type=msg_type, text=text)
        update_content_cache(chat_id, event.id, sender_name, text)

        # 监听暂停时仍记录消息到数据库，然后直接返回
        if not IS_WORKING:
            if is_new_message_event(event):
                log_tree(0, f"Msg={event.id} [T={msg_time_str}] | User={event.sender_id} | [{chat_id}] {text[:200].replace(chr(10),' ')} [{msg_type}][暂停]")
                if chat_id not in _group_name_cache:
                    try:
                        _g = await client.get_entity(chat_id)
                        _group_name_cache[chat_id] = _g.title
                    except Exception:
                        _group_name_cache[chat_id] = str(chat_id)
            return

        reply_to_msg_id = get_primary_reply_target_id(event.message)
        grouped_id = event.message.grouped_id
        msg_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{event.id}"

        alert_wait_signatures = get_wait_alert_signatures()
        matched_wait_keyword = match_signature(text, WAIT_SIGNATURES)
        matched_alert_wait_keyword = match_signature(text, alert_wait_signatures)
        is_wait_cmd = matched_wait_keyword is not None
        is_wait_alert_cmd = matched_alert_wait_keyword is not None
        is_keep_cmd = text.strip() in KEEP_SIGNATURES
        
        is_name_cs = False
        if sender_name:
             for prefix in CS_NAME_PREFIXES:
                 if sender_name.startswith(prefix): is_name_cs = True; break
        
        is_sender_cs = (sender_id == MY_ID) or (sender_id in OTHER_CS_IDS) or is_name_cs

        current_thread_id, thread_type = get_thread_context(event)

        real_customer_id = None
        if reply_to_msg_id:
            if (chat_id, reply_to_msg_id) in msg_to_user_cache:
                real_customer_id = msg_to_user_cache[(chat_id, reply_to_msg_id)]
            
            if not real_customer_id and reply_to_msg_id in wait_msg_map:
                wait_origin_msg = wait_msg_map[reply_to_msg_id]
                for (cid, uid), msg_set in chat_user_active_msgs.items():
                    if cid == chat_id and wait_origin_msg in msg_set:
                        real_customer_id = uid
                        break
            
            if not real_customer_id:
                real_customer_id = await get_traceable_sender(chat_id, reply_to_msg_id)

        if is_sender_cs:
            record_cs_activity(chat_id, user_id=real_customer_id, thread_id=current_thread_id, timestamp=msg_timestamp)
            
            if is_message_edited_event(event):
                 if real_customer_id or current_thread_id:
                     cancel_tasks(chat_id, real_customer_id, current_thread_id, reason="客服编辑: [已隐藏]")
                 try:
                     is_latest = True
                     latest_found_id = event.id
                     if current_thread_id:
                         async for m in client.iter_messages(chat_id, limit=30):
                             is_in_thread = False
                             if m.reply_to:
                                 if m.reply_to.reply_to_top_id == current_thread_id: is_in_thread = True
                                 if m.reply_to.reply_to_msg_id == current_thread_id: is_in_thread = True
                             
                             if is_in_thread:
                                 if m.id > event.id:
                                     is_latest = False
                                     latest_found_id = m.id
                                     txt = m.text or ""
                                     if not any(k in txt for k in WAIT_SIGNATURES):
                                         log_tree(1, f"🛡️ 编辑拦截 | Msg={event.id} 被新消息 Msg={m.id} 覆盖 (内容非稍等) -> 忽略")
                                         return 
                                     else:
                                         log_tree(1, f"⚠️ 编辑放行 | Msg={event.id} 虽非最新 (Top={m.id}) 但Top仍为稍等")
                                 break 
                         else:
                             latest_batch = await client.get_messages(chat_id, limit=1)
                             if latest_batch:
                                 m = latest_batch[0]
                                 if m.id > event.id:
                                     txt = m.text or ""
                                     if not any(k in txt for k in WAIT_SIGNATURES):
                                         log_tree(1, f"🛡️ 编辑拦截(主群) | Msg={event.id} 被新消息 Msg={m.id} 覆盖 -> 忽略")
                                         return

                 except Exception as e:
                     log_tree(9, f"❌ 编辑检测失败: {e}")

            if reply_to_msg_id:
                source_info = "未知"
                if (chat_id, reply_to_msg_id) in msg_to_user_cache: source_info = "缓存命中"
                elif real_customer_id: source_info = "API实时查询"
                else: source_info = "追踪失败" 
                
                pass

            cancel_types = None 
            if is_wait_cmd or is_keep_cmd:
                cancel_types = ['reply', 'self_reply']

            if real_customer_id or current_thread_id:
                cancel_tasks(chat_id, real_customer_id, 
                             thread_id=current_thread_id, 
                             target_msg_id=reply_to_msg_id, 
                             reason="客服回复: [已隐藏]", 
                             types=cancel_types)
            
            for related_msg_id in get_related_album_msg_ids(chat_id, reply_to_msg_id):
                if related_msg_id in reply_tasks:
                    reply_tasks[related_msg_id].cancel()
                if related_msg_id in self_reply_tasks:
                    self_reply_tasks[related_msg_id].cancel()

            if reply_to_msg_id:
                related_users = await get_context_users(chat_id, reply_to_msg_id)
                if not related_users and real_customer_id:
                    related_users = [real_customer_id]

                if related_users:
                    if is_keep_cmd:
                        history_wait_keyword = await find_wait_keyword_in_history(chat_id, current_thread_id, signatures=get_wait_alert_signatures())
                        
                        if not history_wait_keyword:
                             log_tree(1, f"🛡️ 豁免 [跟进] | Msg={event.id} | 原因: 历史流无已选择的[稍等]预警关键词")
                        else:
                            if reply_to_msg_id in wait_tasks:
                                wait_tasks[reply_to_msg_id].cancel()
                                del wait_tasks[reply_to_msg_id]
                                if reply_to_msg_id in wait_timers: del wait_timers[reply_to_msg_id]
                                if reply_to_msg_id in wait_task_keywords: del wait_task_keywords[reply_to_msg_id]
                                remove_map_entries_by_value(wait_msg_map, reply_to_msg_id)
                                log_tree(1, f"🔄 [跟进] 覆盖并销毁 [稍等] | Msg={reply_to_msg_id}")

                            if reply_to_msg_id in followup_tasks:
                                followup_tasks[reply_to_msg_id].cancel()
                                del followup_tasks[reply_to_msg_id]
                                if reply_to_msg_id in followup_timers: del followup_timers[reply_to_msg_id]
                                remove_map_entries_by_value(followup_msg_map, reply_to_msg_id)

                            task = asyncio.create_task(task_followup_timeout(
                                reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users, 
                                trigger_timestamp=msg_timestamp,
                                thread_id=current_thread_id,
                                wait_keyword=history_wait_keyword
                            ))
                            followup_tasks[reply_to_msg_id] = task
                            followup_msg_map[event.id] = reply_to_msg_id

                    elif is_wait_cmd:
                        if not is_wait_alert_cmd:
                            log_tree(1, f"🛡️ 豁免 [稍等预警] | Msg={event.id} | 客服={sender_name} | 关键词={matched_wait_keyword} | 原因: 未选入 ALERT_WAIT_KEYWORDS")
                        else:
                            if reply_to_msg_id in followup_tasks:
                                followup_tasks[reply_to_msg_id].cancel()
                                del followup_tasks[reply_to_msg_id]
                                if reply_to_msg_id in followup_timers: del followup_timers[reply_to_msg_id]
                                remove_map_entries_by_value(followup_msg_map, reply_to_msg_id)
                                log_tree(1, f"🔄 [稍等] 覆盖并销毁 [跟进] | Msg={reply_to_msg_id}")

                            if reply_to_msg_id in wait_tasks:
                                wait_tasks[reply_to_msg_id].cancel()
                                del wait_tasks[reply_to_msg_id]
                                if reply_to_msg_id in wait_timers: del wait_timers[reply_to_msg_id]
                                if reply_to_msg_id in wait_task_keywords: del wait_task_keywords[reply_to_msg_id]
                                remove_map_entries_by_value(wait_msg_map, reply_to_msg_id)

                            task = asyncio.create_task(task_wait_timeout(
                                reply_to_msg_id, sender_name, text[:50], msg_link, event.id, chat_id, related_users,
                                trigger_timestamp=msg_timestamp,
                                thread_id=current_thread_id,
                                wait_keyword=matched_alert_wait_keyword
                            ))
                            wait_tasks[reply_to_msg_id] = task
                            wait_task_keywords[reply_to_msg_id] = matched_alert_wait_keyword
                            wait_msg_map[event.id] = reply_to_msg_id

        else:
            if is_message_edited_event(event):
                return

            update_msg_cache(chat_id, event.id, sender_id, grouped_id)
            cancel_tasks(chat_id, sender_id, current_thread_id, reason="客户发言: [已隐藏]", types=['reply'])
            
            log_tree(0, f"Msg={event.id} [T={msg_time_str}] | User={sender_id} | [{chat_id}] {sender_name}: {text} [{msg_type}]")
            if chat_id not in _group_name_cache:
                try:
                    _g = await client.get_entity(chat_id)
                    _group_name_cache[chat_id] = _g.title
                except Exception:
                    _group_name_cache[chat_id] = str(chat_id)

            if reply_to_msg_id and real_customer_id:
                if sender_id == real_customer_id:
                     if normalize(text.strip()) not in IGNORE_SIGNATURES:
                         should_monitor = True
                         if grouped_id:
                             if grouped_id in self_reply_dedup:
                                 log_tree(1, f"🛡️ 豁免 [自回-图集去重] | GroupID={grouped_id}")
                                 should_monitor = False
                             else:
                                 self_reply_dedup.append(grouped_id)
                         
                         if should_monitor:
                             history_wait_keyword = await find_wait_keyword_in_history(chat_id, current_thread_id, signatures=get_wait_alert_signatures())
                             
                             if not history_wait_keyword:
                                 log_tree(1, f"🛡️ 豁免 [自回-无已选择稍等历史] | User={sender_id} | Msg={event.id}")
                             else:
                                 cancel_tasks(chat_id, sender_id, current_thread_id, reason="新自回覆盖旧自回", types=['self_reply'])
                                 register_task(chat_id, sender_id, event.id, current_thread_id)
                                 log_tree(1, f"🔥 侦测到自回行为 | User={sender_name} | Msg={event.id} -> {reply_to_msg_id}")
                                 
                                 task = asyncio.create_task(task_self_reply_timeout(
                                     event.id, sender_name, text[:50], msg_link, chat_id, sender_id, 
                                     trigger_timestamp=msg_timestamp,
                                     thread_id=current_thread_id,
                                     wait_keyword=history_wait_keyword
                                 ))
                                 
                                 def cleanup_self_reply(_):
                                     if event.id in self_reply_tasks: del self_reply_tasks[event.id]
                                     if event.id in self_reply_timers: del self_reply_timers[event.id]
                                     remove_task_record(chat_id, sender_id, event.id, current_thread_id)
                                     
                                 task.add_done_callback(cleanup_self_reply)
                                 self_reply_tasks[event.id] = task

            if reply_to_msg_id:
                try:
                    replied_msg, target_id, target_name = await get_replied_message_info(chat_id, reply_to_msg_id)

                    if (target_id == MY_ID) or (target_id in OTHER_CS_IDS):
                        if normalize(text.strip()) in IGNORE_SIGNATURES: return
                        history_wait_keyword = await find_wait_keyword_in_history(chat_id, current_thread_id, signatures=get_wait_alert_signatures())
                        if not history_wait_keyword:
                            log_tree(1, f"🛡️ 豁免 [漏回-无已选择稍等历史] | User={sender_id} | Msg={event.id} -> Target={target_name}")
                            return

                        reply_task_id = event.id
                        if grouped_id:
                            album_ids = get_cached_album_msg_ids(chat_id, event.id)
                            active_album_ids = [mid for mid in album_ids if mid in reply_tasks]
                            if active_album_ids:
                                reply_task_id = min(active_album_ids)

                        if reply_task_id in reply_tasks:
                            log_tree(1, f"🛡️ 豁免 [漏回-图集去重] | GroupID={grouped_id} | Msg={event.id} -> Active={reply_task_id}")
                            return

                        task = asyncio.create_task(task_reply_timeout(
                            reply_task_id, sender_name, text[:50], msg_link, chat_id, sender_id, target_name,
                            trigger_timestamp=msg_timestamp,
                            thread_id=current_thread_id,
                            wait_keyword=history_wait_keyword
                        ))
                        reply_tasks[reply_task_id] = task
                except Exception as e:
                    log_tree(9, f"❌ Reply Check Error: {e}")

    except Exception as e:
        log_tree(9, f"❌ Handler 异常: {e}")

if __name__ == '__main__':
    try:
        delay = int(os.environ.get("STARTUP_DELAY", 120))
        if delay > 0:
            logger.info(f"⏳ 启动延迟: 等待 {delay} 秒以确保旧连接断开...")
            time.sleep(delay)
            
        bot_loop = asyncio.get_event_loop()
        bot_loop.create_task(maintenance_task())
        bot_loop.create_task(bot_command_polling_task())
        
        if init_stats_blueprint:
            init_stats_blueprint(app, client, bot_loop, CS_GROUP_IDS)
        
        if init_monitor:
            init_monitor(client, app, OTHER_CS_IDS, CS_NAME_PREFIXES, handler)
            
        Thread(target=run_web).start()
        setup_bot_menu_button()
        setup_bot_commands()
        log_tree(0, "✅ 系统启动 (Ver 45.22 Final Consolidated)")
        client.start()
        if not MY_ID:
            MY_ID = client.loop.run_until_complete(client.get_me()).id
        bot_loop.create_task(backfill_chat_history())
        client.run_until_disconnected()
    except AuthKeyDuplicatedError:
        logger.critical("🚨 严重错误: SESSION_STRING 已失效！检测到多地登录冲突。")
        sys.exit(1)
    except Exception as e:
        log_tree(9, f"❌ 启动失败: {e}")
