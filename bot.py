import asyncio
import os
import time
from pathlib import Path

def _load_dotenv() -> None:
    """Load a local .env file if present (without overriding real env vars).

    This keeps local testing ergonomic (similar to the JS bot), while still
    allowing Docker/Compose to supply the environment normally.
    """

    candidates = [Path(".env"), Path(__file__).with_name(".env")]
    env_path = next((p for p in candidates if p.exists() and p.is_file()), None)
    if env_path is None:
        return

    try:
        raw = env_path.read_text(encoding="utf-8")
    except Exception:
        return

    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        # Trim matching quotes.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        os.environ.setdefault(key, value)


_load_dotenv()

import discord
from discord.ext import commands

from utils.config import COMMAND_PREFIX
from utils.db import Database
from utils.logger import get_logger, init_root_logging
from utils.settings_store import SettingsStore

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.voice_states = True
intents.messages = True

bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents)
logger = get_logger("bot")


@bot.event
async def setup_hook() -> None:
    await bot.load_extension("cogs.tts")
    await bot.load_extension("cogs.webui")

    # Slash commands are global by default (can take a while to propagate).
    # For faster iteration, set `DEV_GUILD_ID` to sync instantly to one guild.
    dev_guild_id = os.getenv("DEV_GUILD_ID")
    try:
        if dev_guild_id:
            guild = discord.Object(id=int(dev_guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            logger.info("Synced application commands to guild %s", dev_guild_id)
        else:
            await bot.tree.sync()
            logger.info("Synced application commands globally")
    except Exception as exc:
        logger.warning("Failed to sync application commands: %s", exc)


@bot.event
async def on_ready() -> None:
    logger.info("Logged in as %s", bot.user)


async def main() -> None:
    loop = asyncio.get_running_loop()
    bot.start_time = time.time()
    bot.log_buffer = init_root_logging(loop)
    bot.settings = SettingsStore(os.getenv("SETTINGS_PATH") or "settings.json")
    await bot.settings.load()
    logger.info("Settings file: %s", bot.settings.path)

    bot.db = Database(os.getenv("DB_PATH") or "data/tts.db")
    await bot.db.connect()
    logger.info("Database file: %s", bot.db.path)

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set")
    try:
        await bot.start(token)
    finally:
        await bot.db.close()


if __name__ == "__main__":
    asyncio.run(main())
