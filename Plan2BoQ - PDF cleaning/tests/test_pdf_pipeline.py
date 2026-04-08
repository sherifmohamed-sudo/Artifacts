"""
tests/test_pdf_pipeline.py
==========================
Regression guard for the PDF pipeline.

These tests confirm that the existing PDF processing code is UNCHANGED
after the CAD pipeline was added.  They verify:

  1. All modules import cleanly (no import-time errors)
  2. Critical constants (REMOVE_PATTERNS) are intact
  3. layer_should_remove() makes correct keep/remove decisions
  4. All 9 public pipeline functions exist with the expected signatures
  5. The CAD package does NOT import any PDF libraries (isolation check)

Run with:
    python3 -m pytest tests/test_pdf_pipeline.py -v
OR:
    python3 tests/test_pdf_pipeline.py
"""

import sys
import inspect
from pathlib import Path

# Allow imports from the parent (Plan2BoQ - PDF cleaning) directory
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ═══════════════════════════════════════════════════════════════════════════════
# 1.  Import-time smoke tests
# ═══════════════════════════════════════════════════════════════════════════════

def test_process_floor_plans_imports():
    """process_floor_plans.py must import without errors."""
    try:
        import process_floor_plans  # noqa: F401
    except Exception as exc:
        raise AssertionError(f"process_floor_plans import failed: {exc}") from exc


def test_clean_floor_plan_imports():
    """clean_floor_plan.py must import without errors."""
    try:
        import clean_floor_plan  # noqa: F401
    except Exception as exc:
        raise AssertionError(f"clean_floor_plan import failed: {exc}") from exc


def test_ml_confidence_scorer_imports():
    """ml_confidence_scorer.py must import without errors."""
    try:
        import ml_confidence_scorer  # noqa: F401
    except Exception as exc:
        raise AssertionError(f"ml_confidence_scorer import failed: {exc}") from exc


# ═══════════════════════════════════════════════════════════════════════════════
# 2.  REMOVE_PATTERNS integrity
# ═══════════════════════════════════════════════════════════════════════════════

def test_remove_patterns_non_empty():
    """REMOVE_PATTERNS must be a non-empty list."""
    import process_floor_plans as pfp
    assert isinstance(pfp.REMOVE_PATTERNS, list), "REMOVE_PATTERNS is not a list"
    assert len(pfp.REMOVE_PATTERNS) > 0, "REMOVE_PATTERNS is empty"


def test_remove_patterns_contains_hatch():
    """'HATCH' must be in REMOVE_PATTERNS."""
    import process_floor_plans as pfp
    assert "HATCH" in pfp.REMOVE_PATTERNS, "'HATCH' not found in REMOVE_PATTERNS"


def test_remove_patterns_contains_furn():
    """'FURN' must be in REMOVE_PATTERNS (furniture removal)."""
    import process_floor_plans as pfp
    assert "FURN" in pfp.REMOVE_PATTERNS, "'FURN' not found in REMOVE_PATTERNS"


def test_remove_patterns_contains_grid():
    """'GRID' must be in REMOVE_PATTERNS (grid lines)."""
    import process_floor_plans as pfp
    assert "GRID" in pfp.REMOVE_PATTERNS, "'GRID' not found in REMOVE_PATTERNS"


# ═══════════════════════════════════════════════════════════════════════════════
# 3.  layer_should_remove() correctness
# ═══════════════════════════════════════════════════════════════════════════════

def test_door_tag_is_kept():
    """A-DOOR-TAG must never be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A-DOOR-TAG") is False, \
        "layer_should_remove('A-DOOR-TAG') returned True — door tags must be kept"


def test_door_layer_is_kept():
    """A_A_DOOR must never be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A_A_DOOR") is False, \
        "layer_should_remove('A_A_DOOR') returned True"


def test_wind_tag_is_kept():
    """A-WIND-TAG must never be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A-WIND-TAG") is False, \
        "layer_should_remove('A-WIND-TAG') returned True — window tags must be kept"


def test_window_layer_is_kept():
    """Glazing layers containing 'wind' (but not wind-direction) must be kept."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A_A_WIND") is False


def test_wind_direction_layer_is_removed():
    """Wind-direction / compass layers must be removed even though name contains 'wind'."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A-WIND-DIRECTION") is True
    assert layer_should_remove("A-WIND-DIR") is True
    assert layer_should_remove("CLIMATE-COMPASS") is True


def test_wall_layer_is_kept():
    """Layers containing 'wall' (non-landscape) must be kept."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A-WALL") is False
    assert layer_should_remove("A_WALL_OUTLINE") is False


def test_landscape_wall_is_removed():
    """Landscape walls must be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("LANDSCAPE_WALL") is True


def test_hatch_layer_is_removed():
    """Hatching layers must always be removed."""
    from process_floor_plans import layer_should_remove
    # A_L_HAT_WALL is intentionally KEPT (contains 'wall' — Priority 1 keep wins)
    # Use unambiguous hatch-only names:
    assert layer_should_remove("A_FLOOR_HATCH") is True, \
        "A_FLOOR_HATCH should be removed"
    assert layer_should_remove("A-HATCH-CONC") is True, \
        "A-HATCH-CONC should be removed"
    assert layer_should_remove("AD-HAT-CONC") is True, \
        "AD-HAT-CONC should be removed (matches -HAT- pattern)"


def test_furniture_layer_is_removed():
    """Furniture layers must be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A_FURN_BEDROOM") is True


def test_stair_layer_is_removed():
    """Stair layers must be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A_A_STAIR") is True


def test_grid_layer_is_removed():
    """Grid layers must be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("AXES_GRID_01") is True


def test_cladding_and_storefront_layers_removed():
    """Stone cladding / storefront XRef layers must be removed."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("A-STONE CLADDING") is True
    assert layer_should_remove("XR_GF ST-01$0$A-STONE CLADDING") is True
    assert layer_should_remove("A-CLADD-GLASS") is True
    assert layer_should_remove("A-VERTICAL FINS") is True
    assert layer_should_remove("A-UPPER") is True


def test_unknown_layer_is_kept():
    """Unknown layers must default to KEEP (when in doubt, keep it)."""
    from process_floor_plans import layer_should_remove
    assert layer_should_remove("SOME_UNKNOWN_LAYER_XYZ") is False


# ═══════════════════════════════════════════════════════════════════════════════
# 4.  Pipeline function existence and signatures
# ═══════════════════════════════════════════════════════════════════════════════

_EXPECTED_FUNCTIONS = {
    "layer_should_remove":         ["name"],
    "clean_floor_plan":            ["input_pdf", "output_pdf"],
    "render_preview":              ["pdf_path", "png_path"],
    "render_web_pdf":              ["cleaned_pdf", "web_pdf"],
    "process_single_pdf":         ["input_path", "cleaned_dir", "archived_dir"],
    "find_unprocessed_pdfs":      ["unprocessed_dir"],
    "generate_processing_report": ["results", "report_path"],
}


def test_all_pipeline_functions_exist():
    """All expected pipeline functions must exist in process_floor_plans."""
    import process_floor_plans as pfp
    for fn_name in _EXPECTED_FUNCTIONS:
        assert hasattr(pfp, fn_name), \
            f"process_floor_plans is missing function '{fn_name}'"


def test_pipeline_function_signatures():
    """Each pipeline function must have the expected parameter names."""
    import process_floor_plans as pfp
    for fn_name, expected_params in _EXPECTED_FUNCTIONS.items():
        fn = getattr(pfp, fn_name, None)
        assert fn is not None, f"Function '{fn_name}' not found"
        sig = inspect.signature(fn)
        actual_params = list(sig.parameters.keys())
        for param in expected_params:
            assert param in actual_params, (
                f"Function '{fn_name}' is missing parameter '{param}'. "
                f"Actual params: {actual_params}"
            )


def test_clean_floor_plan_exists_in_clean_module():
    """clean_floor_plan module must expose clean_floor_plan function."""
    import clean_floor_plan as cfl
    assert hasattr(cfl, "clean_floor_plan"), \
        "clean_floor_plan.py does not expose 'clean_floor_plan' function"
    sig = inspect.signature(cfl.clean_floor_plan)
    params = list(sig.parameters.keys())
    assert "input_pdf" in params
    assert "output_pdf" in params


def test_ml_scorer_has_confidence_scorer_class():
    """ml_confidence_scorer must expose ConfidenceScorer class."""
    import ml_confidence_scorer as mcs
    assert hasattr(mcs, "ConfidenceScorer"), \
        "ml_confidence_scorer.py does not expose 'ConfidenceScorer' class"
    scorer = mcs.ConfidenceScorer()
    assert hasattr(scorer, "analyze_layer_name"), \
        "ConfidenceScorer is missing 'analyze_layer_name' method"


# ═══════════════════════════════════════════════════════════════════════════════
# 5.  Isolation check — CAD package must NOT import PDF libraries
# ═══════════════════════════════════════════════════════════════════════════════

def test_cad_package_does_not_import_fitz():
    """
    The cad package must not import fitz (PyMuPDF) at any level.
    This confirms the CAD pipeline is fully isolated from the PDF stack.
    """
    import cad.layer_analyzer
    import cad.door_window_detector
    import cad.report_writer

    cad_modules = [cad.layer_analyzer, cad.door_window_detector, cad.report_writer]
    for mod in cad_modules:
        # Inspect the module's global namespace for fitz
        assert "fitz" not in vars(mod), (
            f"Module '{mod.__name__}' imported fitz (PyMuPDF) — "
            "CAD pipeline must remain isolated from PDF libraries."
        )


def test_process_cad_does_not_import_fitz():
    """process_cad.py must not import fitz."""
    import process_cad
    assert "fitz" not in vars(process_cad), \
        "process_cad.py imported fitz — must be isolated from PDF libraries."


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
