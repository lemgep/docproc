"""Entry point: run daemon, one-shot sweep, or one-off diagnostics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config, ensure_directories, load_config
from .database import Database
from .logger import setup_logging
from .processor import DocumentProcessor
from .recognizer import Recognizer


def _new_db(cfg: Config) -> Database:
    return Database(cfg.db_path)


def _new_processor(cfg: Config) -> tuple[DocumentProcessor, Database]:
    db = _new_db(cfg)
    recognizer = Recognizer(cfg.recognition)
    return DocumentProcessor(cfg, recognizer, db), db


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    ensure_directories(cfg)
    log = setup_logging(cfg.logs_dir, cfg.logging.level, cfg.logging.max_bytes, cfg.logging.backup_count)
    log.info("daemon mode starting; base_dir=%s", cfg.base_dir)
    processor, db = _new_processor(cfg)
    try:
        try:
            processor.run()
        except RuntimeError as exc:
            # Another instance holds the lock; systemd must not restart-loop on this.
            log.info("already running: %s", exc)
    finally:
        db.close()
    return 0


def cmd_once(args: argparse.Namespace, cfg: Config) -> int:
    ensure_directories(cfg)
    log = setup_logging(cfg.logs_dir, cfg.logging.level, cfg.logging.max_bytes, cfg.logging.backup_count)
    db = _new_db(cfg)
    recognizer = Recognizer(cfg.recognition)
    processor = DocumentProcessor(cfg, recognizer, db)
    try:
        processor.acquire_single_instance_lock()
    except RuntimeError as exc:
        log.info("skip run: %s", exc)
        db.close()
        return 0
    try:
        processor.recover()
        processor.process_incoming()
    finally:
        processor.release_single_instance_lock()
        db.close()
    return 0


def cmd_recognize(args: argparse.Namespace, cfg: Config) -> int:
    """One-off: decode barcodes in a single file without moving it."""
    log = setup_logging(cfg.logs_dir, cfg.logging.level, cfg.logging.max_bytes, cfg.logging.backup_count)
    path = Path(args.file)
    recognizer = Recognizer(cfg.recognition)
    hits = recognizer.recognize(path)
    print(f"file: {path}")
    print(f"hits: {len(hits)}")
    for h in hits:
        print(f"  page={h.page} format={h.format} text={h.text}")
    return 0


def cmd_add_client(args: argparse.Namespace, cfg: Config) -> int:
    db = _new_db(cfg)
    client_id = int(args.client_id)
    db.upsert_client(client_id, args.full_name, args.barcode)
    print(f"client upserted: id={client_id} name={args.full_name!r} barcode={args.barcode}")
    db.close()
    return 0


def cmd_import_clients(args: argparse.Namespace, cfg: Config) -> int:
    import csv

    db = _new_db(cfg)
    count = 0
    with open(args.csv, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            client_id = int(row["client_id"])
            full_name = row["full_name"].strip()
            barcode = row["barcode"].strip()
            db.upsert_client(client_id, full_name, barcode)
            count += 1
    db.close()
    print(f"imported {count} clients from {args.csv}")
    return 0


def cmd_status(args: argparse.Namespace, cfg: Config) -> int:
    db = _new_db(cfg)
    docs = db.all_documents()
    print(f"documents: {len(docs)}")
    counts = {}
    for d in docs:
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    for st, n in sorted(counts.items()):
        print(f"  {st}: {n}")
    clients = db.conn.execute("SELECT COUNT(*) AS c FROM clients").fetchone()["c"]
    print(f"clients: {clients}")
    db.close()
    return 0


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="docproc", description="Standalone document processor (no Metka)")
    parser.add_argument("--config", default=None, help="path to config.ini (default: <project>/config.ini)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run as daemon")
    p_run.set_defaults(func=cmd_run)

    p_once = sub.add_parser("once", help="process incoming files once and exit")
    p_once.set_defaults(func=cmd_once)

    p_rec = sub.add_parser("recognize", help="decode barcodes in a file (diagnostic)")
    p_rec.add_argument("file")
    p_rec.set_defaults(func=cmd_recognize)

    p_add = sub.add_parser("add-client", help="upsert one client")
    p_add.add_argument("client_id")
    p_add.add_argument("full_name")
    p_add.add_argument("barcode")
    p_add.set_defaults(func=cmd_add_client)

    p_imp = sub.add_parser("import-clients", help="import clients from CSV (client_id,full_name,barcode)")
    p_imp.add_argument("csv")
    p_imp.set_defaults(func=cmd_import_clients)

    p_st = sub.add_parser("status", help="show document/client counts")
    p_st.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    return args.func(args, cfg)


if __name__ == "__main__":
    sys.exit(main())
