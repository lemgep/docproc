"""Generate a test PDF containing a Code 128 barcode.

Usage:
    .venv/bin/python examples/make_sample.py 0020000101 incoming/sample.pdf
    .venv/bin/python examples/make_sample.py --no-barcode incoming/blank.pdf
"""
from __future__ import annotations

import argparse
import warnings

import numpy as np
import zxingcpp as z
from PIL import Image

warnings.filterwarnings("ignore", category=DeprecationWarning)

A4 = (2480, 3508)


def make(code: str | None, out_path: str) -> None:
    page = Image.new("RGB", A4, "white")
    if code:
        raw = z.write_barcode(z.BarcodeFormat.Code128, code)
        bar = Image.fromarray(np.asarray(raw)).convert("RGB")
        bar = bar.resize((int(A4[0] * 0.6), int(bar.height * A4[0] * 0.6 / bar.width)))
        page.paste(bar, ((A4[0] - bar.width) // 2, 400))
    page.save(out_path, "PDF", resolution=300)
    print(f"saved {out_path} (barcode={code!r})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("code", nargs="?", help="barcode text (Code 128)")
    parser.add_argument("output")
    parser.add_argument("--no-barcode", action="store_true", help="generate a PDF without a barcode")
    args = parser.parse_args()
    make(None if args.no_barcode else args.code, args.output)
