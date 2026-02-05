# Python Discord TTS Bot Based on Bruska TTS by (ahmed5939) [https://github.com/ahmed5939]

## Features
- Default behaviour: when someone types in a voice channel's chat, the bot joins that VC (locks to it) and speaks messages for everyone in that VC.
- `/tts` speaks text in your current voice channel.
- `/leave` disconnects the bot (unlocks it for the guild).
- `/voice` sets your personal voice (stored in SQLite). Use `/voice reset` to clear.
- `/set voice` opens a voice picker menu (supports all voices, paginated).
- `/set nickname` sets the name the bot will speak for you (stored in SQLite).
- Leaves automatically when no non-bot users remain in the voice channel.
- Uses the same TikTok/Google fallback TTS pipeline as the JS bot.
- Streams audio into ffmpeg via a pipe (no temp files).
- Includes an aiohttp Web UI for logs + settings.

## Setup (Local)
1. Create a Discord application + bot, then copy the token.
2. Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Set your token:

```bash
export DISCORD_TOKEN=your_token_here
```

Tip: you can also create a `tts-bot/.env` file (copied from `.env.example`) and the bot will auto-load it.

4. Run:

```bash
python3 bot.py
```

## Setup (Docker)
1. Build the image:

```bash
docker build -t tts-bot .
```

2. Run the container:

```bash
docker run \\
  -e DISCORD_TOKEN=your_token_here \\
  -e WEB_HOST=0.0.0.0 \\
  -e WEB_PORT=8080 \\
  -e WEB_UI_TOKEN=change_me \\
  -p 8080:8080 \\
  --restart unless-stopped \\
  tts-bot
```

## Setup (Docker Compose)
1. Create a `.env` file next to `docker-compose.yml`:

```bash
DISCORD_TOKEN=your_token_here
WEB_UI_TOKEN=change_me
```

2. Start the bot:

```bash
docker compose up -d --build
```

## Notes
- Requires `ffmpeg` + `libopus` on your system for local runs. Docker image includes them.
- The bot speaks messages from the voice channel's chat (and only for users that are actually connected to that VC).
  When a different person starts typing, it announces them as: `<name> said "<message>"`.
- Commands are organized as cogs in `tts-bot/cogs/`.
- Discord limits slash-command choice lists to 25 items; use `/set voice` to pick from the full voice list.
- Web UI defaults: `http://127.0.0.1:8080` (override with `WEB_HOST`/`WEB_PORT`).
- If `WEB_UI_TOKEN` is set, API routes require it (enter it on the Home page).
- Disable Web UI with `WEB_UI_ENABLED=0`.
- Settings persist to `settings.json` (or `SETTINGS_PATH`).
- Logs write to `data/tts.log` by default (override with `LOG_FILE_PATH`).
- User voice choices persist to `data/tts.db` by default (override with `DB_PATH`).
- For fast slash-command updates while developing, set `DEV_GUILD_ID` to your test server ID.
