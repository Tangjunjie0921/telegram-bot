import os
import json
import time
import re
from collections import defaultdict, deque

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters
)

TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8276405169

keywords_file = "keywords.json"
groups_file = "groups.json"

score_threshold = 3

user_scores = {}
user_history = defaultdict(lambda: deque(maxlen=5))
user_cache = {}
bio_warn_cooldown = {}

link_regex = re.compile(r"(http|t\.me|\.com|\.xyz|\.top|\.cc|\.net)")

username_bad_words = [
    "资源","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片",
    "看片","看片","看片","看片","看片","看片","看片","看片","看片"
]

def load_json(file):
    if not os.path.exists(file):
        return []
    with open(file,"r",encoding="utf-8") as f:
        return json.load(f)

def save_json(file,data):
    with open(file,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

keywords = load_json(keywords_file)
allowed_groups = load_json(groups_file)

def is_admin(update):
    return update.effective_user.id == ADMIN_ID

async def add_group(update:Update,context:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    gid=int(context.args[0])
    if gid not in allowed_groups:
        allowed_groups.append(gid)
        save_json(groups_file,allowed_groups)
    await update.message.reply_text("群组已添加")

async def remove_group(update,context):
    if not is_admin(update): return
    gid=int(context.args[0])
    if gid in allowed_groups:
        allowed_groups.remove(gid)
        save_json(groups_file,allowed_groups)
    await update.message.reply_text("群组已移除")

async def add_keyword(update,context):
    if not is_admin(update): return
    text=update.message.text.replace("/addkw","").strip()
    lines=text.split("\n")
    for l in lines:
        if l not in keywords:
            keywords.append(l)
    save_json(keywords_file,keywords)
    await update.message.reply_text("关键词已增加")

async def export_data(update,context):
    if not is_admin(update): return
    data={
        "keywords":keywords,
        "groups":allowed_groups
    }
    await update.message.reply_text(json.dumps(data,ensure_ascii=False,indent=2))

def check_username(user):
    name=(user.username or "")+(user.full_name or "")
    for w in username_bad_words:
        if w in name:
            return True
    return False

async def get_user_bio(bot,chat_id,user_id):
    key=(chat_id,user_id)
    now=time.time()

    if key in user_cache:
        if now-user_cache[key]["time"]<600:
            return user_cache[key]["bio"]

    member=await bot.get_chat_member(chat_id,user_id)
    bio=getattr(member.user,"bio","")

    user_cache[key]={"bio":bio,"time":now}

    return bio

def update_score(uid,points):
    now=time.time()

    if uid not in user_scores:
        user_scores[uid]={"score":0,"time":now}

    if now-user_scores[uid]["time"]>300:
        user_scores[uid]["score"]=0

    user_scores[uid]["score"]+=points
    user_scores[uid]["time"]=now

    return user_scores[uid]["score"]

async def mute_user(update):
    try:
        await update.effective_chat.restrict_member(
            update.effective_user.id,
            permissions={}
        )
    except:
        pass

async def handle_message(update:Update,context:ContextTypes.DEFAULT_TYPE):

    chat=update.effective_chat
    user=update.effective_user
    text=update.message.text or ""

    if chat.id not in allowed_groups:
        return

    uid=user.id

    user_history[uid].append(text)

    if len(text)==1:
        last=list(user_history[uid])[-3:]
        if len(last)==3:
            if all(len(x)==1 for x in last):
                await mute_user(update)
                await update.message.reply_text(
                    f"{uid} 已触发自研防炸群风控模型，误封请联系管理员"
                )
                return

    combined="".join(user_history[uid])

    for kw in keywords:
        if kw in combined:
            await mute_user(update)
            await update.message.reply_text(
                f"{uid} 已触发关键词风控模型"
            )
            return

    if check_username(user):
        score=update_score(uid,2)
        if score>=score_threshold:
            await mute_user(update)
            await update.message.reply_text("用户名异常已禁言")
            return

    if link_regex.search(text):
        score=update_score(uid,1)

    bio=await get_user_bio(context.bot,chat.id,user.id)

    if bio:
        if link_regex.search(bio):
            now=time.time()
            key=(chat.id,user.id)

            if key not in bio_warn_cooldown or now-bio_warn_cooldown[key]>60:
                bio_warn_cooldown[key]=now

                await update.message.reply_text(
                    "该用户简介包含链接，疑似引流，注意甄别"
                )

        score=update_score(uid,1)

        if score>=score_threshold:
            await mute_user(update)
            await update.message.reply_text(
                "用户行为异常已禁言"
            )

def main():

    app=ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("addgroup",add_group))
    app.add_handler(CommandHandler("delgroup",remove_group))
    app.add_handler(CommandHandler("addkw",add_keyword))
    app.add_handler(CommandHandler("export",export_data))

    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND),handle_message))

    port=int(os.environ.get("PORT",8000))

    app.run_webhook(
        listen="0.0.0.0",
        port=port,
        webhook_url=os.environ.get("RAILWAY_STATIC_URL")+TOKEN
    )

if __name__=="__main__":
    main()