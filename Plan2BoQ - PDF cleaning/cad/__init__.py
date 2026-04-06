"""
cad — Isolated CAD processing package for DXF/DWG files.

This package is completely independent of the PDF pipeline.
It imports only: ezdxf, os, re, json, pathlib, typing, subprocess.
No fitz / PyMuPDF imports exist anywhere in this package.

Public API
----------
from cad.layer_analyzer      import DXFLayerAnalyzer
from cad.door_window_detector import DoorWindowDetector
from cad.report_writer        import write_reports
"""
