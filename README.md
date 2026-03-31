# Telegram MVP bot for a 3-day course

## Files
- `bot.py` — bot engine
- `program.yaml` — course script
- `users.json` — local storage for users and answers
- `requirements.txt` — Python dependencies

## Before launch
1. Create a bot via BotFather and copy the token.
2. Replace placeholders in `program.yaml`:
   - `PUT_VIDEO_FILE_ID_DAY1`
   - `PUT_AUDIO_FILE_ID_DAY2`
   - `PUT_AUDIO_FILE_ID_DAY3`
3. Set environment variables:
   - `BOT_TOKEN` — your Telegram bot token
   - `TIMEZONE_OFFSET_HOURS` — default is `3`
   - `CHECK_INTERVAL_SECONDS` — default is `60`

## Local run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export BOT_TOKEN=your_token_here
python bot.py
```

## Railway
Railway supports environment variables for builds and running services, so put `BOT_TOKEN` there instead of hardcoding it. citeturn293425search2turn293425search11

## How callbacks work
Telegram callback buttons send `callback_data`, and the bot uses that value to look up the action in `program.yaml`. Telegram Bot API documents callback queries and callback data for inline keyboard buttons, and aiogram 3.x supports handling callback queries directly. citeturn293425search1turn293425search0turn293425search3

## Notes
- This MVP stores state in `users.json`. For production, move users to a real database.
- Day 1 uses `delay_minutes` relative to `/start`.
- Days 2 and 3 use fixed times from `program.yaml`.
- To restart the course for a user, they can send `/start` again.
