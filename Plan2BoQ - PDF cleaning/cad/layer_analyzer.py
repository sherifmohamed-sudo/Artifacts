"""
cad.layer_analyzer
==================
Scans every layer in a DXF/DWG document and collects the raw signals needed
by the door/window detector.

No PDF imports. No fitz.
  • DXF  → uses ezdxf
  • DWG  → uses ezdwg raw API (no ODA File Converter required)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict, Counter

import ezdxf
from ezdxf.document import Drawing
from ezdxf.layouts import BaseLayout


# ── Public data structures ────────────────────────────────────────────────────

class LayerProfile:
    """
    All raw data collected for one DXF layer.

    Attributes
    ----------
    name            : layer name as stored in the DXF
    entity_count    : total entities on this layer
    entity_types    : Counter of DXF entity type strings (e.g. {"INSERT": 12, "ARC": 5})
    block_names     : list of unique block names referenced by INSERT entities
    has_text        : True if any TEXT / MTEXT entities exist on the layer
    color_index     : ACI color index of the layer (1-255, 0=BYLAYER, 256=BYBLOCK)
    is_frozen       : whether the layer is frozen in the drawing
    is_off          : whether the layer is turned off in the drawing
    """

    __slots__ = (
        "name", "entity_count", "entity_types",
        "block_names", "has_text", "color_index",
        "is_frozen", "is_off",
        "arc_geometries", "polyline_shapes",
    )

    def __init__(self, name: str) -> None:
        self.name: str = name
        self.entity_count: int = 0
        self.entity_types: Counter = Counter()
        self.block_names: List[str] = []
        self.has_text: bool = False
        self.color_index: int = 7
        self.is_frozen: bool = False
        self.is_off: bool = False
        # Tier-2 geometry data (populated by DXFLayerAnalyzer, not DWGLayerAnalyzer)
        # arc_geometries : List of (cx, cy, radius, start_angle_deg, end_angle_deg)
        # polyline_shapes: List of (width, height) bounding boxes of closed LWPOLYLINEs
        self.arc_geometries: List = []
        self.polyline_shapes: List = []

    # Convenience helpers ─────────────────────────────────────────────────────

    @property
    def arc_count(self) -> int:
        return self.entity_types.get("ARC", 0)

    @property
    def insert_count(self) -> int:
        return self.entity_types.get("INSERT", 0)

    @property
    def line_count(self) -> int:
        return self.entity_types.get("LINE", 0) + self.entity_types.get("LWPOLYLINE", 0)

    @property
    def arc_ratio(self) -> float:
        if self.entity_count == 0:
            return 0.0
        return self.arc_count / self.entity_count

    @property
    def insert_ratio(self) -> float:
        if self.entity_count == 0:
            return 0.0
        return self.insert_count / self.entity_count

    def __repr__(self) -> str:
        return (
            f"LayerProfile({self.name!r}, entities={self.entity_count}, "
            f"arcs={self.arc_count}, inserts={self.insert_count}, "
            f"blocks={self.block_names[:3]})"
        )


# ── Main analyzer class ───────────────────────────────────────────────────────

class DXFLayerAnalyzer:
    """
    Reads a DXF file (or accepts an already-loaded ezdxf Drawing) and
    produces a LayerProfile for every layer that contains at least one entity.

    Usage
    -----
    analyzer = DXFLayerAnalyzer.from_file("path/to/drawing.dxf")
    profiles = analyzer.analyze()
    # profiles is a dict[layer_name → LayerProfile]
    """

    def __init__(self, doc: Drawing) -> None:
        self._doc = doc

    # ── Construction ─────────────────────────────────────────────────────────

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "DXFLayerAnalyzer":
        """
        Load a DXF file and return a ready DXFLayerAnalyzer.

        Raises
        ------
        FileNotFoundError  if the file does not exist
        ezdxf.DXFStructureError  if the file is not a valid DXF
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"DXF file not found: {path}")
        doc = ezdxf.readfile(str(path))
        return cls(doc)

    @classmethod
    def from_document(cls, doc: Drawing) -> "DXFLayerAnalyzer":
        """Accept an already-loaded ezdxf document (useful for tests)."""
        return cls(doc)

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, LayerProfile]:
        """
        Scan all model-space and paper-space layouts and return a dict
        mapping layer name → LayerProfile.

        Only layers that own at least one entity are returned.  Layers
        defined in the layer table but with zero entities are skipped.
        """
        profiles: Dict[str, LayerProfile] = {}

        # Seed profiles from the layer table so we capture frozen/off state
        for layer_entry in self._doc.layers:
            name = layer_entry.dxf.name
            p = LayerProfile(name)
            p.color_index = getattr(layer_entry.dxf, "color", 7)
            p.is_frozen = layer_entry.is_frozen()
            p.is_off = not layer_entry.is_on()
            profiles[name] = p

        # Walk all layouts (modelspace + paperspace sheets)
        for layout in self._iter_layouts():
            self._scan_layout(layout, profiles)

        # Return only populated layers (entity_count > 0)
        return {
            name: p
            for name, p in profiles.items()
            if p.entity_count > 0
        }

    def layer_summary(self) -> List[Dict]:
        """
        Convenience method — returns a list of dicts, one per populated layer,
        sorted by entity count descending.  Useful for quick inspection.
        """
        profiles = self.analyze()
        rows = []
        for p in sorted(profiles.values(), key=lambda x: x.entity_count, reverse=True):
            rows.append({
                "layer": p.name,
                "entities": p.entity_count,
                "arcs": p.arc_count,
                "inserts": p.insert_count,
                "lines": p.line_count,
                "blocks": p.block_names[:5],
                "arc_ratio": round(p.arc_ratio, 3),
                "insert_ratio": round(p.insert_ratio, 3),
            })
        return rows

    # ── Private helpers ───────────────────────────────────────────────────────

    def _iter_layouts(self):
        """Yield modelspace and all paperspace layouts."""
        yield self._doc.modelspace()
        for layout in self._doc.layouts:
            if not layout.is_modelspace:
                yield layout

    def _scan_layout(
        self,
        layout: BaseLayout,
        profiles: Dict[str, LayerProfile],
    ) -> None:
        """Walk every entity in a layout and update the matching LayerProfile."""
        from cad.geometry_engine import extract_arc_geometry, extract_polyline_shape

        for entity in layout:
            layer_name = entity.dxf.get("layer", "0")

            # Ensure the layer has a profile (handles layers not in the table)
            if layer_name not in profiles:
                profiles[layer_name] = LayerProfile(layer_name)

            p = profiles[layer_name]
            p.entity_count += 1

            dxf_type = entity.dxftype()
            p.entity_types[dxf_type] += 1

            # Collect block names from INSERT entities
            if dxf_type == "INSERT":
                block_name: Optional[str] = entity.dxf.get("name", None)
                if block_name and block_name not in p.block_names:
                    p.block_names.append(block_name)

            # Flag text presence
            if dxf_type in ("TEXT", "MTEXT", "ATTDEF", "ATTRIB"):
                p.has_text = True

            # ── Tier-2: collect raw geometry ──────────────────────────────────
            if dxf_type == "ARC":
                geo = extract_arc_geometry(entity)
                if geo is not None:
                    p.arc_geometries.append(geo)

            elif dxf_type == "LWPOLYLINE":
                shape = extract_polyline_shape(entity)
                if shape is not None:
                    p.polyline_shapes.append(shape)

            # Recurse into block references to also credit the INSERT's layer
            # for block-internal geometry (optional deep scan)
            if dxf_type == "INSERT":
                self._scan_insert(entity, layer_name, profiles)

    def _scan_insert(self, insert_entity, parent_layer: str, profiles: Dict[str, LayerProfile]) -> None:
        """
        Scan geometry inside a block reference and credit arc/line counts to
        the layer of the INSERT entity (not the block-internal layer).

        This is important because AutoCAD doors are often blocks where the
        swing arc is on layer "0" internally, but the INSERT is on A-DOOR.
        We want A-DOOR to show arc presence.
        """
        block_name = insert_entity.dxf.get("name", None)
        if not block_name:
            return
        try:
            block = self._doc.blocks.get(block_name)
        except Exception:
            return
        if block is None:
            return

        p = profiles.get(parent_layer)
        if p is None:
            return

        for entity in block:
            dxf_type = entity.dxftype()
            if dxf_type in ("ARC", "CIRCLE", "ELLIPSE"):
                p.entity_types["_BLOCK_ARC"] = p.entity_types.get("_BLOCK_ARC", 0) + 1
            elif dxf_type in ("LINE", "LWPOLYLINE", "POLYLINE"):
                p.entity_types["_BLOCK_LINE"] = p.entity_types.get("_BLOCK_LINE", 0) + 1


# ── DWG-native analyzer ───────────────────────────────────────────────────────

class DWGLayerAnalyzer:
    """
    Reads a DWG file using the ezdwg raw API — no ODA File Converter needed.

    Key design decisions
    --------------------
    • DWG files used in practice are often *xref-heavy*: most geometry lives
      in externally referenced drawings, and the main file contains only the
      sheet structure.  In these cases the entity count per layer is low but
      ALL 107+ layer names (including xref layers) are still present in the
      layer table.  The DoorWindowDetector's name-based scoring therefore
      provides the most reliable signal.

    • ezdwg 0.8.x `msp.query()` raises ValueError on MTEXT entities whose
      height is NaN (a known bug on some AC1027 files).  We query type-by-type
      with per-entity try/except so a single bad entity cannot abort the scan.

    • INSERT block names are read via `raw.decode_insert_entities()` which is
      crash-safe and gives the block-name string directly.

    • ALL layers (including empty/xref ones) are returned so that name-based
      detection operates on the full layer vocabulary.

    • Some DWGs store layer names in UTF-16; ezdwg may surface them as mojibake.
      Geometry may then appear under a garbled layer name while the readable
      xref-style name (e.g. ``…$0$A_A_DOOR``) exists in the table with zero
      entities — both are listed in reports for manual cross-check.

    Usage
    -----
    analyzer = DWGLayerAnalyzer.from_file("path/to/drawing.dwg")
    profiles = analyzer.analyze()
    """

    # Entity types to query individually (avoids the MTEXT NaN crash)
    _SAFE_QUERY_TYPES = ("LINE", "ARC", "LWPOLYLINE", "CIRCLE", "ELLIPSE", "POINT")

    # Files larger than this skip `ezdwg.read()` (full in-memory decode) and use
    # the raw binary decoders instead — avoids multi‑GB RAM spikes / OOM kills
    # on large production DWGs (common on ground-floor plans).
    _LARGE_DWG_BYTES = 4 * 1024 * 1024  # 4 MiB

    def __init__(self, path: str | os.PathLike) -> None:
        self._path = Path(path)

    @classmethod
    def from_file(cls, path: str | os.PathLike) -> "DWGLayerAnalyzer":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"DWG file not found: {path}")
        return cls(path)

    # ── Public API ────────────────────────────────────────────────────────────

    def analyze(self) -> Dict[str, LayerProfile]:
        """
        Return a dict mapping layer_name → LayerProfile.

        Unlike DXFLayerAnalyzer, empty layers are *included* so that
        name-based detection works on xref layers that carry no entities in the
        main file's modelspace.
        """
        import ezdwg
        from ezdwg import raw as raw_mod

        path_str = str(self._path)
        profiles: Dict[str, LayerProfile] = {}

        # ── Step 1: Collect every layer name from the raw layer table ─────────
        try:
            layer_names_raw = raw_mod.decode_layer_names(path_str)
        except Exception:
            layer_names_raw = []

        layer_by_handle: Dict[int, str] = {}
        for lh, name in layer_names_raw:
            layer_by_handle[int(lh)] = str(name)
            if name not in profiles:
                profiles[name] = LayerProfile(name)

        # ── Step 2: Collect block names defined in the file ───────────────────
        all_block_names: List[str] = []
        try:
            block_header_names = raw_mod.decode_block_header_names(path_str)
            for _handle, bname in block_header_names:
                if bname and not bname.startswith("*"):
                    all_block_names.append(bname)
        except Exception:
            pass

        # ── Step 3: Entity counts ─────────────────────────────────────────────
        large = self._path.stat().st_size >= self._LARGE_DWG_BYTES
        if large:
            # Raw decoders + batched layer-handle lookup (no `ezdwg.read`, no
            # `decode_entity_styles` — both can OOM / SIGKILL large DWGs).
            self._analyze_dwg_raw_decoders(path_str, raw_mod, profiles, layer_by_handle)
            self._append_global_block_names(profiles, all_block_names)
        else:
            try:
                doc = ezdwg.read(path_str)
                msp = doc.modelspace()
                self._scan_msp_safe(msp, profiles)
            except Exception:
                self._analyze_dwg_raw_decoders(
                    path_str, raw_mod, profiles, layer_by_handle
                )
                self._append_global_block_names(profiles, all_block_names)
            else:
                # Small file: high-level scan succeeded — add INSERT / block info
                self._collect_insert_blocks(
                    raw_mod, path_str, profiles, all_block_names
                )

        # Return all layers (including empty ones for name-based detection)
        return profiles

    def layer_summary(self) -> List[Dict]:
        """Convenience method — list of dicts sorted by entity count desc."""
        profiles = self.analyze()
        rows = []
        for p in sorted(profiles.values(), key=lambda x: x.entity_count, reverse=True):
            rows.append({
                "layer":        p.name,
                "entities":     p.entity_count,
                "arcs":         p.arc_count,
                "inserts":      p.insert_count,
                "lines":        p.line_count,
                "blocks":       p.block_names[:5],
                "arc_ratio":    round(p.arc_ratio, 3),
                "insert_ratio": round(p.insert_ratio, 3),
            })
        return rows

    # ── Private helpers ───────────────────────────────────────────────────────

    _LAYER_LOOKUP_BATCH = 2500

    def _credit_handle_batch(
        self,
        path_str: str,
        raw_mod,
        profiles: Dict[str, LayerProfile],
        layer_by_handle: Dict[int, str],
        handles: List[int],
        dxf_type: str,
    ) -> None:
        """
        Map many entity handles → layers via ``decode_object_entity_layer_handles``
        in chunks (fast, low RAM — unlike ``decode_entity_styles`` on huge files).
        """
        if not handles:
            return
        batch_n = self._LAYER_LOOKUP_BATCH
        for i in range(0, len(handles), batch_n):
            chunk = handles[i : i + batch_n]
            try:
                pairs = raw_mod.decode_object_entity_layer_handles(path_str, chunk)
            except Exception:
                pairs = []
            lh_map: Dict[int, int] = {}
            for row in pairs or []:
                if not isinstance(row, (tuple, list)) or len(row) < 2:
                    continue
                try:
                    lh_map[int(row[0])] = int(row[1])
                except (TypeError, ValueError):
                    continue
            for h in chunk:
                lh = lh_map.get(h)
                lname = layer_by_handle.get(lh, "0") if lh is not None else "0"
                if lname not in profiles:
                    profiles[lname] = LayerProfile(lname)
                p = profiles[lname]
                p.entity_count += 1
                p.entity_types[dxf_type] += 1

    def _analyze_dwg_raw_decoders(
        self,
        path_str: str,
        raw_mod,
        profiles: Dict[str, LayerProfile],
        layer_by_handle: Dict[int, str],
    ) -> None:
        """
        Walk raw DWG entity decoders and credit each entity to the correct layer.

        Used for large drawings where ``ezdwg.read()`` risks OOM, and as a
        fallback when the high-level reader fails.

        Uses ``decode_line_arc_circle_entities`` (one binary pass for LINE / ARC /
        CIRCLE) plus batched ``decode_object_entity_layer_handles`` — **not**
        ``decode_entity_styles``, which can exhaust memory on multi‑MB DWGs.
        """
        def _ensure(layer_name: str) -> LayerProfile:
            if layer_name not in profiles:
                profiles[layer_name] = LayerProfile(layer_name)
            return profiles[layer_name]

        # ── LINE + ARC + CIRCLE (single decoder pass) ─────────────────────────
        lac = getattr(raw_mod, "decode_line_arc_circle_entities", None)
        if lac is not None:
            try:
                line_rows, arc_rows, circle_rows = lac(path_str)
            except Exception:
                line_rows, arc_rows, circle_rows = [], [], []
            if line_rows:
                hs = []
                for item in line_rows:
                    if isinstance(item, (tuple, list)) and item:
                        try:
                            hs.append(int(item[0]))
                        except (TypeError, ValueError):
                            pass
                self._credit_handle_batch(
                    path_str, raw_mod, profiles, layer_by_handle, hs, "LINE"
                )
            if arc_rows:
                hs = []
                for item in arc_rows:
                    if isinstance(item, (tuple, list)) and item:
                        try:
                            hs.append(int(item[0]))
                        except (TypeError, ValueError):
                            pass
                self._credit_handle_batch(
                    path_str, raw_mod, profiles, layer_by_handle, hs, "ARC"
                )
            if circle_rows:
                hs = []
                for item in circle_rows:
                    if isinstance(item, (tuple, list)) and item:
                        try:
                            hs.append(int(item[0]))
                        except (TypeError, ValueError):
                            pass
                self._credit_handle_batch(
                    path_str, raw_mod, profiles, layer_by_handle, hs, "CIRCLE"
                )
        else:
            # Fallback: separate decoders (extra DWG passes)
            for dxf_type, fn_name in (
                ("LINE", "decode_line_entities"),
                ("ARC", "decode_arc_entities"),
                ("CIRCLE", "decode_circle_entities"),
            ):
                decoder = getattr(raw_mod, fn_name, None)
                if decoder is None:
                    continue
                try:
                    items = decoder(path_str)
                except Exception:
                    continue
                hs = []
                for item in items or []:
                    if isinstance(item, (tuple, list)) and item:
                        try:
                            hs.append(int(item[0]))
                        except (TypeError, ValueError):
                            pass
                self._credit_handle_batch(
                    path_str, raw_mod, profiles, layer_by_handle, hs, dxf_type
                )

        # ── LWPOLYLINE, ELLIPSE, POINT ─────────────────────────────────────────
        for dxf_type, fn_name in (
            ("LWPOLYLINE", "decode_lwpolyline_entities"),
            ("ELLIPSE",    "decode_ellipse_entities"),
            ("POINT",      "decode_point_entities"),
        ):
            decoder = getattr(raw_mod, fn_name, None)
            if decoder is None:
                continue
            try:
                items = decoder(path_str)
            except Exception:
                continue
            hs = []
            for item in items or []:
                if isinstance(item, (tuple, list)) and item:
                    try:
                        hs.append(int(item[0]))
                    except (TypeError, ValueError):
                        pass
            self._credit_handle_batch(
                path_str, raw_mod, profiles, layer_by_handle, hs, dxf_type
            )

        # ── INSERT (block name in tuple index 8) ──────────────────────────────
        try:
            inserts = raw_mod.decode_insert_entities(path_str)
        except Exception:
            inserts = []
        ins_handles: List[int] = []
        ins_blocks: Dict[int, Optional[str]] = {}
        for item in inserts or []:
            if not isinstance(item, (tuple, list)) or len(item) < 9:
                continue
            try:
                h = int(item[0])
            except (TypeError, ValueError):
                continue
            ins_handles.append(h)
            ins_blocks[h] = item[8]

        batch_n = self._LAYER_LOOKUP_BATCH
        for i in range(0, len(ins_handles), batch_n):
            chunk = ins_handles[i : i + batch_n]
            try:
                pairs = raw_mod.decode_object_entity_layer_handles(path_str, chunk)
            except Exception:
                pairs = []
            lh_map: Dict[int, int] = {}
            for row in pairs or []:
                if not isinstance(row, (tuple, list)) or len(row) < 2:
                    continue
                try:
                    lh_map[int(row[0])] = int(row[1])
                except (TypeError, ValueError):
                    continue
            for h in chunk:
                lh = lh_map.get(h)
                lname = layer_by_handle.get(lh, "0") if lh is not None else "0"
                if lname not in profiles:
                    profiles[lname] = LayerProfile(lname)
                p = profiles[lname]
                p.entity_count += 1
                p.entity_types["INSERT"] += 1
                bname = ins_blocks.get(h)
                if bname:
                    bn = str(bname)
                    if not bn.startswith("*") and bn not in p.block_names:
                        p.block_names.append(bn)

    def _append_global_block_names(
        self,
        profiles: Dict[str, LayerProfile],
        all_block_names: List[str],
    ) -> None:
        """Append block-table names to layer \"0\" for block-name signal (no +entity_count)."""
        if not all_block_names:
            return
        if "0" not in profiles:
            profiles["0"] = LayerProfile("0")
        p0 = profiles["0"]
        for bname in all_block_names:
            if bname and bname not in p0.block_names:
                p0.block_names.append(bname)

    def _scan_msp_safe(self, msp, profiles: Dict[str, LayerProfile]) -> None:
        """
        Query each entity type individually and fall back gracefully on errors.
        Layer attribution is best-effort: ezdwg stores layer as a handle in
        e.dxf (a plain dict), not a resolved name, so entities are credited to
        layer "0" when the name cannot be resolved.
        """
        for etype in self._SAFE_QUERY_TYPES:
            try:
                for entity in msp.query(etype):
                    try:
                        # ezdwg dxf is a plain dict; layer name is NOT present —
                        # only layer_handle is.  Credit to layer "0" as fallback.
                        layer_name = "0"
                        if layer_name not in profiles:
                            profiles[layer_name] = LayerProfile(layer_name)
                        p = profiles[layer_name]
                        p.entity_count += 1
                        p.entity_types[etype] += 1
                    except Exception:
                        pass
            except Exception:
                pass

    def _collect_insert_blocks(
        self,
        raw_mod,
        path_str: str,
        profiles: Dict[str, LayerProfile],
        all_block_names: List[str],
    ) -> None:
        """
        Use the raw INSERT decoder to get block names referenced by inserts.
        Named blocks (not starting with '*') are registered on layer "0"
        because we cannot resolve the INSERT's layer without DWG handle tables.
        """
        try:
            inserts = raw_mod.decode_insert_entities(path_str)
        except Exception:
            return

        # Ensure layer "0" exists
        if "0" not in profiles:
            profiles["0"] = LayerProfile("0")
        p0 = profiles["0"]

        for item in inserts:
            if not isinstance(item, tuple) or len(item) < 9:
                continue
            block_name = item[8]
            if not block_name or block_name.startswith("*"):
                continue
            p0.entity_count += 1
            p0.entity_types["INSERT"] += 1
            if block_name not in p0.block_names:
                p0.block_names.append(block_name)

        # Also register any named blocks found in the block header table
        for bname in all_block_names:
            if bname not in p0.block_names:
                p0.block_names.append(bname)
