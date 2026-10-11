"""The magazine metadata model and the user-defined templates built on it.

Template syntax
---------------
``{field}`` or ``{field:format}`` - Python format spec, e.g. ``{month:02}``.
``[ ... ]`` - optional section, dropped entirely when any field inside it is
empty. ``[ - Issue {issue}]`` only shows up when an issue number was found.
Use ``[[`` and ``]]`` for literal brackets.
"""

from __future__ import annotations

import calendar
import re
import string
import unicodedata
from typing import Optional

from pydantic import BaseModel, Field


class MagazineInfo(BaseModel):
    magazine: Optional[str] = Field(None, description="Magazine name exactly as branded on the masthead, e.g. 'National Geographic'.")
    issue: Optional[str] = Field(None, description="Issue number as printed, without a leading '#' or 'No.'.")
    volume: Optional[str] = Field(None, description="Volume number if printed.")
    year: Optional[int] = Field(None, description="Cover date year (four digits).")
    month: Optional[int] = Field(None, description="Cover date month 1-12. For a double issue like 'July/August' use the first month.")
    day: Optional[int] = Field(None, description="Day of month, only for weekly or daily publications that print one.")
    season: Optional[str] = Field(None, description="Season if the cover is dated by season, e.g. 'Summer'.")
    date_text: Optional[str] = Field(None, description="The cover date exactly as printed, e.g. 'July/August 2024'.")
    publisher: Optional[str] = Field(None, description="Publisher if visible on the cover or in the text excerpt.")
    headline: Optional[str] = Field(None, description="The main cover story headline.")
    cover_lines: list[str] = Field(default_factory=list, description="Other cover lines / teasers, in order of prominence.")
    language: Optional[str] = Field(None, description="ISO 639-1 language code of the cover text, e.g. 'en'.")
    topic: Optional[str] = Field(None, description="One- or two-word subject of the magazine, e.g. 'Photography', 'Cars', 'Technology'.")
    confidence: float = Field(0.0, description="0-1: how sure you are about magazine name and cover date together.")
    notes: Optional[str] = Field(None, description="Anything ambiguous a human reviewer should know.")


SEASON_MONTHS = {"spring": 3, "summer": 6, "autumn": 9, "fall": 9, "winter": 12}


def template_fields(info: MagazineInfo, original_stem: str = "") -> dict:
    """Values available to the templates. Missing values are None."""
    month = info.month if info.month and 1 <= info.month <= 12 else None
    date = None
    if info.year:
        date = f"{info.year:04d}" + (f"-{month:02d}" if month else "") + (f"-{info.day:02d}" if month and info.day else "")
    return {
        "magazine": info.magazine,
        "issue": info.issue,
        "volume": info.volume,
        "year": info.year,
        "month": month,
        "month_name": calendar.month_name[month] if month else None,
        "month_abbr": calendar.month_abbr[month] if month else None,
        "day": info.day,
        "season": info.season,
        "date": date,
        "date_text": info.date_text,
        "publisher": info.publisher,
        "headline": info.headline,
        "cover_lines": "; ".join(info.cover_lines) if info.cover_lines else None,
        "language": info.language,
        "topic": info.topic,
        "original": original_stem or None,
    }


TEMPLATE_FIELD_NAMES = list(template_fields(MagazineInfo()).keys())

_formatter = string.Formatter()
# Split on optional sections while honouring [[ / ]] escapes.
_SECTION_RE = re.compile(r"(?<!\[)\[(?!\[)(.*?)(?<!\])\](?!\])", re.S)


def _render_text(text: str, values: dict, sanitize, optional: bool) -> Optional[str]:
    """Format ``text``. Empty fields render as nothing, or make an optional
    section return None so the whole section is dropped."""
    out = []
    for literal, name, spec, conv in _formatter.parse(text):
        out.append(literal)
        if name is None:
            continue
        if name not in values:
            raise KeyError(f"unknown template field '{{{name}}}' (available: {', '.join(TEMPLATE_FIELD_NAMES)})")
        value = values[name]
        if value is None or value == "":
            if optional:
                return None
            continue
        if conv:
            value = _formatter.convert_field(value, conv)
        rendered = format(value, spec)
        out.append(sanitize(rendered) if sanitize else rendered)
    return "".join(out)


def render(template: str, values: dict, sanitize=None) -> str:
    """Render a template. Required fields that are empty render as nothing.

    ``sanitize`` is applied to each substituted value (not to the template's
    own literal text), so a '/' in a magazine name can't create a folder.
    """
    parts = []
    pos = 0
    for m in _SECTION_RE.finditer(template):
        parts.append(_render_text(template[pos:m.start()], values, sanitize, optional=False))
        parts.append(_render_text(m.group(1), values, sanitize, optional=True) or "")
        pos = m.end()
    parts.append(_render_text(template[pos:], values, sanitize, optional=False))
    return "".join(parts).replace("[[", "[").replace("]]", "]")


_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_component(value: str) -> str:
    """Make a string safe as a single file/folder name on Linux, SMB and Windows."""
    value = unicodedata.normalize("NFC", value)
    value = _ILLEGAL.sub(" ", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value[:180]


def render_relative_path(template: str, values: dict) -> str:
    """Render the filename template into a relative path (without extension)."""
    raw = render(template, values, sanitize=lambda v: v.replace("/", "-").replace("\\", "-"))
    parts = [sanitize_component(p) for p in raw.split("/")]
    parts = [p for p in parts if p]
    if not parts:
        raise ValueError("filename template rendered to an empty name")
    return "/".join(parts)


def published_date(info: MagazineInfo) -> Optional[str]:
    """ISO date for Booklore's publishedDate (it requires a full yyyy-MM-dd)."""
    if not info.year:
        return None
    month = info.month if info.month and 1 <= info.month <= 12 else None
    if month is None and info.season:
        month = SEASON_MONTHS.get(info.season.strip().lower())
    month = month or 1
    day = info.day or 1
    day = min(day, calendar.monthrange(info.year, month)[1])
    return f"{info.year:04d}-{month:02d}-{day:02d}"
