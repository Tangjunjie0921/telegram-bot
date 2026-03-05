import os
import json
import re
import time
from collections import defaultdict, deque

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ChatPermissions
)

from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    filters
)

TOKEN = os.environ.get("BOT_TOKEN")

ADMIN_ID = 8276405169
DATA_FILE = "data.json"

# ======================
# 参数
# ======================

PARAMS = {
    "score_threshold": 3,
    "single_char_limit": 3,
    "username_score": 2,
    "link_score": 1,
    "cooldown": 60,
    "bio_alert": True
}

# ======================
# 数据
# ======================

groups = set()
keywords = set()

user_scores = defaultdict(int)
user_chars = defaultdict(lambda: deque(maxlen=5))
user_recent = defaultdict(lambda: deque(maxlen=6))

bio_cache = {}
bio_cache_time = {}

BIO_CACHE_TTL = 600

# ======================
# 拼字词库
# ======================

SPAM_PATTERNS = [
    "点我头像",
    "点击头像",
    "私聊我",
    "dian",
    "diantouxiang"
]

USERNAME_PATTERNS = [
    "资源",
    "看片",
    "福利",
    "萝莉",
    "幼女",
    "头像",
    "私聊"
]

# ======================
# 数据持久化
# ======================

def load_data():

    global groups, keywords, PARAMS

    if os.path.exists(DATA_FILE):

        with open(DATA_FILE, "r") as f:

            data = json.load(f)

            groups = set(data.get("groups", []))
            keywords = set(data.get("keywords", []))
            PARAMS.update(data.get("params", {}))


def save_data():

    with open(DATA_FILE, "w") as f:

        json.dump(
            {
                "groups": list(groups),
                "keywords": list(keywords),
                "params": PARAMS
            },
            f
        )

# ======================
# 工具
# ======================

def contains_link(text):

    pattern = r"(https?://|t\.me/)"

    return re.search(pattern, text, re.IGNORECASE)


def is_single_char(text):

    text = text.strip()

    return len(text) == 1


async def mute_user(chat, user_id):

    permissions = ChatPermissions(
        can_send_messages=False
    )

    try:

        await chat.restrict_member(
            user_id,
            permissions
        )

    except:
        pass


async def get_bio_cached(context, user_id):

    now = time.time()

    if user_id in bio_cache:

        if now - bio_cache_time[user_id] < BIO_CACHE_TTL:

            return bio_cache[user_id]

    try:

        chat = await context.bot.get_chat(user_id)

        bio = chat.bio or ""

        bio_cache[user_id] = bio
        bio_cache_time[user_id] = now

        return bio

    except:

        return ""

# ======================
# 管理员命令
# ======================

async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    gid = int(context.args[0])

    groups.add(gid)

    save_data()

    await update.message.reply_text("群组已添加")


async def del_group(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    gid = int(context.args[0])

    groups.discard(gid)

    save_data()

    await update.message.reply_text("群组已删除")


async def add_kw(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.replace("/addkw", "").strip()

    for line in text.split("\n"):

        kw = line.strip()

        if kw:
            keywords.add(kw)

    save_data()

    await update.message.reply_text("关键词已增加")


async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    data = {

        "groups": list(groups),
        "keywords": list(keywords),
        "params": PARAMS
    }

    await update.message.reply_text(
        json.dumps(data, indent=2)
    )

# ======================
# ADMIN 面板
# ======================

MENU, SETVAL = range(2)

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    keyboard = [

        [InlineKeyboardButton("风控分数", callback_data="score_threshold")],
        [InlineKeyboardButton("单字次数", callback_data="single_char_limit")],
        [InlineKeyboardButton("用户名分数", callback_data="username_score")],
        [InlineKeyboardButton("链接分数", callback_data="link_score")],
        [InlineKeyboardButton("冷却时间", callback_data="cooldown")],
        [InlineKeyboardButton("BIO提醒开关", callback_data="bio_alert")]

    ]

    await update.message.reply_text(

        "管理员控制面板",

        reply_markup=InlineKeyboardMarkup(keyboard)

    )

    return MENU


async def admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await query.answer()

    key = query.data

    if key == "bio_alert":

        PARAMS["bio_alert"] = not PARAMS["bio_alert"]

        save_data()

        await query.edit_message_text(

            f"BIO提醒状态: {PARAMS['bio_alert']}"

        )

        return ConversationHandler.END

    context.user_data["edit_key"] = key

    await query.edit_message_text(

        f"当前值: {PARAMS[key]}\n请输入新值"

    )

    return SETVAL


async def admin_set(update: Update, context: ContextTypes.DEFAULT_TYPE):

    key = context.user_data.get("edit_key")

    try:

        val = int(update.message.text)

    except:

        await update.message.reply_text("请输入数字")

        return SETVAL

    PARAMS[key] = val

    save_data()

    await update.message.reply_text("参数已更新")

    return ConversationHandler.END

# ======================
# 消息监听
# ======================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):

    chat = update.effective_chat

    if chat.id not in groups:
        return

    user = update.effective_user
    text = update.message.text or ""
    uid = user.id

    # ======================
    # 单字检测
    # ======================

    if is_single_char(text):

        user_chars[uid].append(1)

    else:

        user_chars[uid].clear()

    if len(user_chars[uid]) >= PARAMS["single_char_limit"]:

        await mute_user(chat, uid)

        await update.message.reply_text(

            f"{uid} 已触发防炸群风控模型"

        )

        return

    # ======================
    # 拼字检测
    # ======================

    user_recent[uid].append(text)

    joined = "".join(user_recent[uid]).lower()

    for pattern in SPAM_PATTERNS:

        if pattern in joined:

            await mute_user(chat, uid)

            await update.message.reply_text(

                f"{uid} 疑似引流拼字行为"

            )

            return

    # ======================
    # 关键词检测
    # ======================

    for kw in keywords:

        if kw in text:

            await mute_user(chat, uid)

            return

    score = 0

    # ======================
    # 用户名检测
    # ======================

    name = (user.username or "") + (user.full_name or "")

    for p in USERNAME_PATTERNS:

        if p in name:

            score += PARAMS["username_score"]

            break

    # ======================
    # 链接检测
    # ======================

    if contains_link(text):

        score += PARAMS["link_score"]

    user_scores[uid] += score

    if user_scores[uid] >= PARAMS["score_threshold"]:

        await mute_user(chat, uid)

        await update.message.reply_text(

            f"{uid} 已触发自研风控模型"

        )

    # ======================
    # BIO检测
    # ======================

    if PARAMS["bio_alert"]:

        bio = await get_bio_cached(context, uid)

        if bio and contains_link(bio):

            await update.message.reply_text(

                "该用户简介含链接，疑似引流，请注意甄别"

            )

# ======================
# 主程序
# ======================

def main():

    load_data()

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("addgroup", add_group))
    app.add_handler(CommandHandler("delgroup", del_group))
    app.add_handler(CommandHandler("addkw", add_kw))
    app.add_handler(CommandHandler("export", export_data))

    admin_handler = ConversationHandler(

        entry_points=[CommandHandler("admin", admin_panel)],

        states={

            MENU: [CallbackQueryHandler(admin_menu)],
            SETVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_set)]

        },

        fallbacks=[]
        per_chat=True
        per_user=True
    )

    app.add_handler(admin_handler)

    app.add_handler(

        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)

    )

    app.run_polling()


if __name__ == "__main__":

    main()