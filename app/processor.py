"""Document processing pipeline (standalone, no Metka).

incoming/<file> -> recognize barcode -> SQLite lookup -> clients/<id name>/
"""
from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import re
import shutil
import signal
import threading
import time
from datetime import datetime
from pathlib import Path

from . import database as db
from .config import Config
from .database import Database
from .logger import get_logger
from .recognizer import Recognizer

log = get_logger("processor")

_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_component(value: str) -> str:
    return _INVALID_FS.sub("_", value).strip().rstrip(".") or "unknown"


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def move_file(src: Path, dst: Path) -> None:
    """Move a document, tolerating a cross-device setup (e.g. SMB incoming -> local clients).

    `os.replace` fails with EXDEV across mount points, so fall back to copying into
    the destination directory under a temporary name and renaming it atomically.
    The source is removed only after the destination is safely in place.
    """
    try:
        os.replace(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    tmp = dst.with_name(f".{dst.name}.part")
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    os.unlink(src)


def wait_for_stable_file(path: Path, stable_seconds: int, checks: int, extensions: list[str]) -> bool:
    if path.suffix.lower() not in extensions:
        return False
    previous = -1
    stable = 0
    for _ in range(checks):
        if not path.exists():
            return False
        try:
            size = path.stat().st_size
        except OSError:
            size = -1
        if size == previous and size >= 0:
            stable += 1
            if stable >= checks - 1:
                return True
        else:
            stable = 0
        previous = size
        time.sleep(max(1, stable_seconds) / max(1, checks))
    return False


class DocumentProcessor:
    def __init__(self, cfg: Config, recognizer: Recognizer, database: Database):
        self.cfg = cfg
        self.recognizer = recognizer
        self.db = database
        self._stop_event = threading.Event()
        self._lock_handle = None

    # -------------------------------------------------------------- lifecycle
    def stop(self) -> None:
        self._stop_event.set()

    def _handle_signal(self, signum: int, _frame) -> None:
        log.info("signal %s received; stopping", signum)
        self.stop()

    def _install_signal_handlers(self) -> None:
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, self._handle_signal)
            except (ValueError, OSError):  # not in main thread / unsupported
                pass

    def acquire_single_instance_lock(self) -> None:
        self.cfg.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.cfg.lock_path, "a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(f"another instance is already running ({exc})") from exc
        try:
            handle.seek(0)
            handle.truncate()
            handle.write(f"{os.getpid()}\n")
            handle.flush()
        except OSError:
            pass
        self._lock_handle = handle

    def release_single_instance_lock(self) -> None:
        handle = self._lock_handle
        if handle is None:
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                handle.close()
            finally:
                self._lock_handle = None

    def recover(self) -> None:
        log.info("recovery: reconciling state")
        for doc in self.db.all_documents():
            if doc["status"] in db.TERMINAL_STATUSES:
                continue
            if doc["status"] == db.NEW:
                source = Path(doc["source_path"]) if doc["source_path"] else None
                if source and source.exists():
                    continue  # will be resumed by the main loop
                self.db.update_document(doc["id"], status=db.ERROR, error="source file missing during recovery")
        log.info("recovery: done")

    def run(self) -> None:
        self.acquire_single_instance_lock()
        self._install_signal_handlers()
        try:
            self.recover()
            log.info("processor started; formats=%s dpi=%s", self.cfg.recognition.formats,
                     self.cfg.recognition.dpi_candidates)
            while not self._stop_event.is_set():
                try:
                    self.process_incoming()
                except Exception:
                    log.exception("unexpected error in main loop")
                self._stop_event.wait(max(1, self.cfg.processing.poll_interval_seconds))
            log.info("processor stopped")
        finally:
            self.release_single_instance_lock()

    # -------------------------------------------------------------- incoming
    def _existing_doc(self, path: Path):
        row = self.db.find_document_by_source(str(path))
        if row is None:
            return None
        return row

    def process_incoming(self) -> None:
        incoming = self.cfg.incoming_dir
        if not incoming.exists():
            return
        for path in sorted(incoming.iterdir()):
            if self._stop_event.is_set():
                return
            if not path.is_file() or path.name.startswith("."):
                continue
            if path.suffix.lower() not in self.cfg.processing.extensions:
                continue

            # A file present in `incoming` is always a new occurrence unless there is an
            # unfinished (NEW) record for this path to resume. Terminal records must NOT
            # block a freshly dropped file that reuses a previously seen name.
            existing = self._existing_doc(path)
            if existing is not None and existing["status"] == db.NEW:
                doc_id = int(existing["id"])
            else:
                doc_id = None

            if not wait_for_stable_file(path, self.cfg.processing.stability_seconds,
                                        self.cfg.processing.stability_checks, self.cfg.processing.extensions):
                log.debug("file not stable yet: %s", path)
                continue
            try:
                with open(path, "rb"):
                    pass
            except OSError:
                continue

            if doc_id is None:
                try:
                    doc_id = self.db.add_document(path.name, str(path), sha256_of(path))
                except Exception:
                    log.exception("failed to create document record for %s", path)
                    continue

            self._process_file(doc_id, path)

    def _process_file(self, doc_id: int, path: Path) -> None:
        try:
            hits = self.recognizer.recognize(path)
        except Exception as exc:
            log.exception("recognition failed for %s", path.name)
            self._finish_error(doc_id, path, "processing_error", f"{type(exc).__name__}: {exc}", db.ERROR)
            return

        if not hits:
            log.info("no barcode: %s", path.name)
            self._finish_error(doc_id, path, "no_barcode", "no barcode detected", db.NO_BARCODE)
            return

        texts = [h.text for h in hits]
        if len(set(texts)) > 1:
            log.warning("multiple different barcodes on %s: %s; using first", path.name, texts)
        barcode = hits[0].text
        log.info("recognized %s -> %s (%s, page %s)", path.name, barcode, hits[0].format, hits[0].page)

        client = self.db.find_client_by_barcode(barcode)
        if client is None:
            log.info("client not found for barcode=%s (%s)", barcode, path.name)
            self._finish_error(doc_id, path, "client_not_found", "client not found",
                               db.CLIENT_NOT_FOUND, barcode=barcode)
            return

        try:
            final = self._final_destination(client, path.name)
            move_file(path, final)
            self.db.update_document(
                doc_id, status=db.COMPLETED, barcode=barcode, client_id=int(client["client_id"]),
                final_path=str(final), processed_at=db.utcnow(), error=None,
            )
            log.info("doc=%s barcode=%s -> client %s (%s) => %s",
                     doc_id, barcode, client["client_id"], client["full_name"], final)
        except Exception as exc:
            log.exception("failed to move %s to client folder", path.name)
            self._finish_error(doc_id, path, "processing_error", f"{type(exc).__name__}: {exc}",
                               db.ERROR, barcode=barcode)

    def _finish_error(self, doc_id: int, path: Path, category: str, error: str, status: str, barcode: str | None = None) -> None:
        target = None
        try:
            if path.exists():
                target = unique_target(self.cfg.error_dir(category), path.name, self.cfg.processing.duplicate_policy)
                move_file(path, target)
        except Exception:
            log.exception("failed to move %s to errors/%s", path, category)
        fields = dict(status=status, error=error, processed_at=db.utcnow())
        if target is not None:
            fields["final_path"] = str(target)
        if barcode is not None:
            fields["barcode"] = barcode
        self.db.update_document(doc_id, **fields)

    def _final_destination(self, client, filename: str) -> Path:
        folder_name = self.cfg.processing.final_name_template.format(
            client_id=client["client_id"], full_name=client["full_name"])
        folder = self.cfg.clients_dir / sanitize_component(folder_name)
        folder.mkdir(parents=True, exist_ok=True)
        return unique_target(folder, filename, self.cfg.processing.duplicate_policy)


def unique_target(folder: Path, filename: str, policy: str) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    candidate = folder / filename
    if not candidate.exists():
        return candidate
    if policy in {"overwrite", "skip"}:
        return candidate
    stem, suffix = Path(filename).stem, Path(filename).suffix
    if policy == "timestamp":
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return folder / f"{stem}_{stamp}{suffix}"
    index = 1
    while True:
        candidate = folder / f"{stem}_{index:03d}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1