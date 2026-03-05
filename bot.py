import os
import json
import time
import re
from collections import defaultdict, deque

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
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

# =========================
# 可调整参数
# =========================

PARAMS = {
    "score_threshold": 3,
    "single_char_limit": 3,
    "username_score": 2,
    "link_score": 1,
    "cooldown": 60,
    "bio_alert": True
}

# =========================
# 数据结构
# =========================

groups = set()
keywords = set()

user_scores = defaultdict(int)
user_last_messages = defaultdict(lambda: deque(maxlen=5))
user_join_time = {}

bio_cache = {}
bio_cache_time = {}

# =========================
# 数据持久化
# =========================

def load_data():
    global groups, keywords, PARAMS
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE,"r") as f:
            data=json.load(f)
            groups=set(data.get("groups",[]))
            keywords=set(data.get("keywords",[]))
            PARAMS.update(data.get("params",{}))

def save_data():
    with open(DATA_FILE,"w") as f:
        json.dump({
            "groups":list(groups),
            "keywords":list(keywords),
            "params":PARAMS
        },f)

# =========================
# 工具函数
# =========================

def contains_link(text):

    link_pattern=r"(https?://|t\.me/|telegram\.me)"
    return re.search(link_pattern,text,re.IGNORECASE)

def is_single_char(text):

    text=text.strip()

    if len(text)==1:
        return True

    if re.fullmatch(r"[a-zA-Z0-9]",text):
        return True

    return False

# =========================
# 管理员命令
# =========================

async def add_group(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id!=ADMIN_ID:
        return

    gid=int(context.args[0])

    groups.add(gid)

    save_data()

    await update.message.reply_text("群组已添加")

async def del_group(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id!=ADMIN_ID:
        return

    gid=int(context.args[0])

    groups.discard(gid)

    save_data()

    await update.message.reply_text("群组已删除")

async def add_kw(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id!=ADMIN_ID:
        return

    text=update.message.text.replace("/addkw","").strip()

    for line in text.split("\n"):
        keywords.add(line.strip())

    save_data()

    await update.message.reply_text("关键词已增加")

async def export_data(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id!=ADMIN_ID:
        return

    data={
        "groups":list(groups),
        "keywords":list(keywords),
        "params":PARAMS
    }

    await update.message.reply_text(json.dumps(data,indent=2))

# =========================
# ADMIN PANEL
# =========================

MENU,SETVAL=range(2)

async def admin_panel(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id!=ADMIN_ID:
        return ConversationHandler.END

    keyboard=[

        [InlineKeyboardButton("风控分数",callback_data="score_threshold")],
        [InlineKeyboardButton("单字次数",callback_data="single_char_limit")],
        [InlineKeyboardButton("用户名分数",callback_data="username_score")],
        [InlineKeyboardButton("链接分数",callback_data="link_score")],
        [InlineKeyboardButton("冷却时间",callback_data="cooldown")],
        [InlineKeyboardButton("BIO提醒",callback_data="bio_alert")]

    ]

    await update.message.reply_text(
        "管理员面板",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return MENU

async def admin_menu(update:Update,context:ContextTypes.DEFAULT_TYPE):

    query=update.callback_query

    await query.answer()

    key=query.data

    if key=="bio_alert":

        PARAMS["bio_alert"]=not PARAMS["bio_alert"]

        save_data()

        await query.edit_message_text(f"BIO提醒已切换: {PARAMS['bio_alert']}")

        return ConversationHandler.END

    context.user_data["edit_key"]=key

    await query.edit_message_text(f"当前值: {PARAMS[key]}\n请输入新值")

    return SETVAL

async def admin_set(update:Update,context:ContextTypes.DEFAULT_TYPE):

    key=context.user_data.get("edit_key")

    try:

        val=int(update.message.text)

    except:

        await update.message.reply_text("请输入数字")

        return SETVAL

    PARAMS[key]=val

    save_data()

    await update.message.reply_text("已更新")

    return ConversationHandler.END

# =========================
# 风控检测
# =========================

async def handle_message(update:Update,context:ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.id not in groups:
        return

    user=update.effective_user

    text=update.message.text or ""

    uid=user.id

    # 单字检测

    if is_single_char(text):

        user_last_messages[uid].append(1)

    else:

        user_last_messages[uid].clear()

    if len(user_last_messages[uid])>=PARAMS["single_char_limit"]:

        await update.effective_chat.restrict_member(uid)

        await update.message.reply_text(
            f"{uid} 已触发防炸群模型"
        )

        return

    # 关键词检测

    for kw in keywords:

        if kw in text:

            await update.effective_chat.restrict_member(uid)

            return

    score=0

    # 用户名检测

    name=(user.username or "")+(user.full_name or "")

    if "资源" in name or "看片" in name or "福利" in name:

        score+=PARAMS["username_score"]

    # 链接检测

    if contains_link(text):

        score+=PARAMS["link_score"]

    user_scores[uid]+=score

    if user_scores[uid]>=PARAMS["score_threshold"]:

        await update.effective_chat.restrict_member(uid)

        await update.message.reply_text(
            f"{uid} 已触发自研风控模型"
        )

    # BIO检测

    if PARAMS["bio_alert"]:

        try:

            bio=await context.bot.get_chat(uid)

            if bio.bio and contains_link(bio.bio):

                await update.message.reply_text(
                    "该用户简介含链接，疑似引流"
                )

        except:
            pass

# =========================
# 主程序
# =========================

def main():

    load_data()

    app=ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("addgroup",add_group))
    app.add_handler(CommandHandler("delgroup",del_group))
    app.add_handler(CommandHandler("addkw",add_kw))
    app.add_handler(CommandHandler("export",export_data))

    admin_handler=ConversationHandler(

        entry_points=[CommandHandler("admin",admin_panel)],

        states={

            MENU:[CallbackQueryHandler(admin_menu)],

            SETVAL:[MessageHandler(filters.TEXT & ~filters.COMMAND,admin_set)]

        },

        fallbacks=[]

    )

    app.add_handler(admin_handler)

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,handle_message))

    app.run_polling()

if __name__=="__main__":
    main()