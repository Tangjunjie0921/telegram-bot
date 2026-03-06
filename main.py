import asyncio
import json
import os
import time
import hashlib
from collections import deque
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatMemberStatus
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, ReplyKeyboardRemove, ChatMemberUpdated
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ==================== 配置 ====================
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
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

DATA_FILE = "/data/reports.json"
BIO_KEYWORDS_FILE = "/data/bio_keywords.json"
BLACKLIST_CONFIG_FILE = "/data/blacklist_config.json"
AUTOREPLY_RULES_FILE = "/data/autoreply_rules.json"  # 新增：自动回复规则
reports = {}
lock = asyncio.Lock()

exempt_users = {}
blacklist_config = {}

# 自动回复规则：群ID(str) → {"enabled": bool, "keywords": list, "reply_text": str, "buttons": list, "delete_user_sec": int, "delete_bot_sec": int, "reply_all": bool, "quote": bool}
autoreply_rules = {}

async def load_autoreply_rules():
    global autoreply_rules
    try:
        if os.path.exists(AUTOREPLY_RULES_FILE):
            with open(AUTOREPLY_RULES_FILE, "r", encoding="utf-8") as f:
                autoreply_rules = json.load(f)
    except Exception as e:
        print("加载自动回复规则失败:", e)

async def save_autoreply_rules():
    try:
        with open(AUTOREPLY_RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(autoreply_rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存自动回复规则失败:", e)

# 原有 load 函数...
async def load_blacklist_config():
    global blacklist_config
    try:
        if os.path.exists(BLACKLIST_CONFIG_FILE):
            with open(BLACKLIST_CONFIG_FILE, "r", encoding="utf-8") as f:
                blacklist_config = json.load(f)
    except Exception as e:
        print("加载黑名单配置失败:", e)

async def save_blacklist_config():
    try:
        with open(BLACKLIST_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(blacklist_config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("保存黑名单配置失败:", e)

async def load_bio_keywords():
    try:
        os.makedirs(os.path.dirname(BIO_KEYWORDS_FILE), exist_ok=True)
        if os.path.exists(BIO_KEYWORDS_FILE):
            with open(BIO_KEYWORDS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        default = ["qq:", "qq：", "qq号", "加qq", "扣扣", "微信", "wx:", "weixin", "加我微信", "wxid_", "幼女", "萝莉", "少妇", "人妻", "福利", "约炮", "onlyfans", "小红书", "抖音", "纸飞机", "机场", "http", "https", "t.me/", "@"]
        with open(BIO_KEYWORDS_FILE, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=2)
        return default
    except Exception as e:
        print("加载 bio 关键词失败:", e)
        return ["qq:", "微信", "幼女", "福利", "t.me/"]

BIO_KEYWORDS = []

DISPLAY_NAME_KEYWORDS = [
    "加v", "加微信", "加qq", "加扣", "福利加", "约", "约炮", "资源私聊", "私我", "私聊我",
    "飞机", "纸飞机", "福利", "外围", "反差", "嫩模", "学生妹", "空姐", "人妻", "熟女",
    "onlyfans", "of", "leak", "nudes", "十八+", "av"
]

async def load_all():
    global BIO_KEYWORDS
    BIO_KEYWORDS = await load_bio_keywords()
    print(f"bio 关键词加载完成: {len(BIO_KEYWORDS)} 个")
    print(f"显示名称关键词: {len(DISPLAY_NAME_KEYWORDS)} 个")
    await load_blacklist_config()
    await load_autoreply_rules()

class AdminStates(StatesGroup):
    ChoosingGroup = State()
    InGroupMenu = State()
    AddingBioKw = State()
    DeletingBioKw = State()
    AddingDispKw = State()
    DeletingDispKw = State()
    SettingBlacklist = State()
    AutoreplyMenu = State()  # 新增：自动回复主菜单
    AutoreplyEditKeywords = State()
    AutoreplyEditText = State()
    AutoreplyEditButtons = State()
    AutoreplyEditDelete = State()

# 键盘工具（子菜单加新按钮）
def get_group_menu_keyboard(group_id: int):
    buttons = [
        [InlineKeyboardButton(text="添加简介敏感词", callback_data=f"add_bio:{group_id}")],
        [InlineKeyboardButton(text="删除简介敏感词", callback_data=f"del_bio:{group_id}")],
        [InlineKeyboardButton(text="查看简介敏感词", callback_data=f"list_bio:{group_id}")],
        [InlineKeyboardButton(text="添加显示名敏感词", callback_data=f"add_disp:{group_id}")],
        [InlineKeyboardButton(text="删除显示名敏感词", callback_data=f"del_disp:{group_id}")],
        [InlineKeyboardButton(text="查看显示名敏感词", callback_data=f"list_disp:{group_id}")],
        [InlineKeyboardButton(text="退群自动拉黑", callback_data=f"blacklist:{group_id}")],
        [InlineKeyboardButton(text="自动回复规则", callback_data=f"autoreply:{group_id}")],
        [InlineKeyboardButton(text="← 返回主菜单", callback_data="back_to_main")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_autoreply_menu_keyboard(group_id: int):
    rule = autoreply_rules.get(str(group_id), {"enabled": False})
    status = "已开启" if rule.get("enabled", False) else "已关闭"
    buttons = [
        [InlineKeyboardButton(text=f"开关：{status}", callback_data=f"autoreply_toggle:{group_id}")],
        [InlineKeyboardButton(text="编辑关键词", callback_data=f"autoreply_keywords:{group_id}")],
        [InlineKeyboardButton(text="编辑回复内容", callback_data=f"autoreply_text:{group_id}")],
        [InlineKeyboardButton(text="编辑附加按钮", callback_data=f"autoreply_buttons:{group_id}")],
        [InlineKeyboardButton(text="设置删除延时", callback_data=f"autoreply_delete:{group_id}")],
        [InlineKeyboardButton(text="← 返回子菜单", callback_data=f"group:{group_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
    
# 自动回复规则入口
@router.callback_query(F.data.startswith("autoreply:"))
async def enter_autoreply_menu(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        kb = get_autoreply_menu_keyboard(group_id)
        await callback.message.edit_text("自动回复规则设置", reply_markup=kb)
        await state.set_state(AdminStates.AutoreplyMenu)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

# 开关
@router.callback_query(F.data.startswith("autoreply_toggle:"))
async def toggle_autoreply(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        str_gid = str(group_id)
        if str_gid not in autoreply_rules:
            autoreply_rules[str_gid] = {"enabled": False, "keywords": [], "reply_text": "", "buttons": [], "delete_user_sec": 0, "delete_bot_sec": 0, "reply_all": True, "quote": True}
        autoreply_rules[str_gid]["enabled"] = not autoreply_rules[str_gid].get("enabled", False)
        await save_autoreply_rules()
        kb = get_autoreply_menu_keyboard(group_id)
        await callback.message.edit_reply_markup(reply_markup=kb)
        await callback.answer("已切换开关")
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

# 编辑关键词
@router.callback_query(F.data.startswith("autoreply_keywords:"))
async def start_edit_keywords(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        str_gid = str(group_id)
        current = autoreply_rules.get(str_gid, {}).get("keywords", [])
        text = "当前关键词（一行一个）：\n" + "\n".join(current) if current else "暂无关键词"
        text += "\n\n请输入要添加/替换的关键词（一行一个），或发送 /clear 清空："
        await callback.message.edit_text(text, reply_markup=None)
        await callback.message.answer("输入完发送", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.AutoreplyEditKeywords)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AutoreplyEditKeywords))
async def process_edit_keywords(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        group_id = data.get('group_id')
        str_gid = str(group_id)
        text = message.text.strip()
        if text == "/clear":
            keywords = []
        else:
            keywords = [w.strip().lower() for w in text.splitlines() if w.strip()]
        if str_gid not in autoreply_rules:
            autoreply_rules[str_gid] = {"enabled": False}
        autoreply_rules[str_gid]["keywords"] = keywords
        await save_autoreply_rules()
        kb = get_autoreply_menu_keyboard(group_id)
        await message.reply(f"关键词已更新（{len(keywords)} 个）", reply_markup=kb)
        await state.set_state(AdminStates.AutoreplyMenu)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")
        
# 编辑回复内容
@router.callback_query(F.data.startswith("autoreply_text:"))
async def start_edit_text(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        str_gid = str(group_id)
        current = autoreply_rules.get(str_gid, {}).get("reply_text", "")
        text = "当前回复内容：\n" + (current or "暂无内容")
        text += "\n\n请输入新回复内容（支持变量如 {member} {userId}）："
        await callback.message.edit_text(text, reply_markup=None)
        await state.set_state(AdminStates.AutoreplyEditText)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AutoreplyEditText))
async def process_edit_text(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        group_id = data.get('group_id')
        str_gid = str(group_id)
        reply_text = message.text.strip()
        if str_gid not in autoreply_rules:
            autoreply_rules[str_gid] = {"enabled": False}
        autoreply_rules[str_gid]["reply_text"] = reply_text
        await save_autoreply_rules()
        kb = get_autoreply_menu_keyboard(group_id)
        await message.reply("回复内容已更新", reply_markup=kb)
        await state.set_state(AdminStates.AutoreplyMenu)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

# 编辑附加按钮（简单版：一行一个按钮文本）
@router.callback_query(F.data.startswith("autoreply_buttons:"))
async def start_edit_buttons(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        str_gid = str(group_id)
        current = autoreply_rules.get(str_gid, {}).get("buttons", [])
        text = "当前按钮（一行一个文本）：\n" + "\n".join(current) if current else "暂无按钮"
        text += "\n\n请输入新按钮列表（一行一个），或 /clear 清空："
        await callback.message.edit_text(text, reply_markup=None)
        await state.set_state(AdminStates.AutoreplyEditButtons)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AutoreplyEditButtons))
async def process_edit_buttons(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        group_id = data.get('group_id')
        str_gid = str(group_id)
        text = message.text.strip()
        if text == "/clear":
            buttons = []
        else:
            buttons = [w.strip() for w in text.splitlines() if w.strip()]
        if str_gid not in autoreply_rules:
            autoreply_rules[str_gid] = {"enabled": False}
        autoreply_rules[str_gid]["buttons"] = buttons
        await save_autoreply_rules()
        kb = get_autoreply_menu_keyboard(group_id)
        await message.reply(f"按钮已更新（{len(buttons)} 个）", reply_markup=kb)
        await state.set_state(AdminStates.AutoreplyMenu)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

# 设置删除延时
@router.callback_query(F.data.startswith("autoreply_delete:"))
async def start_edit_delete(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        str_gid = str(group_id)
        rule = autoreply_rules.get(str_gid, {})
        user_sec = rule.get("delete_user_sec", 0)
        bot_sec = rule.get("delete_bot_sec", 0)
        text = f"当前删除延时：\n用户消息：{user_sec}s（0=不删）\n机器人消息：{bot_sec}s（0=不删）\n\n请输入新延时（格式：用户秒 机器人秒，例如 3 5）："
        await callback.message.edit_text(text, reply_markup=None)
        await state.set_state(AdminStates.AutoreplyEditDelete)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AutoreplyEditDelete))
async def process_edit_delete(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        group_id = data.get('group_id')
        str_gid = str(group_id)
        text = message.text.strip()
        parts = text.split()
        if len(parts) != 2:
            await message.reply("格式错误，请输入两个数字：用户秒 机器人秒")
            return
        user_sec = int(parts[0])
        bot_sec = int(parts[1])
        if str_gid not in autoreply_rules:
            autoreply_rules[str_gid] = {"enabled": False}
        autoreply_rules[str_gid]["delete_user_sec"] = user_sec
        autoreply_rules[str_gid]["delete_bot_sec"] = bot_sec
        await save_autoreply_rules()
        kb = get_autoreply_menu_keyboard(group_id)
        await message.reply(f"删除延时已更新：用户 {user_sec}s，机器人 {bot_sec}s", reply_markup=kb)
        await state.set_state(AdminStates.AutoreplyMenu)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")
        
        
# ==================== 原有群内功能（完整保留，你之前跑通的版本） ====================
SHORT_MSG_THRESHOLD = 3
MIN_CONSECUTIVE_COUNT = 2
TIME_WINDOW_SECONDS = 60
FILL_GARBAGE_MIN_RAW_LEN = 12
FILL_GARBAGE_MAX_CLEAN_LEN = 8
FILL_SPACE_RATIO = 0.30
FILL_CHARS = set(r" .,，。！？*\\\~`-_=+[]{}()\"'\\|\n\t\r　")

user_short_msg_history = {}

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

def get_profile_hash(bio: str, full_name: str, username: str | None) -> str:
    profile_str = f"{bio}|{full_name}|{username or ''}"
    return hashlib.sha256(profile_str.encode('utf-8')).hexdigest()

@router.message(F.chat.id.in_(GROUP_IDS))
async def check_user_info(message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    user = message.from_user
    user_id = user.id

    async with lock:
        if user_id in exempt_users:
            try:
                chat_info = await bot.get_chat(user_id)
                current_hash = get_profile_hash(
                    (chat_info.bio or ""),
                    user.full_name or "",
                    user.username or ""
                )
                if current_hash == exempt_users[user_id]:
                    return
                else:
                    exempt_users.pop(user_id, None)
            except Exception:
                pass

    try:
        chat_info = await bot.get_chat(user_id)
        bio = (chat_info.bio or "").lower()

        has_link_in_bio = any(x in bio for x in ["http://", "https://", "t.me/", "@"])
        has_spam_in_bio = any(kw.lower() in bio for kw in BIO_KEYWORDS)
        bio_trigger = has_link_in_bio or has_spam_in_bio

        display_name = (user.full_name or "").lower()
        has_spam_in_display = any(kw.lower() in display_name for kw in DISPLAY_NAME_KEYWORDS)

        if bio_trigger or has_spam_in_display:
            reason_parts = []
            if has_link_in_bio: reason_parts.append("简介含链接")
            if has_spam_in_bio: reason_parts.append("简介含敏感词")
            if has_spam_in_display: reason_parts.append("显示名称含敏感词")

            reason_text = " + ".join(reason_parts)
            warning_text = (
                f"⚠️ 检测到疑似广告引流规避（{reason_text}）\n"
                f"用户ID: {user.id}\n"
                f"显示名称: {user.full_name}\n"
                f"举报数: 0"
            )

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}"),
                InlineKeyboardButton(text="误判/豁免 👮‍♂️", callback_data=f"exempt:{message.message_id}")
            ]])
            warning = await message.reply(warning_text, reply_markup=keyboard)

            async with lock:
                reports[message.message_id] = {
                    "warning_id": warning.message_id,
                    "suspect_id": user.id,
                    "chat_id": message.chat.id,
                    "reporters": set(),
                    "original_text": warning_text,
                    "original_message_id": message.message_id
                }
            await save_data()
    except Exception as e:
        print("用户信息检测异常:", e)

@router.callback_query(F.data.startswith("exempt:"))
async def handle_exempt(callback: CallbackQuery):
    try:
        original_id = int(callback.data.split(":", 1)[1])
        caller_id = callback.from_user.id
        chat_id = callback.message.chat.id

        if caller_id not in ADMIN_IDS:
            await callback.answer("仅管理员可操作", show_alert=True)
            return

        async with lock:
            if original_id not in reports:
                await callback.answer("记录已过期", show_alert=True)
                return
            data = reports[original_id]
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]

        suspect_user = await bot.get_chat(suspect_id)
        bio = (suspect_user.bio or "")
        full_name = f"{suspect_user.first_name or ''} {suspect_user.last_name or ''}".strip()
        username = suspect_user.username
        profile_hash = get_profile_hash(bio, full_name, username)

        async with lock:
            exempt_users[suspect_id] = profile_hash
            await bot.delete_message(chat_id, warning_id)

        await callback.answer("已豁免此人 👮‍♂️\n后续资料不变将不再检测", show_alert=True)

        async with lock:
            reports.pop(original_id, None)
        await save_data()

    except TelegramBadRequest as e:
        await callback.answer(f"操作失败: {str(e)}", show_alert=True)
    except Exception as e:
        print("豁免异常:", e)
        await callback.answer("操作失败", show_alert=True)

@router.message(F.chat.id.in_(GROUP_IDS), F.text)
async def detect_short_or_filled_spam(message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    user_id = message.from_user.id

    async with lock:
        if user_id in exempt_users:
            try:
                chat_info = await bot.get_chat(user_id)
                bio = (chat_info.bio or "")
                full_name = message.from_user.full_name or ""
                username = message.from_user.username
                current_hash = get_profile_hash(bio, full_name, username)
                if current_hash == exempt_users[user_id]:
                    return
                else:
                    exempt_users.pop(user_id, None)
            except Exception:
                pass

    text = message.text
    text_len = len(text)
    now = time.time()

    reason = None
    if text_len >= FILL_GARBAGE_MIN_RAW_LEN:
        cleaned = ''.join(c for c in text if c not in FILL_CHARS).strip()
        clean_len = len(cleaned)
        space_ratio = (text.count(" ") + text.count("　")) / text_len if text_len > 0 else 0
        if (clean_len <= FILL_GARBAGE_MAX_CLEAN_LEN) or (space_ratio >= FILL_SPACE_RATIO and clean_len <= 12):
            reason = "单次填充式规避"

    if not reason:
        if user_id not in user_short_msg_history:
            user_short_msg_history[user_id] = deque(maxlen=15)
        history = user_short_msg_history[user_id]
        while history and now - history[0][0] > TIME_WINDOW_SECONDS:
            history.popleft()
        history.append((now, text))
        recent = list(history)[-MIN_CONSECUTIVE_COUNT:]
        if len(recent) >= MIN_CONSECUTIVE_COUNT and all(len(t.strip()) <= SHORT_MSG_THRESHOLD for _, t in recent):
            reason = "连续极短消息"

    if reason:
        await send_warning(message, user_id, reason)

async def send_warning(message: Message, user_id: int, reason: str):
    try:
        warning_text = f"⚠️ 检测到疑似广告引流规避（{reason}）\n用户ID: {user_id}\n举报数: 0"
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}"),
            InlineKeyboardButton(text="误判/豁免 👮‍♂️", callback_data=f"exempt:{message.message_id}")
        ]])
        warning = await message.reply(warning_text, reply_markup=keyboard)
        async with lock:
            reports[message.message_id] = {
                "warning_id": warning.message_id,
                "suspect_id": user_id,
                "chat_id": message.chat.id,
                "reporters": set(),
                "original_text": warning_text,
                "original_message_id": message.message_id
            }
        await save_data()
    except Exception as e:
        print("发送警告失败:", e)

@router.callback_query(F.data.startswith("report:"))
async def handle_report(callback: CallbackQuery):
    try:
        original_id = int(callback.data.split(":", 1)[1])
        reporter_id = callback.from_user.id

        async with lock:
            if original_id not in reports:
                await callback.answer("该举报已过期", show_alert=True)
                return
            data = reports[original_id]
            if reporter_id in data["reporters"]:
                await callback.answer("您已经举报过了", show_alert=True)
                return
            data["reporters"].add(reporter_id)
            count = len(data["reporters"])
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]
            chat_id = data["chat_id"]
            original_text = data.get("original_text", "⚠️ 检测到疑似广告引流规避行为\n用户ID: 未知")

        lines = original_text.splitlines()
        prefix = "\n".join(lines[:2]) if len(lines) >= 2 else original_text

        if count >= 3:
            status = f"🚨 超3人举报 已通知管理员\n\n举报人数: {count}"
            await bot.send_message(list(ADMIN_IDS)[0], f"多人举报\n用户ID: {suspect_id}\n群组: {chat_id}")
        else:
            status = f"🚨 已有人举报\n\n举报人数: {count}"

        new_text = f"{prefix}\n{status}"

        keyboard_list = callback.message.reply_markup.inline_keyboard[:] if callback.message.reply_markup else []
        if not any("ban" in str(btn.callback_data) for row in keyboard_list for btn in row):
            keyboard_list.append([
                InlineKeyboardButton(text="封禁24小时（👮‍♀️）", callback_data=f"ban24h:{original_id}"),
                InlineKeyboardButton(text="永久封禁（👮‍♂️）", callback_data=f"banperm:{original_id}")
            ])
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_list)

        await bot.edit_message_text(chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=new_keyboard)
        await save_data()
        await callback.answer(f"举报成功！当前 {count} 人")
    except Exception as e:
        print("举报处理异常:", e)
        await callback.answer("操作失败", show_alert=True)

@router.callback_query(F.data.startswith(("ban24h:", "banperm:")))
async def handle_ban(callback: CallbackQuery):
    try:
        action, original_id_str = callback.data.split(":", 1)
        original_id = int(original_id_str)
        caller_id = callback.from_user.id
        chat_id = callback.message.chat.id

        if caller_id not in ADMIN_IDS:
            await callback.answer("仅管理员可操作", show_alert=True)
            return

        async with lock:
            if original_id not in reports:
                await callback.answer("记录已过期", show_alert=True)
                return
            data = reports[original_id]
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]
            original_message_id = data.get("original_message_id")
            original_text = data.get("original_text", "⚠️ 检测到疑似广告引流规避行为\n用户ID: 未知")

        until_date = int(time.time()) + 86400 if action == "ban24h" else None
        await bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=suspect_id,
            permissions=ChatPermissions(
                can_send_messages=False,
                can_send_media_messages=False,
                can_send_polls=False,
                can_send_other_messages=False,
                can_add_web_page_previews=False,
                can_change_info=False,
                can_invite_users=False,
                can_pin_messages=False
            ),
            until_date=until_date
        )

        ban_type = "禁言24小时" if action == "ban24h" else "永久限制"
        lines = original_text.splitlines()
        prefix = "\n".join(lines[:2]) if len(lines) >= 2 else original_text
        new_text = f"{prefix}\n🚨 已由管理员{ban_type}\n举报人数: {len(data['reporters'])}"

        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=warning_id,
            text=new_text,
            reply_markup=None
        )

        await callback.answer(f"已{ban_type}", show_alert=True)
        print(f"管理员 {caller_id} 对 {suspect_id} 执行 {ban_type} 在群 {chat_id}")

        async def delayed_delete():
            await asyncio.sleep(10)
            try:
                await bot.delete_message(chat_id, warning_id)
                print(f"删除警告消息 {warning_id}")
            except TelegramBadRequest as e:
                print(f"删除警告失败 {warning_id}: {e}")
            try:
                if original_message_id:
                    await bot.delete_message(chat_id, original_message_id)
                    print(f"删除用户原消息 {original_message_id}")
            except TelegramBadRequest as e:
                print(f"删除用户消息失败 {original_message_id}: {e}")

        asyncio.create_task(delayed_delete())

        async with lock:
            reports.pop(original_id, None)
        await save_data()

    except TelegramBadRequest as e:
        if "user_not_participant" in str(e).lower():
            await callback.answer("用户不在群组", show_alert=True)
        elif "not enough rights" in str(e).lower():
            await callback.answer("机器人缺少权限", show_alert=True)
        else:
            await callback.answer(f"操作失败: {str(e)}", show_alert=True)
    except Exception as e:
        print("封禁异常:", e)
        await callback.answer("操作失败", show_alert=True)

@router.message(Command("status"), F.chat.id.in_(GROUP_IDS), F.from_user.id.in_(ADMIN_IDS))
async def cmd_status(message: Message):
    async with lock:
        exempt_count = len(exempt_users)
    text = (
        f"✅ 机器人运行正常\n"
        f"👮 管理员数量: {len(ADMIN_IDS)}\n"
        f"📊 监控群组: {len(GROUP_IDS)}\n"
        f"📁 当前举报记录: {len(reports)} 条\n"
        f"🚫 简介敏感词数量: {len(BIO_KEYWORDS)} 个\n"
        f"🚫 显示名称敏感词数量: {len(DISPLAY_NAME_KEYWORDS)} 个\n"
        f"🛡️ 当前豁免用户: {exempt_count} 人"
    )
    await message.reply(text, disable_notification=True)

async def cleanup_deleted_messages():
    while True:
        await asyncio.sleep(300)
        to_remove = []
        async with lock:
            check_list = list(reports.items())
        for orig_id, data in check_list:
            try:
                test_msg = await bot.forward_message(
                    chat_id=list(ADMIN_IDS)[0],
                    from_chat_id=data["chat_id"],
                    message_id=orig_id
                )
                await bot.delete_message(list(ADMIN_IDS)[0], test_msg.message_id)
            except TelegramBadRequest as e:
                if "not found" in str(e).lower() or "message to forward not found" in str(e).lower():
                    try:
                        await bot.delete_message(data["chat_id"], data["warning_id"])
                        to_remove.append(orig_id)
                        print(f"同步删除警告: 原消息 {orig_id} 已删")
                    except Exception:
                        pass
        if to_remove:
            async with lock:
                for oid in to_remove:
                    reports.pop(oid, None)
            await save_data()
        await asyncio.sleep(1)

# ==================== 启动 ====================
async def main():
    print("🚀 机器人启动成功（完整版，含自动回复规则）")
    await load_data()
    await load_all()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
    