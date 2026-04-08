"""
ml.detector
============
YOLOv8 inference wrapper for floor plan door/window detection.

Loads a trained ``.pt`` model, runs detection on a floor plan image,
and returns structured results with bounding boxes, counts, and
confidence scores.

If no model file exists, all public methods return ``None`` gracefully.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

_DEFAULT_CONF = 0.25
_DEFAULT_IOU = 0.45

# Bounding box colours for annotated output (BGR for OpenCV)
_COLORS = {
    "door": (0, 200, 0),      # green
    "window": (200, 120, 0),   # blue-ish
}


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class Detection:
    class_name: str               # "door" or "window"
    confidence: float             # 0.0 – 1.0
    bbox: tuple                   # (x1, y1, x2, y2) in pixels
    center: tuple                 # (cx, cy) in pixels

    def to_dict(self) -> dict:
        return {
            "class": self.class_name,
            "confidence": round(self.confidence, 4),
            "bbox": [int(v) for v in self.bbox],
            "center": [round(v, 1) for v in self.center],
        }


@dataclass
class DetectionResult:
    file: str
    model: str
    door_count: int
    window_count: int
    detections: List[Detection]

    def to_dict(self) -> dict:
        return {
            "meta": {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "pipeline": "Plan2BoQ YOLOv8 Detector",
                "model": self.model,
            },
            "file": self.file,
            "door_count": self.door_count,
            "window_count": self.window_count,
            "detections": [d.to_dict() for d in self.detections],
        }


# ── Detector class ────────────────────────────────────────────────────────────

class FloorPlanDetector:
    """
    YOLOv8-based floor plan door/window detector.

    Usage::

        detector = FloorPlanDetector(Path("ml/best.pt"))
        if detector.is_ready():
            result = detector.detect(Path("cleaned/plan.png"))
            print(result.door_count, result.window_count)
    """

    def __init__(self, model_path: str | Path):
        self._model_path = Path(model_path)
        self._model = None

        if self._model_path.exists():
            try:
                from ultralytics import YOLO
                self._model = YOLO(str(self._model_path))
            except Exception as exc:
                print(f"  [ML] Failed to load model: {exc}")
                self._model = None

    def is_ready(self) -> bool:
        """Return True if a model was loaded successfully."""
        return self._model is not None

    def detect(
        self,
        image_path: str | Path,
        conf_threshold: float = _DEFAULT_CONF,
        iou_threshold: float = _DEFAULT_IOU,
    ) -> Optional[DetectionResult]:
        """
        Run inference on a single floor plan image.

        Returns None if the model is not loaded.
        """
        if not self.is_ready():
            return None

        image_path = Path(image_path)
        results = self._model.predict(
            source=str(image_path),
            conf=conf_threshold,
            iou=iou_threshold,
            verbose=False,
        )

        detections: List[Detection] = []
        for r in results:
            boxes = r.boxes
            if boxes is None:
                continue
            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                x1, y1, x2, y2 = boxes.xyxy[i].tolist()
                class_name = self._model.names.get(cls_id, f"class_{cls_id}")

                detections.append(Detection(
                    class_name=class_name,
                    confidence=conf,
                    bbox=(x1, y1, x2, y2),
                    center=((x1 + x2) / 2, (y1 + y2) / 2),
                ))

        doors = [d for d in detections if d.class_name == "door"]
        windows = [d for d in detections if d.class_name == "window"]

        return DetectionResult(
            file=image_path.name,
            model=self._model_path.name,
            door_count=len(doors),
            window_count=len(windows),
            detections=detections,
        )

    def detect_and_annotate(
        self,
        image_path: str | Path,
        output_path: str | Path,
        conf_threshold: float = _DEFAULT_CONF,
        iou_threshold: float = _DEFAULT_IOU,
    ) -> Optional[DetectionResult]:
        """
        Run inference and save an annotated copy with bounding boxes drawn.

        Returns the DetectionResult, or None if model not loaded.
        """
        result = self.detect(image_path, conf_threshold, iou_threshold)
        if result is None:
            return None

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        _draw_boxes(Path(image_path), output_path, result.detections)
        return result


# ── Report writer ─────────────────────────────────────────────────────────────

def write_detection_report(
    result: DetectionResult,
    output_dir: str | Path,
    base_name: str,
) -> Dict[str, str]:
    """
    Write detection results as JSON.

    Returns dict with 'json_path'.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{base_name}_ml_detect.json"

    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, indent=2, ensure_ascii=False)

    return {"json_path": str(json_path)}


# ── Box drawing ───────────────────────────────────────────────────────────────

def _draw_boxes(
    image_path: Path, output_path: Path, detections: List[Detection],
) -> None:
    """Draw bounding boxes and labels on an image using OpenCV."""
    try:
        import cv2
    except ImportError:
        # Fallback: copy the image without boxes
        import shutil
        shutil.copy2(str(image_path), str(output_path))
        return

    img = cv2.imread(str(image_path))
    if img is None:
        return

    for det in detections:
        x1, y1, x2, y2 = [int(v) for v in det.bbox]
        color = _COLORS.get(det.class_name, (128, 128, 128))

        cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)

        label = f"{det.class_name} {det.confidence:.0%}"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.5
        thickness = 1
        (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness)

        cv2.rectangle(img, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
        cv2.putText(img, label, (x1 + 2, y1 - 4), font, font_scale,
                    (255, 255, 255), thickness, cv2.LINE_AA)

    cv2.imwrite(str(output_path), img)
