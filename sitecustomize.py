"""Runtime safety patches for cs-bot.

Python imports ``sitecustomize`` automatically during startup.  We use it here to
patch monitor_responder without rewriting the large generated source file.
"""

import importlib.abc
import importlib.machinery
import os
import re
import sys


_PATCH_FLAG = "_scan_nearby_missed_wait_keyword_patch_applied"


def _split_signature_set(value):
    if not value:
        return set()
    text = str(value)
    for sep in ("，", "；", ";", "|", "\n", "\r"):
        text = text.replace(sep, ",")
    return {item.strip() for item in text.split(",") if item.strip()}


def _normalize_text(value):
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _wait_keywords():
    return _split_signature_set(os.environ.get("WAIT_KEYWORDS", ""))


def _text_matches_wait_keyword(text):
    raw = str(text or "").strip()
    if not raw:
        return False
    normalized = _normalize_text(raw)
    for keyword in sorted(_wait_keywords(), key=len, reverse=True):
        if not keyword:
            continue
        if keyword in raw:
            return True
        key_norm = _normalize_text(keyword)
        if key_norm and key_norm in normalized:
            return True
    return False


def _patch_monitor_responder(module):
    if getattr(module, _PATCH_FLAG, False):
        return

    logger = getattr(module, "logger", None)
    original_remember = getattr(module, "remember_unmatched_message", None)
    original_pop = getattr(module, "pop_pending_unmatched_before", None)

    if not original_remember or not original_pop:
        return

    def log_info(message):
        try:
            if logger:
                logger.info(message)
        except Exception:
            pass

    def sender_name_matches_cs_prefix(sender_name):
        sender_name = str(sender_name or "").strip()
        if not sender_name:
            return False
        prefixes = getattr(module, "system_cs_prefixes", []) or []
        normalizer = getattr(module, "normalize_prefix_text", None)
        if callable(normalizer):
            name_norm = normalizer(sender_name)
        else:
            name_norm = _normalize_text(sender_name)
        raw_lower = sender_name.lower()
        for prefix in prefixes:
            prefix_text = str(prefix or "").strip()
            if not prefix_text:
                continue
            prefix_lower = prefix_text.lower()
            if raw_lower.startswith(prefix_lower):
                return True
            if callable(normalizer):
                prefix_norm = normalizer(prefix_text)
            else:
                prefix_norm = _normalize_text(prefix_text)
            if prefix_norm and (name_norm.startswith(prefix_norm) or (len(prefix_norm) >= 4 and prefix_norm in name_norm)):
                return True
        return False

    def text_from_event_or_message(obj, item=None):
        if item and item.get("text"):
            return item.get("text") or ""
        message = getattr(obj, "message", obj)
        return (
            getattr(obj, "raw_text", None)
            or getattr(obj, "text", None)
            or getattr(message, "raw_text", None)
            or getattr(message, "text", None)
            or getattr(message, "message", "")
            or ""
        )

    def sender_id_from_event_or_message(obj, item=None):
        if item and item.get("sender_id"):
            return item.get("sender_id")
        message = getattr(obj, "message", obj)
        return getattr(obj, "sender_id", None) or getattr(message, "sender_id", None)

    def is_own_message(obj, item=None):
        message = getattr(obj, "message", obj)
        if bool(getattr(obj, "out", False)) or bool(getattr(message, "out", False)):
            return True
        sender_id = sender_id_from_event_or_message(obj, item=item)
        try:
            own_ids = set(getattr(module, "client_user_ids", {}).values())
        except Exception:
            own_ids = set()
        return bool(sender_id and sender_id in own_ids)

    def should_skip_unmatched(obj, sender_name="", item=None):
        text = text_from_event_or_message(obj, item=item)
        if is_own_message(obj, item=item):
            return "自己账号消息"
        if sender_name_matches_cs_prefix(sender_name or (item or {}).get("sender_name") or ""):
            return "客服账号消息"
        if _text_matches_wait_keyword(text):
            return "稍等关键词消息"
        return ""

    def patched_remember_unmatched_message(event, sender_name=""):
        reason = should_skip_unmatched(event, sender_name=sender_name)
        if reason:
            log_info(
                f"🛡️ [Unmatched] 跳过暂存{reason} Chat={getattr(event, 'chat_id', '-')} "
                f"Msg={getattr(event, 'id', '-')} Sender={sender_name or '-'}"
            )
            return
        return original_remember(event, sender_name)

    def patched_pop_pending_unmatched_before(chat_id, before_msg_id):
        candidates = original_pop(chat_id, before_msg_id)
        filtered = []
        for msg_id, item in candidates:
            message = (item or {}).get("message")
            reason = should_skip_unmatched(message, sender_name=(item or {}).get("sender_name", ""), item=item or {})
            if reason:
                log_info(f"🛡️ [Unmatched] 扫描补漏跳过{reason} Chat={chat_id} Msg={msg_id}")
                continue
            filtered.append((msg_id, item))
        return filtered

    async def patched_pending_message_has_reply(client, chat_id, msg_id, before_msg_id):
        try:
            kwargs = {"min_id": msg_id, "limit": 80}
            if before_msg_id:
                kwargs["max_id"] = before_msg_id
            history = await client.get_messages(chat_id, **kwargs)
        except Exception as exc:
            log_info(f"⚠️ [Unmatched] 检查回复失败，按已回复处理以防误补 Chat={chat_id} Msg={msg_id}: {exc}")
            return True

        get_reply_targets = getattr(module, "get_message_reply_target_ids", None)
        for msg in history:
            if getattr(msg, "id", None) == msg_id:
                continue
            try:
                if callable(get_reply_targets) and msg_id in get_reply_targets(msg):
                    return True
            except Exception:
                pass
            text = text_from_event_or_message(msg)
            # If a configured WAIT_KEYWORDS response already appears after the target,
            # treat the target as handled even when Telegram failed to preserve reply_to.
            if _text_matches_wait_keyword(text):
                return True
        return False

    module.remember_unmatched_message = patched_remember_unmatched_message
    module.pop_pending_unmatched_before = patched_pop_pending_unmatched_before
    module.pending_message_has_reply = patched_pending_message_has_reply
    setattr(module, _PATCH_FLAG, True)
    log_info("✅ [Unmatched] 已启用 WAIT_KEYWORDS 补漏扫描防误回补丁")


class _MonitorResponderPatchLoader(importlib.abc.Loader):
    def __init__(self, wrapped_loader):
        self.wrapped_loader = wrapped_loader

    def create_module(self, spec):
        if hasattr(self.wrapped_loader, "create_module"):
            return self.wrapped_loader.create_module(spec)
        return None

    def exec_module(self, module):
        self.wrapped_loader.exec_module(module)
        _patch_monitor_responder(module)


class _MonitorResponderPatchFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "monitor_responder":
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            if finder is importlib.machinery.PathFinder:
                spec = finder.find_spec(fullname, path)
            elif hasattr(finder, "find_spec"):
                spec = finder.find_spec(fullname, path, target)
            else:
                continue
            if spec and spec.loader:
                spec.loader = _MonitorResponderPatchLoader(spec.loader)
                return spec
        return None


if not any(isinstance(finder, _MonitorResponderPatchFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _MonitorResponderPatchFinder())
