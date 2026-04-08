#!/usr/bin/env python3
"""
Automated Floor Plan Processing Workflow
=========================================
Monitors the 'unprocessed' folder for floor plan PDFs, cleans them,
saves cleaned versions to 'cleaned' folder, and archives originals.

Directory Structure:
    unprocessed/  ← Place your PDFs here
    cleaned/      ← Cleaned PDFs appear here
    archived/     ← Original PDFs moved here after processing

Usage:
    python3 process_floor_plans.py

The script will:
1. Find all PDFs in unprocessed/
2. Clean each PDF
3. Save cleaned version to cleaned/
4. Move original to archived/
5. Generate processing report
"""

import fitz  # PyMuPDF
import os
import re
import sys
import shutil
from datetime import datetime
from pathlib import Path


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
    
    # Annotations and markup (explicitly listed)
    "CLOUD",           # Revision clouds
    "TBLOCK",          # Title blocks
    "STAMP",           # Stamps
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
    
    # Furniture and fixtures (explicitly listed)
    "FURN",            # Furniture
    "MOBILIER",        # Furniture (French)
    "EQPM",            # Equipment
    
    # Grid lines and column markers (explicitly listed)
    "GRID",            # Grid lines
    "AXES GRID",       # Axes grid
    "_AXS",            # Axis lines
    "_GRID",           # Grid suffix
    
    # Section and elevation tags (explicitly listed)
    "ELEV. TAG",       # Elevation tags
    "SEC. TAG",        # Section tags
    "ELEV",            # Elevation markers (but not if part of door/window)
    "SECT",            # Section markers
    "DETL",            # Detail callouts
    
    # Level markers (FFL/SSL annotations)
    "ANNO-LEVL",       # Level annotation layers
    "LEVEL",           # Level marker layers
    "FFL",             # Finished Floor Level markers
    "SSL",             # Structural Slab Level markers
    "ELEV-MARKER",     # Elevation marker layers
    
    # Stairs and vertical circulation (explicitly listed)
    "STAIR",           # Stairs
    "HANDRAIL",        # Handrails
    "STEPS",           # Stair steps
    "LIFT",            # Elevators (but NOT if has DOOR/WINDOW)
    "NOYAUX",          # Lift cores (French)
    "RAMP",            # Ramps (vehicular/pedestrian)
    "LIFT 1 to 6 and 7$0$DIMENSIONS",  # Lift dimensions (specific)
    
    # Site and landscape (explicitly listed)
    "LANDSCAPE",       # Landscape layers
    "TREE",            # Trees
    "BENCH",           # Benches
    "SITE",            # Site elements
    "L-PL-AREA",       # Planting/landscape area
    "ROOM-AREA",       # Room area calculations
    "AREA",            # Area measurements (generic)
    
    # Parking and roads (explicitly listed)
    "PARKING",         # Parking areas
    "ROAD",            # Road elements and road hatching
    "ARROW",           # Arrows (parking, direction, etc.)
    
    # Title blocks and borders (explicitly listed)
    "TITLE",           # Title blocks
    
    # MEP systems (explicitly listed)
    "MEP",             # MEP equipment
    "DUCT",            # Ductwork
    "PLUMBING",        # Plumbing
    
    # Climate / orientation (often missed because substring "wind" kept them as windows)
    "WIND DIR",
    "WIND-DIR",
    "WIND ROSE",
    "COMPASS",
    "TRUE NORTH",
    "NORTH ARR",
    
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
    
    # Floor tile patterns (grid-like at bottom of drawing)
    "TA - FLOOR",      # Floor tile/finish patterns
    "TA-FLOOR",        # Floor tile variant (no spaces)
    
    # Structural grid, columns, and consultant layers
    "AXES",            # Grid axis lines (bare AXES layers)
    "A-STRUCT",        # Structural elements / column outlines
    "STRUCT ABOVE",    # Structure-above dashed outlines
    "COLUMN",          # Column grid layers (04- GROUND FlOOR COLUMNS, etc.)
    # EC-WALL kept: structural perimeter walls needed for building outline
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
    
    # ALWAYS keep: Walls (but NOT landscape or OST annotation walls)
    # Check exceptions against LEAF layer name only so building walls
    # nested in landscape XRefs (e.g. XR Landscape...$0$A_A_BLCKWALL Cut) are kept.
    if 'wall' in nl:
        leaf = nl.split('$0$')[-1] if '$0$' in nl else nl
        if not any(exc in leaf for exc in ['landscape', 'l-lo-', '-ost', 'wall-ost']):
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
    
    Enhanced to also map layer config numbers to their actual tags.
    """
    ocgs = doc.get_ocgs()
    props_raw = doc.xref_get_key(page.xref, "Resources/Properties")
    if not props_raw or props_raw[0] not in ("dict", "<<"):
        return set()

    _, props_str = props_raw
    
    # Build mapping: tag name (e.g. 'oc532') -> xref number
    tag_to_xref: dict[str, int] = {}
    for m in re.finditer(r"/(\w+)\s+(\d+)\s+0\s+R", props_str):
        tag_to_xref[m.group(1)] = int(m.group(2))
    
    # Build mapping: layer config number -> layer name
    layers = doc.layer_ui_configs()
    config_num_to_name = {l.get('number'): l.get('text', '') for l in layers}

    # Find tags to remove - ONLY use xref lookup (Method 2 was buggy)
    remove_tags: set[str] = set()
    
    # Check each tag by its xref to get the correct layer name
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
        elif "wall" in nl:
            leaf = nl.split('$0$')[-1] if '$0$' in nl else nl
            if not any(x in leaf for x in ["landscape", "l-lo-"]):
                wall_ocs.add(oc)
        elif any(kw in nl for kw in ["bound", "outline"]):
            wall_ocs.add(oc)

    BOLD_WIDTH = "10"   # ≈ 1.7 pt (at 0.12 scale: 1.2 pt)
    WALL_WIDTH = "25"   # ≈ 3 pt (at 0.12 scale: 3 pt — clearly visible wall lines)
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

        def _fix_zero_w(inner, min_w):
            return re.sub(r'\b0\s+w\b', f'{min_w} w', inner)

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
                inner = _fix_zero_w(inner, WALL_WIDTH)
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
    # ── Identify A-WIND-TAG OCG tags ────────────────────────────────────────
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
    Strip small clutter text in kept layers: room numbers (1–2 digits),
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


def render_preview(pdf_path: str, png_path: str, zoom: float = 3.0) -> None:
    """Render the first page of a PDF to a PNG file at high quality (300 DPI)."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    pix.save(png_path)
    doc.close()


def render_web_pdf(cleaned_pdf: str, web_pdf: str, zoom: float = 3.0) -> None:
    """
    Produce a web-compatible flattened PDF by rasterising the cleaned vector
    PDF at high resolution and embedding the result as a single image page.

    WHY THIS IS NEEDED
    ──────────────────
    Web apps (PDF.js and similar) reset the PDF graphics state when entering
    OCG/BDC marked-content blocks.  This means our colour and stroke-width
    injections (red labels, thicker walls) are silently ignored in those
    renderers, even though Mac Preview and PyMuPDF handle them correctly.

    A rasterised PDF has no layers, no OCG, no BDC blocks — just one JPEG
    image per page.  It renders identically in every viewer: web browsers,
    mobile apps, Acrobat, Preview, and any OCR/vision pipeline.

    ZOOM / DPI
    ──────────
    zoom=3.0  →  3 × 72 = 216 DPI  (default; good balance of quality/size)
    zoom=4.0  →  4 × 72 = 288 DPI  (higher quality, larger file)
    The output page dimensions match the original so layout is preserved.
    """
    src = fitz.open(cleaned_pdf)
    src_page = src[0]
    mat = fitz.Matrix(zoom, zoom)

    # Rasterise to RGB pixmap
    pix = src_page.get_pixmap(matrix=mat, alpha=False)

    # Create a new single-page PDF whose MediaBox matches the original page
    out = fitz.open()
    out_page = out.new_page(width=src_page.rect.width, height=src_page.rect.height)

    # Embed the rasterised image filling the entire page
    img_bytes = pix.tobytes("png")
    out_page.insert_image(out_page.rect, stream=img_bytes)

    out.save(web_pdf, deflate=True)
    out.close()
    src.close()


def process_single_pdf(input_path: str, cleaned_dir: str, archived_dir: str) -> dict:
    """
    Process a single PDF: clean it, save to cleaned/, and archive original.
    
    Returns processing summary dict.
    """
    filename = os.path.basename(input_path)
    base_name = os.path.splitext(filename)[0]
    
    # Define output paths
    output_pdf = os.path.join(cleaned_dir, f"{base_name}_cleaned.pdf")
    web_pdf    = os.path.join(cleaned_dir, f"{base_name}_web.pdf")
    preview_original = os.path.join(cleaned_dir, f"{base_name}_preview_original.png")
    preview_cleaned = os.path.join(cleaned_dir, f"{base_name}_preview_cleaned.png")
    archived_path = os.path.join(archived_dir, filename)
    
    print(f"\n{'─' * 75}")
    print(f"Processing: {filename}")
    print('─' * 75)
    
    try:
        # Get original file size
        original_size = os.path.getsize(input_path)
        
        # Render original preview
        print("  → Rendering original preview ...")
        render_preview(input_path, preview_original, zoom=3.0)
        
        # Clean the PDF
        print("  → Cleaning floor plan layers ...")
        summary = clean_floor_plan(input_path, output_pdf)
        
        # Get cleaned file size
        cleaned_size = os.path.getsize(output_pdf)
        reduction = (original_size - cleaned_size) / original_size * 100
        
        # Render cleaned preview
        print("  → Rendering cleaned preview ...")
        render_preview(output_pdf, preview_cleaned, zoom=3.0)

        # Produce web-compatible flattened PDF (rasterised — no layers/OCG)
        print("  → Generating web-compatible PDF ...")
        render_web_pdf(output_pdf, web_pdf, zoom=3.0)

        # ML detection (optional — runs only if ml/best.pt exists)
        ml_result_dict = {}
        _ml_model_path = os.path.join(os.path.dirname(__file__), "ml", "best.pt")
        if os.path.exists(_ml_model_path):
            try:
                from ml.detector import FloorPlanDetector, write_detection_report

                detector = FloorPlanDetector(_ml_model_path)
                if detector.is_ready():
                    print("  → Running ML door/window detection ...")
                    annotated_png = os.path.join(cleaned_dir, f"{base_name}_ml_annotated.png")
                    ml_result = detector.detect_and_annotate(preview_cleaned, annotated_png)
                    if ml_result:
                        ml_paths = write_detection_report(ml_result, cleaned_dir, base_name)
                        ml_result_dict = {
                            "ml_door_count": ml_result.door_count,
                            "ml_window_count": ml_result.window_count,
                            "ml_json": ml_paths["json_path"],
                            "ml_annotated": annotated_png,
                        }
                        print(f"    ML doors  : {ml_result.door_count}")
                        print(f"    ML windows: {ml_result.window_count}")
            except ImportError:
                pass

        # Move original to archived
        print(f"  → Archiving original to archived/ ...")
        shutil.move(input_path, archived_path)
        
        # Print summary
        print(f"\n  Summary:")
        print(f"    Layers removed:    {summary['removed_count']}")
        print(f"    Content stripped:  {summary['content_sections_removed']} sections")
        print(f"    Bytes removed:     {summary['content_bytes_removed']:,}")
        print(f"    Untagged removed:  {summary['untagged_bytes_removed']:,} bytes (hatching/colors)")
        print(f"    Original size:     {original_size:,} bytes ({original_size/1024/1024:.2f} MB)")
        print(f"    Cleaned size:      {cleaned_size:,} bytes ({cleaned_size/1024/1024:.2f} MB)")
        print(f"    Size reduction:    {reduction:.1f}%")
        print(f"\n  ✓ Success")
        print(f"    Cleaned PDF (vector): {output_pdf}")
        print(f"    Web PDF (flattened):  {web_pdf}")
        print(f"    Original archived:    {archived_path}")
        
        result_dict = {
            'status': 'success',
            'filename': filename,
            'original_size': original_size,
            'cleaned_size': cleaned_size,
            'reduction_percent': reduction,
            'layers_removed': summary['removed_count'],
            'sections_stripped': summary['content_sections_removed'],
            'bytes_removed': summary['content_bytes_removed'],
            'output_pdf': output_pdf,
            'web_pdf': web_pdf,
            'archived_path': archived_path,
            'error': None
        }
        result_dict.update(ml_result_dict)
        return result_dict
        
    except Exception as e:
        print(f"\n  ✗ Error: {e}")
        
        return {
            'status': 'error',
            'filename': filename,
            'error': str(e),
            'original_size': None,
            'cleaned_size': None,
            'reduction_percent': None,
        }


def find_unprocessed_pdfs(unprocessed_dir: str) -> list:
    """Find all PDF files in the unprocessed directory."""
    pdf_files = []
    
    if not os.path.exists(unprocessed_dir):
        return pdf_files
    
    for filename in os.listdir(unprocessed_dir):
        if filename.lower().endswith('.pdf'):
            full_path = os.path.join(unprocessed_dir, filename)
            if os.path.isfile(full_path):
                pdf_files.append(full_path)
    
    return sorted(pdf_files)


def generate_processing_report(results: list, report_path: str):
    """Generate a processing report file."""
    with open(report_path, 'w') as f:
        f.write("=" * 75 + "\n")
        f.write("FLOOR PLAN PROCESSING REPORT\n")
        f.write("=" * 75 + "\n\n")
        
        f.write(f"Processing Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total Files Processed: {len(results)}\n")
        
        successful = [r for r in results if r['status'] == 'success']
        failed = [r for r in results if r['status'] == 'error']
        
        f.write(f"Successful: {len(successful)}\n")
        f.write(f"Failed: {len(failed)}\n\n")
        
        if successful:
            total_original = sum(r['original_size'] for r in successful)
            total_cleaned = sum(r['cleaned_size'] for r in successful)
            avg_reduction = sum(r['reduction_percent'] for r in successful) / len(successful)
            
            f.write("-" * 75 + "\n")
            f.write("OVERALL STATISTICS\n")
            f.write("-" * 75 + "\n")
            f.write(f"Total original size: {total_original:,} bytes ({total_original/1024/1024:.2f} MB)\n")
            f.write(f"Total cleaned size:  {total_cleaned:,} bytes ({total_cleaned/1024/1024:.2f} MB)\n")
            f.write(f"Total space saved:   {total_original - total_cleaned:,} bytes ({(total_original - total_cleaned)/1024/1024:.2f} MB)\n")
            f.write(f"Average reduction:   {avg_reduction:.1f}%\n\n")
        
        f.write("-" * 75 + "\n")
        f.write("SUCCESSFUL PROCESSING\n")
        f.write("-" * 75 + "\n")
        for result in successful:
            f.write(f"\n{result['filename']}:\n")
            f.write(f"  Layers removed:     {result['layers_removed']}\n")
            f.write(f"  Size reduction:     {result['reduction_percent']:.1f}%\n")
            f.write(f"  Output:             {result['output_pdf']}\n")
            f.write(f"  Archived:           {result['archived_path']}\n")
        
        if failed:
            f.write("\n" + "-" * 75 + "\n")
            f.write("FAILED PROCESSING\n")
            f.write("-" * 75 + "\n")
            for result in failed:
                f.write(f"\n{result['filename']}:\n")
                f.write(f"  Error: {result['error']}\n")
        
        f.write("\n" + "=" * 75 + "\n")


def main():
    # Get base directory
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Define directories
    unprocessed_dir = os.path.join(base_dir, "unprocessed")
    cleaned_dir = os.path.join(base_dir, "cleaned")
    archived_dir = os.path.join(base_dir, "archived")
    
    # Ensure directories exist
    os.makedirs(unprocessed_dir, exist_ok=True)
    os.makedirs(cleaned_dir, exist_ok=True)
    os.makedirs(archived_dir, exist_ok=True)
    
    print("=" * 75)
    print("AUTOMATED FLOOR PLAN PROCESSING")
    print("=" * 75)
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    print("Directory Structure:")
    print(f"  Unprocessed: {unprocessed_dir}")
    print(f"  Cleaned:     {cleaned_dir}")
    print(f"  Archived:    {archived_dir}")
    print()
    
    # Find all PDFs in unprocessed folder
    pdf_files = find_unprocessed_pdfs(unprocessed_dir)
    
    if not pdf_files:
        print("No PDF files found in unprocessed/ folder.")
        print()
        print("To use this workflow:")
        print(f"  1. Copy your floor plan PDFs to: {unprocessed_dir}/")
        print(f"  2. Run this script again: python3 {os.path.basename(__file__)}")
        print(f"  3. Find cleaned PDFs in: {cleaned_dir}/")
        print(f"  4. Original PDFs will be in: {archived_dir}/")
        print()
        print("=" * 75)
        return
    
    print(f"Found {len(pdf_files)} PDF(s) to process:")
    for pdf_path in pdf_files:
        print(f"  • {os.path.basename(pdf_path)}")
    print()
    
    # Process each PDF
    results = []
    
    for idx, pdf_path in enumerate(pdf_files, 1):
        print(f"\n[{idx}/{len(pdf_files)}]")
        result = process_single_pdf(pdf_path, cleaned_dir, archived_dir)
        results.append(result)
    
    # Generate report
    print("\n" + "=" * 75)
    print("PROCESSING COMPLETE")
    print("=" * 75)
    
    successful = [r for r in results if r['status'] == 'success']
    failed = [r for r in results if r['status'] == 'error']
    
    print(f"\nResults:")
    print(f"  ✓ Successful: {len(successful)}")
    print(f"  ✗ Failed:     {len(failed)}")
    
    if successful:
        avg_reduction = sum(r['reduction_percent'] for r in successful) / len(successful)
        total_bytes_saved = sum(r['original_size'] - r['cleaned_size'] for r in successful)
        
        print(f"\n  Average size reduction: {avg_reduction:.1f}%")
        print(f"  Total space saved:      {total_bytes_saved/1024/1024:.2f} MB")
    
    # Save detailed report
    report_path = os.path.join(base_dir, "processing_report.txt")
    generate_processing_report(results, report_path)
    print(f"\n  Detailed report: {report_path}")
    
    print(f"\nOutput locations:")
    print(f"  Cleaned PDFs: {cleaned_dir}/")
    print(f"  Archived originals: {archived_dir}/")
    
    print("\n" + "=" * 75)
    
    # Return exit code based on results
    if failed:
        sys.exit(1)  # At least one failure
    else:
        sys.exit(0)  # All successful


if __name__ == "__main__":
    main()
