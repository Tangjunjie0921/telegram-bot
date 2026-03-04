import asyncio
import sqlite3
from datetime import datetime, timedelta
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = "8756346981:AAGXtbNlWAZ-rkfq18NthMMDOwz2nYHWVEo"  # 请设置环境变量或直接写入
DEFAULT_ADMIN_ID = 8276405169

# ===== 初始化 Bot 和 Dispatcher =====
bot = Bot(token=TOKEN)
dp = Dispatcher()

# ===== 数据库初始化 =====
conn = sqlite3.connect("data.db")
c = conn.cursor()

# 管理员表
c.execute("""
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
""")
c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (DEFAULT_ADMIN_ID,))

# 群组表
c.execute("""
CREATE TABLE IF NOT EXISTS groups (
    group_id INTEGER PRIMARY KEY
)
""")

# 关键词表
c.execute("""
CREATE TABLE IF NOT EXISTS keywords (
    keyword TEXT PRIMARY KEY
)
""")

# 全局黑名单表
c.execute("""
CREATE TABLE IF NOT EXISTS global_blacklist (
    user_id INTEGER PRIMARY KEY
)
""")

# 私人黑名单表
c.execute("""
CREATE TABLE IF NOT EXISTS user_blacklist (
    owner_id INTEGER,
    blocked_id INTEGER,
    PRIMARY KEY(owner_id, blocked_id)
)
""")

# 需求表
c.execute("""
CREATE TABLE IF NOT EXISTS demands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    text TEXT,
    timestamp TEXT
)
""")

# 响应记录表
c.execute("""
CREATE TABLE IF NOT EXISTS responses (
    demand_id INTEGER,
    responder_id INTEGER,
    timestamp TEXT,
    PRIMARY KEY(demand_id, responder_id)
)
""")
conn.commit()

# ===== 辅助函数 =====
def is_admin(user_id):
    c.execute("SELECT 1 FROM admins WHERE user_id=?", (user_id,))
    return c.fetchone() is not None

def is_blacklisted(owner_id, responder_id):
    # 私人黑名单
    c.execute("SELECT 1 FROM user_blacklist WHERE owner_id=? AND blocked_id=?", (owner_id, responder_id))
    if c.fetchone():
        return True
    # 全局黑名单
    c.execute("SELECT 1 FROM global_blacklist WHERE user_id=?", (responder_id,))
    return c.fetchone() is not None

def clean_expired_demands():
    now = datetime.utcnow()
    c.execute("SELECT id, timestamp FROM demands")
    rows = c.fetchall()
    for demand_id, ts in rows:
        ts_dt = datetime.fromisoformat(ts)
        if now > ts_dt + timedelta(hours=12):
            c.execute("DELETE FROM demands WHERE id=?", (demand_id,))
            c.execute("DELETE FROM responses WHERE demand_id=?", (demand_id,))
    conn.commit()

def get_recent_responses(user_id, hours=1):
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    c.execute("SELECT COUNT(*) FROM responses WHERE responder_id=? AND timestamp>=?", (user_id, cutoff.isoformat()))
    return c.fetchone()[0]

def get_recent_responses_24h(user_id):
    return get_recent_responses(user_id, hours=24)

# ===== 发布需求 /demand =====
@dp.message(Command(commands=["demand"]))
async def handle_demand(msg: types.Message):
    clean_expired_demands()
    text = msg.text[7:].strip()
    if not text:
        await msg.reply("请提供需求内容")
        return
    timestamp = datetime.utcnow().isoformat()
    c.execute("INSERT INTO demands(user_id, text, timestamp) VALUES (?,?,?)", (msg.from_user.id, text, timestamp))
    demand_id = c.lastrowid
    conn.commit()
    # 生成响应按钮
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("立即响应", callback_data=f"respond:{demand_id}"))
    await msg.reply(f"✅ 需求已发布: {text}", reply_markup=kb)

# ===== 响应需求 =====
@dp.callback_query(lambda c: c.data and c.data.startswith("respond:"))
async def respond_callback(callback: types.CallbackQuery):
    clean_expired_demands()
    demand_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # 检查需求是否存在
    c.execute("SELECT user_id, text FROM demands WHERE id=?", (demand_id,))
    row = c.fetchone()
    if not row:
        await callback.answer("需求已过期或不存在", show_alert=True)
        return
    owner_id, text = row

    if owner_id == user_id:
        await callback.answer("不能响应自己的需求", show_alert=True)
        return
    if is_blacklisted(owner_id, user_id):
        await callback.answer("你已被拉黑，无法响应此用户的需求", show_alert=True)
        return

    # 响应次数限制
    if get_recent_responses(user_id, 1) >= 6:
        await callback.answer("1小时内最多响应6条需求", show_alert=True)
        return
    if get_recent_responses_24h(user_id) >= 10:
        await callback.answer("24小时内最多响应10条需求", show_alert=True)
        return

    # 检查是否重复连续响应同一条需求
    c.execute("SELECT responder_id FROM responses WHERE demand_id=? ORDER BY timestamp DESC LIMIT 2", (demand_id,))
    last_two = [r[0] for r in c.fetchall()]
    if last_two.count(user_id) >= 2:
        await callback.answer("不能连续重复响应同一需求", show_alert=True)
        return

    # 保存响应
    c.execute("INSERT OR IGNORE INTO responses(demand_id, responder_id, timestamp) VALUES (?,?,?)",
              (demand_id, user_id, datetime.utcnow().isoformat()))
    conn.commit()

    # 给需求发起者更新按钮，添加拉黑按钮
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton("拉黑此用户", callback_data=f"block:{demand_id}:{user_id}"))
    await bot.send_message(owner_id, f"用户 {user_id} 响应了你的需求: {text}", reply_markup=kb)
    await callback.answer("✅ 响应成功", show_alert=True)

# ===== 拉黑功能 =====
@dp.callback_query(lambda c: c.data and c.data.startswith("block:"))
async def block_callback(callback: types.CallbackQuery):
    _, demand_id, blocked_id = callback.data.split(":")
    blocked_id = int(blocked_id)
    owner_id = callback.from_user.id
    c.execute("INSERT OR IGNORE INTO user_blacklist(owner_id, blocked_id) VALUES (?,?)", (owner_id, blocked_id))
    conn.commit()
    await callback.answer(f"已拉黑用户 {blocked_id}", show_alert=True)

# ===== 管理面板 =====
@dp.message(Command(commands=["panel"]))
async def admin_panel(msg: types.Message):
    if not is_admin(msg.from_user.id):
        await msg.reply("你不是管理员")
        return
    c.execute("SELECT user_id FROM admins")
    admins = [str(r[0]) for r in c.fetchall()]
    c.execute("SELECT group_id FROM groups")
    groups = [str(r[0]) for r in c.fetchall()]
    c.execute("SELECT keyword FROM keywords")
    keywords = [r[0] for r in c.fetchall()]
    c.execute("SELECT user_id FROM global_blacklist")
    gbl = [str(r[0]) for r in c.fetchall()]
    text = (
        f"🛠 管理面板\n"
        f"管理员: {admins}\n"
        f"群组: {groups}\n"
        f"关键词: {keywords}\n"
        f"全局黑名单: {gbl}\n"
        f"可使用命令:\n"
        f"/addadmin 用户ID, /rmadmin 用户ID\n"
        f"/addgroup 群ID, /rmgroup 群ID\n"
        f"/addkeyword 关键词, /rmkeyword 关键词\n"
        f"/blacklist 用户ID, /rmblacklist 用户ID"
    )
    await msg.reply(text)

# ===== 各种管理命令 =====
async def safe_int_split(msg_text):
    parts = msg_text.split()
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None

@dp.message(Command(commands=["addadmin"]))
async def add_admin(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        c.execute("INSERT OR IGNORE INTO admins(user_id) VALUES (?)", (uid,))
        conn.commit()
        await msg.reply(f"✅ 添加管理员 {uid}")

@dp.message(Command(commands=["rmadmin"]))
async def rm_admin(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        c.execute("DELETE FROM admins WHERE user_id=?", (uid,))
        conn.commit()
        await msg.reply(f"✅ 移除管理员 {uid}")

@dp.message(Command(commands=["addgroup"]))
async def add_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid:
        c.execute("INSERT OR IGNORE INTO groups(group_id) VALUES (?)", (gid,))
        conn.commit()
        await msg.reply(f"✅ 添加群组 {gid}")

@dp.message(Command(commands=["rmgroup"]))
async def rm_group(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    gid = await safe_int_split(msg.text)
    if gid:
        c.execute("DELETE FROM groups WHERE group_id=?", (gid,))
        conn.commit()
        await msg.reply(f"✅ 移除群组 {gid}")

@dp.message(Command(commands=["addkeyword"]))
async def add_keyword(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2: return
    kw = parts[1].strip()
    c.execute("INSERT OR IGNORE INTO keywords(keyword) VALUES (?)", (kw,))
    conn.commit()
    await msg.reply(f"✅ 添加关键词 {kw}")

@dp.message(Command(commands=["rmkeyword"]))
async def rm_keyword(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    parts = msg.text.split(maxsplit=1)
    if len(parts) < 2: return
    kw = parts[1].strip()
    c.execute("DELETE FROM keywords WHERE keyword=?", (kw,))
    conn.commit()
    await msg.reply(f"✅ 移除关键词 {kw}")

@dp.message(Command(commands=["blacklist"]))
async def add_global_blacklist(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        c.execute("INSERT OR IGNORE INTO global_blacklist(user_id) VALUES (?)", (uid,))
        conn.commit()
        await msg.reply(f"✅ 添加全局黑名单 {uid}")

@dp.message(Command(commands=["rmblacklist"]))
async def rm_global_blacklist(msg: types.Message):
    if not is_admin(msg.from_user.id): return
    uid = await safe_int_split(msg.text)
    if uid:
        c.execute("DELETE FROM global_blacklist WHERE user_id=?", (uid,))
        conn.commit()
        await msg.reply(f"✅ 移除全局黑名单 {uid}")

# ===== 启动 =====
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())