#!/usr/bin/env python3
"""
process_all.py — Unified entry point (forwards to dispatch.py)

Routes PDFs → process_floor_plans.py and DWG/DXF → process_cad.py.

Usage:
    python3 process_all.py
    python3 process_all.py --pdf-only
    python3 process_all.py --cad-only
"""

import subprocess
import sys
from pathlib import Path

if __name__ == "__main__":
    dispatch = Path(__file__).resolve().parent / "dispatch.py"
    raise SystemExit(
        subprocess.call([sys.executable, str(dispatch)] + sys.argv[1:])
    )
