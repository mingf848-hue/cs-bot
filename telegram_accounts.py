import os

from session_store import parse_extra_session_items


def get_runtime_account_statuses():
    try:
        import monitor_responder as _monitor_responder
        summaries = _monitor_responder.get_account_summaries()
    except Exception:
        summaries = []
    result = {}
    for item in summaries or []:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        result[name] = {
            "registered": True,
            "connected": bool(item.get("connected")),
            "user_id": item.get("user_id"),
            "role": item.get("role") or "",
            "monitor_enabled": item.get("monitor_enabled"),
        }
    return result


def runtime_status_payload(account_name, runtime_statuses):
    status = runtime_statuses.get(str(account_name or "").strip(), {})
    return {
        "registered": bool(status.get("registered")),
        "connected": bool(status.get("connected")),
        "user_id": status.get("user_id"),
        "role": status.get("role") or "",
        "monitor_enabled": status.get("monitor_enabled"),
    }


def has_any_session_config(main_session_ready, extra_sessions_raw):
    try:
        if main_session_ready:
            return True
        return bool(parse_extra_session_items(extra_sessions_raw))
    except Exception:
        return bool(main_session_ready)


def resolve_account_target(data):
    raw_key = str(data.get("account_key") or "main").strip()
    custom_name = str(data.get("account_name") or "").strip()
    if raw_key == "main":
        return "main", os.environ.get("MAIN_SESSION_NAME", "主账号")
    if raw_key == "extra1":
        return "extra", "副账号 1"
    if raw_key == "extra2":
        return "extra", "副账号 2"
    if raw_key == "extra3":
        return "extra", "副账号 3"
    if raw_key == "custom" and custom_name:
        return "extra", custom_name[:40]
    if raw_key.startswith("extra:"):
        name = raw_key.split(":", 1)[1].strip()
        if name:
            return "extra", name[:40]
    raise ValueError("请选择要保存的账号位置")
