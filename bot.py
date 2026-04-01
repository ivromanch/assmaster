import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
PROGRAM_PATH = BASE_DIR / "program.yaml"
USERS_PATH = BASE_DIR / "users.json"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TIMEZONE_OFFSET_HOURS = int(os.getenv("TIMEZONE_OFFSET_HOURS", "3"))
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))

if not BOT_TOKEN:
    raise RuntimeError("Set BOT_TOKEN in environment variables")


@dataclass
class EventDueResult:
    due: bool
    dedupe_key: str


def load_program() -> dict[str, Any]:
    with PROGRAM_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_users() -> dict[str, Any]:
    if not USERS_PATH.exists():
        return {}
    with USERS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_users(users: dict[str, Any]) -> None:
    temp_path = USERS_PATH.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    temp_path.replace(USERS_PATH)


def now_local() -> datetime:
    return datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET_HOURS)


def parse_hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


def get_or_create_user(users: dict[str, Any], user_id: int) -> dict[str, Any]:
    key = str(user_id)
    if key not in users:
        users[key] = {
            "started_at": now_local().isoformat(),
            "sent_events": [],
            "answers": {},
            "username": None,
            "first_name": None,
        }
    return users[key]


def get_course_day(started_at_iso: str, current_time: datetime) -> int:
    started_at = datetime.fromisoformat(started_at_iso)
    return (current_time.date() - started_at.date()).days + 1


def event_is_due(user: dict[str, Any], day_number: int, event: dict[str, Any], event_index: int, current_time: datetime) -> EventDueResult:
    started_at = datetime.fromisoformat(user["started_at"])
    event_id = event.get("id", f"event_{event_index}")
    dedupe_key = f"{day_number}:{event_id}"

    if dedupe_key in user["sent_events"]:
        return EventDueResult(False, dedupe_key)

    if day_number == 1 and "delay_minutes" in event:
        due_at = started_at + timedelta(minutes=int(event["delay_minutes"]))
        return EventDueResult(current_time >= due_at, dedupe_key)

    if "time" in event:
        hour, minute = parse_hhmm(event["time"])
        due_at = datetime.combine(
            started_at.date() + timedelta(days=day_number - 1),
            datetime.min.time(),
        ).replace(hour=hour, minute=minute)
        return EventDueResult(current_time >= due_at, dedupe_key)

    return EventDueResult(False, dedupe_key)


def build_inline_keyboard(buttons: list[dict[str, str]]) -> InlineKeyboardMarkup:
    rows = []
    for button in buttons:
        rows.append([
            InlineKeyboardButton(text=button["text"], callback_data=button["action"])
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_event(bot: Bot, user_id: int, event: dict[str, Any]) -> None:
    event_type = event["type"]

    if event_type == "text":
        await bot.send_message(user_id, event["text"])
        return

    if event_type == "video":
        await bot.send_video(user_id, video=event["file_id"], caption=event.get("caption"))
        return

    if event_type == "audio":
        await bot.send_audio(user_id, audio=event["file_id"], caption=event.get("caption"))
        return

    if event_type == "buttons":
        markup = build_inline_keyboard(event["buttons"])
        await bot.send_message(user_id, event["text"], reply_markup=markup)
        return

    raise ValueError(f"Unsupported event type: {event_type}")


async def process_due_events(bot: Bot) -> None:
    program = load_program()
    users = load_users()
    current_time = now_local()

    days_map = program.get("days", {})

    for user_id_str, user in users.items():
        user_id = int(user_id_str)
        course_day = get_course_day(user["started_at"], current_time)

        for day_number_str, day_payload in days_map.items():
            day_number = int(day_number_str)
            if day_number > course_day:
                continue

            for index, event in enumerate(day_payload.get("events", [])):
                due_result = event_is_due(user, day_number, event, index, current_time)
                if not due_result.due:
                    continue

                try:
                    await send_event(bot, user_id, event)
                    user["sent_events"].append(due_result.dedupe_key)
                    save_users(users)
                    await asyncio.sleep(0.3)
                except Exception:
                    logger.exception("Failed to send event %s to user %s", due_result.dedupe_key, user_id)


async def scheduler(bot: Bot) -> None:
    while True:
        try:
            await process_due_events(bot)
        except Exception:
            logger.exception("Scheduler loop failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


def get_action_payload(program: dict[str, Any], action_name: str) -> dict[str, Any] | None:
    return program.get("actions", {}).get(action_name)


async def execute_action(bot: Bot, callback: CallbackQuery, action_name: str) -> None:
    program = load_program()
    users = load_users()
    user = get_or_create_user(users, callback.from_user.id)
    action = get_action_payload(program, action_name)

    if not action:
        await callback.answer("Не нашёл это действие в сценарии.", show_alert=True)
        return

    save_answer = action.get("save_answer")
    if save_answer:
        user["answers"][save_answer["key"]] = save_answer["value"]
        save_users(users)

    response = action.get("response")
    if response:
        await send_event(bot, callback.from_user.id, response)

    await callback.answer()


bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


@dp.message(CommandStart())
async def handle_start(message: Message) -> None:
    users = load_users()
    user = get_or_create_user(users, message.from_user.id)
    user["started_at"] = now_local().isoformat()
    user["sent_events"] = []
    user["answers"] = {}
    user["username"] = message.from_user.username
    user["first_name"] = message.from_user.first_name
    save_users(users)

    await message.answer("Запускаю курс. Первые сообщения начнут приходить автоматически.")


@dp.callback_query(F.data)
async def handle_callback(callback: CallbackQuery) -> None:
    await execute_action(bot, callback, callback.data)


async def main() -> None:
    asyncio.create_task(scheduler(bot))
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

@dp.message(Command("next"))
async def debug_next(message: Message):
    user = get_user(message.from_user.id)

    if not user.get("started_at"):
        await message.answer("Сначала нажми /start")
        return

    program_data = load_program()
    days = program_data.get("days", {})

    started_at = datetime.fromisoformat(user["started_at"])
    now = datetime.utcnow()
    current_day = (now.date() - started_at.date()).days + 1

    # ищем следующий неотправленный event
    for day_str, day_data in days.items():
        day_num = int(day_str)

        if day_num < current_day:
            continue

        events = day_data.get("events", [])

        for event in events:
            event_id = f"{day_num}:{event['id']}"

            if event_id not in user["sent_events"]:
                await send_event(message.from_user.id, day_num, event)
                user["sent_events"].append(event_id)
                save_users(users)

                await message.answer(f"👉 Debug: отправлен {event_id}")
                return

    await message.answer("Все события уже отправлены")

@dp.message(Command("reset"))
async def debug_reset(message: Message):
    user = get_user(message.from_user.id)
    user["started_at"] = datetime.utcnow().isoformat()
    user["sent_events"] = []
    user["answers"] = {}
    save_users(users)

    await message.answer("🔄 Сброс. Начинай заново с /next или жди сценарий.")
