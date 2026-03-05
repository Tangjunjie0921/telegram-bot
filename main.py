import asyncio
import json
import os
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ChatPermissions

# ==================== 配置（Railway 环境变量） ====================
TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))
ADMIN_ID = int(os.getenv("ADMIN_ID"))

if not all([TOKEN, GROUP_ID, ADMIN_ID]):
    raise ValueError("❌ 请在 Railway 设置环境变量: BOT_TOKEN, GROUP_ID, ADMIN_ID")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
router = Router()
dp.include_router(router)

DATA_FILE = "/data/reports.json"
reports = {}
lock = asyncio.Lock()

# ==================== 敏感词列表（已包含你之前要求的关键词） ====================
SPAM_KEYWORDS = [
    "qq:", "qq：", "qq号", "加qq", "扣扣",
    "微信", "wx:", "weixin", "加我微信", "wxid_",
    "幼女", "萝莉", "少妇", "人妻", "熟女", "福利", "约炮", "约", "反差", "外围",
    "空姐", "学生妹", "嫩模", "资源", "种子", "磁力", "破解", "独家资源",
    "飞机", "加群", "进群", "私聊我", "私我", "互推", "拉人",
]

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

        has_link = any(x in bio for x in ["http://", "https://", "t.me/", "@"])
        has_spam_keyword = any(keyword.lower() in bio for keyword in SPAM_KEYWORDS)

        if has_link or has_spam_keyword:
            keyword_text = "链接" if has_link else "敏感词"
            if has_link and has_spam_keyword:
                keyword_text = "链接 + 敏感词"

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="举报该用户", callback_data=f"report:{message.message_id}")]
            ])
            text = f"⚠️ 简介疑似广告/引流（含{keyword_text}）\n用户ID: {user.id}\n举报数: 0"
            warning = await message.reply(text, reply_markup=keyboard)

            async with lock:
                reports[message.message_id] = {
                    "warning_id": warning.message_id,
                    "suspect_id": user.id,
                    "chat_id": message.chat.id,
                    "reporters": set()
                }
            await save_data()
            print(f"检测到可疑 bio: 用户 {user.id} - {bio[:60]}...")
    except Exception:
        pass

# ==================== 功能 B：举报系统（1人即可显示管理员封禁按钮） ====================
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

        current_markup = callback.message.reply_markup
        # 只要 >=1 人举报就显示管理员封禁按钮
        ban_button = InlineKeyboardButton(text="管理员封禁", callback_data=f"ban:{original_id}")
        keyboard_list = current_markup.inline_keyboard[:] if current_markup and current_markup.inline_keyboard else []
        # 防止重复添加按钮
        if not any("ban:" in str(btn.callback_data) for row in keyboard_list for btn in row):
            keyboard_list.append([ban_button])
        new_keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_list)

        if count >= 3:
            new_text = f"🚨 超3人举报，已通知管理员\n用户ID: {suspect_id}\n举报人数: {count}"
            await bot.send_message(
                ADMIN_ID,
                f"🚨 用户被多人举报\n用户ID: {suspect_id}\n举报人数: {count}\n群组ID: {chat_id}"
            )
        else:
            new_text = f"🚨 已有人举报，可由管理员封禁\n用户ID: {suspect_id}\n举报人数: {count}"

        await bot.edit_message_text(
            chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=new_keyboard
        )

        await save_data()
        await callback.answer(f"举报成功！当前 {count} 人", show_alert=False)
    except Exception as e:
        print("举报处理异常（已捕获）:", e)
        await callback.answer("操作失败")

# ==================== 管理员封禁回调（限制权限，非踢出） ====================
@router.callback_query(F.data.startswith("ban:"))
async def handle_ban(callback: CallbackQuery):
    try:
        original_id = int(callback.data.split(":", 1)[1])
        caller_id = callback.from_user.id
        chat_id = callback.message.chat.id

        if caller_id != ADMIN_ID:
            await callback.answer("只有管理员可以执行限制权限操作", show_alert=True)
            return

        async with lock:
            if original_id not in reports:
                await callback.answer("举报记录已过期", show_alert=True)
                return
            data = reports[original_id]
            suspect_id = data["suspect_id"]
            warning_id = data["warning_id"]

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
                can_pin_messages=False,
            )
        )

        new_text = f"🚨 已由管理员限制群组权限\n用户ID: {suspect_id}\n举报人数: {len(data['reporters'])}"
        await bot.edit_message_text(
            chat_id=chat_id, message_id=warning_id, text=new_text, reply_markup=None
        )

        await callback.answer("用户已永久限制所有权限", show_alert=True)
        print(f"管理员 {caller_id} 已限制用户 {suspect_id} 的群组权限")

        async with lock:
            reports.pop(original_id, None)
        await save_data()

    except TelegramBadRequest as e:
        if "user_not_participant" in str(e).lower():
            await callback.answer("用户不在群组中", show_alert=True)
        elif "not enough rights" in str(e).lower():
            await callback.answer("机器人缺少限制权限，请检查管理员设置", show_alert=True)
        else:
            await callback.answer(f"操作失败: {str(e)}", show_alert=True)
    except Exception as e:
        print("限制权限异常:", e)
        await callback.answer("操作失败", show_alert=True)

# ==================== 功能 C：消息删除同步 ====================
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
    print("🚀 防广告机器人启动成功（1人举报即显示管理员封禁按钮）")
    await load_data()
    asyncio.create_task(cleanup_deleted_messages())
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())