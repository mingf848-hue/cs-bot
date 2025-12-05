import logging
import asyncio
from telegram import Update
from telegram.ext import Application, MessageHandler, ContextTypes, filters

# ================= 配置区域 =================
# 你的 Bot Token
TOKEN = '8276151101:AAFXQ03i6pyEqJCX2wOnbYoCATMTVIbowGQ'

# 群组 ID (已自动添加 -100 前缀以适配超级群组)
CS_GROUP_ID = -1004990486181
ALERT_GROUP_ID = -1005093247908

TIMEOUT_SECONDS = 15 * 60  # 15分钟

# 触发关键词列表
WAIT_SIGNATURES = [
    "稍等-an", "请稍等elk", "稍等-jl", "请稍等-~cc", "请稍等～aja",
    "请稍等-HED", "请稍等-xxxx", "请稍等-MAD", "请稍等 - AB", "请稍等ART",
    "稍等～ys", "请稍等~lofi", "稍等-SO", "请稍等～～aug", "稍等--Gr💬",
    "稍等-Be", "稍等-XW", "请稍等~d", "请稍等～yu"
]

# ================= 日志设置 =================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# 内存字典：Key=原消息ID, Value=Job对象
pending_jobs = {}

async def alert_callback(context: ContextTypes.DEFAULT_TYPE):
    """倒计时结束，发送预警"""
    job_data = context.job.data
    original_msg_id = job_data['original_msg_id']
    trigger_msg_link = job_data['trigger_msg_link']
    original_user = job_data['original_user']
    trigger_keyword = job_data['trigger_keyword']

    if original_msg_id in pending_jobs:
        del pending_jobs[original_msg_id]

    alert_text = (
        f"🚨 **超时预警 (15分钟)**\n\n"
        f"👤 客户: {original_user}\n"
        f"🔑 触发签名: `{trigger_keyword}`\n"
        f"⚠️ 状态: 客服回复稍等后，超过15分钟未进一步回复。\n\n"
        f"🔗 [点击跳转处理]({trigger_msg_link})"
    )
    
    try:
        await context.bot.send_message(
            chat_id=ALERT_GROUP_ID,
            text=alert_text,
            parse_mode='Markdown',
            disable_web_page_preview=True
        )
    except Exception as e:
        logging.error(f"发送预警失败: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    
    # 过滤非文本或非目标群组消息
    if not msg or not msg.text:
        return
        
    # 如果是在客服群收到消息
    if msg.chat_id == CS_GROUP_ID:
        # 必须是回复消息
        if not msg.reply_to_message:
            return

        original_msg_id = msg.reply_to_message.message_id
        
        # --- 逻辑 A: 检测是否包含签名 (开启监控) ---
        matched_signature = next((sig for sig in WAIT_SIGNATURES if sig in msg.text), None)

        if matched_signature:
            original_user = "用户"
            if msg.reply_to_message.from_user:
                original_user = msg.reply_to_message.from_user.first_name
            
            # 生成链接 (移除 -100 前缀)
            clean_chat_id = str(CS_GROUP_ID).replace("-100", "")
            msg_link = f"https://t.me/c/{clean_chat_id}/{original_msg_id}"

            print(f"✅ 监控开启: {original_user} | 签名: {matched_signature}")

            # 如果已有旧任务，先移除
            if original_msg_id in pending_jobs:
                pending_jobs[original_msg_id].schedule_removal()

            new_job = context.job_queue.run_once(
                alert_callback, 
                TIMEOUT_SECONDS, 
                data={
                    'original_msg_id': original_msg_id,
                    'trigger_msg_link': msg_link,
                    'original_user': original_user,
                    'trigger_keyword': matched_signature
                }
            )
            pending_jobs[original_msg_id] = new_job
            return

        # --- 逻辑 B: 检测后续回复 (取消监控) ---
        # 只要是回复了“正在被监控的消息”，无论回复什么内容，都视为处理
        if original_msg_id in pending_jobs:
            job = pending_jobs[original_msg_id]
            job.schedule_removal()
            del pending_jobs[original_msg_id]
            print(f"❎ 监控解除: 检测到后续回复。")

    # 简单的ID调试：如果是在预警群发消息，打印一下ID确认配置无误
    elif msg.chat_id == ALERT_GROUP_ID:
        pass

if __name__ == '__main__':
    print("Bot 正在启动...")
    print(f"客服群ID: {CS_GROUP_ID}")
    print(f"预警群ID: {ALERT_GROUP_ID}")
    
    application = Application.builder().token(TOKEN).build()
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()
