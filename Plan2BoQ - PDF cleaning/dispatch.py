#!/usr/bin/env python3
"""
dispatch.py — Unified file-type dispatcher
==========================================
Scans the unprocessed/ folder and routes files to the correct pipeline:

    .pdf           →  process_floor_plans.py  (existing PDF pipeline, UNTOUCHED)
    .dxf / .dwg    →  process_cad.py           (new CAD pipeline, isolated)

Each pipeline is launched as a SEPARATE SUBPROCESS so that:
  - A failure in the CAD pipeline can never affect the PDF run
  - Import-time errors in either script are fully contained
  - Both pipelines can be run independently at any time without this dispatcher

Usage:
    python3 dispatch.py              # process everything in unprocessed/
    python3 dispatch.py --pdf-only   # only run PDF pipeline
    python3 dispatch.py --cad-only   # only run CAD pipeline

PDF pipeline contact:  process_floor_plans.py  (DO NOT MODIFY)
CAD pipeline contact:  process_cad.py
"""

import sys
import subprocess
from pathlib import Path


_SCRIPT_DIR = Path(__file__).parent
UNPROCESSED = _SCRIPT_DIR / "unprocessed"

PDF_PIPELINE = _SCRIPT_DIR / "process_floor_plans.py"
CAD_PIPELINE = _SCRIPT_DIR / "process_cad.py"

PDF_EXTS = {".pdf"}
CAD_EXTS = {".dxf", ".dwg"}


# ── File scanner ───────────────────────────────────────────────────────────────

def _scan(exts: set) -> list:
    """Return list of files in unprocessed/ matching the given extensions."""
    if not UNPROCESSED.exists():
        return []
    return [
        p for p in sorted(UNPROCESSED.iterdir())
        if p.is_file() and p.suffix.lower() in exts
    ]


# ── Subprocess runner ──────────────────────────────────────────────────────────

def _run_pipeline(script: Path, label: str) -> int:
    """
    Run a pipeline script in a subprocess.
    Returns the exit code (0 = success).
    The subprocess inherits stdout/stderr so output streams through unchanged.
    """
    print(f"\n{'━' * 75}")
    print(f"  Dispatching: {label}")
    print(f"  Script     : {script}")
    print(f"{'━' * 75}\n")

    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(_SCRIPT_DIR),
    )
    return result.returncode


# ── Main dispatcher ────────────────────────────────────────────────────────────

def dispatch(run_pdf: bool = True, run_cad: bool = True) -> None:
    """
    Detect which file types are waiting in unprocessed/ and launch the
    appropriate pipelines via separate subprocess calls.
    """
    pdf_files = _scan(PDF_EXTS) if run_pdf else []
    cad_files = _scan(CAD_EXTS) if run_cad else []

    print("=" * 75)
    print("Plan2BoQ — DISPATCH")
    print("=" * 75)
    print(f"Unprocessed folder : {UNPROCESSED}")
    print(f"PDF files found    : {len(pdf_files)}")
    print(f"CAD files found    : {len(cad_files)}")

    if not pdf_files and not cad_files:
        print("\n  Nothing to process.  Add files to unprocessed/ and re-run.")
        return

    exit_codes = []

    # ── PDF path ──────────────────────────────────────────────────────────────
    if pdf_files:
        code = _run_pipeline(PDF_PIPELINE, "PDF Pipeline  (process_floor_plans.py)")
        exit_codes.append(("PDF", code))
    elif run_pdf:
        print("\n  No PDF files found — skipping PDF pipeline.")

    # ── CAD path ──────────────────────────────────────────────────────────────
    if cad_files:
        code = _run_pipeline(CAD_PIPELINE, "CAD Pipeline  (process_cad.py)")
        exit_codes.append(("CAD", code))
    elif run_cad:
        print("\n  No DXF/DWG files found — skipping CAD pipeline.")

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print("=" * 75)
    print("DISPATCH COMPLETE")
    print("=" * 75)
    for label, code in exit_codes:
        status = "✓ OK" if code == 0 else f"✗ FAILED (exit {code})"
        print(f"  {label} pipeline: {status}")

    any_failed = any(code != 0 for _, code in exit_codes)
    if any_failed:
        sys.exit(1)


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    args = sys.argv[1:]
    pdf_only = "--pdf-only" in args
    cad_only = "--cad-only" in args

    if pdf_only and cad_only:
        print("Error: --pdf-only and --cad-only are mutually exclusive.")
        sys.exit(1)

    dispatch(
        run_pdf = not cad_only,
        run_cad = not pdf_only,
    )
