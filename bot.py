import os
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CallbackQueryHandler, filters, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# 举报记录
reports = {}
message_map = {}
link_pattern = re.compile(r"(https?://|t\.me/|@)")

# 监听群消息
async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.chat.id != GROUP_ID or msg.from_user.is_bot:
        return

    try:
        member = await context.bot.get_chat_member(GROUP_ID, msg.from_user.id)
    except:
        return

    bio = getattr(member.user, "bio", "")
    if not bio or not link_pattern.search(bio):
        return

    key = (msg.from_user.id, msg.message_id)
    reports[key] = set()

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("举报该用户", callback_data=f"report_{msg.from_user.id}_{msg.message_id}")]
    ])

    bot_msg = await msg.reply_text(
        f"⚠️ 简介有链接，疑似广告引流\n用户ID: {msg.from_user.id}\n举报数: 0",
        reply_markup=keyboard
    )

    message_map[msg.message_id] = bot_msg.message_id

# 举报按钮
async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id, origin_msg = map(int, query.data.split("_")[1:])
    key = (user_id, origin_msg)
    if key not in reports:
        return

    reporter = query.from_user.id
    if reporter in reports[key]:
        return

    reports[key].add(reporter)
    count = len(reports[key])

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("举报该用户", callback_data=f"report_{user_id}_{origin_msg}")]
    ])

    if count >= 3:
        await query.edit_message_text(
            f"⚠️ 简介有链接，疑似广告引流\n用户ID: {user_id}\n🚨 超3人举报，已通知管理员",
            reply_markup=keyboard
        )
        await context.bot.send_message(
            ADMIN_ID,
            f"🚨 用户被多人举报\n用户ID: {user_id}\n举报人数: {count}"
        )
    else:
        await query.edit_message_text(
            f"⚠️ 简介有链接，疑似广告引流\n用户ID: {user_id}\n举报数: {count}",
            reply_markup=keyboard
        )

# 启动
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), check_message))
    app.add_handler(CallbackQueryHandler(handle_report))

    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()