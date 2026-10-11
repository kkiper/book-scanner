"""Web UI: review what the model read, fix it, then apply."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from . import __version__
from .config import Settings
from .metadata import TEMPLATE_FIELD_NAMES, MagazineInfo, render, render_relative_path, template_fields
from .processor import Processor
from .store import Store

log = logging.getLogger(__name__)

STATUSES = ["new", "queued", "review", "applied", "skipped", "error"]
EDITABLE_INFO_FIELDS = ["magazine", "issue", "volume", "year", "month", "day", "season",
                        "date_text", "publisher", "headline", "cover_lines", "language", "topic"]

SAMPLE_INFO = MagazineInfo(
    magazine="National Geographic", issue="245", year=2024, month=7, publisher="National Geographic Partners",
    headline="The Secret Lives of Octopuses", cover_lines=["Saving the Amazon", "Inside Petra"], language="en",
    topic="Science", date_text="July 2024", confidence=0.95,
)


def create_app(settings: Optional[Settings] = None, processor: Optional[Processor] = None) -> FastAPI:
    settings = settings or Settings.load()
    if processor is None:
        processor = Processor(settings, Store(settings.db_file))
        processor.start_scheduler()
    store = processor.store

    app = FastAPI(title="magscan", version=__version__)
    templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
    templates.env.globals.update(version=__version__, settings=settings)

    def redirect(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    def preview(item: dict) -> dict:
        """Planned filename/title/description, or an error message."""
        if not item.get("info"):
            return {}
        try:
            plan = processor.plan(item)
            root = Path(settings.output_dir or settings.library_dir)
            target = plan.target
            return {
                "target": str(target.relative_to(root)) if root in target.parents else str(target),
                "title": plan.metadata.title,
                "authors": ", ".join(plan.metadata.authors),
                "description": plan.metadata.description,
                "published": plan.metadata.published_date,
                "series": plan.metadata.series_name,
                "series_number": plan.metadata.series_number,
                "categories": ", ".join(plan.metadata.categories),
            }
        except (KeyError, ValueError, ValidationError) as e:
            return {"error": str(e)}

    @app.get("/")
    def index(request: Request, status: str = "review"):
        items = store.items(status=status or None)
        for item in items:
            item["preview"] = preview(item)
            item["name"] = Path(item["path"]).name
            item["original_name"] = Path(item["original_path"]).name
        return templates.TemplateResponse(request, "index.html", {
            "items": items, "status": status, "statuses": STATUSES, "counts": store.counts(),
            "pending": processor.pending_jobs(), "current": processor.current,
        })

    @app.post("/scan")
    def scan():
        added = processor.discover()
        log.info("scan found %d new files", added)
        return redirect("/?status=new")

    @app.post("/bulk")
    def bulk(action: str = Form(...), ids: list[int] = Form(default=[]), status: str = Form("")):
        if action == "analyze_all_new":
            ids = [i["id"] for i in store.items(status="new", limit=100000)]
            action = "analyze"
        elif action == "apply_all_review":
            ids = [i["id"] for i in store.items(status="review", limit=100000)]
            action = "apply"
        for item_id in ids:
            if action in ("analyze", "apply"):
                processor.enqueue(action, item_id)
            elif action == "skip":
                store.update(item_id, status="skipped")
            elif action == "unskip":
                store.update(item_id, status="new")
        return redirect(f"/?status={status}")

    @app.get("/item/{item_id}")
    def item_page(request: Request, item_id: int):
        item = store.get(item_id)
        if not item:
            raise HTTPException(404)
        info = processor.effective_info(item).model_dump() if item["info"] else {}
        if isinstance(info.get("cover_lines"), list):
            info["cover_lines"] = "\n".join(info["cover_lines"])
        return templates.TemplateResponse(request, "item.html", {
            "item": item, "info": info, "fields": EDITABLE_INFO_FIELDS, "preview": preview(item),
            "has_history": store.last_history(item_id) is not None,
        })

    @app.post("/item/{item_id}")
    async def item_action(request: Request, item_id: int):
        item = store.get(item_id)
        if not item:
            raise HTTPException(404)
        form = await request.form()
        action = form.get("action", "save")

        if action in ("save", "save_apply") and item["info"]:
            edits = {}
            for f in EDITABLE_INFO_FIELDS:
                raw = str(form.get(f, "")).strip()
                if f == "cover_lines":
                    value = [line.strip() for line in raw.splitlines() if line.strip()]
                elif f in ("year", "month", "day"):
                    value = int(raw) if raw.isdigit() else None
                else:
                    value = raw or None
                if value != item["info"].get(f):
                    edits[f] = value
            try:
                MagazineInfo.model_validate({**item["info"], **edits})
            except ValidationError as e:
                raise HTTPException(400, str(e))
            store.update(item_id, edits=edits)
            if action == "save_apply":
                processor.enqueue("apply", item_id)
        elif action == "reanalyze":
            store.update(item_id, edits={})
            processor.enqueue("analyze", item_id)
        elif action == "skip":
            store.update(item_id, status="skipped")
        elif action == "undo":
            processor.undo(item_id)
        return redirect(f"/item/{item_id}")

    @app.get("/cover/{item_id}")
    def cover(item_id: int):
        path = processor.cover_cache(item_id)
        if not path.exists():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.get("/settings")
    def settings_page(request: Request, saved: bool = False):
        return templates.TemplateResponse(request, "settings.html", {
            "s": settings, "fields": TEMPLATE_FIELD_NAMES, "saved": saved,
            "sample": template_preview(settings.filename_template, settings.title_template,
                                       settings.description_template, settings.author_template),
        })

    @app.post("/settings")
    async def save_settings(request: Request):
        form = dict(await request.form())
        for flag in ("write_sidecar", "write_sidecar_cover", "write_pdf_metadata", "skip_if_sidecar_exists", "auto_apply"):
            form[flag] = flag in form
        check = template_preview(form.get("filename_template", ""), form.get("title_template", ""),
                                 form.get("description_template", ""), form.get("author_template", ""))
        if "error" in check:
            raise HTTPException(400, check["error"])
        settings.update(form)
        settings.description_template = settings.description_template.replace("\r\n", "\n")
        settings.save()
        processor.reset_extractor()
        return redirect("/settings?saved=1")

    @app.post("/api/preview")
    async def api_preview(request: Request):
        form = await request.json()
        return JSONResponse(template_preview(form.get("filename_template", ""), form.get("title_template", ""),
                                             form.get("description_template", ""), form.get("author_template", "")))

    @app.get("/api/status")
    def api_status():
        return {"counts": store.counts(), "pending": processor.pending_jobs(), "current": processor.current}

    return app


def template_preview(filename_t: str, title_t: str, description_t: str, author_t: str) -> dict:
    values = template_fields(SAMPLE_INFO, "natgeo_0724_scan")
    try:
        return {
            "filename": render_relative_path(filename_t, values) + ".pdf",
            "title": render(title_t, values),
            "description": render(description_t, values),
            "author": render(author_t, values),
        }
    except (KeyError, ValueError, IndexError) as e:
        return {"error": str(e)}
