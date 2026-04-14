import sqlite3
import json
from datetime import datetime, timedelta

# --- 配置与之前的代码保持一致 ---
DB_FILE = 'bot_data.db'
WORK_STATS_USERS = [
    6920629731, 6307374737, 5550293026, 6489704257, 
    6853601550, 5896338891, 6664917409, 6576595568, 
    6484379463, 6098044737, 6302061226, 5875958742, 7118359257
]

def manual_export():
    print("🔍 正在连接数据库并提取近30天问答对...")
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    
    # 计算30天前的时间
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d %H:%M:%S')
    
    # 1. 检查是否有新的 reply_to_text 字段，如果没有则尝试兼容旧数据
    try:
        cursor.execute("SELECT id, user_id, group_id, timestamp, message_text, reply_to_text FROM message_logs WHERE timestamp >= ?", (thirty_days_ago,))
    except sqlite3.OperationalError:
        # 如果还没升级数据库，则查旧字段
        cursor.execute("SELECT id, user_id, group_id, timestamp, message_text, NULL FROM message_logs WHERE timestamp >= ?", (thirty_days_ago,))

    logs = cursor.fetchall()
    qa_pairs = []
    
    print(f"统计：共找到 {len(logs)} 条员工发言记录。正在匹配问题...")

    for log_id, user_id, group_id, timestamp, answer_text, reply_text in logs:
        # 如果是监控的员工
        if user_id in WORK_STATS_USERS:
            question = None
            
            # 逻辑 A：如果有明确的回复记录（新代码产生的）
            if reply_text:
                question = reply_text
            else:
                # 逻辑 B：回溯旧数据。查找同一个群里，在这条回答之前最近的一条“非员工”消息
                cursor.execute('''
                    SELECT message_text FROM message_logs 
                    WHERE group_id = ? AND timestamp < ? AND user_id NOT IN ({})
                    ORDER BY timestamp DESC LIMIT 1
                '''.format(','.join(map(str, WORK_STATS_USERS))), (group_id, timestamp))
                
                prev_msg = cursor.fetchone()
                if prev_msg:
                    question = prev_msg[0]

            if question and answer_text:
                # 简单过滤：提问和回答不能太短，避免表情包等干扰
                if len(question) > 2 and len(answer_text) > 2:
                    qa_pairs.append({
                        "question": question.strip(),
                        "answer": answer_text.strip(),
                        "time": timestamp
                    })

    conn.close()

    # 导出为 JSON
    output_file = f"manual_qa_export_{datetime.now().strftime('%m%d_%H%M')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(qa_pairs, f, ensure_ascii=False, indent=4)
    
    print(f"✅ 导出完成！")
    print(f"文件路径: {output_file}")
    print(f"有效问答对数量: {len(qa_pairs)}")
    print("💡 提示：你可以将此 JSON 文件内容粘贴到“哈基米助手”的“客服训练记录”中进行投喂。")

if __name__ == "__main__":
    manual_export()
