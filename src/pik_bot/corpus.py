"""Load runbook sections from the docs directory."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_HEADING = re.compile(r"^## (.+)$", re.M)
_FIX = re.compile(r"\*\*Fix:\*\*\s*(.+?)(?=\n\*\*|\n## |\Z)", re.S)


@dataclass(frozen=True)
class Section:
    id: str
    title: str
    source: str
    text: str
    fix: str


def docs_dir() -> Path:
    override = os.environ.get("PIK_DOCS_DIR")
    if override:
        path = Path(override)
        if not path.is_dir():
            raise FileNotFoundError(f"PIK_DOCS_DIR does not exist: {path}")
        return path
    candidates = [
        Path.cwd() / "docs",
        Path(__file__).resolve().parents[2] / "docs",
        Path("/app/docs"),
    ]
    for candidate in candidates:
        if (candidate / "authentication.md").is_file():
            return candidate
    raise FileNotFoundError("Could not find the docs directory. Set PIK_DOCS_DIR.")


def slug(title: str) -> str:
    text = title.strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text).strip("-")
    return text


def load_sections(path: Path | None = None) -> list[Section]:
    root = path or docs_dir()
    sections: list[Section] = []
    for file in sorted(root.glob("*.md")):
        raw = file.read_text(encoding="utf-8")
        matches = list(_HEADING.finditer(raw))
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
            title = match.group(1).strip()
            body = raw[start:end].strip()
            text = f"{title}\n{body}"
            fix_match = _FIX.search(body)
            fix = " ".join(fix_match.group(1).split()) if fix_match else " ".join(body.split())
            sections.append(
                Section(
                    id=f"{file.name}#{slug(title)}",
                    title=title,
                    source=file.name,
                    text=text,
                    fix=fix,
                )
            )
    if not sections:
        raise FileNotFoundError(f"No runbook sections found in {root}")
    return sections
