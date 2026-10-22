"""End-to-end self-test in an isolated sandbox (no real folders touched).

Run from the project root:
    .venv/bin/python tests/selftest.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import warnings
from pathlib import Path

import numpy as np
import zxingcpp as z
from PIL import Image

warnings.filterwarnings("ignore", category=DeprecationWarning)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import (  # noqa: E402
    Config, LoggingConfig, ProcessingConfig, RecognitionConfig, ensure_directories,
)
from app.database import Database  # noqa: E402
from app.processor import DocumentProcessor  # noqa: E402
from app.recognizer import Recognizer  # noqa: E402

A4 = (2480, 3508)
results: list[tuple[bool, str]] = []


def check(condition: bool, label: str) -> None:
    results.append((bool(condition), label))
    print(f"{'PASS' if condition else 'FAIL'} {label}")


def make_pdf(path: Path, code: str | None) -> None:
    page = Image.new("RGB", A4, "white")
    if code:
        raw = z.write_barcode(z.BarcodeFormat.Code128, code)
        bar = Image.fromarray(np.asarray(raw)).convert("RGB")
        bar = bar.resize((int(A4[0] * 0.6), int(bar.height * A4[0] * 0.6 / bar.width)))
        page.paste(bar, ((A4[0] - bar.width) // 2, 400))
    page.save(path, "PDF", resolution=300)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="docproc_selftest_"))
    try:
        cfg = Config(
            base_dir=tmp,
            incoming_dir=tmp / "incoming",
            clients_dir=tmp / "clients",
            errors_dir=tmp / "errors",
            logs_dir=tmp / "logs",
            db_path=tmp / "data" / "clients.db",
            lock_path=tmp / "data" / "app.lock",
            recognition=RecognitionConfig(),
            processing=ProcessingConfig(stability_seconds=0, stability_checks=2, poll_interval_seconds=1),
            logging=LoggingConfig(),
        )
        ensure_directories(cfg)

        db = Database(cfg.db_path)
        db.upsert_client(20000101, "Test Client", "0020000101")

        make_pdf(cfg.incoming_dir / "ok.pdf", "0020000101")
        make_pdf(cfg.incoming_dir / "unknown.pdf", "0099999999")
        make_pdf(cfg.incoming_dir / "blank.pdf", None)

        proc = DocumentProcessor(cfg, Recognizer(cfg.recognition), db)
        proc.acquire_single_instance_lock()
        proc.recover()
        proc.process_incoming()

        by_name = {d["filename"]: d for d in db.all_documents()}

        ok = by_name.get("ok.pdf")
        check(ok is not None and ok["status"] == "COMPLETED", "known barcode -> COMPLETED")
        if ok is not None:
            final = Path(ok["final_path"])
            check(final.exists(), "known barcode -> file moved to client folder")
            check(final.parent.parent == cfg.clients_dir, "final file is inside clients dir")
            check(ok["client_id"] == 20000101, "client_id resolved from barcode")

        unk = by_name.get("unknown.pdf")
        check(unk is not None and unk["status"] == "CLIENT_NOT_FOUND", "unknown barcode -> CLIENT_NOT_FOUND")
        check((cfg.error_dir("client_not_found") / "unknown.pdf").exists(), "unknown barcode file in errors/client_not_found")

        blank = by_name.get("blank.pdf")
        check(blank is not None and blank["status"] == "NO_BARCODE", "no barcode -> NO_BARCODE")
        check((cfg.error_dir("no_barcode") / "blank.pdf").exists(), "no-barcode file in errors/no_barcode")

        check(len(db.all_documents()) == 3, "exactly 3 documents recorded")

        # idempotency: a second sweep must not create new records or move files
        proc.process_incoming()
        check(len(db.all_documents()) == 3, "idempotent second sweep")
        check(list(cfg.incoming_dir.iterdir()) == [], "incoming is empty after processing")

        db.close()
        proc.release_single_instance_lock()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    passed = sum(1 for ok, _ in results if ok)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
