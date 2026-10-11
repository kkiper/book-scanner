"""Render the front cover and pull the text layer from the first few pages."""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

# Claude downsizes anything with a long edge above ~1568px, so render to that.
MAX_EDGE = 1568
TEXT_PAGES = 3
TEXT_CHARS_PER_PAGE = 3000


@dataclass
class CoverData:
    jpeg: bytes
    page_text: list[str] = field(default_factory=list)
    embedded: dict = field(default_factory=dict)
    page_count: int = 0


def read_cover(path: str) -> CoverData:
    """Open a PDF/CBZ/EPUB and return page 1 as JPEG plus some context.

    The text layer is a hint only: plenty of magazine PDFs are scans with no
    text at all, which is why the image is what the model actually reads.
    """
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ValueError("document is password protected")
        if doc.page_count == 0:
            raise ValueError("document has no pages")
        page = doc[0]
        rect = page.rect
        zoom = MAX_EDGE / max(rect.width, rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        jpeg = pix.tobytes("jpeg", jpg_quality=85)

        texts = []
        for i in range(min(TEXT_PAGES, doc.page_count)):
            text = " ".join(doc[i].get_text("text").split())
            texts.append(text[:TEXT_CHARS_PER_PAGE])

        embedded = {k: v for k, v in (doc.metadata or {}).items() if v and k in ("title", "author", "subject", "keywords", "creationDate")}
        return CoverData(jpeg=jpeg, page_text=texts, embedded=embedded, page_count=doc.page_count)
