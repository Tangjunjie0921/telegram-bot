import os
import re
import asyncio
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

# 举报记录
reports = {}

# 原消息ID -> 机器人消息ID
message_map = {}

# 正则检测链接
link_pattern = re.compile(r"(https?://|t\.me/|@)")

# -----------------------------
# 检测群消息
# -----------------------------
async def check_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = update.message
    if not msg:
        return

    if msg.chat.id != GROUP_ID:
        return

    user = msg.from_user

    if user.is_bot:
        return

    try:
        member = await context.bot.get_chat_member(GROUP_ID, user.id)
    except:
        return

    bio = getattr(member.user, "bio", "")

    if not bio:
        return

    if not link_pattern.search(bio):
        return

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("举报该用户", callback_data=f"report_{user.id}_{msg.message_id}")]]
    )

    bot_msg = await msg.reply_text(
        f"⚠️ 简介有链接，疑似广告引流\n用户ID: {user.id}\n举报数: 0",
        reply_markup=keyboard
    )

    key = (user.id, msg.message_id)

    reports[key] = set()
    message_map[msg.message_id] = bot_msg.message_id


# -----------------------------
# 举报按钮
# -----------------------------
async def handle_report(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    data = query.data.split("_")

    user_id = int(data[1])
    origin_msg = int(data[2])

    key = (user_id, origin_msg)

    if key not in reports:
        return

    reporter = query.from_user.id

    if reporter in reports[key]:
        return

    reports[key].add(reporter)

    count = len(reports[key])

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("举报该用户", callback_data=f"report_{user_id}_{origin_msg}")]]
    )

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


# -----------------------------
# 定时检测消息是否被删除
# -----------------------------
async def cleanup_loop(context: ContextTypes.DEFAULT_TYPE):

    bot = context.bot

    while True:

        await asyncio.sleep(20)

        remove_list = []

        for origin_msg, bot_msg in message_map.items():

            try:

                await bot.forward_message(
                    chat_id=GROUP_ID,
                    from_chat_id=GROUP_ID,
                    message_id=origin_msg
                )

            except:

                try:
                    await bot.delete_message(
                        chat_id=GROUP_ID,
                        message_id=bot_msg
                    )
                except:
                    pass

                remove_list.append(origin_msg)

        for m in remove_list:
            message_map.pop(m, None)


# -----------------------------
# 启动
# -----------------------------
async def post_init(app):

    app.create_task(cleanup_loop(app.bot))


def main():

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), check_message)
    )

    app.add_handler(
        CallbackQueryHandler(handle_report)
    )

    print("Bot running...")

    app.run_polling()


if __name__ == "__main__":
    main()