import asyncio
import os
import time
from threading import Lock, Thread

from telethon import TelegramClient
from telethon.sessions import StringSession


class TelegramLoginManager:
    def __init__(self, ttl_seconds=10 * 60):
        self.ttl_seconds = ttl_seconds
        self.loop = None
        self.sessions = {}
        self.lock = Lock()

    def ensure_loop(self):
        if self.loop and self.loop.is_running():
            return self.loop
        loop = asyncio.new_event_loop()
        thread = Thread(target=loop.run_forever, name="telegram-login-loop", daemon=True)
        thread.start()
        self.loop = loop
        return loop

    def run_coro(self, coro, timeout=60):
        loop = self.ensure_loop()
        return asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=timeout)

    def build_client(self, api_id, api_hash):
        return TelegramClient(
            StringSession(),
            api_id,
            api_hash,
            device_model=os.environ.get("TG_DEVICE_MODEL", "VMware20,1"),
            app_version=os.environ.get("TG_APP_VERSION", "6.6.3 x64"),
            system_version=os.environ.get("TG_SYSTEM_VERSION", "Windows 10 x64"),
            lang_code=os.environ.get("TG_LANG_CODE", "zh-hans"),
            system_lang_code=os.environ.get("TG_SYSTEM_LANG_CODE", "zh-hans"),
        )

    def cleanup_locked(self):
        now_ts = time.time()
        expired = [
            flow_id for flow_id, item in self.sessions.items()
            if not isinstance(item, dict) or item.get("expires_at", 0) <= now_ts
        ]
        for flow_id in expired:
            item = self.sessions.pop(flow_id, None)
            client_obj = item.get("client") if isinstance(item, dict) else None
            if client_obj:
                try:
                    self.run_coro(client_obj.disconnect(), timeout=10)
                except Exception:
                    pass

    def require_token(self, data):
        expected = os.environ.get("TELEGRAM_LOGIN_TOKEN") or os.environ.get("WEB_LOGIN_TOKEN") or ""
        if not expected:
            raise PermissionError("未配置 TELEGRAM_LOGIN_TOKEN，网页登录和账号管理已锁定")
        if str(data.get("token") or "") != expected:
            raise PermissionError("访问口令不正确")

    def parse_api(self, data, default_api_id, default_api_hash):
        api_id = int(str(data.get("api_id") or default_api_id or "").strip())
        api_hash = str(data.get("api_hash") or default_api_hash or "").strip()
        if api_id <= 0 or not api_hash:
            raise ValueError("API_ID/API_HASH 不能为空")
        return api_id, api_hash

    async def send_code_async(self, api_id, api_hash, phone):
        client_obj = self.build_client(api_id, api_hash)
        await client_obj.connect()
        sent = await client_obj.send_code_request(phone)
        return client_obj, sent.phone_code_hash

    async def sign_in_async(self, flow, code):
        client_obj = flow["client"]
        await client_obj.sign_in(
            phone=flow["phone"],
            code=code,
            phone_code_hash=flow["phone_code_hash"],
        )
        return client_obj.session.save()

    async def password_async(self, flow, password):
        client_obj = flow["client"]
        await client_obj.sign_in(password=password)
        return client_obj.session.save()

    def create_flow(self, flow_id, client_obj, phone, phone_code_hash, account_type, account_name):
        with self.lock:
            self.sessions[flow_id] = {
                "client": client_obj,
                "phone": phone,
                "phone_code_hash": phone_code_hash,
                "account_type": account_type,
                "account_name": account_name,
                "expires_at": time.time() + self.ttl_seconds,
            }

    def get_flow(self, flow_id):
        with self.lock:
            self.cleanup_locked()
            flow = self.sessions.get(flow_id)
            if flow:
                flow["expires_at"] = time.time() + self.ttl_seconds
            return flow

    def pop_flow(self, flow_id):
        with self.lock:
            return self.sessions.pop(flow_id, None)
