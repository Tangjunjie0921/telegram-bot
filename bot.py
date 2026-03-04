import asyncio
import os
import json
import time
from collections import defaultdict
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

# ================== 基础配置 ==================
TOKEN = os.getenv("BOT_TOKEN")
MAIN_ADMIN = 8276405169  # 你的主管理员ID

if not TOKEN:
    raise ValueError("未检测到 BOT_TOKEN")

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

DATA_FILE = "data.json"

# ================== 数据持久化 ==================
def load_data():
    if not os.path.exists(DATA_FILE):
        return {
            "admins": [MAIN_ADMIN],
            "authorized_groups": [],
            "keywords": [],
            "global_blacklist": [],
            "user_blacklist": {},
            "settings": {
                "max_per_hour": 5,
                "max_per_day": 20,
                "max_responders": 6,
                "demand_expire_hours": 12
            }
        }
    with open(DATA_FILE, "r") as f:
        return json.load(f)

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)

data = load_data()

# ================== 内存需求数据 ==================
demands = {}
response_log_1h = defaultdict(list)
response_log_24h = defaultdict(list)
admin_lock = False
demand_counter = 0

# ================== 工具函数 ==================
def is_admin(uid):
    return uid in data["admins"]

def clean_expired_demands():
    now = time.time()
    expired = [d for d in demands if demands[d]["expire"] < now]
    for d in expired:
        del demands[d]

def clean_logs(uid):
    now = time.time()
    response_log_1h[uid] = [t for t in response_log_1h[uid] if now - t < 3600]
    response_log_24h[uid] = [t for t in response_log_24h[uid] if now - t < 86400]

def can_respond(uid):
    clean_logs(uid)
    if len(response_log_1h[uid]) >= data["settings"]["max_per_hour"]:
        return False, "⚠️ 一小时响应已达上限"
    if len(response_log_24h[uid]) >= data["settings"]["max_per_day"]:
        return False, "⚠️ 24小时响应已达上限"
    return True, ""

# ================== 群过滤 ==================
def group_allowed(chat_id):
    return chat_id in data["authorized_groups"]

# ================== 面板 ==================
@dp.message(Command("panel"))
async def panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="管理员管理", callback_data="admin_manage")],
        [InlineKeyboardButton(text="群组管理", callback_data="group_manage")],
        [InlineKeyboardButton(text="黑名单管理", callback_data="black_manage")],
        [InlineKeyboardButton(text="风控设置", callback_data="setting_manage")],
        [InlineKeyboardButton(text="关键词管理", callback_data="keyword_manage")]
    ])
    await message.reply("📊 控制面板", reply_markup=kb)

# ================== 发布需求 ==================
@dp.message(Command("demand"))
async def create_demand(message: Message):
    if message.chat.type not in ["group", "supergroup"]:
        return
    if not group_allowed(message.chat.id):
        return
    if message.from_user.is_bot:
        return

    global demand_counter
    clean_expired_demands()

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        return

    demand_counter += 1
    expire_time = time.time() + data["settings"]["demand_expire_hours"] * 3600

    demands[demand_counter] = {
        "owner": message.from_user.id,
        "text": parts[1],
        "responders": set(),
        "expire": expire_time
    }

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="响应", callback_data=f"respond_{demand_counter}")]
    ])

    await message.reply(f"📌 需求 #{demand_counter}\n{parts[1]}", reply_markup=kb)

# ================== 响应 ==================
@dp.callback_query(F.data.startswith("respond_"))
async def respond(callback: CallbackQuery):
    if callback.from_user.is_bot:
        return
    clean_expired_demands()

    demand_id = int(callback.data.split("_")[1])
    if demand_id not in demands:
        await callback.answer("⏳ 需求已过期", show_alert=True)
        return

    demand = demands[demand_id]
    uid = callback.from_user.id

    if uid in data["global_blacklist"]:
        await callback.answer("🚫 你已被管理员拉黑", show_alert=True)
        return

    if str(demand["owner"]) in data["user_blacklist"]:
        if uid in data["user_blacklist"][str(demand["owner"])]:
            await callback.answer("🚫 你已被发布者拉黑", show_alert=True)
            return

    ok, msg = can_respond(uid)
    if not ok:
        await callback.answer(msg, show_alert=True)
        return

    if len(demand["responders"]) >= data["settings"]["max_responders"]:
        await callback.answer("人数已满", show_alert=True)
        return

    if uid in demand["responders"]:
        await callback.answer("已响应过", show_alert=True)
        return

    demand["responders"].add(uid)
    now = time.time()
    response_log_1h[uid].append(now)
    response_log_24h[uid].append(now)

    # 拉黑按钮
    buttons = []
    if uid != demand["owner"]:
        if callback.from_user.id == demand["owner"] or is_admin(callback.from_user.id):
            buttons.append([InlineKeyboardButton(
                text="拉黑该用户",
                callback_data=f"block_{demand_id}_{uid}"
            )])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None

    await bot.send_message(demand["owner"],
                           f"👤 用户 {uid} 响应了你的需求 #{demand_id}",
                           reply_markup=kb)

    await callback.answer("响应成功")

# ================== 拉黑 ==================
@dp.callback_query(F.data.startswith("block_"))
async def block_user(callback: CallbackQuery):
    _, demand_id, target_id = callback.data.split("_")
    demand_id = int(demand_id)
    target_id = int(target_id)

    if demand_id not in demands:
        return

    if not (callback.from_user.id == demands[demand_id]["owner"] or is_admin(callback.from_user.id)):
        return

    # 管理员拉黑是全局
    if is_admin(callback.from_user.id):
        data["global_blacklist"].append(target_id)
    else:
        owner = str(demands[demand_id]["owner"])
        data["user_blacklist"].setdefault(owner, [])
        data["user_blacklist"][owner].append(target_id)

    save_data()
    await callback.answer("已拉黑", show_alert=True)

# ================== 启动 ==================
async def main():
    print("机器人启动成功")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())