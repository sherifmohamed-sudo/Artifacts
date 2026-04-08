"""
ml.dataset
==========
Organizes rendered floor plan images into the standard Ultralytics YOLOv8
dataset directory structure, ready for annotation and training.

Output structure:
    datasets/floorplans/
        data.yaml
        images/
            train/
            val/
        labels/
            train/   (empty .txt files — annotation done externally)
            val/
"""

from __future__ import annotations

import random
import shutil
import yaml
from pathlib import Path
from typing import List, Optional

_CLASSES = ["door", "window"]
_DEFAULT_VAL_SPLIT = 0.2
_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def collect_images(image_dir: str | Path, *, recursive: bool = False) -> List[Path]:
    """
    Return sorted image paths under *image_dir* (top-level only unless *recursive*).
    """
    root = Path(image_dir)
    if not root.is_dir():
        return []
    if recursive:
        return sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
        )
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() in _IMAGE_EXTENSIONS
    )


def collect_pdfs(pdf_dir: str | Path, *, recursive: bool = False) -> List[Path]:
    """
    Return sorted ``.pdf`` paths under *pdf_dir* (top-level only unless *recursive*).
    """
    root = Path(pdf_dir)
    if not root.is_dir():
        return []
    if recursive:
        return sorted(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix.lower() == ".pdf"
        )
    return sorted(
        p for p in root.iterdir()
        if p.is_file() and p.suffix.lower() == ".pdf"
    )


# ── Public API ────────────────────────────────────────────────────────────────

def prepare_dataset(
    image_dir: str | Path,
    output_dir: str | Path,
    val_split: float = _DEFAULT_VAL_SPLIT,
    seed: int = 42,
    *,
    recursive: bool = False,
) -> Path:
    """
    Copy rendered PNGs from *image_dir* into a YOLO dataset structure
    under *output_dir*.

    Creates empty label .txt files alongside each image (placeholder for
    annotation).  Generates ``data.yaml`` for Ultralytics training.

    Parameters
    ----------
    image_dir  : folder containing rendered floor plan images
    output_dir : root of the YOLO dataset to create
    val_split  : fraction of images to put in validation set
    seed       : random seed for reproducible train/val split
    recursive  : if True, include images in subfolders (names must be unique)

    Returns
    -------
    Path to the generated ``data.yaml`` file.
    """
    image_dir = Path(image_dir)
    output_dir = Path(output_dir)

    images = collect_images(image_dir, recursive=recursive)
    if not images:
        raise FileNotFoundError(
            f"No images found in {image_dir} "
            f"(extensions: {', '.join(sorted(_IMAGE_EXTENSIONS))}). "
            f"{'Try recursive=True for subfolders.' if not recursive else ''}"
        )

    random.seed(seed)
    shuffled = list(images)
    random.shuffle(shuffled)

    n_val = max(1, int(len(shuffled) * val_split))
    val_images = shuffled[:n_val]
    train_images = shuffled[n_val:]

    # At least 1 in each split
    if not train_images:
        train_images = [val_images.pop()]

    train_img_dir = output_dir / "images" / "train"
    val_img_dir = output_dir / "images" / "val"
    train_lbl_dir = output_dir / "labels" / "train"
    val_lbl_dir = output_dir / "labels" / "val"

    for d in (train_img_dir, val_img_dir, train_lbl_dir, val_lbl_dir):
        d.mkdir(parents=True, exist_ok=True)

    def _dest_filename(src: Path) -> str:
        if not recursive:
            return src.name
        try:
            rel = src.relative_to(image_dir)
            if rel.parent == Path("."):
                return src.name
            base = "__".join(rel.with_suffix("").parts).replace(" ", "_")
            return base + src.suffix.lower()
        except ValueError:
            return src.name

    def _copy_with_label(src_list: List[Path], img_dst: Path, lbl_dst: Path):
        for src in src_list:
            fname = _dest_filename(src)
            dst = img_dst / fname
            shutil.copy2(str(src), str(dst))
            label_file = lbl_dst / (Path(fname).stem + ".txt")
            if not label_file.exists():
                label_file.touch()

    _copy_with_label(train_images, train_img_dir, train_lbl_dir)
    _copy_with_label(val_images, val_img_dir, val_lbl_dir)

    data_yaml = generate_data_yaml(output_dir, _CLASSES)

    print(f"  Dataset prepared: {len(train_images)} train, {len(val_images)} val")
    print(f"  Classes: {_CLASSES}")
    print(f"  data.yaml: {data_yaml}")

    return data_yaml


def generate_data_yaml(
    dataset_root: str | Path,
    classes: Optional[List[str]] = None,
) -> Path:
    """
    Write the ``data.yaml`` config file expected by Ultralytics YOLOv8.

    Parameters
    ----------
    dataset_root : root folder of the YOLO dataset
    classes      : list of class names (default: ["door", "window"])

    Returns
    -------
    Path to the written ``data.yaml``.
    """
    dataset_root = Path(dataset_root)
    classes = classes or _CLASSES

    config = {
        "path": str(dataset_root.resolve()),
        "train": "images/train",
        "val": "images/val",
        "nc": len(classes),
        "names": classes,
    }

    yaml_path = dataset_root / "data.yaml"
    with open(yaml_path, "w", encoding="utf-8") as fh:
        yaml.dump(config, fh, default_flow_style=False, sort_keys=False)

    return yaml_path


def get_class_names() -> List[str]:
    """Return the canonical class name list."""
    return list(_CLASSES)
