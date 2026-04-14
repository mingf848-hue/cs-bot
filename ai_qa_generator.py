import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession

# 1. 导入你 work_stats.py 中定义的群组分类
try:
    from work_stats import ALL_TARGET_GROUPS
except ImportError:
    # 备用：如果导入失败，手动填入你代码中的群组ID
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
OTHER_CS_IDS = [int(i) for i in os.environ.get("OTHER_CS_IDS", "").split(',') if i.strip()]

# 配置项
TRIGGER_KW = "请稍等ART"
OUTPUT_FILE = "ai_qa_training_data.json"

async def run_export():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    # 获取机器人/自己的ID
    me = await client.get_me()
    cs_ids = set(OTHER_CS_IDS + [me.id])
    
    # 计算一个月前的时间
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    qa_pairs = []

    print(f"🚀 开始从 {len(ALL_TARGET_GROUPS)} 个群组中提取问答...")

    for chat_id in ALL_TARGET_GROUPS:
        try:
            print(f"📂 正在扫描群组: {chat_id}")
            # 搜索包含“稍等”关键词的消息
            async for msg in client.iter_messages(chat_id, offset_date=cutoff, reverse=True, search=TRIGGER_KW):
                # 必须是客服发送，且必须是回复了某条消息
                if msg.sender_id in cs_ids and msg.is_reply:
                    # A. 提取问题：被回复的那条消息
                    question_msg = await msg.get_reply_message()
                    if not question_msg or not question_msg.text:
                        continue
                    
                    question_text = question_msg.text.strip()
                    
                    # B. 提取答复：在该“稍等”消息之后，同一客服发送的第一条长文本
                    answer_text = None
                    async for follow_up in client.iter_messages(chat_id, min_id=msg.id, limit=20):
                        if follow_up.sender_id == msg.sender_id and follow_up.text:
                            # 过滤掉其他的快捷键或重复的稍等
                            if TRIGGER_KW not in follow_up.text and len(follow_up.text) > 5:
                                answer_text = follow_up.text.strip()
                                break
                    
                    if question_text and answer_text:
                        qa_pairs.append({
                            "instruction": "你是一个专业的在线客服，请回答客户的问题。",
                            "input": question_text,
                            "output": answer_text,
                            "time": msg.date.strftime('%Y-%m-%d %H:%M')
                        })
                        print(f" ✅ 已提取: {question_text[:15]}... -> {answer_text[:15]}...")

        except Exception as e:
            print(f" ❌ 群组 {chat_id} 扫描失败: {e}")

    # 保存结果
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(qa_pairs, f, ensure_ascii=False, indent=4)
    
    print(f"\n✨ 导出完成！共计 {len(qa_pairs)} 组数据。文件已保存至: {OUTPUT_FILE}")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_export())
