import asyncio
import os
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
import sqlite3

# ===== 环境变量 =====
TOKEN = os.environ["BOT_TOKEN"]

# ===== 初始化 Bot =====
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== SQLite 数据库 =====
DB_FILE = "demand_bot.db"
conn = sqlite3.connect(DB_FILE)
cursor = conn.cursor()

# ===== 数据表 =====
cursor.execute("""
CREATE TABLE IF NOT EXISTS demands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    timestamp TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS responses (
    demand_id INTEGER,
    responder_id INTEGER,
    timestamp TEXT
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_blacklist (
    user_id INTEGER,
    blocked_id INTEGER
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS global_blacklist (
    user_id INTEGER PRIMARY KEY
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS keywords (
    word TEXT PRIMARY KEY
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY
)
""")
conn.commit()

# ===== 默认管理员 =====
DEFAULT_ADMINS = [123456789]  # 替换为真实管理员ID
for admin_id in DEFAULT_ADMINS:
    cursor.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (admin_id,))
conn.commit()

# ===== 辅助函数 =====
def is_admin(user_id):
    cursor.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def is_global_blacklisted(user_id):
    cursor.execute("SELECT 1 FROM global_blacklist WHERE user_id=?", (user_id,))
    return cursor.fetchone() is not None

def is_user_blacklisted(user_id, target_id):
    cursor.execute("SELECT 1 FROM user_blacklist WHERE user_id=? AND blocked_id=?", (user_id, target_id))
    return cursor.fetchone() is not None

def clean_expired_demands():
    now = datetime.utcnow()
    cursor.execute("SELECT id, timestamp FROM demands")
    for demand_id, ts in cursor.fetchall():
        t = datetime.fromisoformat(ts)
        if now > t + timedelta(hours=12):
            cursor.execute("DELETE FROM demands WHERE id=?", (demand_id,))
            cursor.execute("DELETE FROM responses WHERE demand_id=?", (demand_id,))
    conn.commit()

def get_recent_responses(responder_id, hours=1):
    now = datetime.utcnow()
    since = now - timedelta(hours=hours)
    cursor.execute("SELECT COUNT(*) FROM responses WHERE responder_id=? AND timestamp>=?", (responder_id, since.isoformat()))
    return cursor.fetchone()[0]

def get_responses_24h(responder_id):
    now = datetime.utcnow()
    since = now - timedelta(hours=24)
    cursor.execute("SELECT COUNT(*) FROM responses WHERE responder_id=? AND timestamp>=?", (responder_id, since.isoformat()))
    return cursor.fetchone()[0]

# ===== /demand 发布需求 =====
@dp.message(Command(commands=["demand"]))
async def handle_demand(msg: types.Message):
    clean_expired_demands()
    text = msg.text[7:].strip()
    if not text:
        await msg.reply("请提供需求内容")
        return

    timestamp = datetime.utcnow().isoformat()
    cursor.execute("INSERT INTO demands(user_id, text, timestamp) VALUES(?,?,?)", (msg.from_user.id, text, timestamp))
    demand_id = cursor.lastrowid
    conn.commit()

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("立即响应", callback_data=f"respond:{demand_id}"))
    await msg.reply(f"✅ 需求已发布: {text}", reply_markup=keyboard)

# ===== 响应按钮 =====
@dp.callback_query()
async def handle_button(cb: types.CallbackQuery):
    data_cb = cb.data
    if not data_cb: return

    if data_cb.startswith("respond:"):
        demand_id = int(data_cb.split(":")[1])
        user_id = cb.from_user.id

        cursor.execute("SELECT user_id, text, timestamp FROM demands WHERE id=?", (demand_id,))
        row = cursor.fetchone()
        if not row:
            await cb.answer("需求已过期或不存在", show_alert=True)
            return
        owner_id, text, ts = row

        if is_user_blacklisted(owner_id, user_id) or is_global_blacklisted(user_id):
            await cb.answer("你已被拉黑，无法响应此需求", show_alert=True)
            return

        if get_recent_responses(user_id, 1) >= 6:
            await cb.answer("1小时内响应已达上限", show_alert=True)
            return
        if get_responses_24h(user_id) >= 10:
            await cb.answer("24小时内响应已达上限", show_alert=True)
            return

        cursor.execute("SELECT demand_id FROM responses WHERE responder_id=? ORDER BY timestamp DESC LIMIT 2", (user_id,))
        last_two = [r[0] for r in cursor.fetchall()]
        if len(last_two) == 2 and last_two[0] == last_two[1] == demand_id:
            await cb.answer("不能连续响应同一条需求", show_alert=True)
            return

        cursor.execute("INSERT INTO responses(demand_id, responder_id, timestamp) VALUES(?,?,?)",
                       (demand_id, user_id, datetime.utcnow().isoformat()))
        conn.commit()

        if cb.from_user.id != owner_id:
            keyboard = InlineKeyboardMarkup()
            keyboard.add(InlineKeyboardButton(f"拉黑 {cb.from_user.full_name}", callback_data=f"block:{owner_id}:{user_id}"))
            await bot.send_message(owner_id, f"用户 {cb.from_user.full_name} 响应了你的需求: {text}", reply_markup=keyboard)

        await cb.answer("已响应需求 ✅")

    elif data_cb.startswith("block:"):
        parts = data_cb.split(":")
        owner_id = int(parts[1])
        blocked_id = int(parts[2])

        if cb.from_user.id != owner_id:
            await cb.answer("无权限拉黑", show_alert=True)
            return

        cursor.execute("INSERT OR IGNORE INTO user_blacklist(user_id, blocked_id) VALUES(?,?)", (owner_id, blocked_id))
        conn.commit()
        await cb.answer(f"已拉黑用户 {blocked_id}", show_alert=True)
        await cb.message.edit_reply_markup(None)

# ===== /panel 管理面板 =====
@dp.message(Command(commands=["panel"]))
async def admin_panel(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.reply("你不是管理员")
        return

    cursor.execute("SELECT group_id FROM groups")
    groups = [str(r[0]) for r in cursor.fetchall()]
    cursor.execute("SELECT word FROM keywords")
    keywords = [r[0] for r in cursor.fetchall()]
    cursor.execute("SELECT user_id FROM global_blacklist")
    gbl = [str(r[0]) for r in cursor.fetchall()]
    cursor.execute("SELECT user_id FROM admins")
    admins = [str(r[0]) for r in cursor.fetchall()]

    text = (
        f"🛠 管理面板\n"
        f"群组: {groups}\n"
        f"关键词: {keywords}\n"
        f"全局黑名单: {gbl}\n"
        f"管理员: {admins}\n"
        f"发送 /addgroup 群ID 或 /rmgroup 群ID\n"
        f"发送 /addkeyword 关键词 或 /rmkeyword 关键词\n"
        f"发送 /blacklist 用户ID 或 /rmblacklist 用户ID\n"
        f"发送 /addadmin 用户ID 或 /rmadmin 用户ID"
    )
    await msg.reply(text)

# ===== 管理命令 =====
async def safe_int_split(msg_text):
    try:
        return int(msg_text.split()[1])
    except:
        return None

# 群组
@dp.message(Command(commands=["addgroup"]))
async def add_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid:
        cursor.execute("INSERT OR IGNORE INTO groups(group_id) VALUES(?)", (gid,))
        conn.commit()
        await msg.reply(f"✅ 添加群组 {gid}")

@dp.message(Command(commands=["rmgroup"]))
async def rm_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid:
        cursor.execute("DELETE FROM groups WHERE group_id=?", (gid,))
        conn.commit()
        await msg.reply(f"✅ 移除群组 {gid}")

# 关键词
@dp.message(Command(commands=["addkeyword"]))
async def add_keyword(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    try:
        word = msg.text.split()[1].strip()
        cursor.execute("INSERT OR IGNORE INTO keywords(word) VALUES(?)", (word,))
        conn.commit()
        await msg.reply(f"✅ 添加关键词 {word}")
    except: pass

@dp.message(Command(commands=["rmkeyword"]))
async def rm_keyword(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    try:
        word = msg.text.split()[1].strip()
        cursor.execute("DELETE FROM keywords WHERE word=?", (word,))
        conn.commit()
        await msg.reply(f"✅ 移除关键词 {word}")
    except: pass

# 全局黑名单
@dp.message(Command(commands=["blacklist"]))
async def add_global_blacklist(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        cursor.execute("INSERT OR IGNORE INTO global_blacklist(user_id) VALUES(?)", (uid,))
        conn.commit()
        await msg.reply(f"✅ 用户 {uid} 加入全局黑名单")

@dp.message(Command(commands=["rmblacklist"]))
async def rm_global_blacklist(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        cursor.execute("DELETE FROM global_blacklist WHERE user_id=?", (uid,))
        conn.commit()
        await msg.reply(f"✅ 用户 {uid} 移出全局黑名单")

# 管理员
@dp.message(Command(commands=["addadmin"]))
async def add_admin(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        cursor.execute("INSERT OR IGNORE INTO admins(user_id) VALUES(?)", (uid,))
        conn.commit()
        await msg.reply(f"✅ 用户 {uid} 成为管理员")

@dp.message(Command(commands=["rmadmin"]))
async def rm_admin(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        cursor.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        conn.commit()
        await msg.reply(f"✅ 用户 {uid} 移除管理员")

# ===== 启动 =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())