import asyncio
import json
import os
import time
from collections import deque
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions
from aiogram.filters import Command

# ==================== 配置（Railway 环境变量） ====================
GROUP_IDS = set()
ADMIN_IDS = set()

try:
    for gid in os.getenv("GROUP_IDS", "").strip().split():
        if gid.strip(): GROUP_IDS.add(int(gid.strip()))
    for uid in os.getenv("ADMIN_IDS", "").strip().split():
        if uid.strip(): ADMIN_IDS.add(int(uid.strip()))
    if not GROUP_IDS or not ADMIN_IDS:
        raise ValueError("GROUP_IDS 或 ADMIN_IDS 为空")
except Exception as e:
    raise ValueError(f"❌ 环境变量错误: {e}")

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("❌ 请设置 BOT_TOKEN")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()
router = Router()
dp.include_router(router)

DATA_FILE = "/data/reports.json"
KEYWORDS_FILE = "/data/spam_keywords.json"
reports = {}
lock = asyncio.Lock()

# ==================== 敏感词热更新（JSON + 私聊管理） ====================
async def load_keywords():
    try:
        os.makedirs(os.path.dirname(KEYWORDS_FILE), exist_ok=True)
        if os.path.exists(KEYWORDS_FILE):
            with open(KEYWORDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        default = ["qq:", "qq：", "qq号", "加qq", "扣扣", "微信", "wx:", "weixin", "加我微信", "wxid_", "幼女", "萝莉", "少妇", "人妻", "福利", "约炮", "onlyfans", "小红书", "抖音", "纸飞机", "机场", "http", "https", "t.me/", "@"]
        with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    except Exception as e:
        print("加载敏感词失败，使用内置:", e)
        return ["qq:", "微信", "幼女", "福利", "t.me/"]

SPAM_KEYWORDS = []

async def load_all():
    global SPAM_KEYWORDS
    SPAM_KEYWORDS = await load_keywords()
    print(f"🚀 敏感词加载完成: {len(SPAM_KEYWORDS)} 个")

# ==================== 私聊敏感词管理（防暴露） ====================
@router.message(Command("addkw"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_add_keyword(message: Message):
    try:
        word = message.text.split(maxsplit=1)[1].strip().lower()
        if not word:
            await message.reply("用法: /addkw 关键词")
            return
        async with lock:
            if word not in SPAM_KEYWORDS:
                SPAM_KEYWORDS.append(word)
                with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(SPAM_KEYWORDS, f, ensure_ascii=False, indent=2)
        await message.reply(f"✅ 已添加: {word}（当前 {len(SPAM_KEYWORDS)} 个）")
    except Exception as e:
        await message.reply(f"添加失败: {e}")

@router.message(Command("delkw"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_del_keyword(message: Message):
    try:
        word = message.text.split(maxsplit=1)[1].strip().lower()
        async with lock:
            if word in SPAM_KEYWORDS:
                SPAM_KEYWORDS.remove(word)
                with open(KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(SPAM_KEYWORDS, f, ensure_ascii=False, indent=2)
                await message.reply(f"✅ 已删除: {word}（当前 {len(SPAM_KEYWORDS)} 个）")
            else:
                await message.reply("词不存在")
    except Exception as e:
        await message.reply(f"删除失败: {e}")

@router.message(Command("listkw"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_list_keywords(message: Message):
    async with lock:
        text = f"📋 当前敏感词（{len(SPAM_KEYWORDS)} 个）:\n" + "\n".join(f"• {w}" for w in sorted(SPAM_KEYWORDS))
    await message.reply(text[:4000])

# ==================== 其他参数 ====================
SHORT_MSG_THRESHOLD = 3
MIN_CONSECUTIVE_COUNT = 2
TIME_WINDOW_SECONDS = 60
FILL_GARBAGE_MIN_RAW_LEN = 12
FILL_GARBAGE_MAX_CLEAN_LEN = 8
FILL_SPACE_RATIO = 0.30
FILL_CHARS = set(r" .,，。！？*\\~`-_=+[]{}()\"'\\|\n\t\r　")

user_short_msg_history = {}

# ==================== 数据持久化 ====================
async def load_data():
    global reports
    try:
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in data.items():
                    v["reporters"] = set(v.get("reporters", []))
                    reports[int(k)] = v
    except Exception as e:
        print("数据加载失败（首次正常）:", e)

async def save_data():
    async with lock:
        try:
            data_to_save = {str(k): {**v, "reporters": list(v["reporters"])} for k, v in reports.items()}
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存失败:", e)

# ==================== bio 检测 ====================
@router.message(F.chat.id.in_(GROUP_IDS))
async def check_user_bio(message: Message):
    if not message.from_user or message.from_user.is_bot: return
    user = message.from_user
    try:
        chat_info = await bot.get_chat(user.id)
        bio = (chat_info.bio or "").lower()
        has_link = any(x in bio for x in ["http://", "https://", "t.me/", "@"])
        has_spam = any(kw.lower() in bio for kw in SPAM_KEYWORDS)
        if has_link or has_spam:
            keyword_text = "链接" if has_link else "敏感词"
            if has_link and has_spam: keyword_text = "链接 + 敏感词"
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}")]])
            text = f"⚠️ 简介疑似广告/引流（含{keyword_text}）\n用户ID: {user.id}\n举报数: 0"
            warning = await message.reply(text, reply_markup=keyboard)
            async with lock:
                reports[message.message_id] = {"warning_id": warning.message_id, "suspect_id": user.id, "chat_id": message.chat.id, "reporters": set()}
            await save_data()
    except Exception: pass

# ==================== 短消息 + 填充检测 ====================
@router.message(F.chat.id.in_(GROUP_IDS), F.text)
async def detect_short_or_filled_spam(message: Message):
    if not message.text or message.from_user.is_bot: return
    user_id = message.from_user.id
    text = message.text
    text_len = len(text)
    now = time.time()

    if text_len >= FILL_GARBAGE_MIN_RAW_LEN:
        cleaned = ''.join(c for c in text if c not in FILL_CHARS).strip()
        clean_len = len(cleaned)
        space_ratio = (text.count(" ") + text.count("　")) / text_len
        is_filled = (clean_len <= FILL_GARBAGE_MAX_CLEAN_LEN) or (space_ratio >= FILL_SPACE_RATIO and clean_len <= 12)
        if is_filled:
            await send_warning(message, user_id, "单次填充式规避")
            return

    if user_id not in user_short_msg_history:
        user_short_msg_history[user_id] = deque(maxlen=15)
    history = user_short_msg_history[user_id]
    while history and now - history[0][0] > TIME_WINDOW_SECONDS:
        history.popleft()
    history.append((now, text))
    recent = list(history)[-MIN_CONSECUTIVE_COUNT:]
    if len(recent) >= MIN_CONSECUTIVE_COUNT and all(len(t.strip()) <= SHORT_MSG_THRESHOLD for _, t in recent):
        await send_warning(message, user_id, "连续极短消息")

# ==================== 统一发送警告（现在只显示举报按钮） ====================
async def send_warning(message: Message, user_id: int, reason: str):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}")]
    ])
    text = f"⚠️ 检测到疑似广告引流规避（{reason}）\n用户ID: {user_id}\n举报数: 0"
    await message.reply(text, reply_markup=keyboard)

# ==================== 举报系统（举报后自动生成第二行两个按钮） ====================
@router.callback_query(F.data.startswith("report:"))
async def handle_report(callback: CallbackQuery):
    try:
        original_id = int(callback.data.split(":", 1)[1])
        reporter_id = callback.from_user.id
        async with lock:
            if original_id not in reports:
                await callback.answer("已过期", show_alert=True); return
            data = reports[original_id]
            if reporter_id in data["reporters"]:
                await callback.answer("已举报过", show_alert=True); return
            data["reporters"].add(reporter_id)
            count = len(data["reporters"])
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]
            chat_id = data["chat_id"]

        keyboard_list = callback.message.reply_markup.inline_keyboard[:] if callback.message.reply_markup else []
        # 如果还没有管理员按钮行，则添加第二行（两个按钮并排）
        if not any("ban" in str(btn.callback_data) for row in keyboard_list for btn in row):
            keyboard_list.append([
                InlineKeyboardButton(text="封禁24小时（👮‍♀️）", callback_data=f"ban24h:{original_id}"),
                InlineKeyboardButton(text="永久封禁（👮‍♂️）", callback_data=f"banperm:{original_id}")
            ])
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_list)

        new_text = f"🚨 已有人举报\n用户ID: {suspect_id}\n举报人数: {count}"
        if count >= 3:
            new_text = f"🚨 超3人举报，已通知管理员\n用户ID: {suspect_id}\n举报人数: {count}"
            await bot.send_message(list(ADMIN_IDS)[0], f"多人举报\n用户ID: {suspect_id}\n群组: {chat_id}")

        await bot.edit_message_text(chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=new_keyboard)
        await save_data()
        await callback.answer(f"举报成功！当前 {count} 人")
    except Exception as e:
        await callback.answer("操作失败", show_alert=True)

# ==================== 管理员封禁（支持24h + 永久） ====================
@router.callback_query(F.data.startswith(("ban24h:", "banperm:")))
async def handle_ban(callback: CallbackQuery):
    try:
        action, original_id = callback.data.split(":", 1)
        original_id = int(original_id)
        caller_id = callback.from_user.id
        chat_id = callback.message.chat.id

        if caller_id not in ADMIN_IDS:
            await callback.answer("仅管理员可操作", show_alert=True); return

        async with lock:
            if original_id not in reports:
                await callback.answer("记录已过期", show_alert=True); return
            data = reports[original_id]
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]

        until_date = int(time.time()) + 86400 if action == "ban24h" else None
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=suspect_id,
            permissions=ChatPermissions(can_send_messages=False, can_send_media_messages=False,
                                      can_send_polls=False, can_send_other_messages=False,
                                      can_add_web_page_previews=False, can_change_info=False,
                                      can_invite_users=False, can_pin_messages=False),
            until_date=until_date
        )

        ban_type = "禁言24小时" if action == "ban24h" else "永久限制"
        new_text = f"🚨 已由管理员{ban_type}\n用户ID: {suspect_id}\n举报人数: {len(data['reporters'])}"
        await bot.edit_message_text(chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=None)

        await callback.answer(f"已{ban_type}", show_alert=True)
        print(f"管理员 {caller_id} 对 {suspect_id} 执行 {ban_type}")

        async with lock:
            reports.pop(original_id, None)
        await save_data()

    except TelegramBadRequest as e:
        if "user_not_participant" in str(e).lower():
            await callback.answer("用户不在群组", show_alert=True)
        elif "not enough rights" in str(e).lower():
            await callback.answer("机器人权限不足", show_alert=True)
        else:
            await callback.answer(f"失败: {str(e)}", show_alert=True)
    except Exception as e:
        await callback.answer("操作失败", show_alert=True)

# ==================== /status ====================
@router.message(Command("status"), F.chat.id.in_(GROUP_IDS), F.from_user.id.in_(ADMIN_IDS))
async def cmd_status(message: Message):
    text = f"✅ 机器人正常运行\n👮 管理员: {len(ADMIN_IDS)}\n📊 监控群组: {len(GROUP_IDS)}\n📁 举报记录: {len(reports)}\n🚫 敏感词: {len(SPAM_KEYWORDS)} 个"
    await message.reply(text, disable_notification=True)

# ==================== 自动清理已删消息 ====================
async def cleanup_deleted_messages():
    while True:
        await asyncio.sleep(300)
        to_remove = []
        async with lock:
            check_list = list(reports.items())
        for orig_id, data in check_list:
            try:
                test = await bot.forward_message(list(ADMIN_IDS)[0], data["chat_id"], orig_id)
                await bot.delete_message(list(ADMIN_IDS)[0], test.message_id)
            except TelegramBadRequest as e:
                if "not found" in str(e).lower():
                    try:
                        await bot.delete_message(data["chat_id"], data["warning_id"])
                        to_remove.append(orig_id)
                    except: pass
        if to_remove:
            async with lock:
                for oid in to_remove: reports.pop(oid, None)
            await save_data()

# ==================== 启动 ====================
async def main():
    print("🚀 第六版按钮优化版启动成功（初始只显示举报按钮）")
    await load_data()
    await load_all()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())