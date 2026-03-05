import os
import json
import re
import time
import asyncio
from collections import defaultdict, deque

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
    ChatPermissions, BotCommand
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, CallbackQueryHandler, ConversationHandler,
    filters, JobQueue
)

# =====================================
# 环境变量与常量
# =====================================

TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not TOKEN or ADMIN_ID == 0:
    raise ValueError("缺少 BOT_TOKEN 或 ADMIN_ID 环境变量")

DATA_FILE = "data.json"
BIO_CACHE_TTL = 600  # 10 分钟

# =====================================
# 全局数据
# =====================================

groups = set()                      # 受保护的群组 ID
blacklist = set()                   # 黑名单用户 ID
keywords = set()                    # 自定义关键词（消息检测）
spam_patterns = set()               # 拼字/拆字检测词
username_patterns = set()           # 用户名/昵称检测词

PARAMS = {
    "enable_single_char": True,
    "enable_spam_pattern": True,
    "enable_keyword": True,
    "enable_username": True,
    "enable_link": True,
    "enable_bio_alert": True,
    "score_threshold": 3,
    "single_char_limit": 3,
    "username_score": 2,
    "link_score": 1,
    "cooldown": 60,  # 暂未使用，可扩展发言频率限制
}

# 运行时缓存
user_scores = defaultdict(int)
user_chars = defaultdict(lambda: deque(maxlen=8))
user_recent = defaultdict(lambda: deque(maxlen=8))
bio_cache = {}
bio_cache_time = {}
last_save_time = 0

# =====================================
# 数据持久化
# =====================================

def load_data():
    global groups, blacklist, keywords, spam_patterns, username_patterns, PARAMS
    if not os.path.exists(DATA_FILE):
        return
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            groups = set(data.get("groups", []))
            blacklist = set(data.get("blacklist", []))
            keywords = set(data.get("keywords", []))
            spam_patterns = set(data.get("spam_patterns", ["点我头像", "点击头像", "私聊我", "dian", "diantouxiang"]))
            username_patterns = set(data.get("username_patterns", ["资源", "看片", "福利", "萝莉", "幼女", "头像", "私聊"]))
            PARAMS.update(data.get("params", {}))
    except Exception as e:
        print(f"加载 data.json 失败: {e}")


def save_data(force=False):
    global last_save_time
    now = time.time()
    if not force and now - last_save_time < 10:
        return  # 节流：10秒内不重复保存
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "groups": list(groups),
                "blacklist": list(blacklist),
                "keywords": list(keywords),
                "spam_patterns": list(spam_patterns),
                "username_patterns": list(username_patterns),
                "params": PARAMS
            }, f, ensure_ascii=False, indent=2)
        last_save_time = now
    except Exception as e:
        print(f"保存 data.json 失败: {e}")


# =====================================
# 工具函数
# =====================================

def contains_link(text: str) -> bool:
    if not text:
        return False
    return bool(re.search(r"(https?://|t\.me/)", text, re.IGNORECASE))


def is_single_char(text: str) -> bool:
    return len(text.strip()) == 1


async def mute_user(chat, user_id: int):
    try:
        await chat.restrict_member(
            user_id,
            permissions=ChatPermissions(can_send_messages=False)
        )
    except Exception:
        pass


async def get_bio_cached(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> str:
    now = time.time()
    if user_id in bio_cache and now - bio_cache_time.get(user_id, 0) < BIO_CACHE_TTL:
        return bio_cache[user_id]

    try:
        chat = await context.bot.get_chat(user_id)
        bio = chat.bio or ""
        bio_cache[user_id] = bio
        bio_cache_time[user_id] = now
        return bio
    except Exception:
        return ""


# =====================================
# 消息处理核心逻辑
# =====================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type == "private":
        return

    if chat.id not in groups:
        return

    user = update.effective_user
    uid = user.id
    text = update.message.text or ""

    # 黑名单最高优先级
    if uid in blacklist:
        await mute_user(chat, uid)
        return

    score = 0

    # 单字连发检测
    if PARAMS["enable_single_char"]:
        if is_single_char(text):
            user_chars[uid].append(1)
        else:
            user_chars[uid].clear()
        if len(user_chars[uid]) >= PARAMS["single_char_limit"]:
            await mute_user(chat, uid)
            await update.message.reply_text(f"{uid} 触发单字连发风控")
            return

    # 拼字检测（最近几条拼接）
    if PARAMS["enable_spam_pattern"] and spam_patterns:
        user_recent[uid].append(text.lower())
        joined = "".join(user_recent[uid])
        for pat in spam_patterns:
            if pat in joined:
                await mute_user(chat, uid)
                await update.message.reply_text(f"{uid} 疑似拼字引流")
                return

    # 自定义关键词检测
    if PARAMS["enable_keyword"]:
        for kw in keywords:
            if kw in text:
                await mute_user(chat, uid)
                await update.message.reply_text(f"{uid} 触发自定义关键词风控")
                return

    # 用户名/昵称检测
    if PARAMS["enable_username"]:
        name = (user.username or "") + (user.full_name or "")
        name_lower = name.lower()
        for pat in username_patterns:
            if pat in name_lower:
                score += PARAMS["username_score"]
                break

    # 链接检测
    if PARAMS["enable_link"] and contains_link(text):
        score += PARAMS["link_score"]

    if score > 0:
        user_scores[uid] += score
        if user_scores[uid] >= PARAMS["score_threshold"]:
            await mute_user(chat, uid)
            await update.message.reply_text(f"{uid} 触发综合评分风控")
            return

    # BIO 提醒（不封禁，只提醒）
    if PARAMS["enable_bio_alert"]:
        bio = await get_bio_cached(context, uid)
        if bio and contains_link(bio):
            await update.message.reply_text("该用户简介含链接，疑似引流，请注意甄别")


# =====================================
# 管理员面板 - Conversation 状态
# =====================================

(
    MAIN_MENU,
    PARAM_MENU,
    PARAM_EDIT,
    LIST_SELECT,
    LIST_ADD,
    LIST_REMOVE,
    LIST_CLEAR_CONFIRM,
) = range(7)


async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID or update.effective_chat.type != "private":
        await update.message.reply_text("无权限，仅限管理员私聊使用")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("⚙ 检测开关与阈值", callback_data="params")],
        [InlineKeyboardButton("词表管理", callback_data="wordlists")],
        [InlineKeyboardButton("黑名单管理", callback_data="blacklist")],
        [InlineKeyboardButton("群组管理", callback_data="groups")],
        [InlineKeyboardButton("导出配置", callback_data="export")],
    ]
    await update.message.reply_text(
        "管理员控制中心",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return MAIN_MENU


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "params":
        lines = ["当前设置："]
        for k, v in sorted(PARAMS.items()):
            lines.append(f"  {k:22} : {v}")
        text = "\n".join(lines)
        keyboard = [
            [InlineKeyboardButton("修改某项", callback_data="param_edit")],
            [InlineKeyboardButton("← 返回", callback_data="back")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return PARAM_MENU

    elif data == "param_edit":
        keyboard = [
            [InlineKeyboardButton("enable_single_char", callback_data="edit_enable_single_char")],
            [InlineKeyboardButton("enable_spam_pattern", callback_data="edit_enable_spam_pattern")],
            [InlineKeyboardButton("enable_keyword", callback_data="edit_enable_keyword")],
            [InlineKeyboardButton("enable_username", callback_data="edit_enable_username")],
            [InlineKeyboardButton("enable_link", callback_data="edit_enable_link")],
            [InlineKeyboardButton("enable_bio_alert", callback_data="edit_enable_bio_alert")],
            [InlineKeyboardButton("score_threshold", callback_data="edit_score_threshold")],
            [InlineKeyboardButton("single_char_limit", callback_data="edit_single_char_limit")],
            [InlineKeyboardButton("username_score", callback_data="edit_username_score")],
            [InlineKeyboardButton("link_score", callback_data="edit_link_score")],
            [InlineKeyboardButton("← 返回", callback_data="params")],
        ]
        await query.edit_message_text("请选择要修改的项目：", reply_markup=InlineKeyboardMarkup(keyboard))
        return PARAM_EDIT

    elif data.startswith("edit_"):
        key = data[5:]
        context.user_data["edit_key"] = key
        current = PARAMS.get(key, "未知")
        text = f"当前 {key} = {current}\n\n请回复新值：\n"
        if key.startswith("enable_"):
            text += "（true / false  或  开 / 关）"
        else:
            text += "（整数）"
        await query.edit_message_text(text)
        return PARAM_EDIT

    # 词表统一入口
    elif data == "wordlists":
        keyboard = [
            [InlineKeyboardButton("自定义关键词（消息）", callback_data="list_keywords")],
            [InlineKeyboardButton("拼字/拆字词", callback_data="list_spam_patterns")],
            [InlineKeyboardButton("用户名敏感词", callback_data="list_username_patterns")],
            [InlineKeyboardButton("← 返回", callback_data="back")],
        ]
        await query.edit_message_text("请选择词表：", reply_markup=InlineKeyboardMarkup(keyboard))
        return LIST_SELECT

    # 通用列表处理（关键词、spam、username_patterns、blacklist、groups）
    elif data in ("list_keywords", "list_spam_patterns", "list_username_patterns", "blacklist", "groups"):
        context.user_data["current_list"] = data
        name_map = {
            "list_keywords": "自定义关键词",
            "list_spam_patterns": "拼字/拆字词",
            "list_username_patterns": "用户名敏感词",
            "blacklist": "黑名单",
            "groups": "受保护群组",
        }
        lst_name = name_map.get(data, data)
        target_set = {
            "list_keywords": keywords,
            "list_spam_patterns": spam_patterns,
            "list_username_patterns": username_patterns,
            "blacklist": blacklist,
            "groups": groups,
        }[data]

        items = sorted(target_set)
        if not items:
            text = f"{lst_name}：空"
        else:
            text = f"{lst_name}（共 {len(items)} 项）：\n" + "\n".join(f"  • {x}" for x in items[:30])
            if len(items) > 30:
                text += f"\n... 共 {len(items)} 项（仅显示前30）"

        keyboard = [
            [InlineKeyboardButton("添加", callback_data="list_add")],
            [InlineKeyboardButton("删除", callback_data="list_remove")],
            [InlineKeyboardButton("清空", callback_data="list_clear")],
            [InlineKeyboardButton("← 返回", callback_data="wordlists" if "list_" in data else "back")],
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
        return LIST_SELECT

    elif data == "list_add":
        await query.edit_message_text("请回复要添加的内容（支持多行，每行一个）：")
        return LIST_ADD

    elif data == "list_remove":
        await query.edit_message_text("请回复要删除的内容（支持多行，每行一个）：")
        return LIST_REMOVE

    elif data == "list_clear":
        keyboard = [
            [InlineKeyboardButton("确认清空", callback_data="list_clear_confirm")],
            [InlineKeyboardButton("取消", callback_data="back")],
        ]
        await query.edit_message_text("确定要清空当前列表？", reply_markup=InlineKeyboardMarkup(keyboard))
        return LIST_CLEAR_CONFIRM

    elif data == "list_clear_confirm":
        clist = context.user_data.get("current_list")
        if clist:
            target = {
                "list_keywords": keywords,
                "list_spam_patterns": spam_patterns,
                "list_username_patterns": username_patterns,
                "blacklist": blacklist,
                "groups": groups,
            }.get(clist)
            if target is not None:
                target.clear()
                save_data(force=True)
                await query.edit_message_text("列表已清空")
        return await button_handler(update, context)  # 刷新

    elif data == "export":
        data_dict = {
            "groups": list(groups),
            "blacklist": list(blacklist),
            "keywords": list(keywords),
            "spam_patterns": list(spam_patterns),
            "username_patterns": list(username_patterns),
            "params": PARAMS
        }
        text = "当前完整配置（可复制保存）:\n\n```json\n" + json.dumps(data_dict, ensure_ascii=False, indent=2) + "\n```"
        await query.edit_message_text(text, parse_mode="Markdown")
        return MAIN_MENU

    elif data == "back":
        await admin_start(update, context)
        return MAIN_MENU


async def param_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    key = context.user_data.get("edit_key")
    if not key:
        await update.message.reply_text("操作已超时，请重新开始")
        return ConversationHandler.END

    text = update.message.text.strip().lower()
    try:
        if key.startswith("enable_"):
            if text in ("true", "开", "1", "yes", "on"):
                PARAMS[key] = True
            elif text in ("false", "关", "0", "no", "off"):
                PARAMS[key] = False
            else:
                await update.message.reply_text("请输入 true/false 或 开/关")
                return PARAM_EDIT
        else:
            PARAMS[key] = int(text)

        save_data(force=True)
        await update.message.reply_text(f"{key} 已更新为 {PARAMS[key]}")
        return await admin_start(update, context)

    except ValueError:
        await update.message.reply_text("格式错误，请输入正确数值")
        return PARAM_EDIT


async def list_add_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clist = context.user_data.get("current_list")
    if not clist:
        return ConversationHandler.END

    target = {
        "list_keywords": keywords,
        "list_spam_patterns": spam_patterns,
        "list_username_patterns": username_patterns,
        "blacklist": blacklist,
        "groups": groups,
    }.get(clist)

    if target is None:
        await update.message.reply_text("列表异常，请重新操作")
        return ConversationHandler.END

    added = 0
    for line in update.message.text.splitlines():
        item = line.strip()
        if item:
            if clist in ("blacklist", "groups"):
                try:
                    target.add(int(item))
                    added += 1
                except ValueError:
                    pass
            else:
                target.add(item)
                added += 1

    if added > 0:
        save_data(force=True)
        await update.message.reply_text(f"已添加 {added} 项")
    else:
        await update.message.reply_text("没有有效内容被添加")

    return await button_handler(update, context)


async def list_remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clist = context.user_data.get("current_list")
    if not clist:
        return ConversationHandler.END

    target = {
        "list_keywords": keywords,
        "list_spam_patterns": spam_patterns,
        "list_username_patterns": username_patterns,
        "blacklist": blacklist,
        "groups": groups,
    }.get(clist)

    removed = 0
    for line in update.message.text.splitlines():
        item = line.strip()
        if item:
            if clist in ("blacklist", "groups"):
                try:
                    if int(item) in target:
                        target.remove(int(item))
                        removed += 1
                except ValueError:
                    pass
            else:
                if item in target:
                    target.remove(item)
                    removed += 1

    if removed > 0:
        save_data(force=True)
        await update.message.reply_text(f"已删除 {removed} 项")
    else:
        await update.message.reply_text("没有匹配项被删除")

    return await button_handler(update, context)


async def timed_save(context: ContextTypes.DEFAULT_TYPE):
    save_data(force=True)


# =====================================
# 主程序
# =====================================

def main():
    load_data()

    app = ApplicationBuilder().token(TOKEN).build()

# 消息监听（只处理文本消息）
    app.add_handler(MessageHandler(filters.TEXT - filters.COMMAND, handle_message))

    # 管理员面板对话
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("admin", admin_start)],
        states={
            MAIN_MENU: [CallbackQueryHandler(button_handler)],
            PARAM_MENU: [CallbackQueryHandler(button_handler)],
            PARAM_EDIT: [
                CallbackQueryHandler(button_handler),
                MessageHandler(filters.TEXT - filters.COMMAND, param_edit_handler)
            ],
            LIST_SELECT: [CallbackQueryHandler(button_handler)],
            LIST_ADD: [MessageHandler(filters.TEXT - filters.COMMAND, list_add_handler)],
            LIST_REMOVE: [MessageHandler(filters.TEXT - filters.COMMAND, list_remove_handler)],
            LIST_CLEAR_CONFIRM: [CallbackQueryHandler(button_handler)],
        },
        fallbacks=[],
        allow_reentry=True,
        per_chat=True,
        per_user=True
    )
    app.add_handler(conv_handler)
    # 定时保存（每5分钟）
    job_queue: JobQueue = app.job_queue
    job_queue.run_repeating(timed_save, interval=300, first=60)

    # 设置 /admin 命令描述（可选）
    async def post_init(application):
        await application.bot.set_my_commands([
            BotCommand("admin", "打开管理员控制面板（私聊）")
        ])

    app.post_init = post_init

    print("Bot 启动中...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()