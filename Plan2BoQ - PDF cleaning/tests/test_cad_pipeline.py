"""
tests/test_cad_pipeline.py
==========================
Unit tests for the CAD door/window detection pipeline.

All tests build minimal DXF documents IN MEMORY using ezdxf.new() —
no real DXF/DWG file is required on disk.

Coverage:
  1. DXFLayerAnalyzer correctly counts entities per layer
  2. DXFLayerAnalyzer correctly collects block names from INSERT entities
  3. DXFLayerAnalyzer returns only layers with entities
  4. DoorWindowDetector classifies a name-matched door layer correctly
  5. DoorWindowDetector classifies a block-matched door layer correctly
  6. DoorWindowDetector classifies an ARC-signal door layer correctly
  7. DoorWindowDetector classifies a window layer correctly
  8. DoorWindowDetector classifies a neutral layer as uncertain
  9. LayerClassification.to_dict() has all required keys
 10. All four signal keys are present in results
 11. reason field is always a non-empty string
 12. write_reports() produces both JSON and text files

Run with:
    python3 -m pytest tests/test_cad_pipeline.py -v
OR:
    python3 tests/test_cad_pipeline.py
"""

import sys
import json
import tempfile
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

# Allow imports from the parent (Plan2BoQ - PDF cleaning) directory
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cad.layer_analyzer import DXFLayerAnalyzer, LayerProfile
from cad.door_window_detector import DoorWindowDetector, LayerClassification, MAX_TOTAL_SCORE
from cad.report_writer import write_reports


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers — build minimal in-memory DXF documents
# ═══════════════════════════════════════════════════════════════════════════════

def _make_door_dxf() -> ezdxf.document.Drawing:
    """
    Returns a minimal DXF with:
      - Layer 'A-DOOR'     : 5 INSERT entities (block='GD01'), 3 ARC entities
      - Layer 'A-WINDOW'   : 4 INSERT entities (block='WD06')
      - Layer 'FLOOR-HATCH': 10 HATCH entities (solid fill, no door/window)
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # ── Define blocks ─────────────────────────────────────────────────────────
    door_block = doc.blocks.new("GD01")
    door_block.add_arc(center=(0, 0, 0), radius=1, start_angle=0, end_angle=90)
    door_block.add_line(start=(0, 0, 0), end=(1, 0, 0))

    win_block = doc.blocks.new("WD06")
    win_block.add_line(start=(0, 0, 0), end=(1, 0, 0))
    win_block.add_line(start=(1, 0, 0), end=(1, 1, 0))

    # ── Add layers ────────────────────────────────────────────────────────────
    doc.layers.new("A-DOOR")
    doc.layers.new("A-WINDOW")
    doc.layers.new("FLOOR-HATCH")

    # ── Populate model space ──────────────────────────────────────────────────
    # Door layer: INSERT + ARC (door swing)
    for i in range(5):
        msp.add_blockref("GD01", insert=(i * 2, 0, 0), dxfattribs={"layer": "A-DOOR"})
    for i in range(3):
        msp.add_arc(center=(i * 3, 5, 0), radius=0.9, start_angle=0, end_angle=90,
                    dxfattribs={"layer": "A-DOOR"})

    # Window layer: INSERT only (no arcs)
    for i in range(4):
        msp.add_blockref("WD06", insert=(i * 2, 10, 0), dxfattribs={"layer": "A-WINDOW"})

    # Hatch layer: solid hatching (no door/window relevance)
    for i in range(10):
        msp.add_line(start=(i, 20, 0), end=(i + 0.5, 20, 0),
                     dxfattribs={"layer": "FLOOR-HATCH"})

    return doc


def _analyze(doc) -> dict:
    """Run DXFLayerAnalyzer on an in-memory document."""
    return DXFLayerAnalyzer.from_document(doc).analyze()


def _detect(doc) -> dict:
    """Run the full detection pipeline on an in-memory document."""
    profiles = _analyze(doc)
    return DoorWindowDetector(profiles).classify_all()


# ═══════════════════════════════════════════════════════════════════════════════
# 1–3: DXFLayerAnalyzer
# ═══════════════════════════════════════════════════════════════════════════════

def test_analyzer_counts_entities_per_layer():
    """DXFLayerAnalyzer must count entities on each layer correctly."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)

    assert "A-DOOR" in profiles, "A-DOOR layer not found in profiles"
    assert "A-WINDOW" in profiles, "A-WINDOW layer not found"
    assert "FLOOR-HATCH" in profiles, "FLOOR-HATCH layer not found"

    door_p = profiles["A-DOOR"]
    assert door_p.entity_count == 8, \
        f"Expected 8 entities on A-DOOR, got {door_p.entity_count}"

    win_p = profiles["A-WINDOW"]
    assert win_p.entity_count == 4, \
        f"Expected 4 entities on A-WINDOW, got {win_p.entity_count}"


def test_analyzer_collects_block_names():
    """DXFLayerAnalyzer must record block names from INSERT entities."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)

    door_p = profiles["A-DOOR"]
    assert "GD01" in door_p.block_names, \
        f"Expected 'GD01' in block_names, got {door_p.block_names}"

    win_p = profiles["A-WINDOW"]
    assert "WD06" in win_p.block_names, \
        f"Expected 'WD06' in block_names, got {win_p.block_names}"


def test_analyzer_skips_empty_layers():
    """DXFLayerAnalyzer must not return layers with zero entities."""
    doc = ezdxf.new("R2010")
    doc.layers.new("EMPTY-LAYER")  # defined but no entities

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    assert "EMPTY-LAYER" not in profiles, \
        "Analyzer returned an empty layer — should be filtered out"


# ═══════════════════════════════════════════════════════════════════════════════
# 4: Name-matched door detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_name_match_door():
    """A layer named 'A-DOOR' must be classified as door via name signal."""
    doc = _make_door_dxf()
    results = _detect(doc)

    door_names = [r.layer for r in results["door_layers"]]
    assert "A-DOOR" in door_names, \
        f"'A-DOOR' not in door_layers. Got: {door_names}"


# ═══════════════════════════════════════════════════════════════════════════════
# 5: Block-matched door detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_block_name_door():
    """
    A layer with a neutral name but block names matching the door regex (GD01)
    AND arc entities (door-swing geometry) must be classified as door.

    A single signal (block names only) gives ~31% confidence — below the
    default 35% threshold, which is intentional (one weak signal alone is
    not enough).  Two signals together (block + arcs) comfortably cross the
    threshold and correctly identify the layer as a door layer.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # Block with an arc (door swing) inside
    blk = doc.blocks.new("GD01")
    blk.add_arc(center=(0, 0, 0), radius=1.0, start_angle=0, end_angle=90)
    blk.add_line(start=(0, 0, 0), end=(1, 0, 0))

    doc.layers.new("LAYER-NEUTRAL-NAME")

    # 6 block references on a neutral-named layer
    for i in range(6):
        msp.add_blockref("GD01", insert=(i, 0, 0), dxfattribs={"layer": "LAYER-NEUTRAL-NAME"})

    # 4 arc entities on the same layer (door-swing arcs in model space)
    for i in range(4):
        msp.add_arc(center=(i * 2, 5, 0), radius=0.9, start_angle=0, end_angle=90,
                    dxfattribs={"layer": "LAYER-NEUTRAL-NAME"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    results = DoorWindowDetector(profiles).classify_all()

    door_names = [r.layer for r in results["door_layers"]]
    assert "LAYER-NEUTRAL-NAME" in door_names, (
        "Block-name + ARC signals did not classify neutral-named layer as door. "
        f"Door layers: {door_names}. "
        "Tip: check that block_name regex '^[A-Z]{0,3}D\\d' matches 'GD01' "
        "and that arc entities on the layer contribute entity_types score."
    )


def test_detector_block_name_alone_is_uncertain():
    """
    A neutral-named layer with only block-name signal (no arcs, no name match)
    has ~31% confidence — correctly below the 35% threshold and uncertain.
    This confirms the threshold prevents false positives from single weak signals.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    blk = doc.blocks.new("GD99")
    blk.add_line(start=(0, 0, 0), end=(1, 0, 0))

    doc.layers.new("LAYER-ONLY-BLOCK")
    for i in range(4):
        msp.add_blockref("GD99", insert=(i, 0, 0), dxfattribs={"layer": "LAYER-ONLY-BLOCK"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("LAYER-ONLY-BLOCK")

    assert result is not None
    # Should score for door via block name but stay below threshold (uncertain)
    # block_score will be ~40, total = 40/130 = 0.307 < 0.35
    assert result.confidence < 0.35, (
        f"Single block-name signal confidence {result.confidence:.2f} should be "
        "below the 0.35 threshold (two signals required for confident detection)."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 6: ARC-signal door detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_arc_ratio_door():
    """
    A layer with high ARC ratio must score for door even with a neutral name.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("LAYER-XYZ-ARCS")

    # 8 arcs, 2 lines → arc_ratio = 0.80  (very high)
    for i in range(8):
        msp.add_arc(center=(i, 0, 0), radius=1.0, start_angle=0, end_angle=90,
                    dxfattribs={"layer": "LAYER-XYZ-ARCS"})
    for i in range(2):
        msp.add_line(start=(i, 5, 0), end=(i + 1, 5, 0),
                     dxfattribs={"layer": "LAYER-XYZ-ARCS"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("LAYER-XYZ-ARCS")

    assert result is not None
    # Arc ratio is high so door score should be positive
    assert result.signals["geometry_ratio"]["door_score"] > 0, \
        "High ARC ratio did not contribute a door score via geometry_ratio signal"


# ═══════════════════════════════════════════════════════════════════════════════
# 7: Window layer detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_classifies_window_layer():
    """A layer named 'A-WINDOW' with WD06 inserts must be classified as window."""
    doc = _make_door_dxf()
    results = _detect(doc)

    window_names = [r.layer for r in results["window_layers"]]
    assert "A-WINDOW" in window_names, \
        f"'A-WINDOW' not in window_layers. Got: {window_names}"


def test_detector_window_confidence_above_threshold():
    """Window layer must have confidence ≥ 0.35 (above default threshold)."""
    doc = _make_door_dxf()
    results = _detect(doc)

    for r in results["window_layers"]:
        if r.layer == "A-WINDOW":
            assert r.confidence >= 0.35, \
                f"A-WINDOW confidence {r.confidence:.2f} is below threshold 0.35"
            return
    assert False, "A-WINDOW not found in window_layers"


# ═══════════════════════════════════════════════════════════════════════════════
# 8: Neutral layer stays uncertain
# ═══════════════════════════════════════════════════════════════════════════════

def test_detector_neutral_layer_is_uncertain():
    """
    A layer with only horizontal lines and no door/window signals must be
    classified as uncertain (not door or window).
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("STRUCTURAL-BEAMS")

    for i in range(10):
        msp.add_line(start=(i, 0, 0), end=(i + 1, 0, 0),
                     dxfattribs={"layer": "STRUCTURAL-BEAMS"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    results = DoorWindowDetector(profiles).classify_all()

    door_names   = [r.layer for r in results["door_layers"]]
    window_names = [r.layer for r in results["window_layers"]]

    assert "STRUCTURAL-BEAMS" not in door_names, \
        "Neutral layer 'STRUCTURAL-BEAMS' incorrectly classified as door"
    assert "STRUCTURAL-BEAMS" not in window_names, \
        "Neutral layer 'STRUCTURAL-BEAMS' incorrectly classified as window"


# ═══════════════════════════════════════════════════════════════════════════════
# 9–11: LayerClassification structure
# ═══════════════════════════════════════════════════════════════════════════════

def test_classification_to_dict_has_all_keys():
    """LayerClassification.to_dict() must contain all required keys."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    result = DoorWindowDetector(profiles).classify_layer("A-DOOR")

    assert result is not None
    d = result.to_dict()

    required_keys = ["layer", "type", "confidence", "score", "max_score",
                     "signals", "entity_count", "reason"]
    for key in required_keys:
        assert key in d, f"to_dict() is missing key '{key}'"


def test_all_four_signal_keys_present():
    """signals dict must always contain all four signal categories."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    result = DoorWindowDetector(profiles).classify_layer("A-DOOR")

    assert result is not None
    expected_signals = ["name_match", "block_names", "entity_types", "geometry_ratio"]
    for sig in expected_signals:
        assert sig in result.signals, \
            f"Signal '{sig}' not found in result.signals. Got: {list(result.signals.keys())}"


def test_reason_is_always_non_empty_string():
    """Every LayerClassification must have a non-empty reason string."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    for r in results["all_layers"]:
        assert isinstance(r.reason, str), \
            f"Layer '{r.layer}' reason is not a string: {type(r.reason)}"
        assert len(r.reason.strip()) > 0, \
            f"Layer '{r.layer}' has an empty reason string"


def test_confidence_is_normalised():
    """All confidence values must be in [0.0, 1.0]."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    for r in results["all_layers"]:
        assert 0.0 <= r.confidence <= 1.0, \
            f"Layer '{r.layer}' confidence {r.confidence} is out of range [0, 1]"


def test_score_bounded_by_max():
    """All raw scores must be ≤ MAX_TOTAL_SCORE."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    for r in results["all_layers"]:
        assert r.score <= MAX_TOTAL_SCORE, \
            f"Layer '{r.layer}' score {r.score} exceeds MAX_TOTAL_SCORE {MAX_TOTAL_SCORE}"


# ═══════════════════════════════════════════════════════════════════════════════
# 12: report_writer produces correct files
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_reports_produces_both_files():
    """write_reports() must create both a .json and a .txt report file."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = write_reports(
            results     = results,
            source_file = "test_drawing.dxf",
            output_dir  = tmpdir,
            base_name   = "test_drawing",
        )

        assert Path(paths["json_path"]).exists(), "JSON report was not created"
        assert Path(paths["txt_path"]).exists(),  "Text report was not created"


def test_json_report_is_valid_json_with_correct_structure():
    """The generated JSON report must be valid and contain expected top-level keys."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = write_reports(
            results     = results,
            source_file = "test_drawing.dxf",
            output_dir  = tmpdir,
            base_name   = "test_drawing",
        )

        with open(paths["json_path"], encoding="utf-8") as fh:
            data = json.load(fh)

        required_keys = ["meta", "summary", "door_layers", "window_layers",
                         "uncertain", "all_layers"]
        for key in required_keys:
            assert key in data, f"JSON report missing top-level key '{key}'"

        assert data["summary"]["door_count"] >= 1, \
            "JSON summary shows 0 door layers — expected at least 1"
        assert data["summary"]["window_count"] >= 1, \
            "JSON summary shows 0 window layers — expected at least 1"


def test_json_door_layers_have_reason():
    """Every door layer in the JSON report must have a non-empty reason."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = write_reports(
            results     = results,
            source_file = "test_drawing.dxf",
            output_dir  = tmpdir,
            base_name   = "test_drawing",
        )

        with open(paths["json_path"], encoding="utf-8") as fh:
            data = json.load(fh)

        for layer_dict in data["door_layers"]:
            assert "reason" in layer_dict, "Door layer entry missing 'reason' key"
            assert len(layer_dict["reason"]) > 0, \
                f"Door layer '{layer_dict['layer']}' has empty reason"


# ═══════════════════════════════════════════════════════════════════════════════
# Multilingual detection tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_french_door_layer_name():
    """A layer named with French 'PORTE' must be detected as door."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("A-PORTE")
    for i in range(5):
        msp.add_line(start=(i, 0, 0), end=(i + 1, 0, 0), dxfattribs={"layer": "A-PORTE"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("A-PORTE")
    assert result is not None
    assert result.signals["name_match"]["door_score"] > 0, \
        "French layer name 'PORTE' did not match door name pattern"


def test_french_window_layer_name():
    """A layer named with French 'FENETRE' must be detected as window."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("A-FENETRE")
    for i in range(4):
        msp.add_line(start=(i, 0, 0), end=(i + 1, 0, 0), dxfattribs={"layer": "A-FENETRE"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("A-FENETRE")
    assert result is not None
    assert result.signals["name_match"]["window_score"] > 0, \
        "French layer name 'FENETRE' did not match window name pattern"


# ═══════════════════════════════════════════════════════════════════════════════
# Standalone runner (no pytest required)
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        v for k, v in sorted(globals().items())
        if k.startswith("test_") and callable(v)
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"  ✗ {t.__name__}")
            print(f"    {e}")
            failed += 1

    print()
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    if failed:
        sys.exit(1)
