#!/usr/bin/env python3
"""
Automated CAD Floor Plan Processing Workflow
=============================================
Monitors the 'unprocessed' folder for DXF/DWG files, analyses them for
door and window layer detection, saves structured reports to 'cleaned',
and archives originals.

This script is COMPLETELY ISOLATED from the PDF pipeline.
It does not import, modify, or interact with:
  - process_floor_plans.py
  - clean_floor_plan.py
  - ml_confidence_scorer.py

Directory Structure (shared with PDF pipeline):
    unprocessed/  ← Place your DXF/DWG files here
    cleaned/      ← Detection reports appear here
    archived/     ← Original CAD files moved here after processing

Usage:
    python3 process_cad.py

DWG support:
    DWG files are processed natively via the ezdwg library (no ODA File
    Converter required).  ezdwg reads the raw layer table and entity data
    directly from the binary DWG format (AC1014–AC1032 / R14–R2018).

    Note: DWG files used in practice are often *xref-heavy* — most geometry
    lives in externally referenced drawings.  In such cases entity counts per
    layer are low but all layer names (including xref layers) are still
    scanned so name-based door/window detection can operate on the full
    layer vocabulary.
"""

import shutil
from datetime import datetime
from pathlib import Path

# CAD-only imports — no PDF libraries imported here
from cad.layer_analyzer import DXFLayerAnalyzer, DWGLayerAnalyzer
from cad.door_window_detector import DoorWindowDetector
from cad.report_writer import write_reports


# ── Folder layout (mirrors process_floor_plans.py convention) ─────────────────

_SCRIPT_DIR   = Path(__file__).parent
UNPROCESSED   = _SCRIPT_DIR / "unprocessed"
CLEANED       = _SCRIPT_DIR / "cleaned"
ARCHIVED      = _SCRIPT_DIR / "archived"

SUPPORTED_EXT = {".dxf", ".dwg"}


# ═══════════════════════════════════════════════════════════════════════════════
# Single-file processor
# ═══════════════════════════════════════════════════════════════════════════════

def process_single_cad(input_path: Path) -> dict:
    """
    Process one DXF/DWG file:
      1. Convert DWG → DXF if needed
      2. Analyse all layers with DXFLayerAnalyzer
      3. Classify door/window layers with DoorWindowDetector
      4. Write JSON + text reports to cleaned/
      5. Move original to archived/

    Returns a summary dict.
    """
    filename  = input_path.name
    base_name = input_path.stem
    ext       = input_path.suffix.lower()

    print(f"\n{'─' * 75}")
    print(f"Processing: {filename}")
    print('─' * 75)

    try:
        # ── Step 1 & 2: Layer analysis ────────────────────────────────────────
        if ext == ".dwg":
            print("  → Reading DWG layers natively via ezdwg ...")
            analyzer = DWGLayerAnalyzer.from_file(input_path)
        else:
            print("  → Analysing DXF layers ...")
            analyzer = DXFLayerAnalyzer.from_file(input_path)

        profiles    = analyzer.analyze()
        layer_count = len(profiles)

        # ── Step 3: Door/window detection ─────────────────────────────────────
        print("  → Detecting door and window layers ...")
        detector  = DoorWindowDetector(profiles)
        results   = detector.classify_all()

        summary   = results["summary"]
        door_count   = summary["door_count"]
        window_count = summary["window_count"]

        # ── Step 4: Write reports ─────────────────────────────────────────────
        print("  → Writing detection reports ...")
        report_paths = write_reports(
            results      = results,
            source_file  = str(input_path),
            output_dir   = str(CLEANED),
            base_name    = base_name,
        )

        # ── Step 5: Archive original ──────────────────────────────────────────
        archived_path = ARCHIVED / filename
        print(f"  → Archiving original to archived/ ...")
        shutil.move(str(input_path), str(archived_path))

        print(f"\n  Summary:")
        print(f"    Layers scanned       : {layer_count}")
        print(f"    Door layers found    : {door_count}")
        print(f"    Window layers found  : {window_count}")

        if results["door_layers"]:
            print("    Top door layer(s)    :", end="")
            for r in results["door_layers"][:3]:
                print(f" {r.layer} ({r.confidence:.0%})", end="")
            print()

        if results["window_layers"]:
            print("    Top window layer(s)  :", end="")
            for r in results["window_layers"][:3]:
                print(f" {r.layer} ({r.confidence:.0%})", end="")
            print()

        print(f"\n  ✓ Success")
        print(f"    JSON report : {report_paths['json_path']}")
        print(f"    Text report : {report_paths['txt_path']}")
        print(f"    CSV scores  : {report_paths.get('csv_path', 'n/a')}")
        print(f"    Archived    : {archived_path}")

        return {
            "status":        "success",
            "filename":      filename,
            "layer_count":   layer_count,
            "door_count":    door_count,
            "window_count":  window_count,
            "door_layers":   [r.layer for r in results["door_layers"]],
            "window_layers": [r.layer for r in results["window_layers"]],
            "json_report":   report_paths["json_path"],
            "txt_report":    report_paths["txt_path"],
            "archived_path": str(archived_path),
            "error":         None,
        }

    except Exception as exc:
        print(f"\n  ✗ Error: {exc}")
        return {
            "status":   "error",
            "filename": filename,
            "error":    str(exc),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Batch processor
# ═══════════════════════════════════════════════════════════════════════════════

def process_all_cad() -> None:
    """
    Scan unprocessed/ for DXF/DWG files and process each one.
    Writes a cad_processing_report.txt at the end.
    """
    _ensure_dirs()

    cad_files = sorted(
        p for p in UNPROCESSED.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXT
    )

    _print_header(len(cad_files))

    if not cad_files:
        print("  No DXF/DWG files found in unprocessed/")
        print("\n  Place .dxf (or .dwg) files in the unprocessed/ folder and re-run.")
        return

    results = []
    for i, path in enumerate(cad_files, start=1):
        print(f"\n[{i}/{len(cad_files)}]")
        r = process_single_cad(path)
        results.append(r)

    _print_footer(results)
    _write_processing_report(results)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    UNPROCESSED.mkdir(parents=True, exist_ok=True)
    CLEANED.mkdir(parents=True, exist_ok=True)
    ARCHIVED.mkdir(parents=True, exist_ok=True)


def _print_header(file_count: int) -> None:
    print("=" * 75)
    print("AUTOMATED CAD FLOOR PLAN PROCESSING")
    print("=" * 75)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Directory Structure:")
    print(f"  Unprocessed : {UNPROCESSED}")
    print(f"  Reports     : {CLEANED}")
    print(f"  Archived    : {ARCHIVED}")
    print()
    if file_count:
        print(f"Found {file_count} CAD file(s) to process:")
        for p in sorted(UNPROCESSED.iterdir()):
            if p.suffix.lower() in SUPPORTED_EXT:
                print(f"  • {p.name}")


def _print_footer(results: list) -> None:
    succeeded = [r for r in results if r.get("status") == "success"]
    skipped   = [r for r in results if r.get("status") == "skipped"]
    failed    = [r for r in results if r.get("status") == "error"]

    total_doors   = sum(r.get("door_count",   0) for r in succeeded)
    total_windows = sum(r.get("window_count", 0) for r in succeeded)

    print()
    print("=" * 75)
    print("PROCESSING COMPLETE")
    print("=" * 75)
    print()
    print("Results:")
    print(f"  ✓ Successful : {len(succeeded)}")
    print(f"  ⊘ Skipped    : {len(skipped)}")
    print(f"  ✗ Failed     : {len(failed)}")
    print()
    if succeeded:
        print(f"  Total door layers detected   : {total_doors}")
        print(f"  Total window layers detected : {total_windows}")
    print()
    print(f"  Reports : {CLEANED}/")
    print(f"  Archived: {ARCHIVED}/")
    print()
    print("=" * 75)


def _write_processing_report(results: list) -> None:
    report_path = _SCRIPT_DIR / "cad_processing_report.txt"
    lines = [
        "Plan2BoQ — CAD Processing Report",
        f"Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "=" * 75,
        "",
    ]
    for r in results:
        lines.append(f"File   : {r['filename']}")
        lines.append(f"Status : {r['status']}")
        if r.get("status") == "success":
            lines.append(f"Layers : {r['layer_count']} total, {r['door_count']} door, {r['window_count']} window")
            if r.get("door_layers"):
                lines.append(f"Door   : {', '.join(r['door_layers'])}")
            if r.get("window_layers"):
                lines.append(f"Window : {', '.join(r['window_layers'])}")
        elif r.get("error"):
            lines.append(f"Error  : {r['error']}")
        elif r.get("reason"):
            lines.append(f"Reason : {r['reason']}")
        lines.append("")

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"  Detailed report: {report_path}")


# ══════════════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    process_all_cad()
