"""
cad.door_window_counter
========================
Counts doors and windows from DWG/DXF files by combining:
  1. ARC geometry analysis (door swings ~90 degrees)
  2. TEXT/MTEXT tag scanning (GD01, WD06, etc.)
  3. Merge + deduplication of nearby signals

Produces per-file counts, identified tag IDs, and X/Y coordinates.

No PDF imports. No fitz.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class DoorWindowItem:
    item_id: str          # "GD01", "ARC-14", etc.
    category: str         # "door" | "window"
    source: str           # "text_tag" | "arc_geometry"
    x: float
    y: float
    layer: str
    confidence: str       # "high" | "medium" | "low"


@dataclass
class CountResult:
    file: str
    door_count: int
    window_count: int
    doors: List[DoorWindowItem]
    windows: List[DoorWindowItem]
    is_xref_sheet: bool
    source_files_used: List[str]


# ── Tag patterns ──────────────────────────────────────────────────────────────

_DOOR_TAG = re.compile(
    r'\b(?:GD|FD|SD|DR|DOOR)\s*[-_.]?\s*\d+', re.IGNORECASE,
)
_WINDOW_TAG = re.compile(
    r'\b(?:WD|FW|CW|WIN|WINDOW)\s*[-_.]?\s*\d+', re.IGNORECASE,
)
_DOOR_SHORT = re.compile(r'\bD\d{2,}', re.IGNORECASE)
_WINDOW_SHORT = re.compile(r'\bW\d{2,}', re.IGNORECASE)

# ── Geometry constants ────────────────────────────────────────────────────────

_ARC_SWEEP_MIN_DEG = 70.0
_ARC_SWEEP_MAX_DEG = 110.0
_MAX_COORD = 1e8           # reject coordinates beyond this magnitude
_MAX_RADIUS = 1e6          # reject radii beyond this
_DEDUP_RADIUS = 500.0      # drawing units — merge text tag + ARC within this


# ── Public API ────────────────────────────────────────────────────────────────

def count_from_dwg(path: str | Path, xref_info: Optional[Dict] = None) -> CountResult:
    """
    Count doors and windows from a DWG file.

    Parameters
    ----------
    path       : path to the DWG file
    xref_info  : optional xref analysis dict (from analyze_xrefs)
    """
    path = Path(path)
    path_str = str(path)

    arcs = _count_arcs_dwg(path_str)
    tags = _scan_text_tags_dwg(path_str)

    doors, windows = _merge_and_dedup(arcs, tags)

    is_xref = bool(xref_info and xref_info.get("is_xref_sheet"))

    return CountResult(
        file=path.name,
        door_count=len(doors),
        window_count=len(windows),
        doors=doors,
        windows=windows,
        is_xref_sheet=is_xref,
        source_files_used=[path.name],
    )


def count_from_dxf(path: str | Path) -> CountResult:
    """Count doors and windows from a DXF file via ezdxf."""
    import ezdxf
    path = Path(path)
    doc = ezdxf.readfile(str(path))

    arcs: List[DoorWindowItem] = []
    tags: List[DoorWindowItem] = []
    arc_idx = 0

    for layout in _iter_layouts(doc):
        for entity in layout:
            dxf_type = entity.dxftype()
            layer = entity.dxf.get("layer", "0")

            if dxf_type == "ARC":
                start = getattr(entity.dxf, "start_angle", 0.0)
                end_a = getattr(entity.dxf, "end_angle", 0.0)
                sweep = _sweep_angle(start, end_a)
                if _ARC_SWEEP_MIN_DEG <= sweep <= _ARC_SWEEP_MAX_DEG:
                    cx = entity.dxf.get("center", (0, 0, 0))
                    x, y = float(cx[0]), float(cx[1])
                    if _coord_sane(x, y):
                        arcs.append(DoorWindowItem(
                            item_id=f"ARC-{arc_idx}",
                            category="door",
                            source="arc_geometry",
                            x=round(x, 2), y=round(y, 2),
                            layer=layer,
                            confidence="medium",
                        ))
                        arc_idx += 1

            elif dxf_type in ("TEXT", "MTEXT"):
                text_val = ""
                if dxf_type == "TEXT":
                    text_val = entity.dxf.get("text", "")
                else:
                    text_val = getattr(entity, "text", "") or ""
                text_val = text_val.strip()
                if not text_val:
                    continue

                insert = entity.dxf.get("insert", (0, 0, 0))
                x, y = float(insert[0]), float(insert[1])
                if not _coord_sane(x, y):
                    continue

                items = _match_text_tag(text_val, x, y, layer)
                tags.extend(items)

    doors, windows = _merge_and_dedup(arcs, tags)

    return CountResult(
        file=path.name,
        door_count=len(doors),
        window_count=len(windows),
        doors=doors,
        windows=windows,
        is_xref_sheet=False,
        source_files_used=[path.name],
    )


def write_count_reports(
    result: CountResult,
    output_dir: str | Path,
    base_name: str,
) -> Dict[str, str]:
    """
    Write counting results as JSON and CSV.

    Returns dict with 'json_path' and 'csv_path'.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{base_name}_dw_count.json"
    csv_path = output_dir / f"{base_name}_dw_count.csv"

    _write_count_json(result, json_path)
    _write_count_csv(result, csv_path)

    return {"json_path": str(json_path), "csv_path": str(csv_path)}


# ── ARC counting (DWG raw) ───────────────────────────────────────────────────

def _count_arcs_dwg(path_str: str) -> List[DoorWindowItem]:
    """Extract door-swing ARCs from a DWG via raw decoders."""
    try:
        import ezdwg.raw as raw_mod
        result = raw_mod.decode_line_arc_circle_entities(path_str)
        _, arc_rows, _ = result
    except Exception:
        return []

    items: List[DoorWindowItem] = []
    for idx, arc in enumerate(arc_rows or []):
        if not isinstance(arc, (tuple, list)) or len(arc) < 7:
            continue
        # Tuple format: (handle, cx, cy, cz, radius, start_angle_rad, end_angle_rad)
        try:
            _h, cx, cy, _cz, radius, start_a, end_a = (
                arc[0], float(arc[1]), float(arc[2]), float(arc[3]),
                float(arc[4]), float(arc[5]), float(arc[6]),
            )
        except (TypeError, ValueError):
            continue

        if not _finite(cx) or not _finite(cy) or not _finite(radius):
            continue
        if not _coord_sane(cx, cy) or radius <= 0 or radius > _MAX_RADIUS:
            continue

        sweep = _sweep_angle(math.degrees(start_a), math.degrees(end_a))
        if _ARC_SWEEP_MIN_DEG <= sweep <= _ARC_SWEEP_MAX_DEG:
            items.append(DoorWindowItem(
                item_id=f"ARC-{idx}",
                category="door",
                source="arc_geometry",
                x=round(cx, 2), y=round(cy, 2),
                layer="0",
                confidence="medium",
            ))

    return items


# ── Text tag scanning (DWG raw) ──────────────────────────────────────────────

def _scan_text_tags_dwg(path_str: str) -> List[DoorWindowItem]:
    """Extract door/window text tags from a DWG via raw TEXT decoder."""
    try:
        import ezdwg.raw as raw_mod
        texts = raw_mod.decode_text_entities(path_str)
    except Exception:
        return []

    items: List[DoorWindowItem] = []
    for t in texts or []:
        if not isinstance(t, (tuple, list)) or len(t) < 3:
            continue

        text_val = t[1] if isinstance(t[1], str) else ""
        text_val = text_val.strip()
        if not text_val:
            continue

        # Filter non-printable
        printable = sum(1 for c in text_val if 32 <= ord(c) < 127)
        if printable / max(len(text_val), 1) < 0.8:
            continue

        # Extract insertion point from tuple[2] = (x, y, z)
        coords = t[2]
        if not isinstance(coords, (tuple, list)) or len(coords) < 2:
            continue
        try:
            x, y = float(coords[0]), float(coords[1])
        except (TypeError, ValueError):
            continue

        if not _coord_sane(x, y):
            continue

        matched = _match_text_tag(text_val, x, y, "0")
        items.extend(matched)

    return items


# ── Tag matching ──────────────────────────────────────────────────────────────

def _match_text_tag(
    text: str, x: float, y: float, layer: str
) -> List[DoorWindowItem]:
    """Match a text string against door/window tag patterns."""
    items: List[DoorWindowItem] = []
    seen: set = set()

    for pat, category in [
        (_DOOR_TAG, "door"), (_WINDOW_TAG, "window"),
        (_DOOR_SHORT, "door"), (_WINDOW_SHORT, "window"),
    ]:
        for m in pat.finditer(text):
            tag = m.group(0).strip()
            if tag in seen:
                continue
            seen.add(tag)
            items.append(DoorWindowItem(
                item_id=tag,
                category=category,
                source="text_tag",
                x=round(x, 2), y=round(y, 2),
                layer=layer,
                confidence="high",
            ))

    return items


# ── Merge + dedup ─────────────────────────────────────────────────────────────

def _merge_and_dedup(
    arcs: List[DoorWindowItem],
    tags: List[DoorWindowItem],
) -> Tuple[List[DoorWindowItem], List[DoorWindowItem]]:
    """
    Merge ARC-based and text-tag-based detections.

    If a text tag and an ARC are within _DEDUP_RADIUS drawing units, keep only
    the text tag (higher confidence).  Remaining unmatched ARCs are kept as
    standalone door candidates.
    """
    # Text tags always win — they have explicit IDs
    all_items: List[DoorWindowItem] = list(tags)
    tag_positions = [(t.x, t.y) for t in tags if t.category == "door"]

    for arc in arcs:
        is_dup = False
        for tx, ty in tag_positions:
            dist = math.hypot(arc.x - tx, arc.y - ty)
            if dist < _DEDUP_RADIUS:
                is_dup = True
                break
        if not is_dup:
            all_items.append(arc)

    doors = sorted(
        [i for i in all_items if i.category == "door"],
        key=lambda i: (0 if i.source == "text_tag" else 1, i.item_id),
    )
    windows = sorted(
        [i for i in all_items if i.category == "window"],
        key=lambda i: (0 if i.source == "text_tag" else 1, i.item_id),
    )

    return doors, windows


# ── Report writers ────────────────────────────────────────────────────────────

def _write_count_json(result: CountResult, path: Path) -> None:
    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "pipeline": "Plan2BoQ DWG Door/Window Counter",
        },
        "file": result.file,
        "door_count": result.door_count,
        "window_count": result.window_count,
        "is_xref_sheet": result.is_xref_sheet,
        "source_files_used": result.source_files_used,
        "doors": [asdict(d) for d in result.doors],
        "windows": [asdict(w) for w in result.windows],
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _write_count_csv(result: CountResult, path: Path) -> None:
    all_items = result.doors + result.windows
    lines = ["id,category,source,x,y,layer,confidence"]
    for item in all_items:
        lines.append(
            f"{item.item_id},{item.category},{item.source},"
            f"{item.x},{item.y},{item.layer},{item.confidence}"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# ── Geometry helpers ──────────────────────────────────────────────────────────

def _sweep_angle(start_deg: float, end_deg: float) -> float:
    """Compute positive sweep angle in degrees."""
    if not _finite(start_deg) or not _finite(end_deg):
        return 0.0
    sweep = (end_deg - start_deg) % 360.0
    if sweep == 0.0 and end_deg != start_deg:
        sweep = 360.0
    return sweep


def _finite(v: float) -> bool:
    return math.isfinite(v)


def _coord_sane(x: float, y: float) -> bool:
    return _finite(x) and _finite(y) and abs(x) < _MAX_COORD and abs(y) < _MAX_COORD


def _iter_layouts(doc):
    yield doc.modelspace()
    for layout in doc.layouts:
        if not layout.is_modelspace:
            yield layout
