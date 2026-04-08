#!/usr/bin/env python3
"""
Floor Plan Preprocessing Script - Line Cleanup
================================================
Cleans unneeded lines from the A-619 Mechanical 2 Floor Plan (Doors & Windows Tags)
so that door/window counting is possible without visual clutter.

Two complementary approaches are used:

1. PDF Layer (OCG) visibility: Turns off unneeded layers so that conforming
   PDF viewers (Adobe Acrobat, browsers) hide them automatically.

2. Content-stream stripping: Physically removes the marked-content sections
   (BDC/EMC blocks) for unwanted layers from the raw PDF content streams,
   so the cleanup is visible even in renderers that ignore OCG state.

Layers / content REMOVED:
  - Grid lines and column grid axes (GRID LINE 2 group)
  - Column grid dimensions and reference numbers
  - Elevation and section tags
  - Grid annotation text
  - Plot limit / setback text
  - Dimension annotations
  - Cloud revision marks
  - Concrete hatching patterns (AD-HAT-CONC)
  - Column tone fill patterns from roof level cross-reference (EC-TONE)
  - Title block content (TBLOCK DM group)
  - GFC (Good For Construction) stamp
  - Room tag outline boxes

Layers / content KEPT:
  - Structural walls (A_A_BLCKWALL Cut)
  - Door geometry (A_A_DOOR)
  - Door tags (A-DOOR-TAG)
  - Window tags (A-WIND-TAG)
  - Slab and room boundaries (A-Slab Limit Line)
  - Room names (A-ROOM-TAG)
  - Floor plan text labels (A_TEXT, text, mTEXT_)
  - Stair geometry, loft elements
  - Glazing / glass elements (14-GLAS)
  - Column and wall outlines (EC-WALL)
  - Vertical fins, stone cladding, expansion joints
  - Track indicators for sliding elements (TRACKS)
"""

import fitz  # PyMuPDF
import os
import re
import sys


# ── Layer removal list ────────────────────────────────────────────────────────

# CONSERVATIVE REMOVAL PATTERNS
# Goal: Remove ONLY elements that interfere with door/window counting
# Principle: When in doubt, KEEP IT
REMOVE_PATTERNS = [
    # Hatching and fill patterns (ALL hatching must be removed)
    "HATCH",           # ALL hatching patterns (generic)
    "HACH",            # Hatching variant (without T)
    "HACHURES",        # French for hatching
    "-HAT-",           # Hatching layers (e.g., AD-HAT-CONC)
    "_HATCH",          # Hatching suffix (A_HATCH, A_FLOOR HATCH, etc.)
    
    # Dimensions (ONLY specific types - grid, column, lift)
    "GRID DIM",        # Grid dimensions only
    "GRID-DIM",        # Grid dimensions variant
    "_GRID DIM",       # Grid dimensions prefix
    "COLUMN-DIM",      # Column dimensions
    "LIFT-DIM",        # Lift dimensions
    
    # Annotations and markup
    "CLOUD",           # Revision clouds
    "TBLOCK",          # Title blocks
    "STAMP",           # Stamps
    "-TAG-BOX",        # Tag boxes
    "LTAG",            # Level tags (A_D_LTAG = FFL/SSL markers)
    "X-TAGS",          # External reference tags (often FFL/SSL)
    "ROOM-TAG",        # Room name tags (washroom, pantry, etc.)
    "ROOM TAG",        # Room name tags (space variant)
    "RM TAG",          # Room tags (abbreviated)
    "SUITE",           # Suite labels
    "A_T_TEXT",        # General text layer (room numbers, direction labels)
    "A-T-TEXT",        # General text layer (hyphen variant)
    "DTAG",            # Floor/direction tag layer (G, floor indicators)
    "A_TEXT",          # AutoCAD text layer (NET AREA, room descriptions)
    "A-TEXT",          # AutoCAD text layer (hyphen variant)
    "A-TEXT-",         # AutoCAD text sub-layers (A-TEXT-1, A-TEXT-3, etc.)
    "45-TEXTS",        # General text annotation layer
    "MTEXT",           # Multi-line text annotations
    
    # Level markers (FFL/SSL annotations)
    "ANNO-LEVL",       # Level annotation layers
    "LEVEL",           # Level marker layers
    "FFL",             # Finished Floor Level markers
    "SSL",             # Structural Slab Level markers
    "ELEV-MARKER",     # Elevation marker layers
    
    # Furniture and fixtures
    "A-FURN",          # Furniture prefix
    "MOBILIER",        # Furniture (French)
    
    # Grid lines
    "GRID LINE",       # Grid line groups
    "AXES GRID",       # Axes grid
    "_AXS",            # Axis lines
    
    # Tags and callouts
    "ELEV. TAG",       # Elevation tags
    "SEC. TAG",        # Section tags
    
    # Stairs and lifts
    "STAIR",           # Stairs
    "HANDRAIL",        # Handrails
    "STEPS",           # Stair steps
    "LIFT",            # Elevators (but NOT if has DOOR/WINDOW)
    "LIFT 1 to 6 and 7$0$DIMENSIONS",  # Lift dimensions (specific)
    
    # Landscape
    "LANDSCAPE",       # Landscape layers
    "L-PL-",           # Landscape planting
    "TREE",            # Trees
    "BENCH",           # Benches
    "AREA",            # Area measurements (room area, planting area, etc.)
    
    # Parking and roads
    "PARKING",         # Parking areas
    "ROAD",            # Road elements and road hatching
    "ARROW",           # Arrows
    
    # MEP
    "MEP",             # MEP equipment
    "DUCT",            # Ductwork
    
    # Climate / orientation (often missed because substring "wind" kept them as windows)
    "WIND DIR",        # Wind direction graphics
    "WIND-DIR",
    "WIND ROSE",
    "COMPASS",         # North arrow / compass roses
    "TRUE NORTH",
    "NORTH ARR",       # North arrow callouts
    
    # Facade / cladding / storefronts / curtain walls (not doors or windows)
    "CLADDING",        # Stone cladding, glass cladding
    "CLADD",           # A-CLADD-GLASS etc.
    "STONE CLAD",      # Stone cladding (explicit)
    "VERTICAL FIN",    # Facade vertical fins
    "A-UPPER",         # Upper-floor projection lines (dashed above)
    "UPPER LINE",      # Upper-floor line variant
    "UPPER-DOT",       # Upper-floor dotted variant
    "INTUMESCENT",     # Intumescent fire strips
    "A-FINISH",        # Floor/wall finishes
    "SERVICES",        # MEP services routing
    "TAG LIGHT",       # Lighting tags
    "LIGHT TYPE",      # Lighting types
    
    # Opening schedule tags — CW / ST annotations drawn as vector paths + arrows
    "-OST",            # WALL-OST, ID-OST, 0-OST (Opening Schedule Tags)
    "A_OPENINGS",      # Opening marks for storefronts / curtain walls
    "MDF",             # MDF panels (not door/window)
    
    # Generic vector-drawn text layers (CW/ST labels are paths, not PDF text)
    # Door/window tags live on dedicated A-DOOR-TAG / A-WIND-TAG layers.
    "TEXT",            # Catches bare TEXT, A-TEXT, A_TEXT, MTEXT, etc.
    "A-TXT",           # AutoCAD text variant layer
    
    # Structural grid, columns, and consultant layers
    "AXES",            # Grid axis lines (bare AXES layers)
    "A-STRUCT",        # Structural elements / column outlines
    "STRUCT ABOVE",    # Structure-above dashed outlines
    "COLUMN",          # Column grid layers (04- GROUND FlOOR COLUMNS, etc.)
    "EC-WALL",         # Engineering consultant wall lines
    "EC-TONE",         # Engineering consultant tone fills
    "SHORING",         # Temporary construction shoring
    "A-PROPOSED",      # Proposed structural elements
    "LOT PLOT",        # Site lot plot boundaries
]


def layer_should_remove(name: str) -> bool:
    """
    Return True if this layer should be removed.
    
    GOAL: Keep everything needed for door/window counting.
    PRINCIPLE: When in doubt, KEEP IT.
    """
    nl = name.lower()
    
    # ============================================================================
    # STEP 0: REMOVE wind / compass layers before the generic "wind" KEEP rule
    # (e.g. A-WIND-DIRECTION matches "wind" and would otherwise be kept)
    # ============================================================================
    _wind_or_compass_remove = (
        "wind dir",
        "wind-dir",
        "wind-direction",
        "winddir",
        "wind direction",
        "winddirection",
        "wind rose",
        "windrose",
        "w.dir",
        "wind-arrow",
        "wind arrow",
        "compass",
        "compass rose",
        "true north",
        "north arrow",
        "north arr",
    )
    if any(p in nl for p in _wind_or_compass_remove):
        _glazing_or_door_keep = (
            "window",
            "glaz",
            "win-",
            "wind-tag",
            "wind tag",
            "windtag",
            "w-tag",
            "door",
            "dr-",
            "a-door",
            "door-tag",
            "door tag",
            "doortag",
            "d-tag",
        )
        _kept_glazing = any(k in nl for k in _glazing_or_door_keep)
        # "a-win" must not match inside "a-wind-*" / "a-window-*" (e.g. A-WIND-DIRECTION)
        if not _kept_glazing and "a-win" in nl and "a-wind" not in nl:
            _kept_glazing = True
        if not _kept_glazing:
            return True
    
    # ============================================================================
    # STEP 1: EXPLICIT KEEP LIST (check first, highest priority)
    # ============================================================================
    
    # ALWAYS keep: Doors (including tags and dimensions)
    if any(pattern in nl for pattern in ['door', 'dr-', 'a-door', 'a_a_door', 'door-tag', 'd-tag']):
        return False
    
    # ALWAYS keep: Windows (including tags and dimensions)
    if any(pattern in nl for pattern in ['window', 'wind', 'win-', 'glaz', 'a-win', 'wind-tag', 'w-tag']):
        return False
    
    # ALWAYS keep: Walls (but NOT landscape, OST, or structural-consultant walls)
    if 'wall' in nl and not any(exc in nl for exc in ['landscape', 'l-lo-', 'xr landscape', '-ost', 'wall-ost', 'ec-wall']):
        return False
    
    # ALWAYS keep: Room boundaries
    if 'room' in nl and any(pattern in nl for pattern in ['bound', 'line', 'outline']):
        return False
    
    # ALWAYS keep: Symbols (may contain door/window blocks)
    if 'symb' in nl and not any(bad in nl for bad in ['anno', 'tag']):
        return False
    
    # ALWAYS keep: Context layers (00_CONTEXT) — "context" contains "text" substring
    if 'context' in nl:
        return False
    
    # ============================================================================
    # STEP 2: EXPLICIT REMOVE LIST (check if it matches removal criteria)
    # ============================================================================
    
    # Only remove if it matches one of the explicit removal patterns
    should_remove = any(pat.lower() in nl for pat in REMOVE_PATTERNS)
    
    # ============================================================================
    # STEP 3: DEFAULT = KEEP (when in doubt)
    # ============================================================================
    
    return should_remove


def _build_tag_removal_set(doc: fitz.Document, page: fitz.Page) -> set:
    """
    Build the set of content-stream OCG tag names (e.g. '/oc422') that
    correspond to layers we want to remove.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    if not props_raw or props_raw[0] not in ("dict", "<<"):
        return set()

    _, props_str = props_raw
    tag_to_xref: dict[str, int] = {}
    for m in re.finditer(r"/(\w+)\s+(\d+)\s+0\s+R", props_str):
        tag_to_xref[m.group(1)] = int(m.group(2))

    remove_tags: set[str] = set()
    for tag, xref in tag_to_xref.items():
        name = ocgs.get(xref, {}).get("name", "")
        if layer_should_remove(name):
            remove_tags.add(tag)

    return remove_tags


def _strip_content_streams(doc: fitz.Document, page: fitz.Page,
                            remove_tags: set) -> tuple[int, int]:
    """
    Physically remove BDC/EMC marked-content sections for the given OCG tags
    from all content streams on the page.

    Returns (total_sections_removed, total_bytes_removed).
    """
    total_sections = 0
    total_bytes = 0

    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        content = raw_bytes.decode("latin-1")
        original_len = len(content)

        for tag in remove_tags:
            pattern = r"/" + re.escape(tag) + r"\s+BDC.*?EMC\n?"
            sections = len(re.findall(pattern, content, flags=re.DOTALL))
            total_sections += sections
            content = re.sub(pattern, "", content, flags=re.DOTALL)

        bytes_removed = original_len - len(content)
        if bytes_removed > 0:
            total_bytes += bytes_removed
            doc.update_stream(stream_xref, content.encode("latin-1"))

    return total_sections, total_bytes


def _strip_untagged_patterns(doc: fitz.Document, page: fitz.Page) -> int:
    """
    Remove ONLY untagged (outside BDC/EMC) hatching and color fills.
    
    CRITICAL: Must NOT touch content inside BDC/EMC blocks (layers).
    Door/window labels are IN layers (oc171, oc172) - must preserve them!
    
    Returns bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        content = raw_bytes.decode("latin-1", errors='ignore')
        original_len = len(content)
        
        # STEP 1: Extract all BDC/EMC blocks (these are layer content - DON'T TOUCH)
        layer_blocks = []
        for match in re.finditer(r'/oc\d+\s+BDC(.*?)EMC', content, re.DOTALL):
            layer_blocks.append((match.start(), match.end(), match.group(0)))
        
        # STEP 2: Identify untagged regions (between layer blocks)
        untagged_regions = []
        last_end = 0
        
        for start, end, block_content in layer_blocks:
            if start > last_end:
                # Content between last block and this block = untagged
                untagged_regions.append((last_end, start))
            last_end = end
        
        # Add final region after last block
        if last_end < len(content):
            untagged_regions.append((last_end, len(content)))
        
        # STEP 3: Remove hatching/fills ONLY from untagged regions
        cleaned_content = ""
        pos = 0
        
        for region_start, region_end in untagged_regions:
            # Add any layer content before this region
            if pos < region_start:
                cleaned_content += content[pos:region_start]
            
            # Process untagged region
            untagged_chunk = content[region_start:region_end]
            
            # Remove pattern fills (hatching)
            untagged_chunk = re.sub(
                r'/Pattern\s+cs\s+/P\d+\s+scn.*?[fFbB]\*?(?=\s)',
                '',
                untagged_chunk,
                flags=re.DOTALL
            )
            
            # Remove solid color fills (but NOT if followed by stroke)
            # Only remove if it's: color + simple path + fill (no stroke)
            untagged_chunk = re.sub(
                r'(\d+\.?\d*\s+){1,4}[rRgGkK]\s+[\d\s\.mlrec-]+?\s+[fF](?!\s*S)',
                '',
                untagged_chunk
            )
            
            # Remove red color markup sequences
            # Pattern: 1 0 0 rg/RG ... (geometry) ... S/f/F
            untagged_chunk = re.sub(
                r'1\.?0*\s+0\.?0*\s+0\.?0*\s+[rR][gG]\s+[\d\s\.mlrec-]+?\s+[SfF]\b',
                '',
                untagged_chunk
            )
            
            # Remove FFL/SSL level marker text and geometry
            # Pattern: FFL = +X.XXm or SSL = +X.XXm with associated circle/crosshair
            untagged_chunk = re.sub(
                r'\((?:FFL|SSL)\s*=\s*[+-]?\d+\.\d+m\).*?Tj',
                '',
                untagged_chunk,
                flags=re.DOTALL
            )
            
            # Remove circle symbols (level markers) - typically 0 0 r re f* pattern
            untagged_chunk = re.sub(
                r'\d+\.?\d*\s+\d+\.?\d*\s+\d+\.?\d*\s+0\s+360\s+arc\s+[fFS]\*?',
                '',
                untagged_chunk
            )
            
            cleaned_content += untagged_chunk
            pos = region_end
        
        # Add any remaining content
        if pos < len(content):
            cleaned_content += content[pos:]
        
        # Update stream if changed
        new_len = len(cleaned_content)
        if new_len < original_len:
            doc.update_stream(stream_xref, cleaned_content.encode("latin-1"))
            total_bytes_removed += (original_len - new_len)
    
    return total_bytes_removed


def _strip_red_colors(doc, page) -> int:
    """
    Strip all red color operators (1 0 0 rg/RG) from the content stream.
    
    This removes red markup/highlights that may appear in any layer,
    including layers we're keeping like door tags and dimensions.
    
    Returns: Number of bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        stream = doc.xref_stream(stream_xref)
        if not stream:
            continue
        
        try:
            content = stream.decode("latin-1", errors="ignore")
        except:
            continue
        
        original_len = len(content)
        original_content = content
        
        # Replace red color operators with black (neutral)
        # This preserves geometry but removes the red coloring
        content = re.sub(
            r'1\.?0*\s+0\.?0*\s+0\.?0*\s+rg\b',
            '0 0 0 rg',
            content
        )
        
        content = re.sub(
            r'1\.?0*\s+0\.?0*\s+0\.?0*\s+RG\b',
            '0 0 0 RG',
            content
        )
        
        # Update stream if content changed (even if length is same)
        if content != original_content:
            doc.update_stream(stream_xref, content.encode("latin-1"))
    
    return total_bytes_removed


def _enhance_labels_and_normalize(doc, page) -> int:
    """
    Enhance door vs window OCG BDC blocks with distinct colours, dimensions red,
    and thicken wall/boundary BDC blocks — single read → transform → write pass.

    Three tiers (BDC-wrapped only; no whole-stream q/Q — preserves OCG in PyMuPDF):

      Tier 1a — Door-related layers → bold red  (1 0 0 RG/rg + 10 w)
      Tier 1b — Window-related layers → bold orange  (1 0.55 0 RG/rg + 10 w)
      Tier 1c — Dimension layers → bold red (same as doors, for OCR)

      Tier 2 — Walls & boundaries → thicker black (3 w)

      Tier 3 — Everything else unchanged.

    Returns: number of content streams modified.
    """
    # ── Identify OCG tag numbers by tier ─────────────────────────────────────
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    xref_to_oc = {}
    if props_raw and props_raw[0] in ("dict", "<<"):
        _, ps = props_raw
        for m in re.finditer(r"/oc(\d+)\s+(\d+)\s+0\s+R", ps):
            xref_to_oc[int(m.group(2))] = m.group(1)

    red_label_ocs = set()     # doors + dimensions
    orange_label_ocs = set()  # windows / glazing
    wall_ocs = set()          # Tier 2: thicker black

    _win_tag_kw = (
        "wind-tag", "wind tag", "windtag", "a-wind-tag",
    )
    _door_tag_kw = (
        "door-tag", "door tag", "doortag", "a-door-tag",
    )
    _win_layer_kw = (
        "window", "glaz", "a-win", "w-tag", "win-",
    )
    _door_layer_kw = (
        "door", "dr-", "a-door", "a_a_door", "d-tag",
    )

    for xref, info in ocgs.items():
        name = info.get("name", "")
        nl = name.lower()
        oc = xref_to_oc.get(xref)
        if not oc:
            continue
        # Window tags & window-ish geometry/text layers → orange (check before door — shared "wind")
        if any(kw in nl for kw in _win_tag_kw):
            orange_label_ocs.add(oc)
        elif any(kw in nl for kw in _door_tag_kw):
            red_label_ocs.add(oc)
        elif "dimension" in nl:
            red_label_ocs.add(oc)
        elif any(kw in nl for kw in _win_layer_kw) or (
            "wind" in nl and "door" not in nl
        ):
            orange_label_ocs.add(oc)
        elif any(kw in nl for kw in _door_layer_kw):
            red_label_ocs.add(oc)
        elif "wall" in nl and not any(x in nl for x in ["landscape", "l-lo-"]):
            wall_ocs.add(oc)
        elif any(kw in nl for kw in ["bound", "outline"]):
            wall_ocs.add(oc)

    BOLD_WIDTH = "10"   # ≈ 1.7 pt
    WALL_WIDTH = "3"    # ≈ 0.5 pt
    RED = "1 0 0"
    ORANGE = "1 0.55 0"  # device RGB — distinct from red in viewers & OCR

    modified = 0

    for stream_xref in page.get_contents():
        raw = doc.xref_stream(stream_xref)
        if not raw:
            continue
        try:
            content = raw.decode("latin-1", errors="ignore")
        except Exception:
            continue

        original = content

        def wrap_block(m):
            oc_num = m.group(1)
            inner = m.group(2)
            if oc_num in orange_label_ocs:
                c = ORANGE
                return (
                    f"/oc{oc_num} BDC\n"
                    f"q\n"
                    f"{c} RG\n"
                    f"{c} rg\n"
                    f"{BOLD_WIDTH} w\n"
                    f"{inner}\n"
                    f"Q\n"
                    f"EMC"
                )
            if oc_num in red_label_ocs:
                return (
                    f"/oc{oc_num} BDC\n"
                    f"q\n"
                    f"{RED} RG\n"
                    f"{RED} rg\n"
                    f"{BOLD_WIDTH} w\n"
                    f"{inner}\n"
                    f"Q\n"
                    f"EMC"
                )
            if oc_num in wall_ocs:
                return (
                    f"/oc{oc_num} BDC\n"
                    f"q\n"
                    f"{WALL_WIDTH} w\n"
                    f"{inner}\n"
                    f"Q\n"
                    f"EMC"
                )
            return m.group(0)

        content = re.sub(
            r"/oc(\d+)\s+BDC(.*?)EMC",
            wrap_block,
            content,
            flags=re.DOTALL,
        )

        if content != original:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            modified += 1

    return modified


def _strip_non_window_tags(doc, page) -> int:
    """
    Inside A-WIND-TAG BDC blocks, remove curtain-wall (CW) and storefront (ST)
    tag groups while keeping genuine window-door (WD) tags.

    Detection: each tag is a hexagonal bubble (7-point closed polygon) followed
    by vector-drawn characters.  The FIRST character after the hexagon determines
    the tag type — a "W" is drawn as a distinctive 5-point zigzag (x alternates
    between two values, y monotonically increases).  Blocks whose hexagons all
    start with "W" are WD tags; blocks with non-"W" first letters are CW/ST.

    Returns bytes removed.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    wind_tag_ocs: set[str] = set()
    if props_raw and props_raw[0] in ("dict", "<<"):
        _, ps = props_raw
        for m in re.finditer(r"/oc(\d+)\s+(\d+)\s+0\s+R", ps):
            xref = int(m.group(2))
            if xref in ocgs:
                name = ocgs[xref]["name"].lower()
                if "wind-tag" in name or "wind tag" in name:
                    wind_tag_ocs.add(m.group(1))

    if not wind_tag_ocs:
        return 0

    def _is_w_zigzag(pts):
        if len(pts) != 5:
            return False
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ux = sorted(set(xs))
        if len(ux) != 2:
            return False
        if not all(
            (xs[i] == ux[0] and xs[i + 1] == ux[1])
            or (xs[i] == ux[1] and xs[i + 1] == ux[0])
            for i in range(len(xs) - 1)
        ):
            return False
        return all(ys[i] < ys[i + 1] for i in range(len(ys) - 1)) or all(
            ys[i] > ys[i + 1] for i in range(len(ys) - 1)
        )

    def _block_is_wd(block: str) -> bool | None:
        """True = all tags are WD, False = all non-WD, None = no hexagons found."""
        strokes: list[list[tuple[int, int]]] = []
        for sm in re.finditer(r"((?:\d[\d\s.]*[ml]\s*)+)S", block):
            raw = sm.group(1)
            pts = [
                (int(x), int(y))
                for x, y in re.findall(r"(\d+)\s+(\d+)\s+[ml]", raw)
            ]
            strokes.append(pts)

        first_letters: list[list[tuple[int, int]]] = []
        for i, pts in enumerate(strokes):
            if len(pts) == 7 and pts[0] == pts[6]:
                if i + 1 < len(strokes):
                    first_letters.append(strokes[i + 1])

        if not first_letters:
            return None
        return all(_is_w_zigzag(fl) for fl in first_letters)

    total_removed = 0

    for stream_xref in page.get_contents():
        raw_bytes = doc.xref_stream(stream_xref)
        if not raw_bytes:
            continue
        content = raw_bytes.decode("latin-1", errors="ignore")
        original_len = len(content)

        def _filter_wind_block(m):
            oc_num = m.group(1)
            if oc_num not in wind_tag_ocs:
                return m.group(0)
            block = m.group(2)
            verdict = _block_is_wd(block)
            if verdict is False:
                return f"/oc{oc_num} BDC\nEMC"
            return m.group(0)

        content = re.sub(
            r"/oc(\d+)\s+BDC(.*?)EMC",
            _filter_wind_block,
            content,
            flags=re.DOTALL,
        )
        new_len = len(content)
        if new_len < original_len:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            total_removed += original_len - new_len

    return total_removed


def _strip_room_number_text(doc, page) -> int:
    """
    Strip small clutter text embedded in kept layers: room numbers (1–2 digits),
    floor indicators (UP/DOWN/G), structural/curtain-wall callouts (ST 01, CW04),
    and simple wind-speed labels — not door/window schedule tags (WD/GD/…).
    
    Returns: Number of bytes removed.
    """
    total_bytes_removed = 0
    
    for stream_xref in page.get_contents():
        stream = doc.xref_stream(stream_xref)
        if not stream:
            continue
        
        try:
            content = stream.decode("latin-1", errors="ignore")
        except:
            continue
        
        original_content = content
        
        # Remove text show operators containing ONLY 1-2 digit numbers
        # e.g. (11) Tj  or  (26) Tj  — but NOT (1345) Tj (dimension values)
        # Also removes UP, DOWN, G (floor indicators)
        content = re.sub(
            r'\((0?[0-9]{1,2}|UP|DOWN|G)\)\s*Tj',
            '',
            content
        )
        # Structural grid / curtain-wall panel callouts (not door type WDxx)
        content = re.sub(
            r'\(ST\s*[- ]?\s*0?\d{1,2}\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        content = re.sub(
            r'\(CW\s*0?\d{1,3}\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        # Simple wind-speed annotations, e.g. (3.5 m/s) Tj
        content = re.sub(
            r'\([\d.]+\s*m/s\)\s*Tj',
            '',
            content,
            flags=re.IGNORECASE,
        )
        
        if content != original_content:
            doc.update_stream(stream_xref, content.encode("latin-1"))
            total_bytes_removed += abs(len(original_content) - len(content))
    
    return total_bytes_removed


def clean_floor_plan(input_pdf: str, output_pdf: str) -> dict:
    """
    Clean the floor plan PDF:
      1. Set OCG layer visibility (for conforming PDF viewers).
      2. Strip BDC/EMC content blocks from content streams (for all renderers).

    Returns a summary dict.
    """
    doc = fitz.open(input_pdf)
    page = doc[0]
    ocgs = doc.get_ocgs()

    # ── Step 1: OCG layer visibility ─────────────────────────────────────────
    on_xrefs, off_xrefs = [], []
    kept_names, removed_names = [], []

    for xref, info in sorted(ocgs.items()):
        name = info["name"]
        if layer_should_remove(name):
            off_xrefs.append(xref)
            removed_names.append(name)
        else:
            on_xrefs.append(xref)
            kept_names.append(name)

    doc.set_layer(
        -1,
        basestate="ON",
        on=on_xrefs or None,
        off=off_xrefs or None,
    )

    # ── Step 2: Content-stream stripping (tagged content) ────────────────────
    remove_tags = _build_tag_removal_set(doc, page)
    sections_removed, bytes_removed = _strip_content_streams(
        doc, page, remove_tags
    )
    
    # ── Step 3: Remove untagged hatching and color fills ─────────────────────
    untagged_bytes_removed = _strip_untagged_patterns(doc, page)
    
    # ── Step 4: Strip CW/ST tags from A-WIND-TAG layer (keep only WD) ────────
    cw_st_bytes_removed = _strip_non_window_tags(doc, page)
    
    # ── Step 5: Strip standalone room number text from all content ───────────
    room_num_bytes_removed = _strip_room_number_text(doc, page)
    
    # ── Step 6: Strip red colors from ALL content ─────────────────────────────
    red_bytes_removed = _strip_red_colors(doc, page)

    # ── Step 7: Bold red labels + minimum stroke (single combined pass) ───────
    # One read→transform→write per stream prevents PyMuPDF's in-session cache
    # from causing a second xref_stream call to return pre-update bytes.
    bold_streams = _enhance_labels_and_normalize(doc, page)

    doc.save(output_pdf, garbage=4, deflate=True)
    doc.close()

    return {
        "kept_count": len(kept_names),
        "removed_count": len(removed_names),
        "kept": kept_names,
        "removed": removed_names,
        "content_sections_removed": sections_removed,
        "content_bytes_removed": bytes_removed,
        "untagged_bytes_removed": untagged_bytes_removed,
        "red_bytes_removed": red_bytes_removed,
    }


def render_preview(pdf_path: str, png_path: str, zoom: float = 1.0) -> None:
    """Render the first page of a PDF to a PNG file."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)
    pix.save(png_path)
    doc.close()
    print(f"  Saved: {png_path} ({pix.width}x{pix.height} px)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    input_pdf = os.path.join(
        "/home/ubuntu/.cursor/projects/workspace/uploads",
        "A-619_Mechanical_2_Floor_Plan-Doors___Windows_Tags-A-619.pdf",
    )
    output_pdf = os.path.join(base_dir, "cleaned_floor_plan.pdf")
    preview_original = os.path.join(base_dir, "preview_original.png")
    preview_cleaned = os.path.join(base_dir, "preview_cleaned.png")

    if not os.path.exists(input_pdf):
        print(f"ERROR: Input PDF not found: {input_pdf}")
        sys.exit(1)

    print("=" * 65)
    print("Floor Plan Line Cleanup  –  Preprocessing for Door/Window Count")
    print("=" * 65)
    print(f"Input:  {input_pdf}")
    print(f"Output: {output_pdf}")
    print()

    print("Rendering original preview ...")
    render_preview(input_pdf, preview_original, zoom=1.0)

    print("\nCleaning floor plan layers ...")
    summary = clean_floor_plan(input_pdf, output_pdf)

    print(f"\nLayers KEPT  ({summary['kept_count']}):")
    for name in summary["kept"]:
        print(f"  + {name}")

    print(f"\nLayers REMOVED ({summary['removed_count']}):")
    for name in summary["removed"]:
        print(f"  - {name}")

    print(f"\nContent stream sections stripped : {summary['content_sections_removed']}")
    print(f"Content bytes removed            : {summary['content_bytes_removed']:,}")

    print("\nRendering cleaned preview ...")
    render_preview(output_pdf, preview_cleaned, zoom=1.0)

    print()
    print("=" * 65)
    print("Done.")
    print(f"  Cleaned PDF : {output_pdf}")
    print(f"  Original PNG: {preview_original}")
    print(f"  Cleaned PNG : {preview_cleaned}")
    print("=" * 65)


if __name__ == "__main__":
    main()
