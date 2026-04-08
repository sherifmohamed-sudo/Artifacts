#!/usr/bin/env python3
"""
Build a YOLOv8 dataset from files in the shared unprocessed/ folder.

Usage (from Plan2BoQ - PDF cleaning/):
    python3 ml/build_dataset_from_unprocessed.py
    python3 ml/build_dataset_from_unprocessed.py --clean
    python3 ml/build_dataset_from_unprocessed.py --recursive

Inputs in unprocessed/:
    - Images: .png, .jpg, .jpeg, .webp  (copied as-is)
    - PDFs:   .pdf  (first page rasterised to PNG via PyMuPDF)

DWG/DXF are not handled here (use ml.render separately or the CAD pipeline).

Output:
    ml/datasets/floorplans/
      data.yaml
      images/train/  images/val/
      labels/train/  labels/val/   (empty .txt — annotate with LabelImg / CVAT / Roboflow)
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_ROOT = _SCRIPT_DIR.parent
UNPROCESSED = _ROOT / "unprocessed"
DEFAULT_OUT = _ROOT / "ml" / "datasets" / "floorplans"
# Staging dir for mixed PDF render + image copy (under ml/datasets/, gitignored)
_STAGING = _ROOT / "ml" / "datasets" / ".staging_unprocessed"


def _safe_staging_name(path: Path, kind: str) -> str:
    """Avoid clashes between plan.pdf, plan.png, and nested same names."""
    stem = path.stem.replace(" ", "_")
    if kind == "pdf":
        return f"{stem}__from_pdf.png"
    return path.name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Create YOLO dataset from images and/or PDFs in unprocessed/",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=DEFAULT_OUT,
        help=f"Dataset root (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--val-split",
        type=float,
        default=0.2,
        help="Fraction of images for validation (default: 0.2)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for train/val split",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete output folder before building (fresh dataset)",
    )
    parser.add_argument(
        "--recursive", "-r",
        action="store_true",
        help="Include files in subfolders of unprocessed/",
    )
    parser.add_argument(
        "--dpi",
        type=int,
        default=300,
        help="DPI for PDF rasterisation (default: 300)",
    )
    parser.add_argument(
        "--long-edge",
        type=int,
        default=1280,
        help="Max long edge in pixels for rendered PDFs (default: 1280, 0 = no resize)",
    )
    args = parser.parse_args()

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    from ml.dataset import collect_images, collect_pdfs, prepare_dataset
    from ml.render import render_to_image

    if not UNPROCESSED.is_dir():
        print(f"Error: unprocessed folder not found: {UNPROCESSED}", file=sys.stderr)
        return 1

    images = collect_images(UNPROCESSED, recursive=args.recursive)
    pdfs = collect_pdfs(UNPROCESSED, recursive=args.recursive)

    if not images and not pdfs:
        print(
            f"No images or PDFs in {UNPROCESSED}\n"
            f"  Add .png, .jpg, .jpeg, .webp and/or .pdf (flat or use --recursive).\n"
            f"  DWG/DXF are not converted by this script.",
            file=sys.stderr,
        )
        return 1

    out = args.output.resolve()
    if args.clean and out.exists():
        shutil.rmtree(out)

    if _STAGING.exists():
        shutil.rmtree(_STAGING)
    _STAGING.mkdir(parents=True, exist_ok=True)

    try:
        print(f"Source : {UNPROCESSED}")
        print(f"  Images: {len(images)}  PDFs: {len(pdfs)}")
        print(f"Output : {out}")

        used_names: set[str] = set()

        for pdf in pdfs:
            name = _safe_staging_name(pdf, "pdf")
            if name in used_names:
                name = f"{pdf.stem.replace(' ', '_')}__from_pdf_{id(pdf)}.png"
            used_names.add(name)
            dest = _STAGING / name
            print(f"  PDF → PNG: {pdf.name}  (first page only)")
            render_to_image(
                pdf, dest,
                long_edge=args.long_edge,
                dpi=args.dpi,
            )

        for img in images:
            name = img.name
            if name in used_names:
                name = f"{img.stem.replace(' ', '_')}__img{img.suffix.lower()}"
            used_names.add(name)
            shutil.copy2(str(img), str(_STAGING / name))

        yaml_path = prepare_dataset(
            _STAGING,
            out,
            val_split=args.val_split,
            seed=args.seed,
            recursive=False,
        )
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    finally:
        if _STAGING.exists():
            shutil.rmtree(_STAGING, ignore_errors=True)

    print()
    print("Next steps:")
    print("  1. Annotate empty label files in labels/train/ and labels/val/")
    print("     Classes: 0=door  1=window  (YOLO format: class cx cy w h)")
    print("  2. Zip the floorplans folder (or entire datasets/) for Colab")
    print(f"  3. data.yaml: {yaml_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
