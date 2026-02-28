# Python Discord TTS Bot Based on Bruska TTS by [ahmed5939](https://github.com/ahmed5939)

## Features
- Fully working Discord TTS bot.
- Default behaviour: when someone types in a voice channel's chat, the bot joins that VC (locks to it) and speaks messages for everyone in that VC.
- `/tts` speaks text in your current voice channel.
- `/leave` disconnects the bot (unlocks it for the guild).
- `/voice` sets your personal voice (stored in SQLite). Use `/voice reset` to clear.
- `/set voice` opens a voice picker menu (supports all voices, paginated).
- `/set nickname` sets the name the bot will speak for you (stored in SQLite).
- `/admin panel` (Manage Server) opens an interactive per-server settings panel.
- `/admin show` shows the current per-server settings.
- Optional per-server voice allowlist (restrict which voices members can pick), configurable in the Web UI or `/admin panel` (with previews in Web UI).
- Optional join/leave greetings (good morning/afternoon/evening + goodbye), configurable per server in the Web UI or `/admin panel`.
- Leaves automatically when no non-bot users remain in the voice channel.
- Uses the same TikTok/Google fallback TTS pipeline as the JS bot.
- Streams audio into ffmpeg via a pipe (no temp files).
- Includes an aiohttp Web UI for logs + settings.
- **NEW:** REST API endpoints for external bots to send TTS requests.
- **NEW:** Voice testing page in the Web UI to preview and test voices.

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
  -p 8080:8080 \\
  --restart unless-stopped \\
  tts-bot
```

If you change `WEB_PORT`, make sure the published port (`-p host:container`) matches (e.g. `-e WEB_PORT=5091 -p 5091:5091`).
Optional: set `WEB_UI_TOKEN` to a long random string to protect the Web UI API, then enter it on the Home page.

## Setup (Docker Compose)
1. Create a `.env` file next to `docker-compose.yml`:

```bash
DISCORD_TOKEN=your_token_here
# Optional: protect the Web UI API (enter it on the Home page).
WEB_UI_TOKEN=
```

2. Start the bot:

```bash
docker compose up -d --build
```

## First-time Server Setup
After inviting the bot to your server, configure per-server settings using either:
- Discord: run `/admin panel` (requires **Manage Server**)
- Web UI: open `http://<WEB_HOST>:<WEB_PORT>/settings`

Recommended settings to review:
- Default Voice ID (server default)
- Fallback Voice ID
- Max TTS Characters
- Auto Speak Voice Chat Messages
- Leave When Alone In Voice Channel
- Allowlist Text Channel IDs (optional)
- (Optional) Voice restriction allowlist
- (Optional) Join/leave greetings

## Notes
- Requires `ffmpeg` + `libopus` on your system for local runs. Docker image includes them.
- The bot speaks messages from the voice channel's chat (and only for users that are actually connected to that VC).
  When a different person starts typing, it announces them as: `<name> said "<message>"`.
- Commands are organized as cogs in `tts-bot/cogs/`.
- Discord limits slash-command choice lists to 25 items; use `/set voice` to pick from the full voice list.

## Stability/Performance Config (env vars)
These are global knobs (optional). Defaults shown:
- `QUEUE_MAXSIZE=100` (bounded per-guild queue)
- `DROP_POLICY=drop_oldest` (drop oldest when queue is full)
- `COALESCE_MS=500` (coalesce close messages)
- `COALESCE_SAME_SPEAKER_ONLY=true`
- `MAX_MESSAGE_CHARS=350`
- `MAX_UTTERANCE_CHARS=1000`
- `USER_COOLDOWN_SECONDS=1.5`
- `MAX_AUDIO_SECONDS=20`
- `MAX_RETRIES=2`
- `STUCK_SECONDS=45`
- `SKIP_SUMMARY_ENABLED=true`
- `ALLOWLIST_TEXT_CHANNEL_IDS=` (global CSV allowlist)
- `TTS_HTTP_TIMEOUT=20`

## Sanity Harness
Run a lightweight sanity check (no Discord required):
```bash
python scripts/sanity_harness.py
```

## API Usage
The bot now includes REST API endpoints for external applications and bots to send TTS requests programmatically.

### Quick Start
1. **Enable the Web UI** (enabled by default): Set `WEB_UI_ENABLED=true` in your environment
2. **Set an API token** (recommended for security): Set `WEB_UI_TOKEN=your_secret_token` in your environment
3. **Access the API**: Send POST requests to `http://<host>:<port>/api/tts`

### Send a TTS Request
```bash
curl -X POST http://localhost:8080/api/tts \\
  -H "Authorization: Bearer your_token_here" \\
  -H "Content-Type: application/json" \\
  -d '{
    "guild_id": "123456789012345678",
    "text": "Hello from the API!",
    "voice_id": "en_us_001"
  }'
```

### Voice Testing Page
Navigate to `http://<host>:<port>/test-voices` to:
- Test different voices with custom text
- Preview audio before sending to Discord
- Send TTS directly to voice channels
- View API usage examples

For complete API documentation, see [API_DOCUMENTATION.md](API_DOCUMENTATION.md).

For a Python test script, run:
```bash
python test_api.py
```
- Web UI defaults: `http://127.0.0.1:8080` (override with `WEB_HOST`/`WEB_PORT`).
- If `WEB_UI_TOKEN` is set, API routes require it (enter it on the Home page).
- Disable Web UI with `WEB_UI_ENABLED=0`.
- Settings persist per server to the SQLite DB (table `guild_settings`) at `data/tts.db` (or `DB_PATH`).
- If voice restriction is enabled, Discord voice menus/autocomplete only show allowed voices. If a user's saved voice isn't allowed, the bot uses the server default voice.
- Logs write to `data/tts.log` by default (override with `LOG_FILE_PATH`).
- User voice choices persist to `data/tts.db` by default (override with `DB_PATH`).
- For fast slash-command updates while developing, set `DEV_GUILD_ID` to your test server ID.
