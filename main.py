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

# 豁免列表：user_id → profile_hash (bio|full_name|username)
exempt_users = {}  # 内存存储，重启丢失可接受

# ==================== bio 专用关键词 ====================
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
        print("加载 bio 关键词失败，使用内置:", e)
        return ["qq:", "微信", "幼女", "福利", "t.me/"]

BIO_KEYWORDS = []

# 显示名称专用关键词（独立，暂不持久化，如需可加文件）
DISPLAY_NAME_KEYWORDS = [
    "加v", "加微信", "加qq", "加扣", "福利加", "约", "约炮", "资源私聊", "私我", "私聊我",
    "飞机", "纸飞机", "福利", "外围", "反差", "嫩模", "学生妹", "空姐", "人妻", "熟女",
    "onlyfans", "of", "leak", "nudes", "十八+", "av"
]

async def load_all():
    global BIO_KEYWORDS
    BIO_KEYWORDS = await load_bio_keywords()
    print(f"🚀 bio 关键词加载完成: {len(BIO_KEYWORDS)} 个")
    print(f"显示名称专用关键词: {len(DISPLAY_NAME_KEYWORDS)} 个")

# ==================== FSM States ====================
class AdminStates(StatesGroup):
    ChoosingGroup = State()
    InGroupMenu = State()
    AddingBioKw = State()
    DeletingBioKw = State()
    AddingDispKw = State()
    DeletingDispKw = State()

# ==================== 工具函数 ====================
def get_group_button_text(chat_id: int) -> str:
    return f"群 {chat_id}"

def get_group_selection_keyboard():
    buttons = []
    row = []
    for gid in sorted(GROUP_IDS):
        row.append(InlineKeyboardButton(text=get_group_button_text(gid), callback_data=f"group:{gid}"))
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

# ==================== 主入口：/admin ====================
@router.message(Command("admin"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_admin(message: Message, state: FSMContext):
    try:
        await state.clear()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await message.reply(
            "👑 管理员控制面板\n请选择操作：",
            reply_markup=keyboard
        )
        print(f"[ADMIN] 用户 {message.from_user.id} 进入 /admin，已清空状态")
    except Exception as e:
        await message.reply(f"打开面板失败：{str(e)}")
        print(f"admin panel error: {e}")

# ==================== 群组选择 & 子菜单导航 ====================
@router.callback_query(F.data == "select_group")
async def select_group(callback: CallbackQuery, state: FSMContext):
    try:
        if not GROUP_IDS:
            await callback.message.edit_text("当前没有任何监控群组", reply_markup=None)
            await callback.answer()
            return
        keyboard = get_group_selection_keyboard()
        await callback.message.edit_text("请选择要管理的群组：", reply_markup=keyboard)
        await state.set_state(AdminStates.ChoosingGroup)
        await callback.answer("请选择群组")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入群组选择")
    except Exception as e:
        print(f"select_group error: {e}")
        try:
            await callback.answer(f"选择群组失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"选择群组出错：{str(e)}")

@router.callback_query(F.data.startswith("group:"))
async def enter_group_menu(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            raise ValueError("无效群组")
        await state.update_data(selected_group=group_id)
        keyboard = get_group_menu_keyboard(group_id)
        await callback.message.edit_text(
            f"正在管理群组 {group_id}\n请选择功能：",
            reply_markup=keyboard
        )
        await state.set_state(AdminStates.InGroupMenu)
        await callback.answer("已进入群管理菜单")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入群 {group_id} 管理菜单")
    except Exception as e:
        print(f"enter_group_menu error: {e}")
        try:
            await callback.answer(f"进入菜单失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"进入菜单出错：{str(e)}")

@router.callback_query(F.data == "back_to_groups")
async def back_to_groups(callback: CallbackQuery, state: FSMContext):
    try:
        keyboard = get_group_selection_keyboard()
        await callback.message.edit_text("请选择要管理的群组：", reply_markup=keyboard)
        await state.set_state(AdminStates.ChoosingGroup)
        await callback.answer("返回群组选择")
    except Exception as e:
        print(f"back_to_groups error: {e}")
        try:
            await callback.answer(f"返回失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"返回出错：{str(e)}")

@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await callback.message.edit_text("👑 管理员控制面板\n请选择操作：", reply_markup=keyboard)
        await state.clear()
        await callback.answer("返回主面板")
    except Exception as e:
        print(f"back_to_main error: {e}")
        try:
            await callback.answer(f"返回主菜单失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"返回主菜单出错：{str(e)}")

# ==================== 添加简介关键词 ====================
@router.callback_query(F.data.startswith("add_bio:"))
async def start_add_bio_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            raise ValueError("无效群组")
        await state.update_data(action="add_bio", group_id=group_id)
        await callback.message.edit_text(
            "请输入要**添加**的简介敏感词（一行一个，可多行）：",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.message.answer("请直接在下方输入关键词，输入完按发送。")
        await state.set_state(AdminStates.AddingBioKw)
        await callback.answer("已进入输入模式，请发送关键词")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入添加简介关键词状态")
    except Exception as e:
        print(f"start_add_bio_kw error: {type(e).__name__} {str(e)}")
        try:
            await callback.answer(f"操作失败：{str(e)[:100]}", show_alert=True)
        except Exception as ae:
            print(f"answer 失败: {ae}")
            await callback.message.answer(f"抱歉，操作失败：{str(e)[:200]}\n请 /cancel 后重试。")

# ==================== 处理添加简介关键词输入 ====================
@router.message(StateFilter(AdminStates.AddingBioKw))
async def process_add_bio_kw(message: Message, state: FSMContext):
    print(f"[DEBUG 输入] 用户 {message.from_user.id} | 状态: AddingBioKw | 内容: {message.text}")
    try:
        data = await state.get_data()
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有输入有效关键词，请重新输入。")
            return

        text = "即将添加以下简介关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(
            text,
            reply_markup=get_confirm_keyboard("add_bio")
        )
        await state.update_data(words=words)
        print(f"[DEBUG] 已准备添加 {len(words)} 个词，等待确认")
    except Exception as e:
        print(f"process_add_bio_kw 异常: {type(e).__name__} {str(e)}")
        await message.reply(f"处理出错：{str(e)}\n请重新输入，或 /cancel 退出。")

@router.callback_query(F.data == "confirm:add_bio")
async def confirm_add_bio(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容可添加", show_alert=True)
            return

        async with lock:
            added = []
            for w in words:
                if w not in BIO_KEYWORDS:
                    BIO_KEYWORDS.append(w)
                    added.append(w)
            if added:
                with open(BIO_KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(BIO_KEYWORDS, f, ensure_ascii=False, indent=2)

        msg = f"✅ 已添加 {len(added)} 个简介关键词（当前总数：{len(BIO_KEYWORDS)}）"
        await callback.message.edit_text(msg, reply_markup=None)
        await state.clear()
        await callback.answer("添加成功")
        print(f"[SUCCESS] 用户 {callback.from_user.id} 添加了 {len(added)} 个简介词")
    except Exception as e:
        print(f"confirm_add_bio error: {e}")
        try:
            await callback.answer(f"添加失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"添加失败：{str(e)}")

# ==================== 删除简介关键词（类似结构，已修复 answer 重复问题） ====================
@router.callback_query(F.data.startswith("del_bio:"))
async def start_del_bio_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            raise ValueError("无效群组")
        await state.update_data(action="del_bio", group_id=group_id)
        await callback.message.edit_text(
            "请输入要**删除**的简介敏感词（一行一个，可多行）：",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.message.answer("请直接在下方输入要删除的关键词")
        await state.set_state(AdminStates.DeletingBioKw)
        await callback.answer("已进入删除模式，请发送关键词")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入删除简介关键词状态")
    except Exception as e:
        print(f"start_del_bio_kw error: {type(e).__name__} {str(e)}")
        try:
            await callback.answer(f"操作失败：{str(e)[:100]}", show_alert=True)
        except Exception as ae:
            print(f"answer 失败: {ae}")
            await callback.message.answer(f"抱歉，操作失败：{str(e)[:200]}\n请 /cancel 后重试。")

@router.message(StateFilter(AdminStates.DeletingBioKw))
async def process_del_bio_kw(message: Message, state: FSMContext):
    print(f"[DEBUG 输入] 用户 {message.from_user.id} | 状态: DeletingBioKw | 内容: {message.text}")
    try:
        data = await state.get_data()
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有输入有效关键词，请重新输入。")
            return

        text = "即将删除以下简介关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(
            text,
            reply_markup=get_confirm_keyboard("del_bio")
        )
        await state.update_data(words=words)
        print(f"[DEBUG] 已准备删除 {len(words)} 个词，等待确认")
    except Exception as e:
        print(f"process_del_bio_kw 异常: {type(e).__name__} {str(e)}")
        await message.reply(f"处理出错：{str(e)}\n请重新输入，或 /cancel 退出。")

@router.callback_query(F.data == "confirm:del_bio")
async def confirm_del_bio(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容可删除", show_alert=True)
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

        msg = f"✅ 已删除 {len(removed)} 个简介关键词（当前总数：{len(BIO_KEYWORDS)}）"
        await callback.message.edit_text(msg, reply_markup=None)
        await state.clear()
        await callback.answer("删除成功")
        print(f"[SUCCESS] 用户 {callback.from_user.id} 删除了 {len(removed)} 个简介词")
    except Exception as e:
        print(f"confirm_del_bio error: {e}")
        try:
            await callback.answer(f"删除失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"删除失败：{str(e)}")

# ==================== 显示名称关键词管理（类似，已修复） ====================
# 添加显示名
@router.callback_query(F.data.startswith("add_disp:"))
async def start_add_disp_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            raise ValueError("无效群组")
        await state.update_data(action="add_disp", group_id=group_id)
        await callback.message.edit_text(
            "请输入要**添加**的显示名称敏感词（一行一个，可多行）：",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.message.answer("请直接在下方输入关键词")
        await state.set_state(AdminStates.AddingDispKw)
        await callback.answer("已进入输入模式，请发送关键词")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入添加显示名称关键词状态")
    except Exception as e:
        print(f"start_add_disp_kw error: {type(e).__name__} {str(e)}")
        try:
            await callback.answer(f"操作失败：{str(e)[:100]}", show_alert=True)
        except Exception as ae:
            print(f"answer 失败: {ae}")
            await callback.message.answer(f"抱歉，操作失败：{str(e)[:200]}\n请 /cancel 后重试。")

@router.message(StateFilter(AdminStates.AddingDispKw))
async def process_add_disp_kw(message: Message, state: FSMContext):
    print(f"[DEBUG 输入] 用户 {message.from_user.id} | 状态: AddingDispKw | 内容: {message.text}")
    try:
        data = await state.get_data()
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有输入有效关键词，请重新输入。")
            return

        text = "即将添加以下显示名称关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(
            text,
            reply_markup=get_confirm_keyboard("add_disp")
        )
        await state.update_data(words=words)
        print(f"[DEBUG] 已准备添加 {len(words)} 个显示名称词，等待确认")
    except Exception as e:
        print(f"process_add_disp_kw 异常: {type(e).__name__} {str(e)}")
        await message.reply(f"处理出错：{str(e)}\n请重新输入，或 /cancel 退出。")

@router.callback_query(F.data == "confirm:add_disp")
async def confirm_add_disp(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容可添加", show_alert=True)
            return

        async with lock:
            added = []
            for w in words:
                if w not in DISPLAY_NAME_KEYWORDS:
                    DISPLAY_NAME_KEYWORDS.append(w)
                    added.append(w)

        msg = f"✅ 已添加 {len(added)} 个显示名称关键词（当前总数：{len(DISPLAY_NAME_KEYWORDS)}）"
        await callback.message.edit_text(msg, reply_markup=None)
        await state.clear()
        await callback.answer("添加成功")
        print(f"[SUCCESS] 用户 {callback.from_user.id} 添加了 {len(added)} 个显示名称词")
    except Exception as e:
        print(f"confirm_add_disp error: {e}")
        try:
            await callback.answer(f"添加失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"添加失败：{str(e)}")

# 删除显示名
@router.callback_query(F.data.startswith("del_disp:"))
async def start_del_disp_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            raise ValueError("无效群组")
        await state.update_data(action="del_disp", group_id=group_id)
        await callback.message.edit_text(
            "请输入要**删除**的显示名称敏感词（一行一个，可多行）：",
            reply_markup=ReplyKeyboardRemove()
        )
        await callback.message.answer("请直接在下方输入要删除的关键词")
        await state.set_state(AdminStates.DeletingDispKw)
        await callback.answer("已进入删除模式，请发送关键词")
        print(f"[DEBUG] 用户 {callback.from_user.id} 进入删除显示名称关键词状态")
    except Exception as e:
        print(f"start_del_disp_kw error: {type(e).__name__} {str(e)}")
        try:
            await callback.answer(f"操作失败：{str(e)[:100]}", show_alert=True)
        except Exception as ae:
            print(f"answer 失败: {ae}")
            await callback.message.answer(f"抱歉，操作失败：{str(e)[:200]}\n请 /cancel 后重试。")

@router.message(StateFilter(AdminStates.DeletingDispKw))
async def process_del_disp_kw(message: Message, state: FSMContext):
    print(f"[DEBUG 输入] 用户 {message.from_user.id} | 状态: DeletingDispKw | 内容: {message.text}")
    try:
        data = await state.get_data()
        words = [w.strip().lower() for w in (message.text or "").splitlines() if w.strip()]
        if not words:
            await message.reply("没有输入有效关键词，请重新输入。")
            return

        text = "即将删除以下显示名称关键词：\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(
            text,
            reply_markup=get_confirm_keyboard("del_disp")
        )
        await state.update_data(words=words)
        print(f"[DEBUG] 已准备删除 {len(words)} 个显示名称词，等待确认")
    except Exception as e:
        print(f"process_del_disp_kw 异常: {type(e).__name__} {str(e)}")
        await message.reply(f"处理出错：{str(e)}\n请重新输入，或 /cancel 退出。")

@router.callback_query(F.data == "confirm:del_disp")
async def confirm_del_disp(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容可删除", show_alert=True)
            return

        async with lock:
            removed = []
            for w in words:
                if w in DISPLAY_NAME_KEYWORDS:
                    DISPLAY_NAME_KEYWORDS.remove(w)
                    removed.append(w)

        msg = f"✅ 已删除 {len(removed)} 个显示名称关键词（当前总数：{len(DISPLAY_NAME_KEYWORDS)}）"
        await callback.message.edit_text(msg, reply_markup=None)
        await state.clear()
        await callback.answer("删除成功")
        print(f"[SUCCESS] 用户 {callback.from_user.id} 删除了 {len(removed)} 个显示名称词")
    except Exception as e:
        print(f"confirm_del_disp error: {e}")
        try:
            await callback.answer(f"删除失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"删除失败：{str(e)}")

@router.callback_query(F.data.startswith("list_disp:"))
async def list_disp_keywords(callback: CallbackQuery):
    try:
        async with lock:
            if not DISPLAY_NAME_KEYWORDS:
                text = "当前没有任何显示名称敏感词"
            else:
                text = f"📋 当前显示名称敏感词（{len(DISPLAY_NAME_KEYWORDS)} 个）:\n" + "\n".join(f"• {w}" for w in sorted(DISPLAY_NAME_KEYWORDS))
        await callback.message.edit_text(text[:4000], reply_markup=None)
        await callback.answer("列表已显示")
    except Exception as e:
        print(f"list_disp error: {e}")
        try:
            await callback.answer(f"获取列表失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"获取列表失败：{str(e)}")

# ==================== 取消按钮通用处理 ====================
@router.callback_query(F.data == "cancel")
async def cancel_action(callback: CallbackQuery, state: FSMContext):
    try:
        await state.clear()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await callback.message.edit_text("操作已取消。\n返回主面板：", reply_markup=keyboard)
        await callback.answer("已取消")
    except Exception as e:
        print(f"cancel_action error: {e}")
        try:
            await callback.answer(f"取消失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"取消操作失败：{str(e)}")

# ==================== 取消命令 ====================
@router.message(Command("cancel"), F.chat.type == "private", F.from_user.id.in_(ADMIN_IDS))
async def cmd_cancel(message: Message, state: FSMContext):
    try:
        await state.clear()
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await message.reply("已取消当前操作，返回主菜单。", reply_markup=keyboard)
        print(f"[CANCEL] 用户 {message.from_user.id} 取消操作")
    except Exception as e:
        await message.reply(f"取消失败：{str(e)}")
        print(f"cmd_cancel error: {e}")

# ==================== 调试：任何私聊消息兜底 ====================
@router.message(F.chat.type == "private")
async def debug_private_message(message: Message, state: FSMContext):
    current_state = await state.get_state()
    text_preview = message.text[:50] + "..." if message.text else "[非文本消息]"
    print(f"[DEBUG 私聊消息] 用户: {message.from_user.id} | 状态: {current_state} | 内容: {text_preview}")
    
    if current_state in [
        AdminStates.AddingBioKw.state,
        AdminStates.DeletingBioKw.state,
        AdminStates.AddingDispKw.state,
        AdminStates.DeletingDispKw.state,
    ]:
        await message.reply("系统收到你的输入，但处理似乎卡住了。\n请再发一次关键词，或输入 /cancel 退出。")

# ==================== 第11版原有功能完整保留 ====================
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
        if gid.strip():
            GROUP_IDS.add(int(gid.strip()))
    for uid in os.getenv("ADMIN_IDS", "").strip().split():
        if uid.strip():
            ADMIN_IDS.add(int(uid.strip()))
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

# 豁免列表：user_id → profile_hash
exempt_users = {}

# ==================== bio 关键词 ====================
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
        print("加载 bio 关键词失败，使用内置:", e)
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
    print(f"🚀 bio 关键词加载完成: {len(BIO_KEYWORDS)} 个")
    print(f"显示名称关键词: {len(DISPLAY_NAME_KEYWORDS)} 个")

# ==================== FSM 状态 ====================
class AdminStates(StatesGroup):
    ChoosingGroup = State()
    InGroupMenu = State()
    AddingBioKw = State()
    DeletingBioKw = State()
    AddingDispKw = State()
    DeletingDispKw = State()

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
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="管理群组设置", callback_data="select_group")
        ]])
        await message.reply("👑 管理员控制面板\n请选择操作：", reply_markup=keyboard)
        print(f"[ADMIN] 用户 {message.from_user.id} 进入面板")
    except Exception as e:
        await message.reply(f"打开面板失败：{str(e)}")
        print(f"cmd_admin error: {e}")

# ==================== 回调处理（统一规范：answer 只一次） ====================
@router.callback_query(F.data == "select_group")
async def select_group(callback: CallbackQuery, state: FSMContext):
    try:
        if not GROUP_IDS:
            await callback.message.edit_text("无监控群组")
            await callback.answer()
            return
        kb = get_group_selection_keyboard()
        await callback.message.edit_text("请选择群组：", reply_markup=kb)
        await state.set_state(AdminStates.ChoosingGroup)
        await callback.answer("请选择群组")
    except Exception as e:
        print(f"select_group error: {e}")
        try:
            await callback.answer(f"失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"选择失败：{str(e)}")

@router.callback_query(F.data.startswith("group:"))
async def enter_group_menu(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            await callback.answer("无效群组", show_alert=True)
            return
        await state.update_data(selected_group=group_id)
        kb = get_group_menu_keyboard(group_id)
        await callback.message.edit_text(f"管理群 {group_id}：", reply_markup=kb)
        await state.set_state(AdminStates.InGroupMenu)
        await callback.answer("已进入管理")
    except Exception as e:
        print(f"enter_group_menu error: {e}")
        try:
            await callback.answer(f"失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"进入失败：{str(e)}")

# back_to_groups 和 back_to_main 类似处理，省略重复代码，但实际部署时需完整添加

# ==================== 添加简介关键词（核心修复点） ====================
@router.callback_query(F.data.startswith("add_bio:"))
async def start_add_bio_kw(callback: CallbackQuery, state: FSMContext):
    try:
        group_id = int(callback.data.split(":", 1)[1])
        if group_id not in GROUP_IDS:
            await callback.answer("无效群组", show_alert=True)
            return
        await state.update_data(group_id=group_id)
        await callback.message.edit_text(
            "请输入要添加的简介敏感词（一行一个）：",
            reply_markup=None  # 关键修复：不能用 ReplyKeyboardRemove
        )
        await callback.message.answer(
            "请在下方输入关键词，输入完发送。",
            reply_markup=ReplyKeyboardRemove()  # 在新消息移除键盘
        )
        await state.set_state(AdminStates.AddingBioKw)
        await callback.answer("请输入关键词")
        print(f"[DEBUG] 进入添加 bio kw，用户 {callback.from_user.id}")
    except Exception as e:
        print(f"start_add_bio_kw error: {e}")
        try:
            await callback.answer(f"操作失败：{str(e)[:100]}", show_alert=True)
        except:
            await callback.message.answer(f"失败：{str(e)} 请 /cancel 重试")

@router.message(StateFilter(AdminStates.AddingBioKw))
async def process_add_bio_kw(message: Message, state: FSMContext):
    try:
        words = [w.strip().lower() for w in message.text.splitlines() if w.strip()]
        if not words:
            await message.reply("无有效词，请重新输入")
            return
        text = "确认添加？\n" + "\n".join(f"• {w}" for w in words)
        await message.reply(text, reply_markup=get_confirm_keyboard("add_bio"))
        await state.update_data(words=words)
    except Exception as e:
        await message.reply(f"输入处理失败：{str(e)}")
        print(f"process_add_bio_kw error: {e}")

@router.callback_query(F.data == "confirm:add_bio")
async def confirm_add_bio(callback: CallbackQuery, state: FSMContext):
    try:
        data = await state.get_data()
        words = data.get("words", [])
        if not words:
            await callback.answer("无内容", show_alert=True)
            return
        async with lock:
            added_count = 0
            for w in words:
                if w not in BIO_KEYWORDS:
                    BIO_KEYWORDS.append(w)
                    added_count += 1
            if added_count > 0:
                with open(BIO_KEYWORDS_FILE, "w", encoding="utf-8") as f:
                    json.dump(BIO_KEYWORDS, f, ensure_ascii=False, indent=2)
        await callback.message.edit_text(f"✅ 添加 {added_count} 个词（总 {len(BIO_KEYWORDS)}）")
        await state.clear()
        await callback.answer("成功")
    except Exception as e:
        await callback.message.edit_text(f"添加失败：{str(e)}")
        print(f"confirm_add_bio error: {e}")

# 其他回调（如 del_bio、add_disp 等）类似处理：edit_text reply_markup=None + answer 新消息移除键盘

# ==================== 其余原有功能完整保留（从 check_user_info 到 main） ====================
# 请把你原代码中从 check_user_info 开始的所有群内逻辑、handle_exempt、detect_short_or_filled_spam、send_warning、handle_report、handle_ban、cmd_status、cleanup_deleted_messages、load_data、save_data、get_profile_hash 直接复制到这里
# 为了完整性，我在这里简要占位，但实际部署时必须把它们全部放进来，不能删减

# 示例占位（实际替换成你原代码）
@router.message(F.chat.id.in_(GROUP_IDS))
async def check_user_info(message: Message):
    # 你原有的完整实现
    pass

# ... 其他函数全部放这里 ...

async def main():
    print("🚀 最终修复版启动")
    await load_data()
    await load_all()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())
    
    print("🚀 第十二版（已修复 callback answer 重复问题 + 调试日志）启动成功")
    await load_data()
    await load_all()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())