import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from aiogram import Bot, Dispatcher, types
from aiogram.filters.command import Command  # 修复导入

# ===== 配置 =====
import os
TOKEN = os.getenv("BOT_TOKEN", "").strip()  # 从环境变量获取 token
ADMIN_IDS = [123456789]  # 默认管理员列表，可在面板增加
DATA_FILE = Path("data.json")

# ===== 初始化 =====
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== 数据结构 =====
data = {
    "groups": [],
    "keywords": [],
    "demands": [],
    "user_blacklist": {},
    "global_blacklist": [],
    "admins": ADMIN_IDS.copy()
}

# ===== 数据持久化 =====
def save_data():
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

if DATA_FILE.exists():
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        data.update(json.load(f))

# ===== 辅助函数 =====
def is_admin(user_id):
    return user_id in data["admins"]

def is_blacklisted(user_id, target_id):
    return target_id in data["user_blacklist"].get(user_id, []) or target_id in data["global_blacklist"]

def clean_expired_demands():
    now = datetime.utcnow()
    data["demands"] = [
        d for d in data["demands"]
        if now <= datetime.fromisoformat(d["timestamp"]) + timedelta(hours=12)
    ]

# ===== /demand 发布需求 =====
@dp.message(Command(commands=["demand"]))
async def handle_demand(msg: types.Message):
    clean_expired_demands()
    text = msg.text[7:].strip()
    if not text:
        await msg.reply("请提供需求内容")
        return
    data["demands"].append({
        "id": len(data["demands"]) + 1,
        "user_id": msg.from_user.id,
        "text": text,
        "timestamp": datetime.utcnow().isoformat()
    })
    save_data()
    await msg.reply(f"✅ 需求已发布: {text}")

# ===== 关键词匹配 =====
@dp.message()
async def keyword_match(msg: types.Message):
    if msg.from_user.is_bot or not msg.text:
        return
    clean_expired_demands()
    text_lower = msg.text.lower()
    for keyword in data["keywords"]:
        if keyword.lower() in text_lower:
            await msg.reply(f"⚡ 触发关键词: {keyword}")
            break

# ===== 响应需求 =====
@dp.message()
async def respond_demand(msg: types.Message):
    if msg.from_user.is_bot or not msg.text:
        return
    clean_expired_demands()
    for d in data["demands"]:
        if msg.from_user.id == d["user_id"]:
            continue
        if is_blacklisted(d["user_id"], msg.from_user.id):
            await msg.reply("你已被拉黑，无法响应此用户的需求")
            continue
        if d["text"].lower() in msg.text.lower():
            await msg.reply(f"✅ 响应用户 {d['user_id']} 的需求: {d['text']}")

# ===== 管理面板 =====
@dp.message(Command(commands=["panel"]))
async def admin_panel(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.reply("你不是管理员")
        return
    text = (
        f"🛠 管理面板\n"
        f"群组: {data['groups']}\n"
        f"关键词: {data['keywords']}\n"
        f"全局黑名单: {data['global_blacklist']}\n"
        f"管理员: {data['admins']}\n"
        f"发送 /addgroup 群ID 或 /rmgroup 群ID 来管理群组\n"
        f"发送 /addkeyword 关键词 或 /rmkeyword 关键词 来管理关键词\n"
        f"发送 /blacklist 用户ID 或 /rmblacklist 用户ID 来管理全局黑名单\n"
        f"发送 /addadmin 用户ID 或 /rmadmin 用户ID 来管理管理员"
    )
    await msg.reply(text)

# ===== 各种管理命令 =====
async def safe_int_split(msg_text, default=None):
    parts = msg_text.split()
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return default

# 下面各管理命令保持不变，只是 Command 导入修复即可

@dp.message(Command(commands=["addgroup"]))
async def add_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid and gid not in data["groups"]:
        data["groups"].append(gid)
        save_data()
        await msg.reply(f"✅ 添加群组 {gid}")

@dp.message(Command(commands=["rmgroup"]))
async def rm_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid and gid in data["groups"]:
        data["groups"].remove(gid)
        save_data()
        await msg.reply(f"✅ 移除群组 {gid}")

# ... 其他命令保持原样，只需保证 Command 导入路径正确 ...

# ===== 启动 =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())