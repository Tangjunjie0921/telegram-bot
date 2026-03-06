import asyncio
import json
import os
import time
import hashlib
from collections import deque
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions, ReplyKeyboardRemove
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

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
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

DATA_FILE = "/data/reports.json"
BIO_KEYWORDS_FILE = "/data/bio_keywords.json"
reports = {}
lock = asyncio.Lock()

exempt_users = {}

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

# ==================== FSM 状态 ====================
class AdminStates(StatesGroup):
    ChoosingGroup = State()
    InGroupMenu = State()
    AddingBioKw = State()
    DeletingBioKw = State()
    ListingBioKw = State()
    AddingDispKw = State()
    DeletingDispKw = State()
    ListingDispKw = State()

# ==================== 键盘工具 ====================
def get_group_selection_keyboard():
    buttons = []
    row = []
    for gid in sorted(GROUP_IDS):
        row.append(InlineKeyboardButton(text=f"群 {gid}", callback_data=f"group:{gid}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="← 返回", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_group_menu_keyboard(group_id: int):
    buttons = [
        [InlineKeyboardButton(text="添加简介敏感词", callback_data=f"add_bio:{group_id}")],
        [InlineKeyboardButton(text="删除简介敏感词", callback_data=f"del_bio:{group_id}")],
        [InlineKeyboardButton(text="查看简介敏感词", callback_data=f"list_bio:{group_id}")],
        [InlineKeyboardButton(text="添加显示名敏感词", callback_data=f"add_disp:{group_id}")],
        [InlineKeyboardButton(text="删除显示名敏感词", callback_data=f"del_disp:{group_id}")],
        [InlineKeyboardButton(text="查看显示名敏感词", callback_data=f"list_disp:{group_id}")],
        [InlineKeyboardButton(text="← 返回群组选择", callback_data="back_to_groups")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_confirm_keyboard(action: str):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ 确认", callback_data=f"confirm:{action}"),
        InlineKeyboardButton(text="❌ 取消", callback_data="cancel"),
    ]])

# ==================== /admin 入口 ====================
@router.message(Command("admin"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin(message: Message, state: FSMContext):
    try:
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await message.reply("👑 管理员控制面板\n请选择操作：", reply_markup=kb)
    except Exception as e:
        await message.reply(f"打开面板失败：{str(e)}")

# ==================== 群组选择 & 菜单导航 ====================
@router.callback_query(F.data == "select_group")
async def select_group(callback: CallbackQuery, state: FSMContext):
    try:
        if not GROUP_IDS:
            await callback.message.edit_text("当前没有任何监控群组")
            await callback.answer()
            return
        kb = get_group_selection_keyboard()
        await callback.message.edit_text("请选择要管理的群组：", reply_markup=kb)
        await state.set_state(AdminStates.ChoosingGroup)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.callback_query(F.data.startswith("group:"))
async def enter_group_menu(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            await callback.answer("无效群组", show_alert=True)
            return
        await state.update_data(group_id=group_id)
        kb = get_group_menu_keyboard(group_id)
        await callback.message.edit_text(f"正在管理群组 {group_id}\n请选择功能：", reply_markup=kb)
        await state.set_state(AdminStates.InGroupMenu)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.callback_query(F.data == "back_to_groups")
async def back_to_groups(callback: CallbackQuery, state: FSMContext):
    try:
        kb = get_group_selection_keyboard()
        await callback.message.edit_text("请选择要管理的群组：", reply_markup=kb)
        await state.set_state(AdminStates.ChoosingGroup)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await callback.message.edit_text("👑 管理员控制面板\n请选择操作：", reply_markup=kb)
        await state.clear()
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

# ==================== 简介关键词 - 添加 ====================
@router.callback_query(F.data.startswith("add_bio:"))
async def start_add_bio_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        await callback.message.edit_text(
            "请输入要添加的简介敏感词（一行一个，可多行）：",
            reply_markup=None
        )
        await callback.message.answer("请在下方输入，输入完发送。", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.AddingBioKw)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AddingBioKw))
async def process_add_bio_kw(message: Message, state: FSMContext):
    try:
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有有效关键词，请重新输入。")
            return
        text = "即将添加以下关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(text, reply_markup=get_confirm_keyboard("add_bio"))
        await state.update_data(words=words)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

@router.callback_query(F.data == "confirm:add_bio")
async def confirm_add_bio(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容", show_alert=True)
            return
        async with lock:
            added = [w for w in words if w not in BIO_KEYWORDS]
            BIO_KEYWORDS.extend(added)
            with open(BIO_KEYWORDS_FILE, "w", encoding="utf-8") as f:
                json.dump(BIO_KEYWORDS, f, ensure_ascii=False, indent=2)
        msg = f"✅ 已添加 {len(added)} 个简介关键词（总计：{len(BIO_KEYWORDS)}）"
        await callback.message.edit_text(msg)
        await state.clear()
        await callback.answer("添加成功")
    except Exception as e:
        await callback.message.edit_text(f"添加失败：{str(e)}")

# ==================== 简介关键词 - 删除 ====================
@router.callback_query(F.data.startswith("del_bio:"))
async def start_del_bio_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        await callback.message.edit_text(
            "请输入要删除的简介敏感词（一行一个，可多行）：",
            reply_markup=None
        )
        await callback.message.answer("请在下方输入要删除的词，输入完发送。", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.DeletingBioKw)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.DeletingBioKw))
async def process_del_bio_kw(message: Message, state: FSMContext):
    try:
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有有效关键词，请重新输入。")
            return
        text = "即将删除以下关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(text, reply_markup=get_confirm_keyboard("del_bio"))
        await state.update_data(words=words)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

@router.callback_query(F.data == "confirm:del_bio")
async def confirm_del_bio(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容", show_alert=True)
            return
        async with lock:
            removed = []
            for w in words:
                if w in BIO_KEYWORDS:
                    BIO_KEYWORDS.remove(w)
                    removed.append(w)
            if removed:
                with open(BIO_KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(BIO_KEYWORDS, f, ensure_ascii=False, indent=2)
        msg = f"✅ 已删除 {len(removed)} 个简介关键词（总计：{len(BIO_KEYWORDS)}）"
        await callback.message.edit_text(msg)
        await state.clear()
        await callback.answer("删除成功")
    except Exception as e:
        await callback.message.edit_text(f"删除失败：{str(e)}")

# ==================== 简介关键词 - 查看 ====================
@router.callback_query(F.data.startswith("list_bio:"))
async def list_bio_keywords(callback: CallbackQuery):
    try:
        async with lock:
            if not BIO_KEYWORDS:
                text = "当前没有任何简介敏感词"
            else:
                text = f"📋 当前简介敏感词（{len(BIO_KEYWORDS)} 个）：\n" + "\n".join(f"• {w}" for w in sorted(BIO_KEYWORDS))
        await callback.message.edit_text(text[:4000])
        await callback.answer("列表已更新")
    except Exception as e:
        await callback.message.edit_text(f"查看失败：{str(e)}")

# ==================== 显示名称关键词 - 添加 ====================
@router.callback_query(F.data.startswith("add_disp:"))
async def start_add_disp_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        await callback.message.edit_text(
            "请输入要添加的显示名称敏感词（一行一个，可多行）：",
            reply_markup=None
        )
        await callback.message.answer("请在下方输入，输入完发送。", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.AddingDispKw)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.AddingDispKw))
async def process_add_disp_kw(message: Message, state: FSMContext):
    try:
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有有效关键词，请重新输入。")
            return
        text = "即将添加以下关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(text, reply_markup=get_confirm_keyboard("add_disp"))
        await state.update_data(words=words)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

@router.callback_query(F.data == "confirm:add_disp")
async def confirm_add_disp(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容", show_alert=True)
            return
        async with lock:
            added = [w for w in words if w not in DISPLAY_NAME_KEYWORDS]
            DISPLAY_NAME_KEYWORDS.extend(added)
        msg = f"✅ 已添加 {len(added)} 个显示名关键词（总计：{len(DISPLAY_NAME_KEYWORDS)}）"
        await callback.message.edit_text(msg)
        await state.clear()
        await callback.answer("添加成功")
    except Exception as e:
        await callback.message.edit_text(f"添加失败：{str(e)}")

# ==================== 显示名称关键词 - 删除 ====================
@router.callback_query(F.data.startswith("del_disp:"))
async def start_del_disp_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        await state.update_data(group_id=group_id)
        await callback.message.edit_text(
            "请输入要删除的显示名称敏感词（一行一个，可多行）：",
            reply_markup=None
        )
        await callback.message.answer("请在下方输入要删除的词，输入完发送。", reply_markup=ReplyKeyboardRemove())
        await state.set_state(AdminStates.DeletingDispKw)
        await callback.answer()
    except Exception as e:
        await callback.answer(f"失败：{str(e)}", show_alert=True)

@router.message(StateFilter(AdminStates.DeletingDispKw))
async def process_del_disp_kw(message: Message, state: FSMContext):
    try:
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有有效关键词，请重新输入。")
            return
        text = "即将删除以下关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(text, reply_markup=get_confirm_keyboard("del_disp"))
        await state.update_data(words=words)
    except Exception as e:
        await message.reply(f"处理失败：{str(e)}")

@router.callback_query(F.data == "confirm:del_disp")
async def confirm_del_disp(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容", show_alert=True)
            return
        async with lock:
            removed = []
            for w in words:
                if w in DISPLAY_NAME_KEYWORDS:
                    DISPLAY_NAME_KEYWORDS.remove(w)
                    removed.append(w)
        msg = f"✅ 已删除 {len(removed)} 个显示名关键词（总计：{len(DISPLAY_NAME_KEYWORDS)}）"
        await callback.message.edit_text(msg)
        await state.clear()
        await callback.answer("删除成功")
    except Exception as e:
        await callback.message.edit_text(f"删除失败：{str(e)}")

# ==================== 显示名称关键词 - 查看 ====================
@router.callback_query(F.data.startswith("list_disp:"))
async def list_disp_keywords(callback: CallbackQuery):
    try:
        async with lock:
            if not DISPLAY_NAME_KEYWORDS:
                text = "当前没有任何显示名称敏感词"
            else:
                text = f"📋 当前显示名称敏感词（{len(DISPLAY_NAME_KEYWORDS)} 个）：\n" + "\n".join(f"• {w}" for w in sorted(DISPLAY_NAME_KEYWORDS))
        await callback.message.edit_text(text[:4000])
        await callback.answer("列表已更新")
    except Exception as e:
        await callback.message.edit_text(f"查看失败：{str(e)}")

# ==================== 原有群内功能完整保留 ====================
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
    print("🚀 第十二版启动成功（所有按钮已补全）")
    await load_data()
    await load_all()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())