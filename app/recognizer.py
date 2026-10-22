"""Barcode recognition engine (standalone, no Metka).

PDF pages are rendered with pypdfium2 and decoded with zxing-cpp.
Image files are decoded directly (multi-frame TIFF/GIF supported).
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from functools import reduce
from pathlib import Path

import pypdfium2 as pdfium
import zxingcpp
from PIL import Image, ImageSequence

from .config import RecognitionConfig
from .logger import get_logger

log = get_logger("recognizer")


@dataclass
class BarcodeHit:
    text: str
    format: str
    page: int


def resolve_formats(spec: str):
    """Map a config string to a zxing-cpp format (or combination)."""
    value = (spec or "linear").strip()
    low = value.lower()
    if low in {"linear", "all_linear"}:
        return zxingcpp.BarcodeFormat.AllLinear
    if low in {"all", "all_readable"}:
        return zxingcpp.BarcodeFormat.AllReadable
    formats = []
    for name in [x.strip() for x in value.split(",") if x.strip()]:
        attr = next((n for n in dir(zxingcpp.BarcodeFormat) if n.lower() == name.lower() and not n.startswith("_")), None)
        if attr is None:
            raise ValueError(f"unknown barcode format: {name!r}")
        formats.append(getattr(zxingcpp.BarcodeFormat, attr))
    if not formats:
        return zxingcpp.BarcodeFormat.AllLinear
    return reduce(operator.or_, formats)


class Recognizer:
    def __init__(self, cfg: RecognitionConfig):
        self.cfg = cfg
        self.formats = resolve_formats(cfg.formats)

    def recognize(self, path: Path) -> list[BarcodeHit]:
        path = Path(path)
        if path.suffix.lower() == ".pdf":
            return self._recognize_pdf(path)
        return self._recognize_image(path)

    # ------------------------------------------------------------------ helpers
    def _decode(self, image: Image.Image, page: int) -> list[BarcodeHit]:
        barcodes = zxingcpp.read_barcodes(
            image,
            formats=self.formats,
            try_rotate=self.cfg.try_rotate,
            try_downscale=self.cfg.try_downscale,
            try_invert=self.cfg.try_invert,
        )
        return [BarcodeHit(text=b.text, format=str(b.format), page=page) for b in barcodes]

    def _recognize_pdf(self, path: Path) -> list[BarcodeHit]:
        pdf = pdfium.PdfDocument(str(path))
        try:
            page_count = len(pdf)
            if self.cfg.max_pages:
                page_count = min(page_count, self.cfg.max_pages)
            for index in range(page_count):
                page = pdf[index]
                for dpi in self.cfg.dpi_candidates:
                    pil = page.render(scale=dpi / 72).to_pil().convert("RGB")
                    hits = self._decode(pil, index)
                    if hits:
                        return hits
        finally:
            pdf.close()
        return []

    def _recognize_image(self, path: Path) -> list[BarcodeHit]:
        with Image.open(path) as img:
            for index, frame in enumerate(ImageSequence.Iterator(img)):
                if self.cfg.max_pages and index >= self.cfg.max_pages:
                    break
                hits = self._decode(frame.convert("RGB"), index)
                if hits:
                    return hits
        return []