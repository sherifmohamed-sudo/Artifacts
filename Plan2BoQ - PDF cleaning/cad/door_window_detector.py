"""
cad.door_window_detector
========================
Multi-strategy scoring engine that classifies DXF/DWG layers as likely
door layers, window layers, or unrelated — without assuming any standard
naming convention.

Five independent signals are evaluated and combined into a single score.
Every decision includes a human-readable reason explaining which signals
fired and why.

Signal overview
---------------
  1  Layer name keyword matching (multilingual) .............. max 40 pts
  2  Block (INSERT) name patterns ............................ max 40 pts
  3  Entity type composition (arc/insert/line counts) ........ max 30 pts
  4  Count-based geometry ratios ............................. max 10 pts
  5  Coordinate geometry (arc sweep angles, polyline shapes).. max 10 pts
     (requires ezdxf geometry extraction; upgrades ratio scoring)
                                                               ---------
  Total ....................................................... max 130 pts

No PDF imports. No fitz.
Depends on: re, stdlib, cad.layer_analyzer, cad.geometry_engine.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

from cad.layer_analyzer import LayerProfile


# ═══════════════════════════════════════════════════════════════════════════════
# Signal 1 — Layer name keyword matching (multilingual)
# ═══════════════════════════════════════════════════════════════════════════════

# Each entry: (regex_pattern, category, max_score)
# category is "door" or "window"
# Patterns are applied case-insensitively to the layer name.
_NAME_PATTERNS: List[Tuple[re.Pattern, str, int]] = []

def _np(pattern: str, category: str, score: int) -> None:
    _NAME_PATTERNS.append((re.compile(pattern, re.IGNORECASE), category, score))

# ── Door name patterns ────────────────────────────────────────────────────────
_np(r'\bdoor\b',         "door", 40)   # exact word "door"
_np(r'\bdoors\b',        "door", 40)
_np(r'a[_-]door',        "door", 38)   # A-DOOR, A_DOOR
_np(r'a_a_door',         "door", 38)   # A_A_DOOR (common AutoCAD export)
_np(r'd[_-]tag',         "door", 35)   # D-TAG, D_TAG
_np(r'door[_-]tag',      "door", 38)
_np(r'\bdr\b',           "door", 25)   # standalone DR
_np(r'^dr[_-]',          "door", 30)   # prefix DR-
_np(r'[_-]dr[_-]',       "door", 28)   # middle _DR_
_np(r'\bporte\b',        "door", 38)   # French
_np(r'\bportes\b',       "door", 38)
_np(r'\boutverture\b',   "door", 30)   # French: ouverture
_np(r'\bouverture\b',    "door", 30)
_np(r'\bbab\b',          "door", 25)   # Arabic transliteration
_np(r'\bgd\d',           "door", 32)   # GD01, GD07
_np(r'\bsd\d',           "door", 30)   # SD03
_np(r'\bfd\d',           "door", 28)   # FD02
_np(r'door.*hid',        "door", 35)   # A_A_DOOR_HID (hidden doors)
_np(r'hid.*door',        "door", 35)

# ── Window name patterns ──────────────────────────────────────────────────────
_np(r'\bwindow\b',       "window", 40)
_np(r'\bwindows\b',      "window", 40)
_np(r'\bwind\b',         "window", 35)
_np(r'a[_-]wind',        "window", 38)  # A-WIND, A_WIND
_np(r'wind[_-]tag',      "window", 38)  # WIND-TAG
_np(r'w[_-]tag',         "window", 35)  # W-TAG
_np(r'\bwin\b',          "window", 30)
_np(r'^win[_-]',         "window", 32)
_np(r'\bglaz',           "window", 36)  # GLAZ, GLAZING
_np(r'curtain',          "window", 35)  # curtain wall
_np(r'a[_-]win\b',       "window", 36)  # A-WIN
_np(r'\bfenetre\b',      "window", 38)  # French: fenêtre (ascii)
_np(r'\bfen[eê]tre\b',   "window", 38)
_np(r'\bbaie\b',         "window", 35)  # French: baie vitrée
_np(r'\bvitrage\b',      "window", 35)  # French: vitrage
_np(r'\bnafiza\b',       "window", 25)  # Arabic transliteration
_np(r'\bwd\d',           "window", 32)  # WD06
_np(r'\bcw\d',           "window", 30)  # CW05
_np(r'\bfw\d',           "window", 28)  # FW03


# ═══════════════════════════════════════════════════════════════════════════════
# Signal 2 — Block (INSERT) name patterns
# ═══════════════════════════════════════════════════════════════════════════════

_BLOCK_PATTERNS: List[Tuple[re.Pattern, str, int]] = []

def _bp(pattern: str, category: str, score: int) -> None:
    _BLOCK_PATTERNS.append((re.compile(pattern, re.IGNORECASE), category, score))

# Door block names
_bp(r'^[A-Z]{0,3}D\d',   "door",   40)  # GD01, SD03, FD02, D1
_bp(r'^DOOR',             "door",   40)
_bp(r'^DR[-_]',           "door",   35)
_bp(r'^PORTE',            "door",   38)  # French
_bp(r'^[A-Z]{0,2}SD\d',  "door",   35)  # SD01, ASD01
_bp(r'^[A-Z]{0,2}GD\d',  "door",   38)  # GD07
_bp(r'DOOR',              "door",   30)  # contains DOOR
_bp(r'-DR[-_$]',          "door",   28)

# Window block names
_bp(r'^[A-Z]{0,2}W\d',   "window", 40)  # WD06, CW05, W3
_bp(r'^WIN',              "window", 40)
_bp(r'^GLAZ',             "window", 38)
_bp(r'^FENETRE',          "window", 38)  # French
_bp(r'^WINDOW',           "window", 40)
_bp(r'WINDOW',            "window", 30)
_bp(r'^[A-Z]{0,2}CW\d',  "window", 35)  # CW05
_bp(r'^[A-Z]{0,2}FW\d',  "window", 28)  # FW03


# ═══════════════════════════════════════════════════════════════════════════════
# Score caps per signal (max points each signal can contribute)
# ═══════════════════════════════════════════════════════════════════════════════

MAX_NAME_SCORE    = 40
MAX_BLOCK_SCORE   = 40
MAX_ENTITY_SCORE  = 30
# Signal 4 and Signal 5 share a single 20-point budget.
# When coordinate geometry is available (DXF files) it competes with the
# count-based ratio score and the higher of the two is used.
# When only counts are available (DWG files) Signal 4 provides the full 20 pts.
MAX_RATIO_SCORE    = 20   # count-based ratio (Signal 4)
MAX_GEOMETRY_SCORE = 20   # shapely/numpy coordinate geometry (Signal 5)
_RATIO_GEO_BUDGET  = 20   # shared cap for the combined Signal 4+5 slot
MAX_TOTAL_SCORE   = MAX_NAME_SCORE + MAX_BLOCK_SCORE + MAX_ENTITY_SCORE + _RATIO_GEO_BUDGET  # 130


# ═══════════════════════════════════════════════════════════════════════════════
# Result dataclass
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class LayerClassification:
    """
    The full classification result for one DXF layer.

    Fields
    ------
    layer           : layer name
    type            : "door" | "window" | "uncertain"
    confidence      : 0.0 – 1.0 (score / MAX_TOTAL_SCORE)
    score           : raw score (0 – 130)
    signals         : breakdown per signal (name, blocks, entities, ratio)
    entity_count    : total entities on the layer
    reason          : human-readable explanation (always non-empty)
    """
    layer: str
    type: str                         # door | window | uncertain
    confidence: float
    score: int
    signals: Dict
    entity_count: int
    reason: str

    def to_dict(self) -> Dict:
        return {
            "layer":        self.layer,
            "type":         self.type,
            "confidence":   round(self.confidence, 3),
            "score":        self.score,
            "max_score":    MAX_TOTAL_SCORE,   # 130
            "signals":      self.signals,
            "entity_count": self.entity_count,
            "reason":       self.reason,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# Main detector
# ═══════════════════════════════════════════════════════════════════════════════

# Minimum score to appear in door_layers / window_layers output
CONFIDENCE_THRESHOLD = 0.35   # 35% → score ≥ 46 / 130


class DoorWindowDetector:
    """
    Classify a dict of LayerProfiles (from DXFLayerAnalyzer.analyze()) into
    door layers, window layers, and uncertain layers.

    Usage
    -----
    profiles = DXFLayerAnalyzer.from_file("plan.dxf").analyze()
    detector = DoorWindowDetector(profiles)
    results  = detector.classify_all()

    print(results["door_layers"])
    print(results["window_layers"])
    """

    def __init__(
        self,
        profiles: Dict[str, LayerProfile],
        threshold: float = CONFIDENCE_THRESHOLD,
    ) -> None:
        self._profiles = profiles
        self._threshold = threshold

    # ── Public API ────────────────────────────────────────────────────────────

    def classify_all(self) -> Dict:
        """
        Run classification on every LayerProfile.

        Returns
        -------
        {
          "door_layers":    [LayerClassification, ...],  sorted by confidence desc
          "window_layers":  [LayerClassification, ...],
          "uncertain":      [LayerClassification, ...],  scored but below threshold
          "all_layers":     [LayerClassification, ...],  every layer, sorted by score desc
          "summary": {
            "total_layers": int,
            "door_count":   int,
            "window_count": int,
            "uncertain_count": int,
            "threshold":    float,
          }
        }
        """
        all_results: List[LayerClassification] = []
        for name, profile in self._profiles.items():
            result = self._classify_layer(profile)
            all_results.append(result)

        all_results.sort(key=lambda r: r.score, reverse=True)

        door_layers    = [r for r in all_results if r.type == "door"    and r.confidence >= self._threshold]
        window_layers  = [r for r in all_results if r.type == "window"  and r.confidence >= self._threshold]
        uncertain      = [r for r in all_results if r.confidence < self._threshold or r.type == "uncertain"]

        return {
            "door_layers":   door_layers,
            "window_layers": window_layers,
            "uncertain":     uncertain,
            "all_layers":    all_results,
            "summary": {
                "total_layers":    len(all_results),
                "door_count":      len(door_layers),
                "window_count":    len(window_layers),
                "uncertain_count": len(uncertain),
                "threshold":       self._threshold,
            },
        }

    def classify_layer(self, layer_name: str) -> Optional[LayerClassification]:
        """Classify a single layer by name. Returns None if not found."""
        profile = self._profiles.get(layer_name)
        if profile is None:
            return None
        return self._classify_layer(profile)

    # ── Core scoring ──────────────────────────────────────────────────────────

    def _classify_layer(self, profile: LayerProfile) -> LayerClassification:
        """Run all four signals and combine into a LayerClassification."""

        door_score   = 0
        window_score = 0
        reason_parts: List[str] = []
        signals: Dict = {}

        # ── Signal 1: Layer name ──────────────────────────────────────────────
        name_door, name_window, name_matched = self._score_name(profile.name)
        door_score   += name_door
        window_score += name_window

        signals["name_match"] = {
            "door_score":   name_door,
            "window_score": name_window,
            "matched":      name_matched,
        }
        if name_matched:
            reason_parts.append(
                f"Layer name '{profile.name}' matches {name_matched[1]} pattern "
                f"('{name_matched[0]}', +{max(name_door, name_window)} pts)."
            )

        # ── Signal 2: Block names ─────────────────────────────────────────────
        block_door, block_window, block_samples = self._score_blocks(profile.block_names)
        door_score   += block_door
        window_score += block_window

        signals["block_names"] = {
            "door_score":   block_door,
            "window_score": block_window,
            "samples":      block_samples,
        }
        if block_samples:
            category = "door" if block_door >= block_window else "window"
            reason_parts.append(
                f"Block names {block_samples} match {category} block pattern "
                f"(+{max(block_door, block_window)} pts)."
            )

        # ── Signal 3: Entity type signature ───────────────────────────────────
        entity_door, entity_window, entity_note = self._score_entity_types(profile)
        door_score   += entity_door
        window_score += entity_window

        signals["entity_types"] = {
            "door_score":   entity_door,
            "window_score": entity_window,
            "counts":       dict(profile.entity_types),
            "arc":          profile.arc_count,
            "insert":       profile.insert_count,
            "line":         profile.line_count,
            "note":         entity_note,
        }
        if entity_note:
            reason_parts.append(entity_note)

        # ── Signals 4 + 5: Ratio + Coordinate geometry (shared 20-pt budget) ────
        # Signal 4: count-based (works for both DXF and DWG)
        ratio_door, ratio_window, ratio_note = self._score_geometry_ratio(profile)

        # Signal 5: shapely/numpy coordinate geometry (DXF only; 0 for DWG)
        geo_door, geo_window, geo_note = self._score_coordinate_geometry(profile)

        # The two signals compete — take the higher score per category,
        # then cap the combined contribution at _RATIO_GEO_BUDGET.
        combined_door   = min(max(ratio_door,   geo_door),   _RATIO_GEO_BUDGET)
        combined_window = min(max(ratio_window, geo_window), _RATIO_GEO_BUDGET)
        door_score   += combined_door
        window_score += combined_window

        signals["geometry_ratio"] = {
            "door_score":   ratio_door,
            "window_score": ratio_window,
            "arc_ratio":    round(profile.arc_ratio, 3),
            "insert_ratio": round(profile.insert_ratio, 3),
            "note":         ratio_note,
        }
        signals["coordinate_geometry"] = {
            "door_score":    geo_door,
            "window_score":  geo_window,
            "arc_count":     len(getattr(profile, "arc_geometries", [])),
            "polyline_count": len(getattr(profile, "polyline_shapes", [])),
            "note":          geo_note,
        }
        combined_note = " | ".join(n for n in (ratio_note, geo_note) if n)
        if combined_note:
            reason_parts.append(combined_note)

        # ── Combine ───────────────────────────────────────────────────────────
        total_score = door_score + window_score

        if door_score > window_score and door_score > 0:
            layer_type = "door"
            final_score = min(door_score, MAX_TOTAL_SCORE)
        elif window_score > door_score and window_score > 0:
            layer_type = "uncertain" if window_score < 15 else "window"
            final_score = min(window_score, MAX_TOTAL_SCORE)
        elif door_score == window_score and door_score > 0:
            layer_type = "uncertain"
            final_score = min(door_score, MAX_TOTAL_SCORE)
        else:
            layer_type = "uncertain"
            final_score = 0

        confidence = final_score / MAX_TOTAL_SCORE

        if not reason_parts:
            reason_parts.append(
                f"No strong door/window signals found for layer '{profile.name}' "
                f"(total score: {final_score}/{MAX_TOTAL_SCORE})."
            )

        reason = " ".join(reason_parts)

        return LayerClassification(
            layer        = profile.name,
            type         = layer_type,
            confidence   = confidence,
            score        = final_score,
            signals      = signals,
            entity_count = profile.entity_count,
            reason       = reason,
        )

    # ── Signal scorers ────────────────────────────────────────────────────────

    def _score_name(self, name: str) -> Tuple[int, int, Optional[Tuple[str, str]]]:
        """
        Returns (door_score, window_score, first_match_or_None).
        first_match is (matched_pattern_string, category).
        Scores are capped at MAX_NAME_SCORE.
        """
        door_score   = 0
        window_score = 0
        first_match  = None

        for pattern, category, pts in _NAME_PATTERNS:
            m = pattern.search(name)
            if m:
                if first_match is None:
                    first_match = (m.group(0), category)
                if category == "door":
                    door_score = max(door_score, pts)
                else:
                    window_score = max(window_score, pts)

        return (
            min(door_score,   MAX_NAME_SCORE),
            min(window_score, MAX_NAME_SCORE),
            first_match,
        )

    def _score_blocks(
        self, block_names: List[str]
    ) -> Tuple[int, int, List[str]]:
        """
        Returns (door_score, window_score, sample_matched_block_names).
        Scores are capped at MAX_BLOCK_SCORE.
        """
        door_score   = 0
        window_score = 0
        matched      : List[str] = []

        for bname in block_names:
            for pattern, category, pts in _BLOCK_PATTERNS:
                if pattern.search(bname):
                    if bname not in matched:
                        matched.append(bname)
                    if category == "door":
                        door_score = max(door_score, pts)
                    else:
                        window_score = max(window_score, pts)
                    break  # one match per block name is enough

        return (
            min(door_score,   MAX_BLOCK_SCORE),
            min(window_score, MAX_BLOCK_SCORE),
            matched[:6],
        )

    def _score_entity_types(
        self, profile: LayerProfile
    ) -> Tuple[int, int, str]:
        """
        Entity-type composition scoring.

        Returns (door_score, window_score, explanation_note).
        """
        arc     = profile.arc_count + profile.entity_types.get("_BLOCK_ARC", 0)
        insert  = profile.insert_count
        line    = profile.line_count + profile.entity_types.get("_BLOCK_LINE", 0)
        total   = profile.entity_count

        if total == 0:
            return 0, 0, ""

        door_score   = 0
        window_score = 0
        notes: List[str] = []

        # Arcs strongly suggest door swings
        if arc > 0:
            pts = min(int(30 * min(arc / max(total, 1) * 3, 1)), MAX_ENTITY_SCORE)
            door_score += pts
            notes.append(f"Layer has {arc} ARC entity/entities (door-swing signal, +{pts} pts).")

        # High INSERT without arcs → more window-like (block-only, rectangular)
        if insert > 0 and arc == 0:
            pts = min(int(20 * min(insert / max(total, 1) * 2, 1)), MAX_ENTITY_SCORE)
            window_score += pts
            notes.append(f"Layer has {insert} INSERT entity/entities with no ARCs (window-block signal, +{pts} pts).")
        elif insert > 0:
            door_score += 5
            notes.append(f"Layer has {insert} INSERT entity/entities alongside ARCs.")

        return (
            min(door_score,   MAX_ENTITY_SCORE),
            min(window_score, MAX_ENTITY_SCORE),
            " ".join(notes),
        )

    def _score_geometry_ratio(
        self, profile: LayerProfile
    ) -> Tuple[int, int, str]:
        """
        Count-based geometry ratio scoring (Signal 4).

        Uses entity counts only — no coordinate data.
        This signal fires even for DWG files where we only have counts.

        Returns (door_score, window_score, explanation_note).
        """
        if profile.entity_count == 0:
            return 0, 0, ""

        arc_ratio    = profile.arc_ratio
        insert_ratio = profile.insert_ratio

        door_score   = 0
        window_score = 0
        notes: List[str] = []

        if arc_ratio >= 0.30:
            pts = min(int(arc_ratio * MAX_RATIO_SCORE * 2.5), MAX_RATIO_SCORE)
            door_score += pts
            notes.append(
                f"High ARC ratio ({arc_ratio:.0%}) strongly indicates door-swing geometry (+{pts} pts)."
            )
        elif arc_ratio >= 0.10:
            pts = min(int(arc_ratio * MAX_RATIO_SCORE * 1.5), MAX_RATIO_SCORE)
            door_score += pts
            notes.append(
                f"Moderate ARC ratio ({arc_ratio:.0%}) suggests door geometry (+{pts} pts)."
            )

        if insert_ratio >= 0.60 and arc_ratio < 0.10:
            pts = min(int(insert_ratio * MAX_RATIO_SCORE), MAX_RATIO_SCORE)
            window_score += pts
            notes.append(
                f"High INSERT ratio ({insert_ratio:.0%}) with no ARCs suggests window block insertions (+{pts} pts)."
            )

        return (
            min(door_score,   MAX_RATIO_SCORE),
            min(window_score, MAX_RATIO_SCORE),
            " ".join(notes),
        )

    def _score_coordinate_geometry(
        self, profile: LayerProfile
    ) -> Tuple[int, int, str]:
        """
        Coordinate geometry scoring using shapely + numpy (Signal 5).

        Activates only when LayerProfile contains actual geometry data
        (populated by DXFLayerAnalyzer).  For DWG files or empty layers
        this returns (0, 0, '') without error.

        Returns (door_score, window_score, explanation_note).
        """
        arc_geos  = getattr(profile, "arc_geometries",  [])
        poly_shps = getattr(profile, "polyline_shapes", [])

        if not arc_geos and not poly_shps:
            return 0, 0, ""

        try:
            from cad.geometry_engine import score_arc_geometry, score_polyline_geometry
        except ImportError:
            return 0, 0, ""

        arc_door, _,         arc_note  = score_arc_geometry(arc_geos)
        poly_door, poly_win, poly_note = score_polyline_geometry(poly_shps)

        door_score   = min(arc_door  + poly_door, MAX_GEOMETRY_SCORE)
        window_score = min(poly_win,              MAX_GEOMETRY_SCORE)
        notes = [n for n in (arc_note, poly_note) if n]

        return door_score, window_score, " ".join(notes)
