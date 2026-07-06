# Full Project Files

This document lists every tracked project file, what it does, and its full
content. It is generated — do not hand-edit it, changes will be overwritten.

Regenerate with:

```bash
python scripts/generate_full_project_doc.py
```

## `bot.py`

**What it does:** Discord internship bot entry point.

```python
"""Discord internship bot entry point.

Run locally with:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

import discord
import requests
from discord import app_commands
from discord.ext import commands, tasks

from database.db import (
    get_db_file_size_bytes,
    get_member_profile,
    get_unposted,
    init_db,
    list_member_profiles,
    mark_posted,
    run_storage_maintenance,
    set_member_profile,
    stats,
    upsert_internship,
)
from scanner import run_scan
from scraper.jobright_manual import build_manual_jobright_job
from scraper.linkedin_manual import build_manual_linkedin_job
from utils.config_loader import load_config, save_config
from utils.formatting import chunk_list, internship_to_embed, personal_match_to_embed
from utils.personalization import score_personal_match
from utils.relevance import NEUTRAL_QUALITY_SCORE
from utils.source_store import add_source, load_sources, remove_source

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
LOGGER = logging.getLogger("internship-bot")

config = load_config()

intents = discord.Intents.default()
# Slash commands do not need message_content intent. Keeping it off makes setup easier.
# Members intent IS required (and privileged — enable "Server Members Intent" in the
# Discord Developer Portal's Bot tab) for the premium tier: it's how the bot resolves
# which members hold the premium role so it knows who to send personalized DMs to.
intents.members = True
bot = commands.Bot(command_prefix="!", intents=intents)
startup_scan_completed = False


def get_post_channel() -> discord.TextChannel | None:
    channel_id = str(config.get("discord_channel_id", "")).strip()
    if not channel_id:
        return None
    channel = bot.get_channel(int(channel_id))
    return channel if isinstance(channel, discord.TextChannel) else None


def is_premium_member(member: discord.Member) -> bool:
    """Premium status is just: does this member hold the configured role.

    Officers assign/remove the role manually (e.g. when dues are paid), so
    there's no billing logic here at all — just a role check.
    """
    role_id = str(config.get("premium_role_id", "")).strip()
    if not role_id:
        return False
    return any(str(role.id) == role_id for role in getattr(member, "roles", []))


def get_premium_guild() -> discord.Guild | None:
    """The single guild this bot serves (premium DMs are single-server for now)."""
    guild_id = str(config.get("discord_guild_id", "")).strip()
    if guild_id:
        return bot.get_guild(int(guild_id))
    if len(bot.guilds) == 1:
        return bot.guilds[0]
    return None


async def post_jobs_to_discord(jobs: List[dict]) -> int:
    """Post jobs in small embed batches and return the count posted."""
    channel = get_post_channel()
    if channel is None:
        LOGGER.warning("No valid Discord channel configured. Use /set_channel first.")
        return 0

    max_posts = int(config.get("max_posts_per_scan", 20))
    jobs_to_post = jobs[:max_posts]
    posted_count = 0

    # Discord allows up to 10 embeds per message. Use 5 for cleaner reading.
    for batch in chunk_list(jobs_to_post, 5):
        embeds = [internship_to_embed(job) for job in batch]
        try:
            await channel.send(embeds=embeds)
        except discord.HTTPException:
            LOGGER.exception("Failed to post a batch of %s job(s); will retry next scan", len(batch))
            continue

        # Mark this batch posted immediately so a later batch's failure can't
        # cause an already-sent batch to be re-sent on the next scan.
        mark_posted([job["id"] for job in batch if "id" in job])
        posted_count += len(batch)
        await asyncio.sleep(1)

    return posted_count


def _merge_unique_jobs(*job_lists: List[dict]) -> List[dict]:
    """Combine job lists, keeping the first occurrence of each database id."""
    merged: List[dict] = []
    seen_ids = set()
    for jobs in job_lists:
        for job in jobs:
            job_id = job.get("id")
            if job_id is not None:
                if job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
            merged.append(job)
    return merged


def _quality_score(job: dict) -> int:
    score = job.get("quality_score")
    return score if isinstance(score, int) else NEUTRAL_QUALITY_SCORE


def build_personal_digests(
    new_jobs: List[dict], profiles_by_user_id: Dict[str, str], config: dict
) -> Dict[str, List[dict]]:
    """Score this scan's new jobs against each premium member's profile blurb
    and keep their top matches. Does many blocking Ollama calls, so callers
    should run this via asyncio.to_thread instead of awaiting it directly.
    """
    if not new_jobs or not profiles_by_user_id:
        return {}

    top_n = int(config.get("personal_digest_top_n", 5))
    min_score = int(config.get("personal_digest_min_score", 4))
    digests: Dict[str, List[dict]] = {}

    for user_id, blurb in profiles_by_user_id.items():
        matches = []
        for job in new_jobs:
            verdict = score_personal_match(job, blurb, config)
            if verdict.match_score >= min_score:
                matches.append({**job, "match_score": verdict.match_score, "match_reason": verdict.reason})
        matches.sort(key=lambda job: job["match_score"], reverse=True)
        if matches:
            digests[user_id] = matches[:top_n]

    return digests


async def send_personal_digests(digests: Dict[str, List[dict]], guild: discord.Guild) -> None:
    for user_id, matches in digests.items():
        member = guild.get_member(int(user_id))
        if member is None:
            LOGGER.warning("Premium member %s not found in guild cache; skipping their digest", user_id)
            continue

        embeds = [
            personal_match_to_embed(job, job["match_score"], job["match_reason"]) for job in matches
        ]
        for index, batch in enumerate(chunk_list(embeds, 5)):
            content = "Your personalized internship matches from this scan:" if index == 0 else None
            try:
                await member.send(content=content, embeds=batch)
            except discord.Forbidden:
                LOGGER.warning("Could not DM premium member %s (DMs closed); skipping", user_id)
                break
            except discord.HTTPException:
                LOGGER.exception("Failed to send a personal digest batch to %s", user_id)
                continue


async def send_premium_digests(new_jobs: List[dict]) -> None:
    """Premium members (see /set_premium_role) with a saved /set_profile blurb
    get a personalized DM highlighting their best matches from this scan, on
    top of (not instead of) the shared channel feed everyone else gets."""
    if not config.get("premium_role_id"):
        return

    guild = get_premium_guild()
    if guild is None:
        return

    profiles = list_member_profiles()
    if not profiles:
        return

    premium_user_ids = {str(member.id) for member in guild.members if is_premium_member(member)}
    relevant_profiles = {uid: blurb for uid, blurb in profiles.items() if uid in premium_user_ids}
    if not relevant_profiles:
        return

    digests = await asyncio.to_thread(build_personal_digests, new_jobs, relevant_profiles, config)
    if digests:
        await send_personal_digests(digests, guild)


async def scan_and_post() -> dict:
    # Scanning does blocking network I/O; run it off the event loop so the bot
    # keeps responding to Discord (heartbeats, other commands) while it scans.
    result = await asyncio.to_thread(run_scan, config)

    # Jobs found in an earlier scan but never posted (because that scan hit
    # max_posts_per_scan) are queued here so they get caught up instead of lost.
    max_posts = int(config.get("max_posts_per_scan", 20))
    backlog = get_unposted(limit=max_posts * 5)
    jobs_to_post = _merge_unique_jobs(backlog, result["new_jobs"])

    # Best matches first, so when there are more postings than max_posts_per_scan
    # can send in one go, the strongest ones win the scarce slots instead of
    # whichever happened to appear earliest in a README. Sort is stable, so
    # postings with equal (or no) score keep their original FIFO order.
    jobs_to_post.sort(key=_quality_score, reverse=True)

    posted_count = await post_jobs_to_discord(jobs_to_post)
    result["posted_count"] = posted_count

    try:
        await send_premium_digests(result["new_jobs"])
    except Exception:
        LOGGER.exception("Premium digest step failed; shared-channel posting is unaffected")

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

    if config.get("uptime_kuma_push_url") and not heartbeat.is_running():
        heartbeat.change_interval(minutes=int(config.get("heartbeat_interval_minutes", 5)))
        heartbeat.start()

    if not storage_maintenance.is_running():
        storage_maintenance.change_interval(
            hours=int(config.get("storage_maintenance_interval_hours", 24))
        )
        storage_maintenance.start()

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


@tasks.loop(minutes=5)
async def heartbeat() -> None:
    """Ping an Uptime Kuma Push monitor so it can alert if this process dies
    or hangs. Bot has no HTTP surface, so push (we ping it) instead of pull
    (it pings us) is the natural fit. No-ops if unconfigured.
    """
    push_url = str(config.get("uptime_kuma_push_url", "")).strip()
    if not push_url:
        return
    try:
        await asyncio.to_thread(requests.get, push_url, timeout=10)
    except requests.RequestException:
        # Don't retry harder or crash — Kuma itself being briefly unreachable
        # isn't this bot's problem, and the next tick will try again shortly.
        LOGGER.warning("Uptime Kuma heartbeat request failed (network issue)")


@tasks.loop(hours=24)
async def storage_maintenance() -> None:
    """Prune stale rows and reclaim disk space on a schedule. Always runs
    (not config-gated) since it has no external dependency and is cheap even
    when there's nothing to prune; data_retention_days<=0 skips pruning
    specifically while still checkpointing/vacuuming.
    """
    retention_days = int(config.get("data_retention_days", 180))
    try:
        result = await asyncio.to_thread(run_storage_maintenance, retention_days)
        LOGGER.info("Storage maintenance done: pruned %s old row(s)", result["deleted"])
    except Exception:
        LOGGER.exception("Storage maintenance failed")


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


@bot.tree.command(
    name="set_premium_role",
    description="Set which role counts as a premium member (admin only).",
)
@app_commands.describe(role="The role that marks someone as a paid/premium member")
@app_commands.default_permissions(manage_guild=True)
@app_commands.guild_only()
async def set_premium_role_command(interaction: discord.Interaction, role: discord.Role) -> None:
    config["premium_role_id"] = str(role.id)
    save_config(config)
    await interaction.response.send_message(
        f"Set {role.mention} as the premium member role. Members with this role get a "
        "personalized DM digest once they run /set_profile.",
        ephemeral=True,
    )


@bot.tree.command(
    name="set_profile",
    description="Premium members: set your internship interests for a personalized DM digest.",
)
@app_commands.describe(
    blurb="1-3 sentences: skills, interests, location, level (e.g. 'Backend/Go, remote OK, sophomore')"
)
@app_commands.guild_only()
async def set_profile_command(interaction: discord.Interaction, blurb: str) -> None:
    member = interaction.user
    if not isinstance(member, discord.Member) or not is_premium_member(member):
        await interaction.response.send_message(
            "Personalized matching is a premium-member feature. Ask an officer about "
            "premium membership.",
            ephemeral=True,
        )
        return

    blurb = blurb.strip()
    if not blurb:
        await interaction.response.send_message("Profile can't be empty.", ephemeral=True)
        return

    set_member_profile(str(member.id), blurb)
    await interaction.response.send_message(
        "Saved. You'll get a personalized DM after each scan highlighting your best matches.",
        ephemeral=True,
    )


@bot.tree.command(name="my_profile", description="Show your saved internship interest profile.")
@app_commands.guild_only()
async def my_profile_command(interaction: discord.Interaction) -> None:
    member = interaction.user
    premium = isinstance(member, discord.Member) and is_premium_member(member)
    blurb = get_member_profile(str(interaction.user.id))

    lines = [f"Premium member: `{premium}`"]
    if blurb:
        lines.append(f"Saved profile: {blurb}")
    elif premium:
        lines.append("No profile saved yet. Use `/set_profile` to add one.")
    else:
        lines.append("Personalized matching is a premium-member feature.")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="status", description="Show bot status and scan stats.")
async def status_command(interaction: discord.Interaction) -> None:
    current_stats = stats()
    sources = load_sources()
    enabled_sources = [source for source in sources if source.get("enabled", True)]
    channel_id = config.get("discord_channel_id") or "Not set"
    db_size_mb = get_db_file_size_bytes() / (1024 * 1024)
    await interaction.response.send_message(
        "**Internship Bot Status**\n"
        f"Bot user: `{bot.user}`\n"
        f"Posting channel: `{channel_id}`\n"
        f"Auto scan: `{config.get('auto_scan_enabled')}` every `{config.get('scan_interval_minutes')}` minutes\n"
        f"Scan on startup: `{config.get('auto_scan_on_start')}`\n"
        f"LLM relevance filter: `{config.get('llm_filter_enabled', False)}`\n"
        f"Premium role: `{'configured' if config.get('premium_role_id') else 'not set'}`\n"
        f"Uptime Kuma heartbeat: `{'enabled' if heartbeat.is_running() else 'disabled'}`\n"
        f"Sources: `{len(enabled_sources)}` enabled / `{len(sources)}` total\n"
        f"Last scan: `{current_stats['last_scan_time']}`\n"
        f"Jobs found last scan: `{current_stats['last_scan_found_count']}`\n"
        f"Total jobs in DB: `{current_stats['total']}`\n"
        f"Unposted jobs: `{current_stats['unposted']}`\n"
        f"Applied jobs: `{current_stats['applied']}`\n"
        f"Database size: `{db_size_mb:.2f} MB`\n"
        f"Data retention: `{config.get('data_retention_days', 180)}` days",
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
        "`/set_premium_role <role>` — admin: set which role gets personalized DM digests\n"
        "`/set_profile <blurb>` — premium members: set your interests for personalized matching\n"
        "`/my_profile` — show your saved profile and premium status\n"
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
```

## `scanner.py`

**What it does:** Run all enabled internship sources and store new jobs in SQLite.

```python
"""Run all enabled internship sources and store new jobs in SQLite."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.db import init_db, set_state, update_internship_relevance, upsert_internship
from scraper.github_scraper import scrape_github_readme
from utils.filters import passes_filters
from utils.relevance import classify_relevance
from utils.source_store import get_enabled_sources, update_source_fetch_cache

LOGGER = logging.getLogger(__name__)


def run_scan(config: Dict[str, Any]) -> Dict[str, Any]:
    """Scan enabled sources and return a summary."""
    init_db()
    sources = get_enabled_sources()
    include_keywords = config.get("include_keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])
    llm_filter_enabled = bool(config.get("llm_filter_enabled", False))
    llm_min_quality_score = int(config.get("llm_min_quality_score", 1))

    total_found = 0
    total_after_filters = 0
    new_jobs: List[Dict[str, Any]] = []
    errors: List[str] = []

    for source in sources:
        source_url = source.get("url", "")
        source_type = source.get("type", "github_readme")
        try:
            if source_type == "github_readme":
                result = scrape_github_readme(
                    source_url,
                    preferred_url=source.get("resolved_raw_url", ""),
                    etag=source.get("etag", ""),
                )
                update_source_fetch_cache(source.get("id", ""), result.raw_url, result.etag)
                internships = result.internships
            else:
                LOGGER.info("Skipping unsupported automated source type: %s", source_type)
                continue

            total_found += len(internships)

            for internship in internships:
                if not passes_filters(internship, include_keywords, exclude_keywords):
                    continue
                total_after_filters += 1
                db_id, is_new = upsert_internship(internship)
                internship["id"] = db_id

                # Still store closed roles (keeps dedupe/dashboard accurate), but
                # don't post something to Discord that's already unavailable.
                if not is_new or internship.get("status") == "closed":
                    continue

                if llm_filter_enabled:
                    # Only spend an LLM call on postings that are actually new —
                    # the whole point is to rank/trim what we're about to post.
                    verdict = classify_relevance(internship, config)
                    internship["quality_score"] = verdict.quality_score
                    internship["llm_reason"] = verdict.reason
                    update_internship_relevance(db_id, verdict.quality_score, verdict.reason)
                    if not verdict.relevant or verdict.quality_score < llm_min_quality_score:
                        continue

                new_jobs.append(internship)

        except Exception as exc:  # Keep scanning even if one repo breaks.
            message = f"{source_url}: {exc}"
            LOGGER.exception("Source failed: %s", message)
            errors.append(message)

    scan_time = datetime.now(timezone.utc).isoformat()
    set_state("last_scan_time", scan_time)
    set_state("last_scan_found_count", str(total_after_filters))

    return {
        "sources_scanned": len(sources),
        "total_found_before_filters": total_found,
        "total_found_after_filters": total_after_filters,
        "new_jobs": new_jobs,
        "errors": errors,
        "scan_time": scan_time,
    }
```

## `scraper/__init__.py`

**What it does:** See file contents below.

```python

```

## `scraper/github_scraper.py`

**What it does:** GitHub README internship scraper.

```python
"""GitHub README internship scraper.

This scraper is intentionally simple and safe:
- It fetches public GitHub README markdown through raw.githubusercontent.com.
- It parses Markdown tables.
- It does not log in, bypass rate limits, or scrape private data.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from utils.tags import add_company_classification_tag

LOGGER = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    internships: List[Dict]
    raw_url: str
    etag: str
    not_modified: bool

REQUEST_HEADERS = {
    "User-Agent": "local-discord-internship-bot/1.0 (+https://github.com/)"
}

TAG_KEYWORDS = {
    "software": ["software", "swe", "developer", "full stack", "fullstack"],
    "backend": ["backend", "back-end", "server"],
    "frontend": ["frontend", "front-end", "react", "web"],
    "data": ["data", "analytics", "analyst"],
    "ai": ["ai", "machine learning", "ml", "llm", "generative"],
    "quant": ["quant", "trading", "researcher", "finance"],
    "hardware": ["hardware", "electrical", "embedded", "fpga", "asic"],
    "gpu": ["gpu", "cuda", "parallel", "compiler", "accelerator"],
    "cloud": ["cloud", "aws", "azure", "gcp", "platform"],
    "security": ["security", "cyber", "threat"],
}


def scrape_github_readme(
    source_url: str, preferred_url: str = "", etag: str = ""
) -> ScrapeResult:
    """Fetch a GitHub README and return normalized internship dictionaries.

    ``preferred_url`` is the raw URL that resolved successfully last time (if
    any), so repeat scans can skip straight to the right branch instead of
    guessing HEAD/main/master/dev again. ``etag`` lets the server tell us
    nothing changed (HTTP 304) so we can skip re-parsing entirely.
    """
    markdown, raw_url, new_etag, not_modified = fetch_readme(
        source_url, preferred_url=preferred_url, etag=etag
    )
    if not_modified:
        LOGGER.info("README unchanged since last scan for %s", source_url)
        return ScrapeResult(internships=[], raw_url=raw_url, etag=new_etag, not_modified=True)

    internships = parse_markdown_tables(markdown, source_url=source_url, raw_url=raw_url)
    LOGGER.info("Scraped %s internships from %s", len(internships), source_url)
    return ScrapeResult(internships=internships, raw_url=raw_url, etag=new_etag, not_modified=False)


def fetch_readme(
    source_url: str, preferred_url: str = "", etag: str = ""
) -> Tuple[str, str, str, bool]:
    """Fetch README markdown from GitHub.

    Tries ``preferred_url`` first when given (the URL that worked last scan),
    then falls back through the usual branch-name candidates. Returns
    ``(markdown, raw_url, etag, not_modified)``; ``markdown`` is empty and
    ``not_modified`` is True when the server confirms nothing changed.
    """
    candidate_urls = build_raw_candidates(source_url)
    if preferred_url:
        candidate_urls = [preferred_url] + [url for url in candidate_urls if url != preferred_url]

    last_error: Optional[Exception] = None

    for raw_url in candidate_urls:
        headers = dict(REQUEST_HEADERS)
        if etag and raw_url == preferred_url:
            headers["If-None-Match"] = etag

        try:
            response = requests.get(raw_url, headers=headers, timeout=20)
            if response.status_code == 304:
                return "", raw_url, etag, True
            if response.status_code == 200 and response.text.strip():
                return response.text, raw_url, response.headers.get("ETag", ""), False
            LOGGER.warning("README fetch failed %s: HTTP %s", raw_url, response.status_code)
        except requests.RequestException as exc:
            last_error = exc
            LOGGER.warning("README fetch error %s: %s", raw_url, exc)

    if last_error:
        raise RuntimeError(f"Could not fetch README for {source_url}: {last_error}")
    raise RuntimeError(f"Could not fetch README for {source_url}")


def build_raw_candidates(source_url: str) -> List[str]:
    parsed = urlparse(source_url)

    if parsed.netloc == "raw.githubusercontent.com":
        return [source_url]

    if "github.com" not in parsed.netloc:
        raise ValueError("Only GitHub README URLs are supported by github_scraper.py")

    path_parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(path_parts) < 2:
        raise ValueError("GitHub URL must look like https://github.com/owner/repo")

    owner, repo = path_parts[0], path_parts[1].replace(".git", "")

    # Direct file URL: https://github.com/owner/repo/blob/branch/path/to/README.md
    if len(path_parts) >= 5 and path_parts[2] == "blob":
        branch = path_parts[3]
        file_path = "/".join(path_parts[4:])
        return [f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{file_path}"]

    # GitHub raw supports HEAD in many cases. We also try common defaults.
    return [
        f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/master/README.md",
        f"https://raw.githubusercontent.com/{owner}/{repo}/dev/README.md",
    ]


def parse_markdown_tables(markdown: str, source_url: str, raw_url: str = "") -> List[Dict]:
    lines = markdown.splitlines()
    internships: List[Dict] = []
    current_section = ""
    previous_company = ""
    seen_keys = set()
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        if line.startswith("#"):
            current_section = clean_text(line.lstrip("#").strip())
            i += 1
            continue

        if _looks_like_table_start(lines, i):
            header_cells = split_table_row(lines[i])
            normalized_headers = [normalize_header(cell) for cell in header_cells]
            i += 2  # skip separator row

            while i < len(lines) and "|" in lines[i]:
                row_cells = split_table_row(lines[i])
                if len(row_cells) < 2:
                    i += 1
                    continue

                internship, previous_company = parse_table_row(
                    headers=normalized_headers,
                    cells=row_cells,
                    previous_company=previous_company,
                    source_url=source_url,
                    section=current_section,
                )

                if internship:
                    key = (
                        internship["company"].lower(),
                        internship["title"].lower(),
                        internship.get("application_url", "").lower(),
                    )
                    if key not in seen_keys:
                        internships.append(internship)
                        seen_keys.add(key)
                i += 1
            continue

        i += 1

    return internships


def _looks_like_table_start(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    return "|" in lines[index] and is_separator_row(lines[index + 1])


def is_separator_row(line: str) -> bool:
    cleaned = line.strip().strip("|").replace(" ", "")
    if not cleaned:
        return False
    cells = cleaned.split("|")
    return all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def split_table_row(line: str) -> List[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def normalize_header(header: str) -> str:
    header = clean_text(header).lower()
    if any(word in header for word in ["company", "employer", "organization"]):
        return "company"
    if any(word in header for word in ["role", "title", "position", "job"]):
        return "title"
    if any(word in header for word in ["location", "office"]):
        return "location"
    if any(word in header for word in ["application", "apply", "link"]):
        return "application"
    if "age" in header or "date" in header:
        return "age"
    return header


def parse_table_row(
    headers: List[str],
    cells: List[str],
    previous_company: str,
    source_url: str,
    section: str,
) -> Tuple[Optional[Dict], str]:
    row = {headers[index] if index < len(headers) else f"col_{index}": cell for index, cell in enumerate(cells)}

    # Fall back to common table order: Company | Role | Location | Application | Age
    company_cell = row.get("company", cells[0] if len(cells) > 0 else "")
    title_cell = row.get("title", cells[1] if len(cells) > 1 else "")
    location_cell = row.get("location", cells[2] if len(cells) > 2 else "")
    application_cell = row.get("application", cells[3] if len(cells) > 3 else "")
    uploaded_cell = row.get("age", cells[4] if len(cells) > 4 else "")

    company = extract_display_text(company_cell)
    title = extract_display_text(title_cell)
    location = extract_display_text(location_cell)
    uploaded_at = extract_display_text(uploaded_cell)
    application_url = extract_best_url(application_cell) or extract_best_url(title_cell) or extract_best_url(company_cell)

    # Some repos use ↳ for additional roles from the same company.
    if not company or company.startswith("↳") or company in {"-", "—"}:
        company = previous_company
    elif company:
        previous_company = company

    title = title.lstrip("↳").strip()

    if not company or not title:
        return None, previous_company

    status = "closed" if "closed" in " ".join(cells).lower() else "unknown"

    combined_text = " ".join([company, title, location, section])
    internship = {
        "company": company,
        "title": title,
        "location": location or "Unknown",
        "application_url": application_url or source_url,
        "source_url": source_url,
        "source_type": "github_readme",
        "date_found": datetime.now(timezone.utc).isoformat(),
        "uploaded_at": uploaded_at,
        "tags": add_company_classification_tag(infer_tags(combined_text), company),
        "status": status,
    }
    return internship, previous_company


def extract_display_text(markdown_cell: str) -> str:
    text = markdown_cell.strip()

    # Replace markdown links with their label.
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<br\s*/?>", ", ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    return clean_text(text)


def clean_text(text: str) -> str:
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\n\r|-")


def extract_best_url(markdown_cell: str) -> str:
    urls = re.findall(r"https?://[^)\s>]+", markdown_cell)
    if not urls:
        return ""

    cleaned_urls = [url.rstrip(")].,;") for url in urls]
    filtered = []
    for url in cleaned_urls:
        lower = url.lower()
        if "raw.githubusercontent.com" in lower:
            continue
        if lower.endswith(".svg") or lower.endswith(".png"):
            continue
        filtered.append(url)

    return filtered[0] if filtered else cleaned_urls[0]


def infer_tags(text: str) -> List[str]:
    lower = text.lower()
    tags: List[str] = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(keyword in lower for keyword in keywords):
            tags.append(tag)
    if "intern" in lower or "co-op" in lower or "coop" in lower:
        tags.append("internship")
    return list(dict.fromkeys(tags)) or ["internship"]
```

## `scraper/linkedin_manual.py`

**What it does:** Safe LinkedIn manual-link ingestion.

```python
"""Safe LinkedIn manual-link ingestion.

Direct LinkedIn scraping is intentionally not implemented. LinkedIn commonly blocks
bots and their policies prohibit scraping/automation. This module lets you paste a
job URL that you personally found and stores it in the same structure as scraped jobs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from utils.tags import add_company_classification_tag


def build_manual_linkedin_job(
    url: str,
    company: str = "Unknown Company",
    title: str = "LinkedIn Internship Link",
    location: str = "Unknown",
    tags: List[str] | None = None,
) -> Dict:
    return {
        "company": company.strip() or "Unknown Company",
        "title": title.strip() or "LinkedIn Internship Link",
        "location": location.strip() or "Unknown",
        "application_url": url.strip(),
        "source_url": url.strip(),
        "source_type": "linkedin_manual",
        "date_found": datetime.now(timezone.utc).isoformat(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "tags": add_company_classification_tag(tags or ["manual", "linkedin", "internship"], company),
        "status": "unknown",
    }
```

## `scraper/jobright_manual.py`

**What it does:** Safe Jobright manual-link ingestion.

```python
"""Safe Jobright manual-link ingestion.

This module does not scrape Jobright. It stores URLs you manually paste so the bot can
track them alongside GitHub-sourced internships.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List

from utils.tags import add_company_classification_tag


def build_manual_jobright_job(
    url: str,
    company: str = "Unknown Company",
    title: str = "Jobright Internship Link",
    location: str = "Unknown",
    tags: List[str] | None = None,
) -> Dict:
    return {
        "company": company.strip() or "Unknown Company",
        "title": title.strip() or "Jobright Internship Link",
        "location": location.strip() or "Unknown",
        "application_url": url.strip(),
        "source_url": url.strip(),
        "source_type": "jobright_manual",
        "date_found": datetime.now(timezone.utc).isoformat(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
        "tags": add_company_classification_tag(tags or ["manual", "jobright", "internship"], company),
        "status": "unknown",
    }
```

## `database/__init__.py`

**What it does:** See file contents below.

```python

```

## `database/db.py`

**What it does:** SQLite database helpers for internship storage and duplicate detection.

```python
"""SQLite database helpers for internship storage and duplicate detection."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from utils.tags import add_company_classification_tag

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "internships.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL lets the dashboard read while the bot writes (and vice versa) without
    # "database is locked" errors; busy_timeout makes writers retry instead of
    # failing immediately when they do briefly collide.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_value(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


_TRACKING_PARAM_PREFIXES = ("utm_",)
_TRACKING_PARAM_NAMES = {
    "fbclid",
    "gclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
    "igshid",
}


def strip_tracking_params(url: str) -> str:
    """Drop known tracking params (utm_*, fbclid, ...) and any fragment.

    Many ATS platforms (Greenhouse, Lever, Workday) encode the actual job id
    in a query param, so we only remove params known to be pure tracking
    noise rather than stripping the whole query string — otherwise two
    different real postings could collide into the same dedupe key.
    """
    if not url:
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    kept_params = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.lower() not in _TRACKING_PARAM_NAMES
        and not key.lower().startswith(_TRACKING_PARAM_PREFIXES)
    ]
    new_query = urlencode(kept_params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, new_query, ""))


def build_dedupe_key(company: str, title: str, application_url: str) -> str:
    raw = "|".join([
        normalize_value(company),
        normalize_value(title),
        normalize_value(strip_tracking_params(application_url)),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS internships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT UNIQUE NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                application_url TEXT,
                source_url TEXT,
                source_type TEXT,
                tags TEXT,
                uploaded_at TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                posted_to_discord INTEGER DEFAULT 0,
                status TEXT DEFAULT 'unknown'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS member_profiles (
                user_id TEXT PRIMARY KEY,
                blurb TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        _ensure_column(conn, "internships", "uploaded_at", "TEXT")
        _ensure_column(conn, "internships", "quality_score", "INTEGER")
        _ensure_column(conn, "internships", "llm_reason", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def upsert_internship(internship: Dict[str, Any]) -> Tuple[int, bool]:
    """
    Insert or update an internship.

    Returns (database_id, is_new). is_new is True only the first time a posting is seen.
    """
    init_db()
    company = internship.get("company") or "Unknown Company"
    title = internship.get("title") or "Unknown Internship"
    application_url = internship.get("application_url") or ""
    dedupe_key = build_dedupe_key(company, title, application_url)
    current_time = now_iso()
    tags_list = add_company_classification_tag(internship.get("tags", []), company)
    internship["tags"] = tags_list
    tags = ",".join(tags_list)
    uploaded_at = internship.get("uploaded_at") or internship.get("date_found") or ""

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM internships WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE internships
                SET last_seen = ?, location = COALESCE(NULLIF(?, ''), location),
                    source_url = COALESCE(NULLIF(?, ''), source_url),
                    source_type = COALESCE(NULLIF(?, ''), source_type),
                    tags = COALESCE(NULLIF(?, ''), tags),
                    uploaded_at = COALESCE(NULLIF(?, ''), uploaded_at)
                WHERE dedupe_key = ?
                """,
                (
                    current_time,
                    internship.get("location", ""),
                    internship.get("source_url", ""),
                    internship.get("source_type", ""),
                    tags,
                    uploaded_at,
                    dedupe_key,
                ),
            )
            conn.commit()
            return int(existing["id"]), False

        cursor = conn.execute(
            """
            INSERT INTO internships (
                dedupe_key, company, title, location, application_url, source_url,
                source_type, tags, uploaded_at, first_seen, last_seen, posted_to_discord, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                dedupe_key,
                company,
                title,
                internship.get("location", ""),
                application_url,
                internship.get("source_url", ""),
                internship.get("source_type", "unknown"),
                tags,
                uploaded_at,
                current_time,
                current_time,
                internship.get("status", "unknown"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid), True


def mark_posted(internship_ids: Iterable[int]) -> None:
    ids = list(internship_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        conn.execute(
            f"UPDATE internships SET posted_to_discord = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()


def list_internships(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM internships WHERE status = ? ORDER BY first_seen DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM internships ORDER BY first_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_unposted(limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM internships
            WHERE posted_to_discord = 0
            ORDER BY first_seen ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_internship_relevance(internship_id: int, quality_score: int, reason: str) -> None:
    """Persist the local-LLM relevance judgement so the dashboard and future
    scans (via get_unposted) can see and sort on it."""
    with _connect() as conn:
        conn.execute(
            "UPDATE internships SET quality_score = ?, llm_reason = ? WHERE id = ?",
            (quality_score, reason, internship_id),
        )
        conn.commit()


def update_internship_status(internship_id: int, status: str) -> None:
    allowed = {"active", "closed", "unknown", "applied", "ignored", "saved"}
    if status not in allowed:
        raise ValueError(f"Unsupported status: {status}")
    with _connect() as conn:
        conn.execute("UPDATE internships SET status = ? WHERE id = ?", (status, internship_id))
        conn.commit()


def set_state(key: str, value: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_state(key: str, default: str = "") -> str:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def stats() -> Dict[str, Any]:
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM internships").fetchone()["count"]
        unposted = conn.execute("SELECT COUNT(*) AS count FROM internships WHERE posted_to_discord = 0").fetchone()["count"]
        applied = conn.execute("SELECT COUNT(*) AS count FROM internships WHERE status = 'applied'").fetchone()["count"]
    return {
        "total": total,
        "unposted": unposted,
        "applied": applied,
        "last_scan_time": get_state("last_scan_time", "Never"),
        "last_scan_found_count": get_state("last_scan_found_count", "0"),
    }


def set_member_profile(user_id: str, blurb: str) -> None:
    """Save (or replace) a premium member's short interest blurb."""
    init_db()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO member_profiles(user_id, blurb, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET blurb = excluded.blurb, updated_at = excluded.updated_at
            """,
            (str(user_id), blurb, now_iso()),
        )
        conn.commit()


def get_member_profile(user_id: str) -> Optional[str]:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT blurb FROM member_profiles WHERE user_id = ?", (str(user_id),)
        ).fetchone()
    return row["blurb"] if row else None


def list_member_profiles() -> Dict[str, str]:
    """Return {user_id: blurb} for every member who has set a profile."""
    init_db()
    with _connect() as conn:
        rows = conn.execute("SELECT user_id, blurb FROM member_profiles").fetchall()
    return {row["user_id"]: row["blurb"] for row in rows}


_PRESERVED_STATUSES = {"active", "applied", "saved"}


def prune_old_internships(retention_days: int) -> int:
    """Delete stale closed/unknown/ignored postings older than retention_days.

    Postings a member has flagged active/applied/saved are kept regardless of
    age — those carry personal value the rest don't. Returns rows deleted.
    retention_days <= 0 disables pruning entirely (returns 0).
    """
    if retention_days <= 0:
        return 0

    init_db()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
    placeholders = ",".join("?" for _ in _PRESERVED_STATUSES)
    with _connect() as conn:
        cursor = conn.execute(
            f"""
            DELETE FROM internships
            WHERE last_seen < ?
              AND status NOT IN ({placeholders})
            """,
            (cutoff, *_PRESERVED_STATUSES),
        )
        conn.commit()
        return cursor.rowcount


def checkpoint_and_vacuum() -> None:
    """Flush the WAL file and reclaim disk space freed by deletes.

    Cheap on a small database, so it's safe to call on every maintenance tick
    regardless of whether pruning actually deleted anything this time.
    """
    conn = _connect()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.isolation_level = None  # VACUUM can't run inside a transaction.
        conn.execute("VACUUM")
    finally:
        conn.close()


def run_storage_maintenance(retention_days: int) -> Dict[str, int]:
    """Prune stale rows, then checkpoint/VACUUM. Meant to run on a schedule."""
    deleted = prune_old_internships(retention_days)
    checkpoint_and_vacuum()
    return {"deleted": deleted}


def get_db_file_size_bytes() -> int:
    return DB_PATH.stat().st_size if DB_PATH.exists() else 0


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["tags"] = [tag for tag in (data.get("tags") or "").split(",") if tag]
    return data
```

## `dashboard/app.py`

**What it does:** Optional local Flask dashboard.

```python
"""Optional local Flask dashboard.

Run with:
    python dashboard/app.py
Then open:
    http://localhost:5000
"""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

# Allow running this file directly from dashboard/ while importing project modules.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, Response, redirect, render_template, request, url_for

from database.db import init_db, list_internships, update_internship_status
from utils.source_store import add_source, load_sources, remove_source, set_source_enabled

app = Flask(__name__)

DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")


def _auth_configured() -> bool:
    return bool(DASHBOARD_USERNAME and DASHBOARD_PASSWORD)


def _credentials_match(username: str, password: str) -> bool:
    # compare_digest avoids leaking credential length/content via timing.
    return secrets.compare_digest(username, DASHBOARD_USERNAME) and secrets.compare_digest(
        password, DASHBOARD_PASSWORD
    )


@app.before_request
def require_auth():
    if not _auth_configured():
        return None
    auth = request.authorization
    if not auth or not _credentials_match(auth.username or "", auth.password or ""):
        return Response(
            "Authentication required.",
            401,
            {"WWW-Authenticate": 'Basic realm="Internship Bot Dashboard"'},
        )
    return None


@app.route("/")
def index():
    sources = load_sources()
    return render_template("index.html", sources=sources)


@app.post("/sources/add")
def add_source_route():
    url = request.form.get("url", "").strip()
    if url:
        add_source(url, "github_readme")
    return redirect(url_for("index"))


@app.post("/sources/remove")
def remove_source_route():
    source_id = request.form.get("source_id", "").strip()
    if source_id:
        remove_source(source_id)
    return redirect(url_for("index"))


@app.post("/sources/toggle")
def toggle_source_route():
    source_id = request.form.get("source_id", "").strip()
    enabled = request.form.get("enabled") == "true"
    if source_id:
        set_source_enabled(source_id, enabled)
    return redirect(url_for("index"))


@app.route("/internships")
def internships():
    init_db()
    jobs = list_internships(limit=250)
    return render_template("internships.html", jobs=jobs)


@app.post("/internships/status")
def update_status_route():
    internship_id = int(request.form.get("internship_id", "0"))
    status = request.form.get("status", "unknown")
    if internship_id:
        update_internship_status(internship_id, status)
    return redirect(url_for("internships"))


if __name__ == "__main__":
    init_db()

    # Defaults to loopback-only, non-debug, no auth required (nothing to
    # protect against on your own machine). Widen DASHBOARD_HOST only on a
    # network you trust, and set DASHBOARD_USERNAME/DASHBOARD_PASSWORD before
    # you do — see require_auth() above.
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    debug_mode = os.getenv("DASHBOARD_DEBUG", "false").strip().lower() in {"1", "true", "yes", "on"}

    if debug_mode and host != "127.0.0.1":
        raise RuntimeError(
            "Refusing to start with DASHBOARD_DEBUG enabled while DASHBOARD_HOST "
            "is not 127.0.0.1 — this would expose the Werkzeug debugger console."
        )
    if host != "127.0.0.1" and not _auth_configured():
        raise RuntimeError(
            "Refusing to start with DASHBOARD_HOST != 127.0.0.1 and no "
            "DASHBOARD_USERNAME/DASHBOARD_PASSWORD set — the dashboard has no "
            "other authentication and would be wide open on your network."
        )
    app.run(host=host, port=5000, debug=debug_mode)
```

## `dashboard/templates/index.html`

**What it does:** See file contents below.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Internship Bot Dashboard</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 1000px; margin: 40px auto; padding: 0 20px; background: #f7f7fb; color: #1f2937; }
    .card { background: white; border-radius: 14px; padding: 20px; margin-bottom: 20px; box-shadow: 0 6px 20px rgba(0,0,0,.06); }
    input[type="url"] { width: 70%; padding: 10px; border: 1px solid #d1d5db; border-radius: 8px; }
    button, .button { padding: 10px 14px; border: 0; border-radius: 8px; background: #111827; color: white; cursor: pointer; text-decoration: none; display: inline-block; }
    .danger { background: #b91c1c; }
    .muted { color: #6b7280; font-size: 14px; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: 12px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }
    code { background: #eef2ff; padding: 2px 6px; border-radius: 6px; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Discord Internship Bot Dashboard</h1>
    <p class="muted">Local-only dashboard. Do not expose this to the public internet.</p>
    <a class="button" href="{{ url_for('internships') }}">View internships</a>
  </div>

  <div class="card">
    <h2>Add GitHub source</h2>
    <form method="post" action="{{ url_for('add_source_route') }}">
      <input type="url" name="url" placeholder="https://github.com/SimplifyJobs/Summer2026-Internships" required>
      <button type="submit">Add source</button>
    </form>
  </div>

  <div class="card">
    <h2>Saved sources</h2>
    {% if sources %}
      <table>
        <thead>
          <tr><th>ID</th><th>Status</th><th>Type</th><th>URL</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {% for source in sources %}
            <tr>
              <td><code>{{ source.id }}</code></td>
              <td>{{ 'enabled' if source.enabled else 'disabled' }}</td>
              <td>{{ source.type }}</td>
              <td><a href="{{ source.url }}" target="_blank">{{ source.url }}</a></td>
              <td>
                <form method="post" action="{{ url_for('toggle_source_route') }}" style="display:inline">
                  <input type="hidden" name="source_id" value="{{ source.id }}">
                  <input type="hidden" name="enabled" value="{{ 'false' if source.enabled else 'true' }}">
                  <button type="submit">{{ 'Disable' if source.enabled else 'Enable' }}</button>
                </form>
                <form method="post" action="{{ url_for('remove_source_route') }}" style="display:inline">
                  <input type="hidden" name="source_id" value="{{ source.id }}">
                  <button class="danger" type="submit">Remove</button>
                </form>
              </td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p>No sources yet.</p>
    {% endif %}
  </div>
</body>
</html>
```

## `dashboard/templates/internships.html`

**What it does:** See file contents below.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Found Internships</title>
  <style>
    body { font-family: Arial, sans-serif; max-width: 1200px; margin: 40px auto; padding: 0 20px; background: #f7f7fb; color: #1f2937; }
    .card { background: white; border-radius: 14px; padding: 20px; margin-bottom: 20px; box-shadow: 0 6px 20px rgba(0,0,0,.06); }
    button, .button { padding: 8px 12px; border: 0; border-radius: 8px; background: #111827; color: white; cursor: pointer; text-decoration: none; display: inline-block; }
    table { width: 100%; border-collapse: collapse; font-size: 14px; }
    th, td { padding: 10px; border-bottom: 1px solid #e5e7eb; text-align: left; vertical-align: top; }
    select { padding: 7px; border-radius: 8px; border: 1px solid #d1d5db; }
    .muted { color: #6b7280; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Found Internships</h1>
    <a class="button" href="{{ url_for('index') }}">Back to sources</a>
    <p class="muted">Use the Discord bot <code>/scan</code> command to find new jobs, then refresh this page.</p>
  </div>

  <div class="card">
    {% if jobs %}
      <table>
        <thead>
          <tr>
            <th>Company</th><th>Role</th><th>Location</th><th>Tags</th><th>Status</th><th>Apply</th><th>Uploaded</th><th>First seen</th>
          </tr>
        </thead>
        <tbody>
          {% for job in jobs %}
            <tr>
              <td>{{ job.company }}</td>
              <td>{{ job.title }}</td>
              <td>{{ job.location }}</td>
              <td>{{ ', '.join(job.tags) }}</td>
              <td>
                <form method="post" action="{{ url_for('update_status_route') }}">
                  <input type="hidden" name="internship_id" value="{{ job.id }}">
                  <select name="status" onchange="this.form.submit()">
                    {% for option in ['unknown', 'active', 'saved', 'applied', 'ignored', 'closed'] %}
                      <option value="{{ option }}" {% if job.status == option %}selected{% endif %}>{{ option }}</option>
                    {% endfor %}
                  </select>
                </form>
              </td>
              <td><a href="{{ job.application_url }}" target="_blank">Open</a></td>
              <td>{{ job.uploaded_at or 'Unknown' }}</td>
              <td>{{ job.first_seen }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    {% else %}
      <p>No internships in the database yet. Run <code>/scan</code> in Discord first.</p>
    {% endif %}
  </div>
</body>
</html>
```

## `utils/config_loader.py`

**What it does:** Load and save local configuration for the internship bot.

```python
"""Load and save local configuration for the internship bot."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "discord_channel_id": "",
    "scan_interval_minutes": 240,
    "auto_scan_enabled": True,
    "auto_scan_on_start": True,
    "max_posts_per_scan": 20,
    "include_keywords": [
        "software",
        "swe",
        "intern",
        "internship",
        "co-op",
        "data",
        "ai",
        "machine learning",
        "backend",
        "frontend",
        "cloud",
        "quant",
        "gpu",
        "cuda",
        "hardware",
    ],
    "exclude_keywords": [
        "senior",
        "staff",
        "principal",
        "full-time",
        "new grad",
    ],
    # Optional second-pass filtering/ranking using a local Ollama model. Off by
    # default since it requires a running Ollama server with a model pulled.
    "llm_filter_enabled": False,
    "ollama_host": "http://192.168.1.84:11434",
    "ollama_model": "llama3.2:3b",
    "llm_timeout_seconds": 15,
    "llm_min_quality_score": 1,
    # Premium tier: members with this role get a personalized DM digest after
    # each scan (see /set_premium_role, /set_profile). Empty = feature off.
    "premium_role_id": "",
    "personal_digest_top_n": 5,
    "personal_digest_min_score": 4,
    # Uptime Kuma Push-monitor heartbeat. Empty = feature off (no HTTP surface
    # otherwise, so this is a periodic outbound ping, not an inbound check).
    "uptime_kuma_push_url": "",
    "heartbeat_interval_minutes": 5,
    # Storage maintenance always runs (no external dependency); this just
    # controls how far back it prunes. <= 0 disables pruning but still
    # checkpoints/vacuums the database on the same schedule.
    "data_retention_days": 180,
    "storage_maintenance_interval_hours": 24,
}


def load_config() -> Dict[str, Any]:
    """Load config.json and .env, then return one merged config dictionary."""
    load_dotenv(ROOT_DIR / ".env")

    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = json.load(f)
            config.update(user_config)

    # Environment variables win over config.json when present.
    if os.getenv("DISCORD_CHANNEL_ID"):
        config["discord_channel_id"] = os.getenv("DISCORD_CHANNEL_ID")
    if os.getenv("SCAN_INTERVAL_MINUTES"):
        config["scan_interval_minutes"] = int(os.getenv("SCAN_INTERVAL_MINUTES", "240"))
    if os.getenv("AUTO_SCAN_ENABLED"):
        config["auto_scan_enabled"] = os.getenv("AUTO_SCAN_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}
    if os.getenv("AUTO_SCAN_ON_START"):
        config["auto_scan_on_start"] = os.getenv("AUTO_SCAN_ON_START", "").strip().lower() in {"1", "true", "yes", "on"}
    if os.getenv("MAX_POSTS_PER_SCAN"):
        config["max_posts_per_scan"] = int(os.getenv("MAX_POSTS_PER_SCAN", "20"))
    if os.getenv("LLM_FILTER_ENABLED"):
        config["llm_filter_enabled"] = os.getenv("LLM_FILTER_ENABLED", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    if os.getenv("OLLAMA_HOST"):
        config["ollama_host"] = os.getenv("OLLAMA_HOST")
    if os.getenv("OLLAMA_MODEL"):
        config["ollama_model"] = os.getenv("OLLAMA_MODEL")
    if os.getenv("UPTIME_KUMA_PUSH_URL"):
        config["uptime_kuma_push_url"] = os.getenv("UPTIME_KUMA_PUSH_URL")

    config["discord_token"] = os.getenv("DISCORD_TOKEN", "")
    config["discord_guild_id"] = os.getenv("DISCORD_GUILD_ID", "")
    return config


def save_config(config: Dict[str, Any]) -> None:
    """Save non-secret config values back to config.json."""
    safe_config = {k: v for k, v in config.items() if k not in {"discord_token", "discord_guild_id"}}
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(safe_config, f, indent=2)
```

## `utils/source_store.py`

**What it does:** Simple JSON-backed source management.

```python
"""Simple JSON-backed source management."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

ROOT_DIR = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT_DIR / "sources.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_sources() -> List[Dict[str, Any]]:
    if not SOURCES_PATH.exists():
        save_sources([])
    with SOURCES_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "sources" in data:
        return data["sources"]
    if isinstance(data, list):
        return data
    return []


def save_sources(sources: List[Dict[str, Any]]) -> None:
    with SOURCES_PATH.open("w", encoding="utf-8") as f:
        json.dump(sources, f, indent=2)


def add_source(url: str, source_type: str = "github_readme") -> Dict[str, Any]:
    sources = load_sources()
    normalized_url = url.strip()

    for source in sources:
        if source["url"].strip().lower() == normalized_url.lower():
            source["enabled"] = True
            save_sources(sources)
            return source

    new_source = {
        "id": str(uuid4())[:8],
        "url": normalized_url,
        "type": source_type,
        "enabled": True,
        "date_added": _now_iso(),
    }
    sources.append(new_source)
    save_sources(sources)
    return new_source


def remove_source(url_or_id: str) -> bool:
    sources = load_sources()
    target = url_or_id.strip().lower()
    kept = [
        s
        for s in sources
        if s.get("id", "").lower() != target and s.get("url", "").strip().lower() != target
    ]
    if len(kept) == len(sources):
        return False
    save_sources(kept)
    return True


def set_source_enabled(source_id: str, enabled: bool) -> Optional[Dict[str, Any]]:
    sources = load_sources()
    for source in sources:
        if source.get("id") == source_id:
            source["enabled"] = enabled
            save_sources(sources)
            return source
    return None


def update_source_fetch_cache(source_id: str, resolved_raw_url: str, etag: str) -> None:
    """Remember the raw URL and ETag that resolved successfully for a source.

    Lets the next scan skip straight to the right branch instead of guessing
    HEAD/main/master/dev again, and skip re-downloading unchanged READMEs.
    """
    if not source_id:
        return
    sources = load_sources()
    for source in sources:
        if source.get("id") == source_id:
            source["resolved_raw_url"] = resolved_raw_url
            source["etag"] = etag
            save_sources(sources)
            return


def get_enabled_sources() -> List[Dict[str, Any]]:
    return [s for s in load_sources() if s.get("enabled", True)]
```

## `utils/formatting.py`

**What it does:** Discord formatting helpers.

```python
"""Discord formatting helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List

import discord

from utils.tags import is_faang_company

TAG_EMOJI = {
    "ai": "🤖",
    "backend": "🧱",
    "cloud": "☁️",
    "data": "📊",
    "faang": "⭐",
    "frontend": "🎨",
    "gpu": "⚡",
    "hardware": "🔧",
    "internship": "🎓",
    "jobright": "🧭",
    "linkedin": "💼",
    "manual": "✍️",
    "non-faang": "🌱",
    "quant": "📈",
    "security": "🔒",
    "software": "💻",
}

TAG_LABELS = {
    "ai": "AI",
    "faang": "FAANG",
    "gpu": "GPU",
    "non-faang": "Non-FAANG",
}


def format_tags(tags: Iterable[str]) -> str:
    formatted = []
    for tag in tags:
        normalized = (tag or "").strip().lower()
        if not normalized:
            continue
        label = TAG_LABELS.get(normalized, normalized.replace("-", " ").title())
        emoji = TAG_EMOJI.get(normalized, "🏷️")
        formatted.append(f"{emoji} {label}")
    return ", ".join(dict.fromkeys(formatted)) or "🏷️ Unknown"


def format_uploaded_at(internship: Dict) -> str:
    uploaded_at = (
        internship.get("uploaded_at")
        or internship.get("first_seen")
        or internship.get("date_found")
        or ""
    )
    uploaded_at = str(uploaded_at).strip()
    if not uploaded_at:
        return "Unknown"

    try:
        parsed = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
    except ValueError:
        return uploaded_at

    timestamp = int(parsed.timestamp())
    return f"<t:{timestamp}:R> (<t:{timestamp}:f>)"


def format_quality_score(quality_score: object) -> str:
    if not isinstance(quality_score, int):
        return ""
    quality_score = max(1, min(5, quality_score))
    return f"{'⭐' * quality_score}{'☆' * (5 - quality_score)} ({quality_score}/5)"


def source_display_name(source_url: str, source_type: str) -> str:
    if "SimplifyJobs" in source_url:
        return "GitHub - SimplifyJobs"
    if "zapplyjobs" in source_url:
        return "GitHub - zapplyjobs"
    if source_type == "linkedin_manual":
        return "LinkedIn - Manual Link"
    if source_type == "jobright_manual":
        return "Jobright - Manual Link"
    if "github.com" in source_url:
        parts = source_url.split("github.com/")[-1].split("?")[0].split("#")[0]
        return f"GitHub - {parts.strip('/')}"
    return source_type


def internship_to_embed(internship: Dict) -> discord.Embed:
    company = internship.get("company") or "Unknown Company"
    title = internship.get("title") or "Internship"
    location = internship.get("location") or "Unknown"
    application_url = internship.get("application_url") or internship.get("source_url") or ""
    source_url = internship.get("source_url") or ""
    source_type = internship.get("source_type") or "unknown"
    tag_names = [str(tag).lower() for tag in internship.get("tags", [])]
    is_faang = "faang" in tag_names or is_faang_company(company)

    embed = discord.Embed(
        title=f"{company} - {title}",
        url=application_url if application_url.startswith("http") else None,
        description="New internship found.",
        color=discord.Color.gold() if is_faang else discord.Color.teal(),
    )
    embed.add_field(name="Company", value=company, inline=True)
    embed.add_field(name="Location", value=location, inline=True)
    embed.add_field(name="Uploaded", value=format_uploaded_at(internship), inline=True)
    embed.add_field(name="Role", value=title, inline=False)

    # Only present when llm_filter_enabled scored this posting.
    quality_display = format_quality_score(internship.get("quality_score"))
    if quality_display:
        embed.add_field(name="Match", value=quality_display, inline=True)
        llm_reason = str(internship.get("llm_reason") or "").strip()
        if llm_reason:
            embed.set_footer(text=llm_reason)

    if application_url.startswith("http"):
        embed.add_field(name="Apply", value=f"[Open application]({application_url})", inline=False)
    else:
        embed.add_field(name="Apply", value="No direct application link found", inline=False)

    if source_url.startswith("http"):
        embed.add_field(
            name="Source",
            value=f"[{source_display_name(source_url, source_type)}]({source_url})",
            inline=False,
        )
    else:
        embed.add_field(name="Source", value=source_display_name(source_url, source_type), inline=False)

    embed.add_field(name="Tags", value=format_tags(internship.get("tags", [])), inline=False)
    return embed


def personal_match_to_embed(internship: Dict, match_score: int, reason: str) -> discord.Embed:
    """Personalized variant of internship_to_embed for premium DM digests.

    Reuses internship_to_embed for the shared fields, then foregrounds *why
    this matches you specifically* — distinct from the server-wide "Match"
    quality field (if llm_filter_enabled also scored this posting).
    """
    embed = internship_to_embed(internship)
    embed.color = discord.Color.purple()
    embed.description = f"**Why this matches you:** {reason}" if reason else "Personalized match."
    embed.add_field(name="Your Match", value=format_quality_score(match_score), inline=True)
    return embed


def chunk_list(items: List[Dict], size: int) -> List[List[Dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
```

## `utils/filters.py`

**What it does:** Keyword filtering helpers.

```python
"""Keyword filtering helpers."""

from __future__ import annotations

from typing import Dict, Iterable, List


def _contains_any(text: str, keywords: Iterable[str]) -> bool:
    lower = text.lower()
    return any(keyword.strip().lower() in lower for keyword in keywords if keyword.strip())


def internship_to_search_text(internship: Dict) -> str:
    parts: List[str] = [
        internship.get("company", ""),
        internship.get("title", ""),
        internship.get("location", ""),
        internship.get("application_url", ""),
        internship.get("source_url", ""),
        " ".join(internship.get("tags", [])),
    ]
    return " ".join(parts)


def passes_filters(internship: Dict, include_keywords: List[str], exclude_keywords: List[str]) -> bool:
    """Return True if the internship should be stored/posted."""
    text = internship_to_search_text(internship)

    if exclude_keywords and _contains_any(text, exclude_keywords):
        return False

    # Empty include list means include everything.
    if include_keywords and not _contains_any(text, include_keywords):
        return False

    return True
```

## `utils/tags.py`

**What it does:** Shared internship tag helpers.

```python
"""Shared internship tag helpers."""

from __future__ import annotations

import re
from typing import Iterable, List

FAANG_ALIASES = {
    "meta": ["meta", "facebook", "instagram", "whatsapp", "oculus"],
    "apple": ["apple"],
    "amazon": ["amazon", "aws", "amazon web services", "audible"],
    "netflix": ["netflix"],
    "google": ["google", "alphabet", "youtube", "google cloud", "deepmind"],
}


def is_faang_company(company: str) -> bool:
    """Return True when the company name matches a FAANG company or common alias."""
    company_lower = (company or "").lower()
    if not company_lower.strip():
        return False

    for aliases in FAANG_ALIASES.values():
        for alias in aliases:
            pattern = rf"(?<![a-z0-9]){re.escape(alias.lower())}(?![a-z0-9])"
            if re.search(pattern, company_lower):
                return True
    return False


def add_company_classification_tag(tags: Iterable[str] | str, company: str) -> List[str]:
    """Add exactly one FAANG classification tag while preserving existing tag order."""
    if isinstance(tags, str):
        tags = tags.split(",")

    clean_tags = []
    for tag in tags:
        clean = (tag or "").strip().lower()
        if clean and clean not in {"faang", "non-faang"}:
            clean_tags.append(clean)

    clean_tags.append("faang" if is_faang_company(company) else "non-faang")
    return list(dict.fromkeys(clean_tags))
```

## `utils/relevance.py`

**What it does:** Local-LLM relevance + quality scoring for scraped internship postings.

```python
"""Local-LLM relevance + quality scoring for scraped internship postings.

Keyword include/exclude filtering (utils/filters.py) is cheap but coarse — it
lets through parser garbage and postings that only accidentally contain a
keyword, and it can't tell a strong listing from a weak one. This adds an
optional second pass using a small local Ollama model to:

  1. Catch clearly irrelevant postings the keyword filter missed.
  2. Score the rest 1-5 so the best postings can be prioritized when there
     are more new postings in a scan than max_posts_per_scan can post.

Disabled by default (llm_filter_enabled=false) since it requires a running
Ollama server with a model pulled. Fails open: if Ollama is unreachable or
returns something unusable, the posting is treated as relevant with a neutral
score rather than being silently dropped or blocking the scan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from utils.ollama_client import OllamaError, generate_json

LOGGER = logging.getLogger(__name__)

DEFAULT_OLLAMA_HOST = "http://192.168.1.84:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
NEUTRAL_QUALITY_SCORE = 3
MIN_QUALITY_SCORE = 1
MAX_QUALITY_SCORE = 5

_PROMPT_TEMPLATE = """You help a university Discord server's internship-alert bot decide \
which postings are worth showing students.

Posting:
  Company: {company}
  Role: {title}
  Location: {location}
  Existing tags: {tags}

Judge this ONE posting for a CS/software/data/AI-leaning student audience. Reply with ONLY \
a JSON object, no other text, matching this shape exactly:
{{"relevant": true or false, "quality_score": 1-5 integer, "reason": "one short sentence"}}

Guidance:
- relevant=false only for things that are clearly not a real, in-scope internship/co-op \
listing (e.g. parsing garbage, a full-time/senior role, something unrelated to tech).
- quality_score reflects how strong a match this is: 5 = well-known company, clear \
in-field role; 3 = plausible but generic or unclear; 1 = borderline/likely low value.
"""


@dataclass
class RelevanceResult:
    relevant: bool
    quality_score: int
    reason: str
    source: str  # "llm" or "fallback"


def _fallback(reason: str) -> RelevanceResult:
    return RelevanceResult(
        relevant=True, quality_score=NEUTRAL_QUALITY_SCORE, reason=reason, source="fallback"
    )


def classify_relevance(internship: Dict[str, Any], config: Dict[str, Any]) -> RelevanceResult:
    """Judge one internship posting. Always returns a usable result (fail-open)."""
    host = config.get("ollama_host") or DEFAULT_OLLAMA_HOST
    model = config.get("ollama_model") or DEFAULT_OLLAMA_MODEL
    timeout = float(config.get("llm_timeout_seconds", 15))

    prompt = _PROMPT_TEMPLATE.format(
        company=internship.get("company", "Unknown"),
        title=internship.get("title", "Unknown"),
        location=internship.get("location", "Unknown"),
        tags=", ".join(internship.get("tags", [])) or "none",
    )

    try:
        parsed = generate_json(host=host, model=model, prompt=prompt, timeout=timeout)
    except OllamaError as exc:
        LOGGER.warning("Relevance check failed, keeping posting by default: %s", exc)
        return _fallback("Ollama unavailable; kept by default")

    try:
        relevant = bool(parsed["relevant"])
        quality_score = int(parsed["quality_score"])
        reason = str(parsed.get("reason", "")).strip() or "No reason given"
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("Relevance check returned an unexpected shape %r: %s", parsed, exc)
        return _fallback("Unexpected model output; kept by default")

    quality_score = max(MIN_QUALITY_SCORE, min(MAX_QUALITY_SCORE, quality_score))
    return RelevanceResult(relevant=relevant, quality_score=quality_score, reason=reason, source="llm")
```

## `utils/personalization.py`

**What it does:** Per-member personalized match scoring for the premium tier.

```python
"""Per-member personalized match scoring for the premium tier.

utils/relevance.py scores a posting once for the whole server. This scores it
again per premium member against their own short interest blurb (set via
/set_profile), so the personalized DM digest can surface only what's actually
relevant to *that* person instead of just the server-wide ranking.

Same fail-open contract as utils/relevance.py: if Ollama is unreachable or
returns something unusable, callers get a neutral score back rather than an
exception — a flaky LLM call should never crash a scan or block the digest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict

from utils.ollama_client import OllamaError, generate_json
from utils.relevance import DEFAULT_OLLAMA_HOST, DEFAULT_OLLAMA_MODEL, NEUTRAL_QUALITY_SCORE

LOGGER = logging.getLogger(__name__)

MIN_MATCH_SCORE = 1
MAX_MATCH_SCORE = 5

_PROMPT_TEMPLATE = """A student described what internships they're interested in:
"{profile_blurb}"

Judge how well this ONE posting matches THEIR stated interests specifically \
(not just whether it's a reasonable internship in general):
  Company: {company}
  Role: {title}
  Location: {location}
  Tags: {tags}

Reply with ONLY a JSON object, no other text, matching this shape exactly:
{{"match_score": 1-5 integer, "reason": "one short sentence addressed to the student, e.g. 'Matches your interest in backend/Go roles'"}}

Guidance: 5 = strongly matches their stated interests; 3 = plausible but not a clear \
match to what they described; 1 = doesn't match their stated interests at all.
"""


@dataclass
class PersonalMatchResult:
    match_score: int
    reason: str
    source: str  # "llm" or "fallback"


def _fallback(reason: str) -> PersonalMatchResult:
    return PersonalMatchResult(match_score=NEUTRAL_QUALITY_SCORE, reason=reason, source="fallback")


def score_personal_match(
    internship: Dict[str, Any], profile_blurb: str, config: Dict[str, Any]
) -> PersonalMatchResult:
    """Judge one posting against one member's profile blurb. Always returns a
    usable result (fail-open)."""
    host = config.get("ollama_host") or DEFAULT_OLLAMA_HOST
    model = config.get("ollama_model") or DEFAULT_OLLAMA_MODEL
    timeout = float(config.get("llm_timeout_seconds", 15))

    prompt = _PROMPT_TEMPLATE.format(
        profile_blurb=profile_blurb,
        company=internship.get("company", "Unknown"),
        title=internship.get("title", "Unknown"),
        location=internship.get("location", "Unknown"),
        tags=", ".join(internship.get("tags", [])) or "none",
    )

    try:
        parsed = generate_json(host=host, model=model, prompt=prompt, timeout=timeout)
    except OllamaError as exc:
        LOGGER.warning("Personal match check failed, using neutral score: %s", exc)
        return _fallback("Ollama unavailable; neutral score used")

    try:
        match_score = int(parsed["match_score"])
        reason = str(parsed.get("reason", "")).strip() or "No reason given"
    except (KeyError, TypeError, ValueError) as exc:
        LOGGER.warning("Personal match check returned an unexpected shape %r: %s", parsed, exc)
        return _fallback("Unexpected model output; neutral score used")

    match_score = max(MIN_MATCH_SCORE, min(MAX_MATCH_SCORE, match_score))
    return PersonalMatchResult(match_score=match_score, reason=reason, source="llm")
```

## `utils/ollama_client.py`

**What it does:** Thin HTTP client for a local Ollama server.

```python
"""Thin HTTP client for a local Ollama server.

Kept separate from utils/relevance.py so the HTTP/JSON plumbing can be tested
(and swapped) independently of the prompt and result-parsing logic.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict

import requests

LOGGER = logging.getLogger(__name__)


class OllamaError(Exception):
    """Raised when the local Ollama server can't be reached or errors out."""


def generate_json(host: str, model: str, prompt: str, timeout: float = 15.0) -> Dict[str, Any]:
    """Ask Ollama to generate a single JSON object and return it parsed.

    Raises OllamaError on any network/HTTP/JSON failure so callers decide how
    to fail — this module never guesses at a fallback value itself.
    """
    url = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise OllamaError(f"Could not reach Ollama at {url}: {exc}") from exc

    try:
        body = response.json()
    except ValueError as exc:
        raise OllamaError(f"Ollama returned a non-JSON response body: {exc}") from exc

    raw_text = body.get("response", "")
    try:
        return json.loads(raw_text)
    except (TypeError, ValueError) as exc:
        raise OllamaError(f"Ollama's model output wasn't valid JSON: {raw_text!r}") from exc
```

## `utils/email_digest_template.py`

**What it does:** Future email digest template for internship alerts.

```python
"""Future email digest template for internship alerts.

This module only renders email content. It does not send email or read subscriber
records yet. Later, a scheduled worker can call this once per hour with jobs from
the database and send the returned subject/text/html to subscribed users.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Dict, Iterable, List


def render_hourly_email_digest(jobs: Iterable[Dict], generated_at: datetime | None = None) -> Dict[str, str]:
    """Return subject, text, and HTML bodies for an hourly internship digest."""
    generated_at = generated_at or datetime.now(timezone.utc)
    job_list = list(jobs)
    subject = f"{len(job_list)} new internship opportunities"

    if not job_list:
        return {
            "subject": "No new internship opportunities this hour",
            "text": "No new internship opportunities were found in the latest hourly digest.",
            "html": "<p>No new internship opportunities were found in the latest hourly digest.</p>",
        }

    return {
        "subject": subject,
        "text": _render_text(job_list, generated_at),
        "html": _render_html(job_list, generated_at),
    }


def _render_text(jobs: List[Dict], generated_at: datetime) -> str:
    lines = [
        "New internship opportunities",
        f"Generated: {generated_at.isoformat()}",
        "",
    ]

    for index, job in enumerate(jobs, start=1):
        lines.extend(
            [
                f"{index}. {job.get('company', 'Unknown Company')} - {job.get('title', 'Internship')}",
                f"   Location: {job.get('location', 'Unknown')}",
                f"   Uploaded: {job.get('uploaded_at') or job.get('first_seen') or 'Unknown'}",
                f"   Tags: {_format_plain_tags(job.get('tags', []))}",
                f"   Apply: {job.get('application_url') or job.get('source_url') or 'No link available'}",
                "",
            ]
        )

    return "\n".join(lines).strip()


def _render_html(jobs: List[Dict], generated_at: datetime) -> str:
    items = []
    for job in jobs:
        company = escape(str(job.get("company", "Unknown Company")))
        title = escape(str(job.get("title", "Internship")))
        location = escape(str(job.get("location", "Unknown")))
        uploaded = escape(str(job.get("uploaded_at") or job.get("first_seen") or "Unknown"))
        tags = escape(_format_plain_tags(job.get("tags", [])))
        url = escape(str(job.get("application_url") or job.get("source_url") or ""))
        apply_link = f'<a href="{url}">Apply</a>' if url.startswith("http") else "No link available"

        items.append(
            f"""
            <li style="margin: 0 0 18px;">
              <strong>{company} - {title}</strong><br>
              Location: {location}<br>
              Uploaded: {uploaded}<br>
              Tags: {tags}<br>
              {apply_link}
            </li>
            """
        )

    return f"""
    <section style="font-family: Arial, sans-serif; color: #1f2937;">
      <h1 style="font-size: 22px;">New internship opportunities</h1>
      <p style="color: #6b7280;">Generated: {escape(generated_at.isoformat())}</p>
      <ul style="padding-left: 20px;">
        {''.join(items)}
      </ul>
    </section>
    """.strip()


def _format_plain_tags(tags: object) -> str:
    if isinstance(tags, str):
        return tags or "Unknown"
    if isinstance(tags, list):
        return ", ".join(str(tag) for tag in tags if str(tag).strip()) or "Unknown"
    return "Unknown"
```

## `config.example.json`

**What it does:** See file contents below.

```json
{
  "discord_channel_id": "",
  "scan_interval_minutes": 240,
  "auto_scan_enabled": true,
  "auto_scan_on_start": true,
  "max_posts_per_scan": 20,
  "include_keywords": [
    "software",
    "swe",
    "intern",
    "internship",
    "co-op",
    "data",
    "ai",
    "machine learning",
    "backend",
    "frontend",
    "cloud",
    "quant",
    "gpu",
    "cuda",
    "hardware"
  ],
  "exclude_keywords": [
    "senior",
    "staff",
    "principal",
    "full-time",
    "new grad"
  ],
  "llm_filter_enabled": false,
  "ollama_host": "http://192.168.1.84:11434",
  "ollama_model": "llama3.2:3b",
  "llm_timeout_seconds": 15,
  "llm_min_quality_score": 1,
  "premium_role_id": "",
  "personal_digest_top_n": 5,
  "personal_digest_min_score": 4,
  "uptime_kuma_push_url": "",
  "heartbeat_interval_minutes": 5,
  "data_retention_days": 180,
  "storage_maintenance_interval_hours": 24
}
```

## `sources.example.json`

**What it does:** See file contents below.

```json
[]
```

## `requirements.txt`

**What it does:** See file contents below.

```text
discord.py>=2.4.0
python-dotenv>=1.0.1
requests>=2.32.0
Flask>=3.0.0
```

## `requirements-dev.txt`

**What it does:** See file contents below.

```text
-r requirements.txt
pytest>=8.0.0
```

## `.env.example`

**What it does:** See file contents below.

```env
# Required: paste your Discord bot token here.
DISCORD_TOKEN=PASTE_YOUR_BOT_TOKEN_HERE

# Optional but strongly recommended while testing.
# This makes slash commands appear almost instantly in one server.
# To get it: right-click your server icon in Discord with Developer Mode on > Copy Server ID.
DISCORD_GUILD_ID=

# Optional. You can leave this blank and run /set_channel in Discord.
DISCORD_CHANNEL_ID=

# Optional overrides. If blank, config.json values are used.
SCAN_INTERVAL_MINUTES=
AUTO_SCAN_ENABLED=
AUTO_SCAN_ON_START=
MAX_POSTS_PER_SCAN=

# Optional dashboard settings (see `python dashboard/app.py`).
# Leave DASHBOARD_HOST unset (defaults to 127.0.0.1) unless you need the
# dashboard reachable from other machines. If you do widen it, set both
# DASHBOARD_USERNAME and DASHBOARD_PASSWORD first — the dashboard refuses to
# start on a non-loopback host without them.
DASHBOARD_HOST=
DASHBOARD_DEBUG=
DASHBOARD_USERNAME=
DASHBOARD_PASSWORD=

# Optional: local-LLM relevance/quality scoring (see config.json's
# llm_filter_enabled). Requires a running Ollama server with the model
# already pulled (docker exec ollama ollama pull llama3.2:3b). Off unless
# llm_filter_enabled is true in config.json.
OLLAMA_HOST=
OLLAMA_MODEL=
LLM_FILTER_ENABLED=

# Optional: Uptime Kuma Push-monitor heartbeat (see config.json's
# uptime_kuma_push_url / heartbeat_interval_minutes). The full push URL
# includes an opaque per-monitor token, so it's treated like a secret here
# rather than in config.json. Leave blank to disable.
UPTIME_KUMA_PUSH_URL=
```

## `.gitignore`

**What it does:** See file contents below.

```gitignore
.env
internships.db
config.json
sources.json
__pycache__/
*.pyc
.venv/
venv/
instance/
```

## `docker-compose.yml`

**What it does:** See file contents below.

```yaml
services:
  internship-bot:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: internship-bot
    restart: unless-stopped
    environment:
      DISCORD_TOKEN: ${DISCORD_TOKEN}
      DISCORD_GUILD_ID: ${DISCORD_GUILD_ID:-}
      DISCORD_CHANNEL_ID: ${DISCORD_CHANNEL_ID:-}
      SCAN_INTERVAL_MINUTES: ${SCAN_INTERVAL_MINUTES:-240}
      AUTO_SCAN_ENABLED: ${AUTO_SCAN_ENABLED:-true}
      AUTO_SCAN_ON_START: ${AUTO_SCAN_ON_START:-true}
      MAX_POSTS_PER_SCAN: ${MAX_POSTS_PER_SCAN:-20}
      PYTHONUNBUFFERED: 1
    volumes:
      # Bind mount for development hot reload
      - ./bot.py:/app/bot.py:ro
      - ./scanner.py:/app/scanner.py:ro
      - ./config.json:/app/config.json
      - ./database:/app/database:ro
      - ./scraper:/app/scraper:ro
      - ./utils:/app/utils:ro
      # Persist database and config across restarts
      - internship_data:/app
    env_file:
      - .env
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  internship_data:
```

## `Dockerfile`

**What it does:** See file contents below.

```dockerfile
# Build stage
FROM python:3.12-slim AS builder

WORKDIR /tmp

# Copy only requirements to leverage layer caching
COPY requirements.txt .

# Install dependencies to a virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Runtime stage
FROM python:3.12-slim

WORKDIR /app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Health check (bot is responsive if it can import)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import bot; print('OK')" || exit 1

CMD ["python", "bot.py"]
```

## `.dockerignore`

**What it does:** See file contents below.

```gitignore
.env
.git
.gitignore
.venv
venv
__pycache__
*.pyc
*.pyo
*.pyd
.pytest_cache
.coverage
htmlcov
dist
build
*.egg-info
.DS_Store
internships.db
node_modules
.idea
.vscode
*.log
```

## `README.md`

**What it does:** See file contents below.

```markdown
# Discord Internship Bot

A beginner-friendly Discord bot that runs locally, scans public GitHub internship README sources, stores opportunities in SQLite, and posts new internship alerts into a Discord channel.

LinkedIn and Jobright are supported through safe manual link ingestion instead of direct scraping.

## What this bot does

- Automatically scans enabled GitHub README sources every 4 hours while the bot is running.
- Runs one scan on startup by default.
- Posts only new internships that have not already been posted to Discord.
- Stores jobs in local SQLite: `internships.db`.
- Avoids duplicate Discord posts using company + role + application link.
- Includes upload/source age info in Discord posts when the source provides it.
- Adds a FAANG or Non-FAANG tag to each opportunity.
- Adds emoji-powered tags to make Discord posts easier to scan.
- Lets you manage GitHub sources with slash commands.
- Includes an optional local dashboard at `http://localhost:5000`.
- Includes a future hourly email digest template in `utils/email_digest_template.py`.

## Important LinkedIn and Jobright note

This project does **not** directly scrape LinkedIn or Jobright.

LinkedIn commonly blocks bots and many job boards restrict automated extraction. Because of that, this bot uses safer alternatives:

- Paste a job URL manually with `/add_manual_job`.
- Use saved job links you personally found.
- Later, import a CSV export if you build that upgrade.
- Use email/RSS alerts only when the source officially supports it.

## Project structure

```text
discord-internship-bot/
|-- bot.py
|-- scanner.py
|-- scraper/
|   |-- __init__.py
|   |-- github_scraper.py
|   |-- linkedin_manual.py
|   `-- jobright_manual.py
|-- database/
|   |-- __init__.py
|   `-- db.py
|-- dashboard/
|   |-- app.py
|   `-- templates/
|       |-- index.html
|       `-- internships.html
|-- utils/
|   |-- config_loader.py
|   |-- email_digest_template.py
|   |-- filters.py
|   |-- formatting.py
|   |-- source_store.py
|   `-- tags.py
|-- config.json
|-- sources.json
|-- requirements.txt
|-- README.md
|-- .env.example
`-- .gitignore
```

## Setup guide for Windows

### 1. Install Python

1. Go to the official Python website.
2. Download Python 3.11 or newer.
3. During installation, check **Add python.exe to PATH**.
4. Open PowerShell and test:

```powershell
python --version
```

You should see something like `Python 3.11.x` or newer.

### 2. Open the project folder

Put this folder somewhere simple, like:

```text
C:\Users\YourName\Desktop\discord-internship-bot
```

Then open PowerShell:

```powershell
cd C:\Users\YourName\Desktop\discord-internship-bot
```

### 3. Create a virtual environment

```powershell
python -m venv .venv
```

Activate it:

```powershell
.\.venv\Scripts\activate
```

You should see `(.venv)` at the beginning of your PowerShell line.

### 4. Install dependencies

```powershell
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5. Create your `.env` file

Copy `.env.example` to `.env`:

```powershell
copy .env.example .env
```

Open `.env` in VS Code or Notepad and paste your Discord bot token:

```env
DISCORD_TOKEN=your_real_token_here
```

Do not share this token. Do not commit `.env` to GitHub.

### 6. Create your `config.json` and `sources.json`

These hold your live channel ID and saved sources, and are gitignored so your personal
settings never get committed. Copy the templates:

```powershell
copy config.example.json config.json
copy sources.example.json sources.json
```

You do not need to edit them by hand — `/set_channel` and `/add_source` (or the
dashboard) fill these in for you.

## Docker setup guide

Use Docker if you want the bot to keep running in a container instead of directly in your Windows PowerShell session.

### 1. Install Docker Desktop

1. Install Docker Desktop for Windows.
2. Start Docker Desktop.
3. Open PowerShell in this project folder.
4. Confirm Docker works:

```powershell
docker --version
```

### 2. Create your `.env` file

If you have not already created it, copy the example file:

```powershell
copy .env.example .env
```

Edit `.env` and set at least:

```env
DISCORD_TOKEN=your_real_token_here
```

Optional but recommended while testing:

```env
DISCORD_GUILD_ID=your_server_id_here
```

You can also set the posting channel in `.env` if you already know it:

```env
DISCORD_CHANNEL_ID=your_channel_id_here
```

Otherwise, run `/set_channel` in Discord after the bot starts.

### 3. Create your `config.json` and `sources.json`

```powershell
copy config.example.json config.json
copy sources.example.json sources.json
```

Both are gitignored and mounted into the container so they persist across rebuilds.

### 5. Check the `Dockerfile`

The project includes a multi-stage `Dockerfile` (dependencies build in one stage, the
runtime image only copies the installed virtual environment and the app code, keeping
the final image smaller) that ends with:

```dockerfile
CMD ["python", "bot.py"]
```

### 6. Check `.dockerignore`

The project includes a `.dockerignore` that keeps secrets, your local virtual
environment, caches, and your local database out of the Docker build context.

### 7. Build the Docker image

```powershell
docker build -t discord-internship-bot .
```

### 8. Run the bot container

This command starts the bot and mounts the project folder into the container so `config.json`, `sources.json`, and `internships.db` persist on your computer:

```powershell
docker run --name discord-internship-bot --env-file .env -v ${PWD}:/app discord-internship-bot
```

If the bot starts correctly, you should see logs saying it logged into Discord and synced slash commands.

### 9. Stop and restart the container

Stop it:

```powershell
docker stop discord-internship-bot
```

Start it again:

```powershell
docker start -a discord-internship-bot
```

If you changed code or dependencies, rebuild and recreate the container:

```powershell
docker stop discord-internship-bot
docker rm discord-internship-bot
docker build -t discord-internship-bot .
docker run --name discord-internship-bot --env-file .env -v ${PWD}:/app discord-internship-bot
```

### Optional: run the dashboard in Docker

The dashboard binds to `127.0.0.1` and runs with Flask debug mode off by default, and
it refuses to start on a non-loopback host unless `DASHBOARD_USERNAME` and
`DASHBOARD_PASSWORD` are both set. To access it from your browser through Docker, set
those plus `DASHBOARD_HOST=0.0.0.0` when running the container:

```powershell
docker run --name internship-dashboard --env-file .env -e DASHBOARD_HOST=0.0.0.0 -e DASHBOARD_USERNAME=youruser -e DASHBOARD_PASSWORD=yourpassword -p 5000:5000 -v ${PWD}:/app discord-internship-bot python dashboard/app.py
```

Open:

```text
http://localhost:5000
```

You'll be prompted for the username/password (HTTP Basic Auth) once `DASHBOARD_HOST`
is widened. Only do this on a network you trust — never expose it to the public
internet. Never set `DASHBOARD_DEBUG=true` unless you're debugging locally with
`DASHBOARD_HOST` left at `127.0.0.1`: Flask's debug mode exposes an unauthenticated
interactive Python console over HTTP whenever a route raises, which is a remote
code execution risk the moment the dashboard is reachable from anywhere else.

## Discord bot creation guide

### 1. Create a Discord application

1. Go to the Discord Developer Portal.
2. Click **New Application**.
3. Name it something like `Internship Alerts Bot`.
4. Click **Create**.

### 2. Create the bot user

1. Open your application.
2. Go to the **Bot** tab.
3. Click **Add Bot** or **Reset Token** if needed.
4. Copy the bot token.
5. Paste the token into your local `.env` file as `DISCORD_TOKEN`.

### 3. Enable intents

This bot uses slash commands and does not need Message Content Intent — leave that off.

It **does** require the **Server Members Intent** (a privileged intent), because the
premium-tier personalized DM digest needs to resolve which members hold the premium
role. Turn this on in the **Bot** tab, under **Privileged Gateway Intents**, even if
you're not using the premium tier yet — the bot requests it unconditionally, and
Discord will refuse the connection with `PrivilegedIntentsRequired` if it's off.

### 4. Create the invite URL

1. Go to **OAuth2**.
2. Open **URL Generator**.
3. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
4. Under **Bot Permissions**, select:
   - View Channels
   - Send Messages
   - Read Message History
   - Use Slash Commands
   - Embed Links
5. Copy the generated invite URL.
6. Open it in your browser.
7. Invite the bot to your Discord server.

### 5. Create a private channel

1. In Discord, create a private text channel like `#internship-alerts`.
2. Give yourself access.
3. Give the bot access.
4. Make sure the bot can:
   - View Channel
   - Send Messages
   - Read Message History
   - Embed Links
   - Use Application Commands

### 6. Optional: set your guild ID for faster slash commands

Without a guild ID, Discord global slash commands can take a while to appear.

To make commands appear faster while testing:

1. In Discord, go to **User Settings > Advanced**.
2. Turn on **Developer Mode**.
3. Right-click your server icon.
4. Click **Copy Server ID**.
5. Paste it into `.env`:

```env
DISCORD_GUILD_ID=your_server_id_here
```

## Run guide

### Start the bot

Make sure your virtual environment is activated:

```powershell
.\.venv\Scripts\activate
```

Run:

```powershell
python bot.py
```

You should see console logs showing that the bot logged in and synced commands.

### Set the posting channel

In your private Discord channel, run:

```text
/set_channel
```

This saves the current channel ID into `config.json`.

### Automatic scans

By default, `config.json` is set to:

```json
{
  "scan_interval_minutes": 240,
  "auto_scan_enabled": true,
  "auto_scan_on_start": true
}
```

That means:

- The bot runs one scan when it starts.
- The bot scans again every 4 hours.
- Only new jobs are posted to Discord.
- Jobs already posted stay in SQLite and are not posted again.

The bot still only runs while your laptop is on and `python bot.py` is running.

### Manual scan

You can still run a scan any time:

```text
/scan
```

The bot scans enabled GitHub sources, stores jobs in SQLite, and posts new jobs into the configured channel.

### Stop the bot

In PowerShell, press:

```text
Ctrl + C
```

## Discord post format

Each new internship embed includes:

- Company
- Role
- Location
- Uploaded time or source age, when available
- Apply link
- Source link
- Emoji tags
- FAANG or Non-FAANG classification

Example tags:

```text
💻 Software, 🎓 Internship, ⭐ FAANG
```

```text
📊 Data, 🌱 Non-FAANG
```

FAANG detection currently covers common aliases for Meta/Facebook, Apple, Amazon/AWS, Netflix, and Google/Alphabet.

## Discord commands

- `/scan` - manually scan all enabled GitHub sources.
- `/add_source <url>` - add a new GitHub internship repository or README URL.
- `/list_sources` - show all saved sources.
- `/remove_source <url_or_id>` - remove a source by ID or exact URL.
- `/set_channel` - set the current channel as the posting channel.
- `/status` - show bot status, schedule, last scan time, and job counts.
- `/add_manual_job <source> <url> [company] [title] [location]` - manually save a LinkedIn or Jobright link.
- `/set_premium_role <role>` - admin only: set which role gets personalized DM digests.
- `/set_profile <blurb>` - premium members: set your interests for personalized matching.
- `/my_profile` - show your saved profile and premium status.
- `/help` - show available commands.

## Add more GitHub internship links

Option 1: Discord command:

```text
/add_source https://github.com/example/example-internships
```

Option 2: Edit `sources.json` manually:

```json
{
  "id": "myrepo01",
  "url": "https://github.com/example/example-internships",
  "type": "github_readme",
  "enabled": true,
  "date_added": "2026-07-02T00:00:00+00:00"
}
```

Restart the bot or run `/scan` after editing.

## Config file

Edit `config.json`:

```json
{
  "discord_channel_id": "",
  "scan_interval_minutes": 240,
  "auto_scan_enabled": true,
  "auto_scan_on_start": true,
  "max_posts_per_scan": 20,
  "include_keywords": ["software", "swe", "intern", "data", "ai", "quant", "gpu", "cuda"],
  "exclude_keywords": ["senior", "staff", "principal", "full-time", "new grad"]
}
```

Optional `.env` overrides:

```env
SCAN_INTERVAL_MINUTES=240
AUTO_SCAN_ENABLED=true
AUTO_SCAN_ON_START=true
MAX_POSTS_PER_SCAN=20
```

## Optional local-LLM relevance and quality scoring

The keyword include/exclude filter above is cheap but coarse: it can't tell a strong
posting from a weak one, and it lets through the occasional parser mistake as long as
a keyword happens to match. If you have a local Ollama server, the bot can run a
second pass that judges each new posting for relevance and gives it a 1-5 quality
score, so when a scan finds more postings than `max_posts_per_scan` can send at once,
the strongest matches win the scarce slots instead of whichever happened to appear
first in a README.

Enable it in `config.json`:

```json
{
  "llm_filter_enabled": true,
  "ollama_host": "http://192.168.1.84:11434",
  "ollama_model": "llama3.2:3b",
  "llm_timeout_seconds": 15,
  "llm_min_quality_score": 1
}
```

Or via `.env`: `LLM_FILTER_ENABLED=true`, `OLLAMA_HOST=...`, `OLLAMA_MODEL=...`.

Make sure the model is pulled first:

```bash
docker exec ollama ollama pull llama3.2:3b
```

Notes:

- **Off by default.** Nothing changes until `llm_filter_enabled` is `true`.
- **Fails open.** If Ollama is unreachable, slow, or returns something unusable, the
  posting is kept with a neutral score rather than dropped — a flaky LLM call should
  never cause a real internship to go unposted.
- **Only runs on new postings**, after the keyword filter, so it's not spending a
  model call on every row of every README on every scan.
- **`llm_min_quality_score`** (1-5) additionally drops postings scored below the
  threshold, on top of the model's own relevant/not-relevant judgement. Leave at `1`
  to only filter, not additionally threshold.
- Scored postings show a star rating ("Match") and the model's one-line reason (as
  the embed footer) in Discord; unscored postings (feature off, or the fallback path)
  show neither.

## Optional premium tier: personalized DM digests

Everything above ranks/filters postings the same way for the whole server. The
premium tier adds a second, *personalized* layer on top for members your organization
has marked as paid/premium — it doesn't change or gate anything the rest of the
server sees.

There's no billing integration here at all. "Premium" is just a Discord role your
officers assign the same way you'd assign any other role (e.g. when dues are paid) —
the bot only checks whether a member holds that role.

**Setup:**

1. Create a role in your server for premium members (any name).
2. Make sure **Server Members Intent** is enabled (see the intents step above) —
   required for the bot to know who holds the role.
3. Run `/set_premium_role @YourPremiumRole` (admin/Manage Server permission required).
4. Premium members run `/set_profile` with 1-3 sentences describing what they're
   looking for, e.g. `"Backend/Go internships, remote OK, sophomore, open to startups"`.
   `/my_profile` shows what's currently saved.

That's it — after every scan, each premium member with a saved profile gets DMed
their top matches from that scan (ranked and explained against *their* blurb, not
just the server-wide ranking), in addition to the shared channel post everyone gets.

Config (`config.json`):

```json
{
  "premium_role_id": "",
  "personal_digest_top_n": 5,
  "personal_digest_min_score": 4
}
```

- **`personal_digest_top_n`** — max postings DMed per member per scan.
- **`personal_digest_min_score`** (1-5) — only DM postings scored at or above this
  personal-fit score; keeps digests from including a "meh" match just to fill five
  slots.
- Uses the same `ollama_host`/`ollama_model`/`llm_timeout_seconds` settings as the
  server-wide LLM filter above, and the same fail-open behavior — if Ollama is
  unreachable, that member's digest is silently skipped for this scan rather than
  DMing them something broken or blocking the shared-channel post.
- If a premium member has DMs closed to the bot, they're skipped (logged, not
  retried) — it doesn't affect anyone else's digest or the shared channel post.

## Uptime monitoring (Uptime Kuma)

The bot has no HTTP server, so it can't be health-checked the usual way (something
pinging a `/health` endpoint). Instead it pushes a heartbeat *out* to an Uptime Kuma
[Push monitor](https://github.com/louislam/uptime-kuma) on a timer — if the process
dies, hangs, or loses its connection, the pings stop arriving and Kuma flags it down
using whatever alerting you've already got configured there.

**Setup, in Uptime Kuma:**

1. Add New Monitor → Monitor Type: **Push**.
2. Set the **Heartbeat Interval** to something a bit longer than
   `heartbeat_interval_minutes` below (e.g. 2x it), so a single missed tick doesn't
   immediately page you.
3. Save, then copy the Push URL it gives you
   (`http://<kuma-host>:3001/api/push/<token>?status=up&msg=OK&ping=`).

**Setup, in this bot** (`.env`, since the URL contains an auth token):

```env
UPTIME_KUMA_PUSH_URL=http://192.168.1.84:3001/api/push/your-token-here
```

Optional (`config.json`): `heartbeat_interval_minutes` (default `5`).

Notes:

- **Off by default** — nothing pings anywhere until `UPTIME_KUMA_PUSH_URL` is set.
- This is a liveness check only (bot process alive and connected to Discord), not a
  "is everything working perfectly" check — Ollama being down, for instance, doesn't
  stop the heartbeat, since the LLM features already fail open gracefully instead of
  crashing. If you want that level of detail, `/status` shows it directly.
- A failed push (Kuma unreachable) is logged and dropped, not retried — the next
  scheduled tick tries again on its own.

## Storage maintenance

Nothing in the bot ever prunes on its own by default in most bots — this one does,
so `internships.db` doesn't grow forever on a server that's meant to run for months.
A daily background task (`storage_maintenance_interval_hours`, default `24`):

1. Deletes postings older than `data_retention_days` (default `180`) whose status is
   still `closed`, `unknown`, or `ignored`. Postings marked `active`, `applied`, or
   `saved` (via the dashboard) are **never** auto-deleted, regardless of age — those
   carry personal value that outweighs the storage cost.
2. Runs `PRAGMA wal_checkpoint(TRUNCATE)` and `VACUUM` to flush the WAL file and
   reclaim disk space the deletes freed up.

This always runs (no config flag to enable it — it has no external dependency and is
cheap even when there's nothing to prune). Set `data_retention_days` to `0` or
negative to disable the pruning step specifically while still keeping the
checkpoint/VACUUM housekeeping. Check current database size and retention settings
any time with `/status`.

## Optional dashboard

The dashboard is local only. It is not password protected, so do not expose it to the public internet.

Run it in a second PowerShell window:

```powershell
cd C:\Users\YourName\Desktop\discord-internship-bot
.\.venv\Scripts\activate
python dashboard/app.py
```

Open:

```text
http://localhost:5000
```

Dashboard features:

- View saved sources.
- Add a GitHub source URL.
- Enable/disable sources.
- View found internships.
- See uploaded/source age and first-seen time.
- Mark internships as `saved`, `applied`, `ignored`, `closed`, etc.

## Future hourly email digest

The project includes a template for the next upgrade in:

```text
utils/email_digest_template.py
```

`render_hourly_email_digest(jobs)` returns:

- `subject`
- `text`
- `html`

It is ready to be used later by an hourly job that:

1. Reads new internships from SQLite.
2. Reads subscribed user email addresses from a user database table.
3. Renders the digest template.
4. Sends the email through a provider such as SendGrid, Mailgun, Amazon SES, or SMTP.
5. Marks those jobs as emailed so users do not receive duplicates.

No email is sent yet. This file is only the digest template for the later database-email feature.

## How duplicate detection works

The bot builds a duplicate key from:

```text
company + role/title + application link
```

That key is stored in SQLite as a unique value. If the same job appears again in the same repo or another repo, the bot updates `last_seen` but does not repost it.

## Troubleshooting

### Bot is offline

- Make sure `python bot.py` is running.
- Make sure your `.env` file exists.
- Make sure `DISCORD_TOKEN` is set.
- Make sure your laptop has internet.

### Invalid Discord token

- Go back to the Discord Developer Portal.
- Open your application.
- Go to **Bot**.
- Reset/copy the token again.
- Paste it into `.env`.
- Save the file and restart `python bot.py`.

### Bot does not post in the channel

- Run `/set_channel` inside the private channel.
- Check `config.json` and make sure `discord_channel_id` is saved.
- Make sure the bot has permission to view and send messages in that channel.
- Make sure the bot has **Embed Links** permission.

### Scheduled scans are not running

- Check `/status` and confirm auto scan is enabled.
- Confirm `scan_interval_minutes` is `240`.
- Keep `python bot.py` running. The schedule stops when the process stops.
- If you changed `.env` or `config.json`, restart the bot.

### Slash commands do not appear

- Set `DISCORD_GUILD_ID` in `.env` for faster local testing.
- Restart the bot.
- Wait a minute and refresh Discord with `Ctrl + R`.
- Make sure you invited the bot with the `applications.commands` scope.

### GitHub link does not scrape correctly

- Make sure it is a public GitHub repo or README file.
- The scraper works best with Markdown tables.
- Try using the actual README URL, not just the repo homepage.
- Some repos use unusual formatting. You may need to improve `scraper/github_scraper.py` for that repo.

### Duplicate jobs keep posting

- Confirm that `internships.db` is not being deleted between runs.
- Check if the application links are changing every scan due to tracking parameters.
- Improve `build_dedupe_key()` in `database/db.py` if needed.

### Python package install errors

Try:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If Python is not found, reinstall Python and check **Add python.exe to PATH**.

## Development notes

The main flow is:

1. `bot.py` starts the Discord bot and scheduled scan loop.
2. `scanner.py` loads enabled sources from `sources.json`.
3. `scraper/github_scraper.py` fetches and parses GitHub READMEs.
4. `utils/filters.py` applies include/exclude keywords.
5. `utils/tags.py` adds FAANG or Non-FAANG classification.
6. `database/db.py` stores and deduplicates jobs.
7. `utils/formatting.py` formats jobs as Discord embeds.
8. `bot.py` posts new jobs and marks them as posted.
9. `bot.py` also DMs each premium member (see the premium tier section above) their
   personal top matches for this scan's new jobs, scored by `utils/personalization.py`
   against their `/set_profile` blurb.

If you add a new source later, create a new module in `scraper/`, return the same internship dictionary shape, and call it from `scanner.py`.

`FULL_PROJECT.md` is a generated snapshot of every tracked file for onboarding/AI-assistant
context — don't hand-edit it. Regenerate it after changing any tracked file:

```bash
python scripts/generate_full_project_doc.py
```

### Running tests

```bash
pip install -r requirements-dev.txt
pytest
```

Tests focus on `scraper/github_scraper.py` (the markdown-table parser is the most
format-fragile part of the project) and `utils/tags.py` (FAANG alias matching).
```
