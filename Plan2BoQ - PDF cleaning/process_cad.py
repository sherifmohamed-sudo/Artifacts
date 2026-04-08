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
from cad.xref_resolver import analyze_xrefs
from cad.door_window_counter import count_from_dwg, count_from_dxf, write_count_reports


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

        # ── Step 3: Xref analysis ─────────────────────────────────────────────
        xref_info = analyze_xrefs(
            profiles, input_path,
            search_dirs=[UNPROCESSED, input_path.parent],
        )

        if xref_info.is_xref_sheet:
            print(f"  ⚠ Xref sheet detected  ({xref_info.total_entities} entities, "
                  f"{xref_info.empty_layer_pct:.0%} empty layers)")
            if xref_info.xref_base_names:
                print(f"    Referenced sources: {', '.join(xref_info.xref_base_names[:5])}")
            if xref_info.source_files_found:
                print(f"    ✓ Found on disk: {[p.name for p in xref_info.source_files_found]}")
            if xref_info.source_files_missing:
                print(f"    ✗ Missing files: {xref_info.source_files_missing}")

        # ── Step 4: Door/window detection ─────────────────────────────────────
        print("  → Detecting door and window layers ...")
        detector  = DoorWindowDetector(profiles)
        results   = detector.classify_all()

        # Attach xref metadata to results for report generation
        results["xref"] = {
            "is_xref_sheet":        xref_info.is_xref_sheet,
            "source_files_found":   [str(p) for p in xref_info.source_files_found],
            "source_files_missing": xref_info.source_files_missing,
            "xref_base_names":      xref_info.xref_base_names,
            "total_entities":       xref_info.total_entities,
            "empty_layer_pct":      xref_info.empty_layer_pct,
        }

        summary   = results["summary"]
        door_count   = summary["door_count"]
        window_count = summary["window_count"]

        # ── Step 5: Write reports ─────────────────────────────────────────────
        print("  → Writing detection reports ...")
        report_paths = write_reports(
            results      = results,
            source_file  = str(input_path),
            output_dir   = str(CLEANED),
            base_name    = base_name,
        )

        # ── Step 6: Door/window counting ───────────────────────────────────────
        print("  → Counting doors and windows ...")
        xref_dict = {
            "is_xref_sheet":        xref_info.is_xref_sheet,
            "source_files_found":   [str(p) for p in xref_info.source_files_found],
            "source_files_missing": xref_info.source_files_missing,
        }
        if ext == ".dwg":
            count_result = count_from_dwg(input_path, xref_info=xref_dict)
        else:
            count_result = count_from_dxf(input_path)

        count_paths = write_count_reports(count_result, str(CLEANED), base_name)

        print(f"    Doors  detected : {count_result.door_count}")
        print(f"    Windows detected: {count_result.window_count}")
        if count_result.doors:
            tags = [d.item_id for d in count_result.doors[:8]]
            print(f"    Door tags       : {', '.join(tags)}"
                  f"{'...' if len(count_result.doors) > 8 else ''}")
        if count_result.windows:
            tags = [w.item_id for w in count_result.windows[:8]]
            print(f"    Window tags     : {', '.join(tags)}"
                  f"{'...' if len(count_result.windows) > 8 else ''}")

        # ── Step 7: ML detection (optional) ───────────────────────────────────
        ml_result_dict = {}
        _ml_model_path = _SCRIPT_DIR / "ml" / "best.pt"
        if _ml_model_path.exists():
            try:
                from ml.render import render_to_image
                from ml.detector import FloorPlanDetector, write_detection_report

                render_png = CLEANED / f"{base_name}_render.png"
                render_to_image(input_path, render_png)

                detector = FloorPlanDetector(_ml_model_path)
                if detector.is_ready():
                    print("  → Running ML door/window detection ...")
                    annotated_png = CLEANED / f"{base_name}_ml_annotated.png"
                    ml_result = detector.detect_and_annotate(render_png, annotated_png)
                    if ml_result:
                        ml_paths = write_detection_report(ml_result, str(CLEANED), base_name)
                        ml_result_dict = {
                            "ml_door_count": ml_result.door_count,
                            "ml_window_count": ml_result.window_count,
                            "ml_json": ml_paths["json_path"],
                            "ml_annotated": str(annotated_png),
                        }
                        print(f"    ML doors  : {ml_result.door_count}")
                        print(f"    ML windows: {ml_result.window_count}")
            except ImportError:
                pass

        # ── Step 8: Archive original ──────────────────────────────────────────
        archived_path = ARCHIVED / filename
        print(f"  → Archiving original to archived/ ...")
        shutil.move(str(input_path), str(archived_path))

        print(f"\n  Summary:")
        print(f"    Layers scanned       : {layer_count}")
        print(f"    Door layers found    : {door_count}")
        print(f"    Window layers found  : {window_count}")
        print(f"    Door items counted   : {count_result.door_count}")
        print(f"    Window items counted : {count_result.window_count}")

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
        print(f"    JSON report   : {report_paths['json_path']}")
        print(f"    Text report   : {report_paths['txt_path']}")
        print(f"    CSV scores    : {report_paths.get('csv_path', 'n/a')}")
        print(f"    Count JSON    : {count_paths['json_path']}")
        print(f"    Count CSV     : {count_paths['csv_path']}")
        print(f"    Archived      : {archived_path}")

        result_dict = {
            "status":              "success",
            "filename":            filename,
            "layer_count":         layer_count,
            "door_count":          door_count,
            "window_count":        window_count,
            "door_layers":         [r.layer for r in results["door_layers"]],
            "window_layers":       [r.layer for r in results["window_layers"]],
            "json_report":         report_paths["json_path"],
            "txt_report":          report_paths["txt_path"],
            "count_json":          count_paths["json_path"],
            "count_csv":           count_paths["csv_path"],
            "door_items_counted":  count_result.door_count,
            "window_items_counted":count_result.window_count,
            "archived_path":       str(archived_path),
            "xref_info":           xref_info._asdict(),
            "error":               None,
        }
        result_dict.update(ml_result_dict)
        return result_dict

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
    processed_stems: set = set()
    queue = list(cad_files)
    file_idx = 0

    while file_idx < len(queue):
        path = queue[file_idx]
        file_idx += 1

        if path.stem in processed_stems:
            continue
        processed_stems.add(path.stem)

        print(f"\n[{len(results)+1}/{len(queue)}]")
        r = process_single_cad(path)
        results.append(r)

        # Auto-queue xref source files discovered on disk
        xref = r.get("xref_info") or {}
        for src_path_str in (xref.get("source_files_found") or []):
            src_path = Path(src_path_str)
            if src_path.is_file() and src_path.stem not in processed_stems:
                print(f"  → Auto-queuing xref source: {src_path.name}")
                queue.append(src_path)

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
