"""Runtime settings.

Everything has an environment-variable default (handy for the Unraid template);
the templates and toggles can then be changed from the web UI, which stores
them in ``$CONFIG_DIR/settings.json``. Secrets (API keys) only come from env.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

DEFAULT_FILENAME_TEMPLATE = "{magazine} - {year}-{month:02}[ - Issue {issue}]"
DEFAULT_TITLE_TEMPLATE = "{magazine} - {month_name} {year}[ (Issue {issue})]"
DEFAULT_DESCRIPTION_TEMPLATE = "[{headline}\n\n][In this issue: {cover_lines}]"
DEFAULT_AUTHOR_TEMPLATE = "{publisher}"

SUPPORTED_EXTENSIONS = (".pdf", ".cbz", ".epub")


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Settings:
    # Editable from the UI
    filename_template: str = DEFAULT_FILENAME_TEMPLATE
    title_template: str = DEFAULT_TITLE_TEMPLATE
    description_template: str = DEFAULT_DESCRIPTION_TEMPLATE
    author_template: str = DEFAULT_AUTHOR_TEMPLATE
    categories: str = "Magazine"
    write_sidecar: bool = True
    write_sidecar_cover: bool = True
    write_pdf_metadata: bool = True
    skip_if_sidecar_exists: bool = True
    auto_apply: bool = False
    min_confidence_auto_apply: float = 0.8
    provider: str = "anthropic"
    claude_model: str = "claude-opus-5-5"
    ollama_url: str = "http://ollama:11434"
    ollama_model: str = "qwen2.5vl:7b"
    scan_interval_minutes: int = 0

    # Fixed at startup (paths come from the container's volume mappings)
    library_dir: str = field(default="/library", metadata={"editable": False})
    output_dir: str = field(default="", metadata={"editable": False})
    config_dir: str = field(default="/config", metadata={"editable": False})

    @property
    def settings_file(self) -> Path:
        return Path(self.config_dir) / "settings.json"

    @property
    def db_file(self) -> Path:
        return Path(self.config_dir) / "magscan.db"

    @property
    def cover_cache_dir(self) -> Path:
        return Path(self.config_dir) / "covers"

    @property
    def target_root(self) -> Path:
        """Renamed files go here: OUTPUT_DIR if set, otherwise in place under LIBRARY_DIR."""
        return Path(self.output_dir or self.library_dir)

    @classmethod
    def editable_fields(cls) -> list[str]:
        return [f.name for f in fields(cls) if f.metadata.get("editable", True)]

    @classmethod
    def load(cls) -> "Settings":
        s = cls(
            filename_template=os.environ.get("FILENAME_TEMPLATE", DEFAULT_FILENAME_TEMPLATE),
            title_template=os.environ.get("TITLE_TEMPLATE", DEFAULT_TITLE_TEMPLATE),
            description_template=os.environ.get("DESCRIPTION_TEMPLATE", DEFAULT_DESCRIPTION_TEMPLATE).replace("\\n", "\n"),
            author_template=os.environ.get("AUTHOR_TEMPLATE", DEFAULT_AUTHOR_TEMPLATE),
            categories=os.environ.get("CATEGORIES", "Magazine"),
            write_sidecar=_env_bool("WRITE_SIDECAR", True),
            write_sidecar_cover=_env_bool("WRITE_SIDECAR_COVER", True),
            write_pdf_metadata=_env_bool("WRITE_PDF_METADATA", True),
            skip_if_sidecar_exists=_env_bool("SKIP_IF_SIDECAR_EXISTS", True),
            auto_apply=_env_bool("AUTO_APPLY", False),
            min_confidence_auto_apply=float(os.environ.get("MIN_CONFIDENCE_AUTO_APPLY", "0.8")),
            provider=os.environ.get("PROVIDER", "anthropic").lower(),
            claude_model=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"),
            ollama_url=os.environ.get("OLLAMA_URL", "http://ollama:11434"),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5vl:7b"),
            scan_interval_minutes=_env_int("SCAN_INTERVAL_MINUTES", 0),
            library_dir=os.environ.get("LIBRARY_DIR", "/library"),
            output_dir=os.environ.get("OUTPUT_DIR", ""),
            config_dir=os.environ.get("CONFIG_DIR", "/config"),
        )
        if s.settings_file.exists():
            try:
                saved = json.loads(s.settings_file.read_text())
            except (OSError, json.JSONDecodeError):
                saved = {}
            s.update({k: v for k, v in saved.items() if k in cls.editable_fields()})
        return s

    def update(self, values: dict) -> None:
        for f in fields(self):
            if f.name not in values or not f.metadata.get("editable", True):
                continue
            current = getattr(self, f.name)
            value = values[f.name]
            if isinstance(current, bool):
                value = value if isinstance(value, bool) else str(value).lower() in ("1", "true", "on", "yes")
            elif isinstance(current, int):
                value = int(value)
            elif isinstance(current, float):
                value = float(value)
            setattr(self, f.name, value)

    def save(self) -> None:
        self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        data = {k: v for k, v in asdict(self).items() if k in self.editable_fields()}
        self.settings_file.write_text(json.dumps(data, indent=2))
