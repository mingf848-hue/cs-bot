import json
import os


MAIN_SESSION_REDIS_KEY = "telegram_main_session_string_v1"
EXTRA_SESSIONS_REDIS_KEY = "telegram_extra_session_strings_v1"


def parse_extra_session_items(raw):
    sessions = {}
    for i, item in enumerate([x.strip() for x in str(raw or "").split(";") if x.strip()]):
        if "=" in item:
            left, right = item.split("=", 1)
            if len(left) > 30:
                name = f"副账号 {i + 1}"
                session_value = item
            else:
                name = left.strip() or f"副账号 {i + 1}"
                session_value = right.strip()
        else:
            name = f"副账号 {i + 1}"
            session_value = item
        if session_value:
            sessions[name] = session_value
    return sessions


def format_extra_session_items(sessions):
    return ";".join(
        f"{name}={session_value}"
        for name, session_value in (sessions or {}).items()
        if str(name or "").strip() and str(session_value or "").strip()
    )


class SessionStore:
    def __init__(self, redis_client_getter, main_session_file, extra_sessions_file, logger):
        self.redis_client_getter = redis_client_getter
        self.main_session_file = main_session_file
        self.extra_sessions_file = extra_sessions_file
        self.logger = logger

    def _redis_client(self):
        return self.redis_client_getter()

    def load_main_session(self):
        client = self._redis_client()
        if client:
            try:
                raw = client.get(MAIN_SESSION_REDIS_KEY)
                if raw:
                    value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    value = value.strip()
                    if value:
                        return value, "Redis"
            except Exception as e:
                self.logger.warning(f"⚠️ [Session] Redis 读取失败，尝试文件/环境变量: {e}")

        try:
            if os.path.exists(self.main_session_file):
                with open(self.main_session_file, "r", encoding="utf-8") as f:
                    value = f.read().strip()
                if value:
                    return value, "文件"
        except Exception as e:
            self.logger.warning(f"⚠️ [Session] 文件读取失败，尝试环境变量: {e}")

        return (os.environ.get("SESSION_STRING", "") or "").strip(), "环境变量"

    def load_extra_sessions(self):
        client = self._redis_client()
        if client:
            try:
                raw = client.get(EXTRA_SESSIONS_REDIS_KEY)
                if raw:
                    value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                    data = json.loads(value)
                    if isinstance(data, dict):
                        sessions = clean_session_mapping(data)
                        if sessions:
                            return sessions, "Redis"
            except Exception as e:
                self.logger.warning(f"⚠️ [Session] Redis 副账号读取失败，尝试文件/环境变量: {e}")

        try:
            if os.path.exists(self.extra_sessions_file):
                with open(self.extra_sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    sessions = clean_session_mapping(data)
                    if sessions:
                        return sessions, "文件"
        except Exception as e:
            self.logger.warning(f"⚠️ [Session] 文件副账号读取失败，尝试环境变量: {e}")

        return {}, "环境变量"

    def save_main_session(self, session_string):
        value = str(session_string or "").strip()
        if not value:
            raise ValueError("SESSION_STRING 为空，未保存")

        saved_targets = []
        client = self._redis_client()
        if client:
            try:
                client.set(MAIN_SESSION_REDIS_KEY, value)
                saved_targets.append("Redis")
            except Exception as e:
                self.logger.warning(f"⚠️ [Session] Redis 保存失败，继续尝试文件: {e}")

        try:
            session_dir = os.path.dirname(self.main_session_file)
            if session_dir:
                os.makedirs(session_dir, exist_ok=True)
            with open(self.main_session_file, "w", encoding="utf-8") as f:
                f.write(value)
            try:
                os.chmod(self.main_session_file, 0o600)
            except Exception:
                pass
            saved_targets.append("文件")
        except Exception as e:
            self.logger.warning(f"⚠️ [Session] 文件保存失败: {e}")

        if not saved_targets:
            raise RuntimeError("SESSION_STRING 保存失败：Redis 和文件都不可用")
        return saved_targets

    def save_extra_sessions(self, sessions):
        clean_sessions = clean_session_mapping(sessions)
        saved_targets = []
        payload = json.dumps(clean_sessions, ensure_ascii=False)
        client = self._redis_client()
        if client:
            try:
                client.set(EXTRA_SESSIONS_REDIS_KEY, payload)
                saved_targets.append("Redis")
            except Exception as e:
                self.logger.warning(f"⚠️ [Session] Redis 副账号保存失败，继续尝试文件: {e}")

        try:
            session_dir = os.path.dirname(self.extra_sessions_file)
            if session_dir:
                os.makedirs(session_dir, exist_ok=True)
            with open(self.extra_sessions_file, "w", encoding="utf-8") as f:
                f.write(payload)
            try:
                os.chmod(self.extra_sessions_file, 0o600)
            except Exception:
                pass
            saved_targets.append("文件")
        except Exception as e:
            self.logger.warning(f"⚠️ [Session] 文件副账号保存失败: {e}")

        if not saved_targets:
            raise RuntimeError("副账号 SESSION_STRING 保存失败：Redis 和文件都不可用")
        return saved_targets


def clean_session_mapping(sessions):
    return {
        str(name).strip(): str(session_value).strip()
        for name, session_value in (sessions or {}).items()
        if str(name).strip() and str(session_value).strip()
    }
