"""
ml -- Machine learning pipeline for floor plan door/window detection.

Uses YOLOv8 object detection on rendered floor plan images.
Completely independent of the rule-based CAD and PDF pipelines.

Public API
----------
from ml.render   import render_to_image
from ml.dataset  import prepare_dataset, collect_images, collect_pdfs
from ml.detector import FloorPlanDetector
# CLI: python3 ml/build_dataset_from_unprocessed.py
"""
