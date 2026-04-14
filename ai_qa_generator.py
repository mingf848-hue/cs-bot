import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession

# ================= 配置区 =================

# 1. 尝试从你的工作量统计模块获取目标群组
try:
    from work_stats import ALL_TARGET_GROUPS
except ImportError:
    # 如果导入失败，手动填入你代码中的群组ID列表
    ALL_TARGET_GROUPS = [
        -1001885279888, -1001800838000, -1001703213989, -1001972746703, -1001871198775, -1002957057436,
        -1002169616907, -1002053064967, -1002728905038, -1002154594658, -1002004030172,
        -1002174533164, -1001978088089, -1001931146238, -1001911814916, -1001571955528,
        -1001587586041, -1002807120955, -1001942935698, -1001658527193, -1003511979135
    ]

# 2. 从 Zeabur 环境变量加载配置
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")

# 触发关键词
TRIGGER_KW = "请稍等ART"
# 导出文件名
OUTPUT_FILE = f"ai_training_data_{datetime.now().strftime('%m%d_%H%M')}.json"

# ================= 核心逻辑 =================

async def run_export():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    # 计算 30 天前的时间
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    qa_pairs = []

    print(f"🚀 开始从 {len(ALL_TARGET_GROUPS)} 个群组中提取近 30 天的问答对...")
    print(f"📢 当前模式：已解除客服 ID 限制，扫描所有包含 '{TRIGGER_KW}' 的回复。")

    for chat_id in ALL_TARGET_GROUPS:
        try:
            entity = await client.get_entity(chat_id)
            group_name = getattr(entity, 'title', str(chat_id))
            print(f"📂 正在扫描群组: {group_name}")
            
            # 搜索包含关键词的消息
            async for msg in client.iter_messages(chat_id, offset_date=cutoff, reverse=True, search=TRIGGER_KW):
                # 规则：只要消息包含关键词且是“引用回复”，就尝试溯源
                if msg.is_reply:
                    # A. 提取“问题”：获取该指令回复的那条原始消息内容
                    question_msg = await msg.get_reply_message()
                    if not question_msg or not question_msg.text:
                        continue
                    
                    question_text = question_msg.text.strip()
                    
                    # B. 提取“答复”：在该指令之后，寻找【同一发送者】发出的第一条实质性回复
                    answer_text = None
                    # 在该指令后的 20 条消息内寻找
                    async for follow_up in client.iter_messages(chat_id, min_id=msg.id, limit=20):
                        if follow_up.sender_id == msg.sender_id and follow_up.text:
                            # 过滤掉再次发送的快捷键、空内容或太短的内容
                            if TRIGGER_KW not in follow_up.text and len(follow_up.text) > 3:
                                answer_text = follow_up.text.strip()
                                break
                    
                    if question_text and answer_text:
                        qa_pairs.append({
                            "instruction": "你是一个专业的在线客服，请根据客户问题给出详细准确的答复。",
                            "input": question_text,
                            "output": answer_text,
                            "metadata": {
                                "group": group_name,
                                "time": msg.date.strftime('%Y-%m-%d %H:%M'),
                                "sender_id": msg.sender_id
                            }
                        })
                        print(f" ✅ 成功匹配: [问] {question_text[:15]}... -> [答] {answer_text[:15]}...")

        except Exception as e:
            print(f" ❌ 扫描群组 {chat_id} 时出错: {e}")

    # 保存为 JSON 文件
    if qa_pairs:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(qa_pairs, f, ensure_ascii=False, indent=4)
        print(f"\n✨ 任务完成！共计导出 {len(qa_pairs)} 组问答对。")
        print(f"📁 文件已保存至: {OUTPUT_FILE}")
    else:
        print(f"\n⚠️ 扫描结束，未找到符合条件的问答数据。请确保：")
        print(f"  1. 群组中有包含 '{TRIGGER_KW}' 的消息。")
        print(f"  2. 这些消息是使用了 Telegram 的“回复(Reply)”功能发送的。")
        print(f"  3. 发送者在发送该指令后，有后续的正式答复内容。")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_export())
