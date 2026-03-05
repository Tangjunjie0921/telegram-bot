import asyncio
import json
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

# ==================== 配置（Railway 环境变量） ====================
TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

if not all([TOKEN, GROUP_ID, ADMIN_ID]):
    raise ValueError("❌ 请在 Railway 设置环境变量: BOT_TOKEN, GROUP_ID, ADMIN_ID")

# ✅ 已修复：使用官方推荐的 DefaultBotProperties（aiogram 3.7+ 强制要求）
bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

DATA_FILE = "/data/reports.json"   # Railway Volume 推荐路径（免费计划重启会丢失）
reports = {}  # original_msg_id -> {"warning_id": int, "suspect_id": int, "chat_id": int, "reporters": set}
lock = asyncio.Lock()

# ==================== 数据持久化 ====================
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
            print(f"✅ 已加载 {len(reports)} 条举报记录")
    except Exception as e:
        print("数据加载失败（首次运行正常）:", e)

async def save_data():
    async with lock:
        try:
            os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
            data_to_save = {str(k): {**v, "reporters": list(v["reporters"])} for k, v in reports.items()}
            with open(DATA_FILE, "w", encoding="utf-8") as f:
                json.dump(data_to_save, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("保存数据失败:", e)

# ==================== 功能 A：监听 + 检查简介 ====================
@router.message(F.chat.id == GROUP_ID)
async def check_user_bio(message: Message):
    if not message.from_user or message.from_user.is_bot:
        return

    user = message.from_user
    try:
        chat_info = await bot.get_chat(user.id)
        bio = (chat_info.bio or "").lower()
        if any(x in bio for x in ["http://", "https://", "t.me/", "@"]):
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}")]
            ])
            text = f"⚠️ 简介有链接，疑似广告引流\n用户ID: {user.id}\n举报数: 0"
            warning = await message.reply(text, reply_markup=keyboard)

            async with lock:
                reports[message.message_id] = {
                    "warning_id": warning.message_id,
                    "suspect_id": user.id,
                    "chat_id": message.chat.id,
                    "reporters": set()
                }
            await save_data()
    except Exception:
        pass  # 用户隐私设置或无法获取，静默跳过

# ==================== 功能 B：举报系统 ====================
@router.callback_query(F.data.startswith("report:"))
async def handle_report(callback: CallbackQuery):
    try:
        original_id = int(callback.data.split(":", 1)[1])
        reporter_id = callback.from_user.id

        async with lock:
            if original_id not in reports:
                await callback.answer("该消息举报已过期", show_alert=True)
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

        if count >= 3:
            new_text = f"🚨 超3人举报，已通知管理员\n用户ID: {suspect_id}\n举报人数: {count}"
            await bot.edit_message_text(
                chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=None
            )
            await bot.send_message(
                ADMIN_ID,
                f"🚨 用户被多人举报\n用户ID: {suspect_id}\n举报人数: {count}\n群组ID: {chat_id}"
            )
        else:
            new_text = f"⚠️ 简介有链接，疑似广告引流\n用户ID: {suspect_id}\n举报数: {count}"
            await bot.edit_message_text(
                chat_id=chat_id, message_id=warning_id, text=new_text,
                reply_markup=callback.message.reply_markup
            )

        await save_data()
        await callback.answer(f"举报成功！当前 {count} 人", show_alert=False)
    except Exception as e:
        print("举报处理异常（已捕获）:", e)
        await callback.answer("操作失败")

# ==================== 功能 C：原消息删除 → 同步删除提示 ====================
async def cleanup_deleted_messages():
    while True:
        await asyncio.sleep(300)
        to_remove = []
        async with lock:
            check_list = list(reports.items())
        for orig_id, data in check_list:
            try:
                test = await bot.forward_message(
                    chat_id=ADMIN_ID, from_chat_id=data["chat_id"], message_id=orig_id
                )
                await bot.delete_message(ADMIN_ID, test.message_id)
            except TelegramBadRequest as e:
                if "not found" in str(e).lower():
                    try:
                        await bot.delete_message(data["chat_id"], data["warning_id"])
                        to_remove.append(orig_id)
                        print(f"✅ 原消息 {orig_id} 已删除，已同步删除警告")
                    except:
                        pass
            except Exception:
                pass
        if to_remove:
            async with lock:
                for oid in to_remove:
                    reports.pop(oid, None)
            await save_data()

# ==================== 启动 ====================
async def main():
    print("🚀 防广告机器人启动成功（aiogram 3.14+ + Railway 规范）")
    await load_data()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())