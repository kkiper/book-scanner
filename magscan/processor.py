"""Discover files, analyse covers, plan and apply renames + metadata."""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .booklore import BookMetadata, series_number, sidecar_paths, write_pdf_metadata, write_sidecar
from .config import SUPPORTED_EXTENSIONS, Settings
from .cover import read_cover
from .extract import ExtractionError, get_extractor
from .metadata import MagazineInfo, published_date, render, render_relative_path, template_fields
from .store import Store

log = logging.getLogger(__name__)


@dataclass
class Plan:
    target: Path
    metadata: BookMetadata


class Processor:
    def __init__(self, settings: Settings, store: Store, extractor=None):
        self.settings = settings
        self.store = store
        self._extractor = extractor
        self._queue: "queue.Queue[tuple[str, int]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self.current: Optional[str] = None

    # ---------------------------------------------------------------- discovery

    def discover(self) -> int:
        """Register new files under LIBRARY_DIR and forget ones that disappeared."""
        root = Path(self.settings.library_dir)
        known = self.store.all_paths()
        seen = set()
        added = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for name in filenames:
                if name.startswith(".") or not name.lower().endswith(SUPPORTED_EXTENSIONS):
                    continue
                path = Path(dirpath) / name
                seen.add(str(path))
                if str(path) in known:
                    continue
                if self.settings.skip_if_sidecar_exists and sidecar_paths(path)[0].exists():
                    continue
                if self.store.add_item(str(path)):
                    added += 1
        for path, item in known.items():
            if path not in seen and item["status"] != "applied" and not Path(path).exists():
                self.store.delete(item["id"])
        return added

    # ----------------------------------------------------------------- analysis

    @property
    def extractor(self):
        if self._extractor is None:
            self._extractor = get_extractor(self.settings)
        return self._extractor

    def reset_extractor(self) -> None:
        """Call after provider/model settings change."""
        self._extractor = None

    def cover_cache(self, item_id: int) -> Path:
        return self.settings.cover_cache_dir / f"{item_id}.jpg"

    def analyze(self, item_id: int) -> None:
        item = self.store.get(item_id)
        if not item:
            return
        path = Path(item["path"])
        try:
            cover = read_cover(str(path))
            self.settings.cover_cache_dir.mkdir(parents=True, exist_ok=True)
            self.cover_cache(item_id).write_bytes(cover.jpeg)
            info = self.extractor.extract(cover, path.name)
        except (ExtractionError, ValueError, RuntimeError, OSError) as e:
            log.warning("analyse failed for %s: %s", path, e)
            self.store.update(item_id, status="error", error=str(e))
            return
        self.store.update(item_id, status="review", info=info.model_dump(), error=None, page_count=cover.page_count)
        log.info("analysed %s -> %s %s-%s (confidence %.2f)", path.name, info.magazine, info.year, info.month, info.confidence)

        if self.settings.auto_apply and self._confident(info):
            self.apply(item_id)

    def _confident(self, info: MagazineInfo) -> bool:
        return bool(info.magazine and info.year) and info.confidence >= self.settings.min_confidence_auto_apply

    # --------------------------------------------------------------- planning

    @staticmethod
    def effective_info(item: dict) -> MagazineInfo:
        data = dict(item["info"] or {})
        data.update(item.get("edits") or {})
        return MagazineInfo.model_validate(data)

    def plan(self, item: dict, info: Optional[MagazineInfo] = None) -> Plan:
        s = self.settings
        info = info or self.effective_info(item)
        current = Path(item["path"])
        values = template_fields(info, Path(item["original_path"]).stem)

        rel = render_relative_path(s.filename_template, values)
        if "/" in rel:
            base = s.target_root / rel
        else:
            base = (Path(s.output_dir) if s.output_dir else current.parent) / rel
        target = base.with_name(base.name + current.suffix.lower())

        title = render(s.title_template, values).strip() or current.stem
        author = render(s.author_template, values).strip()
        description = render(s.description_template, values).strip() or None
        categories = [c.strip() for c in s.categories.split(",") if c.strip()]
        if info.topic and info.topic not in categories:
            categories.append(info.topic)

        meta = BookMetadata(
            title=title,
            authors=[a.strip() for a in author.split(";") if a.strip()],
            publisher=info.publisher,
            published_date=published_date(info),
            description=description,
            language=info.language,
            categories=categories,
            series_name=info.magazine,
            series_number=series_number(info),
            page_count=item.get("page_count"),
        )
        return Plan(target=target, metadata=meta)

    @staticmethod
    def _unique(target: Path, current: Path) -> Path:
        if target == current or not target.exists():
            return target
        for n in range(2, 1000):
            candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
            if candidate == current or not candidate.exists():
                return candidate
        raise FileExistsError(f"too many files named like {target}")

    # ----------------------------------------------------------------- apply

    def apply(self, item_id: int) -> None:
        item = self.store.get(item_id)
        if not item or not item["info"]:
            return
        s = self.settings
        current = Path(item["path"])
        try:
            plan = self.plan(item)
            target = self._unique(plan.target, current)

            if s.write_pdf_metadata and current.suffix.lower() == ".pdf":
                try:
                    write_pdf_metadata(current, plan.metadata)
                except (ValueError, RuntimeError) as e:
                    log.warning("PDF metadata not written for %s: %s", current, e)

            created: list[str] = []
            if target != current:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(current), str(target))
                # Keep any existing sidecars attached to the book, like Booklore does.
                for old, new in zip(sidecar_paths(current), sidecar_paths(target)):
                    if old.exists() and not new.exists():
                        shutil.move(str(old), str(new))

            if s.write_sidecar:
                cover = self.cover_cache(item_id)
                cover_bytes = cover.read_bytes() if s.write_sidecar_cover and cover.exists() else None
                existed = {p for p in sidecar_paths(target) if p.exists()}
                for p in write_sidecar(target, plan.metadata, cover_bytes):
                    if p not in existed:
                        created.append(str(p))

            self.store.add_history(item_id, str(current), str(target), created)
            self.store.update(item_id, path=str(target), status="applied", error=None)
            log.info("applied %s -> %s", current.name, target)
        except (OSError, ValueError, KeyError) as e:
            log.warning("apply failed for %s: %s", current, e)
            self.store.update(item_id, status="error", error=f"apply failed: {e}")

    def undo(self, item_id: int) -> None:
        """Move the file back and remove sidecars we created. Embedded PDF metadata stays."""
        hist = self.store.last_history(item_id)
        if not hist:
            return
        current, original = Path(hist["to_path"]), Path(hist["from_path"])
        for f in hist["created_files"]:
            Path(f).unlink(missing_ok=True)
        if current != original and current.exists():
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(original))
            for new, old in zip(sidecar_paths(current), sidecar_paths(original)):
                if new.exists() and not old.exists():
                    shutil.move(str(new), str(old))
            _remove_empty_dirs(current.parent, Path(self.settings.target_root))
        self.store.mark_undone(hist["id"])
        self.store.update(item_id, path=str(original), status="review")

    # ------------------------------------------------------------ background

    def enqueue(self, action: str, item_id: int) -> None:
        if action == "analyze":
            self.store.update(item_id, status="queued")
        self._queue.put((action, item_id))
        self._ensure_worker()

    def pending_jobs(self) -> int:
        return self._queue.qsize() + (1 if self.current else 0)

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._run, name="magscan-worker", daemon=True)
        self._worker.start()

    def _run(self) -> None:
        while True:
            action, item_id = self._queue.get()
            self.current = f"{action} #{item_id}"
            try:
                getattr(self, action)(item_id)
            except Exception:  # keep the worker alive whatever happens to one file
                log.exception("%s failed for item %s", action, item_id)
                self.store.update(item_id, status="error", error="unexpected error, see container log")
            finally:
                self.current = None
                self._queue.task_done()

    def start_scheduler(self) -> None:
        if self.settings.scan_interval_minutes <= 0:
            return

        def loop():
            while True:
                try:
                    self.discover()
                    for item in self.store.items(status="new"):
                        self.enqueue("analyze", item["id"])
                except Exception:
                    log.exception("scheduled scan failed")
                time.sleep(max(1, self.settings.scan_interval_minutes) * 60)

        threading.Thread(target=loop, name="magscan-scheduler", daemon=True).start()


def _remove_empty_dirs(folder: Path, stop: Path) -> None:
    try:
        while folder != stop and stop in folder.parents and not any(folder.iterdir()):
            folder.rmdir()
            folder = folder.parent
    except OSError:
        pass
