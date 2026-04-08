"""
cad — Isolated CAD processing package for DXF/DWG files.

This package is completely independent of the PDF pipeline.
It imports only: ezdxf, os, re, json, pathlib, typing, subprocess.
No fitz / PyMuPDF imports exist anywhere in this package.

Public API
----------
from cad.layer_analyzer        import DXFLayerAnalyzer
from cad.door_window_detector  import DoorWindowDetector
from cad.report_writer         import write_reports
from cad.xref_resolver         import analyze_xrefs
from cad.door_window_counter   import count_from_dwg, count_from_dxf, write_count_reports
"""
