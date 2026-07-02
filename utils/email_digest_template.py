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
