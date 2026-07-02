"""Discord internship bot entry point.

Run locally with:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import List

import discord
from discord import app_commands
from discord.ext import commands, tasks

from database.db import init_db, mark_posted, stats, upsert_internship
from scanner import run_scan
from scraper.jobright_manual import build_manual_jobright_job
from scraper.linkedin_manual import build_manual_linkedin_job
from utils.config_loader import load_config, save_config
from utils.formatting import chunk_list, internship_to_embed
from utils.source_store import add_source, load_sources, remove_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("internship-bot")

config = load_config()

intents = discord.Intents.default()
# Slash commands do not need message_content intent. Keeping it off makes setup easier.
bot = commands.Bot(command_prefix="!", intents=intents)
startup_scan_completed = False


def get_post_channel() -> discord.TextChannel | None:
    channel_id = str(config.get("discord_channel_id", "")).strip()
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


async def post_jobs_to_discord(jobs: List[dict]) -> int:
    """Post jobs in small embed batches and return the count posted."""
    channel = get_post_channel()
    if channel is None:
        LOGGER.warning("No valid Discord channel configured. Use /set_channel first.")
        return 0

    max_posts = int(config.get("max_posts_per_scan", 20))
    jobs_to_post = jobs[:max_posts]
    posted_ids: List[int] = []

    # Discord allows up to 10 embeds per message. Use 5 for cleaner reading.
    for batch in chunk_list(jobs_to_post, 5):
        embeds = [internship_to_embed(job) for job in batch]
        await channel.send(embeds=embeds)
        posted_ids.extend([job["id"] for job in batch if "id" in job])
        await asyncio.sleep(1)

    mark_posted(posted_ids)
    return len(jobs_to_post)


async def scan_and_post() -> dict:
    result = run_scan(config)
    posted_count = await post_jobs_to_discord(result["new_jobs"])
    result["posted_count"] = posted_count
    return result


@bot.event
async def on_ready() -> None:
    global startup_scan_completed

    init_db()
    LOGGER.info("Logged in as %s", bot.user)

    # Guild sync is much faster than global sync while building/testing locally.
    guild_id = str(config.get("discord_guild_id", "")).strip()
    try:
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            bot.tree.copy_global_to(guild=guild)
            await bot.tree.sync(guild=guild)
            LOGGER.info("Slash commands synced to guild %s", guild_id)
        else:
            await bot.tree.sync()
            LOGGER.info("Slash commands synced globally. This can take a while to appear.")
    except Exception:
        LOGGER.exception("Failed to sync slash commands")

    if config.get("auto_scan_enabled") and not scheduled_scan.is_running():
        scheduled_scan.change_interval(minutes=int(config.get("scan_interval_minutes", 240)))
        scheduled_scan.start()

    if config.get("auto_scan_on_start") and not startup_scan_completed:
        LOGGER.info("auto_scan_on_start is enabled. Running first scan.")
        try:
            await scan_and_post()
            startup_scan_completed = True
        except Exception:
            LOGGER.exception("Startup scan failed")


@tasks.loop(minutes=240)
async def scheduled_scan() -> None:
    try:
        LOGGER.info("Running scheduled internship scan")
        await scan_and_post()
    except Exception:
        LOGGER.exception("Scheduled scan failed")


@scheduled_scan.before_loop
async def before_scheduled_scan() -> None:
    await asyncio.sleep(int(config.get("scan_interval_minutes", 240)) * 60)


@bot.tree.command(name="scan", description="Manually scan all enabled internship sources.")
async def scan_command(interaction: discord.Interaction) -> None:
    await interaction.response.defer(ephemeral=True, thinking=True)
    result = await scan_and_post()
    await interaction.followup.send(
        "Scan complete. "
        f"Sources scanned: {result['sources_scanned']}. "
        f"Found after filters: {result['total_found_after_filters']}. "
        f"New jobs: {len(result['new_jobs'])}. "
        f"Posted: {result['posted_count']}. "
        f"Errors: {len(result['errors'])}.",
        ephemeral=True,
    )


@bot.tree.command(name="add_source", description="Add a GitHub internship README source URL.")
@app_commands.describe(url="GitHub repository or README URL")
async def add_source_command(interaction: discord.Interaction, url: str) -> None:
    if "github.com" not in url and "raw.githubusercontent.com" not in url:
        await interaction.response.send_message(
            "Please add a GitHub README/repository URL. LinkedIn/Jobright links should use /add_manual_job.",
            ephemeral=True,
        )
        return
    source = add_source(url, "github_readme")
    await interaction.response.send_message(
        f"Added/enabled source `{source['id']}`: {source['url']}",
        ephemeral=True,
    )


@bot.tree.command(name="list_sources", description="Show all saved internship sources.")
async def list_sources_command(interaction: discord.Interaction) -> None:
    sources = load_sources()
    if not sources:
        await interaction.response.send_message("No sources saved yet.", ephemeral=True)
        return

    lines = []
    for source in sources:
        status = "enabled" if source.get("enabled", True) else "disabled"
        lines.append(f"`{source['id']}` | {status} | {source.get('type')} | {source.get('url')}")

    message = "\n".join(lines)
    if len(message) > 1900:
        message = message[:1900] + "\n..."
    await interaction.response.send_message(message, ephemeral=True)


@bot.tree.command(name="remove_source", description="Remove a source by ID or exact URL.")
@app_commands.describe(url_or_id="Source ID from /list_sources or the exact URL")
async def remove_source_command(interaction: discord.Interaction, url_or_id: str) -> None:
    removed = remove_source(url_or_id)
    if removed:
        await interaction.response.send_message("Source removed.", ephemeral=True)
    else:
        await interaction.response.send_message("Could not find that source.", ephemeral=True)


@bot.tree.command(name="set_channel", description="Set this channel as the internship posting channel.")
async def set_channel_command(interaction: discord.Interaction) -> None:
    config["discord_channel_id"] = str(interaction.channel_id)
    save_config(config)
    await interaction.response.send_message(
        f"Set <#{interaction.channel_id}> as the internship posting channel.",
        ephemeral=True,
    )


@bot.tree.command(name="status", description="Show bot status and scan stats.")
async def status_command(interaction: discord.Interaction) -> None:
    current_stats = stats()
    sources = load_sources()
    enabled_sources = [source for source in sources if source.get("enabled", True)]
    channel_id = config.get("discord_channel_id") or "Not set"
    await interaction.response.send_message(
        "**Internship Bot Status**\n"
        f"Bot user: `{bot.user}`\n"
        f"Posting channel: `{channel_id}`\n"
        f"Auto scan: `{config.get('auto_scan_enabled')}` every `{config.get('scan_interval_minutes')}` minutes\n"
        f"Scan on startup: `{config.get('auto_scan_on_start')}`\n"
        f"Sources: `{len(enabled_sources)}` enabled / `{len(sources)}` total\n"
        f"Last scan: `{current_stats['last_scan_time']}`\n"
        f"Jobs found last scan: `{current_stats['last_scan_found_count']}`\n"
        f"Total jobs in DB: `{current_stats['total']}`\n"
        f"Unposted jobs: `{current_stats['unposted']}`\n"
        f"Applied jobs: `{current_stats['applied']}`",
        ephemeral=True,
    )


@bot.tree.command(name="add_manual_job", description="Save a LinkedIn or Jobright job link manually.")
@app_commands.describe(
    source="Choose linkedin or jobright",
    url="Job link you manually copied",
    company="Company name",
    title="Role title",
    location="Location",
)
async def add_manual_job_command(
    interaction: discord.Interaction,
    source: str,
    url: str,
    company: str = "Unknown Company",
    title: str = "Internship Link",
    location: str = "Unknown",
) -> None:
    source_lower = source.lower().strip()
    if source_lower == "linkedin":
        job = build_manual_linkedin_job(url, company, title, location)
    elif source_lower == "jobright":
        job = build_manual_jobright_job(url, company, title, location)
    else:
        await interaction.response.send_message("Source must be `linkedin` or `jobright`.", ephemeral=True)
        return

    db_id, is_new = upsert_internship(job)
    job["id"] = db_id

    posted = 0
    if is_new:
        posted = await post_jobs_to_discord([job])

    await interaction.response.send_message(
        f"Manual job saved. New: `{is_new}`. Posted: `{posted}`.",
        ephemeral=True,
    )


@bot.tree.command(name="help", description="Show available commands.")
async def help_command(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(
        "**Commands**\n"
        "`/scan` — manually scan all enabled GitHub sources\n"
        "`/add_source <url>` — add a GitHub internship repo/README\n"
        "`/list_sources` — show saved sources\n"
        "`/remove_source <url_or_id>` — remove a source\n"
        "`/set_channel` — set this channel as the posting channel\n"
        "`/status` — show bot status and database stats\n"
        "`/add_manual_job <source> <url> [company] [title] [location]` — save LinkedIn/Jobright links manually\n"
        "`/help` — show this message",
        ephemeral=True,
    )


def main() -> None:
    token = config.get("discord_token", "")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is missing. Copy .env.example to .env and add your token.")
    bot.run(token)


if __name__ == "__main__":
    main()
