import random
import re


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


def monitor_rule_account_name(rule, main_name):
    return str((rule or {}).get("reply_account") or main_name).strip() or main_name
