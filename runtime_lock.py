import asyncio
import os
import time


class TelegramRuntimeLock:
    def __init__(
        self,
        redis_client_getter,
        logger,
        lock_key,
        owner,
        ttl_seconds=180,
        required=False,
        disconnect_callback=None,
    ):
        self.redis_client_getter = redis_client_getter
        self.logger = logger
        self.lock_key = lock_key
        self.owner = owner
        self.ttl_seconds = ttl_seconds
        self.refresh_seconds = max(15, min(60, ttl_seconds // 3))
        self.required = required
        self.disconnect_callback = disconnect_callback
        self.acquired = False
        self.stop = False

    def _client(self):
        try:
            return self.redis_client_getter()
        except Exception as e:
            self.logger.warning(f"⚠️ [RuntimeLock] Redis 获取失败，无法启用跨实例 Telegram 锁: {e}")
            return None

    def _is_owner_value(self, value):
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        return str(value or "") == self.owner

    def acquire(self):
        client = self._client()
        if not client:
            if self.required:
                self.logger.critical("🚨 [RuntimeLock] 已启用强制运行锁，但 Redis 不可用；本实例不会连接 Telegram")
                self.acquired = False
                return False
            self.logger.warning("⚠️ [RuntimeLock] 未配置 Redis，无法阻止多实例同时连接 Telegram；请确保 Zeabur 只运行 1 个实例")
            self.acquired = False
            return True

        wait_logged = False
        while True:
            try:
                if client.set(self.lock_key, self.owner, nx=True, ex=self.ttl_seconds):
                    self.acquired = True
                    self.logger.info(f"🔒 [RuntimeLock] 已取得 Telegram 运行锁，TTL={self.ttl_seconds}s")
                    return True
                if self._is_owner_value(client.get(self.lock_key)):
                    client.expire(self.lock_key, self.ttl_seconds)
                    self.acquired = True
                    return True
                if not wait_logged:
                    self.logger.warning("⏳ [RuntimeLock] 其它实例仍持有 Telegram 运行锁，本实例暂不连接 Telegram，等待接管...")
                    wait_logged = True
            except Exception as e:
                self.logger.warning(f"⚠️ [RuntimeLock] 锁检查异常，继续等待以避免挤号: {e}")
            time.sleep(10)

    def refresh(self):
        client = self._client()
        if not client or not self.acquired:
            return False
        try:
            if self._is_owner_value(client.get(self.lock_key)):
                client.expire(self.lock_key, self.ttl_seconds)
                return True
        except Exception as e:
            self.logger.warning(f"⚠️ [RuntimeLock] Telegram 运行锁续期失败: {e}")
        return False

    def release(self):
        client = self._client()
        if not client or not self.acquired:
            return
        try:
            script = """
            if redis.call('get', KEYS[1]) == ARGV[1] then
                return redis.call('del', KEYS[1])
            end
            return 0
            """
            client.eval(script, 1, self.lock_key, self.owner)
            self.logger.info("🔓 [RuntimeLock] 已释放 Telegram 运行锁")
        except Exception as e:
            self.logger.warning(f"⚠️ [RuntimeLock] 释放 Telegram 运行锁失败: {e}")
        finally:
            self.acquired = False

    def request_stop(self):
        self.stop = True

    def start_heartbeat(self, loop):
        async def heartbeat():
            while not self.stop:
                await asyncio.sleep(self.refresh_seconds)
                if self.stop:
                    break
                if self.acquired and not self.refresh():
                    self.logger.critical("🚨 [RuntimeLock] Telegram 运行锁续期失败或已被接管，为避免多实例挤号，正在退出进程")
                    if self.disconnect_callback:
                        try:
                            result = self.disconnect_callback()
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception:
                            pass
                    os._exit(1)

        if self.acquired:
            loop.create_task(heartbeat())
