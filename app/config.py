"""Configuration for the standalone document recognition service.

Everything is configurable via an INI file; nothing is hard-coded.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path

# Project root = directory that contains the `app` package (i.e. parent of this file's parent).
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _split_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _to_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _to_int_list(value: str) -> list[int]:
    out: list[int] = []
    for item in _split_list(value):
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out or [300]


@dataclass
class RecognitionConfig:
    # "linear" = all linear symbologies, "all" = every format, or a comma list
    # of exact names, e.g. "Code128,Code39,EAN13".
    formats: str = "linear"
    # Page render resolutions tried in order until a barcode is found.
    dpi_candidates: list[int] = field(default_factory=lambda: [300, 400])
    max_pages: int = 0           # 0 = all pages
    try_rotate: bool = True
    try_downscale: bool = True
    try_invert: bool = True


@dataclass
class ProcessingConfig:
    extensions: list[str] = field(default_factory=lambda: [
        ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"
    ])
    stability_seconds: int = 5
    stability_checks: int = 3
    poll_interval_seconds: int = 5
    final_name_template: str = "{client_id} {full_name}"
    duplicate_policy: str = "counter"      # counter | timestamp | skip | overwrite


@dataclass
class LoggingConfig:
    level: str = "INFO"
    max_bytes: int = 5 * 1024 * 1024
    backup_count: int = 5


@dataclass
class Config:
    base_dir: Path
    incoming_dir: Path
    clients_dir: Path
    errors_dir: Path
    logs_dir: Path
    db_path: Path
    lock_path: Path
    recognition: RecognitionConfig = field(default_factory=RecognitionConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def error_dir(self, name: str) -> Path:
        return self.errors_dir / name


def _resolve(base: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (base / p)


def load_config(path: str | Path | None = None) -> Config:
    if path is None:
        path = PROJECT_ROOT / "config.ini"
    else:
        path = Path(path)
        if not path.is_absolute():
            # Prefer a file relative to the current directory, else fall back to the project root.
            candidate = Path.cwd() / path
            path = candidate if candidate.exists() else PROJECT_ROOT / path
    path = path.resolve()

    parser = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    parser.optionxform = str  # type: ignore[assignment]
    if path.exists():
        parser.read(path, encoding="utf-8")

    raw_base = parser.get("app", "base_dir", fallback=str(path.parent)).strip()
    base_dir = Path(raw_base)
    if not base_dir.is_absolute():
        raise ValueError(
            f"{path}: [app] base_dir must be an absolute path (for example /opt/docproc), got {raw_base!r}"
        )
    base_dir = base_dir.resolve()

    cfg = Config(
        base_dir=base_dir,
        incoming_dir=_resolve(base_dir, parser.get("app", "incoming_dir", fallback="incoming")),
        clients_dir=_resolve(base_dir, parser.get("app", "clients_dir", fallback="clients")),
        errors_dir=_resolve(base_dir, parser.get("app", "errors_dir", fallback="errors")),
        logs_dir=_resolve(base_dir, parser.get("app", "logs_dir", fallback="logs")),
        db_path=_resolve(base_dir, parser.get("app", "db_path", fallback="data/clients.db")),
        lock_path=_resolve(base_dir, parser.get("app", "lock_path", fallback="data/app.lock")),
    )

    if "recognition" in parser:
        r = parser["recognition"]
        cfg.recognition = RecognitionConfig(
            formats=r.get("formats", fallback="linear").strip(),
            dpi_candidates=_to_int_list(r.get("dpi_candidates", fallback="300,400")),
            max_pages=int(r.get("max_pages", fallback="0")),
            try_rotate=_to_bool(r.get("try_rotate", fallback="true")),
            try_downscale=_to_bool(r.get("try_downscale", fallback="true")),
            try_invert=_to_bool(r.get("try_invert", fallback="true")),
        )

    if "processing" in parser:
        p = parser["processing"]
        cfg.processing = ProcessingConfig(
            extensions=_split_list(p.get("extensions", fallback=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.bmp")),
            stability_seconds=int(p.get("stability_seconds", fallback="5")),
            stability_checks=int(p.get("stability_checks", fallback="3")),
            poll_interval_seconds=int(p.get("poll_interval_seconds", fallback="5")),
            final_name_template=p.get("final_name_template", fallback="{client_id} {full_name}"),
            duplicate_policy=p.get("duplicate_policy", fallback="counter").strip().lower(),
        )

    if "logging" in parser:
        lg = parser["logging"]
        cfg.logging = LoggingConfig(
            level=lg.get("level", fallback="INFO").strip().upper(),
            max_bytes=int(lg.get("max_bytes", fallback=str(5 * 1024 * 1024))),
            backup_count=int(lg.get("backup_count", fallback="5")),
        )

    return cfg


def ensure_directories(cfg: Config) -> None:
    for directory in (
        cfg.incoming_dir, cfg.clients_dir, cfg.errors_dir, cfg.logs_dir, cfg.db_path.parent,
        cfg.error_dir("no_barcode"), cfg.error_dir("client_not_found"),
        cfg.error_dir("processing_error"),
    ):
        directory.mkdir(parents=True, exist_ok=True)