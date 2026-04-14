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

# ================= 工具函数 =================

def extract_id_list(env_str):
    """
    安全提取 ID 列表，处理带中文备注的情况（如 5787870260（四号））
    逻辑同步自 main.py
    """
    if not env_str: return []
    clean_str = env_str.replace("，", ",") 
    items = clean_str.split(',')
    result = []
    for item in items:
        # 使用正则匹配数字（支持负号 ID）
        match = re.search(r'-?\d+', item)
        if match:
            try:
                result.append(int(match.group()))
            except:
                pass
    return result

# 解析客服 ID 列表
OTHER_CS_IDS = extract_id_list(os.environ.get("OTHER_CS_IDS", ""))

# ================= 核心逻辑 =================

async def run_export():
    client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
    await client.connect()
    
    # 获取当前登录账号的 ID
    me = await client.get_me()
    # 合并所有被视为客服的 ID 集合
    cs_ids = set(OTHER_CS_IDS + [me.id])
    
    # 计算 30 天前的时间
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    qa_pairs = []

    print(f"🚀 开始从 {len(ALL_TARGET_GROUPS)} 个群组中提取近 30 天的问答对...")

    for chat_id in ALL_TARGET_GROUPS:
        try:
            entity = await client.get_entity(chat_id)
            group_name = getattr(entity, 'title', str(chat_id))
            print(f"📂 正在扫描群组: {group_name}")
            
            # 搜索包含“稍等”关键词的消息
            async for msg in client.iter_messages(chat_id, offset_date=cutoff, reverse=True, search=TRIGGER_KW):
                # 规则：必须是客服 ID 发出的，且必须是回复了某条消息
                if msg.sender_id in cs_ids and msg.is_reply:
                    # A. 提取“问题”：获取该指令回复的那条原始消息内容
                    question_msg = await msg.get_reply_message()
                    if not question_msg or not question_msg.text:
                        continue
                    
                    question_text = question_msg.text.strip()
                    
                    # B. 提取“答复”：在该指令之后，寻找同一客服发出的第一条实质性回复
                    answer_text = None
                    async for follow_up in client.iter_messages(chat_id, min_id=msg.id, limit=20):
                        if follow_up.sender_id == msg.sender_id and follow_up.text:
                            # 过滤掉再次发送的快捷键或长度太短的内容
                            if TRIGGER_KW not in follow_up.text and len(follow_up.text) > 5:
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
                                "cs_id": msg.sender_id
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
        print("\n⚠️ 扫描结束，未找到符合条件的问答数据。")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(run_export())
