"""Booklore-compatible output: sidecar JSON, sidecar cover, embedded PDF metadata.

Sidecar format matches Booklore's own (``SidecarMetadata`` in booklore-api):
for ``Book.pdf`` it reads ``Book.metadata.json`` and ``Book.cover.jpg`` from
the same folder. Import them with Metadata Center -> Sidecar -> Import, or
Settings -> Library Metadata Settings -> Import All.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pymupdf

from .metadata import MagazineInfo

log = logging.getLogger(__name__)


@dataclass
class BookMetadata:
    """What gets written for one file, after templates and user edits."""

    title: str
    authors: list[str] = field(default_factory=list)
    publisher: Optional[str] = None
    published_date: Optional[str] = None  # yyyy-MM-dd
    description: Optional[str] = None
    language: Optional[str] = None
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    series_name: Optional[str] = None
    series_number: Optional[float] = None
    page_count: Optional[int] = None


def series_number(info: MagazineInfo) -> Optional[float]:
    """Booklore's series number is numeric; only use the issue if it is one."""
    if not info.issue:
        return None
    try:
        return float(info.issue.strip().lstrip("#"))
    except ValueError:
        return None


def sidecar_paths(book_path: Path) -> tuple[Path, Path]:
    # Booklore strips only the last extension: "A.B.pdf" -> "A.B.metadata.json".
    stem = book_path.name.rsplit(".", 1)[0] if "." in book_path.name[1:] else book_path.name
    return book_path.with_name(stem + ".metadata.json"), book_path.with_name(stem + ".cover.jpg")


def build_sidecar(meta: BookMetadata, cover_file: Optional[str]) -> dict:
    body = {
        "title": meta.title,
        "authors": meta.authors or None,
        "publisher": meta.publisher,
        "publishedDate": meta.published_date,
        "description": meta.description,
        "language": meta.language,
        "pageCount": meta.page_count,
        "categories": meta.categories or None,
        "tags": meta.tags or None,
    }
    if meta.series_name:
        series = {"name": meta.series_name}
        if meta.series_number is not None:
            series["number"] = meta.series_number
        body["series"] = series
    doc = {
        "version": "1.0",
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generatedBy": "magscan",
        "metadata": {k: v for k, v in body.items() if v not in (None, "", [])},
    }
    if cover_file:
        doc["cover"] = {"source": "external", "path": cover_file}
    return doc


def write_sidecar(book_path: Path, meta: BookMetadata, cover_jpeg: Optional[bytes]) -> list[Path]:
    json_path, cover_path = sidecar_paths(book_path)
    written = []
    if cover_jpeg:
        cover_path.write_bytes(cover_jpeg)
        written.append(cover_path)
    doc = build_sidecar(meta, cover_path.name if cover_jpeg else None)
    json_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    written.append(json_path)
    return written


def write_pdf_metadata(pdf_path: Path, meta: BookMetadata) -> None:
    """Embed title/author/subject/keywords so Booklore picks them up on first scan.

    Uses an incremental save: the original bytes are kept and the change is
    appended, which is fast on big scans and avoids rewriting the file.
    """
    keywords = ", ".join(meta.categories + meta.tags)
    with pymupdf.open(pdf_path) as doc:
        if doc.needs_pass or doc.is_encrypted:
            raise ValueError("encrypted PDF, metadata not written")
        current = doc.metadata or {}
        new = dict(current)
        new.update({
            "title": meta.title,
            "author": "; ".join(meta.authors),
            "subject": meta.description or "",
            "keywords": keywords,
        })
        if meta.published_date:
            new["creationDate"] = "D:" + meta.published_date.replace("-", "") + "000000"
        doc.set_metadata(new)
        tmp = None
        if doc.can_save_incrementally():
            doc.saveIncr()
        else:
            tmp = pdf_path.with_name(pdf_path.name + ".magscan-tmp")
            doc.save(tmp)
    if tmp:
        tmp.replace(pdf_path)
