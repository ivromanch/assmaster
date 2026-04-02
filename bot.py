import asyncio
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

# === PATHS ===
BASE_DIR = Path(__file__).resolve().parent
USERS_FILE = BASE_DIR / "users.json"
PROGRAM_FILE = BASE_DIR / "program.yaml"

# === TOKEN ===
TOKEN = os.getenv("BOT_TOKEN")
assert TOKEN, "BOT_TOKEN is missing"

bot = Bot(token=TOKEN)
dp = Dispatcher()

print("🚀 BOT STARTED", flush=True)


# === STORAGE ===
def load_users():
    if not USERS_FILE.exists():
        USERS_FILE.write_text("{}", encoding="utf-8")
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except Exception:
            return {}


def save_users(users):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def load_program():
    with open(PROGRAM_FILE, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    print("🔄 program.yaml reloaded", flush=True)
    return data


users = load_users()


def get_user(user_id: int):
    uid = str(user_id)
    if uid not in users:
        users[uid] = {
            "started_at": None,
            "sent_events": [],
            "answers": {},
            "debug_file_mode": False,
        }
    return users[uid]


# === SEND EVENT ===
async def send_event(user_id: int, day_num: int, event: dict):
    event_type = event["type"]

    if event_type == "text":
        await bot.send_message(user_id, event["text"])

    elif event_type == "video":
        await bot.send_video(
            chat_id=user_id,
            video=event["file_id"],
            caption=event.get("caption"),
        )

    elif event_type == "audio":
        await bot.send_audio(
            chat_id=user_id,
            audio=event["file_id"],
            caption=event.get("caption"),
        )

    elif event_type == "buttons":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=b["text"],
                        callback_data=b["action"],
                    )
                    for b in event["buttons"]
                ]
            ]
        )

        await bot.send_message(
            user_id,
            event["text"],
            reply_markup=kb,
        )


# === COMMANDS ===
@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = get_user(message.from_user.id)

    user["started_at"] = datetime.utcnow().isoformat()
    user["sent_events"] = []
    user["answers"] = {}
    user["debug_file_mode"] = False

    save_users(users)

    await message.answer("🚀 Старт. Добро пожаловать.")


@dp.message(Command("next"))
async def debug_next(message: Message):
    user = get_user(message.from_user.id)

    if not user.get("started_at"):
        await message.answer("Сначала нажми /start")
        return

    program_data = load_program()
    days = program_data.get("days", {})

    for day_str, day_data in days.items():
        day_num = int(day_str)

        for event in day_data.get("events", []):
            event_id = f"{day_num}:{event['id']}"

            if event_id not in user["sent_events"]:
                await send_event(message.from_user.id, day_num, event)
                user["sent_events"].append(event_id)
                save_users(users)

                await message.answer(f"👉 DEBUG: {event_id}")
                return

    await message.answer("Все события отправлены")


@dp.message(Command("reset"))
async def debug_reset(message: Message):
    user = get_user(message.from_user.id)

    user["started_at"] = datetime.utcnow().isoformat()
    user["sent_events"] = []
    user["answers"] = {}
    user["debug_file_mode"] = False

    save_users(users)

    await message.answer("🔄 Сброс. Начни заново с /start")


@dp.message(Command("fileid"))
async def enable_file_mode(message: Message):
    user = get_user(message.from_user.id)
    user["debug_file_mode"] = True
    save_users(users)

    await message.answer("📥 Отправь файл (видео / аудио)")


# === FILE HANDLER ===
@dp.message(F.video | F.document | F.audio)
async def handle_files(message: Message):
    user = get_user(message.from_user.id)

    if not user.get("debug_file_mode"):
        return

    if message.video:
        await message.answer(f"🎥 VIDEO FILE_ID:\n{message.video.file_id}")

    elif message.document:
        await message.answer(f"📦 DOCUMENT FILE_ID:\n{message.document.file_id}")

    elif message.audio:
        await message.answer(f"🎧 AUDIO FILE_ID:\n{message.audio.file_id}")

    user["debug_file_mode"] = False
    save_users(users)


# === CALLBACKS ===
@dp.callback_query(F.data)
async def callbacks_handler(callback: CallbackQuery):
    program_data = load_program()
    actions = program_data.get("actions", {})

    action = actions.get(callback.data)

    if not action:
        await callback.answer("Неизвестное действие")
        return

    user = get_user(callback.from_user.id)

    if "save_answer" in action:
        user["answers"][action["save_answer"]["key"]] = action["save_answer"]["value"]
        save_users(users)

    response = action.get("response")
    if response:
        await callback.message.answer(response["text"])

    await callback.answer()


# === SCHEDULER (FIXED) ===
async def scheduler():
    while True:
        try:
            program_data = load_program()
            days = program_data.get("days", {})

            for uid, user in users.items():
                if not user.get("started_at"):
                    continue

                started_at = datetime.fromisoformat(user["started_at"])
                now = datetime.utcnow()

                # считаем текущий день
                current_day = (now.date() - started_at.date()).days + 1

                for day_str, day_data in days.items():
                    day_num = int(day_str)

                    if day_num != current_day:
                        continue

                    for event in day_data.get("events", []):
                        event_id = f"{day_num}:{event['id']}"

                        if event_id in user["sent_events"]:
                            continue

                        should_send = False

                        # delay события (первый день)
                        if "delay_minutes" in event:
                            target = started_at + timedelta(minutes=event["delay_minutes"])
                            if now >= target:
                                should_send = True

                        # события по времени
                        elif "time" in event:
                            hh, mm = map(int, event["time"].split(":"))
                            target = datetime.combine(
                                now.date(),
                                datetime.min.time()
                            ).replace(hour=hh, minute=mm)

                            if now >= target:
                                should_send = True

                        if should_send:
                            await send_event(int(uid), day_num, event)
                            user["sent_events"].append(event_id)
                            save_users(users)

        except Exception as e:
            print("❌ ERROR:", e, flush=True)

        await asyncio.sleep(10)  # быстрее проверяем


# === MAIN ===
async def main():
    asyncio.create_task(scheduler())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
