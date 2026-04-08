"""
tests/test_ml_pipeline.py
=========================
Unit tests for the ML (YOLOv8) door/window detection pipeline.

Tests cover:
  1. PDF rendering to PNG
  2. DWG rendering to PNG
  3. Dataset preparation (YOLO folder structure + data.yaml)
  4. Detector graceful fallback when no model exists
  5. Detection dataclass structures
  6. Report writing

Run with:
    python3 -m pytest tests/test_ml_pipeline.py -v
"""

import sys
import json
import tempfile
from pathlib import Path

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# Render tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_render_pdf_to_png():
    """render_to_image should produce a PNG from a minimal PDF."""
    import fitz
    from ml.render import render_to_image

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a minimal PDF with a rectangle
        pdf_path = Path(tmpdir) / "test.pdf"
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.draw_rect(fitz.Rect(10, 10, 190, 190), color=(0, 0, 0))
        doc.save(str(pdf_path))
        doc.close()

        out_png = Path(tmpdir) / "test.png"
        result = render_to_image(pdf_path, out_png)

        assert result.exists(), "PNG was not created from PDF"
        assert result.stat().st_size > 0, "PNG is empty"


def test_render_dwg_to_png():
    """render_to_image should produce a PNG from a DWG file."""
    from ml.render import render_to_image

    dwg_path = _ROOT / "archived" / "A-618_Roof and Pool Floor Plan-Doors & Windows Tags.dwg"
    if not dwg_path.exists():
        import pytest
        pytest.skip("DWG test file not available")

    with tempfile.TemporaryDirectory() as tmpdir:
        out_png = Path(tmpdir) / "dwg_render.png"
        result = render_to_image(dwg_path, out_png)

        assert result.exists(), "PNG was not created from DWG"
        assert result.stat().st_size > 0, "DWG render PNG is empty"


def test_render_unsupported_format_raises():
    """render_to_image should raise ValueError for unsupported formats."""
    from ml.render import render_to_image
    import pytest

    with tempfile.TemporaryDirectory() as tmpdir:
        fake = Path(tmpdir) / "test.xyz"
        fake.touch()
        out = Path(tmpdir) / "out.png"

        with pytest.raises(ValueError, match="Unsupported"):
            render_to_image(fake, out)


def test_render_resize_respects_long_edge():
    """Output PNG should have its longest side <= long_edge."""
    import fitz
    from PIL import Image
    from ml.render import render_to_image

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "big.pdf"
        doc = fitz.open()
        page = doc.new_page(width=1000, height=500)
        page.draw_rect(fitz.Rect(10, 10, 990, 490), color=(0, 0, 0))
        doc.save(str(pdf_path))
        doc.close()

        out_png = Path(tmpdir) / "resized.png"
        render_to_image(pdf_path, out_png, long_edge=640)

        img = Image.open(str(out_png))
        assert max(img.size) <= 640, \
            f"Long edge {max(img.size)} exceeds target 640"


# ═══════════════════════════════════════════════════════════════════════════════
# Dataset preparation tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_prepare_dataset_creates_structure():
    """prepare_dataset should create the full YOLO directory tree."""
    from ml.dataset import prepare_dataset

    with tempfile.TemporaryDirectory() as tmpdir:
        img_dir = Path(tmpdir) / "images"
        img_dir.mkdir()
        # Create fake rendered images
        for i in range(5):
            (img_dir / f"plan_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 100)

        out_dir = Path(tmpdir) / "dataset"
        yaml_path = prepare_dataset(img_dir, out_dir, val_split=0.4)

        assert yaml_path.exists(), "data.yaml not created"
        assert (out_dir / "images" / "train").is_dir()
        assert (out_dir / "images" / "val").is_dir()
        assert (out_dir / "labels" / "train").is_dir()
        assert (out_dir / "labels" / "val").is_dir()

        # Check train/val split
        train_imgs = list((out_dir / "images" / "train").glob("*.png"))
        val_imgs = list((out_dir / "images" / "val").glob("*.png"))
        assert len(train_imgs) + len(val_imgs) == 5
        assert len(val_imgs) >= 1


def test_prepare_dataset_creates_label_files():
    """Each image should have a corresponding empty label .txt file."""
    from ml.dataset import prepare_dataset

    with tempfile.TemporaryDirectory() as tmpdir:
        img_dir = Path(tmpdir) / "images"
        img_dir.mkdir()
        for i in range(3):
            (img_dir / f"floor_{i}.png").write_bytes(b"\x89PNG" + b"\x00" * 50)

        out_dir = Path(tmpdir) / "dataset"
        prepare_dataset(img_dir, out_dir)

        train_labels = list((out_dir / "labels" / "train").glob("*.txt"))
        val_labels = list((out_dir / "labels" / "val").glob("*.txt"))
        total_labels = len(train_labels) + len(val_labels)
        assert total_labels == 3, f"Expected 3 label files, got {total_labels}"


def test_data_yaml_has_correct_content():
    """data.yaml should contain correct class names and paths."""
    from ml.dataset import generate_data_yaml
    import yaml

    with tempfile.TemporaryDirectory() as tmpdir:
        ds_root = Path(tmpdir) / "dataset"
        ds_root.mkdir()

        yaml_path = generate_data_yaml(ds_root, ["door", "window"])
        assert yaml_path.exists()

        with open(yaml_path) as fh:
            data = yaml.safe_load(fh)

        assert data["nc"] == 2
        assert data["names"] == ["door", "window"]
        assert data["train"] == "images/train"
        assert data["val"] == "images/val"


def test_prepare_dataset_no_images_raises():
    """prepare_dataset should raise FileNotFoundError if no images exist."""
    from ml.dataset import prepare_dataset
    import pytest

    with tempfile.TemporaryDirectory() as tmpdir:
        empty_dir = Path(tmpdir) / "empty"
        empty_dir.mkdir()
        out_dir = Path(tmpdir) / "dataset"

        with pytest.raises(FileNotFoundError):
            prepare_dataset(empty_dir, out_dir)


# ═══════════════════════════════════════════════════════════════════════════════
# Detector tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_no_model_not_ready():
    """FloorPlanDetector with non-existent model path should not be ready."""
    from ml.detector import FloorPlanDetector

    detector = FloorPlanDetector(Path("/tmp/nonexistent_model.pt"))
    assert not detector.is_ready()


def test_detector_no_model_returns_none():
    """detect() should return None when no model is loaded."""
    from ml.detector import FloorPlanDetector

    detector = FloorPlanDetector(Path("/tmp/nonexistent_model.pt"))
    result = detector.detect(Path("/tmp/some_image.png"))
    assert result is None


def test_detection_dataclass():
    """Detection dataclass should serialize correctly."""
    from ml.detector import Detection

    d = Detection(
        class_name="door",
        confidence=0.92,
        bbox=(100, 200, 150, 250),
        center=(125.0, 225.0),
    )
    data = d.to_dict()
    assert data["class"] == "door"
    assert data["confidence"] == 0.92
    assert data["bbox"] == [100, 200, 150, 250]


def test_detection_result_to_dict():
    """DetectionResult should serialize with metadata."""
    from ml.detector import Detection, DetectionResult

    result = DetectionResult(
        file="test.png",
        model="best.pt",
        door_count=3,
        window_count=1,
        detections=[
            Detection("door", 0.9, (10, 20, 30, 40), (20.0, 30.0)),
        ],
    )
    data = result.to_dict()
    assert data["door_count"] == 3
    assert data["window_count"] == 1
    assert "meta" in data
    assert data["meta"]["model"] == "best.pt"
    assert len(data["detections"]) == 1


def test_write_detection_report():
    """write_detection_report should create a valid JSON file."""
    from ml.detector import Detection, DetectionResult, write_detection_report

    result = DetectionResult(
        file="plan.png",
        model="best.pt",
        door_count=2,
        window_count=1,
        detections=[
            Detection("door", 0.95, (10, 20, 50, 60), (30.0, 40.0)),
            Detection("door", 0.87, (100, 200, 150, 260), (125.0, 230.0)),
            Detection("window", 0.91, (300, 400, 380, 450), (340.0, 425.0)),
        ],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = write_detection_report(result, tmpdir, "test_plan")
        json_path = Path(paths["json_path"])
        assert json_path.exists()

        with open(json_path) as fh:
            data = json.load(fh)

        assert data["door_count"] == 2
        assert data["window_count"] == 1
        assert len(data["detections"]) == 3


def test_get_class_names():
    """get_class_names should return door and window."""
    from ml.dataset import get_class_names
    names = get_class_names()
    assert names == ["door", "window"]


def test_collect_pdfs_flat_and_recursive():
    """collect_pdfs finds pdf in folder and optionally in subfolders."""
    from ml.dataset import collect_pdfs

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "a.pdf").write_bytes(b"%PDF-1.4 minimal")
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.pdf").write_bytes(b"%PDF-1.4 minimal")

        flat = collect_pdfs(root, recursive=False)
        assert len(flat) == 1 and flat[0].name == "a.pdf"

        deep = collect_pdfs(root, recursive=True)
        assert len(deep) == 2


def test_collect_images_flat_and_recursive():
    """collect_images finds png in folder and optionally in subfolders."""
    from ml.dataset import collect_images

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        (root / "a.png").write_bytes(b"\x89PNG" + b"\x00" * 20)
        sub = root / "sub"
        sub.mkdir()
        (sub / "b.png").write_bytes(b"\x89PNG" + b"\x00" * 20)

        flat = collect_images(root, recursive=False)
        assert len(flat) == 1 and flat[0].name == "a.png"

        deep = collect_images(root, recursive=True)
        assert len(deep) == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone runner
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = 0
    failed = 0
    skipped = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            if "SKIP" in str(type(e).__name__).upper():
                print(f"  ⊘ {t.__name__} (skipped)")
                skipped += 1
            else:
                print(f"  ✗ {t.__name__}")
                print(f"    {e}")
                failed += 1

    print()
    print(f"Results: {passed} passed, {failed} failed, {skipped} skipped out of {len(tests)} tests")
    if failed:
        sys.exit(1)
