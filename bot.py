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

# ===== 配置 =====
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = 8276405169

if not TOKEN:
    raise ValueError("请设置环境变量 BOT_TOKEN")

# Railway 会分配 PORT
PORT = int(os.environ.get("PORT", 8000))
# 手动指定你的 Railway 项目 URL，用于 webhook
# 替换为你的实际域名
RAILWAY_URL = f"https://你的Railway域名.up.railway.app/{TOKEN}"

# ===== 文件 =====
KEYWORDS_FILE = "keywords.json"
GROUPS_FILE = "groups.json"

# ===== 参数 =====
SCORE_THRESHOLD = 3

# ===== 数据结构 =====
user_scores = {}
user_history = defaultdict(lambda: deque(maxlen=5))
user_cache = {}
bio_warn_cooldown = {}

# ===== 正则 =====
link_regex = re.compile(r"(http|t\.me|\.com|\.xyz|\.top|\.cc|\.net)")

USERNAME_BAD_WORDS = [
    "资源","看片","卖片","成人视频","幼女","福利","点我头像","私聊我"
]

BIO_KEYWORDS = [
    "资源","看片","福利","幼女"
]

# ===== 工具 =====
def load_json(file):
    if not os.path.exists(file):
        return []
    with open(file,"r",encoding="utf-8") as f:
        return json.load(f)

def save_json(file,data):
    with open(file,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)

def is_admin(update):
    return update.effective_user.id == ADMIN_ID

def check_username(user):
    full = (user.username or "") + (user.full_name or "")
    return any(w in full for w in USERNAME_BAD_WORDS)

# ===== 加载数据 =====
keywords = load_json(KEYWORDS_FILE)
allowed_groups = load_json(GROUPS_FILE)

# ===== 分数管理 =====
def update_score(uid, points):
    now = time.time()
    if uid not in user_scores:
        user_scores[uid] = {"score":0, "time":now}
    if now - user_scores[uid]["time"] > 300:  # 5分钟无行为清零
        user_scores[uid]["score"] = 0
    user_scores[uid]["score"] += points
    user_scores[uid]["time"] = now
    return user_scores[uid]["score"]

def get_score(uid):
    return user_scores.get(uid, {}).get("score", 0)

# ===== 用户资料缓存 =====
async def get_user_bio(bot, chat_id, user_id):
    key = (chat_id, user_id)
    now = time.time()
    if key in user_cache:
        if now - user_cache[key]["time"] < 600:
            return user_cache[key]["bio"]
    member = await bot.get_chat_member(chat_id, user_id)
    bio = getattr(member.user, "bio", "")
    user_cache[key] = {"bio": bio, "time": now}
    return bio

# ===== 封禁用户 =====
async def mute_user(update):
    try:
        await update.effective_chat.restrict_member(
            update.effective_user.id,
            permissions={}
        )
    except:
        pass

# ===== 管理员命令 =====
async def add_group(update:Update, context:ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    gid = int(context.args[0])
    if gid not in allowed_groups:
        allowed_groups.append(gid)
        save_json(GROUPS_FILE, allowed_groups)
    await update.message.reply_text("群组已添加")

async def remove_group(update, context):
    if not is_admin(update): return
    gid = int(context.args[0])
    if gid in allowed_groups:
        allowed_groups.remove(gid)
        save_json(GROUPS_FILE, allowed_groups)
    await update.message.reply_text("群组已移除")

async def add_keyword(update, context):
    if not is_admin(update): return
    text = update.message.text.replace("/addkw","").strip()
    for l in text.split("\n"):
        if l.strip() and l not in keywords:
            keywords.append(l.strip())
    save_json(KEYWORDS_FILE, keywords)
    await update.message.reply_text("关键词已增加")

async def export_data(update, context):
    if not is_admin(update): return
    data = {"keywords": keywords, "groups": allowed_groups}
    await update.message.reply_text(json.dumps(data, ensure_ascii=False, indent=2))

# ===== 消息处理 =====
async def handle_message(update:Update, context:ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    text = update.message.text or ""
    uid = user.id

    if chat.id not in allowed_groups:
        return

    user_history[uid].append(text)

    # 单字连续三次禁言
    if len(text) == 1:
        last = list(user_history[uid])[-3:]
        if len(last) == 3 and all(len(x) == 1 for x in last):
            await mute_user(update)
            await update.message.reply_text(
                f"{uid} 已触发自研防炸群风控模型，误封请联系管理员"
            )
            return

    combined = "".join(user_history[uid])
    for kw in keywords:
        if kw in combined:
            await mute_user(update)
            await update.message.reply_text(f"{uid} 已触发关键词风控模型")
            return

    # 用户名异常
    if check_username(user):
        score = update_score(uid, 2)
        if score >= SCORE_THRESHOLD:
            await mute_user(update)
            await update.message.reply_text("用户名异常已禁言")
            return

    # 消息中含链接
    if link_regex.search(text):
        update_score(uid,1)

    # BIO检查
    bio = await get_user_bio(context.bot, chat.id, uid)
    if bio:
        if link_regex.search(bio):
            key = (chat.id, uid)
            now = time.time()
            if key not in bio_warn_cooldown or now - bio_warn_cooldown[key] > 60:
                bio_warn_cooldown[key] = now
                await update.message.reply_text("该用户简介包含链接，疑似引流，注意甄别")
        update_score(uid,1)

    # 总分判断
    if get_score(uid) >= SCORE_THRESHOLD:
        await mute_user(update)
        await update.message.reply_text("用户行为异常已禁言")

# ===== 启动 =====
def main():
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("addgroup", add_group))
    app.add_handler(CommandHandler("delgroup", remove_group))
    app.add_handler(CommandHandler("addkw", add_keyword))
    app.add_handler(CommandHandler("export", export_data))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))

    app.run_webhook(listen="0.0.0.0", port=PORT, webhook_url=RAILWAY_URL)

if __name__=="__main__":
    main()