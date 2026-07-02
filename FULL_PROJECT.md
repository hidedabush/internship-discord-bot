# Full Project Files

This document lists every project file, what it does, and the full code/content.

## `bot.py`

**What it does:** Main Discord bot entry point. Defines slash commands, syncs commands, runs scans, and posts new internships as Discord embeds.

```python
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
        scheduled_scan.change_interval(minutes=int(config.get("scan_interval_minutes", 60)))
        scheduled_scan.start()

    if config.get("auto_scan_on_start"):
        LOGGER.info("auto_scan_on_start is enabled. Running first scan.")
        try:
            await scan_and_post()
        except Exception:
            LOGGER.exception("Startup scan failed")


@tasks.loop(minutes=60)
async def scheduled_scan() -> None:
    try:
        LOGGER.info("Running scheduled internship scan")
        await scan_and_post()
    except Exception:
        LOGGER.exception("Scheduled scan failed")


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

```

## `scanner.py`

**What it does:** Coordinates enabled sources, filtering, database upserts, and scan summary stats.

```python
"""Run all enabled internship sources and store new jobs in SQLite."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from database.db import init_db, set_state, upsert_internship
from scraper.github_scraper import scrape_github_readme
from utils.filters import passes_filters
from utils.source_store import get_enabled_sources

LOGGER = logging.getLogger(__name__)


def run_scan(config: Dict[str, Any]) -> Dict[str, Any]:
    """Scan enabled sources and return a summary."""
    init_db()
    sources = get_enabled_sources()
    include_keywords = config.get("include_keywords", [])
    exclude_keywords = config.get("exclude_keywords", [])

    total_found = 0
    total_after_filters = 0
    new_jobs: List[Dict[str, Any]] = []
    errors: List[str] = []

    for source in sources:
        source_url = source.get("url", "")
        source_type = source.get("type", "github_readme")
        try:
            if source_type == "github_readme":
                internships = scrape_github_readme(source_url)
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
                if is_new:
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

**What it does:** Marks scraper as a Python package.

```python

```

## `scraper/github_scraper.py`

**What it does:** Fetches public GitHub README markdown and parses internship Markdown tables into normalized job dictionaries.

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
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests

LOGGER = logging.getLogger(__name__)

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


def scrape_github_readme(source_url: str) -> List[Dict]:
    """Fetch a GitHub README and return normalized internship dictionaries."""
    markdown, raw_url = fetch_readme(source_url)
    internships = parse_markdown_tables(markdown, source_url=source_url, raw_url=raw_url)
    LOGGER.info("Scraped %s internships from %s", len(internships), source_url)
    return internships


def fetch_readme(source_url: str) -> Tuple[str, str]:
    """Fetch README markdown from GitHub.

    For normal GitHub URLs, we try HEAD first, then common branch names.
    This avoids requiring a GitHub token or GitHub API setup.
    """
    candidate_urls = build_raw_candidates(source_url)
    last_error: Optional[Exception] = None

    for raw_url in candidate_urls:
        try:
            response = requests.get(raw_url, headers=REQUEST_HEADERS, timeout=20)
            if response.status_code == 200 and response.text.strip():
                return response.text, raw_url
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

    company = extract_display_text(company_cell)
    title = extract_display_text(title_cell)
    location = extract_display_text(location_cell)
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
        "tags": infer_tags(combined_text),
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

**What it does:** Safe manual ingestion module for LinkedIn job URLs. No direct LinkedIn scraping.

```python
"""Safe LinkedIn manual-link ingestion.

Direct LinkedIn scraping is intentionally not implemented. LinkedIn commonly blocks
bots and their policies prohibit scraping/automation. This module lets you paste a
job URL that you personally found and stores it in the same structure as scraped jobs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List


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
        "tags": tags or ["manual", "linkedin", "internship"],
        "status": "unknown",
    }

```

## `scraper/jobright_manual.py`

**What it does:** Safe manual ingestion module for Jobright job URLs. No direct Jobright scraping.

```python
"""Safe Jobright manual-link ingestion.

This module does not scrape Jobright. It stores URLs you manually paste so the bot can
track them alongside GitHub-sourced internships.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List


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
        "tags": tags or ["manual", "jobright", "internship"],
        "status": "unknown",
    }

```

## `database/__init__.py`

**What it does:** Marks database as a Python package.

```python

```

## `database/db.py`

**What it does:** SQLite database layer for deduplication, job storage, status tracking, and app state.

```python
"""SQLite database helpers for internship storage and duplicate detection."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "internships.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_value(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def build_dedupe_key(company: str, title: str, application_url: str) -> str:
    raw = "|".join([
        normalize_value(company),
        normalize_value(title),
        normalize_value(application_url),
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
        conn.commit()


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
    tags = ",".join(internship.get("tags", []))

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
                    tags = COALESCE(NULLIF(?, ''), tags)
                WHERE dedupe_key = ?
                """,
                (
                    current_time,
                    internship.get("location", ""),
                    internship.get("source_url", ""),
                    internship.get("source_type", ""),
                    tags,
                    dedupe_key,
                ),
            )
            conn.commit()
            return int(existing["id"]), False

        cursor = conn.execute(
            """
            INSERT INTO internships (
                dedupe_key, company, title, location, application_url, source_url,
                source_type, tags, first_seen, last_seen, posted_to_discord, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
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


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["tags"] = [tag for tag in (data.get("tags") or "").split(",") if tag]
    return data

```

## `dashboard/app.py`

**What it does:** Optional local Flask dashboard for source management and internship status tracking.

```python
"""Optional local Flask dashboard.

Run with:
    python dashboard/app.py
Then open:
    http://localhost:5000
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running this file directly from dashboard/ while importing project modules.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, redirect, render_template, request, url_for

from database.db import init_db, list_internships, update_internship_status
from utils.source_store import add_source, load_sources, remove_source, set_source_enabled

app = Flask(__name__)


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
    app.run(host="127.0.0.1", port=5000, debug=True)

```

## `dashboard/templates/index.html`

**What it does:** Dashboard home page for viewing, adding, enabling, disabling, and removing sources.

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

**What it does:** Dashboard page for viewing found internships and updating their status.

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
            <th>Company</th><th>Role</th><th>Location</th><th>Tags</th><th>Status</th><th>Apply</th><th>First seen</th>
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

**What it does:** Loads .env secrets and config.json settings, and safely saves non-secret config updates.

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
    "scan_interval_minutes": 60,
    "auto_scan_enabled": False,
    "auto_scan_on_start": False,
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
        config["scan_interval_minutes"] = int(os.getenv("SCAN_INTERVAL_MINUTES", "60"))
    if os.getenv("MAX_POSTS_PER_SCAN"):
        config["max_posts_per_scan"] = int(os.getenv("MAX_POSTS_PER_SCAN", "20"))

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

**What it does:** Reads and writes sources.json and handles add/remove/enable/disable operations.

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


def get_enabled_sources() -> List[Dict[str, Any]]:
    return [s for s in load_sources() if s.get("enabled", True)]

```

## `utils/formatting.py`

**What it does:** Formats internships into Discord embeds and batches posts.

```python
"""Discord formatting helpers."""

from __future__ import annotations

from typing import Dict, Iterable, List

import discord


def format_tags(tags: Iterable[str]) -> str:
    clean = [tag.strip().title() for tag in tags if tag and tag.strip()]
    return ", ".join(dict.fromkeys(clean)) or "Unknown"


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

    embed = discord.Embed(
        title=f"{company} — {title}",
        url=application_url if application_url.startswith("http") else None,
        description="New internship found.",
    )
    embed.add_field(name="Company", value=company, inline=True)
    embed.add_field(name="Role", value=title, inline=False)
    embed.add_field(name="Location", value=location, inline=True)

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


def chunk_list(items: List[Dict], size: int) -> List[List[Dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]

```

## `utils/filters.py`

**What it does:** Applies include/exclude keyword filtering to normalized internships.

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

## `config.json`

**What it does:** Editable local settings for channel ID, scan interval, post limits, and filters.

```json
{
  "discord_channel_id": "",
  "scan_interval_minutes": 60,
  "auto_scan_enabled": false,
  "auto_scan_on_start": false,
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
  ]
}

```

## `sources.json`

**What it does:** Sample saved GitHub internship sources with IDs, URLs, type, enabled status, and date added.

```json
[
  {
    "id": "simp2026",
    "url": "https://github.com/SimplifyJobs/Summer2026-Internships?tab=readme-ov-file",
    "type": "github_readme",
    "enabled": true,
    "date_added": "2026-07-02T00:00:00+00:00"
  },
  {
    "id": "zapp2027",
    "url": "https://github.com/zapplyjobs/Internships-2027#%EF%B8%8F-hardware--engineering",
    "type": "github_readme",
    "enabled": true,
    "date_added": "2026-07-02T00:00:00+00:00"
  }
]

```

## `requirements.txt`

**What it does:** Python dependencies needed to run the bot and optional dashboard.

```text
discord.py>=2.4.0
python-dotenv>=1.0.1
requests>=2.32.0
beautifulsoup4>=4.12.0
Flask>=3.0.0

```

## `.env.example`

**What it does:** Template for local secrets and optional environment overrides.

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
MAX_POSTS_PER_SCAN=

```

## `.gitignore`

**What it does:** Prevents secrets, database files, and virtual environments from being committed.

```gitignore
.env
internships.db
__pycache__/
*.pyc
.venv/
venv/
instance/

```

## `README.md`

**What it does:** Beginner-friendly setup, Discord Developer Portal, run, troubleshooting, and upgrade guide.

```markdown
# Discord Internship Bot

A beginner-friendly Discord bot that runs locally on your Windows laptop and posts internship opportunities into a private Discord channel.

The MVP supports public GitHub internship README repositories. LinkedIn and Jobright are supported through safe manual link ingestion instead of direct scraping.

## What this bot does

- Runs only when you start it locally.
- Scans enabled GitHub README sources.
- Parses Markdown internship tables.
- Stores jobs in local SQLite: `internships.db`.
- Avoids duplicate Discord posts using company + role + application link.
- Posts clean embeds into your private Discord channel.
- Lets you manage GitHub sources with slash commands.
- Includes an optional local dashboard at `http://localhost:5000`.

## Important LinkedIn and Jobright note

This project does **not** directly scrape LinkedIn or Jobright.

LinkedIn commonly blocks bots and states that crawlers/bots/extensions that scrape or automate LinkedIn are not permitted. Job boards also often change layouts, require login, block automated traffic, or restrict automated extraction in their terms. Because of that, this bot uses safer alternatives:

- Paste a job URL manually with `/add_manual_job`.
- Use saved job links you personally found.
- Later, import a CSV export if you build that upgrade.
- Use email/RSS alerts only when the source officially supports it.

## Project structure

```text
discord-internship-bot/
├── bot.py
├── scanner.py
├── scraper/
│   ├── __init__.py
│   ├── github_scraper.py
│   ├── linkedin_manual.py
│   └── jobright_manual.py
├── database/
│   ├── __init__.py
│   └── db.py
├── dashboard/
│   ├── app.py
│   └── templates/
│       ├── index.html
│       └── internships.html
├── utils/
│   ├── config_loader.py
│   ├── source_store.py
│   ├── formatting.py
│   └── filters.py
├── config.json
├── sources.json
├── requirements.txt
├── README.md
├── .env.example
└── .gitignore
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

This MVP uses slash commands and does not need Message Content Intent.

You can leave privileged intents off unless you later add text-prefix commands or member-reading features.

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

### Scan internships

Run:

```text
/scan
```

The bot will scan enabled GitHub sources, store jobs in SQLite, and post new jobs into the configured channel.

### Stop the bot

In PowerShell, press:

```text
Ctrl + C
```

## Discord commands

- `/scan` — manually scan all enabled GitHub sources.
- `/add_source <url>` — add a new GitHub internship repository or README URL.
- `/list_sources` — show all saved sources.
- `/remove_source <url_or_id>` — remove a source by ID or exact URL.
- `/set_channel` — set the current channel as the posting channel.
- `/status` — show bot status, number of sources, last scan time, and job counts.
- `/add_manual_job <source> <url> [company] [title] [location]` — manually save a LinkedIn or Jobright link.
- `/help` — show available commands.

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
  "scan_interval_minutes": 60,
  "auto_scan_enabled": false,
  "auto_scan_on_start": false,
  "max_posts_per_scan": 20,
  "include_keywords": ["software", "swe", "intern", "data", "ai", "quant", "gpu", "cuda"],
  "exclude_keywords": ["senior", "staff", "principal", "full-time", "new grad"]
}
```

Recommended beginner setting: keep `auto_scan_enabled` as `false` and use `/scan` manually until everything works.

If you want scheduled scanning while the bot is running locally, set:

```json
"auto_scan_enabled": true,
"scan_interval_minutes": 60
```

The bot still only runs while your laptop is on and `python bot.py` is running.

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
- Mark internships as `saved`, `applied`, `ignored`, `closed`, etc.

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
- Check if the application links are changing every scan due tracking parameters.
- Improve `build_dedupe_key()` in `database/db.py` if needed.

### Python package install errors

Try:

```powershell
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

If Python is not found, reinstall Python and check **Add python.exe to PATH**.

### Permission errors in Discord

- Check channel permissions, not just server permissions.
- Private channels need explicit bot access.
- Make sure role order does not block the bot.
- Re-invite the bot if you forgot a permission during invite.

## Future upgrade ideas

- Deploy later to Railway, Render, or a VPS.
- Add email alerts.
- Add AI summarization of job posts.
- Add automatic resume keyword matching.
- Add a stronger applied tracker with notes and deadlines.
- Add CSV export.
- Add CSV import for manual LinkedIn/Jobright saved jobs.
- Add GitHub Actions later if you ever want scheduled cloud scanning.
- Add per-source category filters.
- Add a web dashboard login if you deploy it outside localhost.

## Development notes

This project is intentionally not overengineered. The main flow is:

1. `bot.py` receives `/scan`.
2. `scanner.py` loads enabled sources from `sources.json`.
3. `scraper/github_scraper.py` fetches and parses GitHub READMEs.
4. `utils/filters.py` applies include/exclude keywords.
5. `database/db.py` stores and deduplicates jobs.
6. `utils/formatting.py` formats jobs as Discord embeds.
7. `bot.py` posts new jobs and marks them as posted.

If you want to add a new source later, create a new module in `scraper/`, return the same internship dictionary shape, and call it from `scanner.py`.

```
