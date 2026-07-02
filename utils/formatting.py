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
