import asyncio
import logging
from telethon import events

# 复用主程序的日志记录器
logger = logging.getLogger("BotLogger")

def init_monitor(client, other_cs_ids, cs_name_prefixes):
    """
    初始化监控功能
    :param client: Telethon 客户端实例
    :param other_cs_ids: 其他客服 ID 列表 (从 main.py 传入)
    :param cs_name_prefixes: 客服名字前缀列表 (从 main.py 传入)
    """
    # 目标监控群组
    TARGET_GROUP_ID = -1002169616907

    @client.on(events.NewMessage(chats=TARGET_GROUP_ID))
    async def keyword_monitor_handler(event):
        try:
            # 1. 排除客服 (自己 或 列表中的客服ID)
            # event.out=True 代表是机器人自己发的
            if event.out or (event.sender_id in other_cs_ids):
                return

            # 2. 排除回复消息 (需求：非消息流消息)
            # 如果 event.is_reply 为 True，说明这是一条回复消息，跳过
            if event.is_reply:
                return

            # 3. 检查关键字 "对比上时段缺少"
            text = event.text or ""
            if "对比上时段缺少" not in text:
                return

            # 4. 名字前缀检查 (二次确认非客服)
            # 获取发送者信息
            sender = await event.get_sender()
            name = getattr(sender, 'first_name', '') or ''
            # 如果名字以客服前缀开头，视为客服，跳过
            for prefix in cs_name_prefixes:
                if name.startswith(prefix):
                    return

            # --- ✅ 命中触发条件 ---
            logger.info(f"🔎 [Monitor] 捕获关键词 '对比上时段缺少' | User={event.sender_id} | Msg={event.id}")

            # 动作 1: 延迟 3 秒后，引用回复 "请稍等ART"
            await asyncio.sleep(3)
            await event.reply("请稍等ART")

            # 动作 2: 再延迟 2 秒后，引用同一条消息回复 "通道临时调整"
            await asyncio.sleep(2)
            await event.reply("通道临时调整")

        except Exception as e:
            logger.error(f"❌ [Monitor] 自动响应出错: {e}")
