import asyncio
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
import logging
import os

# ------------------- 配置 -------------------
TOKEN = "你的bot_token在这里"
ADMIN_IDS = {123456789}  # 初始管理员ID，可在面板增删
DB_PATH = "bot.db"
EXPIRY_HOURS = 12

logging.basicConfig(level=logging.INFO)
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ------------------- 数据库 -------------------
conn = sqlite3.connect(DB_PATH)
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS demands(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    created_at TEXT
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS responses(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    demand_id INTEGER,
    user_id INTEGER,
    responded_at TEXT
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS user_blacklist(
    blocker_id INTEGER,
    blocked_id INTEGER
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS admin_blacklist(
    blocked_id INTEGER
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS admins(
    user_id INTEGER PRIMARY KEY
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS groups(
    group_id INTEGER PRIMARY KEY
)
""")
c.execute("""
CREATE TABLE IF NOT EXISTS keywords(
    keyword TEXT PRIMARY KEY
)
""")
conn.commit()

# 初始管理员写入
for admin_id in ADMIN_IDS:
    c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (admin_id,))
conn.commit()

# ------------------- 辅助函数 -------------------
def is_admin(user_id: int) -> bool:
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    return c.fetchone() is not None

def is_blocked(blocker, blocked) -> bool:
    c.execute("SELECT 1 FROM user_blacklist WHERE blocker_id=? AND blocked_id=?", (blocker, blocked))
    if c.fetchone(): return True
    c.execute("SELECT 1 FROM admin_blacklist WHERE blocked_id=?", (blocked,))
    return c.fetchone() is not None

def clean_expired_demands():
    expiry_time = datetime.utcnow() - timedelta(hours=EXPIRY_HOURS)
    c.execute("DELETE FROM demands WHERE created_at<?", (expiry_time.isoformat(),))
    c.execute("DELETE FROM responses WHERE demand_id NOT IN (SELECT id FROM demands)")
    conn.commit()

# ------------------- 键盘 -------------------
def demand_keyboard(demand_id, user_id):
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("立即响应", callback_data=f"respond:{demand_id}:{user_id}"))
    return kb

def admin_panel_keyboard():
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("管理关键词", callback_data="admin:keywords"))
    kb.add(InlineKeyboardButton("管理群组", callback_data="admin:groups"))
    kb.add(InlineKeyboardButton("管理管理员", callback_data="admin:admins"))
    kb.add(InlineKeyboardButton("管理全局黑名单", callback_data="admin:blacklist"))
    return kb

# ------------------- 事件处理 -------------------
@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply("欢迎使用需求碰撞机器人！", reply_markup=admin_panel_keyboard() if is_admin(msg.from_user.id) else None)

@dp.message(Command("demand"))
async def new_demand(msg: types.Message):
    text = msg.get_args()
    if not text:
        await msg.reply("请在 /demand 后输入需求内容")
        return
    clean_expired_demands()
    c.execute("INSERT INTO demands(user_id,text,created_at) VALUES(?,?,?)",
              (msg.from_user.id, text, datetime.utcnow().isoformat()))
    demand_id = c.lastrowid
    conn.commit()
    await msg.reply(f"✅ 需求已创建: {text}", reply_markup=demand_keyboard(demand_id, msg.from_user.id))

@dp.callback_query(lambda c: c.data.startswith("respond:"))
async def handle_respond(cb: types.CallbackQuery):
    _, demand_id, demand_owner = cb.data.split(":")
    demand_id = int(demand_id)
    demand_owner = int(demand_owner)
    user_id = cb.from_user.id
    clean_expired_demands()
    if is_blocked(demand_owner, user_id):
        await cb.answer("你已被此用户拉黑，无法响应", show_alert=True)
        return
    c.execute("SELECT 1 FROM responses WHERE demand_id=? AND user_id=?", (demand_id, user_id))
    if c.fetchone():
        await cb.answer("你已响应过此需求", show_alert=True)
        return
    c.execute("INSERT INTO responses(demand_id,user_id,responded_at) VALUES(?,?,?)",
              (demand_id, user_id, datetime.utcnow().isoformat()))
    conn.commit()
    await cb.answer("✅ 响应成功")
    try:
        await bot.send_message(demand_owner, f"用户 {cb.from_user.full_name} 响应了你的需求")
    except:
        pass

# ------------------- 管理员面板示例 -------------------
@dp.callback_query(lambda c: c.data.startswith("admin:"))
async def admin_panel(cb: types.CallbackQuery):
    user_id = cb.from_user.id
    if not is_admin(user_id):
        await cb.answer("你不是管理员", show_alert=True)
        return
    await cb.answer("面板功能暂未实现完整按钮逻辑")

# ------------------- 启动 -------------------
if __name__ == "__main__":
    import asyncio
    asyncio.run(dp.start_polling(bot))