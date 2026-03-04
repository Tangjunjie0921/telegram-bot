import asyncio
import json
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command

# ================== 配置 ==================
TOKEN = "YOUR_BOT_TOKEN_HERE"  # 替换为你的 token
DATA_FILE = "data.json"

# 初始管理员列表，可由管理员在面板中增减
ADMINS = [123456789]  # 替换为你的 Telegram 用户 ID

# 自动清理需求时间（小时）
DEMAND_EXPIRY_HOURS = 12

# ================== 数据管理 ==================
if not os.path.exists(DATA_FILE):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "demands": [],
            "user_blacklist": {},  # 用户A私有拉黑
            "global_blacklist": [],  # 管理员全局拉黑
            "keywords": [],
            "groups": [],
            "admins": ADMINS
        }, f, ensure_ascii=False, indent=2)

def load_data():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ================== Bot & Dispatcher ==================
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ================== 辅助函数 ==================
def is_admin(user_id):
    data = load_data()
    return user_id in data["admins"]

def clean_expired_demands():
    data = load_data()
    now = datetime.utcnow()
    new_demands = []
    for d in data["demands"]:
        created = datetime.fromisoformat(d["timestamp"])
        if now - created < timedelta(hours=DEMAND_EXPIRY_HOURS):
            new_demands.append(d)
    data["demands"] = new_demands
    save_data(data)

def get_user_blacklist(user_id):
    data = load_data()
    return set(data["user_blacklist"].get(str(user_id), []))

# ================== /demand 命令 ==================
@dp.message(Command(commands=["demand"]))
async def cmd_demand(message: types.Message):
    clean_expired_demands()
    data = load_data()
    demand_text = message.text.replace("/demand", "").strip()
    if not demand_text:
        await message.reply("请输入需求内容。")
        return

    demand = {
        "id": len(data["demands"]) + 1,
        "user_id": message.from_user.id,
        "text": demand_text,
        "timestamp": datetime.utcnow().isoformat(),
        "responses": []
    }
    data["demands"].append(demand)
    save_data(data)
    await message.reply(f"✅ 需求已发布: {demand_text}")

# ================== 关键词触发响应 ==================
@dp.message()
async def keyword_handler(message: types.Message):
    clean_expired_demands()
    data = load_data()
    text_lower = message.text.lower()

    # 检查是否在管理的群组
    if message.chat.type != "private" and message.chat.id not in data["groups"]:
        return

    # 检查全局黑名单
    if message.from_user.id in data["global_blacklist"]:
        return

    # 检查关键词
    for kw in data["keywords"]:
        if kw.lower() in text_lower:
            await message.reply(f"关键词触发: {kw}")
            return

# ================== 管理面板指令 ==================
@dp.message(Command(commands=["add_admin"]))
async def add_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        new_admin_id = int(message.text.split()[1])
        data = load_data()
        if new_admin_id not in data["admins"]:
            data["admins"].append(new_admin_id)
            save_data(data)
        await message.reply(f"✅ 已增加管理员: {new_admin_id}")
    except Exception:
        await message.reply("格式错误: /add_admin <用户ID>")

@dp.message(Command(commands=["remove_admin"]))
async def remove_admin(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        rem_id = int(message.text.split()[1])
        data = load_data()
        if rem_id in data["admins"]:
            data["admins"].remove(rem_id)
            save_data(data)
        await message.reply(f"✅ 已移除管理员: {rem_id}")
    except Exception:
        await message.reply("格式错误: /remove_admin <用户ID>")

# ================== 黑名单 ==================
@dp.message(Command(commands=["blacklist"]))
async def blacklist_user(message: types.Message):
    try:
        target_id = int(message.text.split()[1])
        data = load_data()
        if is_admin(message.from_user.id):
            # 管理员全局黑名单
            if target_id not in data["global_blacklist"]:
                data["global_blacklist"].append(target_id)
        else:
            # 用户A拉黑
            uid = str(message.from_user.id)
            if uid not in data["user_blacklist"]:
                data["user_blacklist"][uid] = []
            if target_id not in data["user_blacklist"][uid]:
                data["user_blacklist"][uid].append(target_id)
        save_data(data)
        await message.reply(f"✅ 用户 {target_id} 已拉黑")
    except Exception:
        await message.reply("格式错误: /blacklist <用户ID>")

# ================== 群组管理 ==================
@dp.message(Command(commands=["add_group"]))
async def add_group(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        gid = int(message.text.split()[1])
        data = load_data()
        if gid not in data["groups"]:
            data["groups"].append(gid)
            save_data(data)
        await message.reply(f"✅ 已增加群组: {gid}")
    except Exception:
        await message.reply("格式错误: /add_group <群组ID>")

@dp.message(Command(commands=["remove_group"]))
async def remove_group(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        gid = int(message.text.split()[1])
        data = load_data()
        if gid in data["groups"]:
            data["groups"].remove(gid)
            save_data(data)
        await message.reply(f"✅ 已移除群组: {gid}")
    except Exception:
        await message.reply("格式错误: /remove_group <群组ID>")

# ================== 关键词管理 ==================
@dp.message(Command(commands=["add_keyword"]))
async def add_keyword(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        kw = message.text.split()[1]
        data = load_data()
        if kw.lower() not in [k.lower() for k in data["keywords"]]:
            data["keywords"].append(kw)
            save_data(data)
        await message.reply(f"✅ 已增加关键词: {kw}")
    except Exception:
        await message.reply("格式错误: /add_keyword <关键词>")

@dp.message(Command(commands=["remove_keyword"]))
async def remove_keyword(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("❌ 你不是管理员")
        return
    try:
        kw = message.text.split()[1]
        data = load_data()
        data["keywords"] = [k for k in data["keywords"] if k.lower() != kw.lower()]
        save_data(data)
        await message.reply(f"✅ 已移除关键词: {kw}")
    except Exception:
        await message.reply("格式错误: /remove_keyword <关键词>")

# ================== 启动 ==================
async def main():
    clean_expired_demands()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())