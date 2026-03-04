import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

# ===== 配置 =====
TOKEN = os.environ["BOT_TOKEN"]
DEFAULT_ADMIN_ID = 8276405169
DATA_FILE = Path("data.json")

# ===== 初始化 =====
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== 数据结构 =====
if DATA_FILE.exists():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
else:
    data = {
        "admins": [DEFAULT_ADMIN_ID],
        "groups": [],
        "keywords": [],
        "user_blacklist": {},      # {发布者ID: [被拉黑的用户ID]}
        "global_blacklist": [],    # 管理员拉黑，作用全局
        "demands": [],             # 每条 demand: {id, user_id, text, timestamp, responders: []}
        "limits": {                # 用户限流
            "1h": {},
            "24h": {}
        },
        "responses": []            # {responder_id, demand_id, timestamp}
    }


# ===== 数据持久化 =====
def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ===== 工具函数 =====
def is_admin(user_id: int):
    return user_id in data["admins"]

def is_blacklisted(poster_id: int, user_id: int):
    if user_id in data["global_blacklist"]:
        return True
    return user_id in data["user_blacklist"].get(poster_id, [])

def clean_expired_demands():
    now = datetime.utcnow()
    data["demands"] = [
        d for d in data["demands"]
        if now <= datetime.fromisoformat(d["timestamp"]) + timedelta(hours=12)
    ]
    # 清理响应日志24小时
    data["responses"] = [
        r for r in data["responses"]
        if now <= datetime.fromisoformat(r["timestamp"]) + timedelta(hours=24)
    ]

def check_limits(user_id: int):
    now = datetime.utcnow()
    one_hour_ago = now - timedelta(hours=1)
    twenty_four_hour_ago = now - timedelta(hours=24)
    # 1h limit
    data["limits"]["1h"].setdefault(str(user_id), [])
    data["limits"]["1h"][str(user_id)] = [
        t for t in data["limits"]["1h"][str(user_id)]
        if datetime.fromisoformat(t) > one_hour_ago
    ]
    # 24h limit
    data["limits"]["24h"].setdefault(str(user_id), [])
    data["limits"]["24h"][str(user_id)] = [
        t for t in data["limits"]["24h"][str(user_id)]
        if datetime.fromisoformat(t) > twenty_four_hour_ago
    ]
    return len(data["limits"]["1h"][str(user_id)]), len(data["limits"]["24h"][str(user_id)])


# ===== /demand 发布需求 =====
@dp.message(Command(commands=["demand"]))
async def handle_demand(msg: types.Message):
    clean_expired_demands()
    if msg.chat.id not in data["groups"]:
        return
    text = msg.text[7:].strip()
    if not text:
        await msg.reply("请提供需求内容")
        return

    demand_id = len(data["demands"]) + 1
    demand = {
        "id": demand_id,
        "user_id": msg.from_user.id,
        "text": text,
        "timestamp": datetime.utcnow().isoformat(),
        "responders": []
    }
    data["demands"].append(demand)
    save_data()

    # 创建按钮
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton("响应", callback_data=f"respond:{demand_id}")]
        ]
    )
    await msg.reply(f"需求 #{demand_id}\n内容: {text}", reply_markup=kb)
    
    # ===== 响应按钮回调 =====
@dp.callback_query()
async def handle_buttons(query: CallbackQuery):
    clean_expired_demands()
    data_changed = False

    if query.data.startswith("respond:"):
        demand_id = int(query.data.split(":")[1])
        demand = next((d for d in data["demands"] if d["id"] == demand_id), None)
        if not demand:
            await query.answer("需求已过期")
            return
        user_id = query.from_user.id

        # 检查不能响应自己
        if user_id == demand["user_id"]:
            await query.answer("你不能响应自己的需求", show_alert=True)
            return

        # 检查拉黑
        if is_blacklisted(demand["user_id"], user_id):
            await query.answer("你被发布者拉黑或在全局黑名单中", show_alert=True)
            return

        # 限流检查
        limit1h, limit24h = check_limits(user_id)
        if limit1h >= 5:
            await query.answer("1小时响应次数已达上限", show_alert=True)
            return
        if limit24h >= 20:
            await query.answer("24小时响应次数已达上限", show_alert=True)
            return

        # 避免重复响应
        if user_id in demand["responders"]:
            await query.answer("你已响应过此需求", show_alert=True)
            return

        # 记录响应
        demand["responders"].append(user_id)
        now_iso = datetime.utcnow().isoformat()
        data["responses"].append({"responder_id": user_id, "demand_id": demand_id, "timestamp": now_iso})
        data["limits"]["1h"][str(user_id)].append(now_iso)
        data["limits"]["24h"][str(user_id)].append(now_iso)
        save_data()
        data_changed = True

        # 更新按钮显示数量
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(f"响应 ({len(demand['responders'])})", callback_data=f"respond:{demand_id}")]
            ]
        )
        await query.message.edit_reply_markup(kb)

        # 通知发布者可拉黑
        kb_blacklist = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(f"拉黑用户 {user_id}", callback_data=f"blacklist:{demand_id}:{user_id}")]
            ]
        )
        try:
            await bot.send_message(
                demand["user_id"],
                f"用户 {user_id} 响应了你的需求 #{demand_id}",
                reply_markup=kb_blacklist
            )
        except:
            pass
        await query.answer("响应成功")

    elif query.data.startswith("blacklist:"):
        parts = query.data.split(":")
        demand_id, target_id = int(parts[1]), int(parts[2])
        user_id = query.from_user.id

        # 添加到个人黑名单
        data["user_blacklist"].setdefault(user_id, [])
        if target_id not in data["user_blacklist"][user_id]:
            data["user_blacklist"][user_id].append(target_id)
            save_data()
            await query.answer(f"用户 {target_id} 已被拉黑", show_alert=True)
        else:
            await query.answer("该用户已在黑名单中", show_alert=True)

# ===== 关键词自动触发 =====
@dp.message()
async def keyword_trigger(msg: types.Message):
    if msg.chat.id not in data["groups"]:
        return
    if msg.from_user.is_bot or not msg.text:
        return
    clean_expired_demands()
    text_lower = msg.text.lower()
    for kw in data["keywords"]:
        if kw.lower() in text_lower:
            # 自动触发为 /demand
            demand_id = len(data["demands"]) + 1
            demand = {
                "id": demand_id,
                "user_id": msg.from_user.id,
                "text": msg.text,
                "timestamp": datetime.utcnow().isoformat(),
                "responders": []
            }
            data["demands"].append(demand)
            save_data()

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton("响应", callback_data=f"respond:{demand_id}")]
                ]
            )
            await msg.reply(f"⚡ 关键词触发需求 #{demand_id}\n内容: {msg.text}", reply_markup=kb)
            break

# ===== 管理员私聊面板 =====
@dp.message(Command(commands=["panel", "admin"]))
async def admin_panel(msg: types.Message):
    if msg.chat.type != "private":
        return
    if not is_admin(msg.from_user.id):
        await msg.reply("你不是管理员")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton("关键词管理", callback_data="admin_kw")],
            [InlineKeyboardButton("群组管理", callback_data="admin_group")],
            [InlineKeyboardButton("限流设置", callback_data="admin_limit")],
            [InlineKeyboardButton("全局黑名单", callback_data="admin_gb")],
            [InlineKeyboardButton("管理员管理", callback_data="admin_admin")],
            [InlineKeyboardButton("关闭面板", callback_data="admin_close")]
        ]
    )
    await msg.reply("🛠 管理员面板", reply_markup=kb)

# ===== 启动 =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())