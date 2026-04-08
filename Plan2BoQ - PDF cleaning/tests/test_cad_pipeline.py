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
# Xref resolver tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_xref_detection_on_empty_layers():
    """A profile set with many empty layers and xref names must be detected as xref sheet."""
    from cad.xref_resolver import analyze_xrefs

    profiles = {}
    # Simulate xref sheet: layer "0" has a few entities, 30+ xref layers empty
    p0 = LayerProfile("0")
    p0.entity_count = 50
    profiles["0"] = p0

    for name in [
        "SomeFloorPlan$0$A-DOOR",
        "SomeFloorPlan$0$A-WINDOW",
        "SomeFloorPlan$0$A-DIM",
        "SomeFloorPlan$0$A-WALL",
        "SomeFloorPlan$0$A-TEXT",
        "Grid$0$A-GRID",
        "TitleBlock$0$TB-TEXT",
    ]:
        profiles[name] = LayerProfile(name)
    # Add 20+ more empty layers (garbled names)
    for i in range(20):
        n = f"garbled_{i}"
        profiles[n] = LayerProfile(n)

    result = analyze_xrefs(profiles, Path("/tmp/test.dwg"))
    assert result.is_xref_sheet, "Should detect xref sheet pattern"
    assert "SomeFloorPlan" in result.xref_base_names, \
        f"Should extract xref base name. Got: {result.xref_base_names}"


def test_xref_detection_normal_file():
    """A file with many entities across layers should NOT be flagged as xref sheet."""
    from cad.xref_resolver import analyze_xrefs

    profiles = {}
    for name in ["A-DOOR", "A-WINDOW", "A-WALL", "A-DIM"]:
        p = LayerProfile(name)
        p.entity_count = 200
        profiles[name] = p

    result = analyze_xrefs(profiles, Path("/tmp/test.dxf"))
    assert not result.is_xref_sheet, "Normal file with many entities should not be xref sheet"


def test_xref_source_file_discovery():
    """Source file discovery returns found/missing paths correctly."""
    from cad.xref_resolver import _locate_source_files

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake source DWG
        src = Path(tmpdir) / "A-118_Floor Plan.dwg"
        src.touch()

        found, missing = _locate_source_files(
            ["A-118_Floor Plan", "Missing_File"],
            [Path(tmpdir)],
        )
        assert len(found) == 1, f"Should find 1 file, got {len(found)}"
        assert found[0].name == "A-118_Floor Plan.dwg"
        assert "Missing_File" in missing


# ═══════════════════════════════════════════════════════════════════════════════
# Xref name normalization tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_xref_layer_name_normalization_door():
    """Xref-prefixed layer name 'SomeFile$0$A_A_DOOR' must match door pattern via normalization."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("SomeFile$0$A_A_DOOR")
    for i in range(3):
        msp.add_line(start=(i, 0, 0), end=(i+1, 0, 0),
                     dxfattribs={"layer": "SomeFile$0$A_A_DOOR"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("SomeFile$0$A_A_DOOR")
    assert result is not None
    assert result.signals["name_match"]["door_score"] > 0, \
        "Xref-prefixed 'A_A_DOOR' should be detected via name normalization"


def test_xref_layer_name_normalization_window():
    """Xref-prefixed layer name 'Plan$0$A-WIND-TAG' must match window pattern."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("Plan$0$A-WIND-TAG")
    for i in range(3):
        msp.add_line(start=(i, 0, 0), end=(i+1, 0, 0),
                     dxfattribs={"layer": "Plan$0$A-WIND-TAG"})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    result = DoorWindowDetector(profiles).classify_layer("Plan$0$A-WIND-TAG")
    assert result is not None
    assert result.signals["name_match"]["window_score"] > 0, \
        "Xref-prefixed 'A-WIND-TAG' should be detected via name normalization"


# ═══════════════════════════════════════════════════════════════════════════════
# Text tag scoring tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_text_tag_scoring_door():
    """TEXT entities containing 'GD01', 'GD02' should contribute door text score."""
    p = LayerProfile("0")
    p.entity_count = 10
    p.text_contents = ["GD01", "GD02", "GD03", "ROOF PLAN", "1:100"]

    detector = DoorWindowDetector({"0": p})
    result = detector.classify_layer("0")
    assert result is not None
    assert result.signals["text_tags"]["door_score"] > 0, \
        "Door text tags (GD01, GD02, GD03) should produce a door text score"
    assert len(result.signals["text_tags"]["door_tags"]) == 3


def test_text_tag_scoring_window():
    """TEXT entities containing 'WD01', 'WD02' should contribute window text score."""
    p = LayerProfile("0")
    p.entity_count = 10
    p.text_contents = ["WD01", "WD02", "TITLE", "A-604"]

    detector = DoorWindowDetector({"0": p})
    result = detector.classify_layer("0")
    assert result is not None
    assert result.signals["text_tags"]["window_score"] > 0, \
        "Window text tags (WD01, WD02) should produce a window text score"
    assert len(result.signals["text_tags"]["window_tags"]) == 2


def test_text_tag_scoring_mixed():
    """Mixed GD + WD tags should produce both door and window scores."""
    p = LayerProfile("0")
    p.entity_count = 20
    p.text_contents = ["GD01", "GD02", "WD01", "WD02", "WD03"]

    detector = DoorWindowDetector({"0": p})
    result = detector.classify_layer("0")
    assert result is not None
    tt = result.signals["text_tags"]
    assert tt["door_score"] > 0, "Should have door score from GD tags"
    assert tt["window_score"] > 0, "Should have window score from WD tags"


def test_text_tag_scoring_no_tags():
    """Non-tag text content should produce zero text scores."""
    p = LayerProfile("0")
    p.entity_count = 10
    p.text_contents = ["ROOF PLAN", "1:100", "A-618", "SCALE"]

    detector = DoorWindowDetector({"0": p})
    result = detector.classify_layer("0")
    assert result is not None
    tt = result.signals["text_tags"]
    assert tt["door_score"] == 0
    assert tt["window_score"] == 0


def test_text_tags_signal_in_signals_dict():
    """The 'text_tags' key must always be present in the signals dict."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    result = DoorWindowDetector(profiles).classify_layer("A-DOOR")
    assert result is not None
    assert "text_tags" in result.signals, \
        "Signal 'text_tags' must be present in result.signals"


def test_score_bounded_by_new_max():
    """All raw scores must be <= MAX_TOTAL_SCORE (150 after Signal 6)."""
    doc = _make_door_dxf()
    profiles = _analyze(doc)
    results = DoorWindowDetector(profiles).classify_all()

    for r in results["all_layers"]:
        assert r.score <= MAX_TOTAL_SCORE, \
            f"Layer '{r.layer}' score {r.score} exceeds MAX_TOTAL_SCORE {MAX_TOTAL_SCORE}"


def test_text_contents_collected_in_dxf():
    """DXFLayerAnalyzer should collect text contents from TEXT entities."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("A-TEXT-LAYER")
    msp.add_text("GD01", dxfattribs={"layer": "A-TEXT-LAYER", "height": 2.5})
    msp.add_text("WD02", dxfattribs={"layer": "A-TEXT-LAYER", "height": 2.5})

    profiles = DXFLayerAnalyzer.from_document(doc).analyze()
    assert "A-TEXT-LAYER" in profiles
    p = profiles["A-TEXT-LAYER"]
    assert "GD01" in p.text_contents, \
        f"Expected 'GD01' in text_contents, got {p.text_contents}"
    assert "WD02" in p.text_contents


# ═══════════════════════════════════════════════════════════════════════════════
# Door/window counter tests
# ═══════════════════════════════════════════════════════════════════════════════

from cad.door_window_counter import (
    DoorWindowItem, CountResult,
    _match_text_tag, _merge_and_dedup, _sweep_angle,
    _count_arcs_dwg, _scan_text_tags_dwg,
    count_from_dxf, write_count_reports,
)


def test_sweep_angle_90():
    """Sweep from 0 to 90 degrees = 90."""
    assert _sweep_angle(0.0, 90.0) == 90.0


def test_sweep_angle_wraparound():
    """Sweep from 350 to 80 degrees = 90 (wraps past 360)."""
    assert _sweep_angle(350.0, 80.0) == 90.0


def test_sweep_angle_nan_returns_zero():
    """Non-finite angles return 0."""
    assert _sweep_angle(float("nan"), 90.0) == 0.0


def test_match_text_tag_door():
    """'GD01' matches as a door tag."""
    items = _match_text_tag("GD01", 100.0, 200.0, "0")
    assert len(items) == 1
    assert items[0].category == "door"
    assert items[0].item_id == "GD01"
    assert items[0].confidence == "high"


def test_match_text_tag_window():
    """'WD06' matches as a window tag."""
    items = _match_text_tag("WD06", 300.0, 400.0, "A-WIN")
    assert len(items) == 1
    assert items[0].category == "window"
    assert items[0].item_id == "WD06"


def test_match_text_tag_no_match():
    """'ROOF PLAN' should not match any tag patterns."""
    items = _match_text_tag("ROOF PLAN", 0, 0, "0")
    assert len(items) == 0


def test_match_text_tag_multiple_in_one_string():
    """Text containing both GD01 and WD02 should produce two items."""
    items = _match_text_tag("Label: GD01 / WD02", 10.0, 20.0, "0")
    cats = {i.category for i in items}
    assert "door" in cats
    assert "window" in cats


def test_match_text_tag_short_patterns():
    """Short patterns like D01, W05 should match via short-form regexes (2+ digits)."""
    d_items = _match_text_tag("D01", 0, 0, "0")
    w_items = _match_text_tag("W05", 0, 0, "0")
    # D01 has 2 digits → D\d{2,} matches
    assert len(d_items) == 1
    assert d_items[0].category == "door"
    # W05 has 2 digits → W\d{2,} matches
    assert len(w_items) == 1
    assert w_items[0].category == "window"
    # Single digit should NOT match
    d_single = _match_text_tag("D1", 0, 0, "0")
    assert len(d_single) == 0, "D1 (single digit) should not match short pattern"


def test_merge_dedup_no_overlap():
    """Non-overlapping ARC and text tags should both be kept."""
    arcs = [DoorWindowItem("ARC-0", "door", "arc_geometry", 100.0, 100.0, "0", "medium")]
    tags = [DoorWindowItem("GD01", "door", "text_tag", 5000.0, 5000.0, "0", "high")]
    doors, windows = _merge_and_dedup(arcs, tags)
    assert len(doors) == 2
    assert len(windows) == 0


def test_merge_dedup_nearby_removes_arc():
    """An ARC within 500 units of a text tag should be dropped (dedup)."""
    arcs = [DoorWindowItem("ARC-0", "door", "arc_geometry", 100.0, 100.0, "0", "medium")]
    tags = [DoorWindowItem("GD01", "door", "text_tag", 105.0, 110.0, "0", "high")]
    doors, windows = _merge_and_dedup(arcs, tags)
    assert len(doors) == 1
    assert doors[0].item_id == "GD01", "Text tag should be kept over ARC"


def test_merge_dedup_windows_kept():
    """Window tags must appear in the windows list."""
    arcs = []
    tags = [
        DoorWindowItem("WD01", "window", "text_tag", 10.0, 20.0, "0", "high"),
        DoorWindowItem("WD02", "window", "text_tag", 30.0, 40.0, "0", "high"),
    ]
    doors, windows = _merge_and_dedup(arcs, tags)
    assert len(doors) == 0
    assert len(windows) == 2


def test_count_from_dxf_produces_result():
    """count_from_dxf on a DXF with door arcs and text tags should produce items."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    doc.layers.new("D-LAYER")

    for i in range(3):
        msp.add_arc(center=(i * 100, 0, 0), radius=50.0, start_angle=0, end_angle=90,
                     dxfattribs={"layer": "D-LAYER"})
    msp.add_text("GD01", dxfattribs={"layer": "D-LAYER", "height": 5.0,
                                       "insert": (50, 10, 0)})
    msp.add_text("WD01", dxfattribs={"layer": "D-LAYER", "height": 5.0,
                                       "insert": (500, 10, 0)})

    with tempfile.TemporaryDirectory() as tmpdir:
        dxf_path = Path(tmpdir) / "test.dxf"
        doc.saveas(str(dxf_path))
        result = count_from_dxf(dxf_path)

    assert result.door_count >= 1, f"Expected at least 1 door, got {result.door_count}"
    assert result.window_count >= 1, f"Expected at least 1 window, got {result.window_count}"


def test_write_count_reports_produces_files():
    """write_count_reports must produce a JSON and CSV file."""
    result = CountResult(
        file="test.dwg",
        door_count=2,
        window_count=1,
        doors=[
            DoorWindowItem("GD01", "door", "text_tag", 100.0, 200.0, "0", "high"),
            DoorWindowItem("ARC-0", "door", "arc_geometry", 500.0, 600.0, "0", "medium"),
        ],
        windows=[
            DoorWindowItem("WD01", "window", "text_tag", 300.0, 400.0, "0", "high"),
        ],
        is_xref_sheet=False,
        source_files_used=["test.dwg"],
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = write_count_reports(result, tmpdir, "test")
        assert Path(paths["json_path"]).exists(), "Count JSON not created"
        assert Path(paths["csv_path"]).exists(), "Count CSV not created"

        with open(paths["json_path"], encoding="utf-8") as fh:
            data = json.load(fh)
        assert data["door_count"] == 2
        assert data["window_count"] == 1
        assert len(data["doors"]) == 2
        assert len(data["windows"]) == 1

        with open(paths["csv_path"], encoding="utf-8") as fh:
            lines = fh.read().strip().split("\n")
        assert len(lines) == 4, f"CSV should have 1 header + 3 data rows, got {len(lines)}"


def test_count_result_dataclass_fields():
    """CountResult must have all expected fields."""
    r = CountResult("f.dwg", 1, 2, [], [], False, ["f.dwg"])
    assert r.file == "f.dwg"
    assert r.door_count == 1
    assert r.window_count == 2
    assert r.is_xref_sheet is False


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
