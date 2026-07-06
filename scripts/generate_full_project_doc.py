"""Regenerate FULL_PROJECT.md from the current source tree.

FULL_PROJECT.md is a snapshot for onboarding/AI-assistant context. Hand-maintaining
it drifts from the real code fast, so it's generated instead: run this after any
change to a file in TRACKED_FILES below (or whenever FULL_PROJECT.md looks stale).

Run with:
    python scripts/generate_full_project_doc.py
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT_DIR / "FULL_PROJECT.md"

# Listed in the order they should appear in the doc.
TRACKED_FILES = [
    "bot.py",
    "scanner.py",
    "scraper/__init__.py",
    "scraper/github_scraper.py",
    "scraper/linkedin_manual.py",
    "scraper/jobright_manual.py",
    "database/__init__.py",
    "database/db.py",
    "dashboard/app.py",
    "dashboard/templates/index.html",
    "dashboard/templates/internships.html",
    "utils/config_loader.py",
    "utils/source_store.py",
    "utils/formatting.py",
    "utils/filters.py",
    "utils/tags.py",
    "utils/relevance.py",
    "utils/personalization.py",
    "utils/ollama_client.py",
    "utils/email_digest_template.py",
    "config.example.json",
    "sources.example.json",
    "requirements.txt",
    "requirements-dev.txt",
    ".env.example",
    ".gitignore",
    "docker-compose.yml",
    "Dockerfile",
    ".dockerignore",
    "README.md",
]

FENCE_LANGUAGE_BY_NAME = {
    ".py": "python",
    ".json": "json",
    ".html": "html",
    ".txt": "text",
    ".md": "markdown",
    ".yml": "yaml",
}
FENCE_LANGUAGE_BY_FULL_NAME = {
    ".gitignore": "gitignore",
    ".dockerignore": "gitignore",
    ".env.example": "env",
    "Dockerfile": "dockerfile",
}


def _fence_language(path: Path) -> str:
    if path.name in FENCE_LANGUAGE_BY_FULL_NAME:
        return FENCE_LANGUAGE_BY_FULL_NAME[path.name]
    return FENCE_LANGUAGE_BY_NAME.get(path.suffix, "text")


def _summary_for(path: Path) -> str:
    """First line of the module docstring for .py files, else a placeholder."""
    if path.suffix != ".py":
        return "See file contents below."
    try:
        module = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return "See file contents below."
    docstring = ast.get_docstring(module)
    if not docstring:
        return "See file contents below."
    return docstring.strip().splitlines()[0]


def build_doc() -> str:
    lines = [
        "# Full Project Files",
        "",
        "This document lists every tracked project file, what it does, and its full",
        "content. It is generated — do not hand-edit it, changes will be overwritten.",
        "",
        "Regenerate with:",
        "",
        "```bash",
        "python scripts/generate_full_project_doc.py",
        "```",
        "",
    ]

    for relative_path in TRACKED_FILES:
        path = ROOT_DIR / relative_path
        if not path.exists():
            continue

        content = path.read_text(encoding="utf-8").rstrip("\n")
        lines.append(f"## `{relative_path}`")
        lines.append("")
        lines.append(f"**What it does:** {_summary_for(path)}")
        lines.append("")
        lines.append(f"```{_fence_language(path)}")
        lines.append(content)
        lines.append("```")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"


def main() -> None:
    OUTPUT_PATH.write_text(build_doc(), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH.relative_to(ROOT_DIR)}")


if __name__ == "__main__":
    main()
