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
