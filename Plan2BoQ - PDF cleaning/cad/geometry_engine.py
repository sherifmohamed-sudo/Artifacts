"""
cad.geometry_engine
===================
Geometric feature extraction and scoring using shapely + numpy.

This module operates on coordinate data already extracted into LayerProfile
by DXFLayerAnalyzer.  It is the second tier of the detection pipeline:

    Tier 1 (name / block / entity-type counts)  →  door_window_detector.py
    Tier 2 (actual geometry: radii, angles, shapes)  →  geometry_engine.py

No PDF imports. No fitz.
Depends on: shapely, numpy, stdlib.

Key heuristics
--------------
Door swing arcs
    • Sweep angle ≈ 90 ° (75 °–105 °) is the strongest indicator.
    • Radius in the range 200–1 600 (drawing-unit–independent detection via
      a scale estimator) covers standard single and double doors.
    • Consistency: if > 50 % of the layer's arcs share similar radius, the
      layer is likely a door collection layer.

Window rectangles
    • A closed LWPOLYLINE whose bounding-box aspect ratio ≥ 2.5 : 1 resembles
      a window opening (typically narrow and wide in plan view).
    • If ≥ 40 % of the layer's closed polylines are window-shaped, the layer
      scores as a window candidate.

Unit handling
    • Architectural CAD drawings use mm, cm, or m.  We use a data-driven
      scale estimator: the median arc radius from a populated layer is used to
      normalise to a canonical "mm-equivalent" space.  Comparisons are made
      against normalised ranges so the scorer is unit-agnostic.
"""

from __future__ import annotations

import math
from typing import List, Tuple

import numpy as np

# Shapely is imported lazily so the module can be imported even if shapely is
# not installed (scoring will simply skip the geometry signals).
try:
    from shapely.geometry import Point, LineString, Polygon
    _SHAPELY_AVAILABLE = True
except ImportError:
    _SHAPELY_AVAILABLE = False


# ── Public type aliases ────────────────────────────────────────────────────────

# An arc is stored as (center_x, center_y, radius, start_angle_deg, end_angle_deg)
ArcTuple  = Tuple[float, float, float, float, float]
# A polyline shape is stored as (width, height) of the entity's bounding box
ShapeTuple = Tuple[float, float]


# ── Score budget (these come out of the geometry_ratio signal in the detector) ─

MAX_ARC_SCORE  = 20
MAX_POLY_SCORE = 20


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def score_arc_geometry(
    arc_geometries: List[ArcTuple],
) -> Tuple[int, int, str]:
    """
    Analyse arc geometry for door-swing characteristics.

    Parameters
    ----------
    arc_geometries : list of (cx, cy, radius, start_angle_deg, end_angle_deg)

    Returns
    -------
    (door_score, window_score, explanation_note)
    """
    if not arc_geometries:
        return 0, 0, ""

    radii: List[float]  = []
    sweeps: List[float] = []

    for (_, _, radius, start_a, end_a) in arc_geometries:
        if not _finite(radius) or radius <= 0:
            continue
        radii.append(radius)
        sweep = _sweep_angle(start_a, end_a)
        sweeps.append(sweep)

    if not radii:
        return 0, 0, ""

    radii_arr  = np.array(radii,  dtype=float)
    sweeps_arr = np.array(sweeps, dtype=float)
    n = len(radii_arr)

    # ── Sweep angle analysis (unit-independent) ───────────────────────────────
    # Quarter-circle (80°–100°) is the canonical door-swing shape
    quarter_hits = int(np.sum((sweeps_arr >= 75) & (sweeps_arr <= 105)))
    half_hits    = int(np.sum((sweeps_arr >= 165) & (sweeps_arr <= 195)))
    sweep_hits   = quarter_hits + half_hits
    sweep_ratio  = sweep_hits / n

    # ── Radius consistency (unit-independent) ─────────────────────────────────
    # If > 50 % of arcs share a similar radius (within ±30 %), this layer is a
    # door-collection layer even if the absolute values are unknown.
    median_r = float(np.median(radii_arr))
    if median_r > 0:
        similar_r = int(np.sum(np.abs(radii_arr - median_r) / median_r < 0.30))
        radius_consistency = similar_r / n
    else:
        radius_consistency = 0.0

    # ── Radius range check (with unit estimation) ─────────────────────────────
    # Estimate drawing units from median radius:
    #   mm-scale: median_r in [200, 2000]
    #   cm-scale: median_r in [20, 200]   → multiply × 10
    #   m-scale:  median_r in [0.2, 2.0]  → multiply × 1000
    norm_r = _normalise_to_mm(radii_arr, median_r)
    door_range_hits = int(np.sum((norm_r >= 200) & (norm_r <= 1600)))
    door_range_ratio = door_range_hits / n

    # ── Score ─────────────────────────────────────────────────────────────────
    door_score = 0
    notes: List[str] = []

    if sweep_ratio >= 0.40:
        pts = min(int(sweep_ratio * 12), 12)
        door_score += pts
        notes.append(
            f"Geometry: {sweep_hits}/{n} arcs have door-swing sweep angle (~90°, +{pts} pts)."
        )

    if radius_consistency >= 0.50:
        pts = min(int(radius_consistency * 8), 8)
        door_score += pts
        notes.append(
            f"Geometry: arc radii are consistent (±30%), suggesting a symbol collection (+{pts} pts)."
        )

    if door_range_ratio >= 0.50:
        pts = min(int(door_range_ratio * 6), 6)
        door_score += pts
        notes.append(
            f"Geometry: {door_range_hits}/{n} arc radii in door-width range 200–1600mm (+{pts} pts)."
        )

    return min(door_score, MAX_ARC_SCORE), 0, " ".join(notes)


def score_polyline_geometry(
    polyline_shapes: List[ShapeTuple],
) -> Tuple[int, int, str]:
    """
    Analyse closed polyline bounding-box shapes for window-like geometry.

    Parameters
    ----------
    polyline_shapes : list of (width, height) bounding-box sizes

    Returns
    -------
    (door_score, window_score, explanation_note)
    """
    if not polyline_shapes:
        return 0, 0, ""

    ratios: List[float] = []
    for (w, h) in polyline_shapes:
        if not (_finite(w) and _finite(h)) or min(w, h) <= 0:
            continue
        ratio = max(w, h) / min(w, h)  # always ≥ 1
        ratios.append(ratio)

    if not ratios:
        return 0, 0, ""

    ratios_arr = np.array(ratios, dtype=float)
    n = len(ratios_arr)

    # ── Window: elongated rectangle (aspect ratio ≥ 2.5) ─────────────────────
    window_hits  = int(np.sum(ratios_arr >= 2.5))
    window_ratio = window_hits / n

    # ── Door: square-ish (aspect ratio < 1.5) or with arc overlap already ────
    square_hits  = int(np.sum(ratios_arr < 1.5))
    square_ratio = square_hits / n

    window_score = 0
    door_score   = 0
    notes: List[str] = []

    if window_ratio >= 0.40:
        pts = min(int(window_ratio * 14), 14)
        window_score += pts
        notes.append(
            f"Geometry: {window_hits}/{n} closed polylines have window aspect ratio (≥2.5:1, +{pts} pts)."
        )
    elif square_ratio >= 0.60 and n >= 3:
        # Square-ish + multiple entities often matches door opening rectangles
        pts = min(int(square_ratio * 6), 6)
        door_score += pts
        notes.append(
            f"Geometry: {square_hits}/{n} closed polylines are square-ish (door opening, +{pts} pts)."
        )

    return (
        min(door_score,   MAX_POLY_SCORE),
        min(window_score, MAX_POLY_SCORE),
        " ".join(notes),
    )


def extract_arc_geometry(entity) -> ArcTuple | None:
    """
    Extract (cx, cy, radius, start_angle_deg, end_angle_deg) from an
    ezdxf ARC entity.  Returns None if the entity is degenerate.
    """
    try:
        cx = float(entity.dxf.center.x)
        cy = float(entity.dxf.center.y)
        r  = float(entity.dxf.radius)
        sa = float(entity.dxf.start_angle)
        ea = float(entity.dxf.end_angle)
        if not all(_finite(v) for v in (cx, cy, r, sa, ea)):
            return None
        if r <= 0:
            return None
        return (cx, cy, r, sa, ea)
    except Exception:
        return None


def extract_polyline_shape(entity) -> ShapeTuple | None:
    """
    Extract (width, height) bounding box from a closed ezdxf LWPOLYLINE.
    Returns None if the polyline is open, degenerate, or extraction fails.
    """
    try:
        if not entity.closed:
            return None
        pts = list(entity.get_points())  # list of (x, y, bulge, start_w, end_w)
        if len(pts) < 3:
            return None
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        if not all(_finite(v) for v in xs + ys):
            return None
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if min(w, h) <= 0:
            return None
        return (w, h)
    except Exception:
        return None


# ── Private helpers ───────────────────────────────────────────────────────────

def _sweep_angle(start_deg: float, end_deg: float) -> float:
    """Return the positive sweep angle (0°–360°) from start to end."""
    sweep = (end_deg - start_deg) % 360.0
    if sweep <= 0:
        sweep += 360.0
    return sweep


def _finite(v: float) -> bool:
    """Return True if v is a finite real number."""
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _normalise_to_mm(radii: "np.ndarray", median_r: float) -> "np.ndarray":
    """
    Scale radius values to millimetre-equivalent space using a heuristic
    based on the median radius value.

      median_r ≥ 100  → assume already in mm   (scale × 1)
      5 ≤ median_r < 100  → assume cm           (scale × 10)
      median_r < 5    → assume m                (scale × 1000)
    """
    if median_r >= 100:
        return radii            # mm
    elif median_r >= 5:
        return radii * 10.0    # cm → mm
    else:
        return radii * 1000.0  # m → mm
